from dataclasses import dataclass
from datetime import date
import json
from decimal import Decimal

from stock_rating.db import DatabaseConfig, connect_postgres, is_configured


@dataclass(frozen=True)
class RatingRecord:
    symbol: str
    date: date
    rating_score: int
    rating_label: str
    valuation_score: Decimal
    quality_score: Decimal
    growth_score: Decimal
    momentum_score: Decimal
    risk_score: Decimal
    explanation_json: dict[str, object]
    model_version: str
    freshness_status: str
    freshest_input_date: date


def persist_ratings(database_url: str, ratings: list[RatingRecord], connect_fn=connect_postgres) -> bool:
    if not ratings:
        return False
    if not is_configured(DatabaseConfig(url=database_url)):
        return False

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.executemany(
            """
            insert into ratings_daily (
                symbol,
                date,
                rating_score,
                rating_label,
                valuation_score,
                quality_score,
                growth_score,
                momentum_score,
                risk_score,
                explanation_json,
                model_version,
                freshness_status,
                freshest_input_date
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
            on conflict (symbol, date, model_version) do update set
                rating_score = excluded.rating_score,
                rating_label = excluded.rating_label,
                valuation_score = excluded.valuation_score,
                quality_score = excluded.quality_score,
                growth_score = excluded.growth_score,
                momentum_score = excluded.momentum_score,
                risk_score = excluded.risk_score,
                explanation_json = excluded.explanation_json,
                freshness_status = excluded.freshness_status,
                freshest_input_date = excluded.freshest_input_date
            """,
            [
                (
                    record.symbol,
                    record.date,
                    record.rating_score,
                    record.rating_label,
                    record.valuation_score,
                    record.quality_score,
                    record.growth_score,
                    record.momentum_score,
                    record.risk_score,
                    json.dumps(record.explanation_json),
                    record.model_version,
                    record.freshness_status,
                    record.freshest_input_date,
                )
                for record in ratings
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
