from dataclasses import dataclass
from datetime import date, timedelta
import os
import subprocess
import time

from stock_rating.config import get_settings
from stock_rating.db import DatabaseConfig, is_configured
from stock_rating.ingest.fred_macro import (
    CORE_FRED_SERIES,
    FredMacroResponseError,
    fetch_fred_series_observations,
    parse_fred_series_observations,
    persist_macro_observations,
)
from stock_rating.ingest.analyst import (
    AlphaVantageAnalystRateLimitError,
    fetch_alpha_vantage_company_overview,
    parse_alpha_vantage_analyst_consensus,
    persist_analyst_consensus,
)
from stock_rating.ingest.prices import (
    AlphaVantageRateLimitError,
    StooqResponseError,
    TwelveDataRateLimitError,
    fetch_alpha_vantage_daily_adjusted,
    fetch_stooq_daily,
    fetch_twelve_data_time_series,
    get_price_provider_status,
    persist_price_bars,
)
from stock_rating.ingest.sec_companyfacts import (
    SecCompanyFactsResponseError,
    fetch_sec_company_facts,
    fetch_sec_ticker_mapping,
    normalize_symbol_for_sec,
    parse_company_facts,
    persist_fundamental_facts,
)
from stock_rating.repository.runs import (
    SymbolRefreshRunRecord,
    SourceRefreshSummaryRecord,
    build_pipeline_run_record,
    generate_run_id,
    persist_run_records,
    utc_now,
    write_plan_artifact,
)
from stock_rating.repository.prices import load_recent_price_bars
from stock_rating.repository.ratings import RatingRepairState, load_rating_repair_states, persist_ratings
from stock_rating.repository.analyst import load_latest_analyst_dates
from stock_rating.repository.fundamentals import load_latest_fundamental_facts
from stock_rating.repository.macro import load_latest_macro_observations
from stock_rating.repository.symbols import load_symbol_seeds, update_symbol_last_price_refresh_at
from stock_rating.repository.symbols import update_symbol_last_fundamental_refresh_at
from stock_rating.rating.model_v1 import build_rating_record
from stock_rating.transform.features import compute_price_features, persist_features
from stock_rating.transform.fundamentals import compute_fundamental_features
from stock_rating.transform.macro import compute_macro_features


MAX_PRICE_AGE_BY_TIER = {
    1: 1,
    2: 3,
    3: 5,
}

MAX_FUNDAMENTAL_AGE_BY_TIER = {
    1: 90,
    2: 120,
    3: 180,
}

MAX_ANALYST_AGE_BY_TIER = {
    1: 7,
    2: 14,
    3: 30,
}


@dataclass(frozen=True)
class SymbolRefreshState:
    symbol: str
    refresh_tier: int
    last_price_date: date


@dataclass(frozen=True)
class SymbolPeriodicRefreshState:
    symbol: str
    refresh_tier: int
    last_refresh_date: date | None


@dataclass(frozen=True)
class RefreshTask:
    symbol: str
    refresh_tier: int
    age_in_days: int
    freshness_status: str


def age_in_days(as_of: date, last_price_date: date) -> int:
    if last_price_date >= as_of:
        return 0

    age = 0
    current = last_price_date + timedelta(days=1)
    while current <= as_of:
        if current.weekday() < 5:
            age += 1
        current += timedelta(days=1)
    return age


def calendar_age_in_days(as_of: date, last_refresh_date: date | None) -> int:
    if last_refresh_date is None:
        return 9999
    return max(0, (as_of - last_refresh_date).days)


def freshness_status_for_age(refresh_tier: int, age: int) -> str:
    max_age = MAX_PRICE_AGE_BY_TIER.get(refresh_tier, MAX_PRICE_AGE_BY_TIER[3])
    if age <= 1:
        return "fresh"
    if age <= max_age:
        return "aging"
    return "stale"


def freshness_status_for(symbol: SymbolRefreshState, as_of: date) -> str:
    age = age_in_days(as_of, symbol.last_price_date)
    return freshness_status_for_age(symbol.refresh_tier, age)


def plan_price_refreshes(
    symbols: list[SymbolRefreshState],
    as_of: date,
    budget: int,
) -> list[RefreshTask]:
    ordered_symbols = sorted(
        symbols,
        key=lambda item: (item.refresh_tier, -age_in_days(as_of, item.last_price_date), item.symbol),
    )
    planned: list[RefreshTask] = []

    for symbol in ordered_symbols[:budget]:
        planned.append(
            RefreshTask(
                symbol=symbol.symbol,
                refresh_tier=symbol.refresh_tier,
                age_in_days=age_in_days(as_of, symbol.last_price_date),
                freshness_status=freshness_status_for(symbol, as_of),
            )
        )

    return planned


