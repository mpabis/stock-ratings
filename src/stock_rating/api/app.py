from dataclasses import asdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from stock_rating.config import get_settings
from stock_rating.db import connect_postgres
from stock_rating.pipeline.report import (
    fetch_latest_ratings,
    fetch_latest_run,
    fetch_quality_snapshots,
    fetch_run_status_counts,
    fetch_table_counts,
)
from stock_rating.quality.checks import QualityAlert, build_quality_alerts


def create_app() -> FastAPI:
    app = FastAPI(title="Stock Ratings API", version="0.1.0")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/summary")
    def summary() -> dict[str, object]:
        cursor = _open_cursor_or_503()
        try:
            latest_run = fetch_latest_run(cursor)
            latest_run_counts = fetch_run_status_counts(cursor, latest_run[0] if latest_run else None)
            table_counts = fetch_table_counts(cursor)
            quality_alerts = build_quality_alerts(fetch_quality_snapshots(cursor), date.today())
            return {
                "latest_run": _serialize_latest_run(latest_run),
                "run_status_counts": latest_run_counts,
                "table_counts": table_counts,
                "quality_alert_count": len(quality_alerts),
            }
        finally:
            _close_cursor(cursor)

    @app.get("/api/ratings")
    def ratings(limit: int = Query(default=100, ge=1, le=2000)) -> dict[str, object]:
        cursor = _open_cursor_or_503()
        try:
            rows = fetch_latest_ratings(cursor)
            return {
                "count": min(limit, len(rows)),
                "ratings": [_serialize_value(asdict(row)) for row in rows[:limit]],
            }
        finally:
            _close_cursor(cursor)

    @app.get("/api/quality-alerts")
    def quality_alerts(limit: int = Query(default=100, ge=1, le=2000)) -> dict[str, object]:
        cursor = _open_cursor_or_503()
        try:
            alerts = build_quality_alerts(fetch_quality_snapshots(cursor), date.today())
            return {
                "count": min(limit, len(alerts)),
                "alerts": [_serialize_quality_alert(alert) for alert in alerts[:limit]],
            }
        finally:
            _close_cursor(cursor)

    return app


app = create_app()


def _open_cursor_or_503():
    settings = get_settings()
    if not settings.database_url:
        raise HTTPException(status_code=503, detail="DATABASE_URL is not configured")

    try:
        connection = connect_postgres(settings.database_url)
        cursor = connection.cursor()
        setattr(cursor, "_connection", connection)
        return cursor
    except Exception as error:
        raise HTTPException(status_code=503, detail=f"Database unavailable: {error}") from error


def _close_cursor(cursor) -> None:
    connection = getattr(cursor, "_connection", None)
    try:
        cursor.close()
    finally:
        if connection is not None:
            connection.close()


def _serialize_latest_run(latest_run: tuple[str, str, datetime, datetime | None] | None) -> dict[str, object] | None:
    if latest_run is None:
        return None
    run_id, status, started_at, finished_at = latest_run
    return {
        "run_id": run_id,
        "status": status,
        "started_at": _serialize_value(started_at),
        "finished_at": _serialize_value(finished_at),
    }


def _serialize_quality_alert(alert: QualityAlert) -> dict[str, object]:
    return {
        "symbol": alert.symbol,
        "code": alert.code,
        "severity": alert.severity,
        "message": alert.message,
    }


def _serialize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _serialize_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_serialize_value(inner) for inner in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value