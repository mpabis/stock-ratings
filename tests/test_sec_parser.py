from datetime import UTC, datetime
from decimal import Decimal
import json

from stock_rating.ingest.sec_companyfacts import (
    SEC_COMPANY_FACTS_URL,
    SEC_TICKER_MAPPING_URL,
    build_sec_company_facts_url,
    fetch_sec_ticker_mapping,
    normalize_symbol_for_sec,
    parse_company_facts,
    parse_sec_ticker_mapping,
    persist_fundamental_facts,
)


def test_sec_endpoints_are_https() -> None:
    assert SEC_COMPANY_FACTS_URL.startswith("https://")
    assert SEC_TICKER_MAPPING_URL.startswith("https://")


def test_build_sec_company_facts_url_pads_cik() -> None:
    assert build_sec_company_facts_url("320193").endswith("CIK0000320193.json")


def test_normalize_symbol_for_sec_strips_exchange_and_rewrites_class_shares() -> None:
    assert normalize_symbol_for_sec("NASDAQ:GOOGL") == "GOOGL"
    assert normalize_symbol_for_sec("BRK.B") == "BRK-B"


def test_parse_sec_ticker_mapping_supports_sec_object_payload() -> None:
    payload = {
        "0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."},
        "1": {"ticker": "BRK-B", "cik_str": 1067983, "title": "Berkshire Hathaway Inc."},
    }

    mappings = parse_sec_ticker_mapping(payload)

    assert mappings["AAPL"].cik == "0000320193"
    assert mappings["BRK-B"].company_name == "Berkshire Hathaway Inc."


def test_parse_company_facts_extracts_core_metrics() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01", "val": 1000}
                        ]
                    }
                },
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01", "val": 120}
                        ]
                    }
                },
                "NetCashProvidedByUsedInOperatingActivities": {
                    "units": {
                        "USD": [
                            {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01", "val": 180}
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01", "val": 2500}
                        ]
                    }
                },
                "Liabilities": {
                    "units": {
                        "USD": [
                            {"fy": 2025, "fp": "FY", "form": "10-K", "filed": "2025-11-01", "val": 900}
                        ]
                    }
                },
            }
        }
    }

    facts = parse_company_facts("AAPL", "0000320193", payload)

    assert {fact.metric for fact in facts} == {
        "revenue",
        "net_income",
        "operating_cash_flow",
        "assets",
        "liabilities",
    }
    assert all(fact.symbol == "AAPL" for fact in facts)
    assert all(fact.cik == "0000320193" for fact in facts)


class _FakeHttpResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_fetch_sec_ticker_mapping_uses_user_agent_and_parses_payload() -> None:
    payload = {
        "0": {"ticker": "AAPL", "cik_str": 320193, "title": "Apple Inc."},
    }
    captured_headers: list[str] = []

    def _fake_urlopen(request):
        captured_headers.append(request.headers["User-agent"])
        return _FakeHttpResponse(payload)

    mappings = fetch_sec_ticker_mapping("stock-rating-test@example.com", urlopen_fn=_fake_urlopen)

    assert mappings["AAPL"].company_name == "Apple Inc."
    assert captured_headers == ["stock-rating-test@example.com"]


class _FakeFundamentalCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakeFundamentalConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeFundamentalCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeFundamentalCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_persist_fundamental_facts_inserts_rows() -> None:
    fake_connection = _FakeFundamentalConnection()
    persisted = persist_fundamental_facts(
        database_url="postgresql://example",
        facts=[
            __import__("stock_rating.ingest.sec_companyfacts", fromlist=["FundamentalFact"]).FundamentalFact(
                cik="0000320193",
                symbol="AAPL",
                fiscal_period="FY",
                fiscal_year=2025,
                form="10-K",
                metric="revenue",
                value=Decimal("1000"),
                unit="USD",
                filed_at=datetime(2025, 11, 1, tzinfo=UTC),
            )
        ],
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executemany_calls) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True
