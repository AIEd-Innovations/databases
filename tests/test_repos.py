"""The repos: route-shaped reads and writes, and the secrets they must hide."""

from __future__ import annotations

import pytest

from aied_db import Role, guarded
from aied_db.access import PermissionError_
from aied_db.repos import AssessmentRepo, IntegrityRepo, LabCompanionRepo
from aied_db.schema.lab_companion import EMBEDDING_DIMS

from .factories import COURSE_ID, FACTORIES, INSTRUCTOR_ID, STUDENT_ID


@pytest.fixture
def instructor_repo(db, course_id):
    return AssessmentRepo(guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id=course_id)


@pytest.fixture
def agent_repo(db, course_id):
    return AssessmentRepo(
        guarded(db, Role.ASSESSMENT_AGENT, actor_id="assessment_agent"), course_id=course_id
    )


@pytest.fixture
def integrity_repo(db, course_id):
    return IntegrityRepo(
        guarded(db, Role.INTEGRITY_AGENT, actor_id="integrity_agent"), course_id=course_id
    )


@pytest.fixture
def instructor_integrity_repo(db, course_id):
    return IntegrityRepo(guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id=course_id)


@pytest.fixture
def companion_repo(db, course_id):
    return LabCompanionRepo(
        guarded(db, Role.LAB_COMPANION_AGENT, actor_id="lab_companion_agent"), course_id=course_id
    )


@pytest.fixture
def instructor_companion_repo(db, course_id):
    return LabCompanionRepo(guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id=course_id)


def spec(**overrides):
    return {
        "assignment_id": "hw1_sorting",
        "title": "HW1",
        "description": "Sort integers.",
        "criteria": [
            {"id": "correctness", "name": "Correctness", "description": "", "max_points": 40.0},
            {"id": "quality", "name": "Quality", "description": "", "max_points": 10.0},
        ],
        "guidance": "Be fair.",
        "model": "claude-sonnet-5-5",
        "source": "upload",
        **overrides,
    }


# ── Assessment: assignments ──────────────────────────────────────────────────


def test_upsert_computes_max_score_from_the_criteria(instructor_repo):
    """`max_score` is derived upstream, so it is recomputed, not trusted."""
    assignment = instructor_repo.upsert_assignment({**spec(), "max_score": 999.0})
    assert assignment["max_score"] == 50.0


def test_upsert_rejects_an_assignment_with_no_criteria(instructor_repo):
    with pytest.raises(ValueError, match="at least one criterion"):
        instructor_repo.upsert_assignment(spec(criteria=[]))


def test_upsert_twice_replaces_and_keeps_created_at(instructor_repo):
    first = instructor_repo.upsert_assignment(spec())
    second = instructor_repo.upsert_assignment(spec(title="HW1 v2"))
    assert second["title"] == "HW1 v2"
    assert second["created_at"] == first["created_at"]
    assert second["updated_at"] is not None
    assert len(instructor_repo.list_assignments()) == 1


def test_list_and_get_assignment(instructor_repo):
    instructor_repo.upsert_assignment(spec())
    assert len(instructor_repo.list_assignments()) == 1
    assert instructor_repo.get_assignment("hw1_sorting")["title"] == "HW1"
    assert instructor_repo.get_assignment("nope") is None


def test_get_rubric_returns_the_criteria_and_total(instructor_repo):
    instructor_repo.upsert_assignment(spec())
    rubric = instructor_repo.get_rubric("hw1_sorting")
    assert rubric["max_score"] == 50.0
    assert len(rubric["criteria"]) == 2
    assert instructor_repo.get_rubric("nope") is None


def test_delete_assignment_takes_its_files_with_it(instructor_repo, db):
    instructor_repo.upsert_assignment(spec())
    instructor_repo.add_material("hw1_sorting", {"filename": "notes.txt", "extracted_text": "x"})
    assert instructor_repo.delete_assignment("hw1_sorting") is True
    assert db["assignment_files"].count_documents({}) == 0
    assert instructor_repo.delete_assignment("hw1_sorting") is False


# ── Assessment: secrets ──────────────────────────────────────────────────────


