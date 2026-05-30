from datetime import UTC, date, datetime
from decimal import Decimal

from stock_rating.repository.fundamentals import load_latest_fundamental_facts


class _FakeFundamentalCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.closed = False

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class _FakeFundamentalConnection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.cursor_instance = _FakeFundamentalCursor(rows)
        self.closed = False

    def cursor(self) -> _FakeFundamentalCursor:
        return self.cursor_instance

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


def test_load_latest_fundamental_facts_maps_period_metadata_columns() -> None:
    filed_at = datetime(2026, 5, 29, tzinfo=UTC)
    rows = [
        (
            "0000320193",
            "AAPL",
            "FY",
            2025,
            "10-K",
            "revenue",
            Decimal("100"),
            "USD",
            date(2024, 10, 1),
            date(2025, 9, 30),
            "CY2025",
            filed_at,
            "sec_edgar",
        )
    ]
    connection = _FakeFundamentalConnection(rows)

    facts = load_latest_fundamental_facts(
        "postgresql://example",
        "AAPL",
        connect_fn=lambda _: connection,
    )

    assert len(facts) == 1
    assert facts[0].period_start == date(2024, 10, 1)
    assert facts[0].period_end == date(2025, 9, 30)
    assert facts[0].frame == "CY2025"
    assert facts[0].filed_at == filed_at
    assert facts[0].source == "sec_edgar"


def test_load_latest_fundamental_facts_maps_legacy_columns() -> None:
    filed_at = datetime(2026, 5, 29, tzinfo=UTC)
    rows = [
        (
            "0000320193",
            "AAPL",
            "FY",
            2025,
            "10-K",
            "revenue",
            Decimal("100"),
            "USD",
            filed_at,
            "sec_edgar",
        )
    ]
    connection = _FakeFundamentalConnection(rows)

    facts = load_latest_fundamental_facts(
        "postgresql://example",
        "AAPL",
        connect_fn=lambda _: connection,
    )

    assert len(facts) == 1
    assert facts[0].period_start is None
    assert facts[0].period_end is None
    assert facts[0].frame is None
    assert facts[0].filed_at == filed_at
    assert facts[0].source == "sec_edgar"
