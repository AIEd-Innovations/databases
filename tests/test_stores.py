"""The Mongo stores, against the protocol ui-student's agents rely on."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aied_db import IntegrityError, Role, guarded
from aied_db._util import now
from aied_db.stores import MongoGuardianStore, MongoParticipantStore, MongoSystemStore

from .factories import COURSE_ID, INSTRUCTOR_ID, STUDENT_ID

ISO = "2026-10-28T12:00:00+00:00"
OTHER_COURSE = "course-csc581"


@pytest.fixture
def participant_store(db, course_id):
    return MongoParticipantStore(
        guarded(db, Role.PARTICIPANT_AGENT, actor_id="participant_agent"), course_id=course_id
    )


@pytest.fixture
def guardian_store(db, course_id):
    return MongoGuardianStore(
        guarded(db, Role.GUARDIAN_AGENT, actor_id="guardian_agent"), course_id=course_id
    )


@pytest.fixture
def system_store(admin_db):
    return MongoSystemStore(admin_db)


def interaction(**overrides):
    return {
        "id": "i1",
        "student_id": STUDENT_ID,
        "session_id": "s1",
        "timestamp": ISO,
        "message": "hello",
        "question_type": "conceptual",
        "hint_level": 1,
        "difficulty": "basic",
        "response_time_ms": 50,
        "lab_id": "lab-01",
        **overrides,
    }


# ── Protocol conformance ─────────────────────────────────────────────────────


def test_stores_satisfy_the_protocols(participant_store, guardian_store, system_store):
    """Every protocol method exists with the right name."""
    for method in ("init", "close", "insert_interaction", "fetch_for_student"):
        assert callable(getattr(participant_store, method))
    for method in (
        "init", "close", "create_session", "get_session", "update_session_counters",
        "close_session", "insert_question", "insert_violation", "insert_verification",
    ):
        assert callable(getattr(guardian_store, method))
    for method in (
        "insert_user", "get_user", "get_user_by_email", "get_user_by_sso_subject",
        "list_users", "update_user_sso_subject", "delete_user", "insert_course",
        "get_course", "list_courses", "update_course", "delete_course",
        "insert_enrollment", "delete_enrollment", "is_enrolled",
        "list_enrolled_users", "list_courses_for_user",
    ):
        assert callable(getattr(system_store, method))


def test_init_and_close_are_safe_no_ops(participant_store, guardian_store):
    assert participant_store.init() is None
    assert participant_store.close() is None
    assert guardian_store.init() is None
    assert guardian_store.close() is None


def test_course_id_is_required(db):
    with pytest.raises(ValueError, match="course_id"):
        MongoParticipantStore(db, course_id="")
    with pytest.raises(ValueError, match="course_id"):
        MongoGuardianStore(db, course_id="")


# ── Participant ──────────────────────────────────────────────────────────────


def test_interaction_round_trips_with_an_iso_timestamp(participant_store):
    participant_store.insert_interaction(interaction())
    rows = participant_store.fetch_for_student(STUDENT_ID)
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-10-28T12:00:00Z"
    assert rows[0]["message"] == "hello"


def test_interaction_rows_have_no_course_id_or_object_id(participant_store):
    """The SQLite table had neither, and the agent reads these keys."""
    participant_store.insert_interaction(interaction())
    row = participant_store.fetch_for_student(STUDENT_ID)[0]
    assert set(row) == {
        "id", "student_id", "session_id", "timestamp", "message",
        "question_type", "hint_level", "difficulty", "response_time_ms", "lab_id",
    }


def test_interactions_are_oldest_first(participant_store):
    participant_store.insert_interaction(interaction(id="i2", timestamp="2026-10-28T13:00:00Z"))
    participant_store.insert_interaction(interaction(id="i1", timestamp="2026-10-28T11:00:00Z"))
    assert [r["id"] for r in participant_store.fetch_for_student(STUDENT_ID)] == ["i1", "i2"]


def test_one_course_cannot_see_another(db, participant_store):
    other = MongoParticipantStore(
        guarded(db, Role.PARTICIPANT_AGENT, actor_id="participant_agent"), course_id=OTHER_COURSE
    )
    participant_store.insert_interaction(interaction())
    other.insert_interaction(interaction(id="i2"))
    assert len(participant_store.fetch_for_student(STUDENT_ID)) == 1
    assert len(other.fetch_for_student(STUDENT_ID)) == 1


def test_timestamps_are_stored_as_dates(participant_store, raw_db):
    participant_store.insert_interaction(interaction())
    stored = raw_db["interactions"].find_one({"id": "i1"})
    assert isinstance(stored["timestamp"], datetime)
    assert stored["timestamp"].tzinfo is not None


# ── Guardian ─────────────────────────────────────────────────────────────────


def session(**overrides):
    return {
        "session_id": "s1",
        "student_id": STUDENT_ID,
        "lab_id": "lab-01",
        "course_id": COURSE_ID,
        "started_at": ISO,
        **overrides,
    }


def test_create_and_get_session(guardian_store):
    guardian_store.create_session(session())
    found = guardian_store.get_session("s1", STUDENT_ID)
    assert found["status"] == "active"
    assert found["escalated"] is False
    assert found["report_generated"] is False
    assert found["started_at"] == "2026-10-28T12:00:00Z"
    assert found["questions"] == []
    assert found["violations"] == []


def test_get_session_returns_none_when_missing(guardian_store):
    assert guardian_store.get_session("nope", STUDENT_ID) is None


def test_get_session_requires_the_right_student(guardian_store):
    guardian_store.create_session(session())
    assert guardian_store.get_session("s1", "someone-else") is None


def test_duplicate_session_raises_integrity_error_by_name(guardian_store):
    """GuardianAgent checks `"IntegrityError" in type(e).__name__`."""
    guardian_store.create_session(session())
    with pytest.raises(IntegrityError) as excinfo:
        guardian_store.create_session(session())
    assert "IntegrityError" in type(excinfo.value).__name__


def test_the_stores_course_wins_over_the_agents_default(guardian_store, raw_db):
    """SQLite defaulted to "CSC580"; the store's own course must win."""
    guardian_store.create_session(session(course_id="CSC580"))
    assert raw_db["guardian_sessions"].find_one({"session_id": "s1"})["course_id"] == COURSE_ID


