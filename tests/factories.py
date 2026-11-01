"""Minimal valid documents for every collection.

One factory per collection, each producing the smallest document that
satisfies the validator. Tests mutate a copy to check that the validator
rejects what it should.
"""

from __future__ import annotations

from typing import Any, Callable

from aied_db._util import now
from aied_db.schema.lab_companion import EMBEDDING_DIMS

COURSE_ID = "course-csc580"
STUDENT_ID = "student-001"
INSTRUCTOR_ID = "instructor-001"


def _embedding(value: float = 0.01) -> list[float]:
    return [value] * EMBEDDING_DIMS


def user(**overrides: Any) -> dict:
    return {
        "id": "user-001",
        "email": "student@calpoly.edu",
        "name": "Test Student",
        "sso_subject": None,
        "role": "student",
        "created_at": now(),
        **overrides,
    }


def course(**overrides: Any) -> dict:
    return {
        "id": COURSE_ID,
        "name": "CSC 580",
        "instructor_id": INSTRUCTOR_ID,
        "blob_container_name": "csc580",
        "search_index_name": "csc580-idx",
        "search_indexer_name": "csc580-indexer",
        "search_datasource_name": "csc580-ds",
        "created_at": now(),
        **overrides,
    }


def enrollment(**overrides: Any) -> dict:
    return {
        "course_id": COURSE_ID,
        "user_id": "user-001",
        "enrolled_at": now(),
        **overrides,
    }


def lab(**overrides: Any) -> dict:
    return {
        "lab_id": "lab-01",
        "course_id": COURSE_ID,
        "title": "Sorting",
        "spec_markdown": "# Sorting\n",
        "quiz": [
            {
                "id": "q1",
                "question": "What is the time complexity?",
                "type": "short_answer",
                "expected_answer": "O(n log n)",
                "rubric_points": 10,
                "difficulty": "basic",
            }
        ],
        "rubric": {
            "code_weight": 0.6,
            "report_weight": 0.3,
            "manual_weight": 0.1,
            "criteria": [{"name": "Correctness", "weight": 1.0, "description": "Sorts correctly"}],
        },
        "learning_objectives": ["Explain sorting"],
        "difficulty": "intermediate",
        "estimated_duration_min": 60,
        "material_weight": "balanced",
        "approval_status": "pending",
        "version": 1,
        "generated_at": now(),
        "last_updated": now(),
        **overrides,
    }


def lab_file(**overrides: Any) -> dict:
    return {
        "file_id": "labfile-001",
        "lab_id": "lab-01",
        "course_id": COURSE_ID,
        "filename": "notes.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1024,
        "kind": "material",
        "uploaded_at": now(),
        "storage_key": f"labs/{COURSE_ID}/lab-01/labfile-001/notes.pdf",
        "extracted_text": "lecture notes",
        "char_count": 13,
        **overrides,
    }


def interaction(**overrides: Any) -> dict:
    return {
        "id": "interaction-001",
        "course_id": COURSE_ID,
        "student_id": STUDENT_ID,
        "session_id": "session-001",
        "timestamp": now(),
        "message": "How do I start?",
        "question_type": "conceptual",
        "hint_level": 1,
        "difficulty": "basic",
        "response_time_ms": 120,
        "lab_id": "lab-01",
        "logged_at": now(),
        **overrides,
    }


def guardian_session(**overrides: Any) -> dict:
    return {
        "session_id": "session-001",
        "student_id": STUDENT_ID,
        "lab_id": "lab-01",
        "course_id": COURSE_ID,
        "started_at": now(),
        "ended_at": None,
        "question_count": 0,
        "violation_count": 0,
        "escalated": False,
        "status": "active",
        "report_generated": False,
        "report_id": None,
        **overrides,
    }


def guardian_question(**overrides: Any) -> dict:
    return {
        "question_id": "question-001",
        "session_id": "session-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "sequence_number": 1,
        "timestamp": now(),
        "text": "What is a linked list?",
        "classification": "conceptual",
        "violation": False,
        "violation_type": None,
        "concept_tags": ["linked-list"],
        **overrides,
    }


def guardian_violation(**overrides: Any) -> dict:
    return {
        "violation_id": "violation-001",
        "question_id": "question-001",
        "session_id": "session-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "sequence_number": 1,
        "timestamp": now(),
        "violation_type": "requested_solution",
        "severity": "medium",
        "question_text": "Just give me the code",
        **overrides,
    }


