"""Assessment Agent collections: assignments, assignment_files, assessment_results.

Ported from AIEd-Innovations/assessment_agent @c019f0f (2026-10-26), which is
Sean's redesign: "the agent only grades". `assignments` mirrors `spec.py`'s
`GradingSpec`, `assessment_results` mirrors `models.py`'s `AssessmentResult`,
and `assignment_files` replaces the `solution/`, `materials/` and `originals/`
folders that `spec.py` reads off disk.

What is deliberately NOT here, because the redesign removed it: test cases,
report evaluation, anomaly reports, a review queue, and the submitted text
itself. The task3 assessment draft still describes those; it is out of date.
If you are about to add a `test_cases` field, talk to Sean first.

Two rules the validator cannot enforce on its own, so they are the stores'
and repos' job:

  - SECRETS. `assignments.criteria[].expected_answer` and any
    `assignment_files` row with `kind: "solution"` must never reach a
    student. Upstream enforces this with `exclude=True` on the Pydantic
    fields; here `AssessmentRepo` has separate instructor and student reads.
  - INSERT-ONLY. `assessment_results` is never updated to re-grade; a regrade
    is a new submission_id. The single exception is an instructor review,
    which sets `status`, `final_score`, `instructor_notes` and `reviewed_at`
    — and `final_score` REPLACES `score` when present, it does not add to it.
"""

from __future__ import annotations

from typing import Any

from ._common import IndexSpec, collection, nullable, nullable_object

# models.py: ResultStatus
RESULT_STATUSES = ["ai_graded", "needs_review", "instructor_graded"]
FILE_KINDS = ["solution", "material"]

# ── Embedded documents ───────────────────────────────────────────────────────

CRITERION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["id", "name", "max_points"],
    "additionalProperties": False,
    "properties": {
        "id": {"bsonType": "string", "description": "Unique within the assignment"},
        "name": {"bsonType": "string"},
        "description": {"bsonType": "string"},
        "max_points": {
            "bsonType": ["double", "int"],
            # MongoDB's $jsonSchema is draft-4: exclusiveMinimum is a boolean
            # that modifies `minimum`, not a number on its own.
            "minimum": 0,
            "exclusiveMinimum": True,
            "description": "Positive; spec.py rejects zero or negative",
        },
        "expected_answer": {
            "bsonType": "string",
            "description": "SECRET. Quiz answer key; never returned to a student",
        },
    },
}

CRITERION_SCORE_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["id", "name", "score", "max_points"],
    "additionalProperties": False,
    "properties": {
        "id": {"bsonType": "string", "description": "The assignments.criteria[].id scored"},
        "name": {"bsonType": "string"},
        "score": {"bsonType": ["double", "int"], "minimum": 0},
        "max_points": {"bsonType": ["double", "int"], "minimum": 0},
        "justification": {"bsonType": "string"},
        "evidence": {"bsonType": "string"},
    },
}

FEEDBACK_REPORT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"bsonType": "string"},
        "strengths": {"bsonType": "array", "items": {"bsonType": "string"}},
        "improvements": {"bsonType": "array", "items": {"bsonType": "string"}},
        "next_steps": {"bsonType": "array", "items": {"bsonType": "string"}},
    },
}

LLM_CALL_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["stage", "latency_ms", "success", "timestamp"],
    "additionalProperties": False,
    "properties": {
        "stage": {"bsonType": "string"},
        "latency_ms": {"bsonType": "int", "minimum": 0},
        "success": {"bsonType": "bool"},
        "error": {"bsonType": "string"},
        "timestamp": {"bsonType": "date"},
    },
}

PROVENANCE_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["submission_hash", "spec_hash", "model_id", "created_at"],
    "additionalProperties": False,
    "properties": {
        "submission_hash": {
            "bsonType": "string",
            "description": "Hash of the submission; the text itself is not stored",
        },
        "spec_hash": {"bsonType": "string", "description": "Which spec version graded it"},
        "model_id": {"bsonType": "string"},
        "llm_calls": {"bsonType": "array", "items": LLM_CALL_SCHEMA},
        "created_at": {"bsonType": "date"},
    },
}

# ── Collections ──────────────────────────────────────────────────────────────

ASSIGNMENT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["assignment_id", "course_id", "criteria", "model", "max_score", "created_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "assignment_id": {
            "bsonType": "string",
            "pattern": "^[A-Za-z0-9_-]+$",
            "description": "spec.py's _ID_PATTERN; unique within a course",
        },
        "course_id": {"bsonType": "string", "description": "courses.id"},
        "title": {"bsonType": "string"},
        "description": {"bsonType": "string"},
        "criteria": {
            "bsonType": "array",
            "minItems": 1,
            "items": CRITERION_SCHEMA,
            "description": "At least one; the total score is their sum",
        },
        "guidance": {"bsonType": "string", "description": "Grading guidance for the model"},
        "model": {
            "bsonType": "string",
            "description": "Every submission for this assignment is graded by this model",
        },
        "source": {
            "bsonType": "string",
            "description": "'built-in', 'upload', or the curriculum-designer lab it came from",
        },
        "imported_from_lab": {
            "bsonType": nullable("object"),
            "additionalProperties": False,
            "required": ["course_id", "lab_id"],
            "properties": {
                "course_id": {"bsonType": "string"},
                "lab_id": {"bsonType": "string"},
            },
            "description": "Set when imported from labs; the import takes a COPY, "
                           "so assignments (points) and labs (weights) stay separate",
        },
        "max_score": {
            "bsonType": ["double", "int"],
            "minimum": 0,
            "description": "Derived: sum of criteria[].max_points (spec.py computed_field)",
        },
        "created_at": {"bsonType": "date"},
        "updated_at": {"bsonType": nullable("date")},
    },
}

ASSIGNMENT_FILE_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "file_id",
        "assignment_id",
        "course_id",
        "filename",
        "kind",
        "original",
        "extracted_text",
        "char_count",
        "uploaded_at",
        "storage_key",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "file_id": {"bsonType": "string", "description": "UUID4"},
        "assignment_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "filename": {"bsonType": "string", "pattern": "^[\\w .()-]+$"},
        "content_type": {"bsonType": nullable("string")},
        "size_bytes": {"bsonType": ["int", "null"], "minimum": 0},
        "kind": {
            "enum": FILE_KINDS,
            "description": "SECRET when 'solution': reference solutions and answer keys",
        },
        "original": {
            "bsonType": "bool",
            "description": "True for originals/ — the upload exactly as received; "
                           "False for the extracted-text copy spec.py reads",
        },
        "uploaded_by": {"bsonType": nullable("string"), "description": "users.id"},
        "uploaded_at": {"bsonType": "date"},
        "storage_key": {
            "bsonType": "string",
            "description": "assignments/{course_id}/{assignment_id}/{kind}/{file_id}/{filename}",
        },
        "extracted_text": {"bsonType": "string"},
        "char_count": {"bsonType": "int", "minimum": 0},
    },
}

ASSESSMENT_RESULT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": [
        "submission_id",
        "student_id",
        "assignment_id",
        "course_id",
        "files",
        "model",
        "mock",
        "criteria",
        "max_score",
        "status",
        "created_at",
    ],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "submission_id": {"bsonType": "string", "description": "One grading attempt"},
        "student_id": {"bsonType": "string"},
        "assignment_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "files": {
            "bsonType": "array",
            "items": {"bsonType": "string"},
            "description": "Filenames ONLY; the submitted text is never stored",
        },
        "model": {"bsonType": "string", "description": "'mock' when no API key was provided"},
        "mock": {
            "bsonType": "bool",
            "description": "True when graded offline by the mock model: not a real assessment",
        },
        "criteria": {"bsonType": "array", "items": CRITERION_SCORE_SCHEMA},
        "score": {
            "bsonType": ["double", "int", "null"],
            "minimum": 0,
            "description": "Sum of criterion scores; null when grading failed",
        },
        "max_score": {"bsonType": ["double", "int"], "minimum": 0},
        "feedback": nullable_object(FEEDBACK_REPORT_SCHEMA),
        "status": {"enum": RESULT_STATUSES},
        "review_reasons": {"bsonType": "array", "items": {"bsonType": "string"}},
        "final_score": {
            "bsonType": ["double", "int", "null"],
            "minimum": 0,
            "description": "Set by an instructor; REPLACES score. 0..max_score",
        },
        "instructor_notes": {"bsonType": "string"},
        "reviewed_at": {"bsonType": nullable("date")},
        "created_at": {"bsonType": "date"},
        "provenance": nullable_object(PROVENANCE_SCHEMA),
    },
}

ASSIGNMENT_INDEXES: IndexSpec = [
    ([("course_id", 1), ("assignment_id", 1)], True, "The real primary key"),
    ([("course_id", 1), ("created_at", -1)], False, "List a course's assignments"),
]

ASSIGNMENT_FILE_INDEXES: IndexSpec = [
    ([("file_id", 1)], True, "Fetch one file"),
    ([("course_id", 1), ("assignment_id", 1), ("kind", 1), ("original", 1)],
     False, "spec.py's per-kind reads; keeps solutions separable from materials"),
]

ASSESSMENT_RESULT_INDEXES: IndexSpec = [
    ([("submission_id", 1)], True, "Primary key; insert-only"),
    ([("course_id", 1), ("student_id", 1), ("created_at", -1)],
     False, "A student's results, newest first"),
    ([("course_id", 1), ("assignment_id", 1), ("created_at", -1)],
     False, "One assignment's results"),
    ([("course_id", 1), ("status", 1), ("created_at", 1)],
     False, "The needs_review work queue, oldest first"),
]

COLLECTIONS: dict[str, dict[str, Any]] = {
    "assignments": collection(ASSIGNMENT_SCHEMA, ASSIGNMENT_INDEXES),
    "assignment_files": collection(ASSIGNMENT_FILE_SCHEMA, ASSIGNMENT_FILE_INDEXES),
    "assessment_results": collection(ASSESSMENT_RESULT_SCHEMA, ASSESSMENT_RESULT_INDEXES),
}