def test_expected_answer_is_hidden_from_students(instructor_repo):
    """Quiz answer keys are secret; `for_student=True` strips them."""
    criteria = [{"id": "q1", "name": "Q1", "max_points": 10.0, "expected_answer": "O(n log n)"}]
    instructor_repo.upsert_assignment(spec(criteria=criteria))

    for_instructor = instructor_repo.get_assignment("hw1_sorting")
    assert for_instructor["criteria"][0]["expected_answer"] == "O(n log n)"

    for_student = instructor_repo.get_assignment("hw1_sorting", for_student=True)
    assert "expected_answer" not in for_student["criteria"][0]


def test_student_rubric_and_list_also_hide_the_answer(instructor_repo):
    criteria = [{"id": "q1", "name": "Q1", "max_points": 10.0, "expected_answer": "42"}]
    instructor_repo.upsert_assignment(spec(criteria=criteria))
    rubric = instructor_repo.get_rubric("hw1_sorting", for_student=True)
    assert "expected_answer" not in rubric["criteria"][0]
    listed = instructor_repo.list_assignments(for_student=True)
    assert "expected_answer" not in listed[0]["criteria"][0]


def test_materials_never_include_the_solution(instructor_repo):
    instructor_repo.upsert_assignment(spec())
    instructor_repo.replace_solution("hw1_sorting", [{"filename": "solution.py", "extracted_text": "secret"}])
    instructor_repo.add_material("hw1_sorting", {"filename": "notes.txt", "extracted_text": "public"})

    materials = instructor_repo.list_materials("hw1_sorting")
    assert [f["filename"] for f in materials] == ["notes.txt"]
    assert [f["filename"] for f in instructor_repo.list_solution("hw1_sorting")] == ["solution.py"]


def test_replace_solution_replaces_the_whole_set(instructor_repo):
    instructor_repo.upsert_assignment(spec())
    instructor_repo.replace_solution("hw1_sorting", [{"filename": "v1.py", "extracted_text": "a"}])
    instructor_repo.replace_solution("hw1_sorting", [{"filename": "v2.py", "extracted_text": "b"}])
    assert [f["filename"] for f in instructor_repo.list_solution("hw1_sorting")] == ["v2.py"]


def test_delete_material(instructor_repo):
    instructor_repo.upsert_assignment(spec())
    instructor_repo.add_material("hw1_sorting", {"filename": "notes.txt", "extracted_text": "x"})
    assert instructor_repo.delete_material("hw1_sorting", "notes.txt") is True
    assert instructor_repo.delete_material("hw1_sorting", "notes.txt") is False


def test_the_agent_cannot_write_an_assignment(agent_repo):
    with pytest.raises(PermissionError_):
        agent_repo.upsert_assignment(spec())


# ── Assessment: lab import ───────────────────────────────────────────────────


def test_import_from_lab_copies_the_quiz_into_criteria(instructor_repo):
    assignment = instructor_repo.import_from_lab(FACTORIES["labs"]())
    assert assignment["assignment_id"] == "lab-01"
    assert assignment["max_score"] == 10.0
    assert assignment["criteria"][0]["expected_answer"] == "O(n log n)"
    assert assignment["imported_from_lab"] == {"course_id": COURSE_ID, "lab_id": "lab-01"}
    assert assignment["source"] == "lab:lab-01"


def test_import_is_a_copy_not_a_live_link(instructor_repo, admin_db):
    """Editing the lab afterwards must not change the assignment."""
    lab = FACTORIES["labs"]()
    admin_db.labs.insert_one(lab)
    instructor_repo.import_from_lab(lab)
    admin_db.labs.update_one({"lab_id": "lab-01"}, {"$set": {"title": "Changed"}})
    assert instructor_repo.get_assignment("lab-01")["title"] == "Sorting"


def test_import_refuses_a_lab_with_no_quiz(instructor_repo):
    with pytest.raises(ValueError, match="no quiz questions"):
        instructor_repo.import_from_lab(FACTORIES["labs"](quiz=[]))


# ── Assessment: results ──────────────────────────────────────────────────────


def test_insert_result_stores_filenames_not_content(agent_repo, db):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    stored = db["assessment_results"].find_one({"submission_id": "sub-001"})
    assert stored["files"] == ["main.py", "report.md"]
    assert "content" not in stored


def test_insert_result_always_starts_unreviewed(agent_repo):
    """Even if a caller passes review fields, a new result is unreviewed."""
    result = agent_repo.insert_result(
        FACTORIES["assessment_results"](final_score=40.0, instructor_notes="sneaky")
    )
    assert result["final_score"] is None
    assert result["instructor_notes"] == ""
    assert result["reviewed_at"] is None


