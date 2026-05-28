# Rating Methodology

The first model maps a composite 0-100 score into these labels:

- 90-100: A / Very Attractive
- 75-89: B / Attractive
- 55-74: C / Neutral
- 35-54: D / Unattractive
- 0-34: F / Very Unattractive

This keeps the first implementation explainable while the pipeline and data model mature.

Current persisted feature families include:

- Price/technical: `intraday_return`, `one_day_return`, `five_day_return`, `ten_day_return`, `twenty_day_return`, `gap_open_return`, `high_low_range_pct`, `daily_volume`, `average_volume_20d`, `twenty_day_volatility`
- Fundamentals: `net_margin`, `cash_flow_margin`, `return_on_assets`, `debt_to_assets`
- Macro: `yield_curve_slope`

This exceeds the MVP target of 10 derived features while keeping each feature transparent and deterministic.