def guardian_verification(**overrides: Any) -> dict:
    return {
        "verification_id": "verification-001",
        "session_id": "session-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "question_id": "question-001",
        "timestamp": now(),
        "passes": True,
        "reason": None,
        "guidance_level": "FULL",
        "draft_excerpt": "",
        **overrides,
    }


def rag_document(**overrides: Any) -> dict:
    return {
        "document_id": "doc-001",
        "course_id": COURSE_ID,
        "lab_id": None,
        "title": "Week 1 slides",
        "filename": "week1.pdf",
        "content_type": "application/pdf",
        "size_bytes": 2048,
        "storage_key": f"rag/{COURSE_ID}/doc-001/week1.pdf",
        "extracted_text": "slide text",
        "char_count": 10,
        "chunk_count": 0,
        "uploaded_by": INSTRUCTOR_ID,
        "uploaded_at": now(),
        "indexed_at": None,
        **overrides,
    }


def rag_vector(**overrides: Any) -> dict:
    return {
        "vector_id": "doc-001_0",
        "document_id": "doc-001",
        "course_id": COURSE_ID,
        "lab_id": None,
        "chunk_index": 0,
        "content": "slide text",
        "embedding": _embedding(),
        "embedding_model": "text-embedding-3-small",
        "char_count": 10,
        "created_at": now(),
        **overrides,
    }


def student_file(**overrides: Any) -> dict:
    return {
        "file_id": "sfile-001",
        "course_id": COURSE_ID,
        "student_id": STUDENT_ID,
        "lab_id": "lab-01",
        "session_id": None,
        "filename": "answer.py",
        "content_type": "text/x-python",
        "size_bytes": 128,
        "storage_key": f"students/{COURSE_ID}/{STUDENT_ID}/sfile-001/answer.py",
        "extracted_text": "print(1)",
        "char_count": 8,
        "uploaded_at": now(),
        **overrides,
    }


def rag_query(**overrides: Any) -> dict:
    return {
        "query_id": "query-001",
        "course_id": COURSE_ID,
        "student_id": STUDENT_ID,
        "session_id": "session-001",
        "lab_id": "lab-01",
        "question": "How does quicksort partition?",
        "retrieved": [{"vector_id": "doc-001_0", "document_id": "doc-001", "source": "Week 1 slides", "score": 0.91}],
        "top_k": 3,
        "latency_ms": 42,
        "embedding_model": "text-embedding-3-small",
        "asked_at": now(),
        **overrides,
    }


def assignment(**overrides: Any) -> dict:
    return {
        "assignment_id": "hw1_sorting",
        "course_id": COURSE_ID,
        "title": "HW1: Sorting Algorithms",
        "description": "Sort integers from stdin.",
        "criteria": [
            {
                "id": "correctness",
                "name": "Correctness",
                "description": "Sorts any valid input",
                "max_points": 40.0,
                "expected_answer": "",
            }
        ],
        "guidance": "Be encouraging but rigorous.",
        "model": "claude-sonnet-5-5",
        "source": "built-in",
        "max_score": 40.0,
        "created_at": now(),
        "updated_at": None,
        **overrides,
    }


def assignment_file(**overrides: Any) -> dict:
    return {
        "file_id": "afile-001",
        "assignment_id": "hw1_sorting",
        "course_id": COURSE_ID,
        "filename": "solution.py",
        "content_type": "text/x-python",
        "size_bytes": 256,
        "kind": "solution",
        "original": False,
        "uploaded_by": INSTRUCTOR_ID,
        "uploaded_at": now(),
        "storage_key": f"assignments/{COURSE_ID}/hw1_sorting/solution/afile-001/solution.py",
        "extracted_text": "print(sorted(...))",
        "char_count": 18,
        **overrides,
    }


def assessment_result(**overrides: Any) -> dict:
    return {
        "submission_id": "sub-001",
        "student_id": STUDENT_ID,
        "assignment_id": "hw1_sorting",
        "course_id": COURSE_ID,
        "files": ["main.py", "report.md"],
        "model": "claude-sonnet-5-5",
        "mock": False,
        "criteria": [
            {
                "id": "correctness",
                "name": "Correctness",
                "score": 35.0,
                "max_points": 40.0,
                "justification": "Handles duplicates and negatives.",
                "evidence": "",
            }
        ],
        "score": 35.0,
        "max_score": 40.0,
        "feedback": {
            "summary": "Solid work.",
            "strengths": ["Clear code"],
            "improvements": ["Discuss edge cases"],
            "next_steps": [],
        },
        "status": "ai_graded",
        "review_reasons": [],
        "final_score": None,
        "instructor_notes": "",
        "reviewed_at": None,
        "created_at": now(),
        "provenance": {
            "submission_hash": "abc123",
            "spec_hash": "def456",
            "model_id": "claude-sonnet-5-5",
            "llm_calls": [
                {"stage": "grade", "latency_ms": 900, "success": True, "error": "", "timestamp": now()}
            ],
            "created_at": now(),
        },
        **overrides,
    }


