"""Integrity collections: sessions, records, violations, escalations, reports.

Ported from the task3 integrity draft (`integrity_schema.py`, Rocio's team),
which is the current source for integrity — unlike the task3 assessment
draft, which Sean's redesign superseded.

Changes from the draft, all deliberate:
  - `course_id` added to every collection. The draft has no tenancy field at
    all; one shared database needs one, and every other collection here has
    it.
  - `confidence` is bounded 0..1. The draft left it an unbounded float.
  - `submission_id` points at `assessment_results.submission_id` (the
    decision of record), NOT at a submissions collection: Sean's redesign
    has no such collection.
  - Timestamps are BSON dates rather than Python datetimes-on-the-wire.

OPEN QUESTION — do not resolve this in code without the team.
`integrity_sessions` here and `guardian_sessions` in
`schema/participant_guardian.py` are two models of the same thing. The
guardian one is what ui-student actually runs today; this one comes from the
draft. They are both created so neither team is blocked, but they SHOULD be
merged. Likewise `integrity_violations` overlaps `guardian_violations`. Until
that is settled, write integrity data through `IntegrityRepo` and guardian
data through `MongoGuardianStore`, and do not cross-read.
"""

from __future__ import annotations

from typing import Any

from ._common import IndexSpec, collection, nullable

SESSION_STATUSES = ["active", "closed", "escalated", "restricted"]
ESCALATION_STATUSES = ["pending_review", "resolved"]
RISK_LEVELS = ["low", "medium", "high"]

INTEGRITY_SESSION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["session_id", "student_id", "course_id", "started_at", "last_activity_at", "status"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "session_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string", "description": "users.id"},
        "course_id": {"bsonType": "string", "description": "courses.id"},
        "started_at": {"bsonType": "date"},
        "last_activity_at": {"bsonType": "date"},
        "status": {"enum": SESSION_STATUSES},
    },
}

INTEGRITY_RECORD_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["record_id", "student_id", "course_id", "issue_type", "confidence", "details", "evaluated_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "record_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "submission_id": {
            "bsonType": nullable("string"),
            "description": "assessment_results.submission_id; null for chat-only evaluations",
        },
        "session_id": {
            "bsonType": nullable("string"),
            "description": "integrity_sessions.session_id; null for submission-only evaluations",
        },
        "issue_type": {
            "bsonType": "string",
            "description": "ex: plagiarism, ai_generated, unusual_pattern. Free-form: "
                           "the taxonomy is still changing",
        },
        "confidence": {"bsonType": ["double", "int"], "minimum": 0, "maximum": 1},
        "details": {"bsonType": "string", "description": "Explanation or evidence"},
        "evaluated_at": {"bsonType": "date"},
    },
}

INTEGRITY_VIOLATION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["violation_id", "record_id", "student_id", "course_id", "rule", "description", "detected_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "violation_id": {"bsonType": "string"},
        "record_id": {"bsonType": "string", "description": "integrity_records.record_id"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "session_id": {"bsonType": nullable("string")},
        "submission_id": {"bsonType": nullable("string")},
        "rule": {"bsonType": "string", "description": "ex: requested_solution, copy_paste_detected"},
        "description": {"bsonType": "string"},
        "detected_at": {"bsonType": "date"},
    },
}

INTEGRITY_ESCALATION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["escalation_id", "violation_id", "student_id", "course_id", "reason", "escalated_at", "status"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "escalation_id": {"bsonType": "string"},
        "violation_id": {"bsonType": "string", "description": "integrity_violations.violation_id"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "session_id": {
            "bsonType": nullable("string"),
            "description": "Required in the draft, nullable here: a submission-only "
                           "escalation has no session",
        },
        "reason": {"bsonType": "string", "description": "ex: multiple_violations, high_confidence_plagiarism"},
        "escalated_at": {"bsonType": "date"},
        "status": {"enum": ESCALATION_STATUSES},
        "reviewer_notes": {
            "bsonType": nullable("string"),
            "description": "Instructor-written. An agent may NOT resolve its own escalation",
        },
        "resolved_by": {"bsonType": nullable("string"), "description": "users.id of the instructor"},
        "resolved_at": {"bsonType": nullable("date")},
    },
}

INTEGRITY_REPORT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["report_id", "student_id", "course_id", "summary", "risk_level", "generated_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "report_id": {"bsonType": "string"},
        "student_id": {"bsonType": "string"},
        "course_id": {"bsonType": "string"},
        "submission_id": {"bsonType": nullable("string")},
        "session_id": {"bsonType": nullable("string")},
        "summary": {"bsonType": "string"},
        "risk_level": {"enum": RISK_LEVELS},
        "generated_at": {"bsonType": "date"},
    },
}

INTEGRITY_SESSION_INDEXES: IndexSpec = [
    ([("session_id", 1)], True, "Primary key"),
    ([("course_id", 1), ("student_id", 1), ("started_at", -1)], False, "A student's sessions"),
    ([("course_id", 1), ("status", 1)], False, "Active or escalated sessions"),
]

INTEGRITY_RECORD_INDEXES: IndexSpec = [
    ([("record_id", 1)], True, "Primary key"),
    ([("course_id", 1), ("student_id", 1), ("evaluated_at", -1)], False, "A student's records"),
    ([("submission_id", 1)], False, "Every evaluation of one submission"),
    ([("session_id", 1), ("evaluated_at", 1)], False, "One session's records, in order"),
]

INTEGRITY_VIOLATION_INDEXES: IndexSpec = [
    ([("violation_id", 1)], True, "Primary key"),
    ([("record_id", 1)], False, "The violations a record produced"),
    ([("course_id", 1), ("student_id", 1), ("detected_at", -1)], False, "A student's violations"),
]

INTEGRITY_ESCALATION_INDEXES: IndexSpec = [
    ([("escalation_id", 1)], True, "Primary key"),
    ([("violation_id", 1)], False, "The escalation for one violation"),
    ([("course_id", 1), ("status", 1), ("escalated_at", 1)],
     False, "The instructor's pending_review queue, oldest first"),
]

INTEGRITY_REPORT_INDEXES: IndexSpec = [
    ([("report_id", 1)], True, "Primary key"),
    ([("course_id", 1), ("student_id", 1), ("generated_at", -1)], False, "A student's reports"),
    ([("submission_id", 1)], False, "The report for one submission"),
    ([("session_id", 1)], False, "The report for one session"),
]

COLLECTIONS: dict[str, dict[str, Any]] = {
    "integrity_sessions": collection(INTEGRITY_SESSION_SCHEMA, INTEGRITY_SESSION_INDEXES),
    "integrity_records": collection(INTEGRITY_RECORD_SCHEMA, INTEGRITY_RECORD_INDEXES),
    "integrity_violations": collection(INTEGRITY_VIOLATION_SCHEMA, INTEGRITY_VIOLATION_INDEXES),
    "integrity_escalations": collection(INTEGRITY_ESCALATION_SCHEMA, INTEGRITY_ESCALATION_INDEXES),
    "integrity_reports": collection(INTEGRITY_REPORT_SCHEMA, INTEGRITY_REPORT_INDEXES),
}
