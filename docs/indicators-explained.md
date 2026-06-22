# Indicators Explained

This note explains how to use the benchmark indicators shown beside the stock rating dashboard: Piotroski F-Score, Magic Formula rank, and EV/EBIT / Acquirer's Multiple.

These indicators are diagnostic checks. They are not automatic buy or sell signals, and they are deliberately kept outside the dashboard's weighted composite score. Use them to confirm or challenge the main rating.

## Quick Reference

| Indicator | What It Answers | Better Direction | Best Use |
|---|---|---:|---|
| Piotroski F-Score | Is the company financially healthy? | Higher | Quality and balance-sheet filter |
| Magic Formula rank | Is this a good business at a good price? | Lower | Quality-plus-value confirmation |
| EV/EBIT / Acquirer's Multiple | How expensive is the operating business? | Lower | Valuation sanity check |

## Piotroski F-Score

Piotroski F-Score is a financial health score from 0 to 9. It uses accounting signals across profitability, leverage/liquidity, and operating efficiency.

Higher is better:

| F-Score | Interpretation | Buy / Sell Use |
|---:|---|---|
| 8-9 | Very strong financial quality | Strong positive confirmation |
| 6-7 | Healthy | Acceptable for buys |
| 4-5 | Mixed | Require a stronger valuation, growth, or catalyst reason |
| 0-3 | Weak | Avoid or sell unless there is a clear turnaround thesis |

Check `piotroski_signals_available` before relying on the score. A score of 3 with only 3 signals available is not the same as a score of 3 out of all 9 possible signals. Scores are most useful when most of the 9 signals are evaluable.

Use F-Score as a quality gate:

```text
Strong buy confirmation: F-Score >= 6 with most signals available
Caution: F-Score <= 3, especially if the main rating is only B/C
Potential sell signal: F-Score deteriorates over time alongside weaker rating factors
```

## Magic Formula Rank

Magic Formula combines two ideas:

```text
business quality = return on invested capital
cheapness = EBIT / enterprise value
```

The dashboard shows a combined cross-sectional rank. Lower is better. A low rank means the company compares well on both quality and earnings yield.

| Magic Formula Rank | Interpretation | Buy / Sell Use |
|---:|---|---|
| Top 5-10% of universe | High-quality business at a reasonable or cheap price | Strong buy confirmation |
| Middle of universe | Neutral | Do not rely on it alone |
| Bottom of universe | Low return, expensive, or both | Negative confirmation |

Use Magic Formula mostly within comparable businesses. It is less useful for banks, insurers, utilities, early-stage software, cyclical commodity companies, and companies with distorted EBIT.

```text
Good setup: A/B composite rating + low Magic Formula rank
Bad setup: High composite rating but very weak Magic Formula rank, unless growth explains it
Sell review: Magic Formula rank deteriorates while valuation and quality grades also weaken
```

## EV/EBIT / Acquirer's Multiple

EV/EBIT asks how expensive the whole operating business is relative to operating earnings:

```text
EV/EBIT = enterprise value / EBIT
```

In the dashboard this is shown as `acquirers_multiple`. Lower is cheaper.

General ranges:

| EV/EBIT | Interpretation |
|---:|---|
| < 8 | Potentially cheap |
| 8-15 | Reasonable for stable companies |
| 15-25 | Expensive unless growth or quality is high |
| > 25 | Requires exceptional growth, margins, or durability |
| Negative or null | EBIT is missing, negative, or unusable |

Do not compare EV/EBIT blindly across sectors. A semiconductor company, software company, bank, defense contractor, retailer, and energy company can all deserve different normal ranges.

Use EV/EBIT as a valuation check:

```text
Good setup: low EV/EBIT + strong F-Score + acceptable growth
Potential value trap: low EV/EBIT + weak F-Score + deteriorating growth
Potential overpay: high EV/EBIT + weak Magic Formula rank + falling momentum
```

## Buying Workflow

For a high-conviction buy candidate, prefer this combination:

```text
Composite rating: A or high B
F-Score: 6+ with most signals available
Magic Formula rank: favorable versus the universe and sector
EV/EBIT: reasonable versus sector and growth rate
Valuation grade: A or B
Quality grade: A or B
No stale-price or data-quality alert
```

The strongest candidates usually have the main rating and at least two benchmark indicators pointing in the same direction.

## Selling Workflow

Do not sell because one indicator looks bad in isolation. Consider trimming or selling when multiple signals deteriorate together:

```text
Composite rating falls from A/B to C/D
F-Score drops below 4
Magic Formula rank deteriorates materially
EV/EBIT expands far above peers without better growth
Quality or risk grade weakens
Price-target upside disappears
Original business thesis breaks
```

One bad datapoint is noise. A cluster of deteriorating quality, valuation, and rating signals is a real review trigger.

## Practical Mental Model

```text
F-Score = Is the company financially healthy?
Magic Formula = Is it a good business at a good price?
EV/EBIT = How expensive is the operating business?
Composite rating = What does the full model think across all factors?
```

For buying, require benchmark confirmation unless there is a strong and explicit growth thesis. For selling, look for fundamental deterioration plus valuation or rating deterioration, not a single noisy metric.
