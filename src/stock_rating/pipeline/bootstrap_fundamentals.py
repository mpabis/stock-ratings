from stock_rating.config import get_settings
from stock_rating.ingest.sec_companyfacts import (
    SecCompanyFactsResponseError,
    fetch_sec_company_facts,
    fetch_sec_ticker_mapping,
    normalize_symbol_for_sec,
    parse_company_facts,
    persist_fundamental_facts,
)
from stock_rating.repository.symbols import load_symbol_seeds


def main() -> None:
    settings = get_settings()
    seeds = load_symbol_seeds(settings.database_url, settings.symbol_seed_path or None)
    try:
        mappings = fetch_sec_ticker_mapping(settings.sec_user_agent)
    except SecCompanyFactsResponseError as error:
        print(str(error))
        return

    persisted_fact_count = 0
    matched_symbols = 0
    skipped_symbols: list[str] = []

    for seed in seeds:
        mapping = mappings.get(normalize_symbol_for_sec(seed.symbol))
        if mapping is None:
            skipped_symbols.append(seed.symbol)
            continue

        try:
            payload = fetch_sec_company_facts(mapping.cik, settings.sec_user_agent)
        except SecCompanyFactsResponseError as error:
            print(str(error))
            return
        facts = parse_company_facts(seed.symbol, mapping.cik, payload)
        if facts and persist_fundamental_facts(settings.database_url, facts):
            persisted_fact_count += len(facts)
        matched_symbols += 1

    print(f"Matched SEC symbols: {matched_symbols}")
    print(f"Persisted fundamental facts: {persisted_fact_count}")
    if skipped_symbols:
        preview = ", ".join(skipped_symbols[:10])
        print(f"Skipped symbols without SEC mapping: {preview}")


if __name__ == "__main__":
    main()