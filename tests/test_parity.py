"""The Mongo stores against ui-student's REAL SQLite stores, side by side.

These are the tests that decide whether the swap is safe. Each one drives the
same sequence of calls through both backends and compares what comes back,
key by key. A difference here means ui-student's agents would behave
differently after the migration.

They need a `ui-student` checkout NEXT TO this repo:

    cd .. && gh repo clone AIEd-Innovations/ui-student

Without it they skip, so `pytest -q` still passes on a bare checkout — check
the skip count before trusting a green run.
"""

from __future__ import annotations

import sys

import pytest

from aied_db import Role, guarded
from aied_db.stores import MongoGuardianStore, MongoParticipantStore, MongoSystemStore

COURSE_ID = "course-csc580"
STUDENT_ID = "student-001"
ISO = "2026-10-28T12:00:00Z"
LATER = "2026-10-28T14:30:00Z"


@pytest.fixture
def sqlite_stores(ui_student_repo, tmp_path):
    """ui-student's own store classes, imported from the sibling checkout."""
    if ui_student_repo is None:
        pytest.skip("clone ui-student next to this repo to run the parity tests")
    if ui_student_repo not in sys.path:
        sys.path.insert(0, ui_student_repo)
    try:
        from agentic_system.store.sqlite import SqliteGuardianStore, SqliteParticipantStore
        from agentic_system.store.system import SqliteSystemStore
    except ImportError as exc:  # pragma: no cover - depends on the checkout
        pytest.skip(f"cannot import ui-student's stores: {exc}")

    participant = SqliteParticipantStore(tmp_path / "participant.db")
    guardian = SqliteGuardianStore(tmp_path / "guardian.db")
    system = SqliteSystemStore(tmp_path / "system.db")
    for store in (participant, guardian, system):
        store.init()
    yield {"participant": participant, "guardian": guardian, "system": system}
    for store in (participant, guardian, system):
        store.close()


@pytest.fixture
def mongo_stores(db):
    return {
        "participant": MongoParticipantStore(
            guarded(db, Role.PARTICIPANT_AGENT, actor_id="participant_agent"), course_id=COURSE_ID
        ),
        "guardian": MongoGuardianStore(
            guarded(db, Role.GUARDIAN_AGENT, actor_id="guardian_agent"), course_id=COURSE_ID
        ),
        "system": MongoSystemStore(guarded(db, Role.ADMIN, actor_id="admin")),
    }


def interaction(**overrides):
    return {
        "id": "i1",
        "student_id": STUDENT_ID,
        "session_id": "s1",
        "timestamp": ISO,
        "message": "How do I start?",
        "question_type": "conceptual",
        "hint_level": 2,
        "difficulty": "basic",
        "response_time_ms": 150,
        "lab_id": "lab-01",
        **overrides,
    }


def session(**overrides):
    return {
        "session_id": "s1",
        "student_id": STUDENT_ID,
        "lab_id": "lab-01",
        "course_id": COURSE_ID,
        "started_at": ISO,
        **overrides,
    }


def question(**overrides):
    return {
        "question_id": "q1",
        "sequence_number": 1,
        "timestamp": ISO,
        "text": "What is a linked list?",
        "classification": "conceptual",
        "violation": False,
        "violation_type": None,
        "concept_tags": ["linked-list", "pointers"],
        **overrides,
    }


def violation(**overrides):
    return {
        "violation_id": "v1",
        "question_id": "q1",
        "sequence_number": 2,
        "timestamp": ISO,
        "violation_type": "requested_solution",
        "severity": "high",
        "question_text": "Just write it for me",
        **overrides,
    }


# ── 1-2: participant ─────────────────────────────────────────────────────────


def test_parity_interaction_row_is_identical(sqlite_stores, mongo_stores):
    item = interaction()
    sqlite_stores["participant"].insert_interaction(dict(item))
    mongo_stores["participant"].insert_interaction(dict(item))

    from_sqlite = sqlite_stores["participant"].fetch_for_student(STUDENT_ID)
    from_mongo = mongo_stores["participant"].fetch_for_student(STUDENT_ID)
    assert from_sqlite == from_mongo


def test_parity_interactions_are_ordered_the_same(sqlite_stores, mongo_stores):
    items = [
        interaction(id="i2", timestamp="2026-10-28T13:00:00Z"),
        interaction(id="i1", timestamp="2026-10-28T11:00:00Z"),
        interaction(id="i3", timestamp="2026-10-28T15:00:00Z"),
    ]
    for item in items:
        sqlite_stores["participant"].insert_interaction(dict(item))
        mongo_stores["participant"].insert_interaction(dict(item))

    assert sqlite_stores["participant"].fetch_for_student(STUDENT_ID) == mongo_stores[
        "participant"
    ].fetch_for_student(STUDENT_ID)


# ── 3-7: guardian ────────────────────────────────────────────────────────────