def plan_periodic_refreshes(
    symbols: list[SymbolPeriodicRefreshState],
    as_of: date,
    budget: int,
    max_age_by_tier: dict[int, int],
) -> list[RefreshTask]:
    if budget <= 0:
        return []

    due_symbols = [
        symbol
        for symbol in symbols
        if calendar_age_in_days(as_of, symbol.last_refresh_date)
        >= max_age_by_tier.get(symbol.refresh_tier, max_age_by_tier[3])
    ]
    ordered_symbols = sorted(
        due_symbols,
        key=lambda item: (
            item.refresh_tier,
            -calendar_age_in_days(as_of, item.last_refresh_date),
            item.symbol,
        ),
    )

    planned: list[RefreshTask] = []
    for symbol in ordered_symbols[:budget]:
        age = calendar_age_in_days(as_of, symbol.last_refresh_date)
        planned.append(
            RefreshTask(
                symbol=symbol.symbol,
                refresh_tier=symbol.refresh_tier,
                age_in_days=age,
                freshness_status="due" if age < 9999 else "missing",
            )
        )

    return planned


def rating_task_for_features(task: RefreshTask, features, as_of: date | None = None) -> RefreshTask:
    latest_date = max(feature.date for feature in features)
    effective_as_of = as_of or date.today()
    age = age_in_days(effective_as_of, latest_date)
    return RefreshTask(
        symbol=task.symbol,
        refresh_tier=task.refresh_tier,
        age_in_days=age,
        freshness_status=freshness_status_for_age(task.refresh_tier, age),
    )


def build_default_refresh_plan() -> list[RefreshTask]:
    settings = get_settings()
    seeds = load_symbol_seeds(
        database_url=settings.database_url,
        seed_path=settings.symbol_seed_path or None,
    )
    symbol_states = [
        SymbolRefreshState(
            symbol=seed.symbol,
            refresh_tier=seed.refresh_tier,
            last_price_date=seed.last_price_date,
        )
        for seed in seeds
    ]
    return plan_price_refreshes(symbol_states, as_of=date.today(), budget=min(settings.symbol_limit, len(symbol_states)))


def plan_rating_repairs(
    symbols: list[RatingRepairState],
    as_of: date,
    budget: int | None = None,
) -> list[RefreshTask]:
    planned: list[RefreshTask] = []
    for symbol in symbols:
        if symbol.last_price_date is None:
            continue
        if symbol.latest_rating_date is not None and symbol.latest_rating_date >= symbol.last_price_date:
            continue

        age = age_in_days(as_of, symbol.last_price_date)
        planned.append(
            RefreshTask(
                symbol=symbol.symbol,
                refresh_tier=symbol.refresh_tier,
                age_in_days=age,
                freshness_status=freshness_status_for_age(symbol.refresh_tier, age),
            )
        )

    if budget is not None:
        return planned[:budget]
    return planned


def build_default_rating_repair_plan() -> list[RefreshTask]:
    settings = get_settings()
    return plan_rating_repairs(
        load_rating_repair_states(settings.database_url),
        as_of=date.today(),
    )


def build_default_fundamental_refresh_plan() -> list[RefreshTask]:
    settings = get_settings()
    seeds = load_symbol_seeds(
        database_url=settings.database_url,
        seed_path=settings.symbol_seed_path or None,
    )
    symbol_states = [
        SymbolPeriodicRefreshState(
            symbol=seed.symbol,
            refresh_tier=seed.refresh_tier,
            last_refresh_date=seed.last_fundamental_date,
        )
        for seed in seeds
    ]
    return plan_periodic_refreshes(
        symbol_states,
        as_of=date.today(),
        budget=min(settings.fundamental_symbol_limit, len(symbol_states)),
        max_age_by_tier=MAX_FUNDAMENTAL_AGE_BY_TIER,
    )


def build_default_analyst_refresh_plan() -> list[RefreshTask]:
    settings = get_settings()
    if settings.analyst_symbol_limit <= 0:
        return []

    seeds = load_symbol_seeds(
        database_url=settings.database_url,
        seed_path=settings.symbol_seed_path or None,
    )
    latest_analyst_dates = load_latest_analyst_dates(settings.database_url, [seed.symbol for seed in seeds])
    symbol_states = [
        SymbolPeriodicRefreshState(
            symbol=seed.symbol,
            refresh_tier=seed.refresh_tier,
            last_refresh_date=latest_analyst_dates.get(seed.symbol),
        )
        for seed in seeds
    ]
    return plan_periodic_refreshes(
        symbol_states,
        as_of=date.today(),
        budget=min(settings.analyst_symbol_limit, len(symbol_states)),
        max_age_by_tier=MAX_ANALYST_AGE_BY_TIER,
    )


def preferred_provider_name(alpha_vantage_configured: bool, twelve_data_configured: bool, stooq_configured: bool) -> str:
    if alpha_vantage_configured:
        return "alpha_vantage"
    if twelve_data_configured:
        return "twelve_data"
    if stooq_configured:
        return "stooq"
    return "stooq"


