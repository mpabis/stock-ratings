"""Greenblatt Magic Formula combined ranking (cross-sectional, pass 2).

Each symbol's per-symbol ROIC and EBIT earnings yield are computed in
transform/benchmark_scores.py. This step ranks the universe on each metric
(higher is better) and sums the two ranks; the lowest combined sum is the best
Magic Formula candidate. The combined rank (1 = best) is persisted as the
`magic_formula_combined_rank` feature.

Greenblatt excludes financials and utilities; we drop those when the symbol's
sector is known. The minimum-market-cap filter is implicit — only symbols with
a positive market cap produce an EBIT earnings yield (see _enterprise_value),
so micro/no-cap names never reach this stage.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from stock_rating.transform.benchmark_scores import BENCHMARK_SOURCE_VERSION
from stock_rating.transform.features import (
    FeatureValue,
    MagicFormulaInput,
    load_latest_magic_formula_inputs,
    persist_features,
)


# Sector labels Greenblatt excludes (compared case-insensitively, prefix match).
EXCLUDED_SECTOR_PREFIXES = ("financ", "utilit", "bank", "insurance")


@dataclass(frozen=True)
class MagicFormulaRank:
    symbol: str
    date: date
    combined_rank: int


def rank_magic_formula(entries: list[MagicFormulaInput]) -> list[MagicFormulaRank]:
    """Rank eligible entries; combined_rank 1 = best. Excluded sectors dropped."""
    eligible = [
        entry
        for entry in entries
        if entry.roic is not None and entry.earnings_yield is not None and not _is_excluded(entry.sector)
    ]
    if not eligible:
        return []

    roic_rank = _rank_desc([entry.roic for entry in eligible])
    yield_rank = _rank_desc([entry.earnings_yield for entry in eligible])
    combined = [roic_rank[i] + yield_rank[i] for i in range(len(eligible))]
    # Lower combined sum is better -> rank ascending; ties share a rank.
    final_rank = [sum(1 for other in combined if other < value) + 1 for value in combined]

    return [
        MagicFormulaRank(symbol=entry.symbol, date=entry.date, combined_rank=final_rank[index])
        for index, entry in enumerate(eligible)
    ]


def apply_magic_formula_ranks(
    database_url: str,
    load_fn=load_latest_magic_formula_inputs,
    persist_fn=persist_features,
) -> int:
    """Compute and persist the combined Magic Formula rank; returns count ranked."""
    entries = load_fn(database_url)
    if not entries:
        return 0
    ranks = rank_magic_formula(entries)
    if not ranks:
        return 0
    features = [
        FeatureValue(
            symbol=rank.symbol,
            date=rank.date,
            feature_name="magic_formula_combined_rank",
            feature_value=Decimal(rank.combined_rank),
            source_version=BENCHMARK_SOURCE_VERSION,
        )
        for rank in ranks
    ]
    return len(features) if persist_fn(database_url, features) else 0


def _rank_desc(values: list[Decimal]) -> list[int]:
    """Rank 1 = highest value; ties share the same (best) rank."""
    return [sum(1 for other in values if other > value) + 1 for value in values]


def _is_excluded(sector: str | None) -> bool:
    if not sector:
        return False
    normalized = sector.strip().lower()
    return any(normalized.startswith(prefix) for prefix in EXCLUDED_SECTOR_PREFIXES)
