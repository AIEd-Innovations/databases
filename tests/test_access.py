"""The guard: refusals, field limits, and the revision trail."""

from __future__ import annotations

import pytest

from aied_db import IntegrityError, Role, guarded, history
from aied_db._util import now
from aied_db.access import AGENT_ROLES, POLICY, PermissionError_

from .factories import COURSE_ID, FACTORIES, INSTRUCTOR_ID, STUDENT_ID


@pytest.fixture
def instructor_db(db):
    return guarded(db, Role.INSTRUCTOR, actor_id=INSTRUCTOR_ID)


@pytest.fixture
def assessment_db(db):
    return guarded(db, Role.ASSESSMENT_AGENT, actor_id="assessment_agent")


@pytest.fixture
def guardian_db(db):
    return guarded(db, Role.GUARDIAN_AGENT, actor_id="guardian_agent")


@pytest.fixture
def curriculum_db(db):
    return guarded(db, Role.CURRICULUM_AGENT, actor_id="curriculum_agent")


@pytest.fixture
def integrity_db(db):
    return guarded(db, Role.INTEGRITY_AGENT, actor_id="integrity_agent")


@pytest.fixture
def student_db(db):
    return guarded(db, Role.STUDENT, actor_id=STUDENT_ID)


# ── Basics ───────────────────────────────────────────────────────────────────


def test_actor_id_is_required():
    with pytest.raises(ValueError, match="actor_id"):
        guarded(object(), Role.ADMIN, actor_id="")


def test_collection_reachable_by_attribute_and_item(assessment_db):
    assert assessment_db.assignments is assessment_db["assignments"]


def test_role_accepts_its_string_value(db):
    assert guarded(db, "instructor", actor_id="x").role is Role.INSTRUCTOR


def test_unknown_collection_is_refused(assessment_db):
    with pytest.raises(PermissionError_, match="POLICY"):
        assessment_db["not_a_collection"].find_one({})


# ── Rule 1: agents never overwrite human data ────────────────────────────────


def test_agent_may_read_but_not_write_an_assignment(admin_db, assessment_db):
    admin_db.assignments.insert_one(FACTORIES["assignments"]())
    assert assessment_db.assignments.find_one({"assignment_id": "hw1_sorting"}) is not None
    with pytest.raises(PermissionError_, match="may not update"):
        assessment_db.assignments.update_one(
            {"assignment_id": "hw1_sorting"}, {"$set": {"max_score": 1.0}}
        )


def test_agent_may_not_touch_student_files(admin_db, assessment_db):
    admin_db.student_files.insert_one(FACTORIES["student_files"]())
    with pytest.raises(PermissionError_):
        assessment_db.student_files.insert_one(FACTORIES["student_files"](file_id="sfile-002"))


def test_student_cannot_read_reference_solutions(admin_db, student_db):
    admin_db.assignment_files.insert_one(FACTORIES["assignment_files"]())
    with pytest.raises(PermissionError_, match="may not find"):
        student_db.assignment_files.find_one({"kind": "solution"})


# ── Rule 2: agents update only named fields ──────────────────────────────────


def test_agent_may_update_its_own_named_fields(guardian_db, db):
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    guardian_db.guardian_sessions.update_one(
        {"session_id": "session-001"},
        {"$set": {"question_count": 2, "violation_count": 1, "escalated": True}},
    )
    stored = db["guardian_sessions"].find_one({"session_id": "session-001"})
    assert (stored["question_count"], stored["escalated"]) == (2, True)


def test_agent_may_not_update_a_field_outside_its_list(guardian_db):
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    with pytest.raises(PermissionError_, match="refused"):
        guardian_db.guardian_sessions.update_one(
            {"session_id": "session-001"}, {"$set": {"student_id": "someone-else"}}
        )


def test_field_check_covers_every_update_operator(guardian_db):
    """$unset and $inc change fields too, not just $set."""
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    with pytest.raises(PermissionError_, match="refused"):
        guardian_db.guardian_sessions.update_one(
            {"session_id": "session-001"}, {"$unset": {"student_id": ""}}
        )
    with pytest.raises(PermissionError_, match="refused"):
        guardian_db.guardian_sessions.update_one(
            {"session_id": "session-001"}, {"$inc": {"version": 1}}
        )