def test_parity_new_session_is_identical(sqlite_stores, mongo_stores):
    sqlite_stores["guardian"].create_session(session())
    mongo_stores["guardian"].create_session(session())
    assert sqlite_stores["guardian"].get_session("s1", STUDENT_ID) == mongo_stores[
        "guardian"
    ].get_session("s1", STUDENT_ID)


def test_parity_duplicate_session_raises_the_same_way(sqlite_stores, mongo_stores):
    """The agent checks `"IntegrityError" in type(e).__name__` for both."""
    sqlite_stores["guardian"].create_session(session())
    mongo_stores["guardian"].create_session(session())

    with pytest.raises(Exception) as sqlite_error:
        sqlite_stores["guardian"].create_session(session())
    with pytest.raises(Exception) as mongo_error:
        mongo_stores["guardian"].create_session(session())

    assert "IntegrityError" in type(sqlite_error.value).__name__
    assert "IntegrityError" in type(mongo_error.value).__name__


def test_parity_session_with_questions_and_violations(sqlite_stores, mongo_stores):
    for store in (sqlite_stores["guardian"], mongo_stores["guardian"]):
        store.create_session(session())
        store.insert_question("s1", STUDENT_ID, question())
        store.insert_question("s1", STUDENT_ID, question(question_id="q2", sequence_number=2, violation=True, violation_type="requested_solution"))
        store.insert_violation("s1", STUDENT_ID, violation())
        store.update_session_counters(
            "s1", STUDENT_ID, question_count=2, violation_count=1, escalated=True
        )

    assert sqlite_stores["guardian"].get_session("s1", STUDENT_ID) == mongo_stores[
        "guardian"
    ].get_session("s1", STUDENT_ID)


def test_parity_closed_session(sqlite_stores, mongo_stores):
    for store in (sqlite_stores["guardian"], mongo_stores["guardian"]):
        store.create_session(session())
        store.close_session("s1", STUDENT_ID, LATER, "report-1")

    assert sqlite_stores["guardian"].get_session("s1", STUDENT_ID) == mongo_stores[
        "guardian"
    ].get_session("s1", STUDENT_ID)


def test_parity_missing_session_is_none_in_both(sqlite_stores, mongo_stores):
    assert sqlite_stores["guardian"].get_session("nope", STUDENT_ID) is None
    assert mongo_stores["guardian"].get_session("nope", STUDENT_ID) is None


def test_parity_verification_defaults(sqlite_stores, mongo_stores, raw_db, tmp_path):
    """`insert_verification` has three defaults; both must apply them alike."""
    import sqlite3

    record = {"verification_id": "ver1", "timestamp": ISO, "passes": True}
    for store in (sqlite_stores["guardian"], mongo_stores["guardian"]):
        store.create_session(session())
        store.insert_verification("s1", STUDENT_ID, dict(record))

    connection = sqlite3.connect(tmp_path / "guardian.db")
    connection.row_factory = sqlite3.Row
    from_sqlite = dict(connection.execute("SELECT * FROM verifications").fetchone())
    connection.close()

    from_mongo = raw_db["guardian_verifications"].find_one({"verification_id": "ver1"})
    assert from_sqlite["guidance_level"] == from_mongo["guidance_level"] == "FULL"
    assert from_sqlite["draft_excerpt"] == from_mongo["draft_excerpt"] == ""
    assert from_sqlite["question_id"] is None and from_mongo["question_id"] is None
    assert bool(from_sqlite["passes"]) is from_mongo["passes"] is True


# ── 8-9: system ──────────────────────────────────────────────────────────────


def user(**overrides):
    return {
        "id": "u1",
        "email": "student@calpoly.edu",
        "name": "Test Student",
        "sso_subject": "sso-1",
        "role": "student",
        "created_at": ISO,
        **overrides,
    }


INSTRUCTOR_ID = "instructor-001"


def instructor(**overrides):
    return user(
        id=INSTRUCTOR_ID,
        email="instructor@calpoly.edu",
        name="Test Instructor",
        sso_subject="sso-instructor",
        role="instructor",
        **overrides,
    )


def course(**overrides):
    return {
        "id": COURSE_ID,
        "name": "CSC 580",
        "instructor_id": INSTRUCTOR_ID,
        "blob_container_name": "csc580",
        "search_index_name": "csc580-idx",
        "search_indexer_name": "csc580-indexer",
        "search_datasource_name": "csc580-ds",
        "created_at": ISO,
        **overrides,
    }


def test_parity_user_row_is_identical(sqlite_stores, mongo_stores):
    sqlite_stores["system"].insert_user(user())
    mongo_stores["system"].insert_user(user())
    assert sqlite_stores["system"].get_user("u1") == mongo_stores["system"].get_user("u1")
    assert sqlite_stores["system"].list_users() == mongo_stores["system"].list_users()


