# Data Sources

## Free-only source strategy

- Alpha Vantage: primary free API for daily prices
- Twelve Data: fallback free API for price validation
- Stooq: additional price sanity check
- SEC EDGAR: official fundamentals source
- SEC company tickers file: symbol-to-CIK mapping
- FRED: macro series

## Operational note

When provider limits are hit, the pipeline should continue refreshing the universe over multiple runs instead of forcing a full same-day refresh.
