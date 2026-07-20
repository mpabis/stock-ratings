from csv import DictReader
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import StringIO
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


FREE_PRICE_PROVIDERS = ("alpha_vantage", "twelve_data", "stooq", "yahoo")

TWELVE_DATA_EXCHANGE_ALIASES = {
    "ETR": "XETR",
    "TSE": "TSX",
}

STOOQ_EXCHANGE_SUFFIXES = {
    "NASDAQ": "us",
    "NYSE": "us",
    "AMEX": "us",
    "ARCA": "us",
    "ETR": "de",
    "XETR": "de",
    "TSE": "ca",
    "TSX": "ca",
}

YAHOO_EXCHANGE_SUFFIXES = {
    "ETR": "DE",
    "XETR": "DE",
}

TWELVE_DATA_STOOQ_FIRST_EXCHANGES = {"ETR", "XETR"}

ALPHA_VANTAGE_SYMBOL_OVERRIDES = {
    "TSE:FFH": "FFH.TRT",
}

ALPHA_VANTAGE_PREFIX_STRIP_EXCHANGES = {
    "NASDAQ",
    "NYSE",
    "AMEX",
    "ARCA",
}


@dataclass(frozen=True)
class PriceProviderStatus:
    provider: str
    configured: bool


@dataclass(frozen=True)
class DailyPriceBar:
    symbol: str
    date: date
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    adjusted_close: Decimal
    volume: int
    source: str


class AlphaVantageRateLimitError(RuntimeError):
    pass


class AlphaVantageResponseError(RuntimeError):
    pass


class TwelveDataRateLimitError(RuntimeError):
    pass


class TwelveDataResponseError(RuntimeError):
    pass


class YahooFinanceResponseError(RuntimeError):
    pass


class StooqResponseError(RuntimeError):
    pass


class StooqRateLimitError(RuntimeError):
    """Stooq's keyless CSV endpoint throttled/blocked the request.

    Stooq can return HTTP 403/429 when it throttles or blocks the caller.
    A symbol-level miss should fall through as a normal provider failure so the
    pipeline can continue with later symbols and downstream fallback providers.
    """


TRANSIENT_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}

# Codes Stooq returns when it is throttling/blocking the caller. Symbol-level
# misses are handled as normal provider failures instead of batch-wide throttle.
STOOQ_RATE_LIMIT_STATUS_CODES = {403, 429}


def _is_transient_http_error(error: HTTPError) -> bool:
    return error.code in TRANSIENT_HTTP_STATUS_CODES


def _sleep_backoff(attempt: int, base_seconds: float, sleep_fn=time.sleep) -> None:
    if attempt <= 0 or base_seconds <= 0:
        return
    sleep_fn(base_seconds * (2 ** (attempt - 1)))


def _parse_volume(value: object) -> int:
    return int(Decimal(str(value or "0")))


def get_price_provider_status(
    alpha_vantage_api_key: str,
    twelve_data_api_key: str,
    stooq_api_key: str = "",
) -> list[PriceProviderStatus]:
    return [
        PriceProviderStatus(provider="alpha_vantage", configured=bool(alpha_vantage_api_key)),
        PriceProviderStatus(provider="twelve_data", configured=bool(twelve_data_api_key)),
        PriceProviderStatus(provider="stooq", configured=bool(stooq_api_key)),
        PriceProviderStatus(provider="yahoo", configured=True),
    ]


def parse_alpha_vantage_daily_adjusted(symbol: str, payload: dict[str, object]) -> list[DailyPriceBar]:
    time_series = payload.get("Time Series (Daily)")
    if not isinstance(time_series, dict):
        return []

    bars: list[DailyPriceBar] = []
    for trading_date, raw_bar in sorted(time_series.items(), reverse=True):
        if not isinstance(raw_bar, dict):
            continue

        volume = raw_bar.get("6. volume", raw_bar.get("5. volume"))
        if volume is None:
            continue

        bars.append(
            DailyPriceBar(
                symbol=symbol,
                date=date.fromisoformat(str(trading_date)),
                open=Decimal(str(raw_bar["1. open"])),
                high=Decimal(str(raw_bar["2. high"])),
                low=Decimal(str(raw_bar["3. low"])),
                close=Decimal(str(raw_bar["4. close"])),
                adjusted_close=Decimal(str(raw_bar.get("5. adjusted close", raw_bar["4. close"]))),
                volume=_parse_volume(volume),
                source="alpha_vantage",
            )
        )

    return bars


