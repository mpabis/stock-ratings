from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured
from stock_rating.ingest.prices import normalize_symbol_for_alpha_vantage


TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class AnalystConsensusSnapshot:
    symbol: str
    date: date
    analyst_target_price: Decimal | None
    strong_buy_count: int | None
    buy_count: int | None
    hold_count: int | None
    sell_count: int | None
    strong_sell_count: int | None
    suggestion_label: str | None
    suggestion_score: Decimal | None
    source: str = "alpha_vantage_overview"


class AlphaVantageAnalystResponseError(RuntimeError):
    pass


class AlphaVantageAnalystRateLimitError(RuntimeError):
    pass


def _is_transient_http_error(error: HTTPError) -> bool:
    return error.code in TRANSIENT_HTTP_STATUS_CODES


def _sleep_backoff(attempt: int, base_seconds: float, sleep_fn=time.sleep) -> None:
    if attempt <= 0 or base_seconds <= 0:
        return
    sleep_fn(base_seconds * (2 ** (attempt - 1)))


def build_alpha_vantage_company_overview_url(symbol: str, api_key: str) -> str:
    request_symbol = normalize_symbol_for_alpha_vantage(symbol)
    query = urlencode(
        {
            "function": "OVERVIEW",
            "symbol": request_symbol,
            "apikey": api_key,
        }
    )
    return f"https://www.alphavantage.co/query?{query}"


