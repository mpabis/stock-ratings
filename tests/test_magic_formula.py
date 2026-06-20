from datetime import date
from decimal import Decimal

from stock_rating.rating.magic_formula import (
    apply_magic_formula_ranks,
    rank_magic_formula,
)
from stock_rating.transform.features import FeatureValue, MagicFormulaInput


AS_OF = date(2026, 6, 20)


def _input(symbol: str, roic, ey, sector: str | None = None) -> MagicFormulaInput:
    return MagicFormulaInput(
        symbol=symbol,
        date=AS_OF,
        roic=Decimal(str(roic)),
        earnings_yield=Decimal(str(ey)),
        sector=sector,
    )


def test_combined_rank_best_on_both_metrics_is_first() -> None:
    entries = [
        _input("BEST", roic="0.40", ey="0.20"),   # rank 1 + rank 1 = 2
        _input("MID", roic="0.30", ey="0.10"),     # rank 2 + rank 2 = 4
        _input("WORST", roic="0.10", ey="0.05"),   # rank 3 + rank 3 = 6
    ]
    ranks = {r.symbol: r.combined_rank for r in rank_magic_formula(entries)}
    assert ranks["BEST"] == 1
    assert ranks["MID"] == 2
    assert ranks["WORST"] == 3


def test_combined_rank_balances_two_metrics() -> None:
    # A leads on ROIC, B leads on earnings yield -> they tie on combined sum.
    entries = [
        _input("A", roic="0.40", ey="0.05"),   # roic rank 1, ey rank 2 = 3
        _input("B", roic="0.20", ey="0.30"),   # roic rank 2, ey rank 1 = 3
    ]
    ranks = {r.symbol: r.combined_rank for r in rank_magic_formula(entries)}
    assert ranks["A"] == 1
    assert ranks["B"] == 1  # tie shares the best rank


def test_excludes_financials_and_utilities() -> None:
    entries = [
        _input("BANKCO", roic="0.99", ey="0.99", sector="Financials"),
        _input("POWERCO", roic="0.98", ey="0.98", sector="Utilities"),
        _input("TECHCO", roic="0.30", ey="0.10", sector="Information Technology"),
    ]
    ranks = rank_magic_formula(entries)
    symbols = {r.symbol for r in ranks}
    assert symbols == {"TECHCO"}
    assert ranks[0].combined_rank == 1


def test_unknown_sector_is_kept() -> None:
    entries = [_input("A", roic="0.30", ey="0.10", sector=None), _input("B", roic="0.20", ey="0.05", sector="")]
    ranks = {r.symbol: r.combined_rank for r in rank_magic_formula(entries)}
    assert set(ranks) == {"A", "B"}


def test_empty_and_all_excluded_return_empty() -> None:
    assert rank_magic_formula([]) == []
    assert rank_magic_formula([_input("BANKCO", roic="0.5", ey="0.5", sector="Financial Services")]) == []


def test_apply_magic_formula_ranks_persists_features() -> None:
    entries = [_input("A", roic="0.40", ey="0.20"), _input("B", roic="0.10", ey="0.05")]
    captured: dict[str, object] = {}

    def fake_persist(database_url: str, features: list[FeatureValue]) -> bool:
        captured["features"] = features
        return True

    count = apply_magic_formula_ranks(
        "postgresql://example", load_fn=lambda _url: entries, persist_fn=fake_persist
    )

    assert count == 2
    features = captured["features"]
    assert all(f.feature_name == "magic_formula_combined_rank" for f in features)
    by_symbol = {f.symbol: f.feature_value for f in features}
    assert by_symbol["A"] == Decimal("1")
    assert by_symbol["B"] == Decimal("2")


def test_apply_returns_zero_when_no_inputs() -> None:
    assert apply_magic_formula_ranks("postgresql://example", load_fn=lambda _url: [], persist_fn=lambda _u, _f: True) == 0


def test_apply_returns_zero_when_persist_fails() -> None:
    entries = [_input("A", roic="0.40", ey="0.20")]
    assert apply_magic_formula_ranks("postgresql://example", load_fn=lambda _url: entries, persist_fn=lambda _u, _f: False) == 0
