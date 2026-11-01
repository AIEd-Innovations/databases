"""Shared building blocks for the schema modules.

`schema/curriculum.py` deliberately does NOT import from here: it is a copy
of curriculum-designer's own module and keeps its local helpers so it stays
diffable against upstream.
"""

from __future__ import annotations

from typing import Any

# (keys, unique, why) — the third element documents the index in docs/collections.md
IndexSpec = list[tuple[list[tuple[str, int]], bool, str]]


def nullable(bson_type: str) -> list[str]:
    """A field that may hold `bson_type` or be explicitly null."""
    return [bson_type, "null"]


def collection(schema: dict[str, Any], indexes: IndexSpec) -> dict[str, Any]:
    return {"schema": schema, "indexes": indexes}


def nullable_object(schema: dict[str, Any]) -> dict[str, Any]:
    """An embedded document that may also be explicitly null.

    Object keywords (`properties`, `required`) only constrain instances that
    ARE objects, so they sit harmlessly alongside a null-permitting bsonType.
    """
    out = dict(schema)
    out["bsonType"] = ["object", "null"]
    return out
