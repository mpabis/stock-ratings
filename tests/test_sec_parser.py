from stock_rating.ingest.sec_companyfacts import SEC_COMPANY_FACTS_URL, SEC_TICKER_MAPPING_URL


def test_sec_endpoints_are_https() -> None:
    assert SEC_COMPANY_FACTS_URL.startswith("https://")
    assert SEC_TICKER_MAPPING_URL.startswith("https://")
