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
        "model_version": "v4",
    }


def compute_rating_breakdown(features: list[FeatureValue]) -> RatingBreakdown:
    feature_map = {feature.feature_name: feature.feature_value for feature in features}
    intraday_return = Decimal(feature_map.get("intraday_return", Decimal("0")))
    one_day_return = Decimal(feature_map.get("one_day_return", Decimal("0")))
    daily_volume = Decimal(feature_map.get("daily_volume", Decimal("0")))
    five_day_return = _optional_decimal(feature_map, "five_day_return")
    ten_day_return = _optional_decimal(feature_map, "ten_day_return")
    twenty_day_return = _optional_decimal(feature_map, "twenty_day_return")
    sixty_day_return = _optional_decimal(feature_map, "sixty_day_return")
    one_hundred_day_return = _optional_decimal(feature_map, "one_hundred_day_return")
    twenty_day_volatility = _optional_decimal(feature_map, "twenty_day_volatility")
    twenty_day_max_drawdown = _optional_decimal(feature_map, "twenty_day_max_drawdown")
    yield_curve_slope = _optional_decimal(feature_map, "yield_curve_slope")
    net_margin = _optional_decimal(feature_map, "net_margin")
    cash_flow_margin = _optional_decimal(feature_map, "cash_flow_margin")
    return_on_assets = _optional_decimal(feature_map, "return_on_assets")
    debt_to_assets = _optional_decimal(feature_map, "debt_to_assets")
    earnings_yield = _optional_decimal(feature_map, "earnings_yield")
    book_to_price = _optional_decimal(feature_map, "book_to_price")
    revenue_growth_yoy = _optional_decimal(feature_map, "revenue_growth_yoy")
    net_income_growth_yoy = _optional_decimal(feature_map, "net_income_growth_yoy")
    operating_cash_flow_growth_yoy = _optional_decimal(feature_map, "operating_cash_flow_growth_yoy")

    liquidity_score = _clamp_decimal(Decimal("25") + (daily_volume / Decimal("200000")))
    short_term_trend_score = _clamp_decimal(Decimal("50") + one_day_return * Decimal("400") + intraday_return * Decimal("150"))
    reversal_score = _clamp_decimal(Decimal("55") - one_day_return * Decimal("250") - intraday_return * Decimal("100"))
    momentum_return = _first_decimal(
        one_hundred_day_return,
        sixty_day_return,
        twenty_day_return,
        ten_day_return,
        five_day_return,
        one_day_return,
    )

    valuation_components = [
        reversal_score * Decimal("0.50") + liquidity_score * Decimal("0.10") + Decimal("25")
    ]
    if earnings_yield is not None:
        valuation_components.append(_clamp_decimal(Decimal("45") + earnings_yield * Decimal("700")))
    if book_to_price is not None:
        valuation_components.append(_clamp_decimal(Decimal("35") + book_to_price * Decimal("80")))
    if net_margin is not None:
        valuation_components.append(_clamp_decimal(Decimal("45") + net_margin * Decimal("120")))
    if cash_flow_margin is not None:
        valuation_components.append(_clamp_decimal(Decimal("45") + cash_flow_margin * Decimal("100")))
    if debt_to_assets is not None:
        valuation_components.append(_clamp_decimal(Decimal("80") - debt_to_assets * Decimal("55")))
    valuation_score = _average_decimal(valuation_components)

    quality_components = [
        _clamp_decimal(Decimal("38") + liquidity_score * Decimal("0.45") - abs(intraday_return - one_day_return) * Decimal("350"))
    ]
    if net_margin is not None:
        quality_components.append(_clamp_decimal(Decimal("45") + net_margin * Decimal("220")))
    if cash_flow_margin is not None:
        quality_components.append(_clamp_decimal(Decimal("45") + cash_flow_margin * Decimal("220")))
    if return_on_assets is not None:
        quality_components.append(_clamp_decimal(Decimal("45") + return_on_assets * Decimal("450")))
    if debt_to_assets is not None:
        quality_components.append(_clamp_decimal(Decimal("80") - debt_to_assets * Decimal("65")))
    quality_score = _average_decimal(quality_components)

    growth_components = [short_term_trend_score]
    if revenue_growth_yoy is not None:
        growth_components.append(_clamp_decimal(Decimal("50") + revenue_growth_yoy * Decimal("100")))
    if net_income_growth_yoy is not None:
        growth_components.append(_clamp_decimal(Decimal("50") + net_income_growth_yoy * Decimal("70")))
    if operating_cash_flow_growth_yoy is not None:
        growth_components.append(_clamp_decimal(Decimal("50") + operating_cash_flow_growth_yoy * Decimal("80")))
    if momentum_return is not None:
        growth_components.append(_clamp_decimal(Decimal("50") + momentum_return * Decimal("90")))
    growth_score = _average_decimal(growth_components)

    momentum_score = _clamp_decimal(
        Decimal("50")
        + (momentum_return or one_day_return) * Decimal("120")
        + (twenty_day_return or Decimal("0")) * Decimal("60")
        + liquidity_score * Decimal("0.05")
    )

    risk_penalty = abs(one_day_return) * Decimal("250") + abs(intraday_return) * Decimal("150")
    if twenty_day_volatility is not None:
        risk_penalty += twenty_day_volatility * Decimal("450")
    if twenty_day_max_drawdown is not None:
        risk_penalty += twenty_day_max_drawdown * Decimal("250")
    risk_score = _clamp_decimal(Decimal("82") - risk_penalty + liquidity_score * Decimal("0.08"))

    risk_components = [risk_score]
    if debt_to_assets is not None:
        risk_components.append(_clamp_decimal(Decimal("85") - debt_to_assets * Decimal("75")))
    if cash_flow_margin is not None:
        risk_components.append(_clamp_decimal(Decimal("50") + cash_flow_margin * Decimal("180")))
    if net_margin is not None:
        risk_components.append(_clamp_decimal(Decimal("50") + net_margin * Decimal("160")))
    risk_score = _average_decimal(risk_components)

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
        model_version="v4",
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


def _first_decimal(*values: Decimal | None) -> Decimal | None:
    for value in values:
        if value is not None:
            return value
    return None


def _average_decimal(values: list[Decimal]) -> Decimal:
    if not values:
        return Decimal("50")
    return _clamp_decimal(sum(values) / Decimal(len(values)))
