from dataclasses import dataclass
from decimal import Decimal

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured
from stock_rating.ingest.prices import DailyPriceBar


@dataclass(frozen=True)
class FeatureValue:
    symbol: str
    date: object
    feature_name: str
    feature_value: Decimal
    source_version: str


def feature_version() -> str:
    return "v1"


def compute_price_features(bars: list[DailyPriceBar]) -> list[FeatureValue]:
    if not bars:
        return []

    ordered = sorted(bars, key=lambda bar: bar.date, reverse=True)
    latest = ordered[0]
    features: list[FeatureValue] = []

    if latest.open != 0:
        intraday_return = (latest.close - latest.open) / latest.open
        features.append(
            FeatureValue(
                symbol=latest.symbol,
                date=latest.date,
                feature_name="intraday_return",
                feature_value=intraday_return,
                source_version=feature_version(),
            )
        )

    features.append(
        FeatureValue(
            symbol=latest.symbol,
            date=latest.date,
            feature_name="daily_volume",
            feature_value=Decimal(latest.volume),
            source_version=feature_version(),
        )
    )

    if len(ordered) > 1 and ordered[1].adjusted_close != 0:
        previous = ordered[1]
        one_day_return = (latest.adjusted_close - previous.adjusted_close) / previous.adjusted_close
        features.append(
            FeatureValue(
                symbol=latest.symbol,
                date=latest.date,
                feature_name="one_day_return",
                feature_value=one_day_return,
                source_version=feature_version(),
            )
        )

    return features


def persist_features(database_url: str, features: list[FeatureValue], connect_fn=connect_postgres) -> bool:
    if not features:
        return False
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.executemany(
            """
            insert into features_daily (
                symbol,
                date,
                feature_name,
                feature_value,
                source_version
            ) values (%s, %s, %s, %s, %s)
            on conflict (symbol, date, feature_name, source_version) do update set
                feature_value = excluded.feature_value
            """,
            [
                (
                    feature.symbol,
                    feature.date,
                    feature.feature_name,
                    feature.feature_value,
                    feature.source_version,
                )
                for feature in features
            ],
        )
        connection.commit()
        return True
    except Exception:
        return False
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass
