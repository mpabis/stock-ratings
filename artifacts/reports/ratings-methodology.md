# Stock Rating Methodology

Machine-readable companion to `ratings-methodology.html`. This document is optimized for AI agents and code review because it avoids layout-only HTML/CSS and keeps formulas, weights, and source mappings in plain text tables.

## Rating Scale

Grades are assigned by percentile rank against the tracked universe in even 20% buckets, not by absolute score thresholds. The displayed score is the composite percentile rescaled to 0-100.

| Score Percentile | Label |
|---|---|
| 80-100 | A / Very Attractive |
| 60-79 | B / Attractive |
| 40-59 | C / Neutral |
| 20-39 | D / Unattractive |
| 0-19 | F / Very Unattractive |

## Source To Feature Mapping

| Feature Family | Features | Primary Sources | Code Path |
|---|---|---|---|
| Price / Technical | intraday_return, one_day_return, five_day_return, ten_day_return, twenty_day_return, sixty_day_return, one_hundred_day_return, daily_volume, average_volume_20d, twenty_day_volatility, twenty_day_max_drawdown, high_low_range_pct, gap_open_return | Alpha Vantage, Twelve Data, Stooq | ingest/prices.py, transform/features.py |
| Fundamental | net_margin, cash_flow_margin, return_on_assets, debt_to_assets, earnings_yield, book_to_price, revenue_growth_yoy, net_income_growth_yoy, operating_cash_flow_growth_yoy | SEC EDGAR company facts | ingest/sec_companyfacts.py, transform/fundamentals.py |
| Analyst Consensus | analyst_target_price, analyst recommendation counts, suggestion_label | Alpha Vantage OVERVIEW, Finnhub | ingest/analyst.py, analyst_consensus_daily |
| Analyst Revision | analyst_revision_score, analyst_suggestion_score_delta, analyst_target_price_change_pct | analyst_consensus_daily history | repository/analyst.py, transform/analyst_features.py |
| Benchmark scores | piotroski_fscore, magic_formula_roic, magic_formula_earnings_yield, magic_formula_combined_rank, acquirers_multiple | SEC EDGAR company facts | transform/benchmark_scores.py, rating/magic_formula.py |
| Macro | yield_curve_slope | FRED DGS10 and DGS2 | ingest/fred_macro.py, transform/macro.py |

## Latest Source Calls

| Source | Calls | Succeeded | Failed | Status |
|---|---:|---:|---:|---|
| SEC EDGAR | 7 | 2 | 0 | Partial |
| Alpha Vantage Overview | 6 | 6 | 0 | Succeeded |
| Finnhub | 5 | 5 | 0 | Succeeded |
| Local Rebuild | 110 | 110 | 0 | Succeeded |

## Factor Calculations

### Valuation

```text
liquidity_score = clamp(25 + daily_volume / 200000)
reversal_score = clamp(55 - one_day_return*250 - intraday_return*100)
valuation = average(reversal/liquidity baseline, earnings_yield, book_to_price, profitability, cash flow, leverage)
```

If SEC valuation inputs are missing, valuation falls back to the conservative reversal/liquidity baseline.

### Quality

```text
quality_baseline = clamp(38 + liquidity*0.45 - abs(intraday_return - one_day_return)*350)
quality = average(quality_baseline, net_margin, cash_flow_margin, return_on_assets, leverage)
```

### Growth

```text
short_term_trend = clamp(50 + one_day_return*400 + intraday_return*150)
growth = average(short_term_trend, revenue_growth_yoy, net_income_growth_yoy, operating_cash_flow_growth_yoy, medium_term_momentum)
growth = clamp(growth*0.75 + macro_growth*0.25)
```

### Momentum

```text
momentum_score = clamp(50 + best_available_100/60/20/10/5/1_day_return*120 + twenty_day_return*60 + liquidity*0.05)
```

### Risk

```text
risk_penalty = abs(one_day_return)*250 + abs(intraday_return)*150 + volatility*450 + max_drawdown*250
risk = average(price_stability, leverage, cash_generation, profitability)
risk = clamp(risk*0.75 + macro_risk*0.25)
```

### Analyst Revision

```text
analyst_revision_score = clamp(50 + analyst_suggestion_score_delta*15 + analyst_target_price_change_pct*100)
```

A symbol with no analyst history or only one snapshot contributes neutral 50, so uncovered symbols are not penalized.

## Final Composite Score

Scoring runs in two passes. Pass 1 computes the weighted composite. Pass 2 ranks every symbol's composite against the universe and assigns A-F grades from percentile buckets.

```text
composite = valuation*0.225 + quality*0.225 + growth*0.18 + momentum*0.18 + risk*0.09 + analyst_revision*0.10
score = round(percentile_rank(composite, universe) * 100)
```

| Factor | Weight |
|---|---:|
| Valuation | 22.5% |
| Quality | 22.5% |
| Growth | 18% |
| Momentum | 18% |
| Risk | 9% |
| Analyst Revision | 10% |

## Benchmark Scores

Benchmark scores are shown beside the composite but deliberately excluded from the weighted score.

| Benchmark | Formula | Interpretation |
|---|---|---|
| Piotroski F-Score | 0-9 binary profitability / leverage / efficiency signals | Higher is better; full-confidence values are highlighted in HTML, while partial values are muted and explain coverage in the tooltip. |
| Magic Formula | rank(ROIC) + rank(EBIT / enterprise value) | Lower combined rank is better; financials and utilities are excluded when sector is known. |
| Acquirer's Multiple | enterprise value / EBIT | Lower is cheaper. EBIT is approximated with OperatingIncomeLoss. |
