from datetime import date
from decimal import Decimal

from stock_rating.api.app import _serialize_quality_alert, _serialize_value
from stock_rating.quality.checks import QualityAlert


def test_serialize_value_handles_decimal_and_date() -> None:
    payload = {
        "score": Decimal("67.5"),
        "freshest_input_date": date(2026, 5, 27),
        "nested": [{"risk": Decimal("41.2")}],
    }

    serialized = _serialize_value(payload)

    assert serialized == {
        "score": 67.5,
        "freshest_input_date": "2026-05-27",
        "nested": [{"risk": 41.2}],
    }


def test_serialize_quality_alert_maps_fields() -> None:
    alert = QualityAlert(
        symbol="AAPL",
        code="stale_price",
        severity="warning",
        message="Latest stored price is 3 days old for tier 1.",
    )

    serialized = _serialize_quality_alert(alert)

    assert serialized["symbol"] == "AAPL"
    assert serialized["code"] == "stale_price"
    assert serialized["severity"] == "warning"