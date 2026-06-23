from collections import Counter
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import quote

from stock_rating.config import get_settings
from stock_rating.db import connect_postgres
from stock_rating.quality.checks import QualityAlert, SymbolQualitySnapshot, build_quality_alerts


TABLE_NAMES = [
    "symbols",
    "pipeline_runs",
    "symbol_refresh_runs",
    "price_daily",
    "macro_series_daily",
    "analyst_consensus_daily",
    "features_daily",
    "ratings_daily",
]


@dataclass(frozen=True)
class RatingSnapshot:
        symbol: str
        company_name: str
        rating_score: int
        rating_label: str
        freshness_status: str
        freshest_input_date: date | None
        valuation_score: Decimal | None
        quality_score: Decimal | None
        growth_score: Decimal | None
        momentum_score: Decimal | None
        risk_score: Decimal | None
        summary: str
        analyst_target_price: Decimal | None = None
        analyst_suggestion_label: str | None = None
        latest_price_close: Decimal | None = None
        strong_buy_count: int | None = None
        buy_count: int | None = None
        hold_count: int | None = None
        sell_count: int | None = None
        strong_sell_count: int | None = None
        analyst_revision_score: Decimal | None = None
        valuation_grade: str | None = None
        quality_grade: str | None = None
        growth_grade: str | None = None
        momentum_grade: str | None = None
        risk_grade: str | None = None
        analyst_revision_grade: str | None = None
        piotroski_fscore: Decimal | None = None
        piotroski_signals_available: Decimal | None = None
        magic_formula_combined_rank: Decimal | None = None
        acquirers_multiple: Decimal | None = None


@dataclass(frozen=True)
class SourceRefreshSummary:
    source: str
    calls: int
    succeeded: int
    failed: int
    status: str


def main() -> None:
    settings = get_settings()
    connection = connect_postgres(settings.database_url)
    cursor = connection.cursor()

    try:
        latest_run = fetch_latest_run(cursor)
        latest_run_counts = fetch_run_status_counts(cursor, latest_run[0] if latest_run else None)
        source_refresh_summaries = fetch_source_refresh_summaries(cursor, latest_run[0] if latest_run else None)
        table_counts = fetch_table_counts(cursor)
        ratings = fetch_latest_ratings(cursor)
        quality_alerts = build_quality_alerts(fetch_quality_snapshots(cursor), date.today())

        if latest_run:
            print(f"Latest run: {latest_run[0]}")
            print(f"Status: {latest_run[1]}")
            print(f"Started: {latest_run[2]}")
            print(f"Finished: {latest_run[3]}")
        print(f"Quality alerts: {len(quality_alerts)}")
        for alert in quality_alerts[:5]:
            print(f"- {alert.symbol}: {alert.message}")

        for table_name in TABLE_NAMES:
            print(f"{table_name}: {table_counts[table_name]}")

        output_path = write_dashboard(
            ratings,
            latest_run,
            latest_run_counts,
            source_refresh_summaries,
            table_counts,
            quality_alerts,
        )
        print(f"Dashboard: {output_path}")
    finally:
        cursor.close()
        connection.close()


def fetch_latest_run(cursor: Any) -> tuple[str, str, datetime, datetime | None] | None:
        cursor.execute(
                """
                select run_id, status, started_at, finished_at
                from pipeline_runs
                order by started_at desc
                limit 1
                """
        )
        return cursor.fetchone()


def fetch_table_counts(cursor: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table_name in TABLE_NAMES:
        try:
            cursor.execute(f"select count(*) from {table_name}")
            counts[table_name] = cursor.fetchone()[0]
        except Exception:
            counts[table_name] = 0
    return counts


def fetch_run_status_counts(cursor: Any, run_id: str | None) -> dict[str, int]:
    if not run_id:
        return {}

    cursor.execute(
        """
        select status, count(*)
        from symbol_refresh_runs
        where run_id = %s
        group by status
        """,
        (run_id,),
    )
    return {status: count for status, count in cursor.fetchall()}


def fetch_source_refresh_summaries(cursor: Any, run_id: str | None) -> list[SourceRefreshSummary]:
    if not run_id:
        return []

    artifact_path = Path("artifacts") / "plans" / f"{run_id}.json"
    if artifact_path.exists():
        try:
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}

        summaries = payload.get("source_refresh_summaries", [])
        if isinstance(summaries, list):
            results: list[SourceRefreshSummary] = []
            for item in summaries:
                if not isinstance(item, dict):
                    continue
                try:
                    results.append(
                        SourceRefreshSummary(
                            source=str(item.get("source", "unknown")),
                            calls=int(item.get("calls", 0)),
                            succeeded=int(item.get("succeeded", 0)),
                            failed=int(item.get("failed", 0)),
                            status=str(item.get("status", "unknown")),
                        )
                    )
                except Exception:
                    continue
            if results:
                return results

    return fetch_source_refresh_summaries_from_db(cursor, run_id)


def fetch_source_refresh_summaries_from_db(cursor: Any, run_id: str) -> list[SourceRefreshSummary]:
    try:
        cursor.execute(
            """
            select
                provider,
                count(*) as calls,
                sum(case when status = 'succeeded' then 1 else 0 end) as succeeded,
                sum(case when status in ('failed', 'rate_limited') then 1 else 0 end) as failed,
                sum(case when status = 'skipped' then 1 else 0 end) as skipped,
                min(attempted_at) as first_attempted_at
            from symbol_refresh_runs
            where run_id = %s
            group by provider
            order by first_attempted_at asc
            """,
            (run_id,),
        )
    except Exception:
        return []

    results: list[SourceRefreshSummary] = []
    for provider, calls, succeeded, failed, skipped, _ in cursor.fetchall():
        call_count = int(calls or 0)
        succeeded_count = int(succeeded or 0)
        failed_count = int(failed or 0)
        skipped_count = int(skipped or 0)
        results.append(
            SourceRefreshSummary(
                source=str(provider),
                calls=call_count,
                succeeded=succeeded_count,
                failed=failed_count,
                status=source_summary_status(call_count, succeeded_count, failed_count, skipped_count),
            )
        )
    return results


def source_summary_status(calls: int, succeeded: int, failed: int, skipped: int) -> str:
    if calls == 0:
        return "skipped"
    if succeeded == calls:
        return "succeeded"
    if skipped == calls:
        return "skipped"
    if succeeded == 0 and failed > 0:
        return "failed"
    return "partial"


def fetch_latest_ratings(cursor: Any) -> list[RatingSnapshot]:
    try:
        cursor.execute(
            """
            with ranked_ratings as (
                select
                    r.symbol,
                    s.company_name,
                    r.rating_score,
                    r.rating_label,
                    r.freshness_status,
                    r.freshest_input_date,
                    r.valuation_score,
                    r.quality_score,
                    r.growth_score,
                    r.momentum_score,
                    r.risk_score,
                    r.explanation_json,
                    r.analyst_revision_score,
                    r.valuation_grade,
                    r.quality_grade,
                    r.growth_grade,
                    r.momentum_grade,
                    r.risk_grade,
                    r.analyst_revision_grade,
                    row_number() over (
                        partition by r.symbol
                        order by r.date desc, r.created_at desc
                    ) as row_number
                from ratings_daily r
                join symbols s on s.symbol = r.symbol
            ),
            latest_analyst as (
                select distinct on (symbol)
                    symbol,
                    suggestion_label,
                    strong_buy_count,
                    buy_count,
                    hold_count,
                    sell_count,
                    strong_sell_count
                from analyst_consensus_daily
                order by symbol, date desc, ingested_at desc
            ),
            latest_analyst_target as (
                select distinct on (symbol)
                    symbol,
                    analyst_target_price
                from analyst_consensus_daily
                where analyst_target_price is not null
                order by symbol, date desc, ingested_at desc
            ),
            latest_prices as (
                select distinct on (symbol)
                    symbol,
                    coalesce(adjusted_close, close) as latest_price_close
                from price_daily
                order by symbol, date desc, ingested_at desc
            ),
            latest_feature_rows as (
                select distinct on (symbol, feature_name)
                    symbol,
                    feature_name,
                    feature_value
                from features_daily
                where feature_name in (
                    'piotroski_fscore',
                    'piotroski_signals_available',
                    'magic_formula_combined_rank',
                    'acquirers_multiple'
                )
                order by symbol, feature_name, date desc, source_version desc
            ),
            latest_features as (
                select
                    symbol,
                    max(feature_value) filter (where feature_name = 'piotroski_fscore') as piotroski_fscore,
                    max(feature_value) filter (where feature_name = 'piotroski_signals_available') as piotroski_signals_available,
                    max(feature_value) filter (where feature_name = 'magic_formula_combined_rank') as magic_formula_combined_rank,
                    max(feature_value) filter (where feature_name = 'acquirers_multiple') as acquirers_multiple
                from latest_feature_rows
                group by symbol
            )
            select
                rr.symbol,
                rr.company_name,
                rr.rating_score,
                rr.rating_label,
                rr.freshness_status,
                rr.freshest_input_date,
                rr.valuation_score,
                rr.quality_score,
                rr.growth_score,
                rr.momentum_score,
                rr.risk_score,
                rr.explanation_json,
                lt.analyst_target_price,
                la.suggestion_label,
                lp.latest_price_close,
                la.strong_buy_count,
                la.buy_count,
                la.hold_count,
                la.sell_count,
                la.strong_sell_count,
                rr.analyst_revision_score,
                rr.valuation_grade,
                rr.quality_grade,
                rr.growth_grade,
                rr.momentum_grade,
                rr.risk_grade,
                rr.analyst_revision_grade,
                lf.piotroski_fscore,
                lf.piotroski_signals_available,
                lf.magic_formula_combined_rank,
                lf.acquirers_multiple
            from ranked_ratings rr
            left join latest_analyst la on la.symbol = rr.symbol
            left join latest_analyst_target lt on lt.symbol = rr.symbol
            left join latest_prices lp on lp.symbol = rr.symbol
            left join latest_features lf on lf.symbol = rr.symbol
            where rr.row_number = 1
            order by rr.rating_score desc, rr.symbol asc
            """
        )
    except Exception:
        cursor.execute(
            """
            with ranked_ratings as (
                select
                    r.symbol,
                    s.company_name,
                    r.rating_score,
                    r.rating_label,
                    r.freshness_status,
                    r.freshest_input_date,
                    r.valuation_score,
                    r.quality_score,
                    r.growth_score,
                    r.momentum_score,
                    r.risk_score,
                    r.explanation_json,
                    row_number() over (
                        partition by r.symbol
                        order by r.date desc, r.created_at desc
                    ) as row_number
                from ratings_daily r
                join symbols s on s.symbol = r.symbol
            ),
            latest_prices as (
                select distinct on (symbol)
                    symbol,
                    coalesce(adjusted_close, close) as latest_price_close
                from price_daily
                order by symbol, date desc, ingested_at desc
            )
            select
                symbol,
                company_name,
                rating_score,
                rating_label,
                freshness_status,
                freshest_input_date,
                valuation_score,
                quality_score,
                growth_score,
                momentum_score,
                risk_score,
                explanation_json,
                null as analyst_target_price,
                null as suggestion_label,
                lp.latest_price_close,
                null as strong_buy_count,
                null as buy_count,
                null as hold_count,
                null as sell_count,
                null as strong_sell_count,
                null as analyst_revision_score,
                null as valuation_grade,
                null as quality_grade,
                null as growth_grade,
                null as momentum_grade,
                null as risk_grade,
                null as analyst_revision_grade,
                null as piotroski_fscore,
                null as piotroski_signals_available,
                null as magic_formula_combined_rank,
                null as acquirers_multiple
            from ranked_ratings rr
            left join latest_prices lp on lp.symbol = rr.symbol
            where row_number = 1
            order by rating_score desc, symbol asc
            """
        )

    snapshots: list[RatingSnapshot] = []
    for row in cursor.fetchall():
        explanation_json = row[11] or {}
        snapshots.append(
            RatingSnapshot(
                symbol=row[0],
                company_name=row[1],
                rating_score=row[2],
                rating_label=row[3],
                freshness_status=row[4],
                freshest_input_date=row[5],
                valuation_score=row[6],
                quality_score=row[7],
                growth_score=row[8],
                momentum_score=row[9],
                risk_score=row[10],
                analyst_target_price=row[12],
                analyst_suggestion_label=row[13],
                latest_price_close=row[14],
                strong_buy_count=row[15],
                buy_count=row[16],
                hold_count=row[17],
                sell_count=row[18],
                strong_sell_count=row[19],
                analyst_revision_score=row[20],
                valuation_grade=row[21],
                quality_grade=row[22],
                growth_grade=row[23],
                momentum_grade=row[24],
                risk_grade=row[25],
                analyst_revision_grade=row[26],
                piotroski_fscore=row[27],
                piotroski_signals_available=row[28],
                magic_formula_combined_rank=row[29],
                acquirers_multiple=row[30],
                summary=explanation_json.get("summary", "No explanation available."),
            )
        )
    return snapshots


