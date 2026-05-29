# Changelog

All notable changes to this project should be recorded here.

This project uses dated entries because it has not adopted public semantic version releases yet. Keep the newest entry first. Use these categories when they fit: `Added`, `Changed`, `Fixed`, `Database`, `Documentation`, and `Tests`.

## 2026-05-29

### Added

- Added this changelog system and documented that future notable changes should be recorded here.
- Added separate refresh budgets for SEC fundamentals and optional Alpha Vantage analyst consensus calls.
- Added `STOCK_RATING_FUNDAMENTAL_SYMBOL_LIMIT` and `STOCK_RATING_ANALYST_SYMBOL_LIMIT` configuration.
- Added analyst snapshot date lookup so optional analyst refreshes can follow a cadence instead of repeatedly refreshing every tracked symbol.
- Added longer price-derived features: `sixty_day_return`, `one_hundred_day_return`, and `twenty_day_max_drawdown`.
- Added SEC-derived valuation and growth features: `earnings_yield`, `book_to_price`, `revenue_growth_yoy`, `net_income_growth_yoy`, and `operating_cash_flow_growth_yoy`.

### Changed

- Updated GitHub Actions workflows to current Node 24-compatible action majors.
- Upgraded the scoring model to `v4`, using richer valuation, growth, momentum, quality, and risk components while retaining the planned 25/25/20/20/10 composite weighting.
- Changed rating freshness calculation so persisted ratings use the actual latest feature/input date after a successful refresh, not the stale pre-refresh planning state.
- Changed price freshness age to count trading weekdays rather than raw calendar days.
- Changed the daily pipeline so price refreshes keep first claim on free Alpha Vantage quota; optional analyst refreshes now run from their own configured budget.
- Changed Stooq symbol normalization to preserve `.ST` Stockholm symbols instead of appending `.us`.
- Changed source refresh summaries so skipped symbols are not counted as provider failures.

### Fixed

- Fixed the GitHub Pages artifact so generated report pages such as `ratings-methodology.html` are published instead of only `index.html`.
- Fixed `preferred_provider_name` returning `twelve_data` when only Stooq was configured.
- Fixed non-US or non-SEC-covered symbols being treated as SEC refresh failures; they are now recorded as skipped with `sec_mapping_missing`.
- Fixed stale rating labels that could persist after a symbol refreshed successfully.
- Fixed README and runbook links that pointed to an old local `playground` path.

### Database

- Added `period_start`, `period_end`, and `frame` metadata columns to `fundamental_facts`.
- Added migration `004_add_fundamental_period_metadata.sql`.
- Changed `python -m stock_rating.pipeline.migrate` to apply the idempotent base schema before pending migrations, making fresh database initialization one command.

### Documentation

- Updated README, Supabase setup, runbook, data-source notes, rating methodology, dashboard methodology output, and agent instructions to match the current pipeline.
- Documented that US/SEC-covered equities remain the highest-confidence MVP path, while non-US symbols can remain tracked for price-only coverage when providers support them.

### Tests

- Added/updated tests for trading-day freshness, periodic refresh planning, rating freshness recomputation, Stooq `.ST` symbols, SEC annual fact selection, base schema migration, and new fundamental features.
- Verified the full suite with `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider`: `100 passed`.

## 2026-05-27

### Added

- Created the initial hosted daily stock rating scaffold with Python pipeline modules, Supabase Postgres schema, GitHub Actions workflows, seed-backed symbols, tests, and documentation.