def build_symbol_features(
    database_url: str,
    task: RefreshTask,
    bars,
    compute_price_features_fn=compute_price_features,
    load_fundamental_facts_fn=load_latest_fundamental_facts,
    compute_fundamental_features_fn=compute_fundamental_features,
    load_macro_observations_fn=load_latest_macro_observations,
    compute_macro_features_fn=compute_macro_features,
):
    price_features = compute_price_features_fn(bars)
    if not price_features:
        return []

    latest_date = max(feature.date for feature in price_features)
    latest_bar = max(bars, key=lambda bar: bar.date)
    latest_price = latest_bar.adjusted_close or latest_bar.close
    fundamental_facts = load_fundamental_facts_fn(database_url, task.symbol)
    try:
        fundamental_features = compute_fundamental_features_fn(task.symbol, latest_date, fundamental_facts, latest_price)
    except TypeError:
        fundamental_features = compute_fundamental_features_fn(task.symbol, latest_date, fundamental_facts)
    macro_observations = load_macro_observations_fn(database_url, CORE_FRED_SERIES)
    macro_features = compute_macro_features_fn(task.symbol, latest_date, macro_observations)
    return price_features + fundamental_features + macro_features


def summarize_symbol_runs(source: str, symbol_runs: list[SymbolRefreshRunRecord]) -> SourceRefreshSummaryRecord:
    calls = len(symbol_runs)
    succeeded = sum(1 for record in symbol_runs if record.status == "succeeded")
    failed = sum(1 for record in symbol_runs if record.status in {"failed", "rate_limited"})
    skipped = sum(1 for record in symbol_runs if record.status == "skipped")
    if calls == 0:
        status = "skipped"
    elif succeeded == calls:
        status = "succeeded"
    elif skipped == calls:
        status = "skipped"
    elif failed == 0:
        status = "partial"
    elif succeeded == 0:
        status = "failed"
    else:
        status = "partial"
    return SourceRefreshSummaryRecord(
        source=source,
        calls=calls,
        succeeded=succeeded,
        failed=failed,
        status=status,
    )


def summarize_provider_runs(symbol_runs: list[SymbolRefreshRunRecord]) -> list[SourceRefreshSummaryRecord]:
    provider_order: list[str] = []
    grouped_runs: dict[str, list[SymbolRefreshRunRecord]] = {}
    for record in symbol_runs:
        if record.provider not in grouped_runs:
            provider_order.append(record.provider)
            grouped_runs[record.provider] = []
        grouped_runs[record.provider].append(record)

    return [summarize_symbol_runs(provider, grouped_runs[provider]) for provider in provider_order]


def execute_macro_refresh(
    database_url: str,
    api_key: str,
    fetch_fn=fetch_fred_series_observations,
    parse_fn=parse_fred_series_observations,
    persist_fn=persist_macro_observations,
) -> SourceRefreshSummaryRecord:
    if not is_configured(DatabaseConfig(url=database_url)) or not api_key:
        return SourceRefreshSummaryRecord(source="fred", calls=0, succeeded=0, failed=0, status="skipped")

    calls_made = 0
    succeeded = 0
    for series_id in CORE_FRED_SERIES:
        calls_made += 1
        try:
            payload = fetch_fn(series_id, api_key)
            observations = parse_fn(series_id, payload)
            if observations and not persist_fn(database_url, observations):
                return SourceRefreshSummaryRecord(
                    source="fred",
                    calls=calls_made,
                    succeeded=succeeded,
                    failed=1,
                    status="failed",
                )
            succeeded += 1
        except FredMacroResponseError:
            return SourceRefreshSummaryRecord(
                source="fred",
                calls=calls_made,
                succeeded=succeeded,
                failed=1,
                status="failed",
            )

    return SourceRefreshSummaryRecord(
        source="fred",
        calls=calls_made,
        succeeded=succeeded,
        failed=0,
        status="succeeded",
    )


