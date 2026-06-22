import json
from datetime import date, datetime, UTC
from decimal import Decimal

from stock_rating.pipeline.report import (
    fetch_source_refresh_summaries_from_db,
    fetch_latest_ratings,
    RatingSnapshot,
    SourceRefreshSummary,
    render_dashboard_json,
    render_dashboard_html,
    render_dashboard_markdown,
    render_factor_cell,
    render_methodology_html,
    render_methodology_markdown,
    render_rating_row,
    yahoo_finance_symbol,
    yahoo_finance_url,
)


def _snapshot(**overrides) -> RatingSnapshot:
    base = dict(
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
    base.update(overrides)
    return RatingSnapshot(**base)


def test_factor_cell_shows_grade_when_provided() -> None:
    assert '<sup class="factor-grade">A</sup>' in render_factor_cell("Valuation", Decimal("90"), "A")
    assert "factor-grade" not in render_factor_cell("Valuation", Decimal("90"), None)


def test_rating_row_renders_analyst_revision_grades_and_benchmarks() -> None:
    row = render_rating_row(
        _snapshot(
            analyst_revision_score=Decimal("62"),
            valuation_grade="A",
            analyst_revision_grade="B",
            piotroski_fscore=Decimal("7"),
            piotroski_signals_available=Decimal("9"),
            magic_formula_combined_rank=Decimal("3"),
            acquirers_multiple=Decimal("8.4"),
        )
    )
    assert "Rev" in row  # analyst-revision factor cell (short name)
    assert '<sup class="factor-grade">A</sup>' in row  # valuation grade
    assert '<sup class="factor-grade">B</sup>' in row  # analyst revision grade
    assert "7/9" in row
    assert "3" in row  # magic formula rank
    assert "8.4x" in row  # acquirer's multiple


def test_rating_row_renders_placeholders_when_benchmarks_missing() -> None:
    row = render_rating_row(_snapshot())  # all new fields default to None
    assert "Rev" in row  # analyst-revision factor cell still present (score None -> renders)
    assert "—" in row  # benchmark placeholders
    # Low-confidence dimming only applies when signals_available < 9.
    assert "benchmark-low-confidence" not in row


def test_rating_row_dims_low_confidence_fscore() -> None:
    row = render_rating_row(
        _snapshot(piotroski_fscore=Decimal("5"), piotroski_signals_available=Decimal("6"))
    )
    assert "benchmark-low-confidence" in row
    assert "5/6" in row
    assert "Low conf." in row
    assert "Low confidence: only 6 of 9 Piotroski signals evaluable" in row


class _FakeSourceSummaryCursor:
    def __init__(self) -> None:
        self.query: str | None = None
        self.params: tuple[str, ...] | None = None

    def execute(self, query: str, params: tuple[str, ...]) -> None:
        self.query = query
        self.params = params

    def fetchall(self):
        return [
            ("twelve_data", 3, 1, 1, 1, datetime(2026, 5, 28, 23, 4, 14, tzinfo=UTC)),
            ("stooq", 2, 2, 0, 0, datetime(2026, 5, 28, 23, 5, 14, tzinfo=UTC)),
        ]


class _FakeLatestRatingsCursor:
    def __init__(self) -> None:
        self.query: str | None = None

    def execute(self, query: str) -> None:
        self.query = query

    def fetchall(self):
        return [
            (
                "MU",
                "Micron Technology Inc.",
                100,
                "A / Very Attractive",
                "fresh",
                date(2026, 6, 20),
                Decimal("67"),
                Decimal("74"),
                Decimal("84"),
                Decimal("100"),
                Decimal("60"),
                {"summary": "Strong latest profile."},
                Decimal("652.98"),
                "strong_buy",
                Decimal("117.83"),
                18,
                33,
                3,
                1,
                0,
                Decimal("50"),
                "A",
                "B",
                "A",
                "A",
                "C",
                "C",
                None,
                None,
                None,
                None,
            )
        ]


def test_fetch_latest_ratings_uses_latest_non_null_target_separately() -> None:
    cursor = _FakeLatestRatingsCursor()

    ratings = fetch_latest_ratings(cursor)

    assert ratings[0].analyst_suggestion_label == "strong_buy"
    assert ratings[0].analyst_target_price == Decimal("652.98")
    assert cursor.query is not None
    assert "latest_analyst_target as" in cursor.query
    assert "where analyst_target_price is not null" in cursor.query
    assert "left join latest_analyst_target lt on lt.symbol = rr.symbol" in cursor.query
    assert "lt.analyst_target_price" in cursor.query


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


def test_render_dashboard_markdown_is_agent_readable() -> None:
    markdown = render_dashboard_markdown(
        ratings=[
            _snapshot(
                symbol="MU",
                company_name="Micron Technology Inc.",
                analyst_target_price=Decimal("945.60"),
                latest_price_close=Decimal("1134.00"),
                analyst_suggestion_label="strong_buy",
                strong_buy_count=18,
                buy_count=33,
                hold_count=3,
                sell_count=1,
                strong_sell_count=0,
            )
        ],
        latest_run=("run-123", "success", datetime(2026, 5, 28, 23, 4, 14, tzinfo=UTC), datetime(2026, 5, 28, 23, 4, 52, tzinfo=UTC)),
        latest_run_counts={"succeeded": 1},
        source_refresh_summaries=[SourceRefreshSummary(source="finnhub", calls=5, succeeded=5, failed=0, status="succeeded")],
        table_counts={"symbols": 1, "ratings_daily": 1, "pipeline_runs": 1, "symbol_refresh_runs": 1, "price_daily": 1, "features_daily": 1},
        quality_alerts=[],
    )

    assert markdown.startswith("# Stock Ratings Dashboard")
    assert "| Rank | Symbol | Company | Score |" in markdown
    assert "| 1 | MU | Micron Technology Inc. |" in markdown
    assert "$945.60" in markdown
    assert "## Source Calls" in markdown


def test_render_dashboard_json_has_structured_targets() -> None:
    payload = json.loads(
        render_dashboard_json(
            ratings=[
                _snapshot(
                    symbol="MU",
                    analyst_target_price=Decimal("945.60"),
                    latest_price_close=Decimal("1134.00"),
                    analyst_suggestion_label="strong_buy",
                    strong_buy_count=18,
                    buy_count=33,
                    hold_count=3,
                    sell_count=1,
                    strong_sell_count=0,
                )
            ],
            latest_run=("run-123", "success", datetime(2026, 5, 28, 23, 4, 14, tzinfo=UTC), datetime(2026, 5, 28, 23, 4, 52, tzinfo=UTC)),
            latest_run_counts={"succeeded": 1},
            source_refresh_summaries=[],
            table_counts={"symbols": 1, "ratings_daily": 1, "pipeline_runs": 1, "symbol_refresh_runs": 1, "price_daily": 1, "features_daily": 1},
            quality_alerts=[],
        )
    )

    assert payload["artifact"] == "ratings-dashboard"
    assert payload["ratings"][0]["symbol"] == "MU"
    assert payload["ratings"][0]["analyst"]["target_price"] == 945.6
    assert payload["ratings"][0]["analyst"]["derived_targets"]["mid"]["upside_percent"] == -16.6
    assert payload["latest_run"]["run_id"] == "run-123"


def test_fetch_source_refresh_summaries_from_db_aggregates_status() -> None:
    cursor = _FakeSourceSummaryCursor()

    summaries = fetch_source_refresh_summaries_from_db(cursor, "run-123")

    assert cursor.params == ("run-123",)
    assert summaries == [
        SourceRefreshSummary(source="twelve_data", calls=3, succeeded=1, failed=1, status="partial"),
        SourceRefreshSummary(source="stooq", calls=2, succeeded=2, failed=0, status="succeeded"),
    ]


def test_yahoo_finance_symbol_normalizes_provider_symbols() -> None:
    assert yahoo_finance_symbol("AAPL") == "AAPL"
    assert yahoo_finance_symbol("BRK.B") == "BRK-B"
    assert yahoo_finance_symbol("NASDAQ:GOOGL") == "GOOGL"
    assert yahoo_finance_symbol("TSE:FFH") == "FFH.TO"
    assert yahoo_finance_symbol("ETR:AIXA") == "AIXA.DE"
    assert yahoo_finance_symbol("TEL2-B.ST") == "TEL2-B.ST"


def test_render_dashboard_links_company_to_yahoo_finance() -> None:
    html = render_dashboard_html(
        ratings=[
            RatingSnapshot(
                symbol="NASDAQ:GOOGL",
                company_name="Alphabet Inc. Class A",
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
        latest_run=("run-123", "success", datetime(2026, 5, 28, 23, 4, 14, tzinfo=UTC), datetime(2026, 5, 28, 23, 4, 52, tzinfo=UTC)),
        latest_run_counts={"succeeded": 1},
        source_refresh_summaries=[],
        table_counts={"symbols": 1, "ratings_daily": 1, "pipeline_runs": 1, "symbol_refresh_runs": 1, "price_daily": 1, "features_daily": 1},
        quality_alerts=[],
    )

    assert f'href="{yahoo_finance_url("NASDAQ:GOOGL")}"' in html
    assert 'target="_blank" rel="noopener noreferrer"' in html
    assert "Alphabet Inc. Class A" in html


def test_render_methodology_includes_factor_and_source_sections() -> None:
    html = render_methodology_html(
        [
            SourceRefreshSummary(source="fred", calls=2, succeeded=2, failed=0, status="succeeded"),
            SourceRefreshSummary(source="sec_edgar", calls=5, succeeded=4, failed=1, status="partial"),
        ]
    )

    assert "Stock Rating Methodology" in html
    assert "Source To Feature Mapping" in html
    assert "Valuation" in html
    assert "Final Composite Score" in html
    assert "FRED" in html
    assert "SEC EDGAR" in html


def test_render_methodology_markdown_includes_formulas_and_sources() -> None:
    markdown = render_methodology_markdown(
        [
            SourceRefreshSummary(source="fred", calls=2, succeeded=2, failed=0, status="succeeded"),
            SourceRefreshSummary(source="sec_edgar", calls=5, succeeded=4, failed=1, status="partial"),
        ]
    )

    assert markdown.startswith("# Stock Rating Methodology")
    assert "## Factor Calculations" in markdown
    assert "```text" in markdown
    assert "composite = valuation*0.225" in markdown
    assert "| FRED | 2 | 2 | 0 | Succeeded |" in markdown
