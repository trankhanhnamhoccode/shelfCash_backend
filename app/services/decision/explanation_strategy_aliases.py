"""Small explicit vocabulary for strategies in Decision Explanation questions."""

import re

from app.core.names import normalize_lookup_name


_ALIASES = {
    "lean": ("lean", "tiet kiem"),
    "balanced": ("balanced", "balance", "can bang"),
    "protected": ("protected", "protect", "an toan"),
}


def mentioned_strategies(text: str | None) -> set[str]:
    normalized = normalize_lookup_name(text or "")
    return {
        strategy
        for strategy, aliases in _ALIASES.items()
        if any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", normalized) for alias in aliases)
    }


def canonical_strategy(text: str) -> str | None:
    mentioned = mentioned_strategies(text)
    return next(iter(mentioned)) if len(mentioned) == 1 else None