def fetch_alpha_vantage_company_overview(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> dict[str, object]:
    url = build_alpha_vantage_company_overview_url(symbol, api_key)
    payload: dict[str, object] | None = None
    attempts = max(1, max_attempts)

    for attempt in range(1, attempts + 1):
        try:
            with urlopen_fn(url) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            if _is_transient_http_error(error) and attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise AlphaVantageAnalystResponseError(f"Alpha Vantage request failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise AlphaVantageAnalystResponseError(
                f"Alpha Vantage request failed after {attempts} attempts: {error}"
            ) from error

    if payload is None:
        raise AlphaVantageAnalystResponseError("Alpha Vantage request failed before receiving a payload")

    if "Note" in payload:
        raise AlphaVantageAnalystRateLimitError(str(payload["Note"]))
    if "Information" in payload:
        message = str(payload["Information"])
        if "Please consider spreading out your free API requests" in message or "rate limit" in message.lower():
            raise AlphaVantageAnalystRateLimitError(message)
        raise AlphaVantageAnalystResponseError(message)
    if "Error Message" in payload:
        raise AlphaVantageAnalystResponseError(str(payload["Error Message"]))

    return payload


def parse_alpha_vantage_analyst_consensus(
    symbol: str,
    payload: dict[str, object],
    as_of_date: date,
) -> AnalystConsensusSnapshot | None:
    if not payload:
        return None

    analyst_target_price = _parse_decimal(payload.get("AnalystTargetPrice"))
    strong_buy_count = _parse_int(payload.get("AnalystRatingStrongBuy"))
    buy_count = _parse_int(payload.get("AnalystRatingBuy"))
    hold_count = _parse_int(payload.get("AnalystRatingHold"))
    sell_count = _parse_int(payload.get("AnalystRatingSell"))
    strong_sell_count = _parse_int(payload.get("AnalystRatingStrongSell"))

    suggestion_label, suggestion_score = derive_analyst_suggestion(
        strong_buy_count=strong_buy_count,
        buy_count=buy_count,
        hold_count=hold_count,
        sell_count=sell_count,
        strong_sell_count=strong_sell_count,
    )

    if (
        analyst_target_price is None
        and strong_buy_count is None
        and buy_count is None
        and hold_count is None
        and sell_count is None
        and strong_sell_count is None
    ):
        return None

    return AnalystConsensusSnapshot(
        symbol=symbol,
        date=as_of_date,
        analyst_target_price=analyst_target_price,
        strong_buy_count=strong_buy_count,
        buy_count=buy_count,
        hold_count=hold_count,
        sell_count=sell_count,
        strong_sell_count=strong_sell_count,
        suggestion_label=suggestion_label,
        suggestion_score=suggestion_score,
    )


def derive_analyst_suggestion(
    strong_buy_count: int | None,
    buy_count: int | None,
    hold_count: int | None,
    sell_count: int | None,
    strong_sell_count: int | None,
) -> tuple[str | None, Decimal | None]:
    strong_buy = strong_buy_count or 0
    buy = buy_count or 0
    hold = hold_count or 0
    sell = sell_count or 0
    strong_sell = strong_sell_count or 0
    total = strong_buy + buy + hold + sell + strong_sell

    if total <= 0:
        return (None, None)

    weighted_raw = (strong_buy * 2) + buy - sell - (strong_sell * 2)
    score = Decimal(weighted_raw) / Decimal(total)

    if score >= Decimal("1.2"):
        return ("strong_buy", score)
    if score >= Decimal("0.35"):
        return ("buy", score)
    if score <= Decimal("-1.2"):
        return ("strong_sell", score)
    if score <= Decimal("-0.35"):
        return ("sell", score)
    return ("hold", score)


def persist_analyst_consensus(
    database_url: str,
    rows: list[AnalystConsensusSnapshot],
    connect_fn=connect_postgres,
) -> bool:
    if not rows:
        return False
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    connection = None
    cursor = None
    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.executemany(
            """
            insert into analyst_consensus_daily (
                symbol,
                date,
                analyst_target_price,
                strong_buy_count,
                buy_count,
                hold_count,
                sell_count,
                strong_sell_count,
                suggestion_label,
                suggestion_score,
                source
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (symbol, date, source) do update set
                analyst_target_price = excluded.analyst_target_price,
                strong_buy_count = excluded.strong_buy_count,
                buy_count = excluded.buy_count,
                hold_count = excluded.hold_count,
                sell_count = excluded.sell_count,
                strong_sell_count = excluded.strong_sell_count,
                suggestion_label = excluded.suggestion_label,
                suggestion_score = excluded.suggestion_score,
                ingested_at = now()
            """,
            [
                (
                    row.symbol,
                    row.date,
                    row.analyst_target_price,
                    row.strong_buy_count,
                    row.buy_count,
                    row.hold_count,
                    row.sell_count,
                    row.strong_sell_count,
                    row.suggestion_label,
                    row.suggestion_score,
                    row.source,
                )
                for row in rows
            ],
        )
        connection.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            if cursor is not None:
                cursor.close()
            if connection is not None:
                connection.close()
        except Exception:
            pass


class FinnhubAnalystResponseError(RuntimeError):
    pass


class FinnhubAnalystRateLimitError(RuntimeError):
    pass


class FinnhubAccessDeniedError(FinnhubAnalystResponseError):
    """Raised when Finnhub returns 403 for an endpoint the API key cannot access
    (e.g. premium-only endpoints such as price-target on the free tier)."""

    pass


def normalize_symbol_for_finnhub(symbol: str) -> str:
    if ":" in symbol:
        _, raw_symbol = symbol.split(":", 1)
        return raw_symbol or symbol
    return symbol


def build_finnhub_recommendation_url(symbol: str, api_key: str) -> str:
    query = urlencode({"symbol": normalize_symbol_for_finnhub(symbol), "token": api_key})
    return f"https://finnhub.io/api/v1/stock/recommendation?{query}"


def build_finnhub_price_target_url(symbol: str, api_key: str) -> str:
    query = urlencode({"symbol": normalize_symbol_for_finnhub(symbol), "token": api_key})
    return f"https://finnhub.io/api/v1/stock/price-target?{query}"


def _fetch_finnhub_json(
    url: str,
    symbol: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> object:
    payload = None
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            with urlopen_fn(url) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            if error.code == 429:
                raise FinnhubAnalystRateLimitError(f"Finnhub rate limit hit for {symbol}")
            if error.code == 403:
                raise FinnhubAccessDeniedError(
                    f"Finnhub request for {symbol} denied with HTTP 403 (no access to this resource)"
                ) from error
            if _is_transient_http_error(error) and attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise FinnhubAnalystResponseError(f"Finnhub request failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise FinnhubAnalystResponseError(
                f"Finnhub request failed after {attempts} attempts: {error}"
            ) from error

    if payload is None:
        raise FinnhubAnalystResponseError("Finnhub request failed before receiving a payload")
    return payload


def fetch_finnhub_recommendation_trends(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> list[dict[str, object]]:
    url = build_finnhub_recommendation_url(symbol, api_key)
    payload = _fetch_finnhub_json(url, symbol, urlopen_fn, max_attempts, base_backoff_seconds, sleep_fn)
    if isinstance(payload, dict) and "error" in payload:
        raise FinnhubAnalystResponseError(str(payload["error"]))
    if not isinstance(payload, list):
        raise FinnhubAnalystResponseError(f"Unexpected Finnhub recommendation response: {type(payload)}")
    return payload


def fetch_finnhub_price_target(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> dict[str, object]:
    url = build_finnhub_price_target_url(symbol, api_key)
    payload = _fetch_finnhub_json(url, symbol, urlopen_fn, max_attempts, base_backoff_seconds, sleep_fn)
    if not isinstance(payload, dict):
        raise FinnhubAnalystResponseError(f"Unexpected Finnhub price target response: {type(payload)}")
    if "error" in payload:
        raise FinnhubAnalystResponseError(str(payload["error"]))
    return payload


def parse_finnhub_analyst_consensus(
    symbol: str,
    recommendation_payload: list[dict[str, object]],
    price_target_payload: dict[str, object],
    as_of_date: date,
) -> AnalystConsensusSnapshot | None:
    strong_buy_count = None
    buy_count = None
    hold_count = None
    sell_count = None
    strong_sell_count = None

    if recommendation_payload:
        latest = recommendation_payload[0]
        strong_buy_count = _parse_int(latest.get("strongBuy"))
        buy_count = _parse_int(latest.get("buy"))
        hold_count = _parse_int(latest.get("hold"))
        sell_count = _parse_int(latest.get("sell"))
        strong_sell_count = _parse_int(latest.get("strongSell"))

    target = price_target_payload.get("targetMean") or price_target_payload.get("targetMedian")
    analyst_target_price = _parse_decimal(target)

    if (
        analyst_target_price is None
        and strong_buy_count is None
        and buy_count is None
        and hold_count is None
        and sell_count is None
        and strong_sell_count is None
    ):
        return None

    suggestion_label, suggestion_score = derive_analyst_suggestion(
        strong_buy_count=strong_buy_count,
        buy_count=buy_count,
        hold_count=hold_count,
        sell_count=sell_count,
        strong_sell_count=strong_sell_count,
    )

    return AnalystConsensusSnapshot(
        symbol=symbol,
        date=as_of_date,
        analyst_target_price=analyst_target_price,
        strong_buy_count=strong_buy_count,
        buy_count=buy_count,
        hold_count=hold_count,
        sell_count=sell_count,
        strong_sell_count=strong_sell_count,
        suggestion_label=suggestion_label,
        suggestion_score=suggestion_score,
        source="finnhub",
    )


def _parse_decimal(value: object) -> Decimal | None:
    if value in {None, "", "None"}:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_int(value: object) -> int | None:
    if value in {None, "", "None"}:
        return None
    try:
        return int(str(value))
    except ValueError:
        return None
