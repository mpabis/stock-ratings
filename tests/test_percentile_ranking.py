from decimal import Decimal

from stock_rating.rating.percentile_ranking import (
    FactorScores,
    assign_percentile_grades,
    composite_value,
    grade_from_percentile,
    percentile_ranks,
)


def _scores(symbol: str, value: Decimal) -> FactorScores:
    # Flat profile so the composite equals `value` and ranking is easy to reason about.
    return FactorScores(
        symbol=symbol,
        valuation=value,
        quality=value,
        growth=value,
        momentum=value,
        risk=value,
        analyst_revision=value,
    )


def test_percentile_ranks_orders_values_low_to_high() -> None:
    ranks = percentile_ranks([Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40"), Decimal("50")])
    assert ranks == sorted(ranks)
    assert ranks[0] < ranks[-1]
    # Mid-rank percentiles for 5 distinct values: 0.1, 0.3, 0.5, 0.7, 0.9
    assert ranks[2] == Decimal("0.5")


def test_percentile_ranks_handles_ties_deterministically() -> None:
    ranks = percentile_ranks([Decimal("5"), Decimal("5"), Decimal("5"), Decimal("5")])
    assert all(rank == Decimal("0.5") for rank in ranks)


def test_single_element_universe_is_neutral() -> None:
    assert percentile_ranks([Decimal("42")]) == [Decimal("0.5")]


def test_empty_universe_returns_empty() -> None:
    assert percentile_ranks([]) == []
    assert assign_percentile_grades([]) == []


def test_grade_from_percentile_even_buckets() -> None:
    assert grade_from_percentile(Decimal("0.95")) == "A"
    assert grade_from_percentile(Decimal("0.80")) == "A"
    assert grade_from_percentile(Decimal("0.79")) == "B"
    assert grade_from_percentile(Decimal("0.60")) == "B"
    assert grade_from_percentile(Decimal("0.50")) == "C"
    assert grade_from_percentile(Decimal("0.40")) == "C"
    assert grade_from_percentile(Decimal("0.30")) == "D"
    assert grade_from_percentile(Decimal("0.20")) == "D"
    assert grade_from_percentile(Decimal("0.10")) == "F"
    assert grade_from_percentile(Decimal("0")) == "F"


def test_composite_value_uses_documented_weights() -> None:
    # All-50 profile must produce a composite of 50 (weights sum to 1.0).
    assert composite_value(_scores("X", Decimal("50"))) == Decimal("50.00")


def test_assign_percentile_grades_spreads_across_buckets() -> None:
    universe = [_scores(f"S{i}", Decimal(i)) for i in range(1, 11)]  # values 1..10
    graded = assign_percentile_grades(universe)

    by_symbol = {g.symbol: g for g in graded}
    # Top value -> A, bottom value -> F.
    assert by_symbol["S10"].composite_grade == "A"
    assert by_symbol["S1"].composite_grade == "F"
    # Order preserved and one grade per symbol.
    assert [g.symbol for g in graded] == [f"S{i}" for i in range(1, 11)]
    assert len(graded) == 10


def test_single_symbol_universe_grades_to_neutral_c() -> None:
    graded = assign_percentile_grades([_scores("ONLY", Decimal("73"))])
    assert len(graded) == 1
    assert graded[0].composite_percentile == Decimal("0.5")
    assert graded[0].composite_grade == "C"
    assert all(grade == "C" for grade in graded[0].factor_grades.values())


def test_grade_is_relative_not_absolute() -> None:
    # The same raw scores grade differently depending on the surrounding universe.
    target = _scores("TARGET", Decimal("60"))

    weak_peers = [target] + [_scores(f"W{i}", Decimal("10")) for i in range(9)]
    strong_peers = [target] + [_scores(f"H{i}", Decimal("95")) for i in range(9)]

    weak_grade = {g.symbol: g for g in assign_percentile_grades(weak_peers)}["TARGET"].composite_grade
    strong_grade = {g.symbol: g for g in assign_percentile_grades(strong_peers)}["TARGET"].composite_grade

    assert weak_grade == "A"  # best of a weak field
    assert strong_grade == "F"  # worst of a strong field
