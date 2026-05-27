from datetime import UTC, date, datetime
from decimal import Decimal
import json

from stock_rating.ingest.prices import (
    AlphaVantageResponseError,
    AlphaVantageRateLimitError,
    DailyPriceBar,
    TwelveDataRateLimitError,
    build_alpha_vantage_daily_adjusted_url,
    build_twelve_data_time_series_url,
    fetch_alpha_vantage_daily_adjusted,
    fetch_twelve_data_time_series,
    get_price_provider_status,
    parse_alpha_vantage_daily_adjusted,
    parse_twelve_data_time_series,
    persist_price_bars,
)
from stock_rating.repository.symbols import update_symbol_last_price_refresh_at


def test_free_price_provider_status_marks_stooq_configured() -> None:
    statuses = get_price_provider_status(alpha_vantage_api_key="", twelve_data_api_key="")
    providers = {status.provider: status.configured for status in statuses}

    assert providers["alpha_vantage"] is False
    assert providers["twelve_data"] is False
    assert providers["stooq"] is True


def test_parse_alpha_vantage_daily_adjusted_returns_sorted_bars() -> None:
    payload = {
        "Time Series (Daily)": {
            "2026-05-27": {
                "1. open": "201.10",
                "2. high": "203.50",
                "3. low": "200.00",
                "4. close": "202.75",
                "5. adjusted close": "202.50",
                "6. volume": "12345678",
            },
            "2026-05-26": {
                "1. open": "198.00",
                "2. high": "200.10",
                "3. low": "197.50",
                "4. close": "199.80",
                "5. adjusted close": "199.60",
                "6. volume": "8765432",
            },
        }
    }

    bars = parse_alpha_vantage_daily_adjusted("AAPL", payload)

    assert [bar.date.isoformat() for bar in bars] == ["2026-05-27", "2026-05-26"]
    assert bars[0].symbol == "AAPL"
    assert bars[0].adjusted_close == Decimal("202.50")
    assert bars[1].volume == 8765432


def test_parse_alpha_vantage_daily_adjusted_supports_free_daily_volume_field() -> None:
    payload = {
        "Time Series (Daily)": {
            "2026-05-27": {
                "1. open": "201.10",
                "2. high": "203.50",
                "3. low": "200.00",
                "4. close": "202.75",
                "5. volume": "12345678",
            }
        }
    }

    bars = parse_alpha_vantage_daily_adjusted("AAPL", payload)

    assert len(bars) == 1
    assert bars[0].adjusted_close == Decimal("202.75")
    assert bars[0].volume == 12345678


def test_build_alpha_vantage_daily_adjusted_url_contains_expected_query() -> None:
    url = build_alpha_vantage_daily_adjusted_url("AAPL", "demo-key")

    assert "TIME_SERIES_DAILY" in url
    assert "symbol=AAPL" in url
    assert "apikey=demo-key" in url


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_fetch_alpha_vantage_daily_adjusted_parses_payload() -> None:
    payload = {
        "Time Series (Daily)": {
            "2026-05-27": {
                "1. open": "201.10",
                "2. high": "203.50",
                "3. low": "200.00",
                "4. close": "202.75",
                "5. adjusted close": "202.50",
                "6. volume": "12345678",
            }
        }
    }

    bars = fetch_alpha_vantage_daily_adjusted("AAPL", "demo-key", urlopen_fn=lambda _: _FakeHttpResponse(payload))

    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"


def test_fetch_alpha_vantage_daily_adjusted_raises_on_rate_limit() -> None:
    payload = {"Note": "Thank you for using Alpha Vantage"}

    try:
        fetch_alpha_vantage_daily_adjusted("AAPL", "demo-key", urlopen_fn=lambda _: _FakeHttpResponse(payload))
    except AlphaVantageRateLimitError as error:
        assert "Alpha Vantage" in str(error)
    else:
        raise AssertionError("Expected AlphaVantageRateLimitError")


def test_fetch_alpha_vantage_daily_adjusted_raises_on_information_rate_limit_message() -> None:
    payload = {"Information": "Please consider spreading out your free API requests more sparingly"}

    try:
        fetch_alpha_vantage_daily_adjusted("AAPL", "demo-key", urlopen_fn=lambda _: _FakeHttpResponse(payload))
    except AlphaVantageRateLimitError as error:
        assert "spreading out" in str(error)
    else:
        raise AssertionError("Expected AlphaVantageRateLimitError")


def test_fetch_alpha_vantage_daily_adjusted_raises_on_information_message() -> None:
    payload = {"Information": "This is a premium endpoint"}

    try:
        fetch_alpha_vantage_daily_adjusted("AAPL", "demo-key", urlopen_fn=lambda _: _FakeHttpResponse(payload))
    except AlphaVantageResponseError as error:
        assert "premium endpoint" in str(error)
    else:
        raise AssertionError("Expected AlphaVantageResponseError")


def test_build_twelve_data_time_series_url_contains_expected_query() -> None:
    url = build_twelve_data_time_series_url("AAPL", "demo-key")

    assert "time_series" in url
    assert "symbol=AAPL" in url
    assert "apikey=demo-key" in url


def test_parse_twelve_data_time_series_returns_bars() -> None:
    payload = {
        "values": [
            {
                "datetime": "2026-05-27",
                "open": "201.10",
                "high": "203.50",
                "low": "200.00",
                "close": "202.75",
                "volume": "12345678",
            }
        ]
    }

    bars = parse_twelve_data_time_series("AAPL", payload)

    assert len(bars) == 1
    assert bars[0].source == "twelve_data"
    assert bars[0].adjusted_close == Decimal("202.75")


def test_fetch_twelve_data_time_series_raises_on_rate_limit() -> None:
    payload = {"code": 429, "message": "credits exceeded"}

    try:
        fetch_twelve_data_time_series("AAPL", "demo-key", urlopen_fn=lambda _: _FakeHttpResponse(payload))
    except TwelveDataRateLimitError as error:
        assert "credits exceeded" in str(error)
    else:
        raise AssertionError("Expected TwelveDataRateLimitError")


class _FakePriceCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakePriceConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakePriceCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakePriceCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_persist_price_bars_inserts_rows() -> None:
    bars = [
        DailyPriceBar(
            symbol="AAPL",
            date=date(2026, 5, 27),
            open=Decimal("201.10"),
            high=Decimal("203.50"),
            low=Decimal("200.00"),
            close=Decimal("202.75"),
            adjusted_close=Decimal("202.50"),
            volume=12345678,
            source="alpha_vantage",
        )
    ]
    fake_connection = _FakePriceConnection()

    persisted = persist_price_bars(
        database_url="postgresql://example",
        bars=bars,
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executemany_calls) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True


def test_persist_price_bars_skips_without_database_or_bars() -> None:
    assert persist_price_bars(database_url="", bars=[]) is False


class _FakeSymbolUpdateCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self.closed = False

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.executed.append((query, params))

    def close(self) -> None:
        self.closed = True


class _FakeSymbolUpdateConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeSymbolUpdateCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeSymbolUpdateCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_update_symbol_last_price_refresh_at_updates_timestamp() -> None:
    fake_connection = _FakeSymbolUpdateConnection()

    updated = update_symbol_last_price_refresh_at(
        database_url="postgresql://example",
        symbol="AAPL",
        refreshed_at=datetime(2026, 5, 27, 22, 30, tzinfo=UTC),
        connect_fn=lambda _: fake_connection,
    )

    assert updated is True
    assert len(fake_connection.cursor_instance.executed) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True
