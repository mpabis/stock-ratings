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
    assert record.model_version == "v2"
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
        "steady": "D / Unattractive",
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