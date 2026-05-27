from stock_rating.rating.scoring import map_score_to_label


def test_score_mapping_boundaries() -> None:
    assert map_score_to_label(100).label == "A / Very Attractive"
    assert map_score_to_label(90).label == "A / Very Attractive"
    assert map_score_to_label(89).label == "B / Attractive"
    assert map_score_to_label(75).label == "B / Attractive"
    assert map_score_to_label(74).label == "C / Neutral"
    assert map_score_to_label(55).label == "C / Neutral"
    assert map_score_to_label(54).label == "D / Unattractive"
    assert map_score_to_label(35).label == "D / Unattractive"
    assert map_score_to_label(34).label == "F / Very Unattractive"
    assert map_score_to_label(0).label == "F / Very Unattractive"


def test_scores_are_bounded_to_expected_range() -> None:
    assert map_score_to_label(999).score == 100
    assert map_score_to_label(-10).score == 0