"""Index options that only matter on a real server, checked offline."""

from __future__ import annotations

from aied_db.schema import COLLECTIONS, INDEX_OPTIONS, index_name


def test_no_unique_sparse_index_on_a_field_stored_as_null():
    """A sparse index still indexes an explicit null, so unique+sparse rejects
    the second document whose field is null. Fields that may be null must use
    a partialFilterExpression instead."""
    for collection, by_name in INDEX_OPTIONS.items():
        unique = {index_name(keys): u for keys, u, _ in COLLECTIONS[collection]["indexes"]}
        for name, options in by_name.items():
            assert name in unique, f"{collection}.{name}: options for an index that does not exist"
            if unique[name] and options.get("sparse"):
                field = name.rsplit("_", 1)[0]
                spec = COLLECTIONS[collection]["schema"]["properties"].get(field, {})
                types = spec.get("bsonType")
                types = [types] if isinstance(types, str) else (types or [])
                assert "null" not in types, (
                    f"{collection}.{name} is unique+sparse but {field} may be null; "
                    "use partialFilterExpression"
                )


def test_sso_subject_is_unique_only_when_set():
    assert INDEX_OPTIONS["users"]["sso_subject_1"] == {
        "partialFilterExpression": {"sso_subject": {"$type": "string"}}
    }
