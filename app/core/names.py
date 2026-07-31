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


def normalize_lookup_name(value: str | None, *, strip_variant: bool = False) -> str:
    """Accent/case/spacing insensitive key used only for deterministic lookup."""
    text = unicodedata.normalize("NFD", display_name(value).casefold())
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn").replace("đ", "d")
    text = re.sub(r"\s+", " ", text).strip()
    if strip_variant:
        text = re.sub(r"(?:\s+|[-–—])(?:\d+(?:[.,]\d+)?\s*(?:ml|l|g|kg|oz|cl)|size\s+\w+)\s*$", "", text).strip()
    return text
