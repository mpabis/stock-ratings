from datetime import date
from decimal import Decimal

from stock_rating.ingest.analyst import derive_analyst_suggestion, parse_alpha_vantage_analyst_consensus


def test_parse_alpha_vantage_analyst_consensus_maps_target_and_counts() -> None:
    payload = {
        "AnalystTargetPrice": "277.68",
        "AnalystRatingStrongBuy": "12",
        "AnalystRatingBuy": "18",
        "AnalystRatingHold": "9",
        "AnalystRatingSell": "2",
        "AnalystRatingStrongSell": "1",
    }

    snapshot = parse_alpha_vantage_analyst_consensus("AAPL", payload, as_of_date=date(2026, 5, 29))

    assert snapshot is not None
    assert snapshot.symbol == "AAPL"
    assert snapshot.analyst_target_price == Decimal("277.68")
    assert snapshot.strong_buy_count == 12
    assert snapshot.buy_count == 18
    assert snapshot.hold_count == 9
    assert snapshot.sell_count == 2
    assert snapshot.strong_sell_count == 1
    assert snapshot.suggestion_label in {"buy", "strong_buy"}


def test_parse_alpha_vantage_analyst_consensus_returns_none_when_no_analyst_fields() -> None:
    payload = {
        "Symbol": "AAPL",
        "Name": "Apple Inc.",
    }

    snapshot = parse_alpha_vantage_analyst_consensus("AAPL", payload, as_of_date=date(2026, 5, 29))

    assert snapshot is None


def test_derive_analyst_suggestion_classifies_extremes() -> None:
    strong_buy_label, strong_buy_score = derive_analyst_suggestion(10, 1, 0, 0, 0)
    strong_sell_label, strong_sell_score = derive_analyst_suggestion(0, 0, 0, 1, 8)

    assert strong_buy_label == "strong_buy"
    assert strong_buy_score is not None and strong_buy_score > 1
    assert strong_sell_label == "strong_sell"
    assert strong_sell_score is not None and strong_sell_score < -1