def fetch_quality_snapshots(cursor: Any) -> list[SymbolQualitySnapshot]:
        cursor.execute(
                """
                with latest_prices as (
                        select symbol, max(date) as last_price_date
                        from price_daily
                        group by symbol
                ),
                latest_ratings as (
                        select symbol, max(date) as latest_rating_date
                        from ratings_daily
                        group by symbol
                )
                select
                        s.symbol,
                        s.refresh_tier,
                        lp.last_price_date,
                        lr.latest_rating_date
                from symbols s
                left join latest_prices lp on lp.symbol = s.symbol
                left join latest_ratings lr on lr.symbol = s.symbol
                where s.active = true
                order by s.symbol asc
                """
        )

        return [
                SymbolQualitySnapshot(
                        symbol=row[0],
                        refresh_tier=row[1],
                        last_price_date=row[2],
                        latest_rating_date=row[3],
                )
                for row in cursor.fetchall()
        ]


def write_dashboard(
        ratings: list[RatingSnapshot],
        latest_run: tuple[str, str, datetime, datetime | None] | None,
        latest_run_counts: dict[str, int],
        source_refresh_summaries: list[SourceRefreshSummary],
        table_counts: dict[str, int],
        quality_alerts: list[QualityAlert],
) -> Path:
        output_dir = Path("artifacts") / "reports"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "ratings-dashboard.html"
        methodology_path = output_dir / "ratings-methodology.html"
        dashboard_markdown_path = output_dir / "ratings-dashboard.md"
        dashboard_json_path = output_dir / "ratings-dashboard.json"
        methodology_markdown_path = output_dir / "ratings-methodology.md"
        output_path.write_text(
            render_dashboard_html(
                ratings,
                latest_run,
                latest_run_counts,
                source_refresh_summaries,
                table_counts,
                quality_alerts,
            ),
            encoding="utf-8",
        )
        methodology_path.write_text(
            render_methodology_html(source_refresh_summaries),
            encoding="utf-8",
        )
        dashboard_markdown_path.write_text(
            render_dashboard_markdown(
                ratings,
                latest_run,
                latest_run_counts,
                source_refresh_summaries,
                table_counts,
                quality_alerts,
            ),
            encoding="utf-8",
        )
        dashboard_json_path.write_text(
            render_dashboard_json(
                ratings,
                latest_run,
                latest_run_counts,
                source_refresh_summaries,
                table_counts,
                quality_alerts,
            ),
            encoding="utf-8",
        )
        methodology_markdown_path.write_text(
            render_methodology_markdown(source_refresh_summaries),
            encoding="utf-8",
        )
        return output_path.resolve()