def execute_fundamental_refresh_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    user_agent: str,
    fetch_mapping_fn=fetch_sec_ticker_mapping,
    fetch_company_facts_fn=fetch_sec_company_facts,
    parse_company_facts_fn=parse_company_facts,
    persist_facts_fn=persist_fundamental_facts,
    mark_refreshed_fn=update_symbol_last_fundamental_refresh_at,
) -> list[SymbolRefreshRunRecord]:
    if not tasks or not is_configured(DatabaseConfig(url=database_url)):
        return []

    try:
        mappings = fetch_mapping_fn(user_agent)
    except SecCompanyFactsResponseError as error:
        attempted_at = utc_now()
        return [
            SymbolRefreshRunRecord(
                run_id=run_id,
                symbol=task.symbol,
                data_type="fundamental",
                provider="sec_edgar",
                status="failed",
                attempted_at=attempted_at,
                completed_at=utc_now(),
                error_message=str(error),
                fetched_bar_count=None,
                provider_error_code="sec_edgar_error",
            )
            for task in tasks
        ]

    symbol_runs: list[SymbolRefreshRunRecord] = []
    for task in tasks:
        mapping = mappings.get(normalize_symbol_for_sec(task.symbol))
        if mapping is None:
            attempted_at = utc_now()
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="fundamental",
                    provider="sec_edgar",
                    status="skipped",
                    attempted_at=attempted_at,
                    completed_at=attempted_at,
                    error_message="No SEC ticker mapping found for symbol.",
                    fetched_bar_count=0,
                    provider_error_code="sec_mapping_missing",
                )
            )
            continue

        attempted_at = utc_now()
        try:
            payload = fetch_company_facts_fn(mapping.cik, user_agent)
            facts = parse_company_facts_fn(task.symbol, mapping.cik, payload)
            if facts and not persist_facts_fn(database_url, facts):
                raise RuntimeError(f"Failed to persist fundamental facts for {task.symbol}")
            if facts:
                mark_refreshed_fn(database_url, task.symbol, utc_now())
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="fundamental",
                    provider="sec_edgar",
                    status="succeeded",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=None,
                    fetched_bar_count=len(facts),
                    provider_error_code=None,
                )
            )
        except SecCompanyFactsResponseError as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="fundamental",
                    provider="sec_edgar",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="sec_edgar_error",
                )
            )
        except Exception as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="fundamental",
                    provider="sec_edgar",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="sec_edgar_error",
                )
            )

    return symbol_runs


def execute_analyst_refresh_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    api_key: str,
    fetch_fn=fetch_alpha_vantage_company_overview,
    parse_fn=parse_alpha_vantage_analyst_consensus,
    persist_fn=persist_analyst_consensus,
    request_pause_seconds: float = 0.0,
    sleep_fn=time.sleep,
) -> list[SymbolRefreshRunRecord]:
    if not api_key or not tasks:
        return []

    symbol_runs: list[SymbolRefreshRunRecord] = []
    for index, task in enumerate(tasks):
        attempted_at = utc_now()
        try:
            payload = fetch_fn(task.symbol, api_key)
            snapshot = parse_fn(task.symbol, payload, as_of_date=attempted_at.date())
            if snapshot and database_url:
                persisted = persist_fn(database_url, [snapshot])
                if not persisted:
                    raise RuntimeError(f"Failed to persist analyst consensus for {task.symbol}")

            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="analyst",
                    provider="alpha_vantage_overview",
                    status="succeeded",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=None,
                    fetched_bar_count=1 if snapshot else 0,
                    provider_error_code=None,
                )
            )
        except AlphaVantageAnalystRateLimitError as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="analyst",
                    provider="alpha_vantage_overview",
                    status="rate_limited",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="alpha_vantage_rate_limit",
                )
            )
            break
        except Exception as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="analyst",
                    provider="alpha_vantage_overview",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="alpha_vantage_error",
                )
            )

        if request_pause_seconds > 0 and index < len(tasks) - 1:
            sleep_fn(request_pause_seconds)

    return symbol_runs


def execute_stooq_refresh_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    api_key: str,
    fetch_fn=fetch_stooq_daily,
    persist_fn=persist_price_bars,
    mark_refreshed_fn=update_symbol_last_price_refresh_at,
    persist_features_fn=persist_features,
    compute_features_fn=compute_price_features,
    load_fundamental_facts_fn=load_latest_fundamental_facts,
    compute_fundamental_features_fn=compute_fundamental_features,
    persist_ratings_fn=persist_ratings,
    build_rating_record_fn=build_rating_record,
) -> list[SymbolRefreshRunRecord]:
    symbol_runs: list[SymbolRefreshRunRecord] = []

    for task in tasks:
        attempted_at = utc_now()
        try:
            bars = fetch_fn(task.symbol, api_key)
            persisted = persist_fn(database_url, bars)
            if database_url and not persisted:
                raise RuntimeError(f"Failed to persist price bars for {task.symbol}")
            if persisted:
                features = build_symbol_features(
                    database_url,
                    task,
                    bars,
                    compute_price_features_fn=compute_features_fn,
                    load_fundamental_facts_fn=load_fundamental_facts_fn,
                    compute_fundamental_features_fn=compute_fundamental_features_fn,
                )
                features_persisted = persist_features_fn(database_url, features)
                if features and not features_persisted:
                    raise RuntimeError(f"Failed to persist derived features for {task.symbol}")
                rating_task = rating_task_for_features(task, features)
                rating_record = build_rating_record_fn(rating_task, features)
                rating_persisted = persist_ratings_fn(database_url, [rating_record])
                if not rating_persisted:
                    raise RuntimeError(f"Failed to persist rating for {task.symbol}")
                mark_refreshed_fn(database_url, task.symbol, utc_now())
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="stooq",
                    status="succeeded",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=None,
                    fetched_bar_count=len(bars),
                    provider_error_code=None,
                )
            )
        except Exception as error:
            error_code = "stooq_error"
            if isinstance(error, StooqResponseError):
                error_code = "stooq_error"
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="stooq",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code=error_code,
                )
            )

    return symbol_runs