def test_list_results_filters(agent_repo):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    agent_repo.insert_result(
        FACTORIES["assessment_results"](submission_id="sub-002", student_id="student-002", status="needs_review")
    )
    assert len(agent_repo.list_results()) == 2
    assert len(agent_repo.list_results(student_id=STUDENT_ID)) == 1
    assert len(agent_repo.list_results(status="needs_review")) == 1
    assert len(agent_repo.list_results(assignment_id="hw1_sorting")) == 2


def test_get_result_and_provenance(agent_repo):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    assert agent_repo.get_result("sub-001")["score"] == 35.0
    provenance = agent_repo.get_provenance("sub-001")
    assert provenance["model_id"] == "claude-sonnet-5-5"
    assert provenance["llm_calls"][0]["timestamp"].endswith("Z")
    assert agent_repo.get_result("nope") is None
    assert agent_repo.get_provenance("nope") is None


def test_review_replaces_the_score_rather_than_adjusting_it(agent_repo, instructor_repo):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    reviewed = instructor_repo.review_result("sub-001", final_score=38.0, notes="Generous on edge cases")
    assert reviewed["final_score"] == 38.0
    assert reviewed["score"] == 35.0  # the AI score is kept, not overwritten
    assert reviewed["status"] == "instructor_graded"
    assert reviewed["instructor_notes"] == "Generous on edge cases"


def test_review_is_bounded_by_max_score(instructor_repo, agent_repo):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    with pytest.raises(ValueError, match="negative"):
        instructor_repo.review_result("sub-001", final_score=-1.0)
    with pytest.raises(ValueError, match="cannot exceed 40"):
        instructor_repo.review_result("sub-001", final_score=41.0)


def test_review_of_a_missing_submission_returns_none(instructor_repo):
    assert instructor_repo.review_result("nope", final_score=1.0) is None


def test_review_is_revisioned(agent_repo, instructor_repo, db):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    instructor_repo.review_result("sub-001", final_score=38.0)
    revision = db["revisions"].find_one({"collection": "assessment_results"})
    assert revision["before"]["final_score"] is None
    assert revision["document_key"] == {"submission_id": "sub-001"}


def test_pending_review_queue_is_oldest_first(agent_repo):
    agent_repo.insert_result(FACTORIES["assessment_results"](submission_id="a", status="needs_review"))
    agent_repo.insert_result(FACTORIES["assessment_results"](submission_id="b", status="ai_graded"))
    agent_repo.insert_result(FACTORIES["assessment_results"](submission_id="c", status="needs_review"))
    assert [r["submission_id"] for r in agent_repo.pending_review()] == ["a", "c"]


def test_a_regrade_is_a_new_submission(agent_repo):
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    agent_repo.insert_result(FACTORIES["assessment_results"](submission_id="sub-002", score=40.0))
    assert len(agent_repo.list_results()) == 2


# ── Integrity ────────────────────────────────────────────────────────────────


def test_record_evaluation_needs_something_to_refer_to(integrity_repo):
    with pytest.raises(ValueError, match="submission_id or a session_id"):
        integrity_repo.record_evaluation(
            student_id=STUDENT_ID, issue_type="ai_generated", confidence=0.5, details="x"
        )


def test_record_evaluation_bounds_confidence(integrity_repo):
    with pytest.raises(ValueError, match="between 0 and 1"):
        integrity_repo.record_evaluation(
            student_id=STUDENT_ID, issue_type="x", confidence=1.5, details="y", submission_id="sub-001"
        )


def test_integrity_flow_from_record_to_escalation(integrity_repo):
    record = integrity_repo.record_evaluation(
        student_id=STUDENT_ID,
        issue_type="ai_generated",
        confidence=0.9,
        details="Style shift",
        submission_id="sub-001",
    )
    violation = integrity_repo.add_violation(
        record_id=record["record_id"],
        student_id=STUDENT_ID,
        rule="requested_solution",
        description="Asked outright",
        submission_id="sub-001",
    )
    escalation = integrity_repo.escalate(
        violation_id=violation["violation_id"], student_id=STUDENT_ID, reason="multiple_violations"
    )
    assert escalation["status"] == "pending_review"
    assert integrity_repo.count_violations(STUDENT_ID) == 1
    assert len(integrity_repo.pending_escalations()) == 1
    assert integrity_repo.get_record(record["record_id"])["confidence"] == 0.9


