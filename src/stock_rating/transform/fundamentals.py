from datetime import date
from decimal import Decimal

from stock_rating.ingest.sec_companyfacts import FundamentalFact
from stock_rating.transform.features import FeatureValue


CORE_FUNDAMENTAL_METRICS = (
    "revenue",
    "net_income",
    "operating_cash_flow",
    "assets",
    "liabilities",
)


def compute_fundamental_features(symbol: str, as_of: date, facts: list[FundamentalFact]) -> list[FeatureValue]:
    metric_map = {fact.metric: fact.value for fact in facts if fact.metric in CORE_FUNDAMENTAL_METRICS}

    revenue = metric_map.get("revenue")
    net_income = metric_map.get("net_income")
    operating_cash_flow = metric_map.get("operating_cash_flow")
    assets = metric_map.get("assets")
    liabilities = metric_map.get("liabilities")

    features: list[FeatureValue] = []
    if revenue not in {None, Decimal("0")} and net_income is not None:
        features.append(
            FeatureValue(
                symbol=symbol,
                date=as_of,
                feature_name="net_margin",
                feature_value=net_income / revenue,
                source_version="fundamentals_v1",
            )
        )

    if revenue not in {None, Decimal("0")} and operating_cash_flow is not None:
        features.append(
            FeatureValue(
                symbol=symbol,
                date=as_of,
                feature_name="cash_flow_margin",
                feature_value=operating_cash_flow / revenue,
                source_version="fundamentals_v1",
            )
        )

    if assets not in {None, Decimal("0")} and net_income is not None:
        features.append(
            FeatureValue(
                symbol=symbol,
                date=as_of,
                feature_name="return_on_assets",
                feature_value=net_income / assets,
                source_version="fundamentals_v1",
            )
        )

    if assets not in {None, Decimal("0")} and liabilities is not None:
        features.append(
            FeatureValue(
                symbol=symbol,
                date=as_of,
                feature_name="debt_to_assets",
                feature_value=liabilities / assets,
                source_version="fundamentals_v1",
            )
        )

    return features
