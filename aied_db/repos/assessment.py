"""`AssessmentRepo` — one method per assessment_agent route.

Mirrors the routes in `assessment_agent/api/` @c019f0f, so migrating that
service is a matter of replacing `persistence.append_json` / `read_json` /
`update_json` calls with these methods:

    GET    /assignments                               -> list_assignments
    GET    /assignments/{id}/rubric                   -> get_rubric
    POST   /assignments/import/{lab_id}               -> import_from_lab
    PUT    /assignments/{id}                          -> upsert_assignment
    DELETE /assignments/{id}                          -> delete_assignment
    PUT    /assignments/{id}/solution                 -> replace_solution
    POST   /assignments/{id}/materials                -> add_material
    GET    /assignments/{id}/materials                -> list_materials
    DELETE /assignments/{id}/materials/{name}         -> delete_material
    POST   /submit, /submit-json                      -> insert_result
    GET    /results                                   -> list_results
    GET    /results/{submission_id}                   -> get_result
    POST   /results/{submission_id}/review            -> review_result
    GET    /results/{submission_id}/provenance        -> get_provenance

SECRETS. Every read comes in two forms. The plain one is for instructors and
for the agent itself; the `for_student=True` one strips
`criteria[].expected_answer` and never touches `assignment_files` rows with
`kind: "solution"`. A student-facing route must pass `for_student=True`.
"""

from __future__ import annotations

from typing import Any, Optional

from .._util import new_id, now, strip_id, to_iso

#: Removed from every student-facing assignment read.
_SECRET_CRITERION_FIELDS = ("expected_answer",)

_RESULT_DATES = ("created_at", "reviewed_at")


