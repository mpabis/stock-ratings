from datetime import date

from stock_rating.quality.checks import SymbolQualitySnapshot, build_quality_alerts


def test_build_quality_alerts_flags_missing_price_and_rating() -> None:
    alerts = build_quality_alerts(
        [
            SymbolQualitySnapshot(
                symbol="AAPL",
                refresh_tier=1,
                last_price_date=None,
                latest_rating_date=None,
            )
        ],
        as_of=date(2026, 5, 27),
    )

    assert [alert.code for alert in alerts] == ["missing_price", "missing_rating"]


def test_build_quality_alerts_flags_stale_price_for_tier() -> None:
    alerts = build_quality_alerts(
        [
            SymbolQualitySnapshot(
                symbol="AAPL",
                refresh_tier=1,
                last_price_date=date(2026, 5, 24),
                latest_rating_date=date(2026, 5, 24),
            )
        ],
        as_of=date(2026, 5, 27),
    )

    assert [alert.code for alert in alerts] == ["stale_price"]


def test_build_quality_alerts_flags_stale_rating_when_price_is_newer() -> None:
    alerts = build_quality_alerts(
        [
            SymbolQualitySnapshot(
                symbol="AAPL",
                refresh_tier=2,
                last_price_date=date(2026, 5, 27),
                latest_rating_date=date(2026, 5, 26),
            )
        ],
        as_of=date(2026, 5, 27),
    )

    assert [alert.code for alert in alerts] == ["stale_rating"]