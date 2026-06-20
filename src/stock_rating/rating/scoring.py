from dataclasses import dataclass


@dataclass(frozen=True)
class RatingResult:
    score: int
    label: str


# Full label vocabulary keyed by A-F letter grade. Shared by the absolute
# per-symbol mapping below and the percentile (universe-relative) grader so the
# API/report layer sees the same label strings regardless of which pass set them.
GRADE_LABELS: dict[str, str] = {
    "A": "A / Very Attractive",
    "B": "B / Attractive",
    "C": "C / Neutral",
    "D": "D / Unattractive",
    "F": "F / Very Unattractive",
}


def label_for_grade(grade: str) -> str:
    return GRADE_LABELS.get(grade, GRADE_LABELS["F"])


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
