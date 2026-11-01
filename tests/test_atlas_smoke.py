"""The checks that only a real server can answer. Opt-in.

mongomock cannot hold a `$jsonSchema` validator, run `$vectorSearch`, or
honour a collation, so the unit suite proves the package's logic but not that
MongoDB agrees with it. These tests close that gap against a real deployment.

They are SKIPPED unless you ask for them, because they write to the database
in MONGODB_URI:

    AIED_DB_ATLAS_TEST=1 MONGODB_DB=aied_smoke .venv/bin/python -m pytest tests/test_atlas_smoke.py -v

Point MONGODB_DB at a THROWAWAY database name, never at `aied`.
"""

from __future__ import annotations

import os

import pytest

from aied_db import Role, client, guarded, schema
from aied_db.stores import MongoSystemStore

from .factories import FACTORIES

pytestmark = pytest.mark.skipif(
    os.environ.get("AIED_DB_ATLAS_TEST") != "1",
    reason="set AIED_DB_ATLAS_TEST=1 and a throwaway MONGODB_DB to run the Atlas smoke tests",
)


@pytest.fixture(scope="module")
def atlas_db():
    name = client.db_name()
    if name == "aied":
        pytest.fail("refusing to run the smoke tests against 'aied' — set MONGODB_DB")
    client.ping()
    database = client.get_db()
    schema.ensure_all(database)
    yield database
    for collection in schema.COLLECTIONS:
        database[collection].delete_many({})
    client.close()


def test_every_collection_exists_with_a_validator(atlas_db):
    existing = set(atlas_db.list_collection_names())
    assert set(schema.COLLECTIONS) <= existing

    for name in schema.COLLECTIONS:
        options = atlas_db[name].options()
        assert "validator" in options, f"{name} has no validator"
        assert options["validator"] == schema.validator_for(name), name
        assert options.get("validationLevel") == "moderate", name


def test_ensure_all_is_idempotent(atlas_db):
    again = schema.ensure_all(atlas_db)
    assert set(again.values()) == {"updated"}


def test_the_server_rejects_an_invalid_document(atlas_db):
    """The point of the validators: a bad document cannot be written at all."""
    from pymongo.errors import WriteError

    with pytest.raises(WriteError):
        atlas_db["users"].insert_one(FACTORIES["users"](role="superuser"))
    with pytest.raises(WriteError):
        atlas_db["users"].insert_one(FACTORIES["users"](unexpected_field="x"))


def test_the_server_enforces_the_unique_indexes(atlas_db):
    from aied_db import IntegrityError

    db = guarded(atlas_db, Role.ADMIN, actor_id="smoke-test")
    db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    with pytest.raises(IntegrityError):
        db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    atlas_db["guardian_sessions"].delete_many({})


def test_email_uniqueness_and_lookup_are_case_insensitive(atlas_db):
    """What mongomock could not check: the collation on users.email."""
    from aied_db import IntegrityError

    store = MongoSystemStore(guarded(atlas_db, Role.ADMIN, actor_id="smoke-test"))
    store.insert_user(FACTORIES["users"](id="u1", email="Student@CalPoly.edu"))
    try:
        assert store.get_user_by_email("student@calpoly.edu")["id"] == "u1"
        with pytest.raises(IntegrityError):
            store.insert_user(FACTORIES["users"](id="u2", email="STUDENT@calpoly.edu"))
    finally:
        atlas_db["users"].delete_many({})


def test_many_users_without_sso_are_allowed(atlas_db):
    """A unique *sparse* index would reject the second null sso_subject."""
    from aied_db import IntegrityError

    store = MongoSystemStore(guarded(atlas_db, Role.ADMIN, actor_id="smoke-test"))
    try:
        store.insert_user(FACTORIES["users"](id="n1", email="n1@calpoly.edu", sso_subject=None))
        store.insert_user(FACTORIES["users"](id="n2", email="n2@calpoly.edu", sso_subject=None))
        store.update_user_sso_subject("n1", "cas-n1")
        assert store.get_user_by_sso_subject("cas-n1")["id"] == "n1"
        with pytest.raises(IntegrityError):
            store.update_user_sso_subject("n2", "cas-n1")
    finally:
        atlas_db["users"].delete_many({})


