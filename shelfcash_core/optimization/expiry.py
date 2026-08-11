"""Canonical expiry resolution for planned and realized inbound inventory."""

from __future__ import annotations

from datetime import date, timedelta


def resolve_inbound_expiry(
    *, arrival_date: date, explicit_expiry_date: date | None = None,
    shelf_life_days: int | None = None,
) -> date | None:
    """Return an exact expiry date, never a guessed one.

    Explicit receipt/PO expiry wins.  ``shelf_life_days`` is an inclusive
    arrival offset: 0 means usable on arrival only; N means usable through
    ``arrival_date + N`` under the default inclusive FEFO policy.  ``None``
    means unknown expiry, not non-perishable.
    """
    if explicit_expiry_date is not None:
        return explicit_expiry_date
    if shelf_life_days is None:
        return None
    return arrival_date + timedelta(days=shelf_life_days)
