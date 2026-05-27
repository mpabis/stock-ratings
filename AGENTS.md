# Agent Instructions

## Commands

- Install dependencies: `python -m pip install -e . pytest`
- Run tests: `python -m pytest`
- Run daily pipeline: `python -m stock_rating.pipeline.daily`
- Bootstrap symbols: `python -m stock_rating.pipeline.bootstrap_symbols`
- Check database connection: `python -m stock_rating.pipeline.check_db`
- Report latest database state and generate HTML dashboard: `python -m stock_rating.pipeline.report`

## Rules

- Do not commit API keys.
- Do not change database schema without updating `sql/schema.sql` and adding a migration file.
- Keep ingestion, transformation, and scoring separate.
- Prefer simple, explainable scoring before adding black-box models.
- Store model versions and source timestamps.
