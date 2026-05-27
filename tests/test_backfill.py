from datetime import date
from decimal import Decimal

from stock_rating.ingest.prices import DailyPriceBar
from stock_rating.pipeline.backfill import backfill_symbols


def test_backfill_symbols_requests_full_history_and_marks_refresh() -> None:
    refreshed_symbols: list[str] = []

    succeeded, failed = backfill_symbols(
        database_url="postgresql://example",
        symbols=["AAPL", "MSFT"],
        fetch_bars_fn=lambda symbol: [
            DailyPriceBar(
                symbol=symbol,
                date=date(2026, 5, 27),
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="alpha_vantage",
            )
        ],
        persist_fn=lambda database_url, bars: True,
        mark_refreshed_fn=lambda database_url, symbol: refreshed_symbols.append(symbol) or True,
        sleep_fn=lambda seconds: None,
    )

    assert succeeded == 2
    assert failed == 0
    assert refreshed_symbols == ["AAPL", "MSFT"]


def test_backfill_symbols_counts_failures() -> None:
    succeeded, failed = backfill_symbols(
        database_url="postgresql://example",
        symbols=["AAPL"],
        fetch_bars_fn=lambda symbol: (_ for _ in ()).throw(RuntimeError("boom")),
        persist_fn=lambda database_url, bars: True,
        mark_refreshed_fn=lambda database_url, symbol: True,
        sleep_fn=lambda seconds: None,
    )

    assert succeeded == 0
    assert failed == 1