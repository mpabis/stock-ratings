# Stock Rating System Plan

## Goal

Build a hosted stock rating system that:

- Uses publicly available/free or low-cost stock market data sources
- Runs automatically every day
- Stores historical data, features, and ratings
- Can be improved iteratively with AI coding agents
- Does not depend on running jobs from a local PC

---

## Recommended MVP Architecture

Use:

> GitHub repo + Python pipeline + scheduled cloud runner + Postgres database + simple dashboard/API

Start with **daily end-of-day ratings**, not real-time trading signals.

---

## Recommended Stack

### 1. Code and AI-Friendly Development

Use a **GitHub repository** as the source of truth.

Include:

- `AGENTS.md`
- `.devcontainer/`
- `Makefile` or `justfile`
- `.env.example`
- Modular Python files
- Tests
- Documentation

The goal is to make the repo easy for AI coding agents such as Claude Code, Codex, Copilot, or Cursor to inspect, run, and improve.

---

### 2. Daily Job Runner

For the first version, use **GitHub Actions scheduled workflows**.

Example schedule:

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
          FINNHUB_API_KEY: ${{ secrets.FINNHUB_API_KEY }}
          FRED_API_KEY: ${{ secrets.FRED_API_KEY }}
        run: python -m stock_rating.pipeline daily
```

Alternative hosted job runners:

Render Cron Jobs
Railway Cron Jobs
Fly.io Machines
Google Cloud Run Jobs
AWS Lambda/EventBridge
Database
Use Supabase Postgres for the first version.

Why:

Hosted Postgres
Easy SQL access
Good for structured daily market data
Works well with Python
Easy for AI agents to inspect
Can support dashboards and APIs later
Public/Free Data Sources
Use multiple data sources because free stock data sources are often incomplete or rate-limited.

Need	Source	Notes
US fundamentals	SEC EDGAR APIs	Free and official source for filings and XBRL facts
Ticker to CIK mapping	SEC company tickers file	Useful for linking tickers to SEC data
Historical prices	Stooq, Alpha Vantage, Tiingo, Finnhub	Use one or more depending on coverage and limits
Macro data	FRED	Interest rates, inflation, unemployment, yield curve, etc.
Extra datasets	Nasdaq Data Link	Free and premium datasets
Important:

Respect rate limits
Store source names and timestamps
Keep raw data where practical
Avoid relying on one data provider only
Suggested Data Model
symbols
text
symbol
company_name
exchange
cik
sector
industry
active
price_daily
text
symbol
date
open
high
low
close
adjusted_close
volume
source
ingested_at
fundamental_facts
text
cik
symbol
fiscal_period
fiscal_year
form
metric
value
unit
filed_at
source
features_daily
text
symbol
date
feature_name
feature_value
source_version
ratings_daily
text
symbol
date
rating_score
rating_label
valuation_score
quality_score
growth_score
momentum_score
risk_score
explanation_json
model_version
created_at
pipeline_runs
text
run_id
started_at
finished_at
status
error_message
git_sha
Key Design Principle
Separate:

Raw data
Transformed features
Final ratings
This makes the system easier to debug, backtest, and improve with AI agents.

First Rating Model
Start simple and transparent.

Example:

text
Final score =
  25% valuation
  25% quality
  20% growth
  20% momentum
  10% risk
Rating Components
Valuation
Possible features:

Earnings yield
Free cash flow yield
Price/book
EV/sales
EV/EBITDA
Quality
Possible features:

Gross margin
Operating margin
Return on equity
Return on assets
Debt/assets
Growth
Possible features:

Revenue growth YoY
EPS growth YoY
Free cash flow growth
Margin expansion
Momentum
Possible features:

6-month price momentum
12-month price momentum excluding last month
Relative strength vs. S&P 500
Risk
Possible features:

Volatility
Max drawdown
Debt burden
Negative earnings
Fundamental instability
Rating Labels
Avoid overly direct investment-advice language at first.

Instead of:

text
Strong Buy
Buy
Hold
Sell
Use:

text
A: Very Attractive
B: Attractive
C: Neutral
D: Unattractive
F: Very Unattractive
Example score mapping:

text
90–100: A / Very Attractive
75–89: B / Attractive
55–74: C / Neutral
35–54: D / Unattractive
0–34: F / Very Unattractive
AI-Friendly Repository Layout
text
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
Example AGENTS.md
md
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
Build Path
Phase 1 — MVP
Use 50–100 symbols first
Pull daily prices
Pull SEC fundamentals
Compute 10–20 features
Store ratings in Supabase
Run daily from GitHub Actions
Create a simple dashboard with Streamlit, FastAPI, or Next.js
Phase 2 — Reliability
Add:

Retry logic
Data quality checks
pipeline_runs
Slack/email failure notifications
Backfill scripts
Source freshness checks
Phase 3 — Better Model
Add:

Sector-relative scoring
Backtesting
Model versioning
Historical rating performance
Feature importance
AI-generated company explanations based only on stored facts
Phase 4 — Agentic Improvement Loop
Create GitHub issues such as:

text
Improve valuation score for banks
Add FRED yield curve feature
Add backtest report for rating deciles
Refactor SEC company facts parser
Add data quality checks for stale prices
Add sector-relative normalization
Add model version comparison report
Let AI agents work issue-by-issue, with tests and PR review.

Concrete Recommendation
Use:

GitHub for code
GitHub Actions for daily scheduled jobs
Supabase Postgres for storage
Python for ingestion, transformation, and scoring
Streamlit or FastAPI for viewing results
SEC EDGAR, Stooq/Alpha Vantage/Finnhub, and FRED as initial data sources
AGENTS.md, devcontainer, tests, and documentation to make the project AI-agent friendly
MVP Success Criteria
The MVP is successful when:

A daily job runs automatically
Prices and fundamentals are stored
Features are calculated
Ratings are generated
Historical ratings are preserved
Failures are visible
The rating logic is documented
An AI agent can understand and modify the system safely
sql

If you are on macOS/Linux, you can also create it directly from the terminal with:

```bash
nano stock-rating-system-plan.md
Then paste the content, save, and exit.

If you are on Windows PowerShell, run:

powershell
notepad stock-rating-system-plan.md
Then paste the content and save.