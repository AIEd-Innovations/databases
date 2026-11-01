"""`MongoGuardianStore` — a drop-in `GuardianStore`.

Implements ui-student's protocol exactly. Four SQLite tables become four
collections, and `get_session` reassembles them into the one dict the
GuardianAgent expects, with `questions` and `violations` embedded in
sequence order — the same shape `SqliteGuardianStore` built with two extra
SELECTs.

Parity details that are easy to get wrong, and are tested:

  - `create_session` raises `aied_db.IntegrityError` on a duplicate
    session_id. The agent catches it by NAME and treats the session as one
    that already exists, so this must not become a plain DuplicateKeyError.
  - SQLite defaulted a missing `course_id` to "CSC580". Here the store's own
    course_id wins, always: this store is already scoped to one course, and
    a stray agent default must not write into it.
  - Booleans come back as real bools, timestamps as ISO strings ending in Z.
  - `concept_tags` was a JSON string in SQLite and is an array here; it is
    returned as a list either way.
  - `course_id` is stripped from questions, violations and verifications,
    which had no such column, but KEPT on the session, which did.
"""

from __future__ import annotations

from typing import Any, Optional

from .._util import to_dt, to_iso


class MongoGuardianStore:
    """Guardian sessions, questions, violations and verifications."""

    _SESSION_DATES = ("started_at", "ended_at")
    _CHILD_INTERNAL = ("_id", "course_id")

    def __init__(self, db, course_id: str) -> None:
        if not course_id:
            raise ValueError("course_id is required: it is what isolates one course's data")
        self._db = db
        self._course_id = course_id

    # ----------------------------------------------------------- lifecycle
    def init(self) -> None:
        """No-op; see `MongoParticipantStore.init`."""
        return None

    def close(self) -> None:
        return None

    @property
    def course_id(self) -> str:
        return self._course_id

    # -------------------------------------------------------------- helpers
    def _child_row(self, document: dict[str, Any]) -> dict[str, Any]:
        row = {k: v for k, v in document.items() if k not in self._CHILD_INTERNAL}
        if "timestamp" in row:
            row["timestamp"] = to_iso(row["timestamp"])
        return row

    # ------------------------------------------------------------ sessions
    def create_session(self, doc: dict) -> None:
        """Open a session.

        `doc` keys: session_id, student_id, lab_id, course_id, started_at.
        A duplicate session_id raises `IntegrityError`, which is how the
        agent detects an already-open session.
        """
        document = {
            "session_id": doc["session_id"],
            "student_id": doc["student_id"],
            "lab_id": doc["lab_id"],
            # The store's course wins over whatever default the agent passed.
            "course_id": self._course_id,
            "started_at": to_dt(doc["started_at"]),
            "ended_at": None,
            "question_count": 0,
            "violation_count": 0,
            "escalated": False,
            "status": "active",
            "report_generated": False,
            "report_id": None,
        }
        self._db["guardian_sessions"].insert_one(document)

    def get_session(self, session_id: str, student_id: str) -> Optional[dict]:
        """The session with its questions and violations embedded, or None."""
        document = self._db["guardian_sessions"].find_one(
            {
                "session_id": session_id,
                "student_id": student_id,
                "course_id": self._course_id,
            }
        )
        if document is None:
            return None

        session = {k: v for k, v in document.items() if k != "_id"}
        for field in self._SESSION_DATES:
            session[field] = to_iso(session.get(field))
        session["escalated"] = bool(session.get("escalated"))
        session["report_generated"] = bool(session.get("report_generated"))
        session["questions"] = self._fetch_questions(session_id)
        session["violations"] = self._fetch_violations(session_id)
        return session

    def _fetch_questions(self, session_id: str) -> list[dict]:
        cursor = (
            self._db["guardian_questions"]
            .find({"session_id": session_id, "course_id": self._course_id})
            .sort("sequence_number", 1)
        )
        rows = []
        for document in cursor:
            row = self._child_row(document)
            row["violation"] = bool(row.get("violation"))
            row["concept_tags"] = list(row.get("concept_tags") or [])
            rows.append(row)
        return rows

    def _fetch_violations(self, session_id: str) -> list[dict]:
        cursor = (
            self._db["guardian_violations"]
            .find({"session_id": session_id, "course_id": self._course_id})
            .sort("sequence_number", 1)
        )
        return [self._child_row(document) for document in cursor]

    def update_session_counters(
        self,
        session_id: str,
        student_id: str,
        *,
        question_count: int,
        violation_count: int,
        escalated: bool,
    ) -> None:
        """Update the three counters. These are the only session fields the
        agent may change, and POLICY enforces that independently."""
        self._db["guardian_sessions"].update_one(
            {
                "session_id": session_id,
                "student_id": student_id,
                "course_id": self._course_id,
            },
            {
                "$set": {
                    "question_count": int(question_count),
                    "violation_count": int(violation_count),
                    "escalated": bool(escalated),
                }
            },
        )

    def close_session(
        self,
        session_id: str,
        student_id: str,
        ended_at: str,
        report_id: Optional[str],
    ) -> None:
        """Close the session. `report_generated` is derived from `report_id`,
        exactly as the SQLite store derived it."""
        self._db["guardian_sessions"].update_one(
            {
                "session_id": session_id,
                "student_id": student_id,
                "course_id": self._course_id,
            },
            {
                "$set": {
                    "status": "closed",
                    "ended_at": to_dt(ended_at),
                    "report_generated": bool(report_id),
                    "report_id": report_id,
                }
            },
        )

    # ------------------------------------------------------------- inserts
    def insert_question(self, session_id: str, student_id: str, record: dict) -> None:
        self._db["guardian_questions"].insert_one(
            {
                "question_id": record["question_id"],
                "session_id": session_id,
                "student_id": student_id,
                "course_id": self._course_id,
                "sequence_number": int(record["sequence_number"]),
                "timestamp": to_dt(record["timestamp"]),
                "text": record["text"],
                "classification": record["classification"],
                "violation": bool(record.get("violation")),
                "violation_type": record.get("violation_type"),
                "concept_tags": list(record.get("concept_tags") or []),
            }
        )

    def insert_violation(self, session_id: str, student_id: str, record: dict) -> None:
        self._db["guardian_violations"].insert_one(
            {
                "violation_id": record["violation_id"],
                "question_id": record["question_id"],
                "session_id": session_id,
                "student_id": student_id,
                "course_id": self._course_id,
                "sequence_number": int(record["sequence_number"]),
                "timestamp": to_dt(record["timestamp"]),
                "violation_type": record["violation_type"],
                "severity": record["severity"],
                "question_text": record["question_text"],
            }
        )

    def insert_verification(self, session_id: str, student_id: str, record: dict) -> None:
        self._db["guardian_verifications"].insert_one(
            {
                "verification_id": record["verification_id"],
                "session_id": session_id,
                "student_id": student_id,
                "course_id": self._course_id,
                "question_id": record.get("question_id"),
                "timestamp": to_dt(record["timestamp"]),
                "passes": bool(record["passes"]),
                "reason": record.get("reason"),
                "guidance_level": record.get("guidance_level", "FULL"),
                "draft_excerpt": record.get("draft_excerpt", ""),
            }
        )
