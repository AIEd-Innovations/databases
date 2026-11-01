"""Participant and Guardian collections.

Ported from ui-student's `agentic_system/store/sqlite.py` (@6f71458):
PARTICIPANT_SCHEMA -> `interactions`, and GUARDIAN_SCHEMA's four tables ->
`guardian_sessions`, `guardian_questions`, `guardian_violations`,
`guardian_verifications`. The `guardian_` prefix is added because `sessions`,
`questions` and `violations` are far too generic to share one database with
the integrity and lab-companion collections.

Column names are preserved exactly: the SQLite stores returned `dict(row)`
from `SELECT *`, so the agents read these keys by name.

The one structural change: SQLite isolated courses by using a separate file
per course, so only `sessions` carried a `course_id`. One shared database has
no files to separate, so EVERY document here carries `course_id` and the
stores filter on it. To keep the returned dicts byte-identical to the SQLite
ones, `MongoParticipantStore` and `MongoGuardianStore` project `course_id`
out of `interactions`, `guardian_questions`, `guardian_violations` and
`guardian_verifications` reads — it is an isolation key, not part of the
agent-facing shape.

SQLite booleans were INTEGER 0/1 and were cast with `bool()` on read; here
they are real booleans. `concept_tags` was a JSON string; here it is an array.
"""

from __future__ import annotations

from typing import Any

from ._common import IndexSpec, collection, nullable

# `classification` and `violation_type` are free-form strings upstream: the
# Guardian's taxonomy is still changing, so the validator does not pin them.
SEVERITIES = ["low", "medium", "high"]
GUIDANCE_LEVELS = ["FULL", "MODERATE", "MINIMAL", "REJECTED"]
SESSION_STATUSES = ["active", "closed"]

INTERACTION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "id",
        "course_id",
        "student_id",
        "session_id",
        "timestamp",
        "message",
        "question_type",
        "hint_level",
        "difficulty",
        "response_time_ms",
        "lab_id",
        "logged_at",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "id": {"bsonType": "string", "description": "UUID4"},
        "course_id": {"bsonType": "string", "description": "Isolation key; added by the store"},
        "student_id": {"bsonType": "string"},
        "session_id": {"bsonType": "string"},
        "timestamp": {
            "bsonType": "date",
            "description": "When the interaction happened, per the caller",
        },
        # Set by the store on every write, never by the caller. `timestamp` is
        # the agent's claim about when something happened; this is when the
        # database actually recorded it. They differ for a replayed queue, a
        # clock-skewed host or a migrated row, and only this one can be
        # trusted for an audit of what was logged.
        "logged_at": {
            "bsonType": "date",
            "description": "When this row was written. Set by the writer, never by the caller",
        },
        "message": {"bsonType": "string", "description": "The student's message text"},
        "question_type": {"bsonType": "string"},
        "hint_level": {"bsonType": "int", "minimum": 0},
        "difficulty": {"bsonType": "string"},
        "response_time_ms": {"bsonType": "int", "minimum": 0},
        "lab_id": {"bsonType": "string"},
        # participant-agent's classifier produces these two. Optional, because
        # ui-student writes interactions without them.
        "classification_confidence": {
            "bsonType": nullable("double"),
            "minimum": 0,
            "maximum": 1,
            "description": "The classifier's confidence in question_type/difficulty",
        },
        "feedback_score": {
            "bsonType": nullable("string"),
            "description": "The student's reaction to the answer, when they gave one",
        },
    },
}

GUARDIAN_SESSION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "session_id",
        "student_id",
        "lab_id",
        "course_id",
        "started_at",
        "question_count",
        "violation_count",
        "escalated",
        "status",
        "report_generated",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "session_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "lab_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "started_at": {"bsonType": "date"},
        "ended_at": {"bsonType": nullable("date")},
        "question_count": {"bsonType": "int", "minimum": 0},
        "violation_count": {"bsonType": "int", "minimum": 0},
        "escalated": {"bsonType": "bool"},
        "status": {"enum": SESSION_STATUSES},
        "report_generated": {"bsonType": "bool"},
        "report_id": {"bsonType": nullable("string")},
    },
}