def build_symbol_refresh_run_records(
    run_id: str,
    tasks: list[RefreshTask],
    provider: str,
    attempted_at,
) -> list[SymbolRefreshRunRecord]:
    return [
        SymbolRefreshRunRecord(
            run_id=run_id,
            symbol=task.symbol,
            data_type="price",
            provider=provider,
            status="planned",
            attempted_at=attempted_at,
            completed_at=None,
            error_message=None,
            fetched_bar_count=None,
            provider_error_code=None,
        )
        for task in tasks
    ]


def execute_rating_repair_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    load_price_bars_fn=load_recent_price_bars,
    persist_features_fn=persist_features,
    compute_features_fn=compute_price_features,
    load_fundamental_facts_fn=load_latest_fundamental_facts,
    compute_fundamental_features_fn=compute_fundamental_features,
    persist_ratings_fn=persist_ratings,
    build_rating_record_fn=build_rating_record,
) -> list[SymbolRefreshRunRecord]:
    symbol_runs: list[SymbolRefreshRunRecord] = []

    for task in tasks:
        attempted_at = utc_now()
        try:
            bars = load_price_bars_fn(database_url, task.symbol)
            if not bars:
                symbol_runs.append(
                    SymbolRefreshRunRecord(
                        run_id=run_id,
                        symbol=task.symbol,
                        data_type="rating",
                        provider="local_rebuild",
                        status="skipped",
                        attempted_at=attempted_at,
                        completed_at=utc_now(),
                        error_message="No stored price history available for rating rebuild.",
                        fetched_bar_count=0,
                        provider_error_code="missing_price_history",
                    )
                )
                continue

            features = build_symbol_features(
                database_url,
                task,
                bars,
                compute_price_features_fn=compute_features_fn,
                load_fundamental_facts_fn=load_fundamental_facts_fn,
                compute_fundamental_features_fn=compute_fundamental_features_fn,
            )
            if not features:
                symbol_runs.append(
                    SymbolRefreshRunRecord(
                        run_id=run_id,
                        symbol=task.symbol,
                        data_type="rating",
                        provider="local_rebuild",
                        status="skipped",
                        attempted_at=attempted_at,
                        completed_at=utc_now(),
                        error_message="No features available for rating rebuild.",
                        fetched_bar_count=len(bars),
                        provider_error_code="missing_features",
                    )
                )
                continue

            features_persisted = persist_features_fn(database_url, features)
            if not features_persisted:
                raise RuntimeError(f"Failed to persist derived features for {task.symbol}")

            rating_task = rating_task_for_features(task, features)
            rating_record = build_rating_record_fn(rating_task, features)
            rating_persisted = persist_ratings_fn(database_url, [rating_record])
            if not rating_persisted:
                raise RuntimeError(f"Failed to persist rating for {task.symbol}")

            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="rating",
                    provider="local_rebuild",
                    status="succeeded",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=None,
                    fetched_bar_count=len(bars),
                    provider_error_code=None,
                )
            )
        except Exception as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="rating",
                    provider="local_rebuild",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="rating_rebuild_error",
                )
            )

    return symbol_runs


def execute_alpha_vantage_refresh_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    api_key: str,
    fetch_fn=fetch_alpha_vantage_daily_adjusted,
    persist_fn=persist_price_bars,
    mark_refreshed_fn=update_symbol_last_price_refresh_at,
    persist_features_fn=persist_features,
    compute_features_fn=compute_price_features,
    load_fundamental_facts_fn=load_latest_fundamental_facts,
    compute_fundamental_features_fn=compute_fundamental_features,
    persist_ratings_fn=persist_ratings,
    build_rating_record_fn=build_rating_record,
    request_pause_seconds: float = 0.0,
    sleep_fn=time.sleep,
) -> list[SymbolRefreshRunRecord]:
    symbol_runs: list[SymbolRefreshRunRecord] = []

    for index, task in enumerate(tasks):
        attempted_at = utc_now()
        try:
            bars = fetch_fn(task.symbol, api_key)
            persisted = persist_fn(database_url, bars)
            if database_url and not persisted:
                raise RuntimeError(f"Failed to persist price bars for {task.symbol}")
            if persisted:
                features = build_symbol_features(
                    database_url,
                    task,
                    bars,
                    compute_price_features_fn=compute_features_fn,
                    load_fundamental_facts_fn=load_fundamental_facts_fn,
                    compute_fundamental_features_fn=compute_fundamental_features_fn,
                )
                features_persisted = persist_features_fn(database_url, features)
                if features and not features_persisted:
                    raise RuntimeError(f"Failed to persist derived features for {task.symbol}")
                rating_task = rating_task_for_features(task, features)
                rating_record = build_rating_record_fn(rating_task, features)
                rating_persisted = persist_ratings_fn(database_url, [rating_record])
                if not rating_persisted:
                    raise RuntimeError(f"Failed to persist rating for {task.symbol}")
                mark_refreshed_fn(database_url, task.symbol, utc_now())
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="alpha_vantage",
                    status="succeeded",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=None,
                    fetched_bar_count=len(bars),
                    provider_error_code=None,
                )
            )
        except AlphaVantageRateLimitError as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="alpha_vantage",
                    status="rate_limited",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="alpha_vantage_rate_limit",
                )
            )
            break
        except Exception as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="alpha_vantage",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="alpha_vantage_error",
                )
            )

        if request_pause_seconds > 0 and index < len(tasks) - 1:
            sleep_fn(request_pause_seconds)

    return symbol_runs


def execute_twelve_data_refresh_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    api_key: str,
    fetch_fn=fetch_twelve_data_time_series,
    persist_fn=persist_price_bars,
    mark_refreshed_fn=update_symbol_last_price_refresh_at,
    persist_features_fn=persist_features,
    compute_features_fn=compute_price_features,
    load_fundamental_facts_fn=load_latest_fundamental_facts,
    compute_fundamental_features_fn=compute_fundamental_features,
    persist_ratings_fn=persist_ratings,
    build_rating_record_fn=build_rating_record,
) -> list[SymbolRefreshRunRecord]:
    symbol_runs: list[SymbolRefreshRunRecord] = []

    for task in tasks:
        attempted_at = utc_now()
        try:
            bars = fetch_fn(task.symbol, api_key)
            persisted = persist_fn(database_url, bars)
            if database_url and not persisted:
                raise RuntimeError(f"Failed to persist price bars for {task.symbol}")
            if persisted:
                features = build_symbol_features(
                    database_url,
                    task,
                    bars,
                    compute_price_features_fn=compute_features_fn,
                    load_fundamental_facts_fn=load_fundamental_facts_fn,
                    compute_fundamental_features_fn=compute_fundamental_features_fn,
                )
                features_persisted = persist_features_fn(database_url, features)
                if features and not features_persisted:
                    raise RuntimeError(f"Failed to persist derived features for {task.symbol}")
                rating_task = rating_task_for_features(task, features)
                rating_record = build_rating_record_fn(rating_task, features)
                rating_persisted = persist_ratings_fn(database_url, [rating_record])
                if not rating_persisted:
                    raise RuntimeError(f"Failed to persist rating for {task.symbol}")
                mark_refreshed_fn(database_url, task.symbol, utc_now())
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="twelve_data",
                    status="succeeded",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=None,
                    fetched_bar_count=len(bars),
                    provider_error_code=None,
                )
            )
        except TwelveDataRateLimitError as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="twelve_data",
                    status="rate_limited",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="twelve_data_rate_limit",
                )
            )
            break
        except Exception as error:
            symbol_runs.append(
                SymbolRefreshRunRecord(
                    run_id=run_id,
                    symbol=task.symbol,
                    data_type="price",
                    provider="twelve_data",
                    status="failed",
                    attempted_at=attempted_at,
                    completed_at=utc_now(),
                    error_message=str(error),
                    fetched_bar_count=None,
                    provider_error_code="twelve_data_error",
                )
            )

    return symbol_runs


def execute_price_refresh_plan(
    run_id: str,
    tasks: list[RefreshTask],
    database_url: str,
    alpha_vantage_api_key: str,
    twelve_data_api_key: str,
    stooq_api_key: str = "",
    alpha_fetch_fn=fetch_alpha_vantage_daily_adjusted,
    twelve_fetch_fn=fetch_twelve_data_time_series,
    stooq_fetch_fn=fetch_stooq_daily,
    persist_fn=persist_price_bars,
    mark_refreshed_fn=update_symbol_last_price_refresh_at,
    persist_features_fn=persist_features,
    compute_features_fn=compute_price_features,
    persist_ratings_fn=persist_ratings,
    build_rating_record_fn=build_rating_record,
    alpha_vantage_max_requests: int | None = None,
    alpha_vantage_pause_seconds: float = 0.0,
    alpha_vantage_sleep_fn=time.sleep,
) -> list[SymbolRefreshRunRecord]:
    def unresolved_tasks_from(records: list[SymbolRefreshRunRecord], candidate_tasks: list[RefreshTask]) -> list[RefreshTask]:
        unresolved = {record.symbol for record in records if record.status in {"failed", "rate_limited"}}
        succeeded = {record.symbol for record in records if record.status == "succeeded"}
        return [task for task in candidate_tasks if task.symbol in unresolved and task.symbol not in succeeded]

    if alpha_vantage_api_key:
        alpha_tasks = tasks
        if alpha_vantage_max_requests is not None and alpha_vantage_max_requests >= 0:
            alpha_tasks = tasks[:alpha_vantage_max_requests]

        alpha_runs = execute_alpha_vantage_refresh_plan(
            run_id=run_id,
            tasks=alpha_tasks,
            database_url=database_url,
            api_key=alpha_vantage_api_key,
            fetch_fn=alpha_fetch_fn,
            persist_fn=persist_fn,
            mark_refreshed_fn=mark_refreshed_fn,
            persist_features_fn=persist_features_fn,
            compute_features_fn=compute_features_fn,
            persist_ratings_fn=persist_ratings_fn,
            build_rating_record_fn=build_rating_record_fn,
            request_pause_seconds=alpha_vantage_pause_seconds,
            sleep_fn=alpha_vantage_sleep_fn,
        )
        unresolved = {record.symbol for record in alpha_runs if record.status in {"rate_limited", "failed"}}
        alpha_succeeded = {record.symbol for record in alpha_runs if record.status == "succeeded"}
        skipped_symbols = {task.symbol for task in tasks[len(alpha_tasks):]}
        fallback_tasks = [
            task for task in tasks if (task.symbol in unresolved or task.symbol in skipped_symbols) and task.symbol not in alpha_succeeded
        ]

        if fallback_tasks and twelve_data_api_key:
            twelve_runs = execute_twelve_data_refresh_plan(
                run_id=run_id,
                tasks=fallback_tasks,
                database_url=database_url,
                api_key=twelve_data_api_key,
                fetch_fn=twelve_fetch_fn,
                persist_fn=persist_fn,
                mark_refreshed_fn=mark_refreshed_fn,
                persist_features_fn=persist_features_fn,
                compute_features_fn=compute_features_fn,
                persist_ratings_fn=persist_ratings_fn,
                build_rating_record_fn=build_rating_record_fn,
            )
            stooq_tasks = unresolved_tasks_from(twelve_runs, fallback_tasks)
            if stooq_tasks and stooq_api_key:
                stooq_runs = execute_stooq_refresh_plan(
                    run_id=run_id,
                    tasks=stooq_tasks,
                    database_url=database_url,
                    api_key=stooq_api_key,
                    fetch_fn=stooq_fetch_fn,
                    persist_fn=persist_fn,
                    mark_refreshed_fn=mark_refreshed_fn,
                    persist_features_fn=persist_features_fn,
                    compute_features_fn=compute_features_fn,
                    persist_ratings_fn=persist_ratings_fn,
                    build_rating_record_fn=build_rating_record_fn,
                )
                return alpha_runs + twelve_runs + stooq_runs
            return alpha_runs + twelve_runs

        if fallback_tasks and stooq_api_key:
            stooq_runs = execute_stooq_refresh_plan(
                run_id=run_id,
                tasks=fallback_tasks,
                database_url=database_url,
                api_key=stooq_api_key,
                fetch_fn=stooq_fetch_fn,
                persist_fn=persist_fn,
                mark_refreshed_fn=mark_refreshed_fn,
                persist_features_fn=persist_features_fn,
                compute_features_fn=compute_features_fn,
                persist_ratings_fn=persist_ratings_fn,
                build_rating_record_fn=build_rating_record_fn,
            )
            return alpha_runs + stooq_runs

        return alpha_runs

    if twelve_data_api_key:
        twelve_runs = execute_twelve_data_refresh_plan(
            run_id=run_id,
            tasks=tasks,
            database_url=database_url,
            api_key=twelve_data_api_key,
            fetch_fn=twelve_fetch_fn,
            persist_fn=persist_fn,
            mark_refreshed_fn=mark_refreshed_fn,
            persist_features_fn=persist_features_fn,
            compute_features_fn=compute_features_fn,
            persist_ratings_fn=persist_ratings_fn,
            build_rating_record_fn=build_rating_record_fn,
        )
        stooq_tasks = unresolved_tasks_from(twelve_runs, tasks)
        if stooq_tasks and stooq_api_key:
            stooq_runs = execute_stooq_refresh_plan(
                run_id=run_id,
                tasks=stooq_tasks,
                database_url=database_url,
                api_key=stooq_api_key,
                fetch_fn=stooq_fetch_fn,
                persist_fn=persist_fn,
                mark_refreshed_fn=mark_refreshed_fn,
                persist_features_fn=persist_features_fn,
                compute_features_fn=compute_features_fn,
                persist_ratings_fn=persist_ratings_fn,
                build_rating_record_fn=build_rating_record_fn,
            )
            return twelve_runs + stooq_runs
        return twelve_runs

    if stooq_api_key:
        return execute_stooq_refresh_plan(
            run_id=run_id,
            tasks=tasks,
            database_url=database_url,
            api_key=stooq_api_key,
            fetch_fn=stooq_fetch_fn,
            persist_fn=persist_fn,
            mark_refreshed_fn=mark_refreshed_fn,
            persist_features_fn=persist_features_fn,
            compute_features_fn=compute_features_fn,
            persist_ratings_fn=persist_ratings_fn,
            build_rating_record_fn=build_rating_record_fn,
        )

    return []


