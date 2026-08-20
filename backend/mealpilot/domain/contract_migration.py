"""Compatibility helpers for persisted documents created before contract v2.

The public API remains strict and rejects retired fields.  These helpers are only
used at trusted persistence boundaries so an existing account can still read its
own profile, runs and adopted plans after the price/equipment contract removal.
They do not rewrite the stored source document.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


RETIRED_FIELDS = frozenset(
    {
        "equipment",
        "equipment_refs",
        "required_equipment",
        "estimated_cost_cny",
        "cost_cny",
        "max_cost_cny",
        "price_snapshot_id",
    }
)
RETIRED_PREFERENCE_CATEGORIES = frozenset({"equipment", "budget_habit"})


def migrate_persisted_document(value: Any) -> Any:
    """Return a contract-v2 view of a trusted legacy JSON value.

    This intentionally performs a narrow allowlisted migration: retired fields
    are discarded, budget negotiation options are removed, and retired memory
    categories are ignored.  Unknown non-retired fields remain visible so
    Pydantic can still reject genuinely corrupt data.
    """

    migrated = _walk(deepcopy(value))
    if isinstance(migrated, dict):
        parsed = migrated.get("parsed_constraints")
        if isinstance(parsed, dict):
            migrated["parsed_constraints"] = {
                key: item
                for key, item in parsed.items()
                if "cost" not in key.casefold() and "budget" not in key.casefold()
            }
    return migrated


def migrate_preference_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        migrate_persisted_document(item)
        for item in items
        if item.get("category") not in RETIRED_PREFERENCE_CATEGORIES
    ]


def migrate_persisted_proposal(value: Any) -> dict[str, Any] | None:
    migrated = migrate_persisted_document(value)
    if not isinstance(migrated, dict):
        return None
    options = migrated.get("options")
    if isinstance(options, list) and not options:
        return None
    return migrated


def _walk(value: Any) -> Any:
    if isinstance(value, list):
        result = [_walk(item) for item in value]
        # A legacy budget-only proposal cannot be resumed under contract v2.
        return [
            item
            for item in result
            if not (isinstance(item, dict) and item.get("field") == "max_cost_cny")
        ]
    if not isinstance(value, dict):
        return value
    return {key: _walk(item) for key, item in value.items() if key not in RETIRED_FIELDS}