class AssessmentRepo:
    """Assignments, their files, and grading results for one course."""

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

    @staticmethod
    def _assignment_row(document: Optional[dict], *, for_student: bool) -> Optional[dict]:
        if document is None:
            return None
        row = strip_id(document) or {}
        row["created_at"] = to_iso(row.get("created_at"))
        if row.get("updated_at") is not None:
            row["updated_at"] = to_iso(row["updated_at"])
        if for_student:
            row["criteria"] = [
                {k: v for k, v in criterion.items() if k not in _SECRET_CRITERION_FIELDS}
                for criterion in row.get("criteria", [])
            ]
        return row

    @staticmethod
    def _result_row(document: Optional[dict]) -> Optional[dict]:
        if document is None:
            return None
        row = strip_id(document) or {}
        for field in _RESULT_DATES:
            if field in row:
                row[field] = to_iso(row[field])
        provenance = row.get("provenance")
        if isinstance(provenance, dict):
            provenance = dict(provenance)
            provenance["created_at"] = to_iso(provenance.get("created_at"))
            provenance["llm_calls"] = [
                {**call, "timestamp": to_iso(call.get("timestamp"))}
                for call in provenance.get("llm_calls", [])
            ]
            row["provenance"] = provenance
        return row

    @staticmethod
    def _file_row(document: dict) -> dict:
        row = strip_id(document) or {}
        row["uploaded_at"] = to_iso(row.get("uploaded_at"))
        return row

    # ---------------------------------------------------------- assignments
    def list_assignments(self, *, for_student: bool = False) -> list[dict]:
        """GET /assignments — the course's assignments, newest first."""
        cursor = self._db["assignments"].find(self._scope()).sort("created_at", -1)
        return [self._assignment_row(d, for_student=for_student) for d in cursor]

    def get_assignment(self, assignment_id: str, *, for_student: bool = False) -> Optional[dict]:
        """One assignment, or None."""
        document = self._db["assignments"].find_one(
            self._scope(assignment_id=assignment_id)
        )
        return self._assignment_row(document, for_student=for_student)

    def get_rubric(self, assignment_id: str, *, for_student: bool = False) -> Optional[dict]:
        """GET /assignments/{id}/rubric — the criteria and the total."""
        assignment = self.get_assignment(assignment_id, for_student=for_student)
        if assignment is None:
            return None
        return {
            "assignment_id": assignment["assignment_id"],
            "title": assignment.get("title", ""),
            "criteria": assignment.get("criteria", []),
            "max_score": assignment.get("max_score"),
        }

    def upsert_assignment(self, spec: dict) -> dict:
        """PUT /assignments/{id} — create or replace a grading spec.

        `spec` is a `GradingSpec` dump plus `assignment_id`. `max_score` is
        recomputed from the criteria here rather than trusted, because it is
        a derived field upstream (`computed_field`).
        """
        assignment_id = spec["assignment_id"]
        criteria = list(spec.get("criteria") or [])
        if not criteria:
            raise ValueError("an assignment needs at least one criterion")

        document = {
            "assignment_id": assignment_id,
            "course_id": self._course_id,
            "title": spec.get("title", ""),
            "description": spec.get("description", ""),
            "criteria": criteria,
            "guidance": spec.get("guidance", ""),
            "model": spec["model"],
            "source": spec.get("source", ""),
            "max_score": float(sum(c["max_points"] for c in criteria)),
        }
        if spec.get("imported_from_lab"):
            document["imported_from_lab"] = spec["imported_from_lab"]

        existing = self._db["assignments"].find_one(
            self._scope(assignment_id=assignment_id)
        )
        if existing is None:
            document["created_at"] = now()
            document["updated_at"] = None
            self._db["assignments"].insert_one(document)
        else:
            document["created_at"] = existing["created_at"]
            document["updated_at"] = now()
            self._db["assignments"].replace_one(
                self._scope(assignment_id=assignment_id), document
            )
        return self._assignment_row(document, for_student=False)

    def import_from_lab(self, lab: dict, *, assignment_id: Optional[str] = None) -> dict:
        """POST /assignments/import/{lab_id} — copy a lab into an assignment.

        A COPY, deliberately: `assignments` carry absolute points and `labs`
        carry fractional weights, and the team decided they stay separate
        rather than one deriving from the other at read time. Editing the lab
        afterwards does not change the assignment.

        The lab's quiz questions become criteria, with `rubric_points` as
        `max_points` and `expected_answer` carried over as a secret.
        """
        quiz = lab.get("quiz") or []
        if not quiz:
            raise ValueError(
                f"lab {lab.get('lab_id')!r} has no quiz questions, so there is "
                "nothing to grade against"
            )

        criteria = [
            {
                "id": question["id"],
                "name": question.get("learning_objective_ref") or question["id"],
                "description": question["question"],
                "max_points": float(question["rubric_points"]),
                "expected_answer": question.get("expected_answer", ""),
            }
            for question in quiz
        ]
        return self.upsert_assignment(
            {
                "assignment_id": assignment_id or lab["lab_id"],
                "title": lab.get("title", ""),
                "description": lab.get("spec_markdown", ""),
                "criteria": criteria,
                "guidance": lab.get("agent_instructions") or "",
                "model": lab.get("model") or "",
                "source": f"lab:{lab['lab_id']}",
                "imported_from_lab": {
                    "course_id": lab["course_id"],
                    "lab_id": lab["lab_id"],
                },
            }
        )

    def delete_assignment(self, assignment_id: str) -> bool:
        """DELETE /assignments/{id} — instructor only; its files go too."""
        result = self._db["assignments"].delete_one(
            self._scope(assignment_id=assignment_id)
        )
        self._db["assignment_files"].delete_many(
            self._scope(assignment_id=assignment_id)
        )
        return result.deleted_count > 0

    # --------------------------------------------------------------- files
    def _insert_file(self, assignment_id: str, file: dict, kind: str) -> dict:
        file_id = file.get("file_id") or new_id()
        filename = file["filename"]
        document = {
            "file_id": file_id,
            "assignment_id": assignment_id,
            "course_id": self._course_id,
            "filename": filename,
            "content_type": file.get("content_type"),
            "size_bytes": file.get("size_bytes"),
            "kind": kind,
            "original": bool(file.get("original", False)),
            "uploaded_by": file.get("uploaded_by"),
            "uploaded_at": now(),
            "storage_key": file.get("storage_key")
            or f"assignments/{self._course_id}/{assignment_id}/{kind}/{file_id}/{filename}",
            "extracted_text": file.get("extracted_text", ""),
            "char_count": int(file.get("char_count", len(file.get("extracted_text", "")))),
        }
        self._db["assignment_files"].insert_one(document)
        return self._file_row(document)

    def replace_solution(self, assignment_id: str, files: list[dict]) -> list[dict]:
        """PUT /assignments/{id}/solution — SECRET. Replaces the whole set."""
        self._db["assignment_files"].delete_many(
            self._scope(assignment_id=assignment_id, kind="solution")
        )
        return [self._insert_file(assignment_id, f, "solution") for f in files]

    def add_material(self, assignment_id: str, file: dict) -> dict:
        """POST /assignments/{id}/materials — course material, not secret."""
        return self._insert_file(assignment_id, file, "material")

    def list_materials(self, assignment_id: str) -> list[dict]:
        """GET /assignments/{id}/materials — materials only; never solutions."""
        cursor = self._db["assignment_files"].find(
            self._scope(assignment_id=assignment_id, kind="material")
        ).sort("uploaded_at", 1)
        return [self._file_row(document) for document in cursor]

    def list_solution(self, assignment_id: str) -> list[dict]:
        """The reference solution. SECRET — grading and instructors only.

        There is no student-facing form of this method on purpose.
        """
        cursor = self._db["assignment_files"].find(
            self._scope(assignment_id=assignment_id, kind="solution")
        ).sort("uploaded_at", 1)
        return [self._file_row(document) for document in cursor]

    def delete_material(self, assignment_id: str, filename: str) -> bool:
        """DELETE /assignments/{id}/materials/{name}."""
        result = self._db["assignment_files"].delete_one(
            self._scope(assignment_id=assignment_id, kind="material", filename=filename)
        )
        return result.deleted_count > 0

    # -------------------------------------------------------------- results
    def insert_result(self, result: dict) -> dict:
        """POST /submit — record one grading attempt. INSERT-ONLY.

        A regrade is a new `submission_id`, never an edit of this document.
        The submitted text is not stored: `files` holds filenames only.
        """
        document = {
            "submission_id": result["submission_id"],
            "student_id": result["student_id"],
            "assignment_id": result["assignment_id"],
            "course_id": self._course_id,
            "files": list(result.get("files") or []),
            "model": result.get("model", ""),
            "mock": bool(result.get("mock", False)),
            "criteria": list(result.get("criteria") or []),
            "score": result.get("score"),
            "max_score": float(result.get("max_score", 0.0)),
            "feedback": result.get("feedback"),
            "status": result.get("status", "ai_graded"),
            "review_reasons": list(result.get("review_reasons") or []),
            "final_score": None,
            "instructor_notes": "",
            "reviewed_at": None,
            "created_at": now(),
            "provenance": result.get("provenance"),
        }
        self._db["assessment_results"].insert_one(document)
        return self._result_row(document)

    def list_results(
        self,
        *,
        student_id: Optional[str] = None,
        assignment_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[dict]:
        """GET /results — the same three optional filters as the route."""
        query = self._scope()
        if student_id:
            query["student_id"] = student_id
        if assignment_id:
            query["assignment_id"] = assignment_id
        if status:
            query["status"] = status
        cursor = self._db["assessment_results"].find(query).sort("created_at", -1)
        return [self._result_row(document) for document in cursor]

    def get_result(self, submission_id: str) -> Optional[dict]:
        """GET /results/{submission_id}."""
        return self._result_row(
            self._db["assessment_results"].find_one(
                self._scope(submission_id=submission_id)
            )
        )

    def review_result(
        self,
        submission_id: str,
        *,
        final_score: float,
        notes: str = "",
        reviewed_by: Optional[str] = None,
    ) -> Optional[dict]:
        """POST /results/{submission_id}/review — the instructor's grade.

        `final_score` REPLACES the AI score; it does not adjust it. Bounded
        0..max_score, matching the route's two 400s.

        OPEN QUESTION: curriculum-designer's rubric still reserves a 10%
        `manual_weight`. If that survives its redesign, this becomes an
        adjustment rather than a replacement — the upstream route carries the
        same comment.
        """
        target = self._db["assessment_results"].find_one(
            self._scope(submission_id=submission_id)
        )
        if target is None:
            return None

        if final_score < 0:
            raise ValueError("final_score cannot be negative")
        max_score = target.get("max_score")
        if max_score is not None and final_score > max_score:
            raise ValueError(f"final_score cannot exceed {max_score:g}")

        updates = {
            "status": "instructor_graded",
            "final_score": float(final_score),
            "instructor_notes": notes,
            "reviewed_at": now(),
        }
        self._db["assessment_results"].update_one(
            self._scope(submission_id=submission_id), {"$set": updates}
        )
        return self._result_row({**target, **updates})

    def get_provenance(self, submission_id: str) -> Optional[dict]:
        """GET /results/{submission_id}/provenance."""
        result = self.get_result(submission_id)
        return None if result is None else result.get("provenance")

    def pending_review(self) -> list[dict]:
        """The `needs_review` queue, oldest first.

        This replaces task3's separate `review_queue` collection: review is a
        status on the result, not a second document. Sean's redesign made that
        call; the task3 draft has not caught up.
        """
        cursor = (
            self._db["assessment_results"]
            .find(self._scope(status="needs_review"))
            .sort("created_at", 1)
        )
        return [self._result_row(document) for document in cursor]
