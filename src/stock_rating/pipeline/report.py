from collections import Counter
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from html import escape
from pathlib import Path
from typing import Any

from stock_rating.config import get_settings
from stock_rating.db import connect_postgres
from stock_rating.quality.checks import QualityAlert, SymbolQualitySnapshot, build_quality_alerts


TABLE_NAMES = ["symbols", "pipeline_runs", "symbol_refresh_runs", "price_daily", "features_daily", "ratings_daily"]


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
        source_refresh_summaries = fetch_source_refresh_summaries(latest_run[0] if latest_run else None)
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
                cursor.execute(f"select count(*) from {table_name}")
                counts[table_name] = cursor.fetchone()[0]
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


def fetch_source_refresh_summaries(run_id: str | None) -> list[SourceRefreshSummary]:
    if not run_id:
        return []

    artifact_path = Path("artifacts") / "plans" / f"{run_id}.json"
    if not artifact_path.exists():
        return []

    try:
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    summaries = payload.get("source_refresh_summaries", [])
    if not isinstance(summaries, list):
        return []

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
    return results


def fetch_latest_ratings(cursor: Any) -> list[RatingSnapshot]:
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
                        explanation_json
                from ranked_ratings
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
            '<tr><td colspan="8" class="empty">No ratings found in ratings_daily.</td></tr>'
        )

        run_summary = render_run_summary(latest_run, latest_run_counts)
        source_metrics_html = "".join(render_source_metric(summary) for summary in source_refresh_summaries) or (
            '<div class="run-metric"><div class="label">No source summary</div><div class="value">N/A</div><div class="meta">Run artifact not found.</div></div>'
        )

        return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Ratings Dashboard</title>
    <style>
        :root {{
            --bg: #f6f1e8;
            --panel: rgba(255, 252, 247, 0.9);
            --panel-strong: #fffaf1;
            --text: #1f2933;
            --muted: #5e6b78;
            --line: rgba(31, 41, 51, 0.12);
            --accent: #0d5c63;
            --accent-soft: #d7ecee;
            --warm: #b85c38;
            --good: #2d6a4f;
            --warn: #a2670a;
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
            width: min(1180px, calc(100vw - 32px));
            margin: 0 auto;
            padding: 32px 0 40px;
        }}
        .hero {{
            background: linear-gradient(135deg, rgba(255, 250, 241, 0.95), rgba(240, 233, 218, 0.95));
            border: 1px solid rgba(13, 92, 99, 0.12);
            border-radius: 28px;
            box-shadow: var(--shadow);
            overflow: hidden;
            position: relative;
        }}
        .hero::after {{
            content: "";
            position: absolute;
            inset: auto -80px -100px auto;
            width: 280px;
            height: 280px;
            border-radius: 50%;
            background: radial-gradient(circle, rgba(13, 92, 99, 0.2), transparent 68%);
        }}
        .hero-inner {{
            display: grid;
            grid-template-columns: minmax(0, 1.5fr) minmax(320px, 0.95fr);
            gap: 24px;
            padding: 24px 28px;
            align-items: start;
        }}
        h1 {{
            font-size: clamp(2.4rem, 5vw, 4.5rem);
            line-height: 0.95;
            margin: 0 0 14px;
            letter-spacing: -0.05em;
        }}
        .eyebrow {{
            font-family: "Trebuchet MS", sans-serif;
            text-transform: uppercase;
            letter-spacing: 0.18em;
            font-size: 0.76rem;
            color: var(--accent);
            margin-bottom: 12px;
        }}
        .lead {{
            font-size: 1rem;
            line-height: 1.6;
            color: var(--muted);
            max-width: 48ch;
            margin: 0;
        }}
        .hero-panel {{
            background: rgba(13, 92, 99, 0.06);
            border: 1px solid rgba(13, 92, 99, 0.12);
            border-radius: 22px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 12px;
        }}
        .hero-panel .kicker {{
            font-family: "Trebuchet MS", sans-serif;
            font-size: 0.78rem;
            letter-spacing: 0.14em;
            text-transform: uppercase;
            color: var(--accent);
        }}
        .hero-panel .value {{
            font-size: 1.9rem;
            font-weight: 700;
        }}
        .hero-panel .meta {{
            color: var(--muted);
            line-height: 1.5;
            font-size: 0.95rem;
        }}
        .run-status-chip {{
            display: inline-flex;
            align-items: center;
            width: fit-content;
            padding: 6px 10px;
            border-radius: 999px;
            font-family: "Trebuchet MS", sans-serif;
            font-size: 0.75rem;
            letter-spacing: 0.1em;
            text-transform: uppercase;
            border: 1px solid rgba(31, 41, 51, 0.08);
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
            gap: 12px;
            margin-top: 4px;
        }}
        .source-grid {{
            margin-top: 4px;
        }}
        .source-grid .value {{
            font-size: 1.2rem;
        }}
        .source-grid .meta {{
            color: var(--muted);
            font-size: 0.86rem;
            line-height: 1.35;
        }}
        .run-metric {{
            padding: 10px 12px;
            border-radius: 16px;
            background: rgba(255, 255, 255, 0.45);
            border: 1px solid rgba(13, 92, 99, 0.08);
        }}
        .run-metric .label {{
            color: var(--muted);
            font-family: "Trebuchet MS", sans-serif;
            font-size: 0.72rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}
        .run-metric .value {{
            margin-top: 6px;
            font-size: 1rem;
            line-height: 1.35;
            word-break: break-word;
        }}
        .section {{
            margin-top: 24px;
            background: var(--panel);
            border: 1px solid var(--line);
            border-radius: 24px;
            box-shadow: var(--shadow);
            padding: 24px;
            backdrop-filter: blur(10px);
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
            border-radius: 20px;
            padding: 18px;
        }}
        .card .label {{
            color: var(--muted);
            font-family: "Trebuchet MS", sans-serif;
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
            padding: 10px 14px;
            font-size: 0.95rem;
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
            border-radius: 18px;
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
        table {{
            width: 100%;
            border-collapse: collapse;
            overflow: hidden;
            border-radius: 18px;
        }}
        th, td {{
            text-align: left;
            padding: 14px 12px;
            border-bottom: 1px solid var(--line);
            vertical-align: top;
        }}
        th {{
            font-family: "Trebuchet MS", sans-serif;
            font-size: 0.78rem;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--muted);
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
            background: rgba(13, 92, 99, 0.04);
        }}
        .symbol {{
            font-weight: 700;
            font-size: 1rem;
        }}
        .company {{
            color: var(--muted);
            font-size: 0.85rem;
            margin-top: 2px;
            line-height: 1.25;
        }}
        .score-chip {{
            display: inline-flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            min-width: 72px;
            gap: 3px;
            padding: 10px 12px;
            border-radius: 16px;
            border: 1px solid rgba(31, 41, 51, 0.08);
            color: #183042;
            font-weight: 700;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.5);
        }}
        .score-chip small {{
            font-family: "Trebuchet MS", sans-serif;
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
            background: linear-gradient(180deg, #f7d7cd, #f3b9a8);
        }}
        .score-band-mid {{
            background: linear-gradient(180deg, #f7e9c6, #efd48a);
        }}
        .score-band-high {{
            background: linear-gradient(180deg, #d8edd4, #acd8a1);
        }}
        .freshness-fresh {{ color: var(--good); }}
        .freshness-aging {{ color: var(--warn); }}
        .freshness-stale {{ color: var(--warm); }}
        .factor-cell {{
            min-width: 82px;
        }}
        .factor-chip {{
            padding: 8px 8px 9px;
            border-radius: 14px;
            border: 1px solid rgba(13, 92, 99, 0.08);
            background: linear-gradient(180deg, rgba(255, 255, 255, 0.8), rgba(242, 238, 230, 0.75));
        }}
        .factor-head {{
            display: block;
            margin-bottom: 5px;
        }}
        .factor-chip span {{
            display: block;
            color: var(--muted);
            font-family: "Trebuchet MS", sans-serif;
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
            background: linear-gradient(90deg, #db6f51, #e79870);
        }}
        .factor-fill-mid {{
            background: linear-gradient(90deg, #d5a63f, #edd073);
        }}
        .factor-fill-high {{
            background: linear-gradient(90deg, #3e8a63, #78bc86);
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
            .factor-cell {{ min-width: 72px; }}
        }}
        @media (max-width: 720px) {{
            .page {{ width: min(100vw - 20px, 1180px); padding-top: 20px; }}
            .hero-inner, .section {{ padding: 18px; }}
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
                    <h1>Ratings worth presenting.</h1>
                    <p class="lead">Latest ratings, freshness state, and score breakdowns generated directly from the pipeline's persisted outputs.</p>
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
                        <div class="run-metric">
                            <div class="label">Run ID</div>
                            <div class="value">{escape(run_summary['run_id'])}</div>
                        </div>
                        <div class="run-metric">
                            <div class="label">Started</div>
                            <div class="value">{escape(run_summary['started_at'])}</div>
                        </div>
                    </div>
                    <div class="kicker">Source calls</div>
                    <div class="run-grid source-grid">{source_metrics_html}</div>
                </aside>
            </div>
        </section>

        <section class="section ratings-section">
            <div class="section-title">
                <h2>Latest ratings</h2>
                <p>Sorted by score descending so the strongest names surface first.</p>
            </div>
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
                    </tr>
                </thead>
                                <tbody id="ratings-table-body">
                    {rows_html}
                </tbody>
            </table>
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


def render_stat_card(label: str, value: str, caption: str) -> str:
        return (
                '<article class="card">'
                f'<div class="label">{escape(label)}</div>'
                f'<div class="number">{escape(value)}</div>'
                f'<div class="caption">{escape(caption)}</div>'
                '</article>'
        )


def render_source_metric(summary: SourceRefreshSummary) -> str:
    return (
        '<div class="run-metric">'
        f'<div class="label">{escape(format_source_name(summary.source))}</div>'
        f'<div class="value">{summary.calls} calls</div>'
        f'<div class="meta">{escape(format_source_status(summary))}</div>'
        '</div>'
    )


def render_rating_row(rating: RatingSnapshot) -> str:
    freshness_class = f"freshness-{escape(rating.freshness_status)}"
    rating_ten = format_score_ten(Decimal(rating.rating_score))
    score_band = score_band_class(Decimal(rating.rating_score))
    company_sort = f"{rating.symbol} {rating.company_name}".lower()
    factor_cells_html = "".join(
        [
            render_factor_cell("Valuation", rating.valuation_score),
            render_factor_cell("Quality", rating.quality_score),
            render_factor_cell("Growth", rating.growth_score),
            render_factor_cell("Momentum", rating.momentum_score),
            render_factor_cell("Risk", rating.risk_score),
        ]
    )
    return (
        "<tr>"
        f'<td data-sort="{escape(company_sort)}"><div class="symbol">{escape(rating.symbol)}</div><div class="company">{escape(rating.company_name)}</div></td>'
        f'<td data-sort="{rating.rating_score}"><span class="score-chip {score_band}"><small>Rating</small><strong>{escape(rating_ten)}</strong></span></td>'
        f'<td data-sort="{escape(rating.freshness_status)}" class="{freshness_class}">{escape(rating.freshness_status.title())}</td>'
        f'{factor_cells_html}'
        "</tr>"
    )


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
    return value.isoformat(sep=" ", timespec="seconds")


def format_date(value: date | None) -> str:
    if value is None:
        return "N/A"
    return value.isoformat()


def format_decimal(value: Decimal | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:.1f}"


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


def render_factor_cell(name: str, value: Decimal | None) -> str:
    score_text = format_score_ten(value)
    width = factor_width(value)
    fill_class = factor_fill_class(value)
    sort_value = factor_sort_value(value)
    short_name = factor_short_name(name)
    return (
        f'<td class="factor-cell" data-sort="{sort_value}">'
        '<div class="factor-chip">'
        f'<div class="factor-head"><span>{escape(short_name)}</span><strong>{escape(score_text)}</strong></div>'
        f'<div class="factor-track"><div class="factor-fill {fill_class}" style="width: {width}%"></div></div>'
        '</div>'
        '</td>'
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
    }
    return labels.get(name, name)


if __name__ == "__main__":
    main()