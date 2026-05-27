from dataclasses import dataclass


@dataclass(frozen=True)
class RatingResult:
    score: int
    label: str


def map_score_to_label(score: int) -> RatingResult:
    bounded_score = max(0, min(100, score))

    if bounded_score >= 90:
        label = "A / Very Attractive"
    elif bounded_score >= 75:
        label = "B / Attractive"
    elif bounded_score >= 55:
        label = "C / Neutral"
    elif bounded_score >= 35:
        label = "D / Unattractive"
    else:
        label = "F / Very Unattractive"

    return RatingResult(score=bounded_score, label=label)
