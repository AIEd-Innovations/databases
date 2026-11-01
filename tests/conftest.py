"""Fixtures: a mongomock database that behaves as much like Atlas as it can.

mongomock enforces unique indexes and accepts collation, but silently ignores
`$jsonSchema` validators. `ValidatingDatabase` closes that gap: it checks
every written document against the real validator from `aied_db.schema`
using `tests/jsonschema_check.py`, so a schema mistake fails a unit test
instead of waiting for Atlas.
"""

from __future__ import annotations

import os
from typing import Any

import mongomock
import pytest

from aied_db import Role, guarded
from aied_db.schema import COLLECTIONS, index_name, INDEX_OPTIONS

from .jsonschema_check import SchemaViolation, assert_valid


class ValidatingCollection:
    """A mongomock collection that enforces its `$jsonSchema`."""

    def __init__(self, collection, name: str, schema: dict) -> None:
        self._collection = collection
        self._name = name
        self._schema = schema

    def __getattr__(self, item):  # reads and anything else pass through
        return getattr(self._collection, item)

    def _validate(self, document: dict) -> None:
        assert_valid(dict(document), self._schema, collection=self._name)

    def insert_one(self, document, *args, **kwargs):
        self._validate(document)
        return self._collection.insert_one(document, *args, **kwargs)

    def insert_many(self, documents, *args, **kwargs):
        documents = list(documents)
        for document in documents:
            self._validate(document)
        return self._collection.insert_many(documents, *args, **kwargs)

    def replace_one(self, filter, replacement, *args, **kwargs):
        self._validate(replacement)
        return self._collection.replace_one(filter, replacement, *args, **kwargs)

    def _validate_after(self, filter) -> None:
        for document in self._collection.find(filter):
            self._validate({k: v for k, v in document.items() if k != "_id"})

    def update_one(self, filter, update, *args, **kwargs):
        result = self._collection.update_one(filter, update, *args, **kwargs)
        self._validate_after(filter)
        return result

    def update_many(self, filter, update, *args, **kwargs):
        result = self._collection.update_many(filter, update, *args, **kwargs)
        self._validate_after(filter)
        return result

    def find_one_and_update(self, filter, update, *args, **kwargs):
        result = self._collection.find_one_and_update(filter, update, *args, **kwargs)
        self._validate_after(filter)
        return result


class ValidatingDatabase:
    """A database whose collections validate on write."""

    def __init__(self, database) -> None:
        self._database = database
        self._cache: dict[str, Any] = {}

    def __getitem__(self, name: str):
        if name not in self._cache:
            collection = self._database[name]
            spec = COLLECTIONS.get(name)
            self._cache[name] = (
                ValidatingCollection(collection, name, spec["schema"]) if spec else collection
            )
        return self._cache[name]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._database, name)

    @property
    def name(self) -> str:
        return self._database.name

    def list_collection_names(self) -> list[str]:
        return self._database.list_collection_names()


def apply_indexes(database) -> None:
    """Create every index. Validators are skipped: mongomock cannot hold them."""
    for name, spec in COLLECTIONS.items():
        options = INDEX_OPTIONS.get(name, {})
        for keys, unique, _why in spec["indexes"]:
            database[name].create_index(keys, unique=unique, **options.get(index_name(keys), {}))


@pytest.fixture
def raw_db():
    """A bare mongomock database with the real indexes applied."""
    client = mongomock.MongoClient(tz_aware=True)
    database = client["aied_test"]
    apply_indexes(database)
    yield database
    client.close()


@pytest.fixture
def db(raw_db):
    """A validating database — use this unless a test is about validation itself."""
    return ValidatingDatabase(raw_db)


@pytest.fixture
def admin_db(db):
    """Guarded as ADMIN: for arranging test data that a role under test cannot write."""
    return guarded(db, Role.ADMIN, actor_id="test-admin")


@pytest.fixture
def course_id():
    return "course-csc580"


@pytest.fixture
def ui_student_repo():
    """Path to a sibling ui-student checkout, or None.

    The parity tests import ui-student's real SQLite stores and compare them
    with the Mongo ones. Clone it next to this repo to run them.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "ui-student")
    return path if os.path.isdir(os.path.join(path, "agentic_system", "store")) else None


@pytest.fixture
def schema_violation():
    return SchemaViolation
