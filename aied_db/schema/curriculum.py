"""Curriculum Designer collections: labs, lab_files.

COPY — DO NOT EDIT HERE FIRST.
Source of truth: AIEd-Innovations/curriculum-designer, `services/schema.py`
Copied from commit 93ec2c0 (2026-10-26). Shreevatson owns that file; change
it there, then re-copy, so the two cannot drift apart silently.

Everything below the header is upstream's text verbatim, except that
upstream's `validator_for` / `ensure_collection` / `ensure_all` are omitted:
applying the schema is `aied_db.schema.ensure_all`'s job for all 25
collections at once. `tests/test_curriculum_parity.py` fails if this copy
stops matching upstream.
"""

from __future__ import annotations

from typing import Any

# ── Enumerations, mirroring the Literal types in models/curriculum.py ─────────

QUESTION_TYPES = ["short_answer", "multiple_choice", "code", "essay"]
DIFFICULTY_TIERS = ["basic", "intermediate", "challenge"]
APPROVAL_STATUSES = ["pending", "approved", "needs_changes"]
MATERIAL_WEIGHTS = ["strict", "balanced", "expansive"]
FILE_KINDS = ["material", "instructions"]


def _nullable(bson_type: str) -> list[str]:
    return [bson_type, "null"]


# ── Embedded documents ───────────────────────────────────────────────────────

QUIZ_QUESTION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["id", "question", "type", "expected_answer", "rubric_points", "difficulty"],
    "additionalProperties": False,
    "properties": {
        "id": {"bsonType": "string", "description": "q1, q2, … unique within the quiz"},
        "question": {"bsonType": "string"},
        "type": {"enum": QUESTION_TYPES},
        "expected_answer": {"bsonType": "string"},
        "rubric_points": {"bsonType": "int", "minimum": 0},
        "difficulty": {"enum": DIFFICULTY_TIERS},
        "learning_objective_ref": {
            "bsonType": _nullable("string"),
            "description": "Matches one entry of labs.learning_objectives, by string",
        },
    },
}

RUBRIC_CRITERION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["name", "weight", "description"],
    "additionalProperties": False,
    "properties": {
        "name": {"bsonType": "string"},
        "weight": {"bsonType": "double", "minimum": 0, "maximum": 1},
        "description": {"bsonType": "string"},
    },
}

RUBRIC_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["code_weight", "report_weight", "manual_weight", "criteria"],
    "additionalProperties": False,
    "properties": {
        "code_weight": {"bsonType": "double", "minimum": 0, "maximum": 1},
        "report_weight": {"bsonType": "double", "minimum": 0, "maximum": 1},
        "manual_weight": {"bsonType": "double", "minimum": 0, "maximum": 1},
        "criteria": {"bsonType": "array", "items": RUBRIC_CRITERION_SCHEMA},
    },
}

FEEDBACK_ENTRY_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["feedback", "requested_by", "requested_at", "resulting_version"],
    "additionalProperties": False,
    "properties": {
        "feedback": {"bsonType": "string"},
        "requested_by": {"bsonType": "string"},
        "requested_at": {"bsonType": "date"},
        "resulting_version": {
            "bsonType": "int",
            "minimum": 1,
            "description": "The version produced AFTER this feedback was applied",
        },
    },
}

# ── Collections ──────────────────────────────────────────────────────────────

LAB_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "lab_id",
        "course_id",
        "title",
        "spec_markdown",
        "quiz",
        "rubric",
        "learning_objectives",
        "difficulty",
        "estimated_duration_min",
        "material_weight",
        "approval_status",
        "version",
        "generated_at",
        "last_updated",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "lab_id": {"bsonType": "string", "description": "Unique within a course"},
        "course_id": {"bsonType": "string"},
        "title": {"bsonType": "string"},
        "spec_markdown": {"bsonType": "string", "description": "The lab handout"},
        "quiz": {"bsonType": "array", "items": QUIZ_QUESTION_SCHEMA},
        "rubric": RUBRIC_SCHEMA,
        "learning_objectives": {"bsonType": "array", "items": {"bsonType": "string"}},
        "difficulty": {"bsonType": "string"},
        "estimated_duration_min": {"bsonType": "int", "minimum": 1},
        "material_weight": {"enum": MATERIAL_WEIGHTS},
        "material_content": {
            "bsonType": _nullable("string"),
            "description": "Derived: the extracted text of this lab's material files, merged",
        },
        "agent_instructions": {"bsonType": _nullable("string")},
        "approval_status": {"enum": APPROVAL_STATUSES},
        "approved_by": {"bsonType": _nullable("string")},
        "approval_notes": {"bsonType": _nullable("string")},
        "feedback_history": {"bsonType": "array", "items": FEEDBACK_ENTRY_SCHEMA},
        "version": {"bsonType": "int", "minimum": 1},
        "generated_at": {"bsonType": "date"},
        "last_updated": {"bsonType": "date"},
    },
}

LAB_FILE_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "file_id",
        "lab_id",
        "course_id",
        "filename",
        "content_type",
        "size_bytes",
        "kind",
        "uploaded_at",
        "storage_key",
        "extracted_text",
        "char_count",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "file_id": {"bsonType": "string", "description": "UUID4"},
        "lab_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "filename": {"bsonType": "string"},
        "content_type": {"bsonType": "string"},
        "size_bytes": {"bsonType": "int", "minimum": 0},
        "kind": {"enum": FILE_KINDS},
        "uploaded_by": {"bsonType": _nullable("string")},
        "uploaded_at": {"bsonType": "date"},
        "storage_key": {
            "bsonType": "string",
            "description": "labs/{course_id}/{lab_id}/{file_id}/{filename} in shared file storage",
        },
        "extracted_text": {"bsonType": "string"},
        "char_count": {"bsonType": "int", "minimum": 0},
    },
}

# ── Indexes ──────────────────────────────────────────────────────────────────
# (keys, unique, why)

LAB_INDEXES: list[tuple[list[tuple[str, int]], bool, str]] = [
    ([("course_id", 1), ("lab_id", 1)], True, "The real primary key"),
    ([("course_id", 1), ("approval_status", 1)], False, "Labs awaiting approval"),
]

LAB_FILE_INDEXES: list[tuple[list[tuple[str, int]], bool, str]] = [
    ([("file_id", 1)], True, "Fetch or delete one file"),
    ([("course_id", 1), ("lab_id", 1), ("uploaded_at", -1)], False, "List a lab's files"),
]

COLLECTIONS: dict[str, dict[str, Any]] = {
    "labs": {"schema": LAB_SCHEMA, "indexes": LAB_INDEXES},
    "lab_files": {"schema": LAB_FILE_SCHEMA, "indexes": LAB_FILE_INDEXES},
}