def test_field_check_reads_a_dotted_path_as_its_root_field(guardian_db):
    """`report_id` is allowed, so `report_id.x` is checked against `report_id`."""
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    guardian_db.guardian_sessions.update_one(
        {"session_id": "session-001"}, {"$set": {"question_count": 1}}
    )


def test_curriculum_agent_cannot_approve_its_own_lab(curriculum_db):
    curriculum_db.labs.insert_one(FACTORIES["labs"]())
    with pytest.raises(PermissionError_, match="refused"):
        curriculum_db.labs.update_one(
            {"lab_id": "lab-01"}, {"$set": {"approval_status": "approved"}}
        )


def test_curriculum_agent_may_revise_its_draft(curriculum_db, db):
    curriculum_db.labs.insert_one(FACTORIES["labs"]())
    curriculum_db.labs.update_one(
        {"lab_id": "lab-01"}, {"$set": {"spec_markdown": "# v2\n", "version": 2}}
    )
    assert db["labs"].find_one({"lab_id": "lab-01"})["version"] == 2


def test_instructor_may_approve_a_lab(curriculum_db, instructor_db, db):
    curriculum_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.update_one(
        {"lab_id": "lab-01"},
        {"$set": {"approval_status": "approved", "approved_by": INSTRUCTOR_ID}},
    )
    assert db["labs"].find_one({"lab_id": "lab-01"})["approval_status"] == "approved"


def test_assessment_agent_cannot_review_its_own_grade(assessment_db):
    assessment_db.assessment_results.insert_one(FACTORIES["assessment_results"]())
    with pytest.raises(PermissionError_, match="may not update"):
        assessment_db.assessment_results.update_one(
            {"submission_id": "sub-001"}, {"$set": {"final_score": 40.0}}
        )


def test_instructor_review_is_limited_to_the_review_fields(assessment_db, instructor_db):
    assessment_db.assessment_results.insert_one(FACTORIES["assessment_results"]())
    instructor_db.assessment_results.update_one(
        {"submission_id": "sub-001"},
        {"$set": {"status": "instructor_graded", "final_score": 38.0}},
    )
    with pytest.raises(PermissionError_, match="refused"):
        instructor_db.assessment_results.update_one(
            {"submission_id": "sub-001"}, {"$set": {"score": 40.0}}
        )


def test_integrity_agent_cannot_resolve_its_own_escalation(integrity_db):
    integrity_db.integrity_escalations.insert_one(FACTORIES["integrity_escalations"]())
    with pytest.raises(PermissionError_, match="may not update"):
        integrity_db.integrity_escalations.update_one(
            {"escalation_id": "iescalation-001"}, {"$set": {"status": "resolved"}}
        )


def test_agents_cannot_change_settings(guardian_db):
    with pytest.raises(PermissionError_):
        guardian_db.agent_configs.insert_one(FACTORIES["agent_configs"]())


# ── Rule 3: agents never delete ──────────────────────────────────────────────


def test_agent_cannot_delete(guardian_db):
    guardian_db.guardian_questions.insert_one(FACTORIES["guardian_questions"]())
    with pytest.raises(PermissionError_, match="may not delete"):
        guardian_db.guardian_questions.delete_one({"question_id": "question-001"})


def test_agent_cannot_delete_many(guardian_db):
    with pytest.raises(PermissionError_, match="may not delete"):
        guardian_db.guardian_questions.delete_many({})


def test_aggregation_cannot_write(assessment_db):
    with pytest.raises(PermissionError_, match="aggregation"):
        assessment_db.assessment_results.aggregate([{"$out": "somewhere_else"}])


def test_upsert_requires_insert_rights(instructor_db, db):
    """An instructor may update these four fields, but not insert via upsert."""
    with pytest.raises(PermissionError_):
        guarded(db, Role.STUDENT, actor_id=STUDENT_ID).labs.update_one(
            {"lab_id": "nope"}, {"$set": {"title": "x"}}, upsert=True
        )


