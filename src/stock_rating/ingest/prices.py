from csv import DictReader
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from io import StringIO
import json
from urllib.parse import urlencode
from urllib.request import urlopen

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


FREE_PRICE_PROVIDERS = ("alpha_vantage", "twelve_data", "stooq")

TWELVE_DATA_EXCHANGE_ALIASES = {
    "ETR": "XETR",
    "TSE": "TSX",
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


class StooqResponseError(RuntimeError):
    pass


def get_price_provider_status(
    alpha_vantage_api_key: str,
    twelve_data_api_key: str,
    stooq_api_key: str = "",
) -> list[PriceProviderStatus]:
    return [
        PriceProviderStatus(provider="alpha_vantage", configured=bool(alpha_vantage_api_key)),
        PriceProviderStatus(provider="twelve_data", configured=bool(twelve_data_api_key)),
        PriceProviderStatus(provider="stooq", configured=bool(stooq_api_key)),
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
                volume=int(str(volume)),
                source="alpha_vantage",
            )
        )

    return bars


def normalize_symbol_for_alpha_vantage(symbol: str) -> str:
    if ":" not in symbol:
        return symbol

    _, raw_symbol = symbol.split(":", 1)
    return raw_symbol or symbol


def normalize_symbol_for_twelve_data(symbol: str) -> str:
    if ":" not in symbol:
        return symbol

    exchange, raw_symbol = symbol.split(":", 1)
    normalized_exchange = TWELVE_DATA_EXCHANGE_ALIASES.get(exchange.upper())
    if normalized_exchange:
        return f"{raw_symbol}:{normalized_exchange}"
    return raw_symbol or symbol


def normalize_symbol_for_stooq(symbol: str) -> str:
    if ":" not in symbol:
        return f"{symbol.lower()}.us"

    exchange, raw_symbol = symbol.split(":", 1)
    normalized_symbol = raw_symbol.lower()
    exchange_code = exchange.upper()
    if exchange_code == "TSE":
        return f"{normalized_symbol}.ca"
    if exchange_code == "ETR":
        return f"{normalized_symbol}.de"
    return normalized_symbol


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
) -> list[DailyPriceBar]:
    url = build_alpha_vantage_daily_adjusted_url(symbol, api_key, outputsize=outputsize)
    with urlopen_fn(url) as response:
        payload = json.loads(response.read().decode("utf-8"))

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
                volume=int(str(raw_bar.get("volume", 0))),
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
                volume=int(str(volume_value)),
                source="stooq",
            )
        )
    return sorted(bars, key=lambda bar: bar.date, reverse=True)


def fetch_stooq_daily(symbol: str, api_key: str, urlopen_fn=urlopen) -> list[DailyPriceBar]:
    url = build_stooq_daily_url(symbol, api_key)
    with urlopen_fn(url) as response:
        payload = response.read().decode("utf-8")

    lowered = payload.lower()
    if "get your apikey" in lowered:
        raise StooqResponseError("Stooq API key is invalid or missing")

    bars = parse_stooq_daily_csv(symbol, payload)
    if not bars:
        raise StooqResponseError(f"No daily bars returned for {symbol}")
    return bars


def fetch_twelve_data_time_series(symbol: str, api_key: str, urlopen_fn=urlopen) -> list[DailyPriceBar]:
    url = build_twelve_data_time_series_url(symbol, api_key)
    with urlopen_fn(url) as response:
        payload = json.loads(response.read().decode("utf-8"))

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
