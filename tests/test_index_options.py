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


def test_init_drops_the_retired_agent_configs_index_and_nothing_else(raw_db, monkeypatch):
    """The old unique index lacks assignment_id; left in place it would refuse
    a second assignment's override of the same key."""
    from aied_db import schema

    old = "scope_1_course_id_1_user_id_1_agent_1_key_1"
    assert old in schema.RETIRED_INDEXES["agent_configs"]
    raw_db["agent_configs"].create_index(
        [("scope", 1), ("course_id", 1), ("user_id", 1), ("agent", 1), ("key", 1)], unique=True
    )
    raw_db["agent_configs"].create_index([("unrelated", 1)])
    # mongomock has no collMod; the validator is not what this test is about.
    monkeypatch.setattr(raw_db, "command", lambda *a, **k: {"ok": 1})

    schema.ensure_collection(raw_db, "agent_configs")
    names = {index["name"] for index in raw_db["agent_configs"].list_indexes()}
    assert old not in names
    assert "unrelated_1" in names
    assert index_name(COLLECTIONS["agent_configs"]["indexes"][1][0]) in names

    schema.ensure_collection(raw_db, "agent_configs")  # idempotent once it is gone
