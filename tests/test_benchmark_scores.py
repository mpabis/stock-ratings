from datetime import date
from decimal import Decimal

from stock_rating.ingest.sec_companyfacts import FundamentalFact
from stock_rating.transform.benchmark_scores import compute_benchmark_features


AS_OF = date(2026, 6, 20)


def _fact(metric: str, value, fiscal_year: int) -> FundamentalFact:
    return FundamentalFact(
        cik="0000000000",
        symbol="TEST",
        fiscal_period="FY",
        fiscal_year=fiscal_year,
        form="10-K",
        metric=metric,
        value=Decimal(str(value)),
        unit="USD",
        filed_at=None,
        period_end=date(fiscal_year, 12, 31),
    )


def _by_name(features) -> dict[str, Decimal]:
    return {feature.feature_name: feature.feature_value for feature in features}


def _strong_two_year_facts() -> list[FundamentalFact]:
    # Every Piotroski signal is designed to be TRUE (latest year improves on prior).
    return [
        _fact("net_income", 120, 2025), _fact("net_income", 80, 2024),
        _fact("assets", 1000, 2025), _fact("assets", 900, 2024),
        _fact("operating_cash_flow", 150, 2025), _fact("operating_cash_flow", 100, 2024),
        _fact("revenue", 1000, 2025), _fact("revenue", 800, 2024),
        _fact("long_term_debt", 100, 2025), _fact("long_term_debt", 200, 2024),
        _fact("current_assets", 500, 2025), _fact("current_assets", 400, 2024),
        _fact("current_liabilities", 200, 2025), _fact("current_liabilities", 250, 2024),
        _fact("shares_diluted", 1000, 2025), _fact("shares_diluted", 1000, 2024),
        _fact("gross_profit", 600, 2025), _fact("gross_profit", 400, 2024),
        _fact("operating_income", 200, 2025), _fact("operating_income", 150, 2024),
        _fact("ppe_net", 400, 2025),
        _fact("long_term_debt_current", 50, 2025),
        _fact("cash", 300, 2025),
    ]


def test_perfect_piotroski_fscore_is_nine() -> None:
    features = _by_name(compute_benchmark_features("TEST", AS_OF, _strong_two_year_facts(), Decimal("50")))
    assert features["piotroski_fscore"] == Decimal("9")
    assert features["piotroski_signals_available"] == Decimal("9")


def test_magic_formula_and_acquirers_multiple_computed() -> None:
    features = _by_name(compute_benchmark_features("TEST", AS_OF, _strong_two_year_facts(), Decimal("50")))
    # ROIC = EBIT / (working capital + net fixed assets) = 200 / ((500-200)+400) = 200/700
    assert features["magic_formula_roic"] == Decimal("200") / Decimal("700")
    # EV = 50*1000 + 100 + 50 - 300 = 49850
    assert features["magic_formula_earnings_yield"] == Decimal("200") / Decimal("49850")
    assert features["acquirers_multiple"] == Decimal("49850") / Decimal("200")


def test_signals_degrade_gracefully_with_only_one_year() -> None:
    # Only single-year, profitability-style inputs: rising/Δ signals can't be evaluated.
    facts = [
        _fact("net_income", 120, 2025),
        _fact("assets", 1000, 2025),
        _fact("operating_cash_flow", 150, 2025),
    ]
    features = _by_name(compute_benchmark_features("TEST", AS_OF, facts, Decimal("50")))
    # Signals 1 (ROA>0), 2 (CFO>0), 4 (accruals) are computable; the six Δ/ratio signals are not.
    assert features["piotroski_signals_available"] == Decimal("3")
    assert features["piotroski_fscore"] == Decimal("3")


def test_gross_margin_falls_back_to_revenue_minus_cost_of_revenue() -> None:
    facts = [
        _fact("revenue", 1000, 2025), _fact("revenue", 800, 2024),
        _fact("cost_of_revenue", 400, 2025), _fact("cost_of_revenue", 400, 2024),
        _fact("assets", 1000, 2025), _fact("assets", 900, 2024),
        _fact("net_income", 120, 2025), _fact("net_income", 80, 2024),
    ]
    features = _by_name(compute_benchmark_features("TEST", AS_OF, facts, Decimal("50")))
    # Gross margin signal is now evaluable: 2025 (1000-400)/1000=0.6 > 2024 (800-400)/800=0.5.
    assert "piotroski_fscore" in features
    assert features["piotroski_signals_available"] >= Decimal("4")


def test_no_facts_yields_no_benchmark_features() -> None:
    assert compute_benchmark_features("TEST", AS_OF, [], Decimal("50")) == []


def test_roic_skipped_when_invested_capital_nonpositive() -> None:
    facts = [
        _fact("operating_income", 200, 2025),
        _fact("current_assets", 100, 2025),
        _fact("current_liabilities", 500, 2025),  # working capital negative
        _fact("ppe_net", 50, 2025),  # 100-500+50 = -350 < 0
    ]
    features = _by_name(compute_benchmark_features("TEST", AS_OF, facts, Decimal("50")))
    assert "magic_formula_roic" not in features


def test_enterprise_value_requires_price_and_shares() -> None:
    facts = [_fact("operating_income", 200, 2025), _fact("shares_diluted", 1000, 2025)]
    # No price -> no EV -> no earnings yield / acquirer's multiple.
    features = _by_name(compute_benchmark_features("TEST", AS_OF, facts, None))
    assert "magic_formula_earnings_yield" not in features
    assert "acquirers_multiple" not in features
