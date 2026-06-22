# Changelog

All notable changes to this project should be recorded here.

This project uses dated entries because it has not adopted public semantic version releases yet. Keep the newest entry first. Use these categories when they fit: `Added`, `Changed`, `Fixed`, `Database`, `Documentation`, and `Tests`.

## 2026-06-22 (dashboard-targets)

### Fixed

- Changed the dashboard report query so the analyst badge still uses the latest recommendation row while the Target column uses the latest non-null `analyst_target_price`. This prevents newer Finnhub recommendation-only snapshots from hiding older Alpha Vantage target prices.

### Tests

- Added a regression test for the separate latest-analyst and latest-target report query paths.

## 2026-06-20 (stooq-resilience)

### Changed

- Made the Stooq price fallback resilient to rate-limiting. Stooq's keyless CSV endpoint returns HTTP 404/429/403 when it throttles an IP (a genuinely missing symbol returns HTTP 200 + "No data"); these are now raised as a new `StooqRateLimitError` and recorded as `rate_limited` (retryable next run) instead of hard `failed`. Added inter-symbol pacing (`STOOQ_MIN_INTERVAL_SECONDS`, default 1.0s) and a per-run request cap (`STOOQ_MAX_REQUESTS_PER_RUN`, default 40) so an exhausted-upstream cascade can't dump the whole universe onto Stooq at once. Mirrors the existing Alpha Vantage pacing/cap/rate-limit handling.

### Tests

- Added Stooq 404/429 → rate-limit and 200-"No data" → response-error ingest tests, plus plan-level tests for retryable rate-limit (stops the batch), the per-run cap, and inter-symbol pacing.

## 2026-06-20 (dashboard)

### Added

- Surfaced the v6 outputs in the ratings dashboard: an **Analyst Rev** factor column, **A-F grade badges** on all six factor cells, and three benchmark columns — **Piotroski F-Score** (N/9, dimmed when fewer than nine signals were evaluable), **Magic Formula** combined rank, and **Acquirer's Multiple** (EV/EBIT). Missing values render an em dash.

### Documentation

- Expanded `docs/architecture.md` from a stub into a full description of the layered design and the two-pass v6 rating calculation. Added a "Where to see results" section to `docs/rating_methodology.md` and a Benchmark Scores section to the in-report methodology page.

## 2026-06-20 (latest)

### Changed

- Upgraded the scoring model to `v6`: added a sixth composite factor, **analyst estimate revisions / sentiment momentum**, derived from `analyst_consensus_daily` history (change in `suggestion_score` and mean target price between the two latest snapshots, averaged across Alpha Vantage + Finnhub). Composite weights rebalanced to 22.5/22.5/18/18/9/10 (valuation/quality/growth/momentum/risk/analyst-revision). Symbols with no analyst history contribute a neutral 50, so the factor never penalizes uncovered names. New modules `transform/analyst_features.py` and loader `repository/analyst.load_recent_analyst_consensus_by_source`.

### Database

- Migration `006_add_analyst_revision_factor.sql`: added `analyst_revision_score`, `analyst_revision_percentile`, and `analyst_revision_grade` columns to `ratings_daily`.

### Documentation

- Documented the analyst-revision factor and new weights in `docs/rating_methodology.md` and the in-report methodology page.

### Tests

- Added `tests/test_analyst_features.py` (rising/falling sentiment, multi-source averaging, single-snapshot/no-history neutral fallback, target-price-missing path, clamping).

## 2026-06-20 (later)

### Added

- Added three externally-validated benchmark scores, computed alongside the composite but deliberately kept **out** of the weighted score so they stay comparable to their published backtests: **Piotroski F-Score** (`piotroski_fscore` 0-9 + `piotroski_signals_available`), **Magic Formula** (`magic_formula_roic`, `magic_formula_earnings_yield`, and a cross-sectional `magic_formula_combined_rank` that excludes financials/utilities), and the **Acquirer's Multiple** (`acquirers_multiple` = EV/EBIT). New modules `transform/benchmark_scores.py` and `rating/magic_formula.py`.

### Changed

- Extended SEC fundamentals ingestion with `OperatingIncomeLoss` (EBIT proxy), `GrossProfit`/`CostOfRevenue`, `AssetsCurrent`, `LiabilitiesCurrent`, `PropertyPlantAndEquipmentNet`, cash, and long-term-debt concepts. Balance-sheet items needed for Piotroski year-over-year signals are now retained for two annual periods.

### Documentation

- Documented the benchmark scores in `docs/rating_methodology.md` and the new SEC fields in `docs/data_sources.md`.

### Tests

- Added `tests/test_benchmark_scores.py` and `tests/test_magic_formula.py` (F-Score full/degraded paths, gross-margin fallback, ROIC/EV guards, combined-rank ordering, sector exclusions, persistence).

## 2026-06-20

### Changed

- Upgraded the scoring model to `v5`: grades are now assigned by cross-sectional **percentile rank against the tracked universe** (AAII A+ style, even 20% buckets) instead of fixed absolute score bands. Scoring runs in two passes — per-symbol weighted composite (unchanged 25/25/20/20/10 weights), then a universe-wide percentile pass that assigns the final A-F grade and rescales `rating_score` to the composite percentile (0-100). Grades are relative: a symbol can change grade as the universe shifts even if its own inputs do not.

### Database

- Migration `005_add_rating_percentile_grades.sql`: added `composite_percentile` and per-factor `*_percentile` / `*_grade` columns to `ratings_daily`.

### Documentation

- Rewrote `docs/rating_methodology.md` and the in-report methodology page for v5 percentile grading.

### Tests

- Added `tests/test_percentile_ranking.py` and `tests/test_universe_grading.py` covering percentile assignment, even-bucket boundaries, ties, single-symbol/empty universes, the relative-not-absolute property, and the persistence path.

## 2026-06-19

### Added

- Added six US-listed quality-growth symbols to `data/symbols.csv`: `MPWR` (Monolithic Power Systems, tier 2), `AXON` (Axon Enterprise, tier 2), `VRT` (Vertiv Holdings, tier 2), `ABNB` (Airbnb, tier 3), `ADSK` (Autodesk, tier 3), and `APH` (Amphenol, tier 3). Selected for the model_v1 profile of profitable, low-debt, high-margin growth with positive momentum.
- Expanded the tracked universe by 50 additional US-listed quality-growth symbols in `data/symbols.csv`, diversified across mega-cap tech/internet, semis & EDA, software/AI/SaaS, payments & fintech, financials, consumer/retail, healthcare/medtech, and industrials/power. Tier 2: `META`, `NFLX`, `AVGO`, `CRWD`, `PLTR`, `ISRG`, `GEV`. Tier 3: `ORCL`, `CRM`, `ADBE`, `INTU`, `QCOM`, `AMAT`, `LRCX`, `KLAC`, `SNPS`, `CDNS`, `ADI`, `MCHP`, `WDAY`, `TEAM`, `VEEV`, `FICO`, `TTD`, `V`, `MA`, `AXP`, `FI`, `TOST`, `SOFI`, `JPM`, `SPGI`, `MCO`, `KKR`, `PGR`, `COST`, `TJX`, `ORLY`, `DECK`, `CMG`, `BKNG`, `UBER`, `VRTX`, `REGN`, `BSX`, `EW`, `TMO`, `ETN`, `PWR`, `GE`. Favored US-domiciled issuers so SEC fundamentals map cleanly into the rating sub-scores. Tracked universe is now 109 active symbols, materially increasing daily free-provider refresh load (handled by tier-based rolling refresh).

## 2026-06-03

### Changed

- Expanded `.gitignore` coverage for local Python caches, environments, secrets, build artifacts, test/tool outputs, local runtime files, and editor/OS metadata.

## 2026-06-02

### Added

- Added `TWELVE_DATA_MAX_REQUESTS_PER_RUN` to cap Twelve Data fallback calls before overflow routes to Stooq.
- Added Yahoo Finance links to ticker/company cells in the generated dashboard, using provider-specific ticker normalization.

### Changed

- Changed fallback routing so Xetra symbols go straight to Stooq and Twelve Data overflow is handed to Stooq instead of continuing into rate-limit errors.
- Marked Stockholm symbols without current Twelve Data or Stooq coverage inactive in `data/symbols.csv`.

### Fixed

- Fixed Twelve Data HTTP 429 responses being recorded as generic provider failures instead of rate-limit events.
- Fixed Stooq normalization for prefixed US symbols and class-share tickers such as `NASDAQ:GOOGL` and `BRK.B`.
- Fixed Stooq fallback calls for known unsupported exchanges by recording them as skipped instead of failed provider requests.

### Tests

- Added regression tests for Twelve Data HTTP 429 handling, capped fallback routing, Stooq symbol support, and dashboard Yahoo Finance links.

## 2026-06-01

### Added

- Added a weekend slow-input pipeline that refreshes macro, due SEC fundamentals, optional analyst consensus, and rebuilds ratings from stored prices without normal price-provider calls.
- Added a weekend GitHub Actions workflow scheduled for Saturday and Sunday at 22:30 UTC to publish the refreshed dashboard.

### Documentation

- Documented the weekend refresh command and behavior in README and agent instructions.

### Tests

- Added tests for stored-price rebuild planning, weekend status handling, and weekend pipeline orchestration that verifies price refreshes are skipped.

## 2026-05-30

### Added

- Added a daily rating repair pass that rebuilds missing or stale ratings from stored price history without spending price-provider quota.

### Changed

- Changed data-quality checks so symbols with no price history are not double-counted as missing ratings.

### Fixed

- Fixed latest fundamental fact loading so period metadata rows map `filed_at` and `source` without raising `tuple index out of range` during rating refreshes.
- Fixed Stooq daily price parsing so decimal-formatted volume values are accepted instead of failing the provider refresh.

### Tests

- Added regression tests for fundamental fact row mapping and Stooq decimal volume parsing.
- Added tests for rating repair planning, stored-price loading, and rating repair execution.
- Updated quality alert tests for the missing-price-only behavior.
- Verified the full suite with `.\.venv\Scripts\python.exe -m pytest`: `107 passed`.

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