def test_questions_and_violations_come_back_in_sequence_order(guardian_store):
    guardian_store.create_session(session())
    for number in (3, 1, 2):
        guardian_store.insert_question(
            "s1",
            STUDENT_ID,
            {
                "question_id": f"q{number}",
                "sequence_number": number,
                "timestamp": ISO,
                "text": "why?",
                "classification": "conceptual",
                "violation": False,
                "concept_tags": ["t"],
            },
        )
    found = guardian_store.get_session("s1", STUDENT_ID)
    assert [q["sequence_number"] for q in found["questions"]] == [1, 2, 3]


def test_question_rows_keep_the_sqlite_shape(guardian_store):
    guardian_store.create_session(session())
    guardian_store.insert_question(
        "s1",
        STUDENT_ID,
        {
            "question_id": "q1",
            "sequence_number": 1,
            "timestamp": ISO,
            "text": "why?",
            "classification": "conceptual",
            "violation": True,
            "violation_type": "requested_solution",
            "concept_tags": ["recursion"],
        },
    )
    question = guardian_store.get_session("s1", STUDENT_ID)["questions"][0]
    assert set(question) == {
        "question_id", "session_id", "student_id", "sequence_number", "timestamp",
        "text", "classification", "violation", "violation_type", "concept_tags",
    }
    assert question["violation"] is True
    assert question["concept_tags"] == ["recursion"]
    assert question["timestamp"] == "2026-10-28T12:00:00Z"


def test_concept_tags_default_to_an_empty_list(guardian_store):
    guardian_store.create_session(session())
    guardian_store.insert_question(
        "s1",
        STUDENT_ID,
        {
            "question_id": "q1",
            "sequence_number": 1,
            "timestamp": ISO,
            "text": "why?",
            "classification": "conceptual",
        },
    )
    assert guardian_store.get_session("s1", STUDENT_ID)["questions"][0]["concept_tags"] == []


