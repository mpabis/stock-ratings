from datetime import date
from decimal import Decimal

from stock_rating.repository.prices import load_recent_price_bars


class _FakePriceReadCursor:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows
        self.closed = False
        self.params: tuple[object, ...] | None = None

    def execute(self, query: str, params: tuple[object, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    def close(self) -> None:
        self.closed = True


class _FakePriceReadConnection:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.cursor_instance = _FakePriceReadCursor(rows)
        self.closed = False

    def cursor(self) -> _FakePriceReadCursor:
        return self.cursor_instance

    def close(self) -> None:
        self.closed = True


def test_load_recent_price_bars_maps_rows() -> None:
    connection = _FakePriceReadConnection(
        [
            (
                "AAPL",
                date(2026, 5, 29),
                Decimal("100"),
                Decimal("110"),
                Decimal("99"),
                Decimal("108"),
                Decimal("107"),
                123456,
                "alpha_vantage",
            )
        ]
    )

    bars = load_recent_price_bars(
        "postgresql://example",
        "AAPL",
        limit=20,
        connect_fn=lambda _: connection,
    )

    assert connection.cursor_instance.params == ("AAPL", 20)
    assert len(bars) == 1
    assert bars[0].symbol == "AAPL"
    assert bars[0].adjusted_close == Decimal("107")
    assert bars[0].volume == 123456
    assert bars[0].source == "alpha_vantage"
