from datetime import UTC, date, datetime
from decimal import Decimal
import json

from stock_rating.ingest.prices import (
    AlphaVantageResponseError,
    AlphaVantageRateLimitError,
    DailyPriceBar,
    StooqResponseError,
    TwelveDataRateLimitError,
    build_alpha_vantage_daily_adjusted_url,
    build_stooq_daily_url,
    build_twelve_data_time_series_url,
    fetch_alpha_vantage_daily_adjusted,
    fetch_stooq_daily,
    fetch_twelve_data_time_series,
    get_price_provider_status,
    parse_alpha_vantage_daily_adjusted,
    parse_stooq_daily_csv,
    parse_twelve_data_time_series,
    persist_price_bars,
)
from stock_rating.repository.symbols import update_symbol_last_price_refresh_at


def test_free_price_provider_status_marks_stooq_configured() -> None:
    statuses = get_price_provider_status(alpha_vantage_api_key="", twelve_data_api_key="", stooq_api_key="stooq-key")
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


def test_build_alpha_vantage_daily_adjusted_url_supports_full_outputsize() -> None:
    url = build_alpha_vantage_daily_adjusted_url("AAPL", "demo-key", outputsize="full")

    assert "outputsize=full" in url


def test_build_alpha_vantage_daily_adjusted_url_strips_exchange_prefix() -> None:
    url = build_alpha_vantage_daily_adjusted_url("NASDAQ:GOOGL", "demo-key")

    assert "symbol=GOOGL" in url


def test_build_alpha_vantage_daily_adjusted_url_uses_tsx_override_for_fairfax() -> None:
    url = build_alpha_vantage_daily_adjusted_url("TSE:FFH", "demo-key")

    assert "symbol=FFH.TRT" in url


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


def test_fetch_alpha_vantage_daily_adjusted_retries_transient_failure_then_succeeds() -> None:
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
    calls = {"count": 0}

    def _flaky_urlopen(_: str):
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary timeout")
        return _FakeHttpResponse(payload)

    bars = fetch_alpha_vantage_daily_adjusted(
        "AAPL",
        "demo-key",
        urlopen_fn=_flaky_urlopen,
        sleep_fn=lambda _: None,
    )

    assert len(bars) == 1
    assert calls["count"] == 3


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


def test_build_twelve_data_time_series_url_normalizes_exchange_prefixes() -> None:
    nasdaq_url = build_twelve_data_time_series_url("NASDAQ:GOOGL", "demo-key")
    tsx_url = build_twelve_data_time_series_url("TSE:FFH", "demo-key")
    xetr_url = build_twelve_data_time_series_url("ETR:AIXA", "demo-key")

    assert "symbol=GOOGL" in nasdaq_url
    assert "symbol=FFH%3ATSX" in tsx_url
    assert "symbol=AIXA%3AXETR" in xetr_url


def test_build_stooq_daily_url_normalizes_exchange_prefixes() -> None:
    us_url = build_stooq_daily_url("SKYW", "stooq-key")
    tsx_url = build_stooq_daily_url("TSE:FFH", "stooq-key")
    xetr_url = build_stooq_daily_url("ETR:AIXA", "stooq-key")
    stockholm_url = build_stooq_daily_url("TEL2-B.ST", "stooq-key")

    assert "s=skyw.us" in us_url
    assert "s=ffh.ca" in tsx_url
    assert "s=aixa.de" in xetr_url
    assert "s=tel2-b.st" in stockholm_url
    assert "apikey=stooq-key" in tsx_url


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


def test_fetch_twelve_data_time_series_retries_transient_failure_then_succeeds() -> None:
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
    calls = {"count": 0}

    def _flaky_urlopen(_: str):
        calls["count"] += 1
        if calls["count"] < 2:
            raise ConnectionError("temporary connection reset")
        return _FakeHttpResponse(payload)

    bars = fetch_twelve_data_time_series(
        "AAPL",
        "demo-key",
        urlopen_fn=_flaky_urlopen,
        sleep_fn=lambda _: None,
    )

    assert len(bars) == 1
    assert calls["count"] == 2


def test_parse_stooq_daily_csv_returns_bars() -> None:
    payload = "Date,Open,High,Low,Close,Volume\n2026-05-27,100.0,110.0,99.0,108.0,123456\n"

    bars = parse_stooq_daily_csv("TSE:FFH", payload)

    assert len(bars) == 1
    assert bars[0].symbol == "TSE:FFH"
    assert bars[0].source == "stooq"
    assert bars[0].close == Decimal("108.0")


def test_fetch_stooq_daily_raises_on_missing_api_key_message() -> None:
    try:
        fetch_stooq_daily(
            "TSE:FFH",
            "bad-key",
            urlopen_fn=lambda _: _FakeHttpResponseString("Get your apikey"),
        )
    except StooqResponseError as error:
        assert "invalid or missing" in str(error)
    else:
        raise AssertionError("Expected StooqResponseError")


def test_fetch_stooq_daily_parses_payload() -> None:
    payload = "Date,Open,High,Low,Close,Volume\n2026-05-27,100.0,110.0,99.0,108.0,123456\n"

    bars = fetch_stooq_daily("TSE:FFH", "stooq-key", urlopen_fn=lambda _: _FakeHttpResponseString(payload))

    assert len(bars) == 1
    assert bars[0].date.isoformat() == "2026-05-27"


def test_fetch_stooq_daily_retries_transient_failure_then_succeeds() -> None:
    payload = "Date,Open,High,Low,Close,Volume\n2026-05-27,100.0,110.0,99.0,108.0,123456\n"
    calls = {"count": 0}

    def _flaky_urlopen(_: str):
        calls["count"] += 1
        if calls["count"] < 3:
            raise TimeoutError("temporary timeout")
        return _FakeHttpResponseString(payload)

    bars = fetch_stooq_daily(
        "TSE:FFH",
        "stooq-key",
        urlopen_fn=_flaky_urlopen,
        sleep_fn=lambda _: None,
    )

    assert len(bars) == 1
    assert calls["count"] == 3


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


class _FakeHttpResponseString:
    def __init__(self, payload: str) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload.encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponseString":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


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
