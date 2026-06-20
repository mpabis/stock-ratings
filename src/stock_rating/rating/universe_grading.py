"""Cross-sectional (pass 2) percentile grading over the rated universe.

The per-symbol pass writes absolute factor sub-scores. This step reads the
latest rated row per active symbol, ranks every symbol against the universe,
and writes universe-relative percentiles + A-F grades back onto those rows.
"""

from decimal import ROUND_HALF_UP, Decimal

from stock_rating.rating.percentile_ranking import FactorScores, assign_percentile_grades
from stock_rating.rating.scoring import label_for_grade
from stock_rating.repository.ratings import (
    LatestFactorScore,
    PercentileGradeUpdate,
    load_latest_factor_scores,
    persist_percentile_grades,
)


def build_percentile_updates(
    latest_scores: list[LatestFactorScore],
    model_version: str,
) -> list[PercentileGradeUpdate]:
    """Pure transform: latest factor scores -> percentile-grade updates.

    The composite ``rating_score`` is rescaled to 0-100 from the composite
    percentile so the report's ``order by rating_score desc`` still ranks the
    universe top-to-bottom under the new relative grading.
    """
    universe = [
        FactorScores(
            symbol=score.symbol,
            valuation=score.valuation_score,
            quality=score.quality_score,
            growth=score.growth_score,
            momentum=score.momentum_score,
            risk=score.risk_score,
            analyst_revision=score.analyst_revision_score,
        )
        for score in latest_scores
    ]
    graded_by_symbol = {graded.symbol: graded for graded in assign_percentile_grades(universe)}

    updates: list[PercentileGradeUpdate] = []
    for score in latest_scores:
        graded = graded_by_symbol[score.symbol]
        updates.append(
            PercentileGradeUpdate(
                symbol=score.symbol,
                date=score.date,
                model_version=model_version,
                rating_score=int(
                    (graded.composite_percentile * 100).to_integral_value(rounding=ROUND_HALF_UP)
                ),
                rating_label=label_for_grade(graded.composite_grade),
                composite_percentile=graded.composite_percentile,
                valuation_percentile=graded.factor_percentiles["valuation"],
                quality_percentile=graded.factor_percentiles["quality"],
                growth_percentile=graded.factor_percentiles["growth"],
                momentum_percentile=graded.factor_percentiles["momentum"],
                risk_percentile=graded.factor_percentiles["risk"],
                analyst_revision_percentile=graded.factor_percentiles["analyst_revision"],
                valuation_grade=graded.factor_grades["valuation"],
                quality_grade=graded.factor_grades["quality"],
                growth_grade=graded.factor_grades["growth"],
                momentum_grade=graded.factor_grades["momentum"],
                risk_grade=graded.factor_grades["risk"],
                analyst_revision_grade=graded.factor_grades["analyst_revision"],
            )
        )
    return updates


def apply_universe_percentile_grades(
    database_url: str,
    model_version: str,
    load_fn=load_latest_factor_scores,
    persist_fn=persist_percentile_grades,
) -> int:
    """Run the percentile pass; returns the number of symbols graded.

    Operates over the full active universe's latest available scores (not just
    symbols refreshed this run), so partial multi-run refreshes still yield a
    coherent ranking. Returns 0 when there is nothing to rank.
    """
    latest_scores = load_fn(database_url, model_version)
    if not latest_scores:
        return 0
    updates = build_percentile_updates(latest_scores, model_version)
    persisted = persist_fn(database_url, updates)
    return len(updates) if persisted else 0
