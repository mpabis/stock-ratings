from dataclasses import dataclass
from datetime import date
import time

from stock_rating.config import get_settings
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
from stock_rating.repository.runs import (
    SymbolRefreshRunRecord,
    build_pipeline_run_record,
    generate_run_id,
    persist_run_records,
    utc_now,
    write_plan_artifact,
)
from stock_rating.repository.ratings import persist_ratings
from stock_rating.repository.symbols import load_symbol_seeds, update_symbol_last_price_refresh_at
from stock_rating.rating.model_v1 import build_rating_record
from stock_rating.transform.features import compute_price_features, persist_features


MAX_PRICE_AGE_BY_TIER = {
    1: 1,
    2: 3,
    3: 5,
}


@dataclass(frozen=True)
class SymbolRefreshState:
    symbol: str
    refresh_tier: int
    last_price_date: date


@dataclass(frozen=True)
class RefreshTask:
    symbol: str
    refresh_tier: int
    age_in_days: int
    freshness_status: str


def age_in_days(as_of: date, last_price_date: date) -> int:
    return max(0, (as_of - last_price_date).days)


def freshness_status_for(symbol: SymbolRefreshState, as_of: date) -> str:
    age = age_in_days(as_of, symbol.last_price_date)
    max_age = MAX_PRICE_AGE_BY_TIER[symbol.refresh_tier]
    if age <= 1:
        return "fresh"
    if age <= max_age:
        return "aging"
    return "stale"


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


def preferred_provider_name(alpha_vantage_configured: bool, twelve_data_configured: bool, stooq_configured: bool) -> str:
    if alpha_vantage_configured:
        return "alpha_vantage"
    if twelve_data_configured:
        return "twelve_data"
    if stooq_configured:
        return "twelve_data"
    return "stooq"


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
                features = compute_features_fn(bars)
                features_persisted = persist_features_fn(database_url, features)
                if features and not features_persisted:
                    raise RuntimeError(f"Failed to persist derived features for {task.symbol}")
                rating_record = build_rating_record_fn(task, features)
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
                features = compute_features_fn(bars)
                features_persisted = persist_features_fn(database_url, features)
                if features and not features_persisted:
                    raise RuntimeError(f"Failed to persist derived features for {task.symbol}")
                rating_record = build_rating_record_fn(task, features)
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
                features = compute_features_fn(bars)
                features_persisted = persist_features_fn(database_url, features)
                if features and not features_persisted:
                    raise RuntimeError(f"Failed to persist derived features for {task.symbol}")
                rating_record = build_rating_record_fn(task, features)
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
    if not symbol_runs:
        return "planned"
    symbol_statuses: dict[str, set[str]] = {}
    for record in symbol_runs:
        symbol_statuses.setdefault(record.symbol, set()).add(record.status)

    if all("succeeded" in statuses for statuses in symbol_statuses.values()):
        return "success"
    if any("failed" in statuses or "rate_limited" in statuses for statuses in symbol_statuses.values()):
        return "partial"
    return "planned"


def main() -> None:
    settings = get_settings()
    run_id = generate_run_id()
    started_at = utc_now()
    providers = get_price_provider_status(
        alpha_vantage_api_key=settings.alpha_vantage_api_key,
        twelve_data_api_key=settings.twelve_data_api_key,
        stooq_api_key=settings.stooq_api_key,
    )
    refresh_plan = build_default_refresh_plan()
    provider_name = preferred_provider_name(
        alpha_vantage_configured=providers[0].configured,
        twelve_data_configured=providers[1].configured,
        stooq_configured=providers[2].configured,
    )
    if provider_name in {"alpha_vantage", "twelve_data", "stooq"}:
        symbol_runs = execute_price_refresh_plan(
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
        symbol_runs = build_symbol_refresh_run_records(
            run_id=run_id,
            tasks=refresh_plan,
            provider=provider_name,
            attempted_at=started_at,
        )
    finished_at = utc_now()
    pipeline_run = build_pipeline_run_record(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        status=pipeline_status_for(symbol_runs),
    )
    database_persisted = persist_run_records(settings.database_url, pipeline_run, symbol_runs)
    artifact_path = write_plan_artifact(settings.plan_output_dir or None, pipeline_run, symbol_runs)

    print(f"Run ID: {run_id}")
    print(f"Database persistence: {'enabled' if database_persisted else 'skipped'}")
    print(f"Pipeline status: {pipeline_run.status}")
    print("Price providers:")
    for provider in providers:
        print(f"- {provider.provider}: {'configured' if provider.configured else 'missing key'}")

    print("Planned refresh order:")
    for task in refresh_plan:
        print(
            f"- {task.symbol}: tier={task.refresh_tier} age={task.age_in_days} freshness={task.freshness_status}"
        )
    print(f"Plan artifact: {artifact_path}")


if __name__ == "__main__":
    main()
