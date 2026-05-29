from datetime import date, datetime, UTC
from decimal import Decimal

from stock_rating.pipeline.report import RatingSnapshot, SourceRefreshSummary, render_dashboard_html


def test_render_dashboard_places_ratings_before_portfolio_snapshot() -> None:
    html = render_dashboard_html(
        ratings=[
            RatingSnapshot(
                symbol="AAPL",
                company_name="Apple Inc.",
                rating_score=78,
                rating_label="B / Attractive",
                freshness_status="fresh",
                freshest_input_date=date(2026, 5, 28),
                valuation_score=Decimal("72.0"),
                quality_score=Decimal("68.0"),
                growth_score=Decimal("74.0"),
                momentum_score=Decimal("79.0"),
                risk_score=Decimal("41.0"),
                summary="Strong latest profile.",
            )
        ],
        latest_run=("run-123", "partial", datetime(2026, 5, 28, 23, 4, 14, tzinfo=UTC), datetime(2026, 5, 28, 23, 4, 52, tzinfo=UTC)),
        latest_run_counts={"succeeded": 1},
        source_refresh_summaries=[
            SourceRefreshSummary(source="fred", calls=4, succeeded=4, failed=0, status="succeeded"),
            SourceRefreshSummary(source="sec_edgar", calls=3, succeeded=2, failed=1, status="partial"),
        ],
        table_counts={"symbols": 1, "ratings_daily": 1, "pipeline_runs": 1, "symbol_refresh_runs": 1, "price_daily": 1, "features_daily": 1},
        quality_alerts=[],
    )

    assert html.index("Latest ratings") < html.index("Portfolio snapshot")
    assert html.index("Portfolio snapshot") < html.index("Distribution")
    assert "Source calls" in html


def test_render_dashboard_includes_source_call_summary() -> None:
    html = render_dashboard_html(
        ratings=[],
        latest_run=("run-123", "success", datetime(2026, 5, 28, 23, 4, 14, tzinfo=UTC), datetime(2026, 5, 28, 23, 4, 52, tzinfo=UTC)),
        latest_run_counts={},
        source_refresh_summaries=[
            SourceRefreshSummary(source="fred", calls=4, succeeded=4, failed=0, status="succeeded"),
            SourceRefreshSummary(source="alpha_vantage", calls=2, succeeded=1, failed=1, status="partial"),
        ],
        table_counts={"symbols": 0, "ratings_daily": 0, "pipeline_runs": 0, "symbol_refresh_runs": 0, "price_daily": 0, "features_daily": 0},
        quality_alerts=[],
    )

    assert "FRED" in html
    assert "4 calls" in html
    assert "4 succeeded, 0 failed" in html
    assert "Alpha Vantage" in html
    assert "1 succeeded, 1 failed" in html