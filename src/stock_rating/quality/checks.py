from dataclasses import dataclass
from datetime import date

from stock_rating.pipeline.daily import MAX_PRICE_AGE_BY_TIER, RefreshTask, age_in_days


@dataclass(frozen=True)
class SymbolQualitySnapshot:
    symbol: str
    refresh_tier: int
    last_price_date: date | None
    latest_rating_date: date | None


@dataclass(frozen=True)
class QualityAlert:
    symbol: str
    code: str
    severity: str
    message: str


def count_stale_tasks(tasks: list[RefreshTask]) -> int:
    return sum(1 for task in tasks if task.freshness_status == "stale")


def build_quality_alerts(snapshots: list[SymbolQualitySnapshot], as_of: date) -> list[QualityAlert]:
    alerts: list[QualityAlert] = []

    for snapshot in snapshots:
        if snapshot.last_price_date is None:
            alerts.append(
                QualityAlert(
                    symbol=snapshot.symbol,
                    code="missing_price",
                    severity="critical",
                    message="No stored price history found for active symbol.",
                )
            )
            continue
        else:
            max_age = MAX_PRICE_AGE_BY_TIER.get(snapshot.refresh_tier, 5)
            age = age_in_days(as_of, snapshot.last_price_date)
            if age > max_age:
                alerts.append(
                    QualityAlert(
                        symbol=snapshot.symbol,
                        code="stale_price",
                        severity="warning",
                        message=f"Latest stored price is {age} trading days old for tier {snapshot.refresh_tier}.",
                    )
                )

        if snapshot.latest_rating_date is None:
            alerts.append(
                QualityAlert(
                    symbol=snapshot.symbol,
                    code="missing_rating",
                    severity="warning",
                    message="No rating has been published for the active symbol.",
                )
            )
        elif snapshot.last_price_date is not None and snapshot.latest_rating_date < snapshot.last_price_date:
            alerts.append(
                QualityAlert(
                    symbol=snapshot.symbol,
                    code="stale_rating",
                    severity="warning",
                    message="Latest rating predates the latest stored price.",
                )
            )

    return alerts
