"""`MongoParticipantStore` — a drop-in `ParticipantStore`.

Implements ui-student's `agentic_system/store/base.py` protocol exactly:
same method names, same arguments, same dict shapes out. Swapping it in is
one line in `agentic_system/api.py`:

    p_store = MongoParticipantStore(db, course_id=course_id)

`db` is a guarded database (`guarded(get_db(), Role.PARTICIPANT_AGENT, ...)`).
`course_id` replaces the per-course SQLite file: every write is stamped with
it and every read filters on it. It is projected back OUT of returned rows,
because the SQLite `interactions` table had no such column and the agent
reads these dicts by key.
"""

from __future__ import annotations

from typing import Any, Optional

from .._util import now, to_dt, to_iso


class MongoParticipantStore:
    """Participant telemetry in the shared `interactions` collection."""

    #: Not part of the SQLite row shape; stripped from every read.
    _INTERNAL = ("_id", "course_id", "logged_at")

    def __init__(self, db, course_id: str) -> None:
        if not course_id:
            raise ValueError("course_id is required: it is what isolates one course's data")
        self._db = db
        self._course_id = course_id

    # ----------------------------------------------------------- lifecycle
    def init(self) -> None:
        """No-op.

        The SQLite store created tables here. Schema creation is now a
        deploy-time step (`scripts/init_db.py`), not something each process
        does on startup, so there is nothing to do — but the method stays,
        because the protocol has it and the harness calls it.
        """
        return None

    def close(self) -> None:
        """No-op. The MongoClient is process-wide and closed by `client.close()`."""
        return None

    # -------------------------------------------------------------- helpers
    @property
    def course_id(self) -> str:
        return self._course_id

    def _row(self, document: dict[str, Any]) -> dict[str, Any]:
        """One stored document as the SQLite row the agent expects."""
        row = {k: v for k, v in document.items() if k not in self._INTERNAL}
        row["timestamp"] = to_iso(row.get("timestamp"))
        return row

    # --------------------------------------------------------------- writes
    def insert_interaction(self, item: dict) -> None:
        """Persist one interaction.

        `item` keys, per the protocol: id, student_id, session_id, timestamp,
        message, question_type, hint_level, difficulty, response_time_ms,
        lab_id. `timestamp` may be an ISO string or a `datetime`.

        Every row gets BOTH timestamps:

        * `timestamp` — when the interaction happened. Taken from `item`; if
          the caller leaves it out, or sends something empty, it falls back to
          now rather than raising, because an interaction logged without a
          time is worse than one with an approximate time.
        * `logged_at` — when this row was written. Always set here, never
          taken from `item`, so it cannot be back-dated by a caller.

        `classification_confidence` and `feedback_score` are passed through
        when present (participant-agent sends them; ui-student does not).
        """
        recorded = now()
        document = {
            "id": item["id"],
            "course_id": self._course_id,
            "student_id": item["student_id"],
            "session_id": item["session_id"],
            "timestamp": to_dt(item.get("timestamp")) or recorded,
            "message": item["message"],
            "question_type": item["question_type"],
            "hint_level": int(item["hint_level"]),
            "difficulty": item["difficulty"],
            "response_time_ms": int(item.get("response_time_ms", 0)),
            "lab_id": item["lab_id"],
            "logged_at": recorded,
        }
        for optional in ("classification_confidence", "feedback_score"):
            if item.get(optional) is not None:
                document[optional] = item[optional]
        self._db["interactions"].insert_one(document)

    # ---------------------------------------------------------------- reads
    def fetch_for_student(self, student_id: str) -> list[dict]:
        """Every interaction for a student, oldest first."""
        cursor = (
            self._db["interactions"]
            .find({"course_id": self._course_id, "student_id": student_id})
            .sort("timestamp", 1)
        )
        return [self._row(document) for document in cursor]