def pipeline_status_for(symbol_runs: list[SymbolRefreshRunRecord]) -> str:
    status_runs = [record for record in symbol_runs if record.data_type != "rating"]
    if not status_runs:
        return "planned"
    symbol_statuses: dict[str, set[str]] = {}
    for record in status_runs:
        symbol_statuses.setdefault(record.symbol, set()).add(record.status)

    if all("succeeded" in statuses for statuses in symbol_statuses.values()):
        return "success"
    if any("failed" in statuses or "rate_limited" in statuses for statuses in symbol_statuses.values()):
        return "partial"
    return "planned"


def resolve_git_sha(
    environ: dict[str, str] | None = None,
    git_rev_parse_fn=None,
) -> str | None:
    active_env = environ if environ is not None else os.environ
    github_sha = active_env.get("GITHUB_SHA", "").strip()
    if github_sha:
        return github_sha

    def _default_git_rev_parse() -> str:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )

    git_rev_parse = git_rev_parse_fn or _default_git_rev_parse
    try:
        local_sha = git_rev_parse().strip()
    except Exception:
        return None

    return local_sha or None


def main() -> None:
    settings = get_settings()
    git_sha = resolve_git_sha()
    run_id = generate_run_id()
    started_at = utc_now()
    macro_refresh_summary = execute_macro_refresh(settings.database_url, settings.fred_api_key)
    providers = get_price_provider_status(
        alpha_vantage_api_key=settings.alpha_vantage_api_key,
        twelve_data_api_key=settings.twelve_data_api_key,
        stooq_api_key=settings.stooq_api_key,
    )
    refresh_plan = build_default_refresh_plan()
    fundamental_plan = build_default_fundamental_refresh_plan()
    analyst_plan = build_default_analyst_refresh_plan()
    provider_name = preferred_provider_name(
        alpha_vantage_configured=providers[0].configured,
        twelve_data_configured=providers[1].configured,
        stooq_configured=providers[2].configured,
    )
    fundamental_runs = execute_fundamental_refresh_plan(
        run_id=run_id,
        tasks=fundamental_plan,
        database_url=settings.database_url,
        user_agent=settings.sec_user_agent,
    )
    if provider_name in {"alpha_vantage", "twelve_data", "stooq"}:
        price_runs = execute_price_refresh_plan(
            run_id=run_id,
            tasks=refresh_plan,
            database_url=settings.database_url,
            alpha_vantage_api_key=settings.alpha_vantage_api_key,
            twelve_data_api_key=settings.twelve_data_api_key,
            stooq_api_key=settings.stooq_api_key,
            alpha_vantage_max_requests=settings.alpha_vantage_max_requests_per_run,
            alpha_vantage_pause_seconds=settings.alpha_vantage_min_interval_seconds,
        )
    else:
        price_runs = build_symbol_refresh_run_records(
            run_id=run_id,
            tasks=refresh_plan,
            provider=provider_name,
            attempted_at=started_at,
        )
    analyst_runs = execute_analyst_refresh_plan(
        run_id=run_id,
        tasks=analyst_plan,
        database_url=settings.database_url,
        api_key=settings.alpha_vantage_api_key,
        request_pause_seconds=settings.alpha_vantage_min_interval_seconds,
    )
    rating_repair_plan = build_default_rating_repair_plan()
    rating_repair_runs = execute_rating_repair_plan(
        run_id=run_id,
        tasks=rating_repair_plan,
        database_url=settings.database_url,
    )
    symbol_runs = fundamental_runs + price_runs + analyst_runs + rating_repair_runs

    source_refresh_summaries = [
        macro_refresh_summary,
        summarize_symbol_runs("sec_edgar", fundamental_runs),
        summarize_symbol_runs("alpha_vantage_overview", analyst_runs),
        summarize_symbol_runs("local_rating_rebuild", rating_repair_runs),
        *summarize_provider_runs(price_runs),
    ]
    finished_at = utc_now()
    pipeline_run = build_pipeline_run_record(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        status=pipeline_status_for(symbol_runs),
        git_sha=git_sha,
    )
    database_persisted = persist_run_records(settings.database_url, pipeline_run, symbol_runs)
    artifact_path = write_plan_artifact(
        settings.plan_output_dir or None,
        pipeline_run,
        symbol_runs,
        source_refresh_summaries,
    )

    print(f"Run ID: {run_id}")
    print(f"Database persistence: {'enabled' if database_persisted else 'skipped'}")
    print(f"Macro refresh: {macro_refresh_summary.status}")
    print(f"Pipeline status: {pipeline_run.status}")
    print("Price providers:")
    for provider in providers:
        print(f"- {provider.provider}: {'configured' if provider.configured else 'missing key'}")

    print("Planned refresh order:")
    for task in refresh_plan:
        print(
            f"- {task.symbol}: tier={task.refresh_tier} age={task.age_in_days} freshness={task.freshness_status}"
        )
    print(f"Fundamental refresh tasks: {len(fundamental_plan)}")
    print(f"Analyst refresh tasks: {len(analyst_plan)}")
    print(f"Rating repair tasks: {len(rating_repair_plan)}")
    print(f"Plan artifact: {artifact_path}")


if __name__ == "__main__":
    main()
