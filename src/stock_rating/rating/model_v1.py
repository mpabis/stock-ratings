from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from stock_rating.rating.explanations import build_rating_explanation
from stock_rating.rating.scoring import map_score_to_label
from stock_rating.repository.ratings import RatingRecord
from stock_rating.transform.features import FeatureValue


class RefreshTaskLike(Protocol):
    symbol: str
    freshness_status: str


@dataclass(frozen=True)
class RatingBreakdown:
    score: int
    valuation_score: Decimal
    quality_score: Decimal
    growth_score: Decimal
    momentum_score: Decimal
    risk_score: Decimal


def build_rating(task: RefreshTaskLike, raw_score: int) -> dict[str, object]:
    mapped = map_score_to_label(raw_score)
    return {
        "symbol": task.symbol,
        "score": mapped.score,
        "label": mapped.label,
        "freshness_status": task.freshness_status,
        "explanation": build_rating_explanation(task.symbol, task.freshness_status),
        "model_version": "v3",
    }


def compute_rating_breakdown(features: list[FeatureValue]) -> RatingBreakdown:
    feature_map = {feature.feature_name: feature.feature_value for feature in features}
    intraday_return = Decimal(feature_map.get("intraday_return", Decimal("0")))
    one_day_return = Decimal(feature_map.get("one_day_return", Decimal("0")))
    daily_volume = Decimal(feature_map.get("daily_volume", Decimal("0")))
    yield_curve_slope = _optional_decimal(feature_map, "yield_curve_slope")
    net_margin = _optional_decimal(feature_map, "net_margin")
    cash_flow_margin = _optional_decimal(feature_map, "cash_flow_margin")
    return_on_assets = _optional_decimal(feature_map, "return_on_assets")
    debt_to_assets = _optional_decimal(feature_map, "debt_to_assets")

    liquidity_score = _clamp_decimal(Decimal("25") + (daily_volume / Decimal("200000")))
    trend_score = _clamp_decimal(Decimal("50") + one_day_return * Decimal("1800") + intraday_return * Decimal("700"))
    reversal_score = _clamp_decimal(Decimal("50") - one_day_return * Decimal("1200") - intraday_return * Decimal("400"))
    stability_penalty = (abs(one_day_return) * Decimal("1600")) + (abs(intraday_return) * Decimal("1000"))

    valuation_score = _clamp_decimal((reversal_score * Decimal("0.75")) + (liquidity_score * Decimal("0.25")))
    quality_score = _clamp_decimal(Decimal("30") + liquidity_score * Decimal("0.7") - abs(intraday_return - one_day_return) * Decimal("900"))
    growth_score = _clamp_decimal(Decimal("25") + (trend_score * Decimal("0.75")) + max(one_day_return, Decimal("0")) * Decimal("500"))
    momentum_score = _clamp_decimal((trend_score * Decimal("0.8")) + (liquidity_score * Decimal("0.2")))
    risk_score = _clamp_decimal(Decimal("85") - stability_penalty + (liquidity_score * Decimal("0.15")))

    if any(value is not None for value in (net_margin, cash_flow_margin, return_on_assets, debt_to_assets)):
        profitability_score = _clamp_decimal(Decimal("50") + (net_margin or Decimal("0")) * Decimal("250"))
        cash_generation_score = _clamp_decimal(Decimal("50") + (cash_flow_margin or Decimal("0")) * Decimal("220"))
        asset_efficiency_score = _clamp_decimal(Decimal("50") + (return_on_assets or Decimal("0")) * Decimal("500"))
        leverage_score = _clamp_decimal(Decimal("80") - (debt_to_assets or Decimal("0.5")) * Decimal("60"))

        valuation_score = _clamp_decimal(
            valuation_score * Decimal("0.65")
            + profitability_score * Decimal("0.20")
            + leverage_score * Decimal("0.15")
        )
        quality_score = _clamp_decimal(
            quality_score * Decimal("0.45")
            + profitability_score * Decimal("0.20")
            + cash_generation_score * Decimal("0.20")
            + asset_efficiency_score * Decimal("0.15")
        )
        growth_score = _clamp_decimal(
            growth_score * Decimal("0.70")
            + profitability_score * Decimal("0.20")
            + cash_generation_score * Decimal("0.10")
        )
        risk_score = _clamp_decimal(
            risk_score * Decimal("0.55")
            + cash_generation_score * Decimal("0.15")
            + leverage_score * Decimal("0.30")
        )

    if yield_curve_slope is not None:
        macro_risk_score = _clamp_decimal(Decimal("55") + yield_curve_slope * Decimal("12"))
        macro_growth_score = _clamp_decimal(Decimal("50") + yield_curve_slope * Decimal("8"))
        growth_score = _clamp_decimal(growth_score * Decimal("0.75") + macro_growth_score * Decimal("0.25"))
        risk_score = _clamp_decimal(risk_score * Decimal("0.75") + macro_risk_score * Decimal("0.25"))

    final_score = int(
        round(
            float(
                valuation_score * Decimal("0.25")
                + quality_score * Decimal("0.25")
                + growth_score * Decimal("0.20")
                + momentum_score * Decimal("0.20")
                + risk_score * Decimal("0.10")
            )
        )
    )

    return RatingBreakdown(
        score=max(0, min(100, final_score)),
        valuation_score=valuation_score,
        quality_score=quality_score,
        growth_score=growth_score,
        momentum_score=momentum_score,
        risk_score=risk_score,
    )


def build_rating_record(task: RefreshTaskLike, features: list[FeatureValue]) -> RatingRecord:
    latest_date = max(feature.date for feature in features)
    breakdown = compute_rating_breakdown(features)
    mapped = map_score_to_label(breakdown.score)
    explanation = build_rating_explanation(task.symbol, task.freshness_status)
    explanation["feature_names"] = [feature.feature_name for feature in features]

    return RatingRecord(
        symbol=task.symbol,
        date=latest_date,
        rating_score=mapped.score,
        rating_label=mapped.label,
        valuation_score=breakdown.valuation_score,
        quality_score=breakdown.quality_score,
        growth_score=breakdown.growth_score,
        momentum_score=breakdown.momentum_score,
        risk_score=breakdown.risk_score,
        explanation_json=explanation,
        model_version="v3",
        freshness_status=task.freshness_status,
        freshest_input_date=latest_date,
    )


def _clamp_decimal(value: Decimal) -> Decimal:
    if value < 0:
        return Decimal("0")
    if value > 100:
        return Decimal("100")
    return value


def _optional_decimal(feature_map: dict[str, Decimal], key: str) -> Decimal | None:
    if key not in feature_map:
        return None
    return Decimal(feature_map[key])
