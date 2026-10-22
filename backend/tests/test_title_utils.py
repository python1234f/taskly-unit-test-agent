from app.title_utils import normalize_title


def test_normalize_title_removes_surrounding_whitespace():
    assert normalize_title("\t  Plan the sprint \n") == "Plan the sprint"


def test_normalize_title_preserves_internal_whitespace():
    assert normalize_title("Plan   the sprint") == "Plan   the sprint"
