from datetime import date
from decimal import Decimal

from stock_rating.pipeline.daily import RefreshTask
from stock_rating.rating.model_v1 import build_rating_record, compute_rating_breakdown
from stock_rating.repository.ratings import persist_ratings
from stock_rating.transform.features import FeatureValue


class _FakeRatingsCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakeRatingsConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeRatingsCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeRatingsCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_compute_rating_breakdown_returns_bounded_score() -> None:
    features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.02"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.03"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]

    breakdown = compute_rating_breakdown(features)

    assert 0 <= breakdown.score <= 100
    assert breakdown.momentum_score > 50
    assert breakdown.growth_score > 50
    assert breakdown.risk_score > 0


def test_analyst_revision_score_zero_is_not_treated_as_neutral() -> None:
    # Regression: Decimal("0") is falsy, so `value or Decimal("50")` would wrongly
    # promote a worst-case analyst-revision score (0) to neutral (50).
    base = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.00"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.01"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]
    worst = base + [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="analyst_revision_score", feature_value=Decimal("0"), source_version="analyst_v1"),
    ]
    neutral = base + [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="analyst_revision_score", feature_value=Decimal("50"), source_version="analyst_v1"),
    ]

    worst_breakdown = compute_rating_breakdown(worst)
    neutral_breakdown = compute_rating_breakdown(neutral)

    assert worst_breakdown.analyst_revision_score == Decimal("0")
    assert worst_breakdown.score < neutral_breakdown.score


def test_build_rating_record_maps_to_schema_shape() -> None:
    task = RefreshTask(symbol="AAPL", refresh_tier=1, age_in_days=1, freshness_status="fresh")
    features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.02"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.03"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]

    record = build_rating_record(task, features)

    assert record.symbol == "AAPL"
    assert record.freshness_status == "fresh"
    assert record.model_version == "v6"
    assert record.rating_label


def test_rating_breakdown_penalizes_negative_returns() -> None:
    positive_features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.02"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.03"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]
    negative_features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("-0.03"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("-0.04"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]

    positive_breakdown = compute_rating_breakdown(positive_features)
    negative_breakdown = compute_rating_breakdown(negative_features)

    assert positive_breakdown.score > negative_breakdown.score
    assert positive_breakdown.growth_score > negative_breakdown.growth_score
    assert positive_breakdown.momentum_score > negative_breakdown.momentum_score
    assert positive_breakdown.risk_score > negative_breakdown.risk_score


def test_build_rating_record_spreads_labels_for_different_feature_profiles() -> None:
    task = RefreshTask(symbol="AAPL", refresh_tier=1, age_in_days=1, freshness_status="fresh")
    scenarios = {
        "steady": [
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.01"), source_version="v1"),
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.15"), source_version="v1"),
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("50000000"), source_version="v1"),
        ],
        "balanced": [
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.00"), source_version="v1"),
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.01"), source_version="v1"),
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
        ],
        "stressed": [
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("-0.08"), source_version="v1"),
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("-0.20"), source_version="v1"),
            FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("10000"), source_version="v1"),
        ],
    }

    labels = {
        name: build_rating_record(task, features).rating_label
        for name, features in scenarios.items()
    }

    assert labels == {
        "steady": "C / Neutral",
        "balanced": "C / Neutral",
        "stressed": "F / Very Unattractive",
    }


def test_persist_ratings_inserts_rows() -> None:
    task = RefreshTask(symbol="AAPL", refresh_tier=1, age_in_days=1, freshness_status="fresh")
    features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.02"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.03"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]
    record = build_rating_record(task, features)
    fake_connection = _FakeRatingsConnection()

    persisted = persist_ratings(
        database_url="postgresql://example",
        ratings=[record],
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executemany_calls) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True


def test_fundamentals_improve_breakdown_for_same_price_profile() -> None:
    base_features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.00"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.01"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]
    strong_fundamentals = base_features + [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="net_margin", feature_value=Decimal("0.24"), source_version="fundamentals_v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="cash_flow_margin", feature_value=Decimal("0.22"), source_version="fundamentals_v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="return_on_assets", feature_value=Decimal("0.11"), source_version="fundamentals_v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="debt_to_assets", feature_value=Decimal("0.28"), source_version="fundamentals_v1"),
    ]
    weak_fundamentals = base_features + [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="net_margin", feature_value=Decimal("-0.08"), source_version="fundamentals_v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="cash_flow_margin", feature_value=Decimal("-0.03"), source_version="fundamentals_v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="return_on_assets", feature_value=Decimal("-0.02"), source_version="fundamentals_v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="debt_to_assets", feature_value=Decimal("0.92"), source_version="fundamentals_v1"),
    ]

    strong_breakdown = compute_rating_breakdown(strong_fundamentals)
    weak_breakdown = compute_rating_breakdown(weak_fundamentals)

    assert strong_breakdown.score > weak_breakdown.score
    assert strong_breakdown.valuation_score > weak_breakdown.valuation_score
    assert strong_breakdown.quality_score > weak_breakdown.quality_score
    assert strong_breakdown.risk_score > weak_breakdown.risk_score


def test_inverted_yield_curve_penalizes_growth_and_risk() -> None:
    base_features = [
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="intraday_return", feature_value=Decimal("0.00"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="one_day_return", feature_value=Decimal("0.01"), source_version="v1"),
        FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="daily_volume", feature_value=Decimal("1500000"), source_version="v1"),
    ]

    positive_curve = compute_rating_breakdown(
        base_features
        + [FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="yield_curve_slope", feature_value=Decimal("1.20"), source_version="macro_v1")]
    )
    inverted_curve = compute_rating_breakdown(
        base_features
        + [FeatureValue(symbol="AAPL", date=date(2026, 5, 27), feature_name="yield_curve_slope", feature_value=Decimal("-0.40"), source_version="macro_v1")]
    )

    assert positive_curve.score > inverted_curve.score
    assert positive_curve.growth_score > inverted_curve.growth_score
    assert positive_curve.risk_score > inverted_curve.risk_score
