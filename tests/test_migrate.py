"""The SQLite -> MongoDB migration.

The conversions are where a migration quietly corrupts data, so each one is
tested on its own, and then the whole thing runs against a SQLite file built
to look like ui-student's.
"""

from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from aied_db import Role, guarded

ROOT = Path(__file__).resolve().parent.parent


def _load_script():
    """`scripts/` is not a package, so load the module by path."""
    path = ROOT / "scripts" / "migrate_sqlite.py"
    spec = importlib.util.spec_from_file_location("migrate_sqlite", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate = _load_script()


# ── the conversions ──────────────────────────────────────────────────────────


def test_an_iso_string_becomes_a_utc_datetime():
    moment = migrate.to_date("2026-06-13T00:28:34.378621+00:00")
    assert moment == datetime(2026, 6, 13, 0, 28, 34, 378000, tzinfo=timezone.utc)


def test_a_date_is_truncated_to_milliseconds_because_bson_holds_no_more():
    moment = migrate.to_date("2026-06-13T00:28:34.378621+00:00")
    assert moment.microsecond == 378000


def test_a_trailing_z_is_accepted():
    assert migrate.to_date("2026-06-13T00:28:34Z") == datetime(
        2026, 6, 13, 0, 28, 34, tzinfo=timezone.utc
    )


def test_a_naive_timestamp_is_assumed_to_be_utc():
    """SQLite has no timezones; ui-student writes UTC."""
    assert migrate.to_date("2026-06-13T00:28:34").tzinfo == timezone.utc


def test_a_non_utc_offset_is_converted_rather_than_relabelled():
    assert migrate.to_date("2026-06-13T02:28:34+02:00") == datetime(
        2026, 6, 13, 0, 28, 34, tzinfo=timezone.utc
    )


def test_an_empty_or_missing_timestamp_becomes_none():
    assert migrate.to_date(None) is None
    assert migrate.to_date("") is None


def test_sqlite_integers_become_real_booleans():
    assert migrate.to_bool(1) is True
    assert migrate.to_bool(0) is False


def test_concept_tags_are_parsed_out_of_their_json_string():
    assert migrate.to_tags('["Java", "Comparator"]') == ["Java", "Comparator"]
    assert migrate.to_tags("[]") == []
    assert migrate.to_tags(None) == []


def test_every_planned_table_targets_a_real_collection():
    from aied_db.schema import COLLECTIONS

    for table, (_, collection_name, _) in migrate.PLAN.items():
        assert collection_name in COLLECTIONS, table


def test_the_insert_order_covers_every_planned_table():
    assert set(migrate.ORDER) == set(migrate.PLAN)


# ── end to end ───────────────────────────────────────────────────────────────

# Deliberately fake. Do not paste identifiers out of the real database into
# tests: this repo is public, and a student_id is pseudonymous, not anonymous.
COURSE = "00000000-0000-4000-8000-000000000001"
STUDENT = "00000000-0000-4000-8000-000000000002"
SESSION = "00000000-0000-4000-8000-000000000003"


@pytest.fixture
def course_dir(tmp_path):
    """A folder shaped like ui-student's data/courses/<course_id>/."""
    folder = tmp_path / COURSE
    folder.mkdir()

    participant = sqlite3.connect(folder / "participant.db")
    participant.executescript(
        """
        CREATE TABLE interactions (
            id TEXT PRIMARY KEY, student_id TEXT NOT NULL,
            session_id TEXT NOT NULL, timestamp TEXT NOT NULL,
            message TEXT NOT NULL, question_type TEXT NOT NULL,
            hint_level INTEGER NOT NULL, difficulty TEXT NOT NULL,
            response_time_ms INTEGER NOT NULL DEFAULT 0,
            lab_id TEXT NOT NULL
        );
        """
    )
    participant.execute(
        "INSERT INTO interactions VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("i1", STUDENT, SESSION, "2026-06-13T00:28:34.378621+00:00",
         "why is my loop off by one", "concept", 1, "low", 12493, "default-lab"),
    )
    participant.commit()
    participant.close()

    guardian = sqlite3.connect(folder / "guardian.db")
    guardian.executescript(
        """
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY, student_id TEXT NOT NULL,
            lab_id TEXT NOT NULL, course_id TEXT NOT NULL,
            started_at TEXT NOT NULL, ended_at TEXT,
            question_count INTEGER NOT NULL DEFAULT 0,
            violation_count INTEGER NOT NULL DEFAULT 0,
            escalated INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'active',
            report_generated INTEGER NOT NULL DEFAULT 0,
            report_id TEXT
        );
        CREATE TABLE questions (
            question_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            student_id TEXT NOT NULL, sequence_number INTEGER NOT NULL,
            timestamp TEXT NOT NULL, text TEXT NOT NULL,
            classification TEXT NOT NULL,
            violation INTEGER NOT NULL DEFAULT 0, violation_type TEXT,
            concept_tags TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE violations (
            violation_id TEXT PRIMARY KEY, question_id TEXT NOT NULL,
            session_id TEXT NOT NULL, student_id TEXT NOT NULL,
            sequence_number INTEGER NOT NULL, timestamp TEXT NOT NULL,
            violation_type TEXT NOT NULL, severity TEXT NOT NULL,
            question_text TEXT NOT NULL
        );
        CREATE TABLE verifications (
            verification_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
            student_id TEXT NOT NULL, question_id TEXT,
            timestamp TEXT NOT NULL, passes INTEGER NOT NULL, reason TEXT,
            guidance_level TEXT NOT NULL DEFAULT 'FULL',
            draft_excerpt TEXT NOT NULL DEFAULT ''
        );
        """
    )
    guardian.execute(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (SESSION, STUDENT, "default-lab", COURSE,
         "2026-06-13T00:28:20.789000+00:00", None, 1, 0, 0, "active", 0, None),
    )
    guardian.execute(
        "INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("q1", SESSION, STUDENT, 1, "2026-06-13T00:28:23.426000+00:00",
         "why is my loop off by one", "concept", 0, None, '["Java", "loops"]'),
    )
    guardian.execute(
        "INSERT INTO verifications VALUES (?,?,?,?,?,?,?,?,?)",
        ("v1", SESSION, STUDENT, None, "2026-06-13T00:28:33.283000+00:00",
         1, None, "FULL", ""),
    )
    guardian.commit()
    guardian.close()
    return folder


