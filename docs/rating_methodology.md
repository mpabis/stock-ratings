# Rating Methodology

The current `v5` model grades each stock by **percentile rank against the tracked
universe** (AAII A+ style), replacing the fixed absolute score bands used through `v4`.

## Two-pass scoring

1. **Per-symbol pass** — `compute_rating_breakdown` produces absolute 0-100 factor
   sub-scores (valuation, quality, growth, momentum, risk) and a weighted composite,
   persisted per symbol.
2. **Universe pass** (`rating.universe_grading.apply_universe_percentile_grades`) — runs
   once after all symbols are rated. It ranks every active symbol's sub-scores against
   the whole universe, assigns a percentile (0-1) per factor and for the composite, and
   writes A-F letter grades back onto each rating row.

## Grade buckets (even 20%)

Grades come from the composite percentile, in even quintiles:

- top 20% (percentile ≥ 0.8): A / Very Attractive
- next 20% (≥ 0.6): B / Attractive
- middle 20% (≥ 0.4): C / Neutral
- next 20% (≥ 0.2): D / Unattractive
- bottom 20% (< 0.2): F / Very Unattractive

**Grades are relative, not absolute.** Roughly 20% of the universe is always graded "A".
A stock's grade can change as the universe changes even if its own fundamentals do not.
This is intentional and differs from the `v4` absolute-quality bands.

### Method details

- All five factors are "higher is better" (including `risk_score`, where high = safer),
  so percentiles share one direction and the composite preserves it.
- Percentile uses the mid-rank method `(count_below + 0.5·count_equal) / n`, which handles
  ties deterministically. A single-symbol universe yields percentile 0.5 (a neutral C).
- The composite is the weighted sum of the *raw* sub-scores (weights below), then that
  composite is percentile-ranked. `rating_score` is the composite percentile rescaled to
  0-100 so existing "order by rating_score desc" ranking still holds.
- Per-factor percentiles and letter grades are persisted (`*_percentile`, `*_grade`
  columns on `ratings_daily`; see migration `005`).

Current persisted feature families include:

- Price/technical: `intraday_return`, `one_day_return`, `five_day_return`, `ten_day_return`, `twenty_day_return`, `gap_open_return`, `high_low_range_pct`, `daily_volume`, `average_volume_20d`, `twenty_day_volatility`
- Fundamentals: `net_margin`, `cash_flow_margin`, `return_on_assets`, `debt_to_assets`
- Valuation and growth fundamentals: `earnings_yield`, `book_to_price`, `revenue_growth_yoy`, `net_income_growth_yoy`, `operating_cash_flow_growth_yoy`
- Macro: `yield_curve_slope`

The score still uses the planned transparent weights: 25% valuation, 25% quality, 20% growth, 20% momentum, and 10% risk. Freshness is calculated from the latest input date used by each rating, not from the stale pre-refresh planning state.
