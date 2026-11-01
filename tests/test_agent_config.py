"""The settings cascade: defaults -> course -> user, and what may override what."""

from __future__ import annotations

import json

import pytest

from aied_db import Role, guarded
from aied_db.access import PermissionError_
from aied_db.agent_config import (
    AgentConfig,
    ConfigError,
    defaults_path,
    load_defaults,
    seed_defaults,
)

from .factories import COURSE_ID, INSTRUCTOR_ID, STUDENT_ID

AGENT = "guardian"
KEY = "escalation.violation_threshold"


@pytest.fixture
def seeded(admin_db):
    seed_defaults(admin_db, load_defaults())
    return admin_db


@pytest.fixture
def instructor_db(db):
    return guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID)


@pytest.fixture
def student_db(db):
    return guarded(db, Role.STUDENT, actor_id=STUDENT_ID)


# ── The files on disk ────────────────────────────────────────────────────────


def test_defaults_file_is_read_only():
    """defaults.json is factory settings: mode 444, changed through git."""
    path = defaults_path()
    mode = path.stat().st_mode & 0o222
    assert mode == 0, f"{path.name} is writable (mode {oct(path.stat().st_mode)})"


def test_defaults_file_parses_and_every_agent_declares_its_settings():
    data = json.loads(defaults_path().read_text())
    assert data["agents"]
    seen = set()
    for entry in data["agents"]:
        agent = entry["agent"]
        assert agent
        assert agent not in seen, f"{agent} appears twice"
        seen.add(agent)
        assert entry["settings"], agent
        for setting in entry["settings"]:
            assert setting["key"]
            assert setting["type"]
            assert "value" in setting
            assert setting.get("editable_by") is not None, f"{agent}:{setting['key']}"


def test_every_setting_key_is_unique_within_its_agent():
    """One file now holds all of them, so a duplicated key is easier to miss."""
    documents = load_defaults()
    pairs = [(d["agent"], d["key"]) for d in documents]
    assert len(pairs) == len(set(pairs))


def test_loader_records_the_source_file():
    documents = load_defaults()
    assert documents
    assert all(d["source_file"].endswith(".json") for d in documents)


def test_loader_rejects_a_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_defaults(tmp_path / "nope.json")


def test_assessment_weights_sum_to_one_in_the_shipped_defaults():
    """The Oct 26 rule, checked against the files rather than at runtime only."""
    documents = {d["key"]: d["value"] for d in load_defaults() if d["agent"] == "assessment"}
    total = (
        documents["grading.code_weight"]
        + documents["grading.report_weight"]
        + documents["grading.manual_weight"]
    )
    assert total == pytest.approx(1.0)


# ── Seeding ──────────────────────────────────────────────────────────────────


def test_seeding_is_idempotent(admin_db):
    documents = load_defaults()
    first = seed_defaults(admin_db, documents)
    second = seed_defaults(admin_db, documents)
    assert first["inserted"] == len(documents)
    assert second["inserted"] == 0
    assert second["updated"] == len(documents)


def test_only_admin_may_seed(instructor_db):
    with pytest.raises(PermissionError_):
        seed_defaults(instructor_db, load_defaults())


# ── Resolution ───────────────────────────────────────────────────────────────


def test_default_wins_when_there_is_no_override(seeded):
    config = AgentConfig(seeded, AGENT, course_id=COURSE_ID)
    assert config.get(KEY) == 3
    assert config.resolve()[KEY].source == "default"


def test_course_override_beats_the_default(seeded, instructor_db):
    AgentConfig(instructor_db, AGENT, course_id=COURSE_ID).set_course_override(
        KEY, 5, set_by=INSTRUCTOR_ID, role="instructor"
    )
    config = AgentConfig(seeded, AGENT, course_id=COURSE_ID)
    assert config.get(KEY) == 5
    assert config.resolve()[KEY].source == "course"


def test_user_override_beats_the_course(seeded, instructor_db, admin_db):
    AgentConfig(instructor_db, AGENT, course_id=COURSE_ID).set_course_override(
        KEY, 5, set_by=INSTRUCTOR_ID, role="instructor"
    )
    AgentConfig(admin_db, AGENT, course_id=COURSE_ID, user_id=STUDENT_ID).set_user_override(
        KEY, 7, set_by=STUDENT_ID, role="admin"
    )
    after_login = AgentConfig(seeded, AGENT, course_id=COURSE_ID, user_id=STUDENT_ID)
    assert after_login.get(KEY) == 7
    assert after_login.resolve()[KEY].source == "user"


def test_before_login_only_defaults_and_course_apply(seeded, instructor_db, admin_db):
    """"Before login: defaults + course. After login: + user.\""""
    AgentConfig(instructor_db, AGENT, course_id=COURSE_ID).set_course_override(
        KEY, 5, set_by=INSTRUCTOR_ID, role="instructor"
    )
    AgentConfig(admin_db, AGENT, course_id=COURSE_ID, user_id=STUDENT_ID).set_user_override(
        KEY, 7, set_by=STUDENT_ID, role="admin"
    )
    before_login = AgentConfig(seeded, AGENT, course_id=COURSE_ID)
    assert before_login.get(KEY) == 5


def test_one_courses_override_does_not_leak_into_another(seeded, instructor_db):
    AgentConfig(instructor_db, AGENT, course_id=COURSE_ID).set_course_override(
        KEY, 5, set_by=INSTRUCTOR_ID, role="instructor"
    )
    other = AgentConfig(seeded, AGENT, course_id="course-other")
    assert other.get(KEY) == 3


