"""`MongoSystemStore` — a drop-in `SystemStore`.

Users, courses and enrollments. Unlike the participant and guardian stores
this one is NOT per-course: it is the tenancy table itself, so it takes no
`course_id`.

Parity details:
  - `email` lookups are case-insensitive, matching SQLite's `COLLATE NOCASE`.
    The unique index on `email` carries collation strength 2 (see
    `schema/system.py`), and queries use the same collation.
  - `insert_enrollment` was `INSERT OR IGNORE`: enrolling twice is not an
    error. A duplicate is swallowed here too.
  - `list_enrolled_users` returned `u.*, e.enrolled_at` — the user's fields
    with the enrollment's timestamp overlaid. Same here.
  - SQLite's `courses.participant_db_path` / `guardian_db_path` columns are
    gone: they pointed at the per-course .db files this package replaces.
    `insert_course` and `update_course` DROP those keys rather than failing,
    because ui-student's `routes/admin.py` still passes them as None. The
    validator would otherwise reject the document.
"""

from __future__ import annotations

from typing import Any, Optional

from .._util import IntegrityError, to_dt, to_iso

#: Case-insensitive, to match SQLite's COLLATE NOCASE on users.email.
_CI = {"locale": "en", "strength": 2}

#: Columns that existed only to locate a per-course SQLite file.
_LEGACY_COURSE_KEYS = ("participant_db_path", "guardian_db_path")


class MongoSystemStore:
    """Multi-tenant metadata in `users`, `courses` and `enrollments`."""

    def __init__(self, db) -> None:
        self._db = db

    # ----------------------------------------------------------- lifecycle
    def init(self) -> None:
        """No-op; schema creation is `scripts/init_db.py`'s job."""
        return None

    def close(self) -> None:
        return None

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _user_row(document: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if document is None:
            return None
        row = {k: v for k, v in document.items() if k != "_id"}
        row["created_at"] = to_iso(row.get("created_at"))
        return row

    @staticmethod
    def _course_row(document: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
        if document is None:
            return None
        row = {k: v for k, v in document.items() if k != "_id"}
        row["created_at"] = to_iso(row.get("created_at"))
        return row

    # ------------------------------------------------------------- users
    def insert_user(self, item: dict) -> None:
        """`item` keys: id, email, name, sso_subject, role, created_at."""
        try:
            self._db["users"].insert_one(
                {
                    "id": item["id"],
                    "email": item["email"],
                    "name": item.get("name", ""),
                    "sso_subject": item.get("sso_subject"),
                    "role": item["role"],
                    "created_at": to_dt(item["created_at"]),
                }
            )
        except IntegrityError:
            raise

    def get_user(self, user_id: str) -> Optional[dict]:
        return self._user_row(self._db["users"].find_one({"id": user_id}))

    def get_user_by_email(self, email: str) -> Optional[dict]:
        return self._user_row(
            self._db["users"].find_one({"email": email}, collation=_CI)
        )

    def get_user_by_sso_subject(self, subject: str) -> Optional[dict]:
        return self._user_row(self._db["users"].find_one({"sso_subject": subject}))

    def list_users(self) -> list[dict]:
        cursor = self._db["users"].find({}).sort("created_at", 1)
        return [self._user_row(document) for document in cursor]

    def update_user_sso_subject(self, user_id: str, sso_subject: str) -> None:
        self._db["users"].update_one(
            {"id": user_id}, {"$set": {"sso_subject": sso_subject}}
        )

    def delete_user(self, user_id: str) -> None:
        self._db["users"].delete_one({"id": user_id})

    # ----------------------------------------------------------- courses
    def insert_course(self, item: dict) -> None:
        """`item` keys: id, name, instructor_id, blob_container_name,
        search_index_name, search_indexer_name, search_datasource_name,
        created_at. The two legacy *_db_path keys are dropped if present."""
        document = {
            "id": item["id"],
            "name": item["name"],
            "instructor_id": item["instructor_id"],
            "blob_container_name": item["blob_container_name"],
            "search_index_name": item["search_index_name"],
            "search_indexer_name": item["search_indexer_name"],
            "search_datasource_name": item["search_datasource_name"],
            "created_at": to_dt(item["created_at"]),
        }
        self._db["courses"].insert_one(document)

    def get_course(self, course_id: str) -> Optional[dict]:
        return self._course_row(self._db["courses"].find_one({"id": course_id}))

    def list_courses(self, *, instructor_id: Optional[str] = None) -> list[dict]:
        query: dict[str, Any] = {}
        if instructor_id is not None:
            query["instructor_id"] = instructor_id
        cursor = self._db["courses"].find(query).sort("created_at", 1)
        return [self._course_row(document) for document in cursor]

    def update_course(self, course_id: str, patch: dict) -> None:
        """Apply a partial update. An empty patch is a no-op, as upstream."""
        changes = {k: v for k, v in patch.items() if k not in _LEGACY_COURSE_KEYS}
        if "created_at" in changes:
            changes["created_at"] = to_dt(changes["created_at"])
        if not changes:
            return
        self._db["courses"].update_one({"id": course_id}, {"$set": changes})

    def delete_course(self, course_id: str) -> None:
        self._db["courses"].delete_one({"id": course_id})

    # ------------------------------------------------------- enrollments
    def insert_enrollment(self, course_id: str, user_id: str, enrolled_at: str) -> None:
        """Enroll a user. Enrolling twice is not an error (INSERT OR IGNORE)."""
        try:
            self._db["enrollments"].insert_one(
                {
                    "course_id": course_id,
                    "user_id": user_id,
                    "enrolled_at": to_dt(enrolled_at),
                }
            )
        except IntegrityError:
            return

    def delete_enrollment(self, course_id: str, user_id: str) -> None:
        self._db["enrollments"].delete_one({"course_id": course_id, "user_id": user_id})

    def is_enrolled(self, course_id: str, user_id: str) -> bool:
        found = self._db["enrollments"].find_one(
            {"course_id": course_id, "user_id": user_id}
        )
        return found is not None

    def list_enrolled_users(self, course_id: str) -> list[dict]:
        """Enrolled users, oldest enrollment first.

        Returns each user's fields with `enrolled_at` overlaid — the shape of
        upstream's `SELECT u.*, e.enrolled_at ... JOIN`.
        """
        enrollments = list(
            self._db["enrollments"].find({"course_id": course_id}).sort("enrolled_at", 1)
        )
        if not enrollments:
            return []

        user_ids = [e["user_id"] for e in enrollments]
        users = {
            document["id"]: document
            for document in self._db["users"].find({"id": {"$in": user_ids}})
        }

        rows = []
        for enrollment in enrollments:
            user = users.get(enrollment["user_id"])
            if user is None:
                # The SQL JOIN would have dropped the row; so do we.
                continue
            row = self._user_row(user)
            row["enrolled_at"] = to_iso(enrollment["enrolled_at"])
            rows.append(row)
        return rows

    def list_courses_for_user(self, user_id: str) -> list[dict]:
        """The courses a user is enrolled in, oldest course first."""
        course_ids = [
            enrollment["course_id"]
            for enrollment in self._db["enrollments"].find({"user_id": user_id})
        ]
        if not course_ids:
            return []
        cursor = (
            self._db["courses"].find({"id": {"$in": course_ids}}).sort("created_at", 1)
        )
        return [self._course_row(document) for document in cursor]
