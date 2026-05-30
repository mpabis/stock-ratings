from datetime import date, datetime, UTC
from decimal import Decimal

from stock_rating.pipeline.daily import (
    MAX_FUNDAMENTAL_AGE_BY_TIER,
    SymbolPeriodicRefreshState,
    SymbolRefreshState,
    build_symbol_refresh_run_records,
    execute_analyst_refresh_plan,
    execute_fundamental_refresh_plan,
    execute_alpha_vantage_refresh_plan,
    execute_price_refresh_plan,
    execute_rating_repair_plan,
    execute_stooq_refresh_plan,
    execute_twelve_data_refresh_plan,
    plan_price_refreshes,
    plan_periodic_refreshes,
    plan_rating_repairs,
    pipeline_status_for,
    preferred_provider_name,
    rating_task_for_features,
    resolve_git_sha,
)
from stock_rating.ingest.prices import AlphaVantageRateLimitError, DailyPriceBar, TwelveDataRateLimitError
from stock_rating.ingest.sec_companyfacts import FundamentalFact, SecCompanyFactsResponseError
from stock_rating.repository.ratings import RatingRepairState
from stock_rating.repository.runs import SymbolRefreshRunRecord
from stock_rating.transform.features import FeatureValue


def test_resolve_git_sha_prefers_github_sha_environment_value() -> None:
    git_sha = resolve_git_sha(
        environ={"GITHUB_SHA": "a23f69fd383f58fbe0d3f2be6462e9c957f6f1f3"},
        git_rev_parse_fn=lambda: "should-not-be-used",
    )

    assert git_sha == "a23f69fd383f58fbe0d3f2be6462e9c957f6f1f3"


def test_resolve_git_sha_uses_git_rev_parse_when_github_sha_missing() -> None:
    git_sha = resolve_git_sha(
        environ={},
        git_rev_parse_fn=lambda: "2f1c9db91d8d7237deccf81b12e4f1d0f9ed51ab\n",
    )

    assert git_sha == "2f1c9db91d8d7237deccf81b12e4f1d0f9ed51ab"


def test_resolve_git_sha_returns_none_when_git_rev_parse_fails() -> None:
    git_sha = resolve_git_sha(
        environ={},
        git_rev_parse_fn=lambda: (_ for _ in ()).throw(RuntimeError("git unavailable")),
    )

    assert git_sha is None


def test_refresh_plan_prioritizes_tier_then_staleness() -> None:
    as_of = date(2026, 5, 27)
    symbols = [
        SymbolRefreshState(symbol="TIER3", refresh_tier=3, last_price_date=date(2026, 5, 20)),
        SymbolRefreshState(symbol="TIER2_OLD", refresh_tier=2, last_price_date=date(2026, 5, 21)),
        SymbolRefreshState(symbol="TIER1", refresh_tier=1, last_price_date=date(2026, 5, 26)),
        SymbolRefreshState(symbol="TIER2_NEW", refresh_tier=2, last_price_date=date(2026, 5, 25)),
    ]

    planned = plan_price_refreshes(symbols, as_of=as_of, budget=3)

    assert [task.symbol for task in planned] == ["TIER1", "TIER2_OLD", "TIER2_NEW"]


def test_refresh_plan_marks_stale_symbols() -> None:
    as_of = date(2026, 5, 27)
    symbols = [
        SymbolRefreshState(symbol="OLD", refresh_tier=3, last_price_date=date(2026, 5, 19)),
    ]

    planned = plan_price_refreshes(symbols, as_of=as_of, budget=1)

    assert planned[0].freshness_status == "stale"


def test_refresh_plan_uses_trading_day_age() -> None:
    as_of = date(2026, 6, 1)
    symbols = [
        SymbolRefreshState(symbol="FRIDAY", refresh_tier=1, last_price_date=date(2026, 5, 29)),
    ]

    planned = plan_price_refreshes(symbols, as_of=as_of, budget=1)

    assert planned[0].age_in_days == 1
    assert planned[0].freshness_status == "fresh"


def test_periodic_refresh_plan_only_includes_due_symbols() -> None:
    as_of = date(2026, 5, 27)
    planned = plan_periodic_refreshes(
        [
            SymbolPeriodicRefreshState(symbol="DUE", refresh_tier=1, last_refresh_date=date(2026, 1, 1)),
            SymbolPeriodicRefreshState(symbol="RECENT", refresh_tier=1, last_refresh_date=date(2026, 5, 1)),
        ],
        as_of=as_of,
        budget=5,
        max_age_by_tier=MAX_FUNDAMENTAL_AGE_BY_TIER,
    )

    assert [task.symbol for task in planned] == ["DUE"]


