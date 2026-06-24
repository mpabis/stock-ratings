from stock_rating.config import Settings


def test_settings_use_defaults_for_blank_optional_numeric_env(monkeypatch) -> None:
    monkeypatch.setenv("ALPHA_VANTAGE_MAX_REQUESTS_PER_RUN", "")
    monkeypatch.setenv("ALPHA_VANTAGE_MIN_INTERVAL_SECONDS", "")
    monkeypatch.setenv("TWELVE_DATA_MAX_REQUESTS_PER_RUN", "")
    monkeypatch.setenv("STOOQ_MAX_REQUESTS_PER_RUN", "")
    monkeypatch.setenv("STOCK_RATING_SYMBOL_LIMIT", "")
    monkeypatch.setenv("STOCK_RATING_FUNDAMENTAL_SYMBOL_LIMIT", "")
    monkeypatch.setenv("STOCK_RATING_ANALYST_SYMBOL_LIMIT", "")
    monkeypatch.setenv("STOCK_RATING_FINNHUB_ANALYST_SYMBOL_LIMIT", "")
    monkeypatch.setenv("FINNHUB_ANALYST_MIN_INTERVAL_SECONDS", "")

    settings = Settings()

    assert settings.alpha_vantage_max_requests_per_run == 20
    assert settings.alpha_vantage_min_interval_seconds == 1.2
    assert settings.twelve_data_max_requests_per_run == 12
    assert settings.stooq_max_requests_per_run == 40
    assert settings.symbol_limit == 100
    assert settings.fundamental_symbol_limit == 10
    assert settings.analyst_symbol_limit == 0
    assert settings.finnhub_analyst_symbol_limit == 0
    assert settings.finnhub_analyst_min_interval_seconds == 2.0


def test_settings_read_numeric_env_at_instantiation(monkeypatch) -> None:
    monkeypatch.setenv("TWELVE_DATA_MAX_REQUESTS_PER_RUN", "24")
    monkeypatch.setenv("STOCK_RATING_SYMBOL_LIMIT", "125")

    settings = Settings()

    assert settings.twelve_data_max_requests_per_run == 24
    assert settings.symbol_limit == 125