GUARDIAN_QUESTION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "question_id",
        "session_id",
        "student_id",
        "course_id",
        "sequence_number",
        "timestamp",
        "text",
        "classification",
        "violation",
        "concept_tags",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "question_id": {"bsonType": "string"},
        "session_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "sequence_number": {"bsonType": "int", "minimum": 0},
        "timestamp": {"bsonType": "date"},
        "text": {"bsonType": "string", "description": "The question as asked"},
        "classification": {"bsonType": "string"},
        "violation": {"bsonType": "bool"},
        "violation_type": {"bsonType": nullable("string")},
        "concept_tags": {"bsonType": "array", "items": {"bsonType": "string"}},
    },
}

GUARDIAN_VIOLATION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "violation_id",
        "question_id",
        "session_id",
        "student_id",
        "course_id",
        "sequence_number",
        "timestamp",
        "violation_type",
        "severity",
        "question_text",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "violation_id": {"bsonType": "string"},
        "question_id": {"bsonType": "string", "description": "guardian_questions.question_id"},
        "session_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "sequence_number": {"bsonType": "int", "minimum": 0},
        "timestamp": {"bsonType": "date"},
        "violation_type": {"bsonType": "string"},
        "severity": {"enum": SEVERITIES},
        "question_text": {"bsonType": "string"},
    },
}

GUARDIAN_VERIFICATION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "verification_id",
        "session_id",
        "student_id",
        "course_id",
        "timestamp",
        "passes",
        "guidance_level",
        "draft_excerpt",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "verification_id": {"bsonType": "string"},
        "session_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "question_id": {"bsonType": nullable("string")},
        "timestamp": {"bsonType": "date"},
        "passes": {"bsonType": "bool"},
        "reason": {"bsonType": nullable("string")},
        "guidance_level": {"enum": GUIDANCE_LEVELS},
        "draft_excerpt": {"bsonType": "string"},
    },
}

INTERACTION_INDEXES: IndexSpec = [
    ([("id", 1)], True, "Primary key"),
    ([("course_id", 1), ("student_id", 1), ("timestamp", 1)],
     False, "fetch_for_student: all of a student's interactions, oldest first"),
    ([("course_id", 1), ("student_id", 1), ("session_id", 1)],
     False, "One session's interactions"),
]

GUARDIAN_SESSION_INDEXES: IndexSpec = [
    ([("session_id", 1)], True,
     "Primary key; the unique violation is what makes create_session idempotent"),
    ([("course_id", 1), ("student_id", 1)], False, "get_session filters on both"),
    ([("course_id", 1), ("lab_id", 1)], False, "A lab's sessions"),
    ([("course_id", 1), ("status", 1), ("escalated", 1)], False, "Open or escalated sessions"),
]

GUARDIAN_QUESTION_INDEXES: IndexSpec = [
    ([("question_id", 1)], True, "Primary key"),
    ([("session_id", 1), ("sequence_number", 1)], True,
     "get_session reads these in sequence order; the pair cannot repeat"),
]

GUARDIAN_VIOLATION_INDEXES: IndexSpec = [
    ([("violation_id", 1)], True, "Primary key"),
    ([("session_id", 1), ("sequence_number", 1)], False, "get_session, in sequence order"),
    ([("course_id", 1), ("student_id", 1)], False, "A student's violations across sessions"),
]

GUARDIAN_VERIFICATION_INDEXES: IndexSpec = [
    ([("verification_id", 1)], True, "Primary key"),
    ([("session_id", 1), ("timestamp", 1)], False, "One session's verifications"),
]

COLLECTIONS: dict[str, dict[str, Any]] = {
    "interactions": collection(INTERACTION_SCHEMA, INTERACTION_INDEXES),
    "guardian_sessions": collection(GUARDIAN_SESSION_SCHEMA, GUARDIAN_SESSION_INDEXES),
    "guardian_questions": collection(GUARDIAN_QUESTION_SCHEMA, GUARDIAN_QUESTION_INDEXES),
    "guardian_violations": collection(GUARDIAN_VIOLATION_SCHEMA, GUARDIAN_VIOLATION_INDEXES),
    "guardian_verifications": collection(
        GUARDIAN_VERIFICATION_SCHEMA, GUARDIAN_VERIFICATION_INDEXES
    ),
}
