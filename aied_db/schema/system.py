"""Multi-tenant metadata: users, courses, enrollments.

Ported from ui-student's `agentic_system/store/system.py` SYSTEM_SCHEMA
(@6f71458). Field names are kept EXACTLY as the SQLite columns were, because
`SqliteSystemStore` returned `dict(row)` straight from `SELECT *` and the
server layer reads those keys. Note the primary key is `id`, not `user_id` /
`course_id` — that is the existing contract, not a style choice.

Differences from the SQLite original, all deliberate:
  - `created_at` / `enrolled_at` are BSON dates here; the stores convert to
    ISO strings on the way out (see `_util.to_iso`).
  - SQLite's `participant_db_path` / `guardian_db_path` columns are dropped:
    they pointed at per-course .db files, which is exactly what this package
    replaces. The migration script must not carry them over.
  - `email` was `COLLATE NOCASE` and UNIQUE. Mongo has no per-field collation
    in a validator, so the unique index on `email` is created with
    `strength: 2` to keep case-insensitive uniqueness.
"""

from __future__ import annotations

from typing import Any

from ._common import IndexSpec, collection, nullable

ROLES = ["admin", "instructor", "student"]

USER_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["id", "email", "name", "role", "created_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "id": {"bsonType": "string", "description": "UUID4; the user's primary key"},
        "email": {"bsonType": "string", "description": "Unique, case-insensitive"},
        "name": {"bsonType": "string", "description": "May be empty; defaulted to '' upstream"},
        "sso_subject": {
            "bsonType": nullable("string"),
            "description": "Subject claim from the SSO provider; unique when present",
        },
        "role": {"enum": ROLES},
        "created_at": {"bsonType": "date"},
    },
}

COURSE_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "id",
        "name",
        "instructor_id",
        "blob_container_name",
        "search_index_name",
        "search_indexer_name",
        "search_datasource_name",
        "created_at",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "id": {
            "bsonType": "string",
            "description": "Owns course_id everywhere else in the database",
        },
        "name": {"bsonType": "string"},
        "instructor_id": {"bsonType": "string", "description": "users.id"},
        "blob_container_name": {"bsonType": "string"},
        "search_index_name": {"bsonType": "string"},
        "search_indexer_name": {"bsonType": "string"},
        "search_datasource_name": {"bsonType": "string"},
        "created_at": {"bsonType": "date"},
    },
}

ENROLLMENT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["course_id", "user_id", "enrolled_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "course_id": {"bsonType": "string", "description": "courses.id"},
        "user_id": {"bsonType": "string", "description": "users.id"},
        "enrolled_at": {"bsonType": "date"},
    },
}

USER_INDEXES: IndexSpec = [
    ([("id", 1)], True, "Primary key"),
    ([("email", 1)], True, "Login by email; case-insensitive (collation strength 2)"),
    ([("sso_subject", 1)], True, "Login by SSO subject; unique only when set (partial index), as it may be null"),
]

COURSE_INDEXES: IndexSpec = [
    ([("id", 1)], True, "Primary key"),
    ([("instructor_id", 1)], False, "An instructor's courses"),
]

ENROLLMENT_INDEXES: IndexSpec = [
    ([("course_id", 1), ("user_id", 1)], True, "The composite primary key"),
    ([("user_id", 1)], False, "A student's courses"),
]

# Index options that a plain (keys, unique) call cannot express.
INDEX_OPTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "users": {
        "email_1": {"collation": {"locale": "en", "strength": 2}},
        # Partial, not sparse: MongoSystemStore writes sso_subject: null for
        # users without SSO, and a sparse index still indexes an explicit
        # null, so a second such user would be a duplicate key on a real
        # server. (SQLite's UNIQUE allowed many NULLs; this matches that.)
        "sso_subject_1": {"partialFilterExpression": {"sso_subject": {"$type": "string"}}},
    },
}

COLLECTIONS: dict[str, dict[str, Any]] = {
    "users": collection(USER_SCHEMA, USER_INDEXES),
    "courses": collection(COURSE_SCHEMA, COURSE_INDEXES),
    "enrollments": collection(ENROLLMENT_SCHEMA, ENROLLMENT_INDEXES),
}
