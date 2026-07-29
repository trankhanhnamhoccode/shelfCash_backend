import re
import unicodedata

from app.core.exceptions import ValidationError


def display_name(value: str | None) -> str:
    safe_value = "None" if value is None else str(value)
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", safe_value).strip())
    if not name:
        raise ValidationError("Tên không được để trống.")
    return name


def normalize_name(value: str | None) -> str:
    return display_name(value).casefold()
