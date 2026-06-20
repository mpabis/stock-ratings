"""Analyst estimate-revisions / sentiment-momentum factor.

The project has no true forward EPS-estimate revisions on free data, so this is
a *proxy*: the trailing change in analyst opinion from `analyst_consensus_daily`
history — the change in `suggestion_score` and in `analyst_target_price` between
the two most recent snapshots, averaged across providers (Alpha Vantage, Finnhub).

Emitted features (only when computable history exists):
- `analyst_suggestion_score_delta` — avg (latest - prior) suggestion_score
- `analyst_target_price_change_pct` — avg (latest - prior) / prior target price
- `analyst_revision_score` — 0-100 sub-score consumed by the composite

When a symbol has no analyst history (or a single snapshot), no features are
emitted; the composite defaults the factor to a neutral 50.
"""

from datetime import date
from decimal import Decimal

from stock_rating.repository.analyst import AnalystConsensusPoint
from stock_rating.transform.features import FeatureValue


ANALYST_SOURCE_VERSION = "analyst_v1"

# Sub-score mapping constants (documented in docs/rating_methodology.md):
# a one-notch suggestion-score improvement adds 15 points; a +10% target-price
# revision adds 10 points; centred on a neutral 50.
SUGGESTION_DELTA_WEIGHT = Decimal("15")
TARGET_CHANGE_WEIGHT = Decimal("100")


def compute_analyst_revision_features(
    symbol: str,
    as_of: date,
    snapshots_by_source: dict[str, list[AnalystConsensusPoint]],
) -> list[FeatureValue]:
    suggestion_deltas: list[Decimal] = []
    target_changes: list[Decimal] = []

    for points in snapshots_by_source.values():
        if len(points) < 2:
            continue
        latest, prior = points[0], points[1]

        if latest.suggestion_score is not None and prior.suggestion_score is not None:
            suggestion_deltas.append(latest.suggestion_score - prior.suggestion_score)

        if (
            latest.analyst_target_price is not None
            and prior.analyst_target_price is not None
            and prior.analyst_target_price != 0
        ):
            target_changes.append(
                (latest.analyst_target_price - prior.analyst_target_price) / prior.analyst_target_price
            )

    if not suggestion_deltas and not target_changes:
        return []

    avg_suggestion_delta = _average(suggestion_deltas)
    avg_target_change = _average(target_changes)

    revision_score = _clamp(
        Decimal("50")
        + avg_suggestion_delta * SUGGESTION_DELTA_WEIGHT
        + avg_target_change * TARGET_CHANGE_WEIGHT
    )

    features = [_feature(symbol, as_of, "analyst_revision_score", revision_score)]
    if suggestion_deltas:
        features.append(_feature(symbol, as_of, "analyst_suggestion_score_delta", avg_suggestion_delta))
    if target_changes:
        features.append(_feature(symbol, as_of, "analyst_target_price_change_pct", avg_target_change))
    return features


def _average(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("0")
    return sum(values) / Decimal(len(values))


def _clamp(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal("0")
    if value > 100:
        return Decimal("100")
    return value


def _feature(symbol: str, as_of: date, name: str, value: Decimal) -> FeatureValue:
    return FeatureValue(
        symbol=symbol,
        date=as_of,
        feature_name=name,
        feature_value=value,
        source_version=ANALYST_SOURCE_VERSION,
    )
