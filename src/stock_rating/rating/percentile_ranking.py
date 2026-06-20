"""AAII-style cross-sectional percentile ranking of rating factors.

The per-symbol pass (``model_v1.compute_rating_breakdown``) produces absolute
0-100 factor sub-scores. This module takes the *whole universe* of those
sub-scores for a single date and assigns each symbol a percentile rank per
factor plus an A-F letter grade, following the AAII A+ convention of even
20% buckets (top 20% -> A ... bottom 20% -> F).

Design notes:
- All five factors are "higher is better" (including ``risk_score``, where a
  high value means *safer* in ``model_v1``), so percentiles share one
  direction and the weighted composite preserves it.
- The composite is the existing weighted sum of the *raw* sub-scores; that
  composite is then percentile-ranked across the universe to assign the final
  grade. (Weight-then-percentile, not percentile-then-weight — the smaller
  change from v4 and easier to reason about.)
- Grades are *relative to the tracked universe*, not absolute quality. Roughly
  20% of symbols are always graded "A".
"""

from dataclasses import dataclass
from decimal import Decimal


# Final-composite weights — the single source of truth, also imported by
# model_v1.compute_rating_breakdown so the per-symbol composite and the
# percentile composite always use identical weights.
COMPOSITE_WEIGHTS: dict[str, Decimal] = {
    "valuation": Decimal("0.225"),
    "quality": Decimal("0.225"),
    "growth": Decimal("0.18"),
    "momentum": Decimal("0.18"),
    "risk": Decimal("0.09"),
    "analyst_revision": Decimal("0.10"),
}

FACTORS = ("valuation", "quality", "growth", "momentum", "risk", "analyst_revision")


@dataclass(frozen=True)
class FactorScores:
    """Raw 0-100 factor sub-scores for one symbol (from the per-symbol pass)."""

    symbol: str
    valuation: Decimal
    quality: Decimal
    growth: Decimal
    momentum: Decimal
    risk: Decimal
    analyst_revision: Decimal


@dataclass(frozen=True)
class GradedRating:
    """Percentile ranks (0-1) and A-F letter grades for one symbol."""

    symbol: str
    composite_value: Decimal
    composite_percentile: Decimal
    composite_grade: str
    factor_percentiles: dict[str, Decimal]
    factor_grades: dict[str, str]


def percentile_ranks(values: list[Decimal]) -> list[Decimal]:
    """Mid-rank percentile of each value within ``values``, in [0, 1].

    Uses ``(count_strictly_below + 0.5 * count_equal) / n`` which handles ties
    deterministically and yields ~even buckets for a continuous distribution.
    A single-element universe yields 0.5 (neutral) rather than 0 or 1.
    """
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [Decimal("0.5")]

    ranks: list[Decimal] = []
    for value in values:
        below = sum(1 for other in values if other < value)
        equal = sum(1 for other in values if other == value)
        ranks.append((Decimal(below) + Decimal(equal) / Decimal(2)) / Decimal(n))
    return ranks


def grade_from_percentile(percentile: Decimal) -> str:
    """Map a [0, 1] percentile to an A-F letter using even 20% buckets."""
    if percentile >= Decimal("0.8"):
        return "A"
    if percentile >= Decimal("0.6"):
        return "B"
    if percentile >= Decimal("0.4"):
        return "C"
    if percentile >= Decimal("0.2"):
        return "D"
    return "F"


def composite_value(scores: FactorScores) -> Decimal:
    """Weighted sum of the raw sub-scores (same weights as model_v1)."""
    return (
        scores.valuation * COMPOSITE_WEIGHTS["valuation"]
        + scores.quality * COMPOSITE_WEIGHTS["quality"]
        + scores.growth * COMPOSITE_WEIGHTS["growth"]
        + scores.momentum * COMPOSITE_WEIGHTS["momentum"]
        + scores.risk * COMPOSITE_WEIGHTS["risk"]
        + scores.analyst_revision * COMPOSITE_WEIGHTS["analyst_revision"]
    )


def assign_percentile_grades(universe: list[FactorScores]) -> list[GradedRating]:
    """Assign per-factor and composite percentile grades across the universe.

    Returns one ``GradedRating`` per input symbol, preserving input order.
    Safe for an empty or single-symbol universe.
    """
    if not universe:
        return []

    factor_value_lists = {
        factor: [_factor_value(scores, factor) for scores in universe]
        for factor in FACTORS
    }
    factor_rank_lists = {
        factor: percentile_ranks(values)
        for factor, values in factor_value_lists.items()
    }

    composites = [composite_value(scores) for scores in universe]
    composite_ranks = percentile_ranks(composites)

    graded: list[GradedRating] = []
    for index, scores in enumerate(universe):
        factor_percentiles = {
            factor: factor_rank_lists[factor][index] for factor in FACTORS
        }
        factor_grades = {
            factor: grade_from_percentile(percentile)
            for factor, percentile in factor_percentiles.items()
        }
        graded.append(
            GradedRating(
                symbol=scores.symbol,
                composite_value=composites[index],
                composite_percentile=composite_ranks[index],
                composite_grade=grade_from_percentile(composite_ranks[index]),
                factor_percentiles=factor_percentiles,
                factor_grades=factor_grades,
            )
        )
    return graded


def _factor_value(scores: FactorScores, factor: str) -> Decimal:
    return getattr(scores, factor)