def test_violations_are_embedded_in_the_session(guardian_store):
    guardian_store.create_session(session())
    guardian_store.insert_violation(
        "s1",
        STUDENT_ID,
        {
            "violation_id": "v1",
            "question_id": "q1",
            "sequence_number": 1,
            "timestamp": ISO,
            "violation_type": "requested_solution",
            "severity": "high",
            "question_text": "give me the code",
        },
    )
    violation = guardian_store.get_session("s1", STUDENT_ID)["violations"][0]
    assert violation["severity"] == "high"
    assert "course_id" not in violation


def test_update_session_counters(guardian_store):
    guardian_store.create_session(session())
    guardian_store.update_session_counters(
        "s1", STUDENT_ID, question_count=5, violation_count=2, escalated=True
    )
    found = guardian_store.get_session("s1", STUDENT_ID)
    assert (found["question_count"], found["violation_count"]) == (5, 2)
    assert found["escalated"] is True


def test_close_session_derives_report_generated(guardian_store):
    guardian_store.create_session(session())
    guardian_store.close_session("s1", STUDENT_ID, "2026-10-28T14:00:00Z", "report-1")
    found = guardian_store.get_session("s1", STUDENT_ID)
    assert found["status"] == "closed"
    assert found["ended_at"] == "2026-10-28T14:00:00Z"
    assert found["report_generated"] is True
    assert found["report_id"] == "report-1"


def test_close_session_without_a_report(guardian_store):
    guardian_store.create_session(session())
    guardian_store.close_session("s1", STUDENT_ID, "2026-10-28T14:00:00Z", None)
    found = guardian_store.get_session("s1", STUDENT_ID)
    assert found["report_generated"] is False
    assert found["report_id"] is None


def test_insert_verification_defaults_match_sqlite(guardian_store, raw_db):
    guardian_store.create_session(session())
    guardian_store.insert_verification(
        "s1", STUDENT_ID, {"verification_id": "ver1", "timestamp": ISO, "passes": False}
    )
    stored = raw_db["guardian_verifications"].find_one({"verification_id": "ver1"})
    assert stored["guidance_level"] == "FULL"
    assert stored["draft_excerpt"] == ""
    assert stored["passes"] is False
    assert stored["question_id"] is None


def test_counter_update_is_revisioned(guardian_store, raw_db):
    guardian_store.create_session(session())
    guardian_store.update_session_counters(
        "s1", STUDENT_ID, question_count=1, violation_count=0, escalated=False
    )
    assert raw_db["revisions"].count_documents({"collection": "guardian_sessions"}) == 1


# ── System ───────────────────────────────────────────────────────────────────


def user(**overrides):
    return {
        "id": "u1",
        "email": "Student@CalPoly.edu",
        "name": "Test",
        "sso_subject": "sso-1",
        "role": "student",
        "created_at": ISO,
        **overrides,
    }


def course(**overrides):
    return {
        "id": COURSE_ID,
        "name": "CSC 580",
        "instructor_id": INSTRUCTOR_ID,
        "blob_container_name": "c",
        "search_index_name": "i",
        "search_indexer_name": "ix",
        "search_datasource_name": "ds",
        "created_at": ISO,
        **overrides,
    }


def test_user_round_trip(system_store):
    system_store.insert_user(user())
    found = system_store.get_user("u1")
    assert found["email"] == "Student@CalPoly.edu"
    assert found["created_at"] == "2026-10-28T12:00:00Z"
    assert "_id" not in found


def test_email_lookup_is_exact_in_the_stored_case(system_store):
    system_store.insert_user(user())
    assert system_store.get_user_by_email("Student@CalPoly.edu")["id"] == "u1"
    assert system_store.get_user_by_email("nobody@calpoly.edu") is None


def test_email_index_declares_case_insensitive_collation():
    """SQLite used `COLLATE NOCASE`, so the index must match it.

    mongomock accepts a `collation` argument and then ignores it, so the
    BEHAVIOUR cannot be asserted here — `tests/test_atlas_smoke.py` covers
    that against a real server. What is asserted here is that the index is
    declared with the collation at all, which is the part that gets lost in
    a refactor.
    """
    from aied_db.schema import INDEX_OPTIONS

    collation = INDEX_OPTIONS["users"]["email_1"]["collation"]
    assert collation["strength"] == 2, "strength 2 is what makes it case-insensitive"
    # Partial, not sparse: users without SSO store sso_subject = null, and a
    # unique sparse index would reject the second one (see test_index_options).
    assert INDEX_OPTIONS["users"]["sso_subject_1"] == {
        "partialFilterExpression": {"sso_subject": {"$type": "string"}}
    }