def test_the_agent_cannot_resolve_an_escalation(integrity_repo):
    record = integrity_repo.record_evaluation(
        student_id=STUDENT_ID, issue_type="x", confidence=0.9, details="y", submission_id="sub-001"
    )
    violation = integrity_repo.add_violation(
        record_id=record["record_id"], student_id=STUDENT_ID, rule="r", description="d"
    )
    escalation = integrity_repo.escalate(
        violation_id=violation["violation_id"], student_id=STUDENT_ID, reason="multiple_violations"
    )
    with pytest.raises(PermissionError_):
        integrity_repo.resolve_escalation(
            escalation["escalation_id"], reviewer_notes="fine", resolved_by="integrity_agent"
        )


def test_an_instructor_resolves_the_escalation(integrity_repo, instructor_integrity_repo):
    record = integrity_repo.record_evaluation(
        student_id=STUDENT_ID, issue_type="x", confidence=0.9, details="y", submission_id="sub-001"
    )
    violation = integrity_repo.add_violation(
        record_id=record["record_id"], student_id=STUDENT_ID, rule="r", description="d"
    )
    escalation = integrity_repo.escalate(
        violation_id=violation["violation_id"], student_id=STUDENT_ID, reason="multiple_violations"
    )
    resolved = instructor_integrity_repo.resolve_escalation(
        escalation["escalation_id"], reviewer_notes="Spoke with the student", resolved_by=INSTRUCTOR_ID
    )
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == INSTRUCTOR_ID
    assert instructor_integrity_repo.pending_escalations() == []


def test_resolving_a_missing_escalation_returns_none(instructor_integrity_repo):
    assert instructor_integrity_repo.resolve_escalation("nope", reviewer_notes="", resolved_by="x") is None


def test_integrity_sessions(integrity_repo):
    integrity_repo.open_session("isession-001", STUDENT_ID)
    integrity_repo.touch_session("isession-001", status="escalated")
    session = integrity_repo.get_session("isession-001")
    assert session["status"] == "escalated"
    assert len(integrity_repo.list_sessions(status="escalated")) == 1


def test_report_risk_level_is_checked(integrity_repo):
    with pytest.raises(ValueError, match="low, medium or high"):
        integrity_repo.add_report(student_id=STUDENT_ID, summary="x", risk_level="severe")


def test_reports_round_trip(integrity_repo):
    report = integrity_repo.add_report(
        student_id=STUDENT_ID, summary="No issues", risk_level="low", submission_id="sub-001"
    )
    assert integrity_repo.get_report(report["report_id"])["risk_level"] == "low"
    assert len(integrity_repo.list_reports(submission_id="sub-001")) == 1


def test_integrity_submission_id_points_at_an_assessment_result(integrity_repo, agent_repo):
    """The decision of record: integrity's submission_id is a result's id."""
    agent_repo.insert_result(FACTORIES["assessment_results"]())
    record = integrity_repo.record_evaluation(
        student_id=STUDENT_ID, issue_type="x", confidence=0.5, details="y", submission_id="sub-001"
    )
    assert agent_repo.get_result(record["submission_id"]) is not None


# ── Lab companion ────────────────────────────────────────────────────────────


def embedding(value=0.02):
    return [value] * EMBEDDING_DIMS


def test_instructor_adds_a_document_and_the_agent_indexes_it(
    instructor_companion_repo, companion_repo
):
    document = instructor_companion_repo.add_document(
        {"title": "Week 1", "filename": "w1.pdf", "extracted_text": "slides"}
    )
    assert document["chunk_count"] == 0
    assert document["indexed_at"] is None

    written = companion_repo.add_chunks(
        document["document_id"],
        [{"chunk_index": 0, "content": "slides", "embedding": embedding()}],
        embedding_model="text-embedding-3-small",
    )
    assert written == 1
    indexed = companion_repo.get_document(document["document_id"])
    assert indexed["chunk_count"] == 1
    assert indexed["indexed_at"] is not None


def test_the_agent_cannot_upload_a_document(companion_repo):
    """RAG documents are instructor uploads: human data."""
    with pytest.raises(PermissionError_):
        companion_repo.add_document({"title": "mine", "extracted_text": "x"})


