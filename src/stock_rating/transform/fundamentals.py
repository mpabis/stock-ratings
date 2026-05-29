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
    "stockholders_equity",
    "eps_diluted",
    "shares_diluted",
)


def compute_fundamental_features(
    symbol: str,
    as_of: date,
    facts: list[FundamentalFact],
    latest_price: Decimal | None = None,
) -> list[FeatureValue]:
    facts_by_metric: dict[str, list[FundamentalFact]] = {}
    for fact in facts:
        if fact.metric in CORE_FUNDAMENTAL_METRICS:
            facts_by_metric.setdefault(fact.metric, []).append(fact)

    for metric_facts in facts_by_metric.values():
        metric_facts.sort(
            key=lambda fact: (
                fact.fiscal_year,
                fact.period_end or date(fact.fiscal_year or 1970, 1, 1),
                fact.filed_at.date() if fact.filed_at else date(fact.fiscal_year or 1970, 1, 1),
            ),
            reverse=True,
        )

    metric_map = {
        metric: metric_facts[0].value
        for metric, metric_facts in facts_by_metric.items()
        if metric_facts
    }

    revenue = metric_map.get("revenue")
    net_income = metric_map.get("net_income")
    operating_cash_flow = metric_map.get("operating_cash_flow")
    assets = metric_map.get("assets")
    liabilities = metric_map.get("liabilities")
    stockholders_equity = metric_map.get("stockholders_equity")
    eps_diluted = metric_map.get("eps_diluted")
    shares_diluted = metric_map.get("shares_diluted")

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

    if latest_price not in {None, Decimal("0")} and eps_diluted is not None:
        features.append(
            FeatureValue(
                symbol=symbol,
                date=as_of,
                feature_name="earnings_yield",
                feature_value=eps_diluted / latest_price,
                source_version="fundamentals_v2",
            )
        )

    if (
        latest_price not in {None, Decimal("0")}
        and stockholders_equity not in {None, Decimal("0")}
        and shares_diluted not in {None, Decimal("0")}
    ):
        book_value_per_share = stockholders_equity / shares_diluted
        if book_value_per_share != 0:
            features.append(
                FeatureValue(
                    symbol=symbol,
                    date=as_of,
                    feature_name="book_to_price",
                    feature_value=book_value_per_share / latest_price,
                    source_version="fundamentals_v2",
                )
            )

    for metric, feature_name in {
        "revenue": "revenue_growth_yoy",
        "net_income": "net_income_growth_yoy",
        "operating_cash_flow": "operating_cash_flow_growth_yoy",
    }.items():
        growth_value = _year_over_year_growth(facts_by_metric.get(metric, []))
        if growth_value is not None:
            features.append(
                FeatureValue(
                    symbol=symbol,
                    date=as_of,
                    feature_name=feature_name,
                    feature_value=growth_value,
                    source_version="fundamentals_v2",
                )
            )

    return features


def _year_over_year_growth(facts: list[FundamentalFact]) -> Decimal | None:
    if len(facts) < 2:
        return None

    latest = facts[0].value
    previous = facts[1].value
    if previous == 0:
        return None
    return (latest - previous) / abs(previous)