@pytest.mark.skip(
    reason="mongomock ignores collation; covered by tests/test_atlas_smoke.py on real Atlas"
)
def test_email_lookup_is_case_insensitive(system_store):
    system_store.insert_user(user())
    assert system_store.get_user_by_email("student@calpoly.edu")["id"] == "u1"
    assert system_store.get_user_by_email("STUDENT@CALPOLY.EDU")["id"] == "u1"


def test_sso_lookup(system_store):
    system_store.insert_user(user())
    assert system_store.get_user_by_sso_subject("sso-1")["id"] == "u1"
    assert system_store.get_user_by_sso_subject("nope") is None


def test_duplicate_email_is_refused(system_store):
    system_store.insert_user(user())
    with pytest.raises(IntegrityError):
        system_store.insert_user(user(id="u2"))


def test_two_users_may_both_have_no_sso_subject(system_store):
    """The unique sso_subject index is partial (strings only), so nulls do not collide."""
    system_store.insert_user(user(id="u1", email="a@x.edu", sso_subject=None))
    system_store.insert_user(user(id="u2", email="b@x.edu", sso_subject=None))
    assert len(system_store.list_users()) == 2


def test_update_and_delete_user(system_store):
    system_store.insert_user(user(sso_subject=None))
    system_store.update_user_sso_subject("u1", "sso-9")
    assert system_store.get_user("u1")["sso_subject"] == "sso-9"
    system_store.delete_user("u1")
    assert system_store.get_user("u1") is None


def test_course_round_trip_drops_the_legacy_db_paths(system_store, raw_db):
    """routes/admin.py still passes participant_db_path / guardian_db_path."""
    system_store.insert_course(
        {**course(), "participant_db_path": None, "guardian_db_path": None}
    )
    stored = raw_db["courses"].find_one({"id": COURSE_ID})
    assert "participant_db_path" not in stored
    assert system_store.get_course(COURSE_ID)["name"] == "CSC 580"


def test_update_course_ignores_an_empty_patch(system_store):
    system_store.insert_course(course())
    system_store.update_course(COURSE_ID, {})
    assert system_store.get_course(COURSE_ID)["name"] == "CSC 580"


def test_update_course_applies_a_patch(system_store):
    system_store.insert_course(course())
    system_store.update_course(COURSE_ID, {"name": "CSC 580 Fall"})
    assert system_store.get_course(COURSE_ID)["name"] == "CSC 580 Fall"


def test_list_courses_filters_by_instructor(system_store):
    system_store.insert_course(course())
    system_store.insert_course(course(id="other", instructor_id="instructor-002"))
    assert len(system_store.list_courses()) == 2
    assert len(system_store.list_courses(instructor_id=INSTRUCTOR_ID)) == 1


def test_enrollment_is_idempotent(system_store):
    """SQLite used INSERT OR IGNORE: enrolling twice is not an error."""
    system_store.insert_user(user())
    system_store.insert_course(course())
    system_store.insert_enrollment(COURSE_ID, "u1", ISO)
    system_store.insert_enrollment(COURSE_ID, "u1", ISO)
    assert system_store.is_enrolled(COURSE_ID, "u1") is True
    assert len(system_store.list_enrolled_users(COURSE_ID)) == 1


def test_list_enrolled_users_overlays_enrolled_at(system_store):
    system_store.insert_user(user())
    system_store.insert_course(course())
    system_store.insert_enrollment(COURSE_ID, "u1", ISO)
    row = system_store.list_enrolled_users(COURSE_ID)[0]
    assert row["id"] == "u1"
    assert row["enrolled_at"] == "2026-10-28T12:00:00Z"


