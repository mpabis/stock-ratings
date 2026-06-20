def build_rating_explanation(symbol: str, freshness_status: str) -> dict[str, str]:
    return {
        "symbol": symbol,
        "freshness_status": freshness_status,
        "summary": "Transparent score built from valuation, quality, growth, momentum, risk, and analyst revisions.",
    }