def render_dashboard_html(
        ratings: list[RatingSnapshot],
        latest_run: tuple[str, str, datetime, datetime | None] | None,
        latest_run_counts: dict[str, int],
        source_refresh_summaries: list[SourceRefreshSummary],
        table_counts: dict[str, int],
        quality_alerts: list[QualityAlert],
) -> str:
        label_counts = Counter(rating.rating_label for rating in ratings)
        freshness_counts = Counter(rating.freshness_status for rating in ratings)
        average_score = round(sum(rating.rating_score for rating in ratings) / len(ratings), 1) if ratings else 0.0
        latest_input_date = max((rating.freshest_input_date for rating in ratings if rating.freshest_input_date), default=None)

        cards_html = "".join(
                [
                        render_stat_card("Tracked symbols", str(table_counts["symbols"]), "Universe currently configured in the database."),
                        render_stat_card("Published ratings", str(table_counts["ratings_daily"]), "All persisted rating rows across runs."),
                        render_stat_card("Average score", f"{average_score}", "Average latest score across the current universe."),
                        render_stat_card("Latest input date", format_date(latest_input_date), "Most recent feature date feeding the displayed ratings."),
                ]
        )

        label_pills = "".join(
                f'<div class="pill"><span>{escape(label)}</span><strong>{count}</strong></div>'
                for label, count in sorted(label_counts.items())
        ) or '<div class="pill"><span>No ratings</span><strong>0</strong></div>'

        freshness_pills = "".join(
                f'<div class="pill"><span>{escape(status.title())}</span><strong>{count}</strong></div>'
                for status, count in sorted(freshness_counts.items())
        ) or '<div class="pill"><span>No freshness data</span><strong>0</strong></div>'

        quality_alert_pills = "".join(
            f'<div class="pill"><span>{escape(alert_code.replace("_", " ").title())}</span><strong>{count}</strong></div>'
            for alert_code, count in sorted(Counter(alert.code for alert in quality_alerts).items())
        ) or '<div class="pill"><span>No active alerts</span><strong>0</strong></div>'

        quality_alert_rows = "".join(
            f'<li><strong>{escape(alert.symbol)}</strong><span>{escape(alert.message)}</span></li>'
            for alert in quality_alerts[:10]
        ) or '<li><strong>Healthy</strong><span>No active data quality alerts.</span></li>'

        rows_html = "".join(render_rating_row(rating) for rating in ratings) or (
            '<tr><td colspan="14" class="empty">No ratings found in ratings_daily.</td></tr>'
        )

        run_summary = render_run_summary(latest_run, latest_run_counts)
        source_metrics_html = render_source_metrics_table(source_refresh_summaries)

        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Ratings Dashboard</title>
    <style>
        :root {{
            --bg: #f3f6fa;
            --panel: #ffffff;
            --panel-strong: #ffffff;
            --text: #0f172a;
            --muted: #475569;
            --line: #dce3eb;
            --accent: #0f766e;
            --accent-soft: #e6f6f4;
            --warm: #b45309;
            --good: #0f766e;
            --warn: #b45309;
            --shadow: none;
        }}

        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            color: var(--text);
            background:
                radial-gradient(1100px 300px at 50% -210px, rgba(15, 118, 110, 0.12), transparent 70%),
                linear-gradient(180deg, #f8fafc 0%, var(--bg) 100%);
            min-height: 100vh;
        }}
        .page {{
            width: min(1760px, calc(100vw - 32px));
            margin: 0 auto;
            padding: 32px 0 40px;
        }}
        .hero {{
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 16px;
            box-shadow: var(--shadow);
            overflow: hidden;
            position: relative;
        }}
        .hero::after {{
            content: none;
        }}
        .hero-inner {{
            display: grid;
            grid-template-columns: minmax(0, 1.15fr) minmax(440px, 1.2fr);
            gap: 16px;
            padding: 18px 20px;
            align-items: start;
        }}
        h1 {{
            font-size: clamp(1.7rem, 2.8vw, 2.3rem);
            line-height: 1.03;
            margin: 0 0 8px;
            letter-spacing: -0.02em;
        }}
        .eyebrow {{
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.1em;
            font-size: 0.68rem;
            color: var(--accent);
            margin-bottom: 8px;
            font-weight: 700;
        }}
        .lead {{
            font-size: 0.92rem;
            line-height: 1.45;
            color: var(--muted);
            max-width: 52ch;
            margin: 0;
        }}
        .hero-link {{
            margin-top: 12px;
        }}
        .hero-link a {{
            display: inline-block;
            text-decoration: none;
            color: #ffffff;
            background: var(--accent);
            border-radius: 999px;
            padding: 8px 14px;
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.74rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .hero-panel {{
            background: #f8fafc;
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 14px;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .hero-panel .kicker {{
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.7rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--accent);
            font-weight: 700;
        }}
        .hero-panel .value {{
            font-size: 1.35rem;
            font-weight: 700;
        }}
        .hero-panel .meta {{
            color: var(--muted);
            line-height: 1.35;
            font-size: 0.84rem;
        }}
        .run-status-chip {{
            display: inline-flex;
            align-items: center;
            width: fit-content;
            padding: 6px 10px;
            border-radius: 999px;
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.75rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            border: 1px solid var(--line);
        }}
        .run-status-success {{
            background: rgba(62, 138, 99, 0.14);
            color: var(--good);
        }}
        .run-status-partial {{
            background: rgba(213, 166, 63, 0.16);
            color: var(--warn);
        }}
        .run-status-failed {{
            background: rgba(184, 92, 56, 0.16);
            color: var(--warm);
        }}
        .run-grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 8px;
            margin-top: 2px;
        }}
        .run-metric {{
            padding: 8px 10px;
            border-radius: 8px;
            background: #ffffff;
            border: 1px solid var(--line);
        }}
        .run-metric .label {{
            color: var(--muted);
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.65rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}
        .run-metric .value {{
            margin-top: 4px;
            font-size: 0.95rem;
            line-height: 1.25;
            word-break: break-word;
        }}
        .source-metrics-table-wrap {{
            margin-top: 2px;
            overflow-x: auto;
            border-radius: 10px;
            border: 1px solid var(--line);
            background: #ffffff;
        }}
        .source-metrics-table {{
            width: 100%;
            min-width: 0;
            border-collapse: collapse;
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            table-layout: fixed;
        }}
        .source-metrics-table th,
        .source-metrics-table td {{
            padding: 6px 8px;
            border-bottom: 1px solid var(--line);
            text-align: left;
            vertical-align: middle;
            font-size: 0.72rem;
            line-height: 1.15;
            white-space: nowrap;
        }}
        .source-metrics-table th {{
            color: var(--muted);
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.56rem;
            background: #f8fafc;
        }}
        .source-metrics-table tbody tr:last-child td {{
            border-bottom: 0;
        }}
        .source-metrics-table td:first-child {{
            color: var(--accent);
            font-weight: 700;
            letter-spacing: 0.04em;
            white-space: normal;
        }}
        .source-metrics-table th:nth-child(2),
        .source-metrics-table td:nth-child(2),
        .source-metrics-table th:nth-child(4),
        .source-metrics-table td:nth-child(4),
        .source-metrics-table th:nth-child(5),
        .source-metrics-table td:nth-child(5) {{
            width: 56px;
        }}
        .source-metrics-table th:nth-child(3),
        .source-metrics-table td:nth-child(3) {{
            width: 84px;
        }}
        .source-status-chip {{
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 2px 6px;
            font-size: 0.56rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            border: 1px solid rgba(31, 41, 51, 0.08);
        }}
        .source-status-succeeded {{
            color: var(--good);
            background: rgba(62, 138, 99, 0.12);
        }}
        .source-status-partial {{
            color: var(--warn);
            background: rgba(213, 166, 63, 0.14);
        }}
        .source-status-failed {{
            color: var(--warm);
            background: rgba(184, 92, 56, 0.16);
        }}
        .source-count-ok {{
            color: var(--good);
            font-weight: 700;
        }}
        .source-count-failed {{
            color: #b9402e;
            font-weight: 700;
        }}
        .section {{
            margin-top: 24px;
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 14px;
            box-shadow: var(--shadow);
            padding: 20px;
        }}
        .section-title {{
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 16px;
            margin-bottom: 18px;
        }}
        .section-title h2 {{
            margin: 0;
            font-size: 1.5rem;
        }}
        .section-title p {{
            margin: 0;
            color: var(--muted);
            font-size: 0.95rem;
        }}
        .cards {{
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 16px;
        }}
        .card {{
            background: var(--panel-strong);
            border: 1px solid var(--line);
            border-radius: 10px;
            padding: 14px;
        }}
        .card .label {{
            color: var(--muted);
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.8rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .card .number {{
            margin-top: 10px;
            font-size: 2rem;
            font-weight: 700;
        }}
        .card .caption {{
            margin-top: 8px;
            color: var(--muted);
            font-size: 0.92rem;
            line-height: 1.45;
        }}
        .pill-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .pill {{
            display: inline-flex;
            align-items: center;
            gap: 10px;
            border: 1px solid var(--line);
            background: var(--panel-strong);
            border-radius: 999px;
            padding: 8px 12px;
            font-size: 0.88rem;
        }}
        .pill strong {{
            color: var(--accent);
        }}
        .alert-list {{
            list-style: none;
            padding: 0;
            margin: 0;
            display: grid;
            gap: 12px;
        }}
        .alert-list li {{
            display: grid;
            gap: 4px;
            padding: 14px 16px;
            border-radius: 10px;
            border: 1px solid var(--line);
            background: var(--panel-strong);
        }}
        .alert-list strong {{
            font-size: 0.95rem;
        }}
        .alert-list span {{
            color: var(--muted);
            font-size: 0.92rem;
            line-height: 1.45;
        }}
        .table-scroll {{
            width: 100%;
            overflow-x: auto;
            border: 1px solid var(--line);
            border-radius: 10px;
            background: #ffffff;
        }}
        table {{
            width: 100%;
            min-width: 1580px;
            border-collapse: collapse;
        }}
        th, td {{
            text-align: left;
            padding: 14px 10px;
            border-bottom: 1px solid var(--line);
            vertical-align: top;
        }}
        th {{
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--muted);
            position: sticky;
            top: 0;
            z-index: 1;
            background: #f8fafc;
        }}
        th button {{
            border: 0;
            background: transparent;
            padding: 0;
            font: inherit;
            letter-spacing: inherit;
            text-transform: inherit;
            color: inherit;
            cursor: pointer;
        }}
        tbody tr:hover {{
            background: #f8fbfb;
        }}
        .symbol {{
            font-weight: 700;
            font-size: 1rem;
        }}
        .company-link {{
            display: inline-flex;
            flex-direction: column;
            align-items: flex-start;
            gap: 2px;
            color: inherit;
            text-decoration: none;
        }}
        .company-link:hover .symbol,
        .company-link:hover .company {{
            color: var(--accent);
            text-decoration: underline;
            text-underline-offset: 2px;
        }}
        .company {{
            color: var(--muted);
            font-size: 0.85rem;
            margin-top: 2px;
            line-height: 1.25;
        }}
        .analyst {{
            color: var(--muted);
            font-size: 0.76rem;
            margin-top: 3px;
            line-height: 1.3;
        }}
        .analyst-chip {{
            display: inline-flex;
            align-items: center;
            border-radius: 999px;
            padding: 4px 9px;
            font-size: 0.66rem;
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            letter-spacing: 0.06em;
            text-transform: uppercase;
            font-weight: 700;
            border: 1px solid var(--line);
        }}
        .analyst-chip-up {{
            background: #dcf5e5;
            color: #1f8b4d;
        }}
        .analyst-chip-down {{
            background: #fde4df;
            color: #b9402e;
        }}
        .analyst-chip-flat {{
            background: #efe8da;
            color: #7c6f5b;
        }}
        .target-cell {{
            width: 270px;
        }}
        .target-option-one {{
            display: grid;
            gap: 0;
        }}
        .target-grid {{
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 6px;
        }}
        .target-mini {{
            border: 1px solid var(--line);
            border-radius: 8px;
            padding: 6px;
            text-align: center;
            background: #ffffff;
        }}
        .target-mini .price {{
            display: block;
            font-size: 0.78rem;
            font-weight: 700;
            line-height: 1.2;
        }}
        .target-mini .pct {{
            display: block;
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.62rem;
            letter-spacing: 0.04em;
            font-weight: 700;
            margin-top: 2px;
        }}
        .target-mini .pct-up {{ color: var(--good); }}
        .target-mini .pct-down {{ color: var(--warm); }}
        .target-mini .pct-flat {{ color: var(--muted); }}
        .score-chip {{
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-width: 72px;
            gap: 3px;
            padding: 10px 12px;
            border-radius: 10px;
            border: 1px solid var(--line);
            color: #183042;
            font-weight: 700;
        }}
        .score-chip small {{
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.68rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            opacity: 0.72;
        }}
        .score-chip strong {{
            font-size: 1.35rem;
            line-height: 1;
        }}
        .score-band-low {{
            background: #fee2e2;
        }}
        .score-band-mid {{
            background: #fef3c7;
        }}
        .score-band-high {{
            background: #d1fae5;
        }}
        .freshness-fresh {{ color: var(--good); }}
        .freshness-aging {{ color: var(--warn); }}
        .freshness-stale {{ color: var(--warm); }}
        .factor-cell {{
            min-width: 82px;
        }}
        .factor-chip {{
            padding: 8px 8px 9px;
            border-radius: 10px;
            border: 1px solid var(--line);
            background: #ffffff;
        }}
        .factor-head {{
            display: block;
            margin-bottom: 5px;
        }}
        .factor-chip span {{
            display: block;
            color: var(--muted);
            font-family: "IBM Plex Sans", "Segoe UI", sans-serif;
            font-size: 0.65rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .factor-chip strong {{
            display: block;
            margin-top: 4px;
            font-size: 0.98rem;
            line-height: 1;
            white-space: nowrap;
        }}
        .factor-grade {{
            position: relative;
            top: -0.4em;
            left: 2px;
            font-size: 0.52rem;
            font-weight: 700;
            color: var(--muted);
            opacity: 0.75;
        }}
        .benchmark-cell {{
            font-variant-numeric: tabular-nums;
            white-space: nowrap;
            text-align: right;
            color: var(--muted);
            width: 76px;
        }}
        .benchmark-low-confidence {{
            color: #b45309;
            opacity: 0.72;
        }}
        .benchmark-value {{
            line-height: 1;
        }}
        .factor-track {{
            width: 100%;
            height: 7px;
            border-radius: 999px;
            background: rgba(31, 41, 51, 0.08);
            overflow: hidden;
        }}
        .factor-fill {{
            height: 100%;
            border-radius: 999px;
        }}
        .factor-fill-low {{
            background: #fb7185;
        }}
        .factor-fill-mid {{
            background: #f59e0b;
        }}
        .factor-fill-high {{
            background: #10b981;
        }}
        .empty {{
            text-align: center;
            color: var(--muted);
            padding: 24px;
        }}
        .footer {{
            margin-top: 14px;
            color: var(--muted);
            font-size: 0.9rem;
        }}
        .ratings-section {{
            margin-top: 20px;
        }}
        @media (max-width: 980px) {{
            .hero-inner, .cards {{
                grid-template-columns: 1fr;
            }}
            .source-grid {{
                grid-template-columns: 1fr;
            }}
            .factor-cell {{ min-width: 72px; }}
        }}
        @media (max-width: 720px) {{
            .page {{ width: min(100vw - 20px, 1760px); padding-top: 20px; }}
            .hero-inner, .section {{ padding: 18px; }}
            .table-scroll {{ overflow-x: visible; border: 0; }}
            table {{ min-width: 0; border: 0; }}
            table, thead, tbody, th, td, tr {{ display: block; }}
            thead {{ display: none; }}
            tbody tr {{ padding: 12px 0; border-bottom: 1px solid var(--line); }}
            td {{ border-bottom: 0; padding: 8px 0; }}
        }}
    </style>
</head>
<body>
    <main class="page">
        <section class="hero">
            <div class="hero-inner">
                <div>
                    <div class="eyebrow">Daily stock snapshot</div>
                    <h1>Today's signal, quickly.</h1>
                    <p class="lead">Flat, clean view of ratings, freshness, analyst stance, and targets with quick scan priority.</p>
                    <div class="hero-link"><a href="ratings-methodology.html">How Ratings Are Calculated</a></div>
                </div>
                <aside class="hero-panel">
                    <div class="kicker">Latest pipeline run</div>
                    <div class="run-status-chip {escape(run_summary['status_class'])}">{escape(run_summary['status_text'])}</div>
                    <div class="value">{escape(run_summary['headline'])}</div>
                    <div class="meta">{escape(run_summary['detail'])}</div>
                    <div class="run-grid">
                        <div class="run-metric">
                            <div class="label">Finished</div>
                            <div class="value">{escape(run_summary['finished_at'])}</div>
                        </div>
                        <div class="run-metric">
                            <div class="label">Result</div>
                            <div class="value">{escape(run_summary['result'])}</div>
                        </div>
                    </div>
                    <div class="kicker">Source calls</div>
                    {source_metrics_html}
                </aside>
            </div>
        </section>

        <section class="section ratings-section">
            <div class="section-title">
                <h2>Latest ratings</h2>
                <p>Sorted by score descending so the strongest names surface first.</p>
            </div>
            <div class="table-scroll">
            <table>
                <thead>
                    <tr>
                                                <th><button type="button" data-sort-index="0" data-sort-kind="text">Company</button></th>
                                                <th><button type="button" data-sort-index="1" data-sort-kind="number">Score</button></th>
                                                <th><button type="button" data-sort-index="2" data-sort-kind="text">Freshness</button></th>
                                                <th><button type="button" data-sort-index="3" data-sort-kind="number">Val</button></th>
                                                <th><button type="button" data-sort-index="4" data-sort-kind="number">Qual</button></th>
                                                <th><button type="button" data-sort-index="5" data-sort-kind="number">Growth</button></th>
                                                <th><button type="button" data-sort-index="6" data-sort-kind="number">Mom</button></th>
                                                <th><button type="button" data-sort-index="7" data-sort-kind="number">Risk</button></th>
                                                <th><button type="button" data-sort-index="8" data-sort-kind="number">Rev</button></th>
                                                <th><button type="button" data-sort-index="9" data-sort-kind="number">Analyst</button></th>
                                                <th><button type="button" data-sort-index="10" data-sort-kind="number">Target</button></th>
                                                <th title="Piotroski F-Score. Green values use all 9 signals; amber values are partial and low confidence."><button type="button" data-sort-index="11" data-sort-kind="number">F-Score</button></th>
                                                <th title="Magic Formula combined rank (1 = best)"><button type="button" data-sort-index="12" data-sort-kind="number">Magic</button></th>
                                                <th title="Acquirer's Multiple, EV/EBIT (lower is cheaper)"><button type="button" data-sort-index="13" data-sort-kind="number">EV/EBIT</button></th>
                    </tr>
                </thead>
                <tbody id="ratings-table-body">
                    {rows_html}
                </tbody>
            </table>
            </div>
        </section>

        <section class="section">
            <div class="section-title">
                <h2>Portfolio snapshot</h2>
                <p>Generated from the live database on demand.</p>
            </div>
            <div class="cards">{cards_html}</div>
        </section>

        <section class="section">
            <div class="section-title">
                <h2>Distribution</h2>
                <p>How the current universe is spread across labels and freshness buckets.</p>
            </div>
            <div class="pill-row">{label_pills}</div>
            <div style="height: 12px"></div>
            <div class="pill-row">{freshness_pills}</div>
        </section>

        <section class="section">
            <div class="section-title">
                <h2>Data quality</h2>
                <p>Flags active symbols with stale prices or missing ratings.</p>
            </div>
            <div class="pill-row">{quality_alert_pills}</div>
            <div style="height: 16px"></div>
            <ul class="alert-list">{quality_alert_rows}</ul>
        </section>

        <section class="section">
            <div class="footer">File is regenerated by <code>python -m stock_rating.pipeline.report</code> and written to <code>artifacts/reports/ratings-dashboard.html</code>.</div>
        </section>
    </main>
        <script>
            (() => {{
                const tbody = document.getElementById("ratings-table-body");
                if (!tbody) return;
                const directions = new Map();
                document.querySelectorAll("th button[data-sort-index]").forEach((button) => {{
                    button.addEventListener("click", () => {{
                        const columnIndex = Number(button.dataset.sortIndex);
                        const sortKind = button.dataset.sortKind || "text";
                        const current = directions.get(columnIndex) || "desc";
                        const next = current === "asc" ? "desc" : "asc";
                        directions.set(columnIndex, next);
                        const rows = Array.from(tbody.querySelectorAll("tr"));
                        rows.sort((leftRow, rightRow) => {{
                            const leftValue = leftRow.children[columnIndex]?.dataset.sort || "";
                            const rightValue = rightRow.children[columnIndex]?.dataset.sort || "";
                            let comparison = 0;
                            if (sortKind === "number") {{
                                comparison = Number(leftValue) - Number(rightValue);
                            }} else if (sortKind === "date") {{
                                comparison = Date.parse(leftValue) - Date.parse(rightValue);
                            }} else {{
                                comparison = leftValue.localeCompare(rightValue);
                            }}
                            return next === "asc" ? comparison : -comparison;
                        }});
                        rows.forEach((row) => tbody.appendChild(row));
                    }});
                }});
            }})();
        </script>
</body>
</html>
"""


def render_dashboard_markdown(
        ratings: list[RatingSnapshot],
        latest_run: tuple[str, str, datetime, datetime | None] | None,
        latest_run_counts: dict[str, int],
        source_refresh_summaries: list[SourceRefreshSummary],
        table_counts: dict[str, int],
        quality_alerts: list[QualityAlert],
) -> str:
    label_counts = Counter(rating.rating_label for rating in ratings)
    freshness_counts = Counter(rating.freshness_status for rating in ratings)
    average_score = round(sum(rating.rating_score for rating in ratings) / len(ratings), 1) if ratings else 0.0
    latest_input_date = max((rating.freshest_input_date for rating in ratings if rating.freshest_input_date), default=None)
    run_summary = render_run_summary(latest_run, latest_run_counts)

    lines = [
        "# Stock Ratings Dashboard",
        "",
        "Machine-readable companion to `ratings-dashboard.html`. Generated by `python -m stock_rating.pipeline.report`.",
        "",
        "## Latest Pipeline Run",
        "",
        f"- Status: {run_summary['status_text']}",
        f"- Headline: {run_summary['headline']}",
        f"- Finished: {run_summary['finished_at']}",
        f"- Result: {run_summary['result']}",
        "",
        "## Portfolio Snapshot",
        "",
        f"- Tracked symbols: {table_counts['symbols']}",
        f"- Published ratings: {table_counts['ratings_daily']}",
        f"- Average score: {average_score}",
        f"- Latest input date: {format_date(latest_input_date)}",
        "",
        "## Distribution",
        "",
        "| Type | Bucket | Count |",
        "|---|---:|---:|",
    ]

    for label, count in sorted(label_counts.items()):
        lines.append(f"| Rating | {markdown_cell(label)} | {count} |")
    for freshness_status, count in sorted(freshness_counts.items()):
        lines.append(f"| Freshness | {markdown_cell(freshness_status.title())} | {count} |")

    lines.extend(
        [
            "",
            "## Source Calls",
            "",
            "| Source | Calls | Succeeded | Failed | Status |",
            "|---|---:|---:|---:|---|",
        ]
    )
    if source_refresh_summaries:
        for summary in source_refresh_summaries:
            lines.append(
                "| "
                f"{markdown_cell(format_source_name(summary.source))} | "
                f"{summary.calls} | "
                f"{summary.succeeded} | "
                f"{summary.failed} | "
                f"{markdown_cell(summary.status.replace('_', ' ').title())} |"
            )
    else:
        lines.append("| No source summary | 0 | 0 | 0 | N/A |")

    lines.extend(
        [
            "",
            "## Latest Ratings",
            "",
            "| Rank | Symbol | Company | Score | Label | Freshness | Val | Qual | Growth | Mom | Risk | Rev | Analyst | Target Low | Target Mid | Target High | F-Score | Magic | EV/EBIT |",
            "|---:|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for rank, rating in enumerate(ratings, start=1):
        target_low, target_mid, target_high = derive_target_triplet(
            rating.analyst_target_price,
            rating.strong_buy_count,
            rating.buy_count,
            rating.hold_count,
            rating.sell_count,
            rating.strong_sell_count,
        )
        analyst_suggestion = (rating.analyst_suggestion_label or "N/A").replace("_", " ").title()
        lines.append(
            "| "
            f"{rank} | "
            f"{markdown_cell(rating.symbol)} | "
            f"{markdown_cell(rating.company_name)} | "
            f"{format_score_ten(Decimal(rating.rating_score))} | "
            f"{markdown_cell(rating.rating_label)} | "
            f"{markdown_cell(rating.freshness_status.title())} | "
            f"{factor_markdown(rating.valuation_score, rating.valuation_grade)} | "
            f"{factor_markdown(rating.quality_score, rating.quality_grade)} | "
            f"{factor_markdown(rating.growth_score, rating.growth_grade)} | "
            f"{factor_markdown(rating.momentum_score, rating.momentum_grade)} | "
            f"{factor_markdown(rating.risk_score, rating.risk_grade)} | "
            f"{factor_markdown(rating.analyst_revision_score, rating.analyst_revision_grade)} | "
            f"{markdown_cell(analyst_suggestion)} | "
            f"{target_markdown(target_low, rating.latest_price_close)} | "
            f"{target_markdown(target_mid, rating.latest_price_close)} | "
            f"{target_markdown(target_high, rating.latest_price_close)} | "
            f"{fscore_markdown(rating.piotroski_fscore, rating.piotroski_signals_available)} | "
            f"{decimal_markdown(rating.magic_formula_combined_rank, 'int')} | "
            f"{decimal_markdown(rating.acquirers_multiple, 'ratio')} |"
        )

    lines.extend(
        [
            "",
            "## Data Quality",
            "",
        ]
    )
    if quality_alerts:
        lines.extend(["| Symbol | Code | Message |", "|---|---|---|"])
        for alert in quality_alerts:
            lines.append(f"| {markdown_cell(alert.symbol)} | {markdown_cell(alert.code)} | {markdown_cell(alert.message)} |")
    else:
        lines.append("No active data quality alerts.")

    return "\n".join(lines) + "\n"


def render_dashboard_json(
        ratings: list[RatingSnapshot],
        latest_run: tuple[str, str, datetime, datetime | None] | None,
        latest_run_counts: dict[str, int],
        source_refresh_summaries: list[SourceRefreshSummary],
        table_counts: dict[str, int],
        quality_alerts: list[QualityAlert],
) -> str:
    label_counts = Counter(rating.rating_label for rating in ratings)
    freshness_counts = Counter(rating.freshness_status for rating in ratings)
    average_score = round(sum(rating.rating_score for rating in ratings) / len(ratings), 1) if ratings else 0.0
    latest_input_date = max((rating.freshest_input_date for rating in ratings if rating.freshest_input_date), default=None)
    run_summary = render_run_summary(latest_run, latest_run_counts)
    payload = {
        "artifact": "ratings-dashboard",
        "format_version": 1,
        "description": "Structured companion to ratings-dashboard.html for agents and automation.",
        "latest_run": {
            "run_id": latest_run[0] if latest_run else None,
            "status": latest_run[1] if latest_run else None,
            "started_at": iso_datetime(latest_run[2]) if latest_run else None,
            "finished_at": iso_datetime(latest_run[3]) if latest_run and latest_run[3] else None,
            "summary": run_summary,
            "status_counts": latest_run_counts,
        },
        "portfolio": {
            "tracked_symbols": table_counts["symbols"],
            "published_ratings": table_counts["ratings_daily"],
            "average_score": average_score,
            "latest_input_date": iso_date(latest_input_date),
        },
        "distribution": {
            "ratings": dict(sorted(label_counts.items())),
            "freshness": dict(sorted(freshness_counts.items())),
        },
        "source_calls": [
            {
                "source": summary.source,
                "display_name": format_source_name(summary.source),
                "calls": summary.calls,
                "succeeded": summary.succeeded,
                "failed": summary.failed,
                "status": summary.status,
            }
            for summary in source_refresh_summaries
        ],
        "ratings": [rating_to_json(rank, rating) for rank, rating in enumerate(ratings, start=1)],
        "quality_alerts": [
            {
                "symbol": alert.symbol,
                "code": alert.code,
                "message": alert.message,
            }
            for alert in quality_alerts
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def render_stat_card(label: str, value: str, caption: str) -> str:
        return (
                '<article class="card">'
                f'<div class="label">{escape(label)}</div>'
                f'<div class="number">{escape(value)}</div>'
                f'<div class="caption">{escape(caption)}</div>'
                '</article>'
        )


def render_source_metrics_table(source_refresh_summaries: list[SourceRefreshSummary]) -> str:
    if not source_refresh_summaries:
        return (
            '<div class="run-metric">'
            '<div class="label">No source summary</div>'
            '<div class="value">N/A</div>'
            '<div class="meta">Run artifact not found.</div>'
            '</div>'
        )

    rows_html = "".join(render_source_metric_row(summary) for summary in source_refresh_summaries)
    return (
        '<div class="source-metrics-table-wrap">'
        '<table class="source-metrics-table">'
        '<thead><tr><th>Source</th><th>Calls</th><th>Status</th><th>Ok</th><th>Failed</th></tr></thead>'
        f'<tbody>{rows_html}</tbody>'
        '</table>'
        '</div>'
    )


def render_source_metric_row(summary: SourceRefreshSummary) -> str:
    status_class = source_status_class(summary.status)
    row_summary = f"{summary.calls} calls - {format_source_status(summary)}"
    status_label = short_source_status(summary.status)
    return (
        f'<tr title="{escape(row_summary)}">'
        f'<td>{escape(format_source_name(summary.source))}</td>'
        f'<td>{summary.calls}</td>'
        f'<td><span class="source-status-chip {escape(status_class)}">{escape(status_label)}</span></td>'
        f'<td class="source-count-ok">{summary.succeeded}</td>'
        f'<td class="source-count-failed">{summary.failed}</td>'
        '</tr>'
    )


def source_status_class(status: str) -> str:
    normalized = status.strip().lower()
    if normalized in {"succeeded", "success"}:
        return "source-status-succeeded"
    if normalized == "partial":
        return "source-status-partial"
    return "source-status-failed"


def short_source_status(status: str) -> str:
    normalized = status.strip().lower().replace("_", " ")
    if normalized in {"succeeded", "success"}:
        return "OK"
    if normalized == "partial":
        return "Partial"
    return "Failed"


def yahoo_finance_symbol(symbol: str) -> str:
    if symbol.endswith(".ST"):
        return symbol
    if ":" in symbol:
        exchange, raw_symbol = symbol.split(":", 1)
        exchange_code = exchange.upper()
        normalized_symbol = raw_symbol.replace(".", "-")
        if exchange_code in {"NASDAQ", "NYSE", "AMEX", "ARCA"}:
            return normalized_symbol
        if exchange_code in {"TSE", "TSX"}:
            return f"{normalized_symbol}.TO"
        if exchange_code in {"ETR", "XETR"}:
            return f"{normalized_symbol}.DE"
        return normalized_symbol
    return symbol.replace(".", "-")


def yahoo_finance_url(symbol: str) -> str:
    return f"https://finance.yahoo.com/quote/{quote(yahoo_finance_symbol(symbol), safe='')}"


def render_rating_row(rating: RatingSnapshot) -> str:
    freshness_class = f"freshness-{escape(rating.freshness_status)}"
    rating_ten = format_score_ten(Decimal(rating.rating_score))
    score_band = score_band_class(Decimal(rating.rating_score))
    company_sort = f"{rating.symbol} {rating.company_name}".lower()
    analyst_suggestion = (rating.analyst_suggestion_label or "").replace("_", " ").title()
    analyst_sort_rank = analyst_rank_value(rating.analyst_suggestion_label)
    analyst_target_sort = str(rating.analyst_target_price) if rating.analyst_target_price is not None else "-1"
    analyst_badge_html = render_analyst_badge(analyst_suggestion or "N/A")
    target_option_one_html = render_target_option_one(
        analyst_target_price=rating.analyst_target_price,
        latest_price_close=rating.latest_price_close,
        strong_buy_count=rating.strong_buy_count,
        buy_count=rating.buy_count,
        hold_count=rating.hold_count,
        sell_count=rating.sell_count,
        strong_sell_count=rating.strong_sell_count,
    )
    finance_url = yahoo_finance_url(rating.symbol)
    company_link_html = (
        f'<a class="company-link" href="{escape(finance_url)}" target="_blank" rel="noopener noreferrer">'
        f'<span class="symbol">{escape(rating.symbol)}</span>'
        f'<span class="company">{escape(rating.company_name)}</span>'
        "</a>"
    )
    factor_cells_html = "".join(
        [
            render_factor_cell("Valuation", rating.valuation_score, rating.valuation_grade),
            render_factor_cell("Quality", rating.quality_score, rating.quality_grade),
            render_factor_cell("Growth", rating.growth_score, rating.growth_grade),
            render_factor_cell("Momentum", rating.momentum_score, rating.momentum_grade),
            render_factor_cell("Risk", rating.risk_score, rating.risk_grade),
            render_factor_cell("Analyst Rev", rating.analyst_revision_score, rating.analyst_revision_grade),
        ]
    )
    benchmark_cells_html = "".join(
        [
            render_fscore_cell(rating.piotroski_fscore, rating.piotroski_signals_available),
            render_benchmark_cell(rating.magic_formula_combined_rank, fmt="int"),
            render_benchmark_cell(rating.acquirers_multiple, fmt="ratio"),
        ]
    )
    freshness_date_title = format_date_readable(rating.freshest_input_date)
    freshness_title = f"Last updated: {freshness_date_title}"
    return (
        "<tr>"
        f'<td data-sort="{escape(company_sort)}">{company_link_html}</td>'
        f'<td data-sort="{rating.rating_score}"><span class="score-chip {score_band}"><small>Rating</small><strong>{escape(rating_ten)}</strong></span></td>'
        f'<td data-sort="{escape(rating.freshness_status)}" class="{freshness_class}" title="{escape(freshness_title)}">{escape(rating.freshness_status.title())}</td>'
        f'{factor_cells_html}'
        f'<td data-sort="{analyst_sort_rank}" class="analyst">{analyst_badge_html}</td>'
        f'<td data-sort="{escape(analyst_target_sort)}" class="target-cell">{target_option_one_html}</td>'
        f'{benchmark_cells_html}'
        "</tr>"
    )


def render_analyst_badge(suggestion_label: str) -> str:
    normalized = suggestion_label.strip().lower().replace(" ", "_")
    if normalized in {"strong_buy", "buy"}:
        chip_class = "analyst-chip-up"
    elif normalized in {"strong_sell", "sell"}:
        chip_class = "analyst-chip-down"
    else:
        chip_class = "analyst-chip-flat"
    return f'<span class="analyst-chip {chip_class}">{escape(suggestion_label)}</span>'


def render_target_option_one(
    analyst_target_price: Decimal | None,
    latest_price_close: Decimal | None,
    strong_buy_count: int | None,
    buy_count: int | None,
    hold_count: int | None,
    sell_count: int | None,
    strong_sell_count: int | None,
) -> str:
    targets = derive_target_triplet(
        analyst_target_price,
        strong_buy_count,
        buy_count,
        hold_count,
        sell_count,
        strong_sell_count,
    )

    cells_html = "".join(
        (
            '<div class="target-mini">'
            f'<span class="price">{escape(format_currency_usd(value))}</span>'
            f'<span class="pct {escape(target_pct_class(target_percent(value, latest_price_close)))}">'
            f'{escape(format_percent(target_percent(value, latest_price_close)))}'
            '</span>'
            '</div>'
        )
        for value in targets
    )

    return (
        '<div class="target-option-one">'
        f'<div class="target-grid">{cells_html}</div>'
        '</div>'
    )


def derive_target_triplet(
    median_target: Decimal | None,
    strong_buy_count: int | None,
    buy_count: int | None,
    hold_count: int | None,
    sell_count: int | None,
    strong_sell_count: int | None,
) -> list[Decimal | None]:
    if median_target is None:
        return [None, None, None]

    strong_buy = strong_buy_count or 0
    buy = buy_count or 0
    hold = hold_count or 0
    sell = sell_count or 0
    strong_sell = strong_sell_count or 0
    total = strong_buy + buy + hold + sell + strong_sell

    # Use analyst vote dispersion to estimate a low/high envelope around the reported target.
    if total <= 0:
        spread_pct = Decimal("0.12")
    else:
        weighted_bias = (strong_buy * 2 + buy) - (sell + strong_sell * 2)
        consensus_strength = Decimal(abs(weighted_bias)) / (Decimal(2) * Decimal(total))
        spread_pct = Decimal("0.06") + (Decimal("1") - consensus_strength) * Decimal("0.14")

    low = (median_target * (Decimal("1") - spread_pct)).quantize(Decimal("0.01"))
    high = (median_target * (Decimal("1") + spread_pct)).quantize(Decimal("0.01"))
    return [low, median_target.quantize(Decimal("0.01")), high]


def analyst_rank_value(suggestion_label: str | None) -> int:
    normalized = (suggestion_label or "").strip().lower().replace(" ", "_")
    ranking = {
        "strong_sell": 1,
        "sell": 2,
        "hold": 3,
        "buy": 4,
        "strong_buy": 5,
    }
    return ranking.get(normalized, 0)


def render_run_summary(
    latest_run: tuple[str, str, datetime, datetime | None] | None,
    latest_run_counts: dict[str, int],
) -> dict[str, str]:
    if latest_run is None:
        return {
            "headline": "No run data",
            "detail": "pipeline_runs is empty, so the dashboard only reflects persisted rating rows.",
            "run_id": "N/A",
            "started_at": "N/A",
            "finished_at": "N/A",
            "result": "No symbol refresh data",
            "status_text": "No run",
            "status_class": "run-status-failed",
        }

    run_id, status, started_at, finished_at = latest_run
    finished_text = format_timestamp(finished_at) if finished_at else "Still running"
    started_text = format_timestamp(started_at)
    succeeded_count = latest_run_counts.get("succeeded", 0)
    failed_count = latest_run_counts.get("failed", 0)
    rate_limited_count = latest_run_counts.get("rate_limited", 0)
    result_parts = [f"{succeeded_count} succeeded"]
    if failed_count:
        result_parts.append(f"{failed_count} failed")
    if rate_limited_count:
        result_parts.append(f"{rate_limited_count} rate limited")
    return {
        "headline": f"{status.title()} update",
        "detail": f"Last database-backed refresh attempt for the tracked universe.",
        "run_id": run_id,
        "started_at": started_text,
        "finished_at": finished_text,
        "result": ", ".join(result_parts),
        "status_text": status.replace("_", " "),
        "status_class": run_status_class(status),
    }


def format_source_name(source: str) -> str:
    normalized = source.strip().lower()
    if normalized == "fred":
        return "FRED"
    if normalized == "sec_edgar":
        return "SEC EDGAR"
    if normalized == "alpha_vantage":
        return "Alpha Vantage"
    if normalized == "twelve_data":
        return "Twelve Data"
    if normalized == "stooq":
        return "Stooq"
    return source.replace("_", " ").title()


def format_source_status(summary: SourceRefreshSummary) -> str:
    status = summary.status.replace("_", " ").title()
    return f"{status} · {summary.succeeded} succeeded, {summary.failed} failed"


def run_status_class(status: str) -> str:
    normalized = status.lower()
    if normalized == "success":
        return "run-status-success"
    if normalized == "partial":
        return "run-status-partial"
    return "run-status-failed"


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    timezone_label = ""
    if value.tzinfo is not None:
        timezone_label = value.strftime(" %Z") or " UTC"
    return value.strftime("%b %d, %Y, %H:%M") + timezone_label


def format_date(value: date | None) -> str:
    if value is None:
        return "N/A"
    return value.isoformat()


def format_date_readable(value: date | None) -> str:
    if value is None:
        return "N/A"
    return value.strftime("%b %d, %Y")


def format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}"


def format_currency(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.2f}"


def format_currency_usd(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:.2f}"


def target_percent(target_value: Decimal | None, latest_price_close: Decimal | None) -> Decimal | None:
    if target_value is None or latest_price_close is None:
        return None
    if latest_price_close == 0:
        return None
    percent = ((target_value - latest_price_close) / latest_price_close) * Decimal("100")
    return percent.quantize(Decimal("0.1"))


def format_percent(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def target_pct_class(value: Decimal | None) -> str:
    if value is None:
        return "pct-flat"
    if value > 0:
        return "pct-up"
    if value < 0:
        return "pct-down"
    return "pct-flat"


def rating_to_json(rank: int, rating: RatingSnapshot) -> dict[str, Any]:
    target_low, target_mid, target_high = derive_target_triplet(
        rating.analyst_target_price,
        rating.strong_buy_count,
        rating.buy_count,
        rating.hold_count,
        rating.sell_count,
        rating.strong_sell_count,
    )
    return {
        "rank": rank,
        "symbol": rating.symbol,
        "company_name": rating.company_name,
        "score": rating.rating_score,
        "score_10": decimal_to_float(Decimal(rating.rating_score) / Decimal("10")),
        "label": rating.rating_label,
        "freshness_status": rating.freshness_status,
        "freshest_input_date": iso_date(rating.freshest_input_date),
        "latest_price_close": decimal_to_float(rating.latest_price_close),
        "factors": {
            "valuation": factor_to_json(rating.valuation_score, rating.valuation_grade),
            "quality": factor_to_json(rating.quality_score, rating.quality_grade),
            "growth": factor_to_json(rating.growth_score, rating.growth_grade),
            "momentum": factor_to_json(rating.momentum_score, rating.momentum_grade),
            "risk": factor_to_json(rating.risk_score, rating.risk_grade),
            "analyst_revision": factor_to_json(rating.analyst_revision_score, rating.analyst_revision_grade),
        },
        "analyst": {
            "suggestion_label": rating.analyst_suggestion_label,
            "target_price": decimal_to_float(rating.analyst_target_price),
            "strong_buy_count": rating.strong_buy_count,
            "buy_count": rating.buy_count,
            "hold_count": rating.hold_count,
            "sell_count": rating.sell_count,
            "strong_sell_count": rating.strong_sell_count,
            "derived_targets": {
                "low": target_to_json(target_low, rating.latest_price_close),
                "mid": target_to_json(target_mid, rating.latest_price_close),
                "high": target_to_json(target_high, rating.latest_price_close),
            },
        },
        "benchmarks": {
            "piotroski_fscore": decimal_to_float(rating.piotroski_fscore),
            "piotroski_signals_available": decimal_to_float(rating.piotroski_signals_available),
            "magic_formula_combined_rank": decimal_to_float(rating.magic_formula_combined_rank),
            "acquirers_multiple": decimal_to_float(rating.acquirers_multiple),
        },
        "summary": rating.summary,
    }


def factor_to_json(score: Decimal | None, grade: str | None) -> dict[str, float | str | None]:
    return {
        "score": decimal_to_float(score),
        "score_10": decimal_to_float(score / Decimal("10")) if score is not None else None,
        "grade": grade,
    }


def target_to_json(target_value: Decimal | None, latest_price_close: Decimal | None) -> dict[str, float | None]:
    return {
        "price": decimal_to_float(target_value),
        "upside_percent": decimal_to_float(target_percent(target_value, latest_price_close)),
    }


def decimal_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def iso_date(value: date | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def factor_markdown(score: Decimal | None, grade: str | None) -> str:
    score_text = format_score_ten(score)
    if grade:
        return markdown_cell(f"{score_text} {grade}")
    return markdown_cell(score_text)


def target_markdown(target_value: Decimal | None, latest_price_close: Decimal | None) -> str:
    price = format_currency_usd(target_value)
    percent = format_percent(target_percent(target_value, latest_price_close))
    if price == "N/A":
        return "N/A"
    return markdown_cell(f"{price} ({percent})")


def fscore_markdown(score: Decimal | None, signals_available: Decimal | None) -> str:
    if score is None:
        return "N/A"
    if signals_available is None:
        return str(int(score))
    if signals_available < Decimal("9"):
        return markdown_cell(f"{int(score)}/9 low confidence; {int(signals_available)}/9 signals")
    return markdown_cell(f"{int(score)}/9")


def decimal_markdown(value: Decimal | None, fmt: str = "number") -> str:
    if value is None:
        return "N/A"
    if fmt == "int":
        return str(int(value))
    if fmt == "ratio":
        return f"{float(value):.1f}x"
    return markdown_cell(value)


def format_score_ten(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{(value / Decimal('10')):.1f}"


def score_band_class(value: Decimal | None) -> str:
    if value is None:
        return "score-band-mid"
    if value >= 70:
        return "score-band-high"
    if value >= 45:
        return "score-band-mid"
    return "score-band-low"


def render_factor_cell(name: str, value: Decimal | None, grade: str | None = None) -> str:
    score_text = format_score_ten(value)
    width = factor_width(value)
    fill_class = factor_fill_class(value)
    sort_value = factor_sort_value(value)
    short_name = factor_short_name(name)
    grade_html = f'<sup class="factor-grade">{escape(grade)}</sup>' if grade else ""
    return (
        f'<td class="factor-cell" data-sort="{sort_value}">'
        '<div class="factor-chip">'
        f'<div class="factor-head"><span>{escape(short_name)}</span><strong>{escape(score_text)}{grade_html}</strong></div>'
        f'<div class="factor-track"><div class="factor-fill {fill_class}" style="width: {width}%"></div></div>'
        '</div>'
        '</td>'
    )


def render_benchmark_cell(value: Decimal | None, fmt: str = "number", sort_floor: str = "-1") -> str:
    """Render a benchmark-score cell (F-Score, Magic Formula rank, EV/EBIT).

    Missing values render an em dash and sort last. ``fmt`` controls display:
    "int" for whole numbers, "ratio" for two-decimal multiples, else as-is.
    """
    if value is None:
        return f'<td class="benchmark-cell" data-sort="{escape(sort_floor)}">—</td>'
    if fmt == "int":
        display = str(int(value))
    elif fmt == "ratio":
        display = f"{float(value):.1f}x"
    else:
        display = str(value)
    return f'<td class="benchmark-cell" data-sort="{escape(str(value))}">{escape(display)}</td>'


def render_fscore_cell(score: Decimal | None, signals_available: Decimal | None) -> str:
    """Piotroski F-Score with confidence communicated by color and tooltip."""
    if score is None:
        return '<td class="benchmark-cell" data-sort="-1">—</td>'
    low_confidence = signals_available is not None and signals_available < Decimal("9")
    if low_confidence:
        cell_class = "benchmark-cell benchmark-low-confidence"
    else:
        cell_class = "benchmark-cell"
    display = f"{int(score)}/9"
    if low_confidence and signals_available is not None:
        title = f' title="{escape(f"Low confidence: {int(score)} positive signals out of only {int(signals_available)} evaluable; 9 required for full confidence")}"'
    elif signals_available is not None:
        title = f' title="{escape(f"Full confidence: all {int(signals_available)} of 9 Piotroski signals evaluable")}"'
    else:
        title = ""
    return (
        f'<td class="{cell_class}" data-sort="{escape(str(score))}"{title}>'
        f'<span class="benchmark-value">{escape(display)}</span>'
        "</td>"
    )


def factor_width(value: Decimal | None) -> int:
    if value is None:
        return 0
    bounded = max(Decimal("0"), min(Decimal("100"), value))
    return int(round(float(bounded)))


def factor_fill_class(value: Decimal | None) -> str:
    if value is None:
        return "factor-fill-mid"
    if value >= 70:
        return "factor-fill-high"
    if value >= 45:
        return "factor-fill-mid"
    return "factor-fill-low"


def factor_sort_value(value: Decimal | None) -> int:
    if value is None:
        return 0
    bounded = max(Decimal("0"), min(Decimal("100"), value))
    return int(round(float(bounded)))


def factor_short_name(name: str) -> str:
    labels = {
        "Valuation": "Val",
        "Quality": "Qual",
        "Growth": "Growth",
        "Momentum": "Mom",
        "Risk": "Risk",
        "Analyst Rev": "Rev",
    }
    return labels.get(name, name)


def render_methodology_markdown(source_refresh_summaries: list[SourceRefreshSummary]) -> str:
    lines = [
        "# Stock Rating Methodology",
        "",
        "Machine-readable companion to `ratings-methodology.html`. This document is optimized for AI agents and code review because it avoids layout-only HTML/CSS and keeps formulas, weights, and source mappings in plain text tables.",
        "",
        "## Rating Scale",
        "",
        "Grades are assigned by percentile rank against the tracked universe in even 20% buckets, not by absolute score thresholds. The displayed score is the composite percentile rescaled to 0-100.",
        "",
        "| Score Percentile | Label |",
        "|---|---|",
        "| 80-100 | A / Very Attractive |",
        "| 60-79 | B / Attractive |",
        "| 40-59 | C / Neutral |",
        "| 20-39 | D / Unattractive |",
        "| 0-19 | F / Very Unattractive |",
        "",
        "## Source To Feature Mapping",
        "",
        "| Feature Family | Features | Primary Sources | Code Path |",
        "|---|---|---|---|",
        "| Price / Technical | intraday_return, one_day_return, five_day_return, ten_day_return, twenty_day_return, sixty_day_return, one_hundred_day_return, daily_volume, average_volume_20d, twenty_day_volatility, twenty_day_max_drawdown, high_low_range_pct, gap_open_return | Alpha Vantage, Twelve Data, Stooq | ingest/prices.py, transform/features.py |",
        "| Fundamental | net_margin, cash_flow_margin, return_on_assets, debt_to_assets, earnings_yield, book_to_price, revenue_growth_yoy, net_income_growth_yoy, operating_cash_flow_growth_yoy | SEC EDGAR company facts | ingest/sec_companyfacts.py, transform/fundamentals.py |",
        "| Analyst Consensus | analyst_target_price, analyst recommendation counts, suggestion_label | Alpha Vantage OVERVIEW, Finnhub | ingest/analyst.py, analyst_consensus_daily |",
        "| Analyst Revision | analyst_revision_score, analyst_suggestion_score_delta, analyst_target_price_change_pct | analyst_consensus_daily history | repository/analyst.py, transform/analyst_features.py |",
        "| Benchmark scores | piotroski_fscore, magic_formula_roic, magic_formula_earnings_yield, magic_formula_combined_rank, acquirers_multiple | SEC EDGAR company facts | transform/benchmark_scores.py, rating/magic_formula.py |",
        "| Macro | yield_curve_slope | FRED DGS10 and DGS2 | ingest/fred_macro.py, transform/macro.py |",
        "",
        "## Latest Source Calls",
        "",
        "| Source | Calls | Succeeded | Failed | Status |",
        "|---|---:|---:|---:|---|",
    ]

    if source_refresh_summaries:
        for summary in source_refresh_summaries:
            lines.append(
                "| "
                f"{markdown_cell(format_source_name(summary.source))} | "
                f"{summary.calls} | "
                f"{summary.succeeded} | "
                f"{summary.failed} | "
                f"{markdown_cell(summary.status.replace('_', ' ').title())} |"
            )
    else:
        lines.append("| No source summary | 0 | 0 | 0 | N/A |")

    lines.extend(
        [
            "",
            "## Factor Calculations",
            "",
            "### Valuation",
            "",
            "```text",
            "liquidity_score = clamp(25 + daily_volume / 200000)",
            "reversal_score = clamp(55 - one_day_return*250 - intraday_return*100)",
            "valuation = average(reversal/liquidity baseline, earnings_yield, book_to_price, profitability, cash flow, leverage)",
            "```",
            "",
            "If SEC valuation inputs are missing, valuation falls back to the conservative reversal/liquidity baseline.",
            "",
            "### Quality",
            "",
            "```text",
            "quality_baseline = clamp(38 + liquidity*0.45 - abs(intraday_return - one_day_return)*350)",
            "quality = average(quality_baseline, net_margin, cash_flow_margin, return_on_assets, leverage)",
            "```",
            "",
            "### Growth",
            "",
            "```text",
            "short_term_trend = clamp(50 + one_day_return*400 + intraday_return*150)",
            "growth = average(short_term_trend, revenue_growth_yoy, net_income_growth_yoy, operating_cash_flow_growth_yoy, medium_term_momentum)",
            "growth = clamp(growth*0.75 + macro_growth*0.25)",
            "```",
            "",
            "### Momentum",
            "",
            "```text",
            "momentum_score = clamp(50 + best_available_100/60/20/10/5/1_day_return*120 + twenty_day_return*60 + liquidity*0.05)",
            "```",
            "",
            "### Risk",
            "",
            "```text",
            "risk_penalty = abs(one_day_return)*250 + abs(intraday_return)*150 + volatility*450 + max_drawdown*250",
            "risk = average(price_stability, leverage, cash_generation, profitability)",
            "risk = clamp(risk*0.75 + macro_risk*0.25)",
            "```",
            "",
            "### Analyst Revision",
            "",
            "```text",
            "analyst_revision_score = clamp(50 + analyst_suggestion_score_delta*15 + analyst_target_price_change_pct*100)",
            "```",
            "",
            "A symbol with no analyst history or only one snapshot contributes neutral 50, so uncovered symbols are not penalized.",
            "",
            "## Final Composite Score",
            "",
            "Scoring runs in two passes. Pass 1 computes the weighted composite. Pass 2 ranks every symbol's composite against the universe and assigns A-F grades from percentile buckets.",
            "",
            "```text",
            "composite = valuation*0.225 + quality*0.225 + growth*0.18 + momentum*0.18 + risk*0.09 + analyst_revision*0.10",
            "score = round(percentile_rank(composite, universe) * 100)",
            "```",
            "",
            "| Factor | Weight |",
            "|---|---:|",
            "| Valuation | 22.5% |",
            "| Quality | 22.5% |",
            "| Growth | 18% |",
            "| Momentum | 18% |",
            "| Risk | 9% |",
            "| Analyst Revision | 10% |",
            "",
            "## Benchmark Scores",
            "",
            "Benchmark scores are shown beside the composite but deliberately excluded from the weighted score.",
            "",
            "| Benchmark | Formula | Interpretation |",
            "|---|---|---|",
            "| Piotroski F-Score | 0-9 binary profitability / leverage / efficiency signals | Higher is better; full-confidence values are highlighted in HTML, while partial values are muted and explain coverage in the tooltip. |",
            "| Magic Formula | rank(ROIC) + rank(EBIT / enterprise value) | Lower combined rank is better; financials and utilities are excluded when sector is known. |",
            "| Acquirer's Multiple | enterprise value / EBIT | Lower is cheaper. EBIT is approximated with OperatingIncomeLoss. |",
        ]
    )

    return "\n".join(lines) + "\n"


def render_methodology_html(source_refresh_summaries: list[SourceRefreshSummary]) -> str:
    source_rows = "".join(
        (
            "<tr>"
            f"<td>{escape(format_source_name(summary.source))}</td>"
            f"<td>{summary.calls}</td>"
            f"<td>{summary.succeeded}</td>"
            f"<td>{summary.failed}</td>"
            f"<td>{escape(summary.status.replace('_', ' ').title())}</td>"
            "</tr>"
        )
        for summary in source_refresh_summaries
    ) or "<tr><td colspan=\"5\">No source call summary available in latest artifact.</td></tr>"

    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Ratings Methodology</title>
    <style>
        :root {{
            --bg: #f6f1e8;
            --panel: rgba(255, 252, 247, 0.95);
            --panel-strong: #fffaf1;
            --text: #1f2933;
            --muted: #5e6b78;
            --line: rgba(31, 41, 51, 0.12);
            --accent: #0d5c63;
            --shadow: 0 18px 50px rgba(29, 43, 57, 0.12);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: Georgia, "Times New Roman", serif;
            color: var(--text);
            background:
                radial-gradient(circle at top left, rgba(13, 92, 99, 0.18), transparent 28%),
                radial-gradient(circle at top right, rgba(184, 92, 56, 0.16), transparent 24%),
                linear-gradient(180deg, #fbf7ef 0%, var(--bg) 55%, #efe4d3 100%);
            min-height: 100vh;
        }}
        .page {{
            width: min(1160px, calc(100vw - 30px));
            margin: 0 auto;
            padding: 24px 0 36px;
        }}
        .hero, .section {{
            border: 1px solid var(--line);
            border-radius: 22px;
            background: var(--panel);
            box-shadow: var(--shadow);
        }}
        .hero {{ padding: 22px 24px; }}
        .eyebrow {{
            font-family: "Trebuchet MS", sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.14em;
            color: var(--accent);
            font-size: 0.72rem;
        }}
        h1 {{
            margin: 8px 0 10px;
            font-size: clamp(1.9rem, 4vw, 3rem);
            line-height: 0.95;
            letter-spacing: -0.03em;
        }}
        .lead {{
            margin: 0;
            color: var(--muted);
            line-height: 1.55;
            max-width: 80ch;
        }}
        .back-link {{
            margin-top: 14px;
            display: inline-block;
            text-decoration: none;
            color: white;
            background: linear-gradient(90deg, var(--accent), #1b7f88);
            border-radius: 999px;
            padding: 8px 14px;
            font-family: "Trebuchet MS", sans-serif;
            font-size: 0.72rem;
            text-transform: uppercase;
            letter-spacing: 0.08em;
        }}
        .section {{
            margin-top: 18px;
            padding: 20px;
        }}
        h2 {{
            margin: 0 0 8px;
            font-size: 1.35rem;
        }}
        h3 {{
            margin: 14px 0 6px;
            font-size: 1rem;
        }}
        p, li {{
            color: var(--muted);
            line-height: 1.5;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 12px;
        }}
        .card {{
            background: var(--panel-strong);
            border: 1px solid var(--line);
            border-radius: 14px;
            padding: 12px;
        }}
        .formula {{
            font-family: "Consolas", "Courier New", monospace;
            font-size: 0.83rem;
            color: #213746;
            background: #eef4f4;
            border: 1px solid #d6e6e6;
            border-radius: 10px;
            padding: 8px 10px;
            display: block;
            white-space: pre-wrap;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 8px;
        }}
        th, td {{
            padding: 10px 8px;
            border-bottom: 1px solid var(--line);
            text-align: left;
            vertical-align: top;
        }}
        th {{
            font-family: "Trebuchet MS", sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            font-size: 0.7rem;
            color: var(--muted);
        }}
        .weights {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 10px;
        }}
        .pill {{
            border: 1px solid var(--line);
            border-radius: 999px;
            padding: 7px 10px;
            background: var(--panel-strong);
            font-size: 0.88rem;
        }}
        @media (max-width: 900px) {{
            .grid {{ grid-template-columns: 1fr; }}
        }}
    </style>
</head>
<body>
    <main class="page">
        <section class="hero">
            <div class="eyebrow">Administration</div>
            <h1>Stock Rating Methodology</h1>
            <p class="lead">This page documents how the v6 ratings are calculated in production from the current code path: which features are derived, which data sources feed each feature family, the factor formulas, the weighted composite, and the cross-sectional percentile grading that assigns the final A-F grade relative to the tracked universe.</p>
            <a class="back-link" href="ratings-dashboard.html">Back To Dashboard</a>
        </section>

        <section class="section">
            <h2>Rating Scale</h2>
            <p>Grades are assigned by <strong>percentile rank against the tracked universe</strong> (AAII A+ style) in even 20% buckets, not by absolute score thresholds. The displayed score is the composite percentile rescaled to 0-100, so roughly 20% of symbols fall in each band. Grades are relative: a symbol can change grade as the universe changes even if its own fundamentals do not.</p>
            <table>
                <thead>
                    <tr><th>Score (composite percentile)</th><th>Label</th></tr>
                </thead>
                <tbody>
                    <tr><td>80-100 (top 20%)</td><td>A / Very Attractive</td></tr>
                    <tr><td>60-79</td><td>B / Attractive</td></tr>
                    <tr><td>40-59</td><td>C / Neutral</td></tr>
                    <tr><td>20-39</td><td>D / Unattractive</td></tr>
                    <tr><td>0-19 (bottom 20%)</td><td>F / Very Unattractive</td></tr>
                </tbody>
            </table>
        </section>

        <section class="section">
            <h2>Source To Feature Mapping</h2>
            <table>
                <thead>
                    <tr><th>Feature Family</th><th>Features</th><th>Primary Sources</th><th>Where In Code</th></tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Price / Technical</td>
                        <td>intraday_return, one_day_return, five_day_return, ten_day_return, twenty_day_return, sixty_day_return, one_hundred_day_return, daily_volume, average_volume_20d, twenty_day_volatility, twenty_day_max_drawdown, high_low_range_pct, gap_open_return</td>
                        <td>Alpha Vantage, Twelve Data, Stooq (fallback order in daily pipeline)</td>
                        <td>ingest/prices.py + transform/features.py</td>
                    </tr>
                    <tr>
                        <td>Fundamental</td>
                        <td>net_margin, cash_flow_margin, return_on_assets, debt_to_assets, earnings_yield, book_to_price, revenue_growth_yoy, net_income_growth_yoy, operating_cash_flow_growth_yoy</td>
                        <td>SEC EDGAR company facts</td>
                        <td>ingest/sec_companyfacts.py + transform/fundamentals.py</td>
                    </tr>
                    <tr>
                        <td>Analyst Consensus</td>
                        <td>analyst_target_price, analyst strong_buy/buy/hold/sell/strong_sell counts, suggestion_label</td>
                        <td>Alpha Vantage OVERVIEW</td>
                        <td>ingest/analyst.py + analyst_consensus_daily</td>
                    </tr>
                    <tr>
                        <td>Analyst Revision (composite factor)</td>
                        <td>analyst_revision_score, analyst_suggestion_score_delta, analyst_target_price_change_pct</td>
                        <td>analyst_consensus_daily history (Alpha Vantage + Finnhub)</td>
                        <td>repository/analyst.py + transform/analyst_features.py</td>
                    </tr>
                    <tr>
                        <td>Benchmark scores (diagnostic, NOT in composite)</td>
                        <td>piotroski_fscore, magic_formula_roic / earnings_yield / combined_rank, acquirers_multiple</td>
                        <td>SEC EDGAR company facts</td>
                        <td>transform/benchmark_scores.py + rating/magic_formula.py</td>
                    </tr>
                    <tr>
                        <td>Macro</td>
                        <td>yield_curve_slope</td>
                        <td>FRED series DGS10 and DGS2</td>
                        <td>ingest/fred_macro.py + transform/macro.py</td>
                    </tr>
                </tbody>
            </table>

            <h3>Latest Source Call Summary</h3>
            <table>
                <thead>
                    <tr><th>Source</th><th>Calls</th><th>Succeeded</th><th>Failed</th><th>Status</th></tr>
                </thead>
                <tbody>
                    {source_rows}
                </tbody>
            </table>
        </section>

        <section class="section">
            <h2>Factor Calculations</h2>
            <div class="grid">
                <article class="card">
                    <h3>Valuation</h3>
                    <span class="formula">liquidity_score = clamp(25 + daily_volume / 200000)
reversal_score = clamp(55 - one_day_return*250 - intraday_return*100)
valuation = average(reversal/liquidity baseline, earnings_yield, book_to_price, profitability, cash flow, leverage)</span>
                    <p>If SEC valuation inputs are missing, valuation falls back to the conservative reversal/liquidity baseline.</p>
                </article>

                <article class="card">
                    <h3>Quality</h3>
                    <span class="formula">quality_baseline = clamp(38 + liquidity*0.45 - abs(intraday_return - one_day_return)*350)</span>
                    <p>With fundamentals, quality averages the baseline with net margin, cash-flow margin, return on assets, and leverage.</p>
                </article>

                <article class="card">
                    <h3>Growth</h3>
                    <span class="formula">short_term_trend = clamp(50 + one_day_return*400 + intraday_return*150)
growth = average(short_term_trend, revenue_growth_yoy, net_income_growth_yoy, operating_cash_flow_growth_yoy, medium_term_momentum)</span>
                    <p>Macro yield-curve slope can raise or lower the growth component.</p>
                    <span class="formula">growth = clamp(growth*0.75 + macro_growth*0.25)</span>
                </article>

                <article class="card">
                    <h3>Momentum</h3>
                    <span class="formula">momentum_score = clamp(50 + best_available_100/60/20/10/5/1_day_return*120 + twenty_day_return*60 + liquidity*0.05)</span>
                    <p>Momentum prefers longer available lookbacks and falls back to short-term returns when compact provider data is all that exists.</p>
                </article>

                <article class="card">
                    <h3>Risk</h3>
                    <span class="formula">risk_penalty = abs(one_day_return)*250 + abs(intraday_return)*150 + volatility*450 + max_drawdown*250
risk = average(price_stability, leverage, cash_generation, profitability)</span>
                    <p>With macro data, risk adds yield-curve context.</p>
                    <span class="formula">risk = clamp(risk*0.75 + macro_risk*0.25)</span>
                </article>
            </div>
        </section>

        <section class="section">
            <h2>Final Composite Score</h2>
            <p>Scoring runs in two passes. <strong>Pass 1 (per symbol)</strong> computes the weighted composite of the six factor scores. <strong>Pass 2 (cross-sectional)</strong> ranks every symbol's composite against the whole universe and assigns the A-F grade from its percentile; the displayed score is that percentile rescaled to 0-100.</p>
            <span class="formula">composite = valuation*0.225 + quality*0.225 + growth*0.18 + momentum*0.18 + risk*0.09 + analyst_revision*0.10
score = round(percentile_rank(composite, universe) * 100)</span>
            <div class="weights">
                <div class="pill">Valuation: 22.5%</div>
                <div class="pill">Quality: 22.5%</div>
                <div class="pill">Growth: 18%</div>
                <div class="pill">Momentum: 18%</div>
                <div class="pill">Risk: 9%</div>
                <div class="pill">Analyst Revision: 10%</div>
            </div>
        </section>

        <section class="section">
            <h2>Benchmark Scores</h2>
            <p>Three externally-validated, fully-specified value/quality scores are shown alongside the composite as <strong>benchmarks</strong>. They are deliberately <strong>excluded from the weighted composite</strong> so they stay directly comparable to their published backtests, and they are diagnostic rather than standalone buy signals.</p>
            <div class="grid">
                <article class="card">
                    <h3>Piotroski F-Score</h3>
                    <span class="formula">0-9: nine binary profitability / leverage / efficiency signals</span>
                    <p>Shown as N/9. Full-confidence values use all nine signals and are highlighted; partial values are muted and explain their lower confidence in the tooltip. Designed as a second-stage filter on already-cheap stocks.</p>
                </article>
                <article class="card">
                    <h3>Magic Formula (Greenblatt)</h3>
                    <span class="formula">rank(ROIC) + rank(EBIT / enterprise value); lowest sum = best (rank 1)</span>
                    <p>Cross-sectional combined rank across the universe; financials and utilities excluded when sector is known.</p>
                </article>
                <article class="card">
                    <h3>Acquirer's Multiple (Carlisle)</h3>
                    <span class="formula">enterprise value / EBIT</span>
                    <p>Deep-value lens; lower is cheaper. EBIT is approximated by us-gaap OperatingIncomeLoss.</p>
                </article>
            </div>
        </section>
    </main>
</body>
</html>
"""


if __name__ == "__main__":
    main()
