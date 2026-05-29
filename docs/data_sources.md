# Data Sources

## Free-only source strategy

- Alpha Vantage: primary free API for daily prices
- Alpha Vantage company overview: analyst target and recommendation counts
- Twelve Data: fallback free API for price validation
- Stooq: additional price sanity check
- SEC EDGAR: official fundamentals source
- SEC company tickers file: symbol-to-CIK mapping
- FRED: macro series

## Operational note

When provider limits are hit, the pipeline should continue refreshing the universe over multiple runs instead of forcing a full same-day refresh.

The highest-confidence MVP path is still US/SEC-covered equities. Non-US symbols can remain in the tracked universe for price-only coverage when a price provider supports the exchange, but the SEC fundamentals step records them as skipped when no SEC ticker mapping exists instead of treating them as failed refreshes.
