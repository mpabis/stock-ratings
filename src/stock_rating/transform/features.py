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

    if latest.close != 0:
        features.append(
            FeatureValue(
                symbol=latest.symbol,
                date=latest.date,
                feature_name="high_low_range_pct",
                feature_value=(latest.high - latest.low) / latest.close,
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

        if previous.adjusted_close != 0:
            features.append(
                FeatureValue(
                    symbol=latest.symbol,
                    date=latest.date,
                    feature_name="gap_open_return",
                    feature_value=(latest.open - previous.adjusted_close) / previous.adjusted_close,
                    source_version=feature_version(),
                )
            )

    lookback_feature_map = {
        5: "five_day_return",
        10: "ten_day_return",
        20: "twenty_day_return",
    }
    for lookback, feature_name in lookback_feature_map.items():
        if len(ordered) > lookback and ordered[lookback].adjusted_close != 0:
            lookback_return = (latest.adjusted_close - ordered[lookback].adjusted_close) / ordered[lookback].adjusted_close
            features.append(
                FeatureValue(
                    symbol=latest.symbol,
                    date=latest.date,
                    feature_name=feature_name,
                    feature_value=lookback_return,
                    source_version=feature_version(),
                )
            )

    if len(ordered) >= 21:
        recent_volumes = [Decimal(bar.volume) for bar in ordered[:20]]
        average_volume_20d = sum(recent_volumes) / Decimal(len(recent_volumes))
        features.append(
            FeatureValue(
                symbol=latest.symbol,
                date=latest.date,
                feature_name="average_volume_20d",
                feature_value=average_volume_20d,
                source_version=feature_version(),
            )
        )

        daily_returns: list[Decimal] = []
        for index in range(20):
            current = ordered[index]
            previous = ordered[index + 1]
            if previous.adjusted_close == 0:
                continue
            daily_returns.append((current.adjusted_close - previous.adjusted_close) / previous.adjusted_close)

        if daily_returns:
            mean_return = sum(daily_returns) / Decimal(len(daily_returns))
            variance = sum((item - mean_return) ** 2 for item in daily_returns) / Decimal(len(daily_returns))
            features.append(
                FeatureValue(
                    symbol=latest.symbol,
                    date=latest.date,
                    feature_name="twenty_day_volatility",
                    feature_value=variance.sqrt(),
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
