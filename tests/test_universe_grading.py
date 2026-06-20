from datetime import date
from decimal import Decimal

from stock_rating.rating.universe_grading import (
    apply_universe_percentile_grades,
    build_percentile_updates,
)
from stock_rating.repository.ratings import (
    LatestFactorScore,
    PercentileGradeUpdate,
    persist_percentile_grades,
)


def _latest(symbol: str, value: Decimal) -> LatestFactorScore:
    return LatestFactorScore(
        symbol=symbol,
        date=date(2026, 6, 20),
        valuation_score=value,
        quality_score=value,
        growth_score=value,
        momentum_score=value,
        risk_score=value,
    )


def test_build_percentile_updates_maps_grades_and_rescales_score() -> None:
    latest = [_latest(f"S{i}", Decimal(i)) for i in range(1, 11)]
    updates = build_percentile_updates(latest, "v5")

    by_symbol = {u.symbol: u for u in updates}
    assert by_symbol["S10"].rating_label == "A / Very Attractive"
    assert by_symbol["S1"].rating_label == "F / Very Unattractive"
    # rating_score is the composite percentile rescaled to 0-100.
    # Top of a 10-symbol universe: mid-rank percentile (9 + 0.5)/10 = 0.95 -> 95.
    assert by_symbol["S10"].composite_percentile == Decimal("0.95")
    assert by_symbol["S10"].rating_score == 95
    assert all(u.model_version == "v5" for u in updates)
    assert all(u.date == date(2026, 6, 20) for u in updates)


def test_build_percentile_updates_carries_factor_grades() -> None:
    updates = build_percentile_updates([_latest("ONLY", Decimal("50"))], "v5")
    assert len(updates) == 1
    only = updates[0]
    # Single-symbol universe -> neutral C everywhere.
    assert only.rating_label == "C / Neutral"
    assert only.valuation_grade == "C"
    assert only.risk_grade == "C"
    assert only.composite_percentile == Decimal("0.5")


def test_apply_universe_percentile_grades_returns_count() -> None:
    latest = [_latest("AAA", Decimal("10")), _latest("BBB", Decimal("90"))]
    captured: dict[str, object] = {}

    def fake_load(database_url: str, model_version: str) -> list[LatestFactorScore]:
        captured["model_version"] = model_version
        return latest

    def fake_persist(database_url: str, updates: list[PercentileGradeUpdate]) -> bool:
        captured["updates"] = updates
        return True

    count = apply_universe_percentile_grades(
        "postgresql://example", "v5", load_fn=fake_load, persist_fn=fake_persist
    )

    assert count == 2
    assert captured["model_version"] == "v5"
    assert len(captured["updates"]) == 2


def test_apply_universe_percentile_grades_handles_empty_universe() -> None:
    count = apply_universe_percentile_grades(
        "postgresql://example",
        "v5",
        load_fn=lambda _url, _v: [],
        persist_fn=lambda _url, _u: True,
    )
    assert count == 0


def test_apply_returns_zero_when_persist_fails() -> None:
    count = apply_universe_percentile_grades(
        "postgresql://example",
        "v5",
        load_fn=lambda _url, _v: [_latest("AAA", Decimal("50"))],
        persist_fn=lambda _url, _u: False,
    )
    assert count == 0


class _FakeCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_persist_percentile_grades_issues_update() -> None:
    updates = build_percentile_updates([_latest("AAA", Decimal("10")), _latest("BBB", Decimal("90"))], "v5")
    connection = _FakeConnection()

    persisted = persist_percentile_grades(
        "postgresql://example", updates, connect_fn=lambda _: connection
    )

    assert persisted is True
    assert len(connection.cursor_instance.executemany_calls) == 1
    query, params = connection.cursor_instance.executemany_calls[0]
    assert "update ratings_daily" in query
    assert len(params) == 2
    assert connection.committed is True
    assert connection.closed is True


def test_persist_percentile_grades_no_updates_returns_false() -> None:
    assert persist_percentile_grades("postgresql://example", [], connect_fn=lambda _: _FakeConnection()) is False