def test_parity_enrollment_listing_is_identical(sqlite_stores, mongo_stores):
    for store in (sqlite_stores["system"], mongo_stores["system"]):
        store.insert_user(instructor())
        store.insert_user(user())
        store.insert_course(course())
        store.insert_enrollment(COURSE_ID, "u1", ISO)
        store.insert_enrollment(COURSE_ID, "u1", ISO)  # INSERT OR IGNORE

    assert sqlite_stores["system"].list_enrolled_users(COURSE_ID) == mongo_stores[
        "system"
    ].list_enrolled_users(COURSE_ID)
    assert sqlite_stores["system"].is_enrolled(COURSE_ID, "u1") == mongo_stores[
        "system"
    ].is_enrolled(COURSE_ID, "u1")


def test_parity_course_row_differs_only_by_the_legacy_db_paths(sqlite_stores, mongo_stores):
    """The two *_db_path columns are gone on purpose; nothing else may differ."""
    for store in (sqlite_stores["system"], mongo_stores["system"]):
        store.insert_user(instructor())
        store.insert_course(course())

    from_sqlite = sqlite_stores["system"].get_course(COURSE_ID)
    from_mongo = mongo_stores["system"].get_course(COURSE_ID)

    assert set(from_sqlite) - set(from_mongo) == {"participant_db_path", "guardian_db_path"}
    assert set(from_mongo) - set(from_sqlite) == set()
    for field in from_mongo:
        assert from_mongo[field] == from_sqlite[field], field


def test_parity_courses_for_user(sqlite_stores, mongo_stores):
    for store in (sqlite_stores["system"], mongo_stores["system"]):
        store.insert_user(instructor())
        store.insert_user(user())
        store.insert_course(course())
        store.insert_course(course(id="course-other", name="CSC 581"))
        store.insert_enrollment(COURSE_ID, "u1", ISO)

    from_sqlite = sqlite_stores["system"].list_courses_for_user("u1")
    from_mongo = mongo_stores["system"].list_courses_for_user("u1")
    assert [c["id"] for c in from_sqlite] == [c["id"] for c in from_mongo]


# ── The protocols themselves ─────────────────────────────────────────────────


def test_mongo_stores_satisfy_ui_students_protocols(ui_student_repo, mongo_stores):
    """`runtime_checkable` Protocols, checked against ui-student's own copies."""
    if ui_student_repo is None:
        pytest.skip("clone ui-student next to this repo to run the parity tests")
    if ui_student_repo not in sys.path:
        sys.path.insert(0, ui_student_repo)
    try:
        from agentic_system.store.base import GuardianStore, ParticipantStore, SystemStore
    except ImportError as exc:  # pragma: no cover - depends on the checkout
        pytest.skip(f"cannot import ui-student's protocols: {exc}")

    assert isinstance(mongo_stores["participant"], ParticipantStore)
    assert isinstance(mongo_stores["guardian"], GuardianStore)
    assert isinstance(mongo_stores["system"], SystemStore)


# ── A difference the migration has to account for ────────────────────────────


def test_sqlite_enforced_foreign_keys_and_mongo_does_not(sqlite_stores, mongo_stores):
    """SQLite refused a course whose instructor did not exist. Mongo accepts it.

    `SqliteSystemStore` opens every connection with `PRAGMA foreign_keys = ON`
    and declares `courses.instructor_id REFERENCES users(id)`, plus
    `ON DELETE CASCADE` on both enrollment columns. MongoDB has no equivalent,
    and a `$jsonSchema` validator cannot express one.

    This is NOT a parity failure to fix in the stores — it is a real property
    the database loses, and it needs a decision:
      - validate the reference in the service layer before inserting, or
      - accept orphans and make the reads tolerate them (which
        `list_enrolled_users` already does: it skips a missing user, exactly
        as the SQL JOIN did), or
      - run a periodic integrity check.

    Until then, deleting a user or course no longer cascades: enrollments that
    referenced it are left behind. Flagged for the team.
    """
    with pytest.raises(Exception) as sqlite_error:
        sqlite_stores["system"].insert_course(course())
    assert "FOREIGN KEY" in str(sqlite_error.value).upper()

    # Mongo takes it without complaint.
    mongo_stores["system"].insert_course(course())
    assert mongo_stores["system"].get_course(COURSE_ID)["instructor_id"] == INSTRUCTOR_ID


def test_deleting_a_course_no_longer_cascades(mongo_stores):
    """SQLite had ON DELETE CASCADE on enrollments; Mongo leaves them."""
    mongo_stores["system"].insert_user(instructor())
    mongo_stores["system"].insert_user(user())
    mongo_stores["system"].insert_course(course())
    mongo_stores["system"].insert_enrollment(COURSE_ID, "u1", ISO)

    mongo_stores["system"].delete_course(COURSE_ID)

    # The enrollment survives its course. The read tolerates it, but the row is orphaned.
    assert mongo_stores["system"].is_enrolled(COURSE_ID, "u1") is True
    assert mongo_stores["system"].list_courses_for_user("u1") == []