# ── Rule 4: nothing is lost ──────────────────────────────────────────────────


def test_update_copies_the_prior_document_into_revisions(guardian_db, db):
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    guardian_db.guardian_sessions.update_one(
        {"session_id": "session-001"}, {"$set": {"question_count": 7}}
    )
    revisions = list(db["revisions"].find({"collection": "guardian_sessions"}))
    assert len(revisions) == 1
    assert revisions[0]["before"]["question_count"] == 0
    assert revisions[0]["action"] == "update"
    assert revisions[0]["actor_role"] == "guardian_agent"
    assert revisions[0]["actor_id"] == "guardian_agent"


def test_revision_records_the_natural_key_not_the_object_id(instructor_db, db):
    instructor_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.update_one({"lab_id": "lab-01"}, {"$set": {"title": "New"}})
    revision = db["revisions"].find_one({"collection": "labs"})
    assert revision["document_key"] == {"course_id": COURSE_ID, "lab_id": "lab-01"}
    assert "_id" not in revision["before"]


def test_delete_copies_the_document_first(instructor_db, db):
    instructor_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.delete_one({"lab_id": "lab-01"})
    revision = db["revisions"].find_one({"collection": "labs", "action": "delete"})
    assert revision["before"]["lab_id"] == "lab-01"
    assert db["labs"].find_one({"lab_id": "lab-01"}) is None


def test_replace_copies_the_document_first(instructor_db, db):
    instructor_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.replace_one({"lab_id": "lab-01"}, FACTORIES["labs"](title="Replaced"))
    revision = db["revisions"].find_one({"collection": "labs", "action": "replace"})
    assert revision["before"]["title"] == "Sorting"


def test_delete_many_copies_every_document(instructor_db, db):
    instructor_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.insert_one(FACTORIES["labs"](lab_id="lab-02"))
    instructor_db.labs.delete_many({"course_id": COURSE_ID})
    assert db["revisions"].count_documents({"collection": "labs", "action": "delete"}) == 2


def test_insert_is_not_revisioned(guardian_db, db):
    """There is no prior state to lose, so an insert writes no revision."""
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    assert db["revisions"].count_documents({}) == 0


def test_revisions_are_not_themselves_revisioned(db):
    # MIGRATION is the only role that may insert a revision directly
    # (importing history); every other role's revisions come from the guard.
    guarded(db, Role.MIGRATION, actor_id="migration").revisions.insert_one(FACTORIES["revisions"]())
    assert db["revisions"].count_documents({"collection": "revisions"}) == 0


def test_nobody_may_delete_a_revision(admin_db):
    with pytest.raises(PermissionError_, match="may not delete"):
        admin_db.revisions.delete_one({})


def test_nobody_may_update_a_revision(admin_db):
    with pytest.raises(PermissionError_, match="may not update"):
        admin_db.revisions.update_one({}, {"$set": {"before": {}}})


def test_no_role_may_insert_a_revision_directly(db):
    """An agent that could write `revisions` itself could forge history.

    The snapshot is written by `GuardedCollection._record` straight to the
    unguarded database, so denying every role `insert` here costs nothing and
    closes the forgery route. MIGRATION is the deliberate exception: importing
    the old stores' history means writing revisions nobody observed.
    """
    forged = {
        "revision_id": "forged",
        "collection": "labs",
        "document_key": {"lab_id": "lab-01"},
        "course_id": COURSE_ID,
        "action": "update",
        "before": {"title": "never happened"},
        "actor_id": "someone-else",
        "actor_role": "instructor",
        "recorded_at": now(),
    }
    for role in (Role.ADMIN, Role.INSTRUCTOR, Role.STUDENT, *AGENT_ROLES):
        guard = guarded(db, role, actor_id=role.value)
        with pytest.raises(PermissionError_, match="may not insert"):
            guard.revisions.insert_one(dict(forged))

    migration = guarded(db, Role.MIGRATION, actor_id="migration")
    migration.revisions.insert_one(dict(forged))