def test_vector_id_matches_the_azure_uid_composition(instructor_companion_repo, companion_repo, db):
    document = instructor_companion_repo.add_document({"title": "W", "extracted_text": "t"})
    companion_repo.add_chunks(
        document["document_id"],
        [{"chunk_index": 2, "content": "t", "embedding": embedding()}],
        embedding_model="m",
    )
    stored = db["rag_vectors"].find_one({"chunk_index": 2})
    assert stored["vector_id"] == f"{document['document_id']}_2"


def test_add_chunks_rejects_a_wrong_length_embedding(instructor_companion_repo, companion_repo):
    document = instructor_companion_repo.add_document({"title": "W", "extracted_text": "t"})
    with pytest.raises(ValueError, match="dimensions"):
        companion_repo.add_chunks(
            document["document_id"],
            [{"chunk_index": 0, "content": "t", "embedding": [0.1, 0.2]}],
            embedding_model="m",
        )


def test_add_chunks_refuses_an_unknown_document(companion_repo):
    with pytest.raises(ValueError, match="no rag_document"):
        companion_repo.add_chunks(
            "ghost", [{"chunk_index": 0, "content": "t", "embedding": embedding()}], embedding_model="m"
        )


def test_add_chunks_with_nothing_to_add(companion_repo):
    assert companion_repo.add_chunks("ghost", [], embedding_model="m") == 0


def test_search_hit_maps_onto_retrieved_doc_fields():
    """`content` / `source` / `uid` are RetrievedDoc's fields, verbatim."""
    hit = LabCompanionRepo._hit_row(
        {"content": "chunk text", "document_id": "doc-001", "vector_id": "doc-001_0", "score": 0.9},
        {"doc-001": "Week 1 slides"},
    )
    assert hit["content"] == "chunk text"
    assert hit["source"] == "Week 1 slides"
    assert hit["uid"] == "doc-001_0"


def test_search_rejects_a_wrong_length_query_vector(companion_repo):
    with pytest.raises(ValueError, match="dimensions"):
        companion_repo.search([0.1, 0.2])


def test_unindexed_documents_can_be_listed(instructor_companion_repo, companion_repo):
    first = instructor_companion_repo.add_document({"title": "A", "extracted_text": "a"})
    instructor_companion_repo.add_document({"title": "B", "extracted_text": "b"})
    companion_repo.add_chunks(
        first["document_id"],
        [{"chunk_index": 0, "content": "a", "embedding": embedding()}],
        embedding_model="m",
    )
    pending = companion_repo.list_documents(unindexed_only=True)
    assert [d["title"] for d in pending] == ["B"]


def test_student_files_are_read_only_to_the_agent(companion_repo, admin_db):
    admin_db.student_files.insert_one(FACTORIES["student_files"]())
    assert len(companion_repo.list_student_files(STUDENT_ID)) == 1
    with pytest.raises(PermissionError_):
        companion_repo.add_student_file({"student_id": STUDENT_ID, "filename": "x.py"})


def test_query_log_records_what_was_retrieved(companion_repo):
    logged = companion_repo.log_query(
        student_id=STUDENT_ID,
        question="How does partition work?",
        retrieved=[
            {"uid": "doc-001_0", "document_id": "doc-001", "source": "Week 1", "score": 0.88}
        ],
        session_id="session-001",
        top_k=3,
        latency_ms=40,
        embedding_model="text-embedding-3-small",
    )
    assert logged["retrieved"][0]["vector_id"] == "doc-001_0"
    assert logged["asked_at"].endswith("Z")
    assert len(companion_repo.list_queries(student_id=STUDENT_ID)) == 1
    assert len(companion_repo.list_queries(session_id="session-001")) == 1


def test_query_log_accepts_an_empty_result_set(companion_repo):
    logged = companion_repo.log_query(student_id=STUDENT_ID, question="anything?", retrieved=[])
    assert logged["retrieved"] == []


# ── Course isolation, across every repo ──────────────────────────────────────


def test_repos_require_a_course_id(db):
    for repo_class in (AssessmentRepo, IntegrityRepo, LabCompanionRepo):
        with pytest.raises(ValueError, match="course_id"):
            repo_class(db, course_id="")


def test_a_repo_never_sees_another_course(db, instructor_repo):
    other = AssessmentRepo(
        guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID), course_id="course-other"
    )
    instructor_repo.upsert_assignment(spec())
    other.upsert_assignment(spec())
    assert len(instructor_repo.list_assignments()) == 1
    assert other.get_assignment("hw1_sorting") is not None