def test_reading_a_course_converts_every_row(course_dir):
    sessions = migrate.read_table(course_dir / "guardian.db", "sessions", COURSE)
    assert len(sessions) == 1
    assert isinstance(sessions[0]["started_at"], datetime)
    assert sessions[0]["escalated"] is False
    assert sessions[0]["report_generated"] is False

    questions = migrate.read_table(course_dir / "guardian.db", "questions", COURSE)
    assert questions[0]["concept_tags"] == ["Java", "loops"]
    assert questions[0]["violation"] is False


def test_the_course_id_is_stamped_on_a_table_that_has_no_such_column(course_dir):
    """This is what replaces the per-course SQLite file."""
    rows = migrate.read_table(course_dir / "participant.db", "interactions", COURSE)
    assert rows[0]["course_id"] == COURSE


def test_a_row_that_names_its_own_course_keeps_it(course_dir):
    """A mislabelled folder must not silently relabel real data."""
    rows = migrate.read_table(course_dir / "guardian.db", "sessions", "wrong-course")
    assert rows[0]["course_id"] == COURSE


def test_every_converted_row_satisfies_its_validator(course_dir, db):
    """The conversions are only right if MongoDB accepts the result."""
    from aied_db.schema import validator_for

    from .jsonschema_check import assert_valid

    for table in migrate.ORDER:
        source, collection_name, _ = migrate.PLAN[table]
        path = course_dir / f"{source}.db"
        for document in migrate.read_table(path, table, COURSE):
            assert_valid(
                document,
                validator_for(collection_name)["$jsonSchema"],
                collection=collection_name,
            )


def test_migrated_rows_read_back_through_ui_students_own_protocol(course_dir, db):
    """The point of the whole exercise: the agent sees what it used to see."""
    from aied_db.stores import MongoGuardianStore, MongoParticipantStore

    migration = guarded(db, Role.MIGRATION, actor_id="test")
    for table in migrate.ORDER:
        source, collection_name, _ = migrate.PLAN[table]
        documents = migrate.read_table(course_dir / f"{source}.db", table, COURSE)
        if documents:
            migration[collection_name].insert_many(documents)

    guardian_db = guarded(db, Role.GUARDIAN_AGENT, actor_id="guardian_agent")
    session = MongoGuardianStore(guardian_db, course_id=COURSE).get_session(
        SESSION, STUDENT
    )
    assert session["started_at"] == "2026-06-13T00:28:20.789000Z"
    assert session["escalated"] is False
    assert len(session["questions"]) == 1
    assert session["questions"][0]["concept_tags"] == ["Java", "loops"]
    assert "course_id" not in session["questions"][0]

    participant_db = guarded(db, Role.PARTICIPANT_AGENT, actor_id="participant_agent")
    rows = MongoParticipantStore(participant_db, course_id=COURSE).fetch_for_student(
        STUDENT
    )
    assert len(rows) == 1
    assert rows[0]["timestamp"] == "2026-06-13T00:28:34.378000Z"


def test_the_migration_role_cannot_overwrite_what_is_already_there(course_dir, db):
    """Why re-running is safe: MIGRATION holds insert and find, nothing else."""
    from aied_db.access import PermissionError_

    migration = guarded(db, Role.MIGRATION, actor_id="test")
    rows = migrate.read_table(course_dir / "guardian.db", "sessions", COURSE)
    migration["guardian_sessions"].insert_many(rows)

    with pytest.raises(PermissionError_):
        migration["guardian_sessions"].update_one(
            {"session_id": SESSION}, {"$set": {"status": "closed"}}
        )
    with pytest.raises(PermissionError_):
        migration["guardian_sessions"].delete_one({"session_id": SESSION})
