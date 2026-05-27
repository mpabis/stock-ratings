from datetime import date
from decimal import Decimal

from stock_rating.ingest.fred_macro import CORE_FRED_SERIES, MacroObservation
from stock_rating.transform.features import FeatureValue


def compute_macro_features(
    symbol: str,
    as_of: date,
    observations: dict[str, MacroObservation],
) -> list[FeatureValue]:
    ten_year = observations.get("DGS10")
    two_year = observations.get("DGS2")
    if ten_year is None or two_year is None:
        return []

    slope = ten_year.value - two_year.value
    return [
        FeatureValue(
            symbol=symbol,
            date=as_of,
            feature_name="yield_curve_slope",
            feature_value=slope,
            source_version="macro_v1",
        )
    ]