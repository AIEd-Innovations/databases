"""Every validator must parse on a real MongoDB server.

mongomock never sends a validator anywhere, so a keyword MongoDB rejects only
shows up when init_db.py runs against Atlas (that is how the draft-6 style
`"exclusiveMinimum": 0` was found). This walks every schema and applies the
rules MongoDB's $jsonSchema parser enforces, so the next one fails here.

MongoDB implements JSON Schema draft 4 plus `bsonType`. Omitted on purpose and
rejected by the server: $ref, $schema, default, definitions, format, id.
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from aied_db.schema import COLLECTIONS

ALLOWED = {
    "additionalItems", "additionalProperties", "allOf", "anyOf", "bsonType",
    "dependencies", "description", "enum", "exclusiveMaximum", "exclusiveMinimum",
    "items", "maximum", "maxItems", "maxLength", "maxProperties", "minimum",
    "minItems", "minLength", "minProperties", "multipleOf", "not", "oneOf",
    "pattern", "patternProperties", "properties", "required", "title", "type",
    "uniqueItems",
}
BSON_TYPES = {
    "double", "string", "object", "array", "binData", "objectId", "bool", "date",
    "null", "regex", "javascript", "int", "timestamp", "long", "decimal",
    "minKey", "maxKey", "number",
}


def _walk(spec: Any, path: str) -> Iterator[tuple[str, dict]]:
    if not isinstance(spec, dict):
        return
    yield path, spec
    for name, sub in spec.get("properties", {}).items():
        yield from _walk(sub, f"{path}.{name}")
    for name, sub in spec.get("patternProperties", {}).items():
        yield from _walk(sub, f"{path}[/{name}/]")
    items = spec.get("items")
    if isinstance(items, dict):
        yield from _walk(items, f"{path}[]")
    elif isinstance(items, list):
        for i, sub in enumerate(items):
            yield from _walk(sub, f"{path}[{i}]")
    if isinstance(spec.get("additionalProperties"), dict):
        yield from _walk(spec["additionalProperties"], f"{path}.*")
    for key in ("allOf", "anyOf", "oneOf"):
        for i, sub in enumerate(spec.get(key, [])):
            yield from _walk(sub, f"{path}<{key}{i}>")
    if isinstance(spec.get("not"), dict):
        yield from _walk(spec["not"], f"{path}<not>")


def _problems(collection: str) -> list[str]:
    out = []
    for path, spec in _walk(COLLECTIONS[collection]["schema"], collection):
        for key in set(spec) - ALLOWED:
            out.append(f"{path}: keyword {key!r} is not supported by MongoDB")
        for flag, partner in (("exclusiveMinimum", "minimum"), ("exclusiveMaximum", "maximum")):
            if flag in spec:
                if not isinstance(spec[flag], bool):
                    out.append(f"{path}: {flag} must be true/false (draft 4), got {spec[flag]!r}")
                if partner not in spec:
                    out.append(f"{path}: {flag} needs {partner!r} alongside it")
        types = spec.get("bsonType")
        for t in [types] if isinstance(types, str) else (types or []):
            if t not in BSON_TYPES:
                out.append(f"{path}: unknown bsonType {t!r}")
        if "type" in spec and "bsonType" in spec:
            out.append(f"{path}: use either type or bsonType, not both")
        if "required" in spec and not spec["required"]:
            out.append(f"{path}: 'required' must not be an empty array")
    return out


@pytest.mark.parametrize("collection", sorted(COLLECTIONS))
def test_validator_uses_only_what_mongodb_accepts(collection):
    assert _problems(collection) == []


def test_the_dialect_check_catches_the_original_bug():
    bad = {"bsonType": "object", "properties": {"x": {"bsonType": "int", "exclusiveMinimum": 0}}}
    found = []
    for path, spec in _walk(bad, "t"):
        if "exclusiveMinimum" in spec and not isinstance(spec["exclusiveMinimum"], bool):
            found.append(path)
    assert found == ["t.x"]
