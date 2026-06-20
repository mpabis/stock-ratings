from datetime import date
from decimal import Decimal

from stock_rating.repository.analyst import AnalystConsensusPoint
from stock_rating.transform.analyst_features import compute_analyst_revision_features


AS_OF = date(2026, 6, 20)


def _point(snapshot_date: date, suggestion_score, target_price, source: str) -> AnalystConsensusPoint:
    return AnalystConsensusPoint(
        symbol="TEST",
        date=snapshot_date,
        analyst_target_price=Decimal(str(target_price)) if target_price is not None else None,
        strong_buy_count=None,
        buy_count=None,
        hold_count=None,
        sell_count=None,
        strong_sell_count=None,
        suggestion_label=None,
        suggestion_score=Decimal(str(suggestion_score)) if suggestion_score is not None else None,
        source=source,
    )


def _by_name(features) -> dict[str, Decimal]:
    return {feature.feature_name: feature.feature_value for feature in features}


def test_rising_sentiment_pushes_score_above_neutral() -> None:
    snapshots = {
        "finnhub": [
            _point(date(2026, 6, 20), suggestion_score="1.2", target_price="120", source="finnhub"),
            _point(date(2026, 5, 20), suggestion_score="0.4", target_price="100", source="finnhub"),
        ]
    }
    features = _by_name(compute_analyst_revision_features("TEST", AS_OF, snapshots))
    # suggestion delta = +0.8, target change = +20% -> score well above 50.
    assert features["analyst_suggestion_score_delta"] == Decimal("0.8")
    assert features["analyst_target_price_change_pct"] == Decimal("0.2")
    assert features["analyst_revision_score"] > Decimal("50")


def test_falling_sentiment_pushes_score_below_neutral() -> None:
    snapshots = {
        "finnhub": [
            _point(date(2026, 6, 20), suggestion_score="-0.5", target_price="80", source="finnhub"),
            _point(date(2026, 5, 20), suggestion_score="0.5", target_price="100", source="finnhub"),
        ]
    }
    features = _by_name(compute_analyst_revision_features("TEST", AS_OF, snapshots))
    assert features["analyst_revision_score"] < Decimal("50")


def test_multi_source_averages_deltas() -> None:
    snapshots = {
        "finnhub": [
            _point(date(2026, 6, 20), suggestion_score="1.0", target_price="110", source="finnhub"),
            _point(date(2026, 5, 20), suggestion_score="0.0", target_price="100", source="finnhub"),
        ],
        "alpha_vantage_overview": [
            _point(date(2026, 6, 18), suggestion_score="0.0", target_price="100", source="alpha_vantage_overview"),
            _point(date(2026, 5, 18), suggestion_score="0.0", target_price="100", source="alpha_vantage_overview"),
        ],
    }
    features = _by_name(compute_analyst_revision_features("TEST", AS_OF, snapshots))
    # finnhub delta +1.0, alpha delta 0.0 -> average +0.5; target change +10% and 0% -> +5%.
    assert features["analyst_suggestion_score_delta"] == Decimal("0.5")
    assert features["analyst_target_price_change_pct"] == Decimal("0.05")


def test_single_snapshot_yields_no_features() -> None:
    snapshots = {"finnhub": [_point(date(2026, 6, 20), suggestion_score="1.0", target_price="100", source="finnhub")]}
    assert compute_analyst_revision_features("TEST", AS_OF, snapshots) == []


def test_no_history_yields_no_features() -> None:
    assert compute_analyst_revision_features("TEST", AS_OF, {}) == []


def test_missing_target_price_still_uses_suggestion_delta() -> None:
    snapshots = {
        "finnhub": [
            _point(date(2026, 6, 20), suggestion_score="1.0", target_price=None, source="finnhub"),
            _point(date(2026, 5, 20), suggestion_score="0.0", target_price=None, source="finnhub"),
        ]
    }
    features = _by_name(compute_analyst_revision_features("TEST", AS_OF, snapshots))
    assert features["analyst_suggestion_score_delta"] == Decimal("1.0")
    assert "analyst_target_price_change_pct" not in features
    assert features["analyst_revision_score"] > Decimal("50")


def test_revision_score_is_clamped_to_0_100() -> None:
    snapshots = {
        "finnhub": [
            _point(date(2026, 6, 20), suggestion_score="50", target_price="100", source="finnhub"),
            _point(date(2026, 5, 20), suggestion_score="0", target_price="100", source="finnhub"),
        ]
    }
    features = _by_name(compute_analyst_revision_features("TEST", AS_OF, snapshots))
    assert features["analyst_revision_score"] == Decimal("100")