def test_rating_repair_plan_includes_missing_and_stale_ratings() -> None:
    as_of = date(2026, 5, 30)
    planned = plan_rating_repairs(
        [
            RatingRepairState("MISSING", 3, date(2026, 5, 29), None),
            RatingRepairState("STALE", 2, date(2026, 5, 29), date(2026, 5, 28)),
            RatingRepairState("CURRENT", 1, date(2026, 5, 29), date(2026, 5, 29)),
            RatingRepairState("NO_PRICE", 3, None, None),
        ],
        as_of=as_of,
    )

    assert [task.symbol for task in planned] == ["MISSING", "STALE"]
    assert planned[0].freshness_status == "fresh"


def test_preferred_provider_name_returns_stooq_when_only_stooq_configured() -> None:
    assert preferred_provider_name(False, False, True) == "stooq"


def test_rating_task_for_features_recomputes_freshness_from_latest_feature_date() -> None:
    task = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 20))],
        as_of=date(2026, 5, 27),
        budget=1,
    )[0]

    rating_task = rating_task_for_features(
        task,
        [
            FeatureValue(
                symbol="AAPL",
                date=date(2026, 5, 27),
                feature_name="daily_volume",
                feature_value=Decimal("1"),
                source_version="v1",
            )
        ],
        as_of=date(2026, 5, 27),
    )

    assert task.freshness_status == "stale"
    assert rating_task.freshness_status == "fresh"


def test_symbol_refresh_run_records_are_created_from_plan() -> None:
    as_of = date(2026, 5, 27)
    symbols = [
        SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26)),
    ]

    planned = plan_price_refreshes(symbols, as_of=as_of, budget=1)
    records = build_symbol_refresh_run_records(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=planned,
        provider="stooq",
        attempted_at=datetime(2026, 5, 27, 22, 30, tzinfo=UTC),
    )

    assert len(records) == 1
    assert records[0].status == "planned"
    assert records[0].provider == "stooq"
    assert records[0].fetched_bar_count is None


def test_execute_alpha_vantage_refresh_plan_marks_success() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )

    records = execute_alpha_vantage_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        api_key="demo-key",
        fetch_fn=lambda symbol, api_key: [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="alpha_vantage",
            )
        ],
        persist_fn=lambda database_url, bars: False,
    )

    assert records[0].status == "succeeded"
    assert pipeline_status_for(records) == "success"
    assert records[0].fetched_bar_count == 1


def test_execute_alpha_vantage_refresh_plan_marks_success_and_updates_refresh_time() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )
    updates: list[str] = []

    records = execute_alpha_vantage_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="postgresql://example",
        api_key="demo-key",
        fetch_fn=lambda symbol, api_key: [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
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
        mark_refreshed_fn=lambda database_url, symbol, refreshed_at: updates.append(symbol) or True,
        persist_features_fn=lambda database_url, features: True,
        compute_features_fn=lambda bars: [
            FeatureValue(
                symbol="AAPL",
                date=as_of,
                feature_name="daily_volume",
                feature_value=Decimal("1"),
                source_version="v1",
            )
        ],
        persist_ratings_fn=lambda database_url, ratings: True,
    )

    assert records[0].status == "succeeded"
    assert updates == ["AAPL"]


def test_execute_alpha_vantage_refresh_plan_marks_rate_limited_and_stops() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [
            SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26)),
            SymbolRefreshState(symbol="MSFT", refresh_tier=1, last_price_date=date(2026, 5, 26)),
        ],
        as_of=as_of,
        budget=2,
    )

    def _rate_limited(symbol: str, api_key: str):
        raise AlphaVantageRateLimitError("limit hit")

    records = execute_alpha_vantage_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        api_key="demo-key",
        fetch_fn=_rate_limited,
        persist_fn=lambda database_url, bars: False,
    )

    assert len(records) == 1
    assert records[0].status == "rate_limited"
    assert pipeline_status_for(records) == "partial"
    assert records[0].provider_error_code == "alpha_vantage_rate_limit"


def test_execute_twelve_data_refresh_plan_marks_success() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )

    records = execute_twelve_data_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        api_key="demo-key",
        fetch_fn=lambda symbol, api_key: [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="twelve_data",
            )
        ],
        persist_fn=lambda database_url, bars: False,
        persist_features_fn=lambda database_url, features: False,
        compute_features_fn=lambda bars: [],
    )

    assert records[0].provider == "twelve_data"
    assert records[0].status == "succeeded"
    assert records[0].fetched_bar_count == 1


def test_execute_stooq_refresh_plan_marks_success() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="TSE:FFH", refresh_tier=3, last_price_date=date(2026, 5, 20))],
        as_of=as_of,
        budget=1,
    )

    records = execute_stooq_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        api_key="stooq-key",
        fetch_fn=lambda symbol, api_key: [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="stooq",
            )
        ],
        persist_fn=lambda database_url, bars: False,
        persist_features_fn=lambda database_url, features: False,
        compute_features_fn=lambda bars: [],
    )

    assert records[0].provider == "stooq"
    assert records[0].status == "succeeded"


