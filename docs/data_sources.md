# Data Sources

## Free-only source strategy

- Alpha Vantage: primary free API for daily prices
- Alpha Vantage company overview: analyst target and recommendation counts (source tag: `alpha_vantage_overview`)
- Twelve Data: fallback free API for price validation
- Stooq: additional price sanity check
- SEC EDGAR: official fundamentals source. Beyond the core income/balance-sheet
  metrics, the benchmark scores (story 1.2) also pull `OperatingIncomeLoss` (EBIT
  proxy), `GrossProfit` / `CostOfRevenue`, `AssetsCurrent`, `LiabilitiesCurrent`,
  `PropertyPlantAndEquipmentNet`, cash, and long-term-debt concepts. Balance-sheet
  items used for Piotroski year-over-year signals are retained for two annual
  periods (latest + prior).
- SEC company tickers file: symbol-to-CIK mapping
- FRED: macro series
- Finnhub: additional analyst consensus source — recommendation trends + price targets (source tag: `finnhub`)

## Analyst consensus providers

Two independent providers supply analyst data, each tracked separately by source in `analyst_consensus_daily`:

| Provider | Source tag | Endpoints used | Free tier limit | Config key |
|---|---|---|---|---|
| Alpha Vantage OVERVIEW | `alpha_vantage_overview` | `/query?function=OVERVIEW` | ~5 req/min | `STOCK_RATING_ANALYST_SYMBOL_LIMIT` |
| Finnhub | `finnhub` | `/stock/recommendation` + `/stock/price-target` | 60 req/min | `STOCK_RATING_FINNHUB_ANALYST_SYMBOL_LIMIT` |

Finnhub makes 2 API calls per symbol. The default inter-symbol pause (`FINNHUB_ANALYST_MIN_INTERVAL_SECONDS`, default `2.0`) keeps the call rate at ~30 symbols/min, well within the free tier.

Both providers use the same staleness tiers (tier 1: 7 days, tier 2: 14 days, tier 3: 30 days) but track freshness independently so each source refreshes on its own schedule.

## Operational note

When provider limits are hit, the pipeline should continue refreshing the universe over multiple runs instead of forcing a full same-day refresh.

The highest-confidence MVP path is still US/SEC-covered equities. Non-US symbols can remain in the tracked universe for price-only coverage when a price provider supports the exchange, but the SEC fundamentals step records them as skipped when no SEC ticker mapping exists instead of treating them as failed refreshes.
