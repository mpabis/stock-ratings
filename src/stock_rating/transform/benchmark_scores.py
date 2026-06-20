"""Externally-validated benchmark scores: Piotroski F-Score, Magic Formula
inputs (ROIC + EBIT earnings yield), and the Acquirer's Multiple (EV/EBIT).

These are kept SEPARATE from the v5 composite — they are interpretable
benchmarks with published backtests, not inputs to the weighted score. Each is
emitted as a FeatureValue and persisted to features_daily alongside the others;
compute_rating_breakdown ignores feature names it doesn't recognize, so they do
not affect the composite.

Free-data caveats (documented in docs/rating_methodology.md):
- EBIT is approximated by us-gaap OperatingIncomeLoss.
- Piotroski signals that lack the required inputs score 0 and are excluded from
  the "signals available" count, per the story's graceful-degradation rule.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stock_rating.ingest.sec_companyfacts import FundamentalFact
from stock_rating.transform.features import FeatureValue
from stock_rating.transform.fundamentals import annual_values_by_metric


BENCHMARK_SOURCE_VERSION = "benchmark_v1"


@dataclass(frozen=True)
class _Annual:
    """Latest and prior annual values for a single metric."""

    latest: Decimal | None
    prior: Decimal | None


def compute_benchmark_features(
    symbol: str,
    as_of: date,
    facts: list[FundamentalFact],
    latest_price: Decimal | None = None,
) -> list[FeatureValue]:
    metrics = _annual_metrics(facts)

    features: list[FeatureValue] = []

    fscore, signals_available = _piotroski_fscore(metrics)
    if signals_available > 0:
        features.append(_feature(symbol, as_of, "piotroski_fscore", Decimal(fscore)))
        features.append(_feature(symbol, as_of, "piotroski_signals_available", Decimal(signals_available)))

    ebit = _latest(metrics, "operating_income")
    enterprise_value = _enterprise_value(metrics, latest_price)

    roic = _magic_formula_roic(metrics, ebit)
    if roic is not None:
        features.append(_feature(symbol, as_of, "magic_formula_roic", roic))

    if ebit is not None and enterprise_value is not None and enterprise_value > 0:
        features.append(
            _feature(symbol, as_of, "magic_formula_earnings_yield", ebit / enterprise_value)
        )

    if ebit is not None and ebit > 0 and enterprise_value is not None:
        features.append(_feature(symbol, as_of, "acquirers_multiple", enterprise_value / ebit))

    return features


def _piotroski_fscore(metrics: dict[str, _Annual]) -> tuple[int, int]:
    """Return (score, signals_available). Each computable signal contributes 0/1.

    Signals whose inputs are missing are skipped (neither scored nor counted),
    so a low signals_available flags low confidence in the score.
    """
    score = 0
    available = 0

    net_income = _latest(metrics, "net_income")
    assets = _latest(metrics, "assets")
    prior_net_income = _prior(metrics, "net_income")
    prior_assets = _prior(metrics, "assets")
    cfo = _latest(metrics, "operating_cash_flow")
    revenue = _latest(metrics, "revenue")
    prior_revenue = _prior(metrics, "revenue")

    roa = _safe_div(net_income, assets)
    prior_roa = _safe_div(prior_net_income, prior_assets)

    # 1. Positive return on assets.
    if roa is not None:
        available += 1
        if roa > 0:
            score += 1
    # 2. Positive operating cash flow.
    if cfo is not None:
        available += 1
        if cfo > 0:
            score += 1
    # 3. Rising ROA.
    if roa is not None and prior_roa is not None:
        available += 1
        if roa > prior_roa:
            score += 1
    # 4. Accruals: cash flow exceeds net income.
    if cfo is not None and net_income is not None:
        available += 1
        if cfo > net_income:
            score += 1
    # 5. Falling long-term leverage (long-term debt / assets).
    leverage = _safe_div(_latest(metrics, "long_term_debt"), assets)
    prior_leverage = _safe_div(_prior(metrics, "long_term_debt"), prior_assets)
    if leverage is not None and prior_leverage is not None:
        available += 1
        if leverage < prior_leverage:
            score += 1
    # 6. Rising current ratio.
    current_ratio = _safe_div(_latest(metrics, "current_assets"), _latest(metrics, "current_liabilities"))
    prior_current_ratio = _safe_div(_prior(metrics, "current_assets"), _prior(metrics, "current_liabilities"))
    if current_ratio is not None and prior_current_ratio is not None:
        available += 1
        if current_ratio > prior_current_ratio:
            score += 1
    # 7. No new shares issued (diluted share count did not rise).
    shares = _latest(metrics, "shares_diluted")
    prior_shares = _prior(metrics, "shares_diluted")
    if shares is not None and prior_shares is not None:
        available += 1
        if shares <= prior_shares:
            score += 1
    # 8. Rising gross margin.
    gross_margin = _safe_div(_gross_profit(metrics, latest=True), revenue)
    prior_gross_margin = _safe_div(_gross_profit(metrics, latest=False), prior_revenue)
    if gross_margin is not None and prior_gross_margin is not None:
        available += 1
        if gross_margin > prior_gross_margin:
            score += 1
    # 9. Rising asset turnover (revenue / assets).
    asset_turnover = _safe_div(revenue, assets)
    prior_asset_turnover = _safe_div(prior_revenue, prior_assets)
    if asset_turnover is not None and prior_asset_turnover is not None:
        available += 1
        if asset_turnover > prior_asset_turnover:
            score += 1

    return score, available


def _magic_formula_roic(metrics: dict[str, _Annual], ebit: Decimal | None) -> Decimal | None:
    """ROIC = EBIT / (net working capital + net fixed assets)."""
    if ebit is None:
        return None
    current_assets = _latest(metrics, "current_assets")
    current_liabilities = _latest(metrics, "current_liabilities")
    ppe_net = _latest(metrics, "ppe_net")
    if current_assets is None or current_liabilities is None or ppe_net is None:
        return None
    invested_capital = (current_assets - current_liabilities) + ppe_net
    if invested_capital <= 0:
        return None
    return ebit / invested_capital


def _enterprise_value(metrics: dict[str, _Annual], latest_price: Decimal | None) -> Decimal | None:
    """EV = market cap + total debt - cash. Returns None without a market cap."""
    shares = _latest(metrics, "shares_diluted")
    if latest_price is None or latest_price <= 0 or shares is None or shares <= 0:
        return None
    market_cap = latest_price * shares
    long_term_debt = _latest(metrics, "long_term_debt") or Decimal("0")
    long_term_debt_current = _latest(metrics, "long_term_debt_current") or Decimal("0")
    cash = _latest(metrics, "cash") or Decimal("0")
    return market_cap + long_term_debt + long_term_debt_current - cash


def _gross_profit(metrics: dict[str, _Annual], latest: bool) -> Decimal | None:
    """Gross profit, falling back to revenue - cost_of_revenue when absent."""
    pick = _latest if latest else _prior
    gross_profit = pick(metrics, "gross_profit")
    if gross_profit is not None:
        return gross_profit
    revenue = pick(metrics, "revenue")
    cost_of_revenue = pick(metrics, "cost_of_revenue")
    if revenue is None or cost_of_revenue is None:
        return None
    return revenue - cost_of_revenue


def _annual_metrics(facts: list[FundamentalFact]) -> dict[str, _Annual]:
    return {
        metric: _Annual(
            latest=values[0] if values else None,
            prior=values[1] if len(values) > 1 else None,
        )
        for metric, values in annual_values_by_metric(facts).items()
    }


def _latest(metrics: dict[str, _Annual], metric: str) -> Decimal | None:
    entry = metrics.get(metric)
    return entry.latest if entry else None


def _prior(metrics: dict[str, _Annual], metric: str) -> Decimal | None:
    entry = metrics.get(metric)
    return entry.prior if entry else None


def _safe_div(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _feature(symbol: str, as_of: date, name: str, value: Decimal) -> FeatureValue:
    return FeatureValue(
        symbol=symbol,
        date=as_of,
        feature_name=name,
        feature_value=value,
        source_version=BENCHMARK_SOURCE_VERSION,
    )
