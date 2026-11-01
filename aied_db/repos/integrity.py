"""`IntegrityRepo` — reads and writes for the integrity collections.

Shaped after the routes in the task3 integrity draft: record an evaluation,
raise a violation, escalate, resolve, report.

The split that matters: the AGENT detects and the HUMAN resolves.
`escalate()` is an agent write; `resolve_escalation()` is an instructor write
and POLICY refuses it to every agent role. If you find yourself wanting an
agent to call it, that is the rule working, not a bug.

`submission_id` on any of these points at `assessment_results.submission_id`.
"""

from __future__ import annotations

from typing import Any, Optional

from .._util import new_id, now, strip_id, to_dt, to_iso


class IntegrityRepo:
    """Integrity sessions, records, violations, escalations and reports."""

    def __init__(self, db, course_id: str) -> None:
        if not course_id:
            raise ValueError("course_id is required")
        self._db = db
        self._course_id = course_id

    @property
    def course_id(self) -> str:
        return self._course_id

    def _scope(self, **extra: Any) -> dict[str, Any]:
        return {"course_id": self._course_id, **extra}

    @staticmethod
    def _row(document: Optional[dict], *date_fields: str) -> Optional[dict]:
        if document is None:
            return None
        row = strip_id(document) or {}
        for field in date_fields:
            if field in row:
                row[field] = to_iso(row[field])
        return row

    # ------------------------------------------------------------ sessions
    def open_session(self, session_id: str, student_id: str) -> dict:
        """Start tracking a student's interactions."""
        moment = now()
        document = {
            "session_id": session_id,
            "student_id": student_id,
            "course_id": self._course_id,
            "started_at": moment,
            "last_activity_at": moment,
            "status": "active",
        }
        self._db["integrity_sessions"].insert_one(document)
        return self._row(document, "started_at", "last_activity_at")

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._row(
            self._db["integrity_sessions"].find_one(self._scope(session_id=session_id)),
            "started_at",
            "last_activity_at",
        )

    def touch_session(self, session_id: str, *, status: Optional[str] = None) -> None:
        """Bump `last_activity_at`, and optionally the status.

        These are the only two fields an agent may change on a session."""
        updates: dict[str, Any] = {"last_activity_at": now()}
        if status is not None:
            updates["status"] = status
        self._db["integrity_sessions"].update_one(
            self._scope(session_id=session_id), {"$set": updates}
        )

    def list_sessions(self, *, student_id: Optional[str] = None, status: Optional[str] = None) -> list[dict]:
        query = self._scope()
        if student_id:
            query["student_id"] = student_id
        if status:
            query["status"] = status
        cursor = self._db["integrity_sessions"].find(query).sort("started_at", -1)
        return [self._row(d, "started_at", "last_activity_at") for d in cursor]

    # ------------------------------------------------------------- records
    def record_evaluation(
        self,
        *,
        student_id: str,
        issue_type: str,
        confidence: float,
        details: str,
        submission_id: Optional[str] = None,
        session_id: Optional[str] = None,
        record_id: Optional[str] = None,
    ) -> dict:
        """Append one integrity evaluation. Append-only.

        At least one of `submission_id` or `session_id` must be given, or the
        record refers to nothing.
        """
        if submission_id is None and session_id is None:
            raise ValueError("an integrity record needs a submission_id or a session_id")
        if not 0 <= confidence <= 1:
            raise ValueError(f"confidence must be between 0 and 1, got {confidence!r}")

        document = {
            "record_id": record_id or new_id(),
            "student_id": student_id,
            "course_id": self._course_id,
            "submission_id": submission_id,
            "session_id": session_id,
            "issue_type": issue_type,
            "confidence": float(confidence),
            "details": details,
            "evaluated_at": now(),
        }
        self._db["integrity_records"].insert_one(document)
        return self._row(document, "evaluated_at")

    def get_record(self, record_id: str) -> Optional[dict]:
        return self._row(
            self._db["integrity_records"].find_one(self._scope(record_id=record_id)),
            "evaluated_at",
        )

    def list_records(
        self,
        *,
        student_id: Optional[str] = None,
        submission_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> list[dict]:
        query = self._scope()
        if student_id:
            query["student_id"] = student_id
        if submission_id:
            query["submission_id"] = submission_id
        if session_id:
            query["session_id"] = session_id
        cursor = self._db["integrity_records"].find(query).sort("evaluated_at", -1)
        return [self._row(d, "evaluated_at") for d in cursor]

    # ---------------------------------------------------------- violations
    def add_violation(
        self,
        *,
        record_id: str,
        student_id: str,
        rule: str,
        description: str,
        session_id: Optional[str] = None,
        submission_id: Optional[str] = None,
        violation_id: Optional[str] = None,
    ) -> dict:
        """Record a rule violation against an existing evaluation."""
        document = {
            "violation_id": violation_id or new_id(),
            "record_id": record_id,
            "student_id": student_id,
            "course_id": self._course_id,
            "session_id": session_id,
            "submission_id": submission_id,
            "rule": rule,
            "description": description,
            "detected_at": now(),
        }
        self._db["integrity_violations"].insert_one(document)
        return self._row(document, "detected_at")

    def list_violations(
        self, *, student_id: Optional[str] = None, record_id: Optional[str] = None
    ) -> list[dict]:
        query = self._scope()
        if student_id:
            query["student_id"] = student_id
        if record_id:
            query["record_id"] = record_id
        cursor = self._db["integrity_violations"].find(query).sort("detected_at", -1)
        return [self._row(d, "detected_at") for d in cursor]

    def count_violations(self, student_id: str) -> int:
        """How many violations a student has, for escalation thresholds."""
        return self._db["integrity_violations"].count_documents(
            self._scope(student_id=student_id)
        )

    # --------------------------------------------------------- escalations
    def escalate(
        self,
        *,
        violation_id: str,
        student_id: str,
        reason: str,
        session_id: Optional[str] = None,
        escalation_id: Optional[str] = None,
    ) -> dict:
        """Raise an escalation for a human to review. An agent may do this."""
        document = {
            "escalation_id": escalation_id or new_id(),
            "violation_id": violation_id,
            "student_id": student_id,
            "course_id": self._course_id,
            "session_id": session_id,
            "reason": reason,
            "escalated_at": now(),
            "status": "pending_review",
            "reviewer_notes": None,
            "resolved_by": None,
            "resolved_at": None,
        }
        self._db["integrity_escalations"].insert_one(document)
        return self._row(document, "escalated_at", "resolved_at")

    def pending_escalations(self) -> list[dict]:
        """The instructor's review queue, oldest first."""
        cursor = (
            self._db["integrity_escalations"]
            .find(self._scope(status="pending_review"))
            .sort("escalated_at", 1)
        )
        return [self._row(d, "escalated_at", "resolved_at") for d in cursor]

    def resolve_escalation(
        self, escalation_id: str, *, reviewer_notes: str, resolved_by: str
    ) -> Optional[dict]:
        """Close an escalation. INSTRUCTOR ONLY — POLICY refuses this to agents.

        An agent resolving its own escalation is exactly what the Oct 26
        rules forbid, so this method is useless from an agent's connection
        and that is intentional.
        """
        target = self._db["integrity_escalations"].find_one(
            self._scope(escalation_id=escalation_id)
        )
        if target is None:
            return None
        updates = {
            "status": "resolved",
            "reviewer_notes": reviewer_notes,
            "resolved_by": resolved_by,
            "resolved_at": now(),
        }
        self._db["integrity_escalations"].update_one(
            self._scope(escalation_id=escalation_id), {"$set": updates}
        )
        return self._row({**target, **updates}, "escalated_at", "resolved_at")

    # ------------------------------------------------------------- reports
    def add_report(
        self,
        *,
        student_id: str,
        summary: str,
        risk_level: str,
        submission_id: Optional[str] = None,
        session_id: Optional[str] = None,
        report_id: Optional[str] = None,
    ) -> dict:
        """Write the final integrity summary. Append-only.

        Not student-facing: `integrity_reports` gives the STUDENT role no
        access at all.
        """
        if risk_level not in ("low", "medium", "high"):
            raise ValueError(f"risk_level must be low, medium or high, got {risk_level!r}")
        document = {
            "report_id": report_id or new_id(),
            "student_id": student_id,
            "course_id": self._course_id,
            "submission_id": submission_id,
            "session_id": session_id,
            "summary": summary,
            "risk_level": risk_level,
            "generated_at": now(),
        }
        self._db["integrity_reports"].insert_one(document)
        return self._row(document, "generated_at")

    def get_report(self, report_id: str) -> Optional[dict]:
        return self._row(
            self._db["integrity_reports"].find_one(self._scope(report_id=report_id)),
            "generated_at",
        )

    def list_reports(
        self, *, student_id: Optional[str] = None, submission_id: Optional[str] = None
    ) -> list[dict]:
        query = self._scope()
        if student_id:
            query["student_id"] = student_id
        if submission_id:
            query["submission_id"] = submission_id
        cursor = self._db["integrity_reports"].find(query).sort("generated_at", -1)
        return [self._row(d, "generated_at") for d in cursor]