def test_the_vector_index_exists_or_says_why_not(atlas_db):
    outcomes = schema.ensure_search_indexes(atlas_db)
    label = "rag_vectors.rag_vectors_embedding_idx"
    assert label in outcomes
    if outcomes[label].startswith("unavailable"):
        pytest.skip(f"vector search not available on this deployment: {outcomes[label]}")
    assert outcomes[label] in ("created", "exists")


def test_a_second_run_reports_the_vector_index_as_existing(atlas_db):
    """Not cosmetic: it is how you tell a real creation from a no-op.

    Atlas answers `createSearchIndexes` for an identical definition with a
    successful no-op, so `ensure_search_indexes` has to list the indexes
    before creating or every run claims "created".
    """
    first = schema.ensure_search_indexes(atlas_db)
    label = "rag_vectors.rag_vectors_embedding_idx"
    if first.get(label, "").startswith("unavailable"):
        pytest.skip(f"vector search not available: {first[label]}")

    assert schema.ensure_search_indexes(atlas_db)[label] == "exists"


def test_a_date_round_trips_to_the_millisecond(atlas_db):
    """`_util.now()` truncates to milliseconds so this comparison holds."""
    from aied_db._util import now

    moment = now()
    atlas_db["interactions"].insert_one(FACTORIES["interactions"](timestamp=moment))
    stored = atlas_db["interactions"].find_one({"id": "interaction-001"})
    assert stored["timestamp"] == moment
    atlas_db["interactions"].delete_many({})


def test_vector_search_ranks_by_similarity_and_honours_the_course_filter(atlas_db):
    """The real `$vectorSearch`, which is the whole point of `rag_vectors`.

    Two things have to hold: the nearest neighbour comes back first, and the
    `course_id` filter keeps one course's material out of another's answers.
    The index is eventually consistent, so this polls rather than asserting
    on the first try.
    """
    import random
    import time

    from aied_db._util import new_id, now
    from aied_db.schema.lab_companion import EMBEDDING_DIMS, VECTOR_INDEX_NAME

    outcomes = schema.ensure_search_indexes(atlas_db)
    label = f"rag_vectors.{VECTOR_INDEX_NAME}"
    if outcomes.get(label, "").startswith("unavailable"):
        pytest.skip(f"vector search not available: {outcomes[label]}")

    def embedding(seed: int) -> list[float]:
        random.seed(seed)
        return [random.uniform(-1, 1) for _ in range(EMBEDDING_DIMS)]

    target = embedding(1)
    vectors = atlas_db["rag_vectors"]
    vectors.delete_many({"document_id": "smoke-doc"})
    vectors.insert_many(
        [
            {
                "_id": new_id(),
                "vector_id": f"smoke-v{position}",
                "document_id": "smoke-doc",
                "course_id": course,
                "chunk_index": position,
                "content": f"chunk {position}",
                "embedding_model": "text-embedding-3-small",
                "embedding": target if position == 0 else embedding(100 + position),
                "created_at": now(),
            }
            for position, course in enumerate(("csc580", "csc580", "csc480"))
        ]
    )

    pipeline = [
        {
            "$vectorSearch": {
                "index": VECTOR_INDEX_NAME,
                "path": "embedding",
                "queryVector": target,
                "numCandidates": 50,
                "limit": 3,
                "filter": {"course_id": "csc580"},
            }
        },
        {
            "$project": {
                "_id": 0,
                "vector_id": 1,
                "course_id": 1,
                "score": {"$meta": "vectorSearchScore"},
            }
        },
    ]

    try:
        hits = []
        for _ in range(15):
            hits = list(vectors.aggregate(pipeline))
            if hits:
                break
            time.sleep(4)

        assert hits, "the vector index returned nothing after a minute of polling"
        assert hits[0]["vector_id"] == "smoke-v0"
        assert hits[0]["score"] > 0.99
        assert [hit["course_id"] for hit in hits] == ["csc580"] * len(hits), (
            "the course_id filter leaked another course's chunks"
        )
    finally:
        vectors.delete_many({"document_id": "smoke-doc"})
