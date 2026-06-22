# Runbook

## Local development

1. Install dependencies with `python -m pip install -e . pytest`.
2. Run tests with `python -m pytest`.
3. Run the daily planner demo with `python -m stock_rating.pipeline.daily`.
4. Bootstrap symbols with `python -m stock_rating.pipeline.bootstrap_symbols`.
5. Follow [docs/supabase_setup.md](supabase_setup.md) to create and initialize the hosted database.
6. Apply pending migrations with `python -m stock_rating.pipeline.migrate`.
7. Verify connectivity with `python -m stock_rating.pipeline.check_db`.
8. Summarize current DB state and generate the ratings dashboard with `python -m stock_rating.pipeline.report`.

## Operations

- Monitor price provider limits.
- Prioritize Tier 1 symbols first.
- Allow Tier 2 and Tier 3 symbols to roll across multiple trading days.
- Surface stale ratings to downstream consumers.
- Update `data/symbols.csv` to change the tracked symbol universe or refresh tiers.
- Inspect `artifacts/plans/` for the latest persisted planner run output.
- Inspect `artifacts/reports/ratings-dashboard.html` for the presentation-friendly view, `ratings-dashboard.md` for agent-readable review, or `ratings-dashboard.json` for structured parsing.
- Configure `DATABASE_URL` to persist `pipeline_runs` and `symbol_refresh_runs` into Postgres.
- Apply migrations before running the bootstrap or daily pipeline against a fresh database.
- Use `python -m stock_rating.pipeline.migrate` to apply outstanding migrations after pulling new changes.

## Hosted database

- Preferred provider: Supabase free-tier Postgres
- Use the direct Postgres connection string as `DATABASE_URL`
- Run `python -m stock_rating.pipeline.migrate`; it applies the idempotent base schema first, then any pending files in `sql/migrations/`.
- Then run `python -m stock_rating.pipeline.bootstrap_symbols`
