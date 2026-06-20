# Rating Methodology

The current `v6` model grades each stock by **percentile rank against the tracked
universe** (AAII A+ style), replacing the fixed absolute score bands used through `v4`.
`v6` adds a sixth composite factor — analyst estimate revisions (see below).

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
- Analyst revision: `analyst_revision_score`, `analyst_suggestion_score_delta`, `analyst_target_price_change_pct`

`v6` composite weights (sum to 1.0): 22.5% valuation, 22.5% quality, 18% growth, 18% momentum, 9% risk, and 10% analyst revision. Freshness is calculated from the latest input date used by each rating, not from the stale pre-refresh planning state.

## Analyst estimate-revisions factor (v6)

The sixth factor is the project's proxy for the estimate-revision momentum that
drives Zacks/AAII — free data has no true forward EPS-estimate revisions, so it
uses the trailing change in analyst opinion from `analyst_consensus_daily`:

- `analyst_suggestion_score_delta` — change in `suggestion_score` between the two
  most recent snapshots, averaged across providers (Alpha Vantage, Finnhub).
- `analyst_target_price_change_pct` — change in mean analyst target price.
- `analyst_revision_score` (0-100) = `clamp(50 + suggestion_delta*15 + target_change_pct*100)`.

A symbol with no analyst history (or a single snapshot) contributes a neutral 50,
so the factor never drops or penalizes uncovered symbols. Computed in
`transform/analyst_features.py` from `repository/analyst.load_recent_analyst_consensus_by_source`.

## Where to see results

Run `python -m stock_rating.pipeline.report` to (re)generate
`artifacts/reports/ratings-dashboard.html`. The dashboard shows each symbol's
composite score, all six factor sub-scores with their A-F grades, the analyst
badge/target, and the benchmark scores (Piotroski F-Score, Magic Formula rank,
Acquirer's Multiple). The companion `ratings-methodology.html` documents this
methodology. Full detail (per-factor percentiles, all benchmark features) lives
in the `ratings_daily` and `features_daily` tables.

## Benchmark scores (separate from the composite)

Three externally-validated, fully-specified value/quality scores are computed
alongside the composite as **benchmarks** — they are persisted as their own
features and are deliberately **kept out of the weighted composite** so they stay
directly comparable to their published backtests. They are diagnostic, not
standalone buy signals (Piotroski was designed as a second-stage filter on
already-cheap stocks; Magic Formula / Acquirer's Multiple are deep-value tilts
that have had weak multi-year stretches).

- **Piotroski F-Score** (`piotroski_fscore`, 0-9; `piotroski_signals_available`):
  nine binary fundamental signals (profitability, leverage/liquidity, operating
  efficiency). Signals whose inputs are missing are skipped — `signals_available`
  flags how many of the nine were evaluable, so a low value means low confidence.
- **Magic Formula** (`magic_formula_roic`, `magic_formula_earnings_yield`,
  `magic_formula_combined_rank`): ROIC = EBIT / (net working capital + net fixed
  assets); earnings yield = EBIT / enterprise value. The combined rank is a
  cross-sectional pass — rank on ROIC plus rank on earnings yield, lowest sum is
  best (rank 1). Financials and utilities are excluded when the sector is known.
- **Acquirer's Multiple** (`acquirers_multiple`): enterprise value / EBIT.

EBIT is approximated by us-gaap `OperatingIncomeLoss`; enterprise value =
market cap (`price × diluted shares`) + total debt − cash. These come from the
SEC fundamentals path (`transform/benchmark_scores.py`, `rating/magic_formula.py`).
