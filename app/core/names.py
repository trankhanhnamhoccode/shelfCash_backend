import re
import unicodedata

from app.core.exceptions import ValidationError


def display_name(value: str) -> str:
    name = re.sub(r"\s+", " ", unicodedata.normalize("NFC", value).strip())
    if not name:
        raise ValidationError("Tên không được để trống.")
    return name


def normalize_name(value: str) -> str:
    return display_name(value).casefold()
