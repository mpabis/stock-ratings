# Rating Methodology

The current `v4` model maps a composite 0-100 score into these labels:

- 90-100: A / Very Attractive
- 75-89: B / Attractive
- 55-74: C / Neutral
- 35-54: D / Unattractive
- 0-34: F / Very Unattractive

This keeps the first implementation explainable while the pipeline and data model mature.

Current persisted feature families include:

- Price/technical: `intraday_return`, `one_day_return`, `five_day_return`, `ten_day_return`, `twenty_day_return`, `gap_open_return`, `high_low_range_pct`, `daily_volume`, `average_volume_20d`, `twenty_day_volatility`
- Fundamentals: `net_margin`, `cash_flow_margin`, `return_on_assets`, `debt_to_assets`
- Valuation and growth fundamentals: `earnings_yield`, `book_to_price`, `revenue_growth_yoy`, `net_income_growth_yoy`, `operating_cash_flow_growth_yoy`
- Macro: `yield_curve_slope`

The score still uses the planned transparent weights: 25% valuation, 25% quality, 20% growth, 20% momentum, and 10% risk. Freshness is calculated from the latest input date used by each rating, not from the stale pre-refresh planning state.