def test_list_enrolled_users_skips_a_missing_user(system_store):
    """The SQL JOIN dropped such a row; so does this."""
    system_store.insert_course(course())
    system_store.insert_enrollment(COURSE_ID, "ghost", ISO)
    assert system_store.list_enrolled_users(COURSE_ID) == []


def test_list_courses_for_user(system_store):
    system_store.insert_user(user())
    system_store.insert_course(course())
    system_store.insert_course(course(id="other"))
    system_store.insert_enrollment(COURSE_ID, "u1", ISO)
    assert [c["id"] for c in system_store.list_courses_for_user("u1")] == [COURSE_ID]
    assert system_store.list_courses_for_user("nobody") == []


def test_unenroll(system_store):
    system_store.insert_user(user())
    system_store.insert_course(course())
    system_store.insert_enrollment(COURSE_ID, "u1", ISO)
    system_store.delete_enrollment(COURSE_ID, "u1")
    assert system_store.is_enrolled(COURSE_ID, "u1") is False


# ── every interaction gets a timestamp ───────────────────────────────────────


def test_an_interaction_without_a_timestamp_still_gets_one(participant_store, db):
    """Logging something with no time at all is worse than an approximate time."""
    item = interaction()
    del item["timestamp"]
    before = now()
    participant_store.insert_interaction(item)

    stored = db["interactions"].find_one({"id": "i1"})
    assert stored["timestamp"] is not None
    assert stored["timestamp"] >= before


def test_an_empty_timestamp_falls_back_rather_than_storing_null(participant_store, db):
    participant_store.insert_interaction(interaction(timestamp=""))
    assert db["interactions"].find_one({"id": "i1"})["timestamp"] is not None


def test_every_row_records_when_the_database_wrote_it(participant_store, db):
    before = now()
    participant_store.insert_interaction(interaction())
    stored = db["interactions"].find_one({"id": "i1"})
    assert stored["logged_at"] is not None
    assert stored["logged_at"] >= before


def test_logged_at_cannot_be_back_dated_by_the_caller(participant_store, db):
    """`timestamp` is the caller's claim; `logged_at` is the database's record."""
    participant_store.insert_interaction(
        interaction(timestamp="2001-01-01T00:00:00Z", logged_at="2001-01-01T00:00:00Z")
    )
    stored = db["interactions"].find_one({"id": "i1"})
    assert stored["timestamp"].year == 2001   # the claim is kept
    assert stored["logged_at"].year > 2001    # the record is not


def test_logged_at_is_not_handed_back_to_the_agent(participant_store):
    """It is not in the SQLite row shape, and the agent reads these keys."""
    participant_store.insert_interaction(interaction())
    row = participant_store.fetch_for_student(STUDENT_ID)[0]
    assert "logged_at" not in row
    assert "course_id" not in row


def test_the_participant_agents_extra_fields_survive_the_write(participant_store, db):
    participant_store.insert_interaction(
        interaction(classification_confidence=0.82, feedback_score="helpful")
    )
    stored = db["interactions"].find_one({"id": "i1"})
    assert stored["classification_confidence"] == 0.82
    assert stored["feedback_score"] == "helpful"


def test_a_timestamp_survives_the_round_trip_unchanged(participant_store, db):
    """Sub-millisecond precision is dropped on the way in, not on the way out."""
    moment = datetime(2026, 10, 30, 12, 0, 0, 123456, tzinfo=timezone.utc)
    participant_store.insert_interaction(interaction(timestamp=moment))

    stored = db["interactions"].find_one({"id": "i1"})
    assert stored["timestamp"] == datetime(
        2026, 10, 30, 12, 0, 0, 123000, tzinfo=timezone.utc
    )
    row = participant_store.fetch_for_student(STUDENT_ID)[0]
    assert row["timestamp"] == "2026-10-30T12:00:00.123000Z"


def test_the_database_itself_refuses_an_interaction_with_no_logged_at(db):
    """Not just a store convention: the validator requires it.

    An agent that inserts straight into the collection, bypassing the store,
    is rejected rather than silently writing an untimestamped row.
    """
    from .factories import interaction

    item = interaction()
    del item["logged_at"]
    with pytest.raises(Exception) as caught:
        db["interactions"].insert_one(item)
    assert "logged_at" in str(caught.value)