def normalize_symbol_for_alpha_vantage(symbol: str) -> str:
    override = ALPHA_VANTAGE_SYMBOL_OVERRIDES.get(symbol)
    if override:
        return override

    if ":" not in symbol:
        return symbol

    exchange, raw_symbol = symbol.split(":", 1)
    if exchange.upper() in ALPHA_VANTAGE_PREFIX_STRIP_EXCHANGES and raw_symbol:
        return raw_symbol
    return symbol


def normalize_symbol_for_twelve_data(symbol: str) -> str:
    if ":" not in symbol:
        return symbol

    exchange, raw_symbol = symbol.split(":", 1)
    normalized_exchange = TWELVE_DATA_EXCHANGE_ALIASES.get(exchange.upper())
    if normalized_exchange:
        return f"{raw_symbol}:{normalized_exchange}"
    return raw_symbol or symbol


def normalize_symbol_for_stooq(symbol: str) -> str:
    lowered = symbol.lower()
    if lowered.endswith(".st"):
        return lowered

    if ":" not in symbol:
        return f"{lowered.replace('.', '-')}.us"

    exchange, raw_symbol = symbol.split(":", 1)
    normalized_symbol = raw_symbol.lower().replace(".", "-")
    exchange_code = exchange.upper()
    suffix = STOOQ_EXCHANGE_SUFFIXES.get(exchange_code)
    if suffix:
        return f"{normalized_symbol}.{suffix}"
    return normalized_symbol


def normalize_symbol_for_yahoo(symbol: str) -> str:
    if ":" not in symbol:
        return symbol

    exchange, raw_symbol = symbol.split(":", 1)
    suffix = YAHOO_EXCHANGE_SUFFIXES.get(exchange.upper())
    if suffix and raw_symbol:
        return f"{raw_symbol}.{suffix}"
    return symbol


def stooq_supports_symbol(symbol: str) -> bool:
    lowered = symbol.lower()
    if lowered.endswith(".st"):
        return False
    if ":" not in symbol:
        return True

    exchange, _ = symbol.split(":", 1)
    return exchange.upper() not in {"TSE", "TSX"}


def prefer_stooq_before_twelve_data(symbol: str) -> bool:
    if ":" not in symbol:
        return False

    exchange, _ = symbol.split(":", 1)
    return exchange.upper() in TWELVE_DATA_STOOQ_FIRST_EXCHANGES


def prefer_yahoo_before_stooq(symbol: str) -> bool:
    if ":" not in symbol:
        return False

    exchange, _ = symbol.split(":", 1)
    return exchange.upper() in YAHOO_EXCHANGE_SUFFIXES


def build_alpha_vantage_daily_adjusted_url(symbol: str, api_key: str, outputsize: str = "compact") -> str:
    request_symbol = normalize_symbol_for_alpha_vantage(symbol)
    query = urlencode(
        {
            "function": "TIME_SERIES_DAILY",
            "symbol": request_symbol,
            "outputsize": outputsize,
            "apikey": api_key,
        }
    )
    return f"https://www.alphavantage.co/query?{query}"


