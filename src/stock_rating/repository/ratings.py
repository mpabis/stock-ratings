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


@dataclass(frozen=True)
class RatingRepairState:
    symbol: str
    refresh_tier: int
    last_price_date: date | None
    latest_rating_date: date | None


def load_rating_repair_states(database_url: str, connect_fn=connect_postgres) -> list[RatingRepairState]:
    if not is_configured(DatabaseConfig(url=database_url)):
        return []

    try:
        connection = connect_fn(database_url)
        cursor = connection.cursor()
        cursor.execute(
            """
            with latest_prices as (
                select symbol, max(date) as last_price_date
                from price_daily
                group by symbol
            ),
            latest_ratings as (
                select symbol, max(date) as latest_rating_date
                from ratings_daily
                group by symbol
            )
            select
                s.symbol,
                s.refresh_tier,
                lp.last_price_date,
                lr.latest_rating_date
            from symbols s
            left join latest_prices lp on lp.symbol = s.symbol
            left join latest_ratings lr on lr.symbol = s.symbol
            where s.active = true
            order by s.refresh_tier asc, s.symbol asc
            """
        )
        rows = cursor.fetchall()
    except Exception:
        return []
    finally:
        try:
            cursor.close()
            connection.close()
        except Exception:
            pass

    return [
        RatingRepairState(
            symbol=row[0],
            refresh_tier=row[1],
            last_price_date=row[2],
            latest_rating_date=row[3],
        )
        for row in rows
    ]


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
