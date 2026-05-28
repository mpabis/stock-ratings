from datetime import date, timedelta
from decimal import Decimal

from stock_rating.ingest.prices import DailyPriceBar
from stock_rating.transform.features import compute_price_features, persist_features


class _FakeFeatureCursor:
    def __init__(self) -> None:
        self.executemany_calls: list[tuple[str, list[tuple[object, ...]]]] = []
        self.closed = False

    def executemany(self, query: str, params_seq: list[tuple[object, ...]]) -> None:
        self.executemany_calls.append((query, params_seq))

    def close(self) -> None:
        self.closed = True


class _FakeFeatureConnection:
    def __init__(self) -> None:
        self.cursor_instance = _FakeFeatureCursor()
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeFeatureCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


def test_compute_price_features_generates_expected_values() -> None:
    bars = [
        DailyPriceBar(
            symbol="AAPL",
            date=date(2026, 5, 27),
            open=Decimal("100"),
            high=Decimal("110"),
            low=Decimal("99"),
            close=Decimal("105"),
            adjusted_close=Decimal("105"),
            volume=1000,
            source="alpha_vantage",
        ),
        DailyPriceBar(
            symbol="AAPL",
            date=date(2026, 5, 26),
            open=Decimal("96"),
            high=Decimal("101"),
            low=Decimal("95"),
            close=Decimal("100"),
            adjusted_close=Decimal("100"),
            volume=900,
            source="alpha_vantage",
        ),
    ]

    features = compute_price_features(bars)
    names = {feature.feature_name for feature in features}

    assert names == {
        "intraday_return",
        "daily_volume",
        "one_day_return",
        "high_low_range_pct",
        "gap_open_return",
    }


def test_compute_price_features_generates_lookback_and_volatility_metrics() -> None:
    bars: list[DailyPriceBar] = []
    base_date = date(2026, 5, 27)
    for day_index in range(25):
        close = Decimal("100") + Decimal(day_index)
        bars.append(
            DailyPriceBar(
                symbol="AAPL",
                date=base_date - timedelta(days=day_index),
                open=close - Decimal("1"),
                high=close + Decimal("1"),
                low=close - Decimal("2"),
                close=close,
                adjusted_close=close,
                volume=1000 + day_index,
                source="alpha_vantage",
            )
        )

    features = compute_price_features(bars)
    names = {feature.feature_name for feature in features}

    assert "five_day_return" in names
    assert "ten_day_return" in names
    assert "twenty_day_return" in names
    assert "average_volume_20d" in names
    assert "twenty_day_volatility" in names


def test_persist_features_inserts_rows() -> None:
    features = compute_price_features(
        [
            DailyPriceBar(
                symbol="AAPL",
                date=date(2026, 5, 27),
                open=Decimal("100"),
                high=Decimal("110"),
                low=Decimal("99"),
                close=Decimal("105"),
                adjusted_close=Decimal("105"),
                volume=1000,
                source="alpha_vantage",
            ),
            DailyPriceBar(
                symbol="AAPL",
                date=date(2026, 5, 26),
                open=Decimal("96"),
                high=Decimal("101"),
                low=Decimal("95"),
                close=Decimal("100"),
                adjusted_close=Decimal("100"),
                volume=900,
                source="alpha_vantage",
            ),
        ]
    )
    fake_connection = _FakeFeatureConnection()

    persisted = persist_features(
        database_url="postgresql://example",
        features=features,
        connect_fn=lambda _: fake_connection,
    )

    assert persisted is True
    assert len(fake_connection.cursor_instance.executemany_calls) == 1
    assert fake_connection.committed is True
    assert fake_connection.cursor_instance.closed is True
    assert fake_connection.closed is True
