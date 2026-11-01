"""A small `$jsonSchema` checker, because mongomock ignores validators.

mongomock raises `NotImplementedError` for `create_collection(validator=...)`
and for `collMod`, so a test suite running on it would never exercise the
validators at all — and the validators are most of this package. This module
implements the subset of `$jsonSchema` the schema modules actually use:

    bsonType (single or list, including "null"), required,
    additionalProperties: false, properties, items, enum,
    minimum / maximum (+ draft-4 boolean exclusiveMinimum/Maximum), minItems / maxItems, pattern

It is a TEST DOUBLE for MongoDB's own validation, not a reimplementation of
it. When the two disagree, MongoDB is right — which is why
`scripts/create_db.sh` against real Atlas is still the test that counts.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

#: BSON type name -> acceptable Python types.
_PYTHON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "object": (dict,),
    "array": (list, tuple),
    "bool": (bool,),
    "date": (datetime,),
    "int": (int,),
    "long": (int,),
    "double": (float, int),
    "decimal": (float, int),
    "null": (type(None),),
    "objectId": (object,),
}


class SchemaViolation(AssertionError):
    """Raised when a document does not satisfy its collection's validator."""


def _type_matches(value: Any, bson_type: str) -> bool:
    if bson_type == "null":
        return value is None
    # bool is a subclass of int in Python; MongoDB keeps them apart.
    if bson_type in ("int", "long", "double", "decimal") and isinstance(value, bool):
        return False
    if bson_type == "int" and isinstance(value, float):
        return False
    expected = _PYTHON_TYPES.get(bson_type)
    if expected is None:
        return True  # a type this checker does not model: do not fail on it
    return isinstance(value, expected)


def _check(value: Any, spec: dict[str, Any], path: str) -> None:
    if not spec:
        return  # {} accepts anything, as _id does

    if "enum" in spec:
        if value not in spec["enum"]:
            raise SchemaViolation(f"{path}: {value!r} is not one of {spec['enum']}")
        return

    bson_type = spec.get("bsonType")
    if bson_type is not None:
        types = [bson_type] if isinstance(bson_type, str) else list(bson_type)
        if not any(_type_matches(value, t) for t in types):
            raise SchemaViolation(
                f"{path}: expected {'/'.join(types)}, got {type(value).__name__} ({value!r})"
            )
        if value is None:
            return

    # Draft-4 semantics, as MongoDB implements them: exclusiveMinimum /
    # exclusiveMaximum are booleans that make minimum / maximum strict.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in spec:
            limit = spec["minimum"]
            strict = spec.get("exclusiveMinimum") is True
            if (value <= limit) if strict else (value < limit):
                raise SchemaViolation(f"{path}: {value!r} violates minimum {limit}{' (exclusive)' if strict else ''}")
        if "maximum" in spec:
            limit = spec["maximum"]
            strict = spec.get("exclusiveMaximum") is True
            if (value >= limit) if strict else (value > limit):
                raise SchemaViolation(f"{path}: {value!r} violates maximum {limit}{' (exclusive)' if strict else ''}")

    if isinstance(value, str) and "pattern" in spec:
        if not re.search(spec["pattern"], value):
            raise SchemaViolation(f"{path}: {value!r} does not match /{spec['pattern']}/")

    if isinstance(value, (list, tuple)):
        if "minItems" in spec and len(value) < spec["minItems"]:
            raise SchemaViolation(f"{path}: needs at least {spec['minItems']} items, got {len(value)}")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            raise SchemaViolation(f"{path}: allows at most {spec['maxItems']} items, got {len(value)}")
        item_spec = spec.get("items")
        if item_spec:
            for index, item in enumerate(value):
                _check(item, item_spec, f"{path}[{index}]")

    if isinstance(value, dict):
        properties = spec.get("properties", {})
        for field in spec.get("required", []):
            if field not in value:
                raise SchemaViolation(f"{path}: missing required field {field!r}")
        if spec.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise SchemaViolation(f"{path}: fields not in the schema: {unknown}")
        for field, field_value in value.items():
            if field in properties:
                _check(field_value, properties[field], f"{path}.{field}" if path else field)


def assert_valid(document: dict, schema: dict, *, collection: str = "document") -> None:
    """Raise `SchemaViolation` if `document` does not satisfy `schema`."""
    _check(document, schema, collection)


def is_valid(document: dict, schema: dict) -> bool:
    try:
        assert_valid(document, schema)
    except SchemaViolation:
        return False
    return True