def test_resetting_an_override_updates_it_in_place(seeded, instructor_db, db):
    config = AgentConfig(instructor_db, AGENT, course_id=COURSE_ID)
    config.set_course_override(KEY, 5, set_by=INSTRUCTOR_ID, role="instructor")
    config.set_course_override(KEY, 9, set_by=INSTRUCTOR_ID, role="instructor")
    assert db["agent_configs"].count_documents({"key": KEY}) == 1
    assert AgentConfig(seeded, AGENT, course_id=COURSE_ID).get(KEY) == 9


def test_all_returns_every_setting(seeded):
    settings = AgentConfig(seeded, AGENT, course_id=COURSE_ID).all()
    assert settings[KEY] == 3
    assert "guidance.default_level" in settings


def test_unknown_key_returns_the_fallback(seeded):
    assert AgentConfig(seeded, AGENT, course_id=COURSE_ID).get("nope", "fallback") == "fallback"


# ── Validation ───────────────────────────────────────────────────────────────


def test_unknown_setting_is_refused(seeded, instructor_db):
    with pytest.raises(ConfigError, match="not a known setting"):
        AgentConfig(instructor_db, AGENT, course_id=COURSE_ID).set_course_override(
            "made.up", 1, set_by=INSTRUCTOR_ID, role="instructor"
        )


def test_editable_by_is_enforced(seeded, student_db):
    """No setting is student-editable yet, so a student override is refused."""
    with pytest.raises(ConfigError, match="editable by"):
        AgentConfig(student_db, AGENT, course_id=COURSE_ID, user_id=STUDENT_ID).set_user_override(
            KEY, 5, set_by=STUDENT_ID, role="student"
        )


def test_no_setting_is_student_editable_yet():
    """An open question for the team: which student preferences belong here?"""
    for document in load_defaults():
        assert "student" not in document["editable_by"], document["key"]


def test_type_is_checked(seeded, instructor_db):
    config = AgentConfig(instructor_db, AGENT, course_id=COURSE_ID)
    with pytest.raises(ConfigError, match="expects int"):
        config.set_course_override(KEY, "five", set_by=INSTRUCTOR_ID, role="instructor")


def test_a_bool_is_not_an_int(seeded, instructor_db):
    """bool subclasses int in Python; the checker keeps them apart."""
    config = AgentConfig(instructor_db, AGENT, course_id=COURSE_ID)
    with pytest.raises(ConfigError, match="got bool"):
        config.set_course_override(KEY, True, set_by=INSTRUCTOR_ID, role="instructor")


def test_minimum_and_maximum_are_checked(seeded, instructor_db):
    config = AgentConfig(instructor_db, AGENT, course_id=COURSE_ID)
    with pytest.raises(ConfigError, match=">= 1"):
        config.set_course_override(KEY, 0, set_by=INSTRUCTOR_ID, role="instructor")
    with pytest.raises(ConfigError, match="<= 20"):
        config.set_course_override(KEY, 99, set_by=INSTRUCTOR_ID, role="instructor")


def test_choices_are_checked(seeded, instructor_db):
    config = AgentConfig(instructor_db, AGENT, course_id=COURSE_ID)
    with pytest.raises(ConfigError, match="must be one of"):
        config.set_course_override(
            "guidance.default_level", "VERBOSE", set_by=INSTRUCTOR_ID, role="instructor"
        )
    config.set_course_override(
        "guidance.default_level", "MINIMAL", set_by=INSTRUCTOR_ID, role="instructor"
    )


def test_an_override_needs_the_scope_it_is_setting(seeded, instructor_db):
    with pytest.raises(ConfigError, match="needs course_id"):
        AgentConfig(instructor_db, AGENT).set_course_override(
            KEY, 5, set_by=INSTRUCTOR_ID, role="instructor"
        )
    with pytest.raises(ConfigError, match="needs user_id"):
        AgentConfig(instructor_db, AGENT, course_id=COURSE_ID).set_user_override(
            KEY, 5, set_by=INSTRUCTOR_ID, role="instructor"
        )


# ── Grading weights ──────────────────────────────────────────────────────────


def test_a_weight_change_that_breaks_the_sum_is_refused(seeded, instructor_db):
    config = AgentConfig(instructor_db, "assessment", course_id=COURSE_ID)
    with pytest.raises(ConfigError, match="must sum to 1.0"):
        config.set_course_override(
            "grading.code_weight", 0.9, set_by=INSTRUCTOR_ID, role="instructor"
        )


def test_weights_that_still_sum_to_one_are_accepted(seeded, instructor_db):
    config = AgentConfig(instructor_db, "assessment", course_id=COURSE_ID)
    config.check_weights({"grading.code_weight": 0.5, "grading.report_weight": 0.4})
    config.set_course_override(
        "grading.manual_weight", 0.1, set_by=INSTRUCTOR_ID, role="instructor"
    )


def test_weight_check_is_a_no_op_for_an_agent_without_weights(seeded):
    AgentConfig(seeded, AGENT, course_id=COURSE_ID).check_weights()


def test_weight_check_skips_an_unseeded_group(db):
    """Nothing to check before the defaults are seeded."""
    AgentConfig(db, "assessment", course_id=COURSE_ID).check_weights()


# ── Nothing reads this yet ───────────────────────────────────────────────────


def test_agents_still_use_hard_coded_values():
    """A reminder, not a requirement.

    As of v0.1.0 no agent calls AgentConfig.get(): they all still read their
    own hard-coded constants. Wiring each one up is a separate task in its own
    repo. This test documents the state so it is not mistaken for done.
    """
    assert True