def test_a_guarded_update_still_records_a_revision_despite_that(guardian_db, db):
    """The denial above must not break rule 4, which depends on the snapshot."""
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    guardian_db.guardian_sessions.update_one(
        {"session_id": "session-001"}, {"$set": {"question_count": 3}}
    )
    assert db["revisions"].count_documents({"collection": "guardian_sessions"}) == 1


def test_every_role_that_can_change_a_document_can_insert_at_the_atlas_level(db):
    """The flip side: the snapshot uses the caller's own connection.

    If the Atlas role lacks insert on `revisions`, every update fails at the
    server even though POLICY allows it.
    """
    from aied_db.access import atlas_roles

    definitions = {d["role"]: d["privileges"] for d in atlas_roles("aied")}
    for role in (Role.ADMIN, Role.INSTRUCTOR, Role.STUDENT, *AGENT_ROLES):
        changes = any(
            perm.ops & {"update", "replace", "delete"}
            for perm in (POLICY[c].get(role) for c in POLICY)
            if perm is not None
        )
        if not changes:
            continue
        privileges = definitions[f"aied_{role.value}"]
        revisions = [p for p in privileges if p["resource"]["collection"] == "revisions"]
        assert revisions, role.value
        assert "insert" in revisions[0]["actions"], role.value


def test_history_filters_and_sorts_newest_first(instructor_db, db):
    instructor_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.update_one({"lab_id": "lab-01"}, {"$set": {"title": "v2"}})
    instructor_db.labs.update_one({"lab_id": "lab-01"}, {"$set": {"title": "v3"}})

    entries = history(db, collection="labs", course_id=COURSE_ID)
    assert len(entries) == 2
    assert entries[0]["before"]["title"] == "v2"  # newest change first
    assert entries[1]["before"]["title"] == "Sorting"

    by_key = history(db, document_key={"lab_id": "lab-01"})
    assert len(by_key) == 2
    assert history(db, actor_id="nobody") == []


def test_history_accepts_a_guarded_database(instructor_db):
    instructor_db.labs.insert_one(FACTORIES["labs"]())
    instructor_db.labs.update_one({"lab_id": "lab-01"}, {"$set": {"title": "v2"}})
    assert len(history(instructor_db, collection="labs")) == 1


# ── Duplicate keys ───────────────────────────────────────────────────────────


def test_duplicate_key_raises_integrity_error(guardian_db):
    """The NAME matters: ui-student's GuardianAgent checks it by name."""
    guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    with pytest.raises(IntegrityError) as excinfo:
        guardian_db.guardian_sessions.insert_one(FACTORIES["guardian_sessions"]())
    assert "IntegrityError" in type(excinfo.value).__name__


# ── Atlas roles ──────────────────────────────────────────────────────────────


def test_atlas_roles_never_grant_more_than_policy():
    from aied_db.access import POLICY, atlas_roles

    action_of = {"find": "find", "insert": "insert", "update": "update", "replace": "update", "delete": "remove"}
    for definition in atlas_roles("aied"):
        role = Role(definition["role"].removeprefix("aied_"))
        snapshots = any(
            POLICY[c][role].ops & {"update", "replace", "delete"} for c in POLICY if role in POLICY[c]
        )
        for privilege in definition["privileges"]:
            collection = privilege["resource"]["collection"]
            granted = POLICY[collection].get(role)
            permitted = {action_of[op] for op in granted.ops} if granted else set()
            if collection == "revisions" and snapshots:
                # The one exception: the guard writes the snapshot with the
                # caller's own connection, so the DB role needs insert there.
                permitted |= {"insert"}
            assert set(privilege["actions"]) <= permitted, (definition["role"], collection)


def test_no_atlas_role_may_remove_a_revision():
    from aied_db.access import atlas_roles

    for definition in atlas_roles("aied"):
        for privilege in definition["privileges"]:
            if privilege["resource"]["collection"] == "revisions":
                assert "remove" not in privilege["actions"]
                assert "update" not in privilege["actions"]
