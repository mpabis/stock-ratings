from datetime import date
from decimal import Decimal
import json

from stock_rating.ingest.fred_macro import (
    CORE_FRED_SERIES,
    FRED_SERIES_BASE_URL,
    build_fred_series_observations_url,
    fetch_fred_series_observations,
    parse_fred_series_observations,
    persist_macro_observations,
)
from stock_rating.pipeline.daily import execute_macro_refresh
from stock_rating.transform.macro import compute_macro_features


def test_fred_endpoint_is_https() -> None:
    assert FRED_SERIES_BASE_URL.startswith("https://")
    assert CORE_FRED_SERIES == ("DGS10", "DGS2")


def test_build_fred_url_contains_series_and_api_key() -> None:
    url = build_fred_series_observations_url("DGS10", "demo")

    assert "series_id=DGS10" in url
    assert "api_key=demo" in url


def test_parse_fred_observations_skips_missing_values() -> None:
    payload = {
        "observations": [
            {"date": "2026-05-26", "value": "4.50"},
            {"date": "2026-05-27", "value": "."},
        ]
    }

    observations = parse_fred_series_observations("DGS10", payload)

    assert observations == [
        __import__("stock_rating.ingest.fred_macro", fromlist=["MacroObservation"]).MacroObservation(
            series_id="DGS10",
            date=date(2026, 5, 26),
            value=Decimal("4.50"),
        )
    ]


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_fetch_fred_series_observations_returns_payload() -> None:
    payload = {"observations": [{"date": "2026-05-26", "value": "4.5"}]}

    result = fetch_fred_series_observations("DGS10", "demo", urlopen_fn=lambda request: _FakeHttpResponse(payload))

    assert result == payload


class _FakeMacroCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakeMacroConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeMacroCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeMacroCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_persist_macro_observations_inserts_rows() -> None:
    fake_connection = _FakeMacroConnection()
    persisted = persist_macro_observations(
        database_url="postgresql://example",
        observations=[
            __import__("stock_rating.ingest.fred_macro", fromlist=["MacroObservation"]).MacroObservation(
                series_id="DGS10",
                date=date(2026, 5, 26),
                value=Decimal("4.50"),
            )
        ],
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executemany_calls) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True


def test_compute_macro_features_builds_yield_curve_slope() -> None:
    observations = {
        "DGS10": __import__("stock_rating.ingest.fred_macro", fromlist=["MacroObservation"]).MacroObservation(
            series_id="DGS10",
            date=date(2026, 5, 26),
            value=Decimal("4.60"),
        ),
        "DGS2": __import__("stock_rating.ingest.fred_macro", fromlist=["MacroObservation"]).MacroObservation(
            series_id="DGS2",
            date=date(2026, 5, 26),
            value=Decimal("3.90"),
        ),
    }

    features = compute_macro_features("AAPL", date(2026, 5, 27), observations)

    assert len(features) == 1
    assert features[0].feature_name == "yield_curve_slope"
    assert features[0].feature_value == Decimal("0.70")


def test_execute_macro_refresh_persists_each_core_series() -> None:
    persisted_series: list[str] = []

    refreshed = execute_macro_refresh(
        database_url="postgresql://example",
        api_key="fred-key",
        fetch_fn=lambda series_id, api_key: {"observations": [{"date": "2026-05-26", "value": "4.0"}]},
        parse_fn=lambda series_id, payload: [
            __import__("stock_rating.ingest.fred_macro", fromlist=["MacroObservation"]).MacroObservation(
                series_id=series_id,
                date=date(2026, 5, 26),
                value=Decimal("4.0"),
            )
        ],
        persist_fn=lambda database_url, observations: persisted_series.append(observations[0].series_id) or True,
    )

    assert refreshed == "succeeded"
    assert persisted_series == ["DGS10", "DGS2"]