"""The collection registry: every schema module merged into one map.

`COLLECTIONS` is the single source of truth for three things — the
`$jsonSchema` validators, the indexes, and the tables in
`docs/collections.md` (generated; run `python scripts/gen_docs.py`).

Adding a collection means: define it in a schema module, add it to that
module's `COLLECTIONS`, and give it a `POLICY` entry in `access.py`.
`tests/test_registry.py` fails if the two drift apart.

Validation level is "moderate": existing documents that already violate the
validator are left alone, new and updated ones are checked. Switch to
"strict" with `python scripts/init_db.py --strict` once the migrated data is
known clean.
"""

from __future__ import annotations

from typing import Any, Iterable

from pymongo.errors import OperationFailure

from . import (
    assessment,
    curriculum,
    integrity,
    lab_companion,
    participant_guardian,
    platform,
    system,
)

MODULES = (
    system,
    curriculum,
    participant_guardian,
    lab_companion,
    assessment,
    integrity,
    platform,
)

#: Which agent owns each group, for docs and for the Atlas role grants.
MODULE_NAMES = {
    system: "system",
    curriculum: "curriculum",
    participant_guardian: "participant_guardian",
    lab_companion: "lab_companion",
    assessment: "assessment",
    integrity: "integrity",
    platform: "platform",
}

VALIDATION_LEVELS = ("off", "moderate", "strict")
DEFAULT_VALIDATION_LEVEL = "moderate"


def _merge() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    collections: dict[str, dict[str, Any]] = {}
    owners: dict[str, str] = {}
    for module in MODULES:
        name = MODULE_NAMES[module]
        for collection_name, spec in module.COLLECTIONS.items():
            if collection_name in collections:
                raise RuntimeError(
                    f"collection {collection_name!r} is defined twice: "
                    f"in {owners[collection_name]} and in {name}"
                )
            collections[collection_name] = spec
            owners[collection_name] = name
    return collections, owners


COLLECTIONS, OWNERS = _merge()

#: Index options a plain (keys, unique) tuple cannot express, by collection
#: and generated index name.
INDEX_OPTIONS: dict[str, dict[str, dict[str, Any]]] = {}
for _module in MODULES:
    INDEX_OPTIONS.update(getattr(_module, "INDEX_OPTIONS", {}))

#: Atlas Search / Vector Search indexes, which are not ordinary indexes.
SEARCH_INDEXES: dict[str, list[dict[str, Any]]] = {}
for _module in MODULES:
    SEARCH_INDEXES.update(getattr(_module, "SEARCH_INDEXES", {}))


def index_name(keys: Iterable[tuple[str, int]]) -> str:
    """The name pymongo generates for these keys, so options can be attached."""
    return "_".join(f"{field}_{direction}" for field, direction in keys)


def validator_for(collection: str) -> dict[str, Any]:
    return {"$jsonSchema": COLLECTIONS[collection]["schema"]}


def ensure_collection(
    db,
    name: str,
    *,
    validation_level: str = DEFAULT_VALIDATION_LEVEL,
) -> str:
    """Create the collection with its validator, or update an existing one.

    Returns "created" or "updated". Idempotent — safe on every deploy.
    Mirrors curriculum-designer's `ensure_collection` so the two behave
    identically for `labs` and `lab_files`.
    """
    if validation_level not in VALIDATION_LEVELS:
        raise ValueError(f"validation_level must be one of {VALIDATION_LEVELS}")

    validator = validator_for(name)
    if name in db.list_collection_names():
        db.command({"collMod": name, "validator": validator, "validationLevel": validation_level})
        action = "updated"
    else:
        db.create_collection(name, validator=validator, validationLevel=validation_level)
        action = "created"

    options = INDEX_OPTIONS.get(name, {})
    for keys, unique, _why in COLLECTIONS[name]["indexes"]:
        db[name].create_index(keys, unique=unique, **options.get(index_name(keys), {}))

    return action


def ensure_all(
    db,
    *,
    validation_level: str = DEFAULT_VALIDATION_LEVEL,
) -> dict[str, str]:
    """Apply every collection's validator and indexes."""
    return {
        name: ensure_collection(db, name, validation_level=validation_level)
        for name in COLLECTIONS
    }


def ensure_search_indexes(db) -> dict[str, str]:
    """Create the Atlas Vector Search indexes.

    `createSearchIndexes` exists only on Atlas: a local mongod and mongomock
    both reject it. That is not an error here — the ordinary collections and
    indexes are already in place and everything except vector search works —
    so the failure is reported per index and the caller decides.
    """
    results: dict[str, str] = {}
    for collection_name, definitions in SEARCH_INDEXES.items():
        collection_handle = db[collection_name]

        # Ask before creating. Atlas treats createSearchIndexes with an
        # identical definition as a successful no-op and returns the name, so
        # catching "already exists" never fires and every run would claim
        # "created". Listing first is what makes the report truthful.
        try:
            existing = {index["name"] for index in collection_handle.list_search_indexes()}
        except OperationFailure as exc:
            detail = exc.details.get("errmsg", exc) if exc.details else exc
            for definition in definitions:
                results[f"{collection_name}.{definition['name']}"] = f"unavailable: {detail}"
            continue
        except Exception as exc:  # pymongo raises plain errors on old servers
            for definition in definitions:
                results[f"{collection_name}.{definition['name']}"] = f"unavailable: {exc}"
            continue

        for definition in definitions:
            label = f"{collection_name}.{definition['name']}"
            if definition["name"] in existing:
                results[label] = "exists"
                continue
            try:
                collection_handle.create_search_index(definition)
                results[label] = "created"
            except OperationFailure as exc:
                if "already exists" in str(exc).lower():
                    results[label] = "exists"
                else:
                    results[label] = f"unavailable: {exc.details.get('errmsg', exc) if exc.details else exc}"
            except Exception as exc:
                results[label] = f"unavailable: {exc}"
    return results


__all__ = [
    "COLLECTIONS",
    "OWNERS",
    "INDEX_OPTIONS",
    "SEARCH_INDEXES",
    "MODULES",
    "MODULE_NAMES",
    "DEFAULT_VALIDATION_LEVEL",
    "VALIDATION_LEVELS",
    "ensure_all",
    "ensure_collection",
    "ensure_search_indexes",
    "index_name",
    "validator_for",
]
