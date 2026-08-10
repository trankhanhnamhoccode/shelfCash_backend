import re
from typing import Any

from app.core.exceptions import ValidationError


_RECIPE_VERSION_PATTERN = re.compile(r"(?:v)?([0-9]+)", re.IGNORECASE)
_ERROR_MESSAGE = "recipe_version must be a positive integer such as 1, 2, 3"


def normalize_recipe_version(value: Any) -> int | None:
    """Return the canonical internal recipe version without lossy coercion."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        match = _RECIPE_VERSION_PATTERN.fullmatch(text)
        if match is None:
            raise ValidationError(
                _ERROR_MESSAGE,
                {"field": "recipe_version", "raw_value": value},
            )
        normalized = int(match.group(1))
    elif isinstance(value, int) and not isinstance(value, bool):
        normalized = value
    else:
        raise ValidationError(
            _ERROR_MESSAGE,
            {"field": "recipe_version", "raw_value": value},
        )
    if normalized < 1:
        raise ValidationError(
            _ERROR_MESSAGE,
            {"field": "recipe_version", "raw_value": value},
        )
    return normalized

