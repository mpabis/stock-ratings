# Agent Instructions

## Project

- Python package using a `src/` layout; requires Python 3.12 or newer.
- Keep ingestion, transformation, persistence, quality checks, and scoring separate.
- Main package areas:
  - `stock_rating.ingest`: external data fetch and parsing.
  - `stock_rating.transform`: derived price, fundamental, and macro features.
  - `stock_rating.quality`: data quality checks on ingested and derived inputs.
  - `stock_rating.rating`: explainable scoring and rating records.
  - `stock_rating.repository`: database reads/writes.
  - `stock_rating.pipeline`: executable pipeline entry points.
  - `stock_rating.api`: read-only FastAPI dashboard API.

## Commands

- Install editable dev dependencies: `python -m pip install -e . pytest`
- Install CI/runtime dependencies: `python -m pip install -r requirements.txt`
- Run tests: `python -m pytest`
- Run daily pipeline: `python -m stock_rating.daily`
- Run daily pipeline with original module path: `python -m stock_rating.pipeline.daily`
- Run weekend slow-input refresh without normal price-provider calls: `python -m stock_rating.pipeline.weekend`
- Run read-only API locally: `uvicorn stock_rating.api.app:app --host 0.0.0.0 --port 8000`
- Bootstrap symbols: `python -m stock_rating.pipeline.bootstrap_symbols`
- Apply pending SQL migrations: `python -m stock_rating.pipeline.migrate`
- Bootstrap SEC fundamentals: `python -m stock_rating.pipeline.bootstrap_fundamentals`
- Backfill historical daily prices: `python -m stock_rating.pipeline.backfill`
- Check database connection: `python -m stock_rating.pipeline.check_db`
- Report latest database state and generate dashboard artifacts: `python -m stock_rating.pipeline.report`

## Configuration

- Copy `.env.example` locally and configure `.env`; never commit real secrets.
- Important environment variables include `DATABASE_URL`, `ALPHA_VANTAGE_API_KEY`, `ALPHA_VANTAGE_MAX_REQUESTS_PER_RUN`, `ALPHA_VANTAGE_MIN_INTERVAL_SECONDS`, `TWELVE_DATA_API_KEY`, `TWELVE_DATA_MAX_REQUESTS_PER_RUN`, `STOOQ_API_KEY`, `SEC_USER_AGENT`, `FRED_API_KEY`, `STOCK_RATING_SYMBOL_LIMIT`, `STOCK_RATING_FUNDAMENTAL_SYMBOL_LIMIT`, `STOCK_RATING_ANALYST_SYMBOL_LIMIT`, `STOCK_RATING_SYMBOL_SEED_PATH`, and `STOCK_RATING_PLAN_OUTPUT_DIR`.
- `SEC_USER_AGENT` should identify the app and include contact information when using SEC EDGAR.
- `DATABASE_URL` should be the direct Supabase Postgres connection string for hosted runs.

## Data And Outputs

- Tracked symbols live in `data/symbols.csv`; update it before running `python -m stock_rating.pipeline.bootstrap_symbols`.
- SQL schema lives in `sql/schema.sql`; migrations live in `sql/migrations/`.
- Planner artifacts are written under `artifacts/plans/`.
- Report output is written under `artifacts/reports/`, including HTML for humans plus Markdown/JSON companions for AI agents.

## Rules

- Do not commit API keys, `.env` secrets, database credentials, or provider tokens.
- Do not change database schema without updating `sql/schema.sql` and adding a migration file.
- Record notable user-facing, operational, schema, scoring, pipeline, and documentation changes in `CHANGELOG.md`.
- Prefer deterministic, explainable scoring before introducing black-box models.
- Store model versions, provider/source names, and source timestamps for persisted data.
- Keep free-provider rate limits in mind; preserve tier-based refresh planning and fallback provider behavior.
- Keep optional analyst refreshes separately budgeted so they do not consume the price refresh quota by default.
- When changing pipeline behavior, add or update focused tests under `tests/`.
