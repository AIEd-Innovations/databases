"""Student submissions: students hand in, instructors grade, nobody edits."""

from __future__ import annotations

import pytest

from aied_db import IntegrityError, Role, guarded
from aied_db.access import PermissionError_
from aied_db.repos import AssessmentRepo, SubmissionRepo

from .factories import FACTORIES, INSTRUCTOR_ID, STUDENT_ID

FILES = [{"filename": "main.py", "content": "print(sorted([3, 1, 2]))\n"}]


@pytest.fixture
def assignment(admin_db, course_id):
    admin_db.assignments.insert_one(FACTORIES["assignments"](course_id=course_id))
    return "hw1_sorting"


def student_repo(db, course_id, student_id=STUDENT_ID):
    return SubmissionRepo(guarded(db, Role.STUDENT, actor_id=student_id), course_id=course_id)


@pytest.fixture
def student(db, course_id):
    return student_repo(db, course_id)


@pytest.fixture
def instructor(db, course_id):
    return SubmissionRepo(guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id=course_id)


@pytest.fixture
def grader(db, course_id):
    return AssessmentRepo(
        guarded(db, Role.ASSESSMENT_AGENT, actor_id="assessment_agent"), course_id=course_id
    )


def grade(grader, submission, **overrides):
    """What the assessment agent does when an instructor clicks Grade."""
    result = FACTORIES["assessment_results"](
        submission_id=overrides.pop("submission_id", "res-" + submission["submission_id"][:8]),
        student_id=submission["student_id"],
        assignment_id=submission["assignment_id"],
        student_submission_id=submission["submission_id"],
        **overrides,
    )
    return grader.insert_result(result)


# ── Students write ──────────────────────────────────────────────────────────


def test_a_student_submits_and_reads_it_back(student, assignment):
    row = student.submit(assignment, FILES, comment="first try")
    assert row["attempt"] == 1 and row["student_id"] == STUDENT_ID
    assert row["graded"] is False and row["latest_result_id"] is None
    assert student.get_submission(row["submission_id"])["files"] == FILES
    listed = student.list_submissions()
    assert [r["submission_id"] for r in listed] == [row["submission_id"]]
    assert listed[0]["files"] == ["main.py"]  # filenames only in a list


def test_a_resubmission_is_a_new_attempt_not_an_edit(student, assignment):
    first = student.submit(assignment, FILES)
    second = student.submit(assignment, [{"filename": "main.py", "content": "v2\n"}])
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert student.get_submission(first["submission_id"])["files"] == FILES
    assert [r["attempt"] for r in student.list_submissions(latest=True)] == [2]


def test_submit_needs_files_and_a_real_assignment(student, assignment):
    with pytest.raises(ValueError):
        student.submit(assignment, [])
    with pytest.raises(ValueError):
        student.submit("no_such_assignment", FILES)


def test_a_student_cannot_submit_as_someone_else(db, student, assignment, course_id):
    with pytest.raises(PermissionError):
        student.submit(assignment, FILES, student_id="student-999")
    raw = guarded(db, Role.STUDENT, actor_id=STUDENT_ID)
    with pytest.raises(PermissionError_):
        raw.student_submissions.insert_one(FACTORIES["student_submissions"](student_id="student-999"))


def test_a_student_sees_only_their_own_work(db, student, assignment, course_id):
    mine = student.submit(assignment, FILES)
    theirs = student_repo(db, course_id, "student-002").submit(assignment, FILES)
    assert [r["submission_id"] for r in student.list_submissions()] == [mine["submission_id"]]
    assert student.get_submission(theirs["submission_id"]) is None
    with pytest.raises(PermissionError):
        student.list_submissions(student_id="student-002")


def test_submissions_are_insert_only_for_students(db, student, assignment):
    row = student.submit(assignment, FILES)
    raw = guarded(db, Role.STUDENT, actor_id=STUDENT_ID)
    with pytest.raises(PermissionError_):
        raw.student_submissions.update_one({"submission_id": row["submission_id"]}, {"$set": {"comment": "x"}})
    with pytest.raises(PermissionError_):
        raw.student_submissions.delete_one({"submission_id": row["submission_id"]})


def test_the_same_attempt_cannot_be_stored_twice(admin_db):
    admin_db.student_submissions.insert_one(FACTORIES["student_submissions"]())
    with pytest.raises(IntegrityError):
        admin_db.student_submissions.insert_one(FACTORIES["student_submissions"](submission_id="ssub-002"))


# ── Instructors read, and nobody else writes ────────────────────────────────


