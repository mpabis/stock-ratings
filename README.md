# Stock Rating

Hosted daily stock ratings pipeline scaffold for free-only market data sources.

## Quick start

1. Install dependencies: `python -m pip install -e . pytest`
2. Configure `.env` with your `DATABASE_URL` and market data API keys.
3. Bootstrap the tracked symbols into the database: `python -m stock_rating.pipeline.bootstrap_symbols`
4. Run the daily pipeline: `python -m stock_rating.daily`
5. Generate the latest summary and dashboard: `python -m stock_rating.pipeline.report`

## Commands

- Install dependencies: `python -m pip install -e . pytest`
- Run tests: `python -m pytest`
- Run daily pipeline: `python -m stock_rating.daily`
- Run daily pipeline with the original module path: `python -m stock_rating.pipeline.daily`
- Run read-only API: `uvicorn stock_rating.api.app:app --host 0.0.0.0 --port 8000`
- Backfill historical daily prices with Alpha Vantage full-history fetches: `python -m stock_rating.pipeline.backfill`
- Bootstrap symbols into Postgres: `python -m stock_rating.pipeline.bootstrap_symbols`
- Bootstrap SEC fundamentals into `fundamental_facts`: `python -m stock_rating.pipeline.bootstrap_fundamentals`
- Check database connection safely: `python -m stock_rating.pipeline.check_db`
- Report latest database state and generate HTML dashboard: `python -m stock_rating.pipeline.report`

Optional provider note:
- `STOOQ_API_KEY` can be configured to enable Stooq as a third fallback provider for symbols that Alpha Vantage or Twelve Data do not cover on your current plan.
- `SEC_USER_AGENT` should identify you when calling SEC EDGAR endpoints. SEC expects a descriptive user agent with contact information.
	Example: `stock-rating/0.1 your-email@example.com`
- When `DATABASE_URL` and `SEC_USER_AGENT` are configured, the daily pipeline automatically refreshes SEC fundamentals for SEC-covered symbols before scoring.
- When `DATABASE_URL` and `FRED_API_KEY` are configured, the daily pipeline also refreshes core FRED macro series and uses the yield-curve slope in scoring.

## How to update tracked stocks

The tracked universe lives in [data/symbols.csv](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/data/symbols.csv).

Each row controls:
- `symbol`
- `company_name`
- `exchange`
- `refresh_tier`
- `last_price_date`
- `active`

Example row:

```csv
MU,Micron Technology Inc.,NASDAQ,2,2026-05-23,true
```

To add, remove, or change symbols:

1. Edit [data/symbols.csv](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/data/symbols.csv).
2. Run `python -m stock_rating.pipeline.bootstrap_symbols` to upsert the updated rows into Postgres.
3. Run `python -m stock_rating.daily` to fetch fresh data and compute ratings for the current universe.

Tier guidance:
- `1` for highest-priority names you want refreshed most aggressively.
- `2` for important names that can tolerate a few days of staleness.
- `3` for lower-priority names that can roll over multiple days.

## How to update the data

The system persists data in stages:
- `symbols` stores the tracked universe.
- `price_daily` stores fetched daily price bars.
- `fundamental_facts` stores the latest SEC-derived core facts per metric and filing period.
- `macro_series_daily` stores persisted FRED macro observations.
- `features_daily` stores derived factors.
- `ratings_daily` stores the final published ratings.

Typical refresh workflow:

1. Verify DB connectivity with `python -m stock_rating.pipeline.check_db`.
2. If you changed [data/symbols.csv](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/data/symbols.csv), run `python -m stock_rating.pipeline.bootstrap_symbols`.
3. Run `python -m stock_rating.daily`.
4. Run `python -m stock_rating.pipeline.report` to refresh the presentation layer.

Manual fundamentals bootstrap is still available with `python -m stock_rating.pipeline.bootstrap_fundamentals` if you want to backfill SEC facts separately from the daily price run.
Historical price backfills are available with `python -m stock_rating.pipeline.backfill`.

## How to see the data

There are two main ways to inspect results.

Console summary:
- Run `python -m stock_rating.pipeline.report`
- This prints the latest run metadata and row counts for the main tables.

HTML dashboard:
- Open [artifacts/reports/ratings-dashboard.html](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/artifacts/reports/ratings-dashboard.html)
- This dashboard shows the latest ratings snapshot, score breakdowns, freshness state, and active data quality alerts for stale prices or missing ratings.

Read-only API (FastAPI):
- Start: `uvicorn stock_rating.api.app:app --host 0.0.0.0 --port 8000`
- Endpoints:
	- `GET /healthz`
	- `GET /api/summary`
	- `GET /api/ratings?limit=100`
	- `GET /api/quality-alerts?limit=100`

Planner artifacts:
- Open files in [artifacts/plans](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/artifacts/plans)
- These contain the saved refresh plan output for each pipeline run.

## Database

Use Supabase Postgres for the hosted MVP.

- Setup guide: [docs/supabase_setup.md](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/docs/supabase_setup.md)
- Schema file: [sql/schema.sql](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/sql/schema.sql)
- Migration file: [sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql)

## Current Scope

- Daily end-of-day ratings
- Free-only data source strategy
- Rolling multi-day refresh support for larger universes
- Deterministic tier-based refresh planning
- Seed-backed symbol universe loaded from `data/symbols.csv`
- Planner run artifacts written to `artifacts/plans/`
- Pipeline run metadata persisted to Postgres when `DATABASE_URL` is configured
- Successful refreshes can persist derived price features into `features_daily`
- Successful refreshes can persist ratings into `ratings_daily`
- The report command writes a styled dashboard to `artifacts/reports/ratings-dashboard.html`
