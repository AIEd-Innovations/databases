"""`SubmissionRepo` — students hand work in, instructors pick it up to grade.

The workflow agreed with the UI teams:

    ui-student      submit()                      -> student_submissions   (write)
    ui-instructor   list_submissions(), get_submission()                   (read)
                    grade through the assessment agent with its own key
    assessment      AssessmentRepo.insert_result(
                        {..., "student_submission_id": s["submission_id"]},
                        submitted_files=s["files"])  -> assessment_results (write)
    ui-instructor   AssessmentRepo.list_results(student_submission_id=...),
                    review_result()                -> assessment_results   (read/write)

`student_submissions` is the "submissions" store and `assessment_results` the
"graded submissions" store. They are two collections in the one `aied`
database rather than two databases: one connection, one set of access rules,
and a grade can point at the work it graded.

Insert-only. A resubmission is a new document with the next `attempt`, so
work that has been graded never changes underneath its grade, and the
instructor can see every attempt.

Students see their own submissions and nobody else's. Whatever `student_id` a
caller passes, a repo built on a `Role.STUDENT` guard reads and writes as the
guard's `actor_id` — a route that forgets to filter cannot leak a classmate's
work.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from .._util import new_id, now, strip_id, to_iso
from ..access import Role


class SubmissionRepo:
    """Student submissions for one course, and whether each has been graded."""

    def __init__(self, db, course_id: str) -> None:
        if not course_id:
            raise ValueError("course_id is required")
        self._db = db
        self._course_id = course_id

    # -------------------------------------------------------------- helpers
    @property
    def course_id(self) -> str:
        return self._course_id

    def _scope(self, **extra: Any) -> dict[str, Any]:
        return {"course_id": self._course_id, **extra}

    @property
    def _student(self) -> Optional[str]:
        """The student this repo is confined to, when it runs as one."""
        if getattr(self._db, "role", None) == Role.STUDENT:
            return self._db.actor_id
        return None

    def _as(self, student_id: Optional[str]) -> Optional[str]:
        """The student_id to act as: a student guard always wins."""
        confined = self._student
        if confined is None:
            return student_id
        if student_id and student_id != confined:
            raise PermissionError("a student may only see and submit their own work")
        return confined

    def _grading(self, submission_ids: Iterable[str]) -> dict[str, dict]:
        """Newest result per student submission: {submission_id: result}."""
        ids = list(submission_ids)
        if not ids:
            return {}
        cursor = self._db["assessment_results"].find(
            self._scope(student_submission_id={"$in": ids}),
            {"submission_id": 1, "student_submission_id": 1, "created_at": 1, "status": 1},
        ).sort([("created_at", -1), ("_id", -1)])
        newest: dict[str, dict] = {}
        for result in cursor:
            newest.setdefault(result["student_submission_id"], result)
        return newest

    @staticmethod
    def _row(document: dict, result: Optional[dict], *, with_files: bool) -> dict:
        row = strip_id(document) or {}
        row["submitted_at"] = to_iso(row.get("submitted_at"))
        if not with_files:
            row["files"] = [f["filename"] for f in row.get("files", [])]
        row["graded"] = result is not None
        row["latest_result_id"] = result["submission_id"] if result else None
        row["grade_status"] = result.get("status") if result else None
        row["graded_at"] = to_iso(result["created_at"]) if result else None
        return row

    def _rows(self, documents: list[dict], *, with_files: bool) -> list[dict]:
        grading = self._grading(d["submission_id"] for d in documents)
        return [
            self._row(d, grading.get(d["submission_id"]), with_files=with_files)
            for d in documents
        ]

    # ---------------------------------------------------------------- write
    def submit(
        self,
        assignment_id: str,
        files: list[dict],
        *,
        student_id: Optional[str] = None,
        comment: str = "",
    ) -> dict:
        """ui-student: hand in `files` (`[{"filename", "content"}, ...]`).

        Returns the stored submission with its `submission_id` and `attempt`.
        Raises ValueError for an unknown assignment or no files, and
        `aied_db.IntegrityError` if two submissions for the same attempt race
        (retry the call).
        """
        student_id = self._as(student_id)
        if not student_id:
            raise ValueError("student_id is required")
        if not files:
            raise ValueError("a submission needs at least one file")
        if self._db["assignments"].find_one(self._scope(assignment_id=assignment_id)) is None:
            raise ValueError(f"no assignment {assignment_id!r} in course {self._course_id!r}")

        previous = self._db["student_submissions"].find_one(
            self._scope(student_id=student_id, assignment_id=assignment_id),
            sort=[("attempt", -1)],
        )
        document = {
            "submission_id": new_id(),
            "student_id": student_id,
            "assignment_id": assignment_id,
            "course_id": self._course_id,
            "attempt": (previous["attempt"] + 1) if previous else 1,
            "files": [{"filename": f["filename"], "content": f["content"]} for f in files],
            "comment": comment,
            "submitted_at": now(),
        }
        self._db["student_submissions"].insert_one(document)
        return self._row(document, None, with_files=True)

    # ---------------------------------------------------------------- reads
    def list_submissions(
        self,
        *,
        student_id: Optional[str] = None,
        assignment_id: Optional[str] = None,
        graded: Optional[bool] = None,
        latest: bool = False,
        with_files: bool = False,
    ) -> list[dict]:
        """Submissions, newest first, each marked `graded` or not.

        ui-instructor: `list_submissions(assignment_id=..., graded=False)` is
        the to-grade queue. ui-student: the student's own history.

        `latest=True` keeps each student's newest attempt per assignment, and
        is applied BEFORE `graded` — so `latest=True, graded=False` is the
        students whose current attempt still needs grading.

        Files are filenames only unless `with_files=True`; use
        `get_submission` for one submission's content.
        """
        query = self._scope()
        student_id = self._as(student_id)
        if student_id:
            query["student_id"] = student_id
        if assignment_id:
            query["assignment_id"] = assignment_id
        documents = list(
            self._db["student_submissions"].find(query).sort(
                [("submitted_at", -1), ("attempt", -1)]
            )
        )
        if latest:
            seen: set[tuple[str, str]] = set()
            newest = []
            for document in documents:
                key = (document["student_id"], document["assignment_id"])
                if key not in seen:
                    seen.add(key)
                    newest.append(document)
            documents = newest
        rows = self._rows(documents, with_files=with_files)
        if graded is not None:
            rows = [row for row in rows if row["graded"] is graded]
        return rows

    def get_submission(self, submission_id: str, *, student_id: Optional[str] = None) -> Optional[dict]:
        """One submission with its file contents, or None.

        The assessment agent grades `get_submission(id)["files"]`. A student
        gets None for anyone else's submission.
        """
        query = self._scope(submission_id=submission_id)
        student_id = self._as(student_id)
        if student_id:
            query["student_id"] = student_id
        document = self._db["student_submissions"].find_one(query)
        if document is None:
            return None
        return self._rows([document], with_files=True)[0]