def test_instructors_and_the_agent_read_but_never_write(db):
    for role, actor in ((Role.INSTRUCTOR, INSTRUCTOR_ID), (Role.ASSESSMENT_AGENT, "assessment_agent")):
        raw = guarded(db, role, actor_id=actor)
        raw.student_submissions.find_one({})
        with pytest.raises(PermissionError_):
            raw.student_submissions.insert_one(FACTORIES["student_submissions"]())
        with pytest.raises(PermissionError_):
            raw.student_submissions.update_one({}, {"$set": {"comment": "x"}})
        with pytest.raises(PermissionError_):
            raw.student_submissions.delete_one({})


def test_other_agents_cannot_read_submissions(db):
    for role in (Role.GUARDIAN_AGENT, Role.LAB_COMPANION_AGENT, Role.CURRICULUM_AGENT):
        with pytest.raises(PermissionError_):
            guarded(db, role, actor_id=role.value).student_submissions.find_one({})


def test_the_instructor_sees_the_whole_class_and_the_to_grade_queue(db, student, instructor, grader, assignment, course_id):
    a = student.submit(assignment, FILES)
    b = student_repo(db, course_id, "student-002").submit(assignment, FILES)
    assert {r["submission_id"] for r in instructor.list_submissions(assignment_id=assignment)} == {
        a["submission_id"], b["submission_id"]
    }
    grade(grader, a)
    assert [r["submission_id"] for r in instructor.list_submissions(graded=False)] == [b["submission_id"]]
    assert [r["submission_id"] for r in instructor.list_submissions(graded=True)] == [a["submission_id"]]


def test_latest_applies_before_graded(student, instructor, grader, assignment):
    first = student.submit(assignment, FILES)
    grade(grader, first)
    second = student.submit(assignment, FILES)
    # The current attempt is ungraded, even though an older one was graded.
    assert [r["submission_id"] for r in instructor.list_submissions(latest=True, graded=False)] == [
        second["submission_id"]
    ]


def test_submissions_are_scoped_to_the_course(db, student, assignment):
    student.submit(assignment, FILES)
    other = SubmissionRepo(guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id="course-other")
    assert other.list_submissions() == []


# ── Grading: the "graded submissions" store ─────────────────────────────────


def test_grading_links_the_result_to_the_submission(student, instructor, grader, assignment):
    sub = student.submit(assignment, FILES)
    files = instructor.get_submission(sub["submission_id"])["files"]
    assert files == FILES
    result = grade(grader, sub)
    assert result["student_submission_id"] == sub["submission_id"]

    row = instructor.get_submission(sub["submission_id"])
    assert row["graded"] is True
    assert row["latest_result_id"] == result["submission_id"]
    assert row["grade_status"] == "ai_graded"
    # The student sees that it was graded, too.
    assert student.get_submission(sub["submission_id"])["graded"] is True


def test_a_regrade_becomes_the_latest_result(student, instructor, grader, assignment):
    sub = student.submit(assignment, FILES)
    first = grade(grader, sub, submission_id="res-1")
    grade(grader, sub, submission_id="res-2", regrade_of=first["submission_id"])
    assert instructor.get_submission(sub["submission_id"])["latest_result_id"] == "res-2"
    assert [r["submission_id"] for r in grader.list_results(student_submission_id=sub["submission_id"])] == [
        "res-2", "res-1"
    ]


def test_the_instructor_reviews_the_grade(db, student, grader, assignment, course_id):
    sub = student.submit(assignment, FILES)
    result = grade(grader, sub)
    reviewer = AssessmentRepo(guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id=course_id)
    reviewed = reviewer.review_result(result["submission_id"], final_score=38.0, notes="ok")
    assert reviewed["final_score"] == 38.0 and reviewed["status"] == "instructor_graded"


def test_a_grade_must_point_at_a_real_matching_submission(student, grader, assignment):
    sub = student.submit(assignment, FILES)
    with pytest.raises(ValueError):
        grade(grader, {**sub, "submission_id": "no-such-submission"})
    with pytest.raises(ValueError):
        grade(grader, {**sub, "student_id": "student-999"})
    with pytest.raises(ValueError):
        grade(grader, {**sub, "assignment_id": "hw2_other"})


def test_a_regrade_can_read_the_files_from_the_submission(student, grader, assignment):
    sub = student.submit(assignment, FILES)
    result = grade(grader, sub)  # no submitted_files copy stored
    assert grader.get_submitted_files(result["submission_id"]) == FILES


def test_a_result_without_a_submission_still_has_a_null_link(grader):
    result = grader.insert_result(FACTORIES["assessment_results"]())
    assert result["student_submission_id"] is None