def test_execute_rating_repair_plan_builds_rating_from_stored_prices() -> None:
    as_of = date(2026, 5, 29)
    tasks = plan_rating_repairs(
        [RatingRepairState("AAPL", 1, as_of, None)],
        as_of=as_of,
    )
    persisted_features: list[FeatureValue] = []
    persisted_ratings: list[object] = []

    records = execute_rating_repair_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="postgresql://example",
        load_price_bars_fn=lambda database_url, symbol: [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="alpha_vantage",
            )
        ],
        compute_features_fn=lambda bars: [
            FeatureValue(
                symbol="AAPL",
                date=as_of,
                feature_name="daily_volume",
                feature_value=Decimal("1"),
                source_version="v1",
            )
        ],
        persist_features_fn=lambda database_url, features: persisted_features.extend(features) or True,
        build_rating_record_fn=lambda task, features: {"symbol": task.symbol, "date": task.age_in_days},
        persist_ratings_fn=lambda database_url, ratings: persisted_ratings.extend(ratings) or True,
    )

    assert records[0].data_type == "rating"
    assert records[0].provider == "local_rebuild"
    assert records[0].status == "succeeded"
    assert records[0].fetched_bar_count == 1
    assert persisted_features[0].symbol == "AAPL"
    assert persisted_ratings == [{"symbol": "AAPL", "date": 0}]


def test_execute_fundamental_refresh_plan_marks_success_and_updates_refresh_time() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )
    updates: list[str] = []

    records = execute_fundamental_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="postgresql://example",
        user_agent="stock-rating-test@example.com",
        fetch_mapping_fn=lambda user_agent: {"AAPL": type("Mapping", (), {"cik": "0000320193"})()},
        fetch_company_facts_fn=lambda cik, user_agent: {"facts": {}},
        parse_company_facts_fn=lambda symbol, cik, payload: [
            FundamentalFact(
                cik=cik,
                symbol=symbol,
                fiscal_period="FY",
                fiscal_year=2025,
                form="10-K",
                metric="revenue",
                value=Decimal("1000"),
                unit="USD",
                filed_at=datetime(2025, 11, 1, tzinfo=UTC),
            )
        ],
        persist_facts_fn=lambda database_url, facts: True,
        mark_refreshed_fn=lambda database_url, symbol, refreshed_at: updates.append(symbol) or True,
    )

    assert len(records) == 1
    assert records[0].provider == "sec_edgar"
    assert records[0].status == "succeeded"
    assert records[0].fetched_bar_count == 1
    assert updates == ["AAPL"]


def test_execute_fundamental_refresh_plan_marks_failure_on_sec_error() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )

    records = execute_fundamental_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="postgresql://example",
        user_agent="stock-rating-test@example.com",
        fetch_mapping_fn=lambda user_agent: (_ for _ in ()).throw(SecCompanyFactsResponseError("forbidden")),
    )

    assert len(records) == 1
    assert records[0].provider == "sec_edgar"
    assert records[0].status == "failed"
    assert records[0].provider_error_code == "sec_edgar_error"


def test_execute_fundamental_refresh_plan_skips_symbols_missing_sec_mapping() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="ETR:AIXA", refresh_tier=3, last_price_date=date(2026, 5, 20))],
        as_of=as_of,
        budget=1,
    )

    records = execute_fundamental_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="postgresql://example",
        user_agent="stock-rating-test@example.com",
        fetch_mapping_fn=lambda user_agent: {},
    )

    assert len(records) == 1
    assert records[0].status == "skipped"
    assert records[0].provider_error_code == "sec_mapping_missing"
    assert pipeline_status_for(records) == "planned"


def test_execute_analyst_refresh_plan_marks_success_with_missing_consensus() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )

    records = execute_analyst_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        api_key="demo-key",
        fetch_fn=lambda symbol, api_key: {"Symbol": symbol, "Name": "Apple Inc."},
    )

    assert len(records) == 1
    assert records[0].provider == "alpha_vantage_overview"
    assert records[0].status == "succeeded"
    assert records[0].fetched_bar_count == 0