def fetch_alpha_vantage_daily_adjusted(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    outputsize: str = "compact",
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> list[DailyPriceBar]:
    url = build_alpha_vantage_daily_adjusted_url(symbol, api_key, outputsize=outputsize)
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
            raise AlphaVantageResponseError(f"Alpha Vantage request failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise AlphaVantageResponseError(
                f"Alpha Vantage request failed after {attempts} attempts: {error}"
            ) from error

    if payload is None:
        raise AlphaVantageResponseError("Alpha Vantage request failed before receiving a payload")

    if "Note" in payload:
        raise AlphaVantageRateLimitError(str(payload["Note"]))
    if "Information" in payload:
        message = str(payload["Information"])
        if "Please consider spreading out your free API requests" in message or "rate limit" in message.lower():
            raise AlphaVantageRateLimitError(message)
        raise AlphaVantageResponseError(message)
    if "Error Message" in payload:
        raise AlphaVantageResponseError(str(payload["Error Message"]))

    bars = parse_alpha_vantage_daily_adjusted(symbol, payload)
    if not bars:
        raise AlphaVantageResponseError(f"No daily bars returned for {symbol}")
    return bars


def fetch_yahoo_daily(
    symbol: str,
    urlopen_fn=urlopen,
    range_value: str = "6mo",
    interval: str = "1d",
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> list[DailyPriceBar]:
    url = build_yahoo_chart_url(symbol, range_value=range_value, interval=interval)
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
            raise YahooFinanceResponseError(f"Yahoo Finance request failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise YahooFinanceResponseError(
                f"Yahoo Finance request failed after {attempts} attempts: {error}"
            ) from error

    if payload is None:
        raise YahooFinanceResponseError("Yahoo Finance request failed before receiving a payload")

    bars = parse_yahoo_chart_response(symbol, payload)
    if not bars:
        raise YahooFinanceResponseError(f"No daily bars returned for {symbol}")
    return bars


def parse_twelve_data_time_series(symbol: str, payload: dict[str, object]) -> list[DailyPriceBar]:
    values = payload.get("values")
    if not isinstance(values, list):
        return []

    bars: list[DailyPriceBar] = []
    for raw_bar in values:
        if not isinstance(raw_bar, dict):
            continue

        trading_date = str(raw_bar["datetime"]).split(" ")[0]
        close = Decimal(str(raw_bar["close"]))
        bars.append(
            DailyPriceBar(
                symbol=symbol,
                date=date.fromisoformat(trading_date),
                open=Decimal(str(raw_bar["open"])),
                high=Decimal(str(raw_bar["high"])),
                low=Decimal(str(raw_bar["low"])),
                close=close,
                adjusted_close=close,
                volume=_parse_volume(raw_bar.get("volume", 0)),
                source="twelve_data",
            )
        )

    return bars


def build_twelve_data_time_series_url(symbol: str, api_key: str) -> str:
    request_symbol = normalize_symbol_for_twelve_data(symbol)
    query = urlencode(
        {
            "symbol": request_symbol,
            "interval": "1day",
            "outputsize": "30",
            "apikey": api_key,
        }
    )
    return f"https://api.twelvedata.com/time_series?{query}"


def build_yahoo_chart_url(symbol: str, range_value: str = "6mo", interval: str = "1d") -> str:
    request_symbol = normalize_symbol_for_yahoo(symbol)
    query = urlencode(
        {
            "range": range_value,
            "interval": interval,
            "includeAdjustedClose": "true",
            "events": "div,splits",
        }
    )
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(request_symbol)}?{query}"


def build_stooq_daily_url(symbol: str, api_key: str) -> str:
    request_symbol = normalize_symbol_for_stooq(symbol)
    query = urlencode(
        {
            "s": request_symbol,
            "i": "d",
            "apikey": api_key,
        }
    )
    return f"https://stooq.com/q/d/l/?{query}"


def parse_yahoo_chart_response(symbol: str, payload: dict[str, object]) -> list[DailyPriceBar]:
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        return []

    if chart.get("error") is not None:
        return []

    results = chart.get("result")
    if not isinstance(results, list) or not results or not isinstance(results[0], dict):
        return []

    result = results[0]
    timestamps = result.get("timestamp")
    indicators = result.get("indicators")
    if not isinstance(timestamps, list) or not isinstance(indicators, dict):
        return []

    quotes = indicators.get("quote")
    if not isinstance(quotes, list) or not quotes or not isinstance(quotes[0], dict):
        return []

    quote_values = quotes[0]
    opens = quote_values.get("open")
    highs = quote_values.get("high")
    lows = quote_values.get("low")
    closes = quote_values.get("close")
    volumes = quote_values.get("volume")
    if not all(isinstance(values, list) for values in (opens, highs, lows, closes, volumes)):
        return []

    adjclose_values = None
    adjclose_group = indicators.get("adjclose")
    if isinstance(adjclose_group, list) and adjclose_group and isinstance(adjclose_group[0], dict):
        adjclose_values = adjclose_group[0].get("adjclose")

    bars: list[DailyPriceBar] = []
    for index, timestamp in enumerate(timestamps):
        if not isinstance(timestamp, int):
            continue
        if index >= len(opens) or index >= len(highs) or index >= len(lows) or index >= len(closes) or index >= len(volumes):
            continue

        open_value = opens[index]
        high_value = highs[index]
        low_value = lows[index]
        close_value = closes[index]
        volume_value = volumes[index]
        if None in {open_value, high_value, low_value, close_value}:
            continue

        adjusted_close = close_value
        if isinstance(adjclose_values, list) and index < len(adjclose_values) and adjclose_values[index] is not None:
            adjusted_close = adjclose_values[index]

        bars.append(
            DailyPriceBar(
                symbol=symbol,
                date=date.fromtimestamp(timestamp),
                open=Decimal(str(open_value)),
                high=Decimal(str(high_value)),
                low=Decimal(str(low_value)),
                close=Decimal(str(close_value)),
                adjusted_close=Decimal(str(adjusted_close)),
                volume=_parse_volume(volume_value),
                source="yahoo",
            )
        )

    return sorted(bars, key=lambda bar: bar.date, reverse=True)


def parse_stooq_daily_csv(symbol: str, payload: str) -> list[DailyPriceBar]:
    bars: list[DailyPriceBar] = []
    reader = DictReader(StringIO(payload))
    for row in reader:
        if not row:
            continue
        trading_date = row.get("Date")
        close = row.get("Close")
        if not trading_date or not close or close.lower() == "n/d":
            continue
        volume_value = row.get("Volume", "0") or "0"
        bars.append(
            DailyPriceBar(
                symbol=symbol,
                date=date.fromisoformat(trading_date),
                open=Decimal(str(row["Open"])),
                high=Decimal(str(row["High"])),
                low=Decimal(str(row["Low"])),
                close=Decimal(str(close)),
                adjusted_close=Decimal(str(close)),
                volume=_parse_volume(volume_value),
                source="stooq",
            )
        )
    return sorted(bars, key=lambda bar: bar.date, reverse=True)


def fetch_stooq_daily(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> list[DailyPriceBar]:
    url = build_stooq_daily_url(symbol, api_key)
    payload: str | None = None
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            with urlopen_fn(url) as response:
                payload = response.read().decode("utf-8")
            break
        except HTTPError as error:
            if error.code in STOOQ_RATE_LIMIT_STATUS_CODES:
                raise StooqRateLimitError(
                    f"Stooq rate-limited or blocked the request (HTTP {error.code}) for {symbol}"
                ) from error
            if _is_transient_http_error(error) and attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise StooqResponseError(f"Stooq request failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise StooqResponseError(f"Stooq request failed after {attempts} attempts: {error}") from error

    if payload is None:
        raise StooqResponseError("Stooq request failed before receiving a payload")

    lowered = payload.lower()
    if "get your apikey" in lowered:
        raise StooqResponseError("Stooq API key is invalid or missing")

    bars = parse_stooq_daily_csv(symbol, payload)
    if not bars:
        raise StooqResponseError(f"No daily bars returned for {symbol}")
    return bars


def fetch_twelve_data_time_series(
    symbol: str,
    api_key: str,
    urlopen_fn=urlopen,
    max_attempts: int = 3,
    base_backoff_seconds: float = 0.5,
    sleep_fn=time.sleep,
) -> list[DailyPriceBar]:
    url = build_twelve_data_time_series_url(symbol, api_key)
    payload: dict[str, object] | None = None
    attempts = max(1, max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            with urlopen_fn(url) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except HTTPError as error:
            if error.code == 429:
                raise TwelveDataRateLimitError("Twelve Data request failed with HTTP 429") from error
            if _is_transient_http_error(error) and attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise TwelveDataResponseError(f"Twelve Data request failed with HTTP {error.code}") from error
        except (URLError, TimeoutError, ConnectionError) as error:
            if attempt < attempts:
                _sleep_backoff(attempt, base_backoff_seconds, sleep_fn=sleep_fn)
                continue
            raise TwelveDataResponseError(
                f"Twelve Data request failed after {attempts} attempts: {error}"
            ) from error

    if payload is None:
        raise TwelveDataResponseError("Twelve Data request failed before receiving a payload")

    if payload.get("code") == 429:
        raise TwelveDataRateLimitError(str(payload.get("message", "Twelve Data rate limit reached")))
    if payload.get("status") == "error":
        raise TwelveDataResponseError(str(payload.get("message", "Twelve Data returned an error")))

    bars = parse_twelve_data_time_series(symbol, payload)
    if not bars:
        raise TwelveDataResponseError(f"No daily bars returned for {symbol}")
    return bars


def persist_price_bars(database_url: str, bars: list[DailyPriceBar], connect_fn=connect_postgres) -> bool:
    if not bars:
        return False
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.executemany(
            """
            insert into price_daily (
                symbol,
                date,
                open,
                high,
                low,
                close,
                adjusted_close,
                volume,
                source
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            on conflict (symbol, date, source) do update set
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                adjusted_close = excluded.adjusted_close,
                volume = excluded.volume,
                ingested_at = now()
            """,
            [
                (
                    bar.symbol,
                    bar.date,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.adjusted_close,
                    bar.volume,
                    bar.source,
                )
                for bar in bars
            ],
        )
        connection.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass
