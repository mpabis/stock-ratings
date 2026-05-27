# Stock Rating System Plan

## Goal

Build a hosted stock rating system that:

- Uses free data sources for market and macro data
- Runs automatically every trading day
- Stores raw data, derived features, and historical ratings
- Can be improved safely by AI coding agents over time
- Does not depend on a local machine staying online

## MVP Recommendation

Build a daily end-of-day rating system first.

Use this stack:

- GitHub repository for source control and agent-friendly development
- Python pipeline for ingestion, transformation, and scoring
- GitHub Actions for scheduled execution
- Supabase Postgres for storage
- Streamlit or FastAPI for the first read-only dashboard/API

This is the simplest path that is cheap, hosted, inspectable, and easy to iterate on.

## Data Source Decision

### Free-only constraint

This project should use **free-only data sources** for market data, fundamentals, ticker mapping, and macro inputs.

That changes the decision criteria:

- Low cost is not enough; the source must be usable for free.
- Official APIs are preferred over scrape-style integrations.
- For daily prices, we should expect tighter rate limits and less predictable reliability than paid feeds.
- At 1,000 symbols, free-only market data is the main operational constraint, not storage or scoring.

### Data source summary

For the current plan, use these sources:

| Need | Primary source | Type | Notes |
| --- | --- | --- | --- |
| Daily stock prices | Alpha Vantage | Free API | Best fit as the primary free API source for daily OHLCV in an MVP |
| Price fallback / validation | Twelve Data | Free API | Secondary free API to cross-check gaps or suspicious values |
| Additional price sanity check | Stooq | Free public dataset | Useful as a non-API validation source when API values look incomplete |
| Fundamentals | SEC EDGAR company facts APIs | Free official API | Best source for US company fundamentals |
| Ticker to CIK mapping | SEC company tickers file | Free official file | Required to connect symbols to SEC filings |
| Macro context | FRED | Free official API | Best source for macro series |

### Direct answer

For stock market data specifically, the plan should use **Alpha Vantage as the primary free API source for daily prices** and **Twelve Data as the secondary free API source for fallback/validation**.

Use **Stooq only as an additional free validation source**, not as the main operational feed.

For fundamentals, use **SEC EDGAR**, because it is free, official, and a better match for an explainable ratings system than third-party fundamentals APIs.

### Why this choice

- Alpha Vantage is one of the more practical free APIs for daily stock data.
- Twelve Data provides a second free API path so the system does not depend on a single price provider.
- Stooq is useful as a lightweight external cross-check even though it is not the main API source.
- SEC EDGAR is the right source of truth for US company fundamentals.
- FRED remains the right source for macro indicators.

### Important limitation

With the **free-only API** requirement, the architecture can still support 1,000 stocks, but the daily ingestion workflow becomes more constrained.

It is acceptable for the system to refresh the full symbol universe over **multiple days** when API limits are hit.

That means the system does **not** need to guarantee a same-day refresh for every symbol on every run.

You should expect one or more of these tradeoffs:

- Chunked or staggered refreshes
- Rolling multi-day refresh windows for lower-priority symbols
- Cached historical backfills instead of repeated full refreshes
- Occasional partial runs when one provider is slow or rate-limited
- More provider reconciliation logic
- Slower daily completion than a paid market data feed

### What not to optimize for yet

Do not optimize for:

- Real-time quotes
- Intraday bars
- Tick data
- Global exchange coverage
- Complex alternative datasets

The first version should only solve daily US equity ratings well.

## Scaling: 100 vs 1,000 Stocks

The architecture stays broadly the same at 1,000 stocks, but the ingestion strategy and runner discipline become stricter.

| Area | 50-100 symbols | 1,000 symbols |
| --- | --- | --- |
| Core architecture | Keep current design | Keep current design |
| Database | Supabase Postgres is comfortable | Supabase Postgres is still fine |
| Pipeline shape | Single daily run is simple | Use chunking, retries, and stronger run tracking |
| Job runner | GitHub Actions is an easy default | GitHub Actions can still work, but job duration and retries need more care |
| Daily prices | Free APIs are manageable | Free APIs remain possible, but become the main reliability bottleneck |
| Fundamentals | SEC EDGAR works well | SEC EDGAR still works well |
| Macro | FRED works well | FRED still works well |

### Practical conclusion

If the target grows to 1,000 stocks:

- Keep Python, Postgres, and the raw/features/ratings separation.
- Keep SEC EDGAR and FRED.
- Keep GitHub Actions initially, but design for chunked runs and retries.
- Do not assume a single free API will refresh all 1,000 symbols quickly every day without friction.
- Build provider abstraction from day one so price ingestion can reconcile Alpha Vantage, Twelve Data, and Stooq.
- Treat freshness as a policy decision: some symbols can be updated daily, while others can be updated on a rolling multi-day schedule.

### Suggested refresh policy for 1,000 stocks

Use a tiered refresh approach:

- Tier 1: high-priority symbols refreshed every trading day
- Tier 2: medium-priority symbols refreshed every 2-3 trading days
- Tier 3: low-priority symbols refreshed over a longer rolling window when limits are tight

This keeps the system compatible with free APIs while still allowing broad market coverage.

### Freshness SLA

Treat freshness as an explicit service-level target.

| Tier | Typical symbols | Target refresh cadence | Max acceptable age for price data | Rating behavior |
| --- | --- | --- | --- | --- |
| Tier 1 | Watchlist, benchmark names, user-selected symbols | Every trading day | 1 trading day | Ratings generated normally |
| Tier 2 | Core coverage universe | Every 2-3 trading days | 3 trading days | Ratings generated with freshness flag if older than 1 day |
| Tier 3 | Long-tail coverage | Rolling multi-day schedule | 5 trading days | Ratings allowed, but clearly marked stale when outside Tier 1 freshness |

If a symbol exceeds its maximum acceptable age, the system should keep the most recent stored data but mark downstream ratings as stale.

### Scheduler policy

The daily scheduler should work like this:

1. Refresh all Tier 1 symbols first.
2. Spend remaining API budget on the stalest Tier 2 symbols.
3. Use any remaining budget for Tier 3 symbols, ordered by oldest successful refresh.
4. If a provider limit is hit, stop cleanly and continue on the next scheduled run.
5. Record which symbols were attempted, succeeded, skipped, rate-limited, or failed.

This makes refresh order deterministic and keeps the best symbols freshest.

## Recommended Architecture

```text
GitHub repo
  -> Python pipeline
  -> GitHub Actions scheduled workflow
  -> Supabase Postgres
  -> Streamlit/FastAPI read-only output
```

Start with daily end-of-day ratings, not trading signals.

## Repository Setup

Use a GitHub repository as the source of truth.

Include:

- AGENTS.md
- .devcontainer/
- Makefile or justfile
- .env.example
- pyproject.toml
- Modular Python packages
- Tests
- Documentation

The repo should be easy for agents and humans to inspect, run, and improve without guessing how the system works.

## Daily Job Runner

Use GitHub Actions scheduled workflows for the first version.

Example:

```yaml
name: daily-stock-ratings

on:
  workflow_dispatch:
  schedule:
    - cron: "30 22 * * 1-5" # 22:30 UTC, Mon-Fri

jobs:
  update-ratings:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - run: pip install -r requirements.txt

      - name: Run daily pipeline
        env:
          DATABASE_URL: ${{ secrets.DATABASE_URL }}
          ALPHA_VANTAGE_API_KEY: ${{ secrets.ALPHA_VANTAGE_API_KEY }}
          TWELVE_DATA_API_KEY: ${{ secrets.TWELVE_DATA_API_KEY }}
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
        run: python -m stock_rating.pipeline.daily
```

Alternative hosted runners if GitHub Actions becomes limiting:

- Render Cron Jobs
- Railway Cron Jobs
- Fly.io Machines
- Google Cloud Run Jobs
- AWS Lambda + EventBridge

## Database

Use Supabase Postgres for the first version.

Why:

- Hosted Postgres
- Easy SQL access
- Good fit for structured daily market data
- Works well with Python
- Easy for AI agents to inspect
- Suitable for dashboards and APIs later

## Data Ingestion Principles

- Respect provider rate limits
- Store the source and ingestion timestamp for every imported row
- Keep raw data whenever practical
- Separate raw data from derived features and ratings
- Build the system so one provider can fail without corrupting the whole run
- Design price ingestion so symbols can be refreshed in batches
- Prefer incremental updates over repeated full-history pulls
- Accept rolling multi-day refreshes when daily limits are insufficient
- Track per-symbol freshness so ratings can surface stale inputs explicitly
- Prioritize refreshes by symbol tier and oldest successful update time
- Stop cleanly on provider limit exhaustion and resume on the next run

## Suggested Data Model

### symbols

| Column | Notes |
| --- | --- |
| symbol | Ticker |
| company_name | Display name |
| exchange | Primary exchange |
| cik | SEC identifier |
| sector | Optional enrichment |
| industry | Optional enrichment |
| active | Soft active flag |
| refresh_tier | Tier 1, 2, or 3 |
| last_price_refresh_at | Timestamp of last successful price refresh |
| last_fundamental_refresh_at | Timestamp of last successful fundamentals refresh |

### price_daily

| Column | Notes |
| --- | --- |
| symbol | Ticker |
| date | Trading date |
| open | Daily open |
| high | Daily high |
| low | Daily low |
| close | Daily close |
| adjusted_close | Adjusted close when available |
| volume | Daily volume |
| source | Provider name |
| ingested_at | Import timestamp |

### fundamental_facts

| Column | Notes |
| --- | --- |
| cik | SEC identifier |
| symbol | Ticker |
| fiscal_period | Quarter or annual period |
| fiscal_year | Fiscal year |
| form | 10-K, 10-Q, etc. |
| metric | Normalized metric name |
| value | Metric value |
| unit | Reported unit |
| filed_at | Filing timestamp |
| source | Data source |

### features_daily

| Column | Notes |
| --- | --- |
| symbol | Ticker |
| date | Feature date |
| feature_name | Name of derived feature |
| feature_value | Numeric value |
| source_version | Transformation version |

### ratings_daily

| Column | Notes |
| --- | --- |
| symbol | Ticker |
| date | Rating date |
| rating_score | Final numeric score |
| rating_label | A-F label |
| valuation_score | Sub-score |
| quality_score | Sub-score |
| growth_score | Sub-score |
| momentum_score | Sub-score |
| risk_score | Sub-score |
| explanation_json | Explainable output payload |
| model_version | Version of scoring model |
| created_at | Insert timestamp |
| freshness_status | fresh, aging, or stale |
| freshest_input_date | Date of the newest input used in the rating |

### pipeline_runs

| Column | Notes |
| --- | --- |
| run_id | Unique pipeline run identifier |
| started_at | Start time |
| finished_at | End time |
| status | success / failed / partial |
| error_message | Failure text if any |
| git_sha | Code version used |

### symbol_refresh_runs

| Column | Notes |
| --- | --- |
| run_id | Pipeline run identifier |
| symbol | Ticker |
| data_type | price or fundamentals |
| provider | Source attempted |
| status | succeeded / skipped / rate_limited / failed |
| attempted_at | Attempt timestamp |
| completed_at | Completion timestamp |
| error_message | Failure or skip reason |

## Core Design Principle

Keep these layers separate:

- Raw data
- Transformed features
- Final ratings

That separation makes the system easier to debug, backtest, and evolve safely.

## First Rating Model

Start with a transparent weighted score:

```text
Final score =
  25% valuation
  25% quality
  20% growth
  20% momentum
  10% risk
```

### Valuation

Possible features:

- Earnings yield
- Free cash flow yield
- Price/book
- EV/sales
- EV/EBITDA

### Quality

Possible features:

- Gross margin
- Operating margin
- Return on equity
- Return on assets
- Debt/assets

### Growth

Possible features:

- Revenue growth year over year
- EPS growth year over year
- Free cash flow growth
- Margin expansion

### Momentum

Possible features:

- 6-month price momentum
- 12-month price momentum excluding the most recent month
- Relative strength vs. S&P 500

### Risk

Possible features:

- Volatility
- Max drawdown
- Debt burden
- Negative earnings
- Fundamental instability

## Rating Labels

Avoid direct investment-advice wording in the first release.

Use:

| Score | Label |
| --- | --- |
| 90-100 | A / Very Attractive |
| 75-89 | B / Attractive |
| 55-74 | C / Neutral |
| 35-54 | D / Unattractive |
| 0-34 | F / Very Unattractive |

## AI-Friendly Repository Layout

```text
stock-rating-system/
  AGENTS.md
  README.md
  Makefile
  pyproject.toml
  requirements.txt
  .env.example

  .github/
    workflows/
      daily-stock-ratings.yml
      tests.yml

  .devcontainer/
    devcontainer.json

  sql/
    migrations/
    schema.sql

  src/
    stock_rating/
      __init__.py
      config.py
      db.py

      ingest/
        prices.py
        sec_companyfacts.py
        fred_macro.py
        symbols.py

      transform/
        fundamentals.py
        technicals.py
        features.py

      rating/
        model_v1.py
        scoring.py
        explanations.py

      pipeline/
        daily.py
        backfill.py

      quality/
        checks.py

  tests/
    test_scoring.py
    test_sec_parser.py
    test_price_ingest.py

  docs/
    data_sources.md
    rating_methodology.md
    architecture.md
    runbook.md
```

## Example AGENTS.md

```md
# Agent Instructions

## Commands

- Install dependencies: `pip install -r requirements.txt`
- Run tests: `pytest`
- Run daily pipeline: `python -m stock_rating.pipeline.daily`
- Run local rating for one symbol: `python -m stock_rating.rating.model_v1 --symbol AAPL`

## Rules

- Do not commit API keys.
- Do not change database schema without adding a migration.
- Every new rating feature must include:
  - source data table
  - calculation formula
  - unit test
  - documentation in `docs/rating_methodology.md`
- Keep ingestion, transformation, and scoring separate.
- Prefer simple, explainable scoring before adding black-box models.
- Store model versions.
- Store source and ingestion timestamp for all imported data.
```

## Delivery Phases

### Phase 1: MVP

- Start with 50-100 symbols
- Pull daily prices from Alpha Vantage
- Use Twelve Data for fallback validation
- Use Stooq only as a third-source sanity check
- Pull SEC fundamentals
- Compute 10-20 features
- Store ratings in Supabase
- Run daily from GitHub Actions
- Create a simple dashboard with Streamlit or FastAPI

### Phase 1b: Scale to 1,000 Symbols with Free APIs

- Add per-symbol freshness tracking
- Divide the universe into refresh tiers
- Refresh the highest-priority symbols daily
- Refresh the remaining symbols over a rolling multi-day window
- Mark stale symbols clearly in downstream ratings and dashboards
- Add provider failover and retry rules for price ingestion
- Persist per-symbol refresh attempts for auditability and debugging

### Phase 2: Reliability

- Retry logic
- Data quality checks
- pipeline_runs tracking
- Slack or email failure notifications
- Backfill scripts
- Source freshness checks

### Phase 3: Better Model

- Sector-relative scoring
- Backtesting
- Model versioning
- Historical rating performance analysis
- Feature importance reporting
- AI-generated company explanations based only on stored facts

### Phase 4: Agentic Improvement Loop

Create focused GitHub issues such as:

- Improve valuation score for banks
- Add FRED yield curve feature
- Add backtest report for rating deciles
- Refactor SEC company facts parser
- Add data quality checks for stale prices
- Add sector-relative normalization
- Add model version comparison report

That lets AI agents work issue by issue with tests and reviewable changes.

## Concrete Recommendation

Use:

- GitHub for code
- GitHub Actions for scheduled jobs
- Supabase Postgres for storage
- Python for ingestion, transformation, and scoring
- Streamlit or FastAPI for the first interface
- Alpha Vantage for primary free daily stock price API access
- Twelve Data for secondary free price API access
- Stooq for additional price validation
- SEC EDGAR for fundamentals
- FRED for macro inputs
- AGENTS.md, tests, documentation, and a devcontainer to keep the project agent-friendly

## MVP Success Criteria

The MVP is successful when:

- A daily job runs automatically
- Prices and fundamentals are stored consistently
- Features are calculated reproducibly
- Ratings are generated every trading day
- Historical ratings are preserved
- Failures are visible and diagnosable
- The rating logic is documented
- An AI coding agent can understand and modify the system safely

For the larger 1,000-symbol free-only version, success does not require every symbol to refresh every day. It is acceptable for part of the universe to update on a rolling multi-day schedule, as long as freshness is tracked and visible.

