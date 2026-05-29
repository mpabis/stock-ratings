from datetime import UTC, date, datetime
from decimal import Decimal

from stock_rating.ingest.sec_companyfacts import FundamentalFact
from stock_rating.transform.fundamentals import compute_fundamental_features


def test_compute_fundamental_features_derives_growth_and_price_ratios() -> None:
    facts = [
        FundamentalFact(
            cik="0000320193",
            symbol="AAPL",
            fiscal_period="FY",
            fiscal_year=2025,
            form="10-K",
            metric="revenue",
            value=Decimal("1200"),
            unit="USD",
            filed_at=datetime(2026, 1, 31, tzinfo=UTC),
            period_end=date(2025, 12, 31),
        ),
        FundamentalFact(
            cik="0000320193",
            symbol="AAPL",
            fiscal_period="FY",
            fiscal_year=2024,
            form="10-K",
            metric="revenue",
            value=Decimal("1000"),
            unit="USD",
            filed_at=datetime(2025, 1, 31, tzinfo=UTC),
            period_end=date(2024, 12, 31),
        ),
        FundamentalFact(
            cik="0000320193",
            symbol="AAPL",
            fiscal_period="FY",
            fiscal_year=2025,
            form="10-K",
            metric="eps_diluted",
            value=Decimal("5"),
            unit="USD/shares",
            filed_at=datetime(2026, 1, 31, tzinfo=UTC),
            period_end=date(2025, 12, 31),
        ),
        FundamentalFact(
            cik="0000320193",
            symbol="AAPL",
            fiscal_period="FY",
            fiscal_year=2025,
            form="10-K",
            metric="stockholders_equity",
            value=Decimal("400"),
            unit="USD",
            filed_at=datetime(2026, 1, 31, tzinfo=UTC),
            period_end=date(2025, 12, 31),
        ),
        FundamentalFact(
            cik="0000320193",
            symbol="AAPL",
            fiscal_period="FY",
            fiscal_year=2025,
            form="10-K",
            metric="shares_diluted",
            value=Decimal("100"),
            unit="shares",
            filed_at=datetime(2026, 1, 31, tzinfo=UTC),
            period_end=date(2025, 12, 31),
        ),
    ]

    features = compute_fundamental_features("AAPL", date(2026, 2, 1), facts, latest_price=Decimal("20"))
    values = {feature.feature_name: feature.feature_value for feature in features}

    assert values["revenue_growth_yoy"] == Decimal("0.2")
    assert values["earnings_yield"] == Decimal("0.25")
    assert values["book_to_price"] == Decimal("0.2")
