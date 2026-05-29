from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
from uuid import uuid4

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


@dataclass(frozen=True)
class PipelineRunRecord:
    run_id: str
    started_at: datetime
    finished_at: datetime
    status: str
    error_message: str | None
    git_sha: str | None


@dataclass(frozen=True)
class SymbolRefreshRunRecord:
    run_id: str
    symbol: str
    data_type: str
    provider: str
    status: str
    attempted_at: datetime
    completed_at: datetime | None
    error_message: str | None
    fetched_bar_count: int | None = None
    provider_error_code: str | None = None


@dataclass(frozen=True)
class SourceRefreshSummaryRecord:
    source: str
    calls: int
    succeeded: int
    failed: int
    status: str


def generate_run_id() -> str:
    return str(uuid4())


def utc_now() -> datetime:
    return datetime.now(UTC)


def build_pipeline_run_record(
    run_id: str,
    started_at: datetime,
    finished_at: datetime,
    status: str,
    error_message: str | None = None,
    git_sha: str | None = None,
) -> PipelineRunRecord:
    return PipelineRunRecord(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        status=status,
        error_message=error_message,
        git_sha=git_sha,
    )


def write_plan_artifact(
    output_dir: str | None,
    pipeline_run: PipelineRunRecord,
    symbol_runs: list[SymbolRefreshRunRecord],
    source_refresh_summaries: list[SourceRefreshSummaryRecord] | None = None,
) -> Path:
    base_dir = Path(output_dir) if output_dir else Path(__file__).resolve().parents[3] / "artifacts" / "plans"
    base_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = base_dir / f"{pipeline_run.run_id}.json"

    payload = {
        "pipeline_run": _json_ready(asdict(pipeline_run)),
        "symbol_refresh_runs": [_json_ready(asdict(record)) for record in symbol_runs],
        "source_refresh_summaries": [
            _json_ready(asdict(record)) for record in (source_refresh_summaries or [])
        ],
    }

    artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return artifact_path


def persist_run_records(
    database_url: str,
    pipeline_run: PipelineRunRecord,
    symbol_runs: list[SymbolRefreshRunRecord],
    connect_fn=connect_postgres,
) -> bool:
    config = DatabaseConfig(url=database_url)
    if not is_configured(config):
        return False

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            insert into pipeline_runs (
                run_id,
                started_at,
                finished_at,
                status,
                error_message,
                git_sha
            ) values (%s, %s, %s, %s, %s, %s)
            """,
            (
                pipeline_run.run_id,
                pipeline_run.started_at,
                pipeline_run.finished_at,
                pipeline_run.status,
                pipeline_run.error_message,
                pipeline_run.git_sha,
            ),
        )

        if symbol_runs:
            cursor.executemany(
                """
                insert into symbol_refresh_runs (
                    run_id,
                    symbol,
                    data_type,
                    provider,
                    status,
                    attempted_at,
                    completed_at,
                    error_message,
                    fetched_bar_count,
                    provider_error_code
                ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        record.run_id,
                        record.symbol,
                        record.data_type,
                        record.provider,
                        record.status,
                        record.attempted_at,
                        record.completed_at,
                        record.error_message,
                        record.fetched_bar_count,
                        record.provider_error_code,
                    )
                    for record in symbol_runs
                ],
            )

        connection.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass


def _json_ready(payload: dict[str, object]) -> dict[str, object]:
    converted: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, datetime):
            converted[key] = value.isoformat()
        else:
            converted[key] = value
    return converted
