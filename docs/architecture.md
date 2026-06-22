# Architecture

The system is a Python `src`-layout application that ingests free market data,
derives features, computes a transparent stock rating, and publishes results to
Postgres, an HTML dashboard, and a small API. GitHub Actions schedules the daily
and weekend runs; Postgres (Supabase free tier) persists state.

The most important operational rule is that free API limits are handled by
deterministic **tier-based refresh planning** rather than forcing a full
same-day refresh of every symbol — Tier 1 first, Tier 2/3 rolling across days.

## Layers (`src/stock_rating/`)

| Layer | Package | Responsibility |
|---|---|---|
| Ingest | `ingest/` | Fetch + parse raw data: prices (Alpha Vantage → Twelve Data → Stooq fallback), SEC EDGAR company facts, FRED macro, analyst consensus (Alpha Vantage OVERVIEW + Finnhub) |
| Transform | `transform/` | Derive features from raw data: `features.py` (price/technical), `fundamentals.py` (SEC ratios + the shared `annual_values_by_metric` helper), `benchmark_scores.py` (Piotroski / Magic Formula / Acquirer's Multiple), `analyst_features.py` (estimate-revision factor), `macro.py` |
| Rating | `rating/` | `model_v1.py` (per-symbol composite, `MODEL_VERSION`), `scoring.py` (label vocabulary), `percentile_ranking.py` (cross-sectional grading + canonical `COMPOSITE_WEIGHTS`), `universe_grading.py` (pass-2 orchestration), `magic_formula.py` (combined-rank pass), `explanations.py` |
| Repository | `repository/` | DB reads/writes per table (`ratings`, `analyst`, `fundamentals`, `prices`, `macro`, `symbols`, `runs`). Defensive `try/except → default` style; all DB access is injectable for tests |
| Pipeline | `pipeline/` | Orchestration: `daily.py` (`run_pipeline`), `weekend.py`, `migrate.py`, `report.py` (dashboard + methodology HTML/Markdown/JSON artifacts), bootstrap + backfill utilities |
| API | `api/app.py` | FastAPI; `/api/ratings` serves the latest ratings |

Data flows one direction: **ingest → transform → rating → repository → (report / api)**.

## The rating model (v6)

Scoring is a **two-pass** process:

1. **Per-symbol pass** (`model_v1.compute_rating_breakdown`) — assembles a flat
   list of `FeatureValue`s (price + fundamental + benchmark + analyst-revision +
   macro, merged in `pipeline.daily.build_symbol_features`) and produces six
   absolute 0-100 factor sub-scores plus a weighted composite. Weights
   (`percentile_ranking.COMPOSITE_WEIGHTS`, the single source of truth):
   valuation 22.5%, quality 22.5%, growth 18%, momentum 18%, risk 9%,
   analyst-revision 10%.
2. **Universe pass** (`rating.universe_grading.apply_universe_percentile_grades`)
   — runs once after all symbols are rated. It ranks every active symbol's
   sub-scores against the whole universe (AAII A+ style), assigns a percentile
   and A-F grade per factor and for the composite, and writes them back onto the
   `ratings_daily` rows. The displayed `rating_score` is the composite percentile
   rescaled to 0-100, so grades are **relative to the tracked universe**.

A separate cross-sectional step (`rating.magic_formula.apply_magic_formula_ranks`)
computes the Greenblatt combined rank. The **benchmark scores** (Piotroski,
Magic Formula, Acquirer's Multiple) are persisted to `features_daily` and shown
alongside the composite but are **excluded from it**, so they stay comparable to
their published backtests.

See [rating_methodology.md](rating_methodology.md) for the factor formulas and
weights, and [data_sources.md](data_sources.md) for provider details.

## Persistence

Postgres schema in `sql/schema.sql`, evolved by idempotent migrations in
`sql/migrations/` (applied by `pipeline.migrate`, which runs the base schema
first then pending files). Key tables: `symbols`, `price_daily`,
`fundamental_facts`, `analyst_consensus_daily`, `macro_series_daily`,
`features_daily` (name/value feature rows), and `ratings_daily` (one row per
`symbol, date, model_version` with sub-scores, percentiles, and grades).

## Scheduling & outputs

GitHub Actions runs the daily/weekend pipelines. Each run writes `pipeline_runs`
+ `symbol_refresh_runs`, refreshes per the tier plan, recomputes ratings, runs
the two universe passes, and `pipeline.report` regenerates the report artifacts
under `artifacts/reports/`: HTML for humans, Markdown for agent-readable
narrative review, and JSON for deterministic parsing.
