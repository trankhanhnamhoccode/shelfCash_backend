"""Bounded recognition of asserted versus denied causal relations."""

import re
import unicodedata


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold()).replace("đ", "d")
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


_RELATION = re.compile(
    r"\b(?:vi|do|boi|nen|dan den|khien|xuat phat tu|de bu(?: dap)?|de tranh|de dam bao|"
    r"because|due to|therefore|caused by|to offset|to cover)\b|"
    r"\b(?:ly do|nguyen nhan)\s+(?:la|chinh la)\b"
)
_DENIAL = re.compile(
    r"(?:khong|chua)\s+(?:co\s+(?:bang chung|du lieu|ly do)|du\s+(?:du lieu|bang chung)|"
    r"the\s+(?:xac nhan|ket luan)|the\s+khẳng\s+dinh)|"
    r"(?:khong|chua)\s+the\s+(?:xac nhan|ket luan)|"
    r"(?:no|without)\s+(?:evidence|reason|proof)|"
    r"(?:cannot|can't|unable to)\s+(?:confirm|conclude)"
)


def is_causal_limitation(text: str) -> bool:
    """True only for a sentence that denies/qualifies a causal explanation."""
    normalized = _plain(text)
    return bool(_DENIAL.search(normalized)) and not asserts_cause(text)


def asserts_cause(text: str) -> bool:
    """Recognize clear causal assertions; negated premises confer no authority."""
    normalized = _plain(text)
    for clause in re.split(r"[.!?;]|\b(?:nhung|however|but)\b", normalized):
        for match in _RELATION.finditer(clause):
            prefix = clause[:match.start()]
            # The noun "lý do" is not the connective "do".
            if match.group() == "do" and re.search(r"\bly\s+$", prefix):
                continue
            if match.group() == "vi" and re.search(r"\btrung\s+$", prefix):
                continue
            if re.search(r"(?:khong|chua)\s+phai\s+$", prefix):
                continue
            denial = list(_DENIAL.finditer(prefix))
            if denial and not re.search(r"\b(?:nhung|however|but)\b", prefix[denial[-1].end():]):
                continue
            return True
    return False
