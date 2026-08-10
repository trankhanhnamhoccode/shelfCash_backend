import pytest

from app.core.exceptions import ValidationError
from app.core.recipe_versions import normalize_recipe_version


@pytest.mark.parametrize("raw, expected", [
    (None, None), ("", None), (" ", None), (1, 1),
    ("1", 1), ("v1", 1), ("V2", 2), (" v3 ", 3),
])
def test_normalize_recipe_version(raw, expected):
    assert normalize_recipe_version(raw) == expected


@pytest.mark.parametrize("raw", ["v1.2", "1.2", 1.2, "abc", "version1", 0, -1])
def test_invalid_recipe_versions_raise_controlled_validation(raw):
    with pytest.raises(ValidationError) as caught:
        normalize_recipe_version(raw)
    assert caught.value.details == {"field": "recipe_version", "raw_value": raw}