def test_execute_price_refresh_plan_falls_back_to_stooq_after_twelve_failure() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="TSE:FFH", refresh_tier=3, last_price_date=date(2026, 5, 20))],
        as_of=as_of,
        budget=1,
    )
    stooq_symbols: list[str] = []

    records = execute_price_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        alpha_vantage_api_key="",
        twelve_data_api_key="twelve-key",
        stooq_api_key="stooq-key",
        twelve_fetch_fn=lambda symbol, api_key: (_ for _ in ()).throw(RuntimeError("paid plan")),
        stooq_fetch_fn=lambda symbol, api_key: stooq_symbols.append(symbol) or [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="stooq",
            )
        ],
        persist_fn=lambda database_url, bars: False,
        mark_refreshed_fn=lambda database_url, symbol, refreshed_at: True,
        persist_features_fn=lambda database_url, features: False,
        compute_features_fn=lambda bars: [],
    )

    assert stooq_symbols == ["TSE:FFH"]
    assert [record.provider for record in records] == ["twelve_data", "stooq"]
    assert records[-1].status == "succeeded"
    assert pipeline_status_for(records) == "success"


def test_execute_price_refresh_plan_falls_back_to_twelve_data() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )

    records = execute_price_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        alpha_vantage_api_key="alpha-key",
        twelve_data_api_key="twelve-key",
        alpha_fetch_fn=lambda symbol, api_key: (_ for _ in ()).throw(AlphaVantageRateLimitError("limit hit")),
        twelve_fetch_fn=lambda symbol, api_key: [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="twelve_data",
            )
        ],
        persist_fn=lambda database_url, bars: False,
        mark_refreshed_fn=lambda database_url, symbol, refreshed_at: True,
        persist_features_fn=lambda database_url, features: False,
        compute_features_fn=lambda bars: [],
    )

    assert [record.provider for record in records] == ["alpha_vantage", "twelve_data"]
    assert pipeline_status_for(records) == "success"


def test_execute_twelve_data_refresh_plan_marks_rate_limited() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26))],
        as_of=as_of,
        budget=1,
    )

    records = execute_twelve_data_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        api_key="demo-key",
        fetch_fn=lambda symbol, api_key: (_ for _ in ()).throw(TwelveDataRateLimitError("limit hit")),
        persist_fn=lambda database_url, bars: False,
        persist_features_fn=lambda database_url, features: False,
        compute_features_fn=lambda bars: [],
    )

    assert records[0].status == "rate_limited"


def test_execute_price_refresh_plan_limits_alpha_vantage_requests_and_falls_back() -> None:
    as_of = date(2026, 5, 27)
    tasks = plan_price_refreshes(
        [
            SymbolRefreshState(symbol="AAPL", refresh_tier=1, last_price_date=date(2026, 5, 26)),
            SymbolRefreshState(symbol="MSFT", refresh_tier=1, last_price_date=date(2026, 5, 26)),
        ],
        as_of=as_of,
        budget=2,
    )
    alpha_symbols: list[str] = []
    twelve_symbols: list[str] = []

    records = execute_price_refresh_plan(
        run_id="ddda45d6-d8fa-47c6-8aae-91ab5f50752b",
        tasks=tasks,
        database_url="",
        alpha_vantage_api_key="alpha-key",
        twelve_data_api_key="twelve-key",
        alpha_fetch_fn=lambda symbol, api_key: alpha_symbols.append(symbol) or [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="alpha_vantage",
            )
        ],
        twelve_fetch_fn=lambda symbol, api_key: twelve_symbols.append(symbol) or [
            DailyPriceBar(
                symbol=symbol,
                date=as_of,
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("1"),
                close=Decimal("2"),
                adjusted_close=Decimal("2"),
                volume=1,
                source="twelve_data",
            )
        ],
        persist_fn=lambda database_url, bars: False,
        mark_refreshed_fn=lambda database_url, symbol, refreshed_at: True,
        persist_features_fn=lambda database_url, features: False,
        compute_features_fn=lambda bars: [],
        alpha_vantage_max_requests=1,
        alpha_vantage_pause_seconds=0,
    )

    assert alpha_symbols == ["AAPL"]
    assert twelve_symbols == ["MSFT"]
    assert [record.provider for record in records] == ["alpha_vantage", "twelve_data"]
    assert pipeline_status_for(records) == "success"


def test_pipeline_status_ignores_rating_repair_success_for_price_failure() -> None:
    attempted_at = datetime(2026, 5, 30, tzinfo=UTC)
    records = [
        SymbolRefreshRunRecord(
            run_id="run",
            symbol="AAPL",
            data_type="price",
            provider="alpha_vantage",
            status="failed",
            attempted_at=attempted_at,
            completed_at=attempted_at,
            error_message="provider failed",
        ),
        SymbolRefreshRunRecord(
            run_id="run",
            symbol="AAPL",
            data_type="rating",
            provider="local_rebuild",
            status="succeeded",
            attempted_at=attempted_at,
            completed_at=attempted_at,
            error_message=None,
            fetched_bar_count=130,
        ),
    ]

    assert pipeline_status_for(records) == "partial"

