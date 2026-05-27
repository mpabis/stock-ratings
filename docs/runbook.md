# Runbook

## Local development

1. Install dependencies with `python -m pip install -e . pytest`.
2. Run tests with `python -m pytest`.
3. Run the daily planner demo with `python -m stock_rating.pipeline.daily`.
4. Bootstrap symbols with `python -m stock_rating.pipeline.bootstrap_symbols`.
5. Follow [docs/supabase_setup.md](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/docs/supabase_setup.md) to create and initialize the hosted database.
6. Verify connectivity with `python -m stock_rating.pipeline.check_db`.
7. Summarize current DB state and generate the ratings dashboard with `python -m stock_rating.pipeline.report`.

## Operations

- Monitor price provider limits.
- Prioritize Tier 1 symbols first.
- Allow Tier 2 and Tier 3 symbols to roll across multiple trading days.
- Surface stale ratings to downstream consumers.
- Update `data/symbols.csv` to change the tracked symbol universe or refresh tiers.
- Inspect `artifacts/plans/` for the latest persisted planner run output.
- Inspect `artifacts/reports/ratings-dashboard.html` for the latest presentation-friendly ratings view.
- Configure `DATABASE_URL` to persist `pipeline_runs` and `symbol_refresh_runs` into Postgres.
- Apply migrations before running the bootstrap or daily pipeline against a fresh database.

## Hosted database

- Preferred provider: Supabase free-tier Postgres
- Use the direct Postgres connection string as `DATABASE_URL`
- Run [sql/schema.sql](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/sql/schema.sql) first, then [sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql](c:/Users/MartinPabiš/source/repos/playground/stock-ratings/sql/migrations/001_add_symbol_refresh_run_outcome_fields.sql)
- Then run `python -m stock_rating.pipeline.bootstrap_symbols`