def integrity_session(**overrides: Any) -> dict:
    return {
        "session_id": "isession-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "started_at": now(),
        "last_activity_at": now(),
        "status": "active",
        **overrides,
    }


def integrity_record(**overrides: Any) -> dict:
    return {
        "record_id": "irecord-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "submission_id": "sub-001",
        "session_id": None,
        "issue_type": "ai_generated",
        "confidence": 0.82,
        "details": "Style shift mid-file.",
        "evaluated_at": now(),
        **overrides,
    }


def integrity_violation(**overrides: Any) -> dict:
    return {
        "violation_id": "iviolation-001",
        "record_id": "irecord-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "session_id": None,
        "submission_id": "sub-001",
        "rule": "requested_solution",
        "description": "Asked for the full answer.",
        "detected_at": now(),
        **overrides,
    }


def integrity_escalation(**overrides: Any) -> dict:
    return {
        "escalation_id": "iescalation-001",
        "violation_id": "iviolation-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "session_id": None,
        "reason": "multiple_violations",
        "escalated_at": now(),
        "status": "pending_review",
        "reviewer_notes": None,
        "resolved_by": None,
        "resolved_at": None,
        **overrides,
    }


def integrity_report(**overrides: Any) -> dict:
    return {
        "report_id": "ireport-001",
        "student_id": STUDENT_ID,
        "course_id": COURSE_ID,
        "submission_id": "sub-001",
        "session_id": None,
        "summary": "No issues found.",
        "risk_level": "low",
        "generated_at": now(),
        **overrides,
    }


def revision(**overrides: Any) -> dict:
    return {
        "revision_id": "rev-001",
        "collection": "labs",
        "document_key": {"course_id": COURSE_ID, "lab_id": "lab-01"},
        "course_id": COURSE_ID,
        "action": "update",
        "before": {"lab_id": "lab-01", "title": "Old title"},
        "actor_id": INSTRUCTOR_ID,
        "actor_role": "instructor",
        "recorded_at": now(),
        **overrides,
    }


def agent_config_default(**overrides: Any) -> dict:
    return {
        "agent": "guardian",
        "key": "escalation.violation_threshold",
        "value": 3,
        "type": "int",
        "choices": None,
        "minimum": 1,
        "maximum": 20,
        "editable_by": ["admin", "instructor"],
        "description": "Violations before escalation.",
        "source_file": "guardian.json",
        "seeded_at": now(),
        **overrides,
    }


def agent_config(**overrides: Any) -> dict:
    return {
        "config_id": "config-001",
        "scope": "course",
        "agent": "guardian",
        "key": "escalation.violation_threshold",
        "value": 5,
        "course_id": COURSE_ID,
        "user_id": None,
        "set_by": INSTRUCTOR_ID,
        "updated_at": now(),
        **overrides,
    }


#: Collection name -> factory. Every collection in the registry must appear.
FACTORIES: dict[str, Callable[..., dict]] = {
    "users": user,
    "courses": course,
    "enrollments": enrollment,
    "labs": lab,
    "lab_files": lab_file,
    "interactions": interaction,
    "guardian_sessions": guardian_session,
    "guardian_questions": guardian_question,
    "guardian_violations": guardian_violation,
    "guardian_verifications": guardian_verification,
    "rag_documents": rag_document,
    "rag_vectors": rag_vector,
    "student_files": student_file,
    "rag_queries": rag_query,
    "assignments": assignment,
    "assignment_files": assignment_file,
    "assessment_results": assessment_result,
    "integrity_sessions": integrity_session,
    "integrity_records": integrity_record,
    "integrity_violations": integrity_violation,
    "integrity_escalations": integrity_escalation,
    "integrity_reports": integrity_report,
    "revisions": revision,
    "agent_config_defaults": agent_config_default,
    "agent_configs": agent_config,
}
