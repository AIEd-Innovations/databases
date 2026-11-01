"""Holes found in the pre-push review (Oct 29), each now closed.

Every test pairs the attack with the legitimate operation next to it, so a
fix that simply refuses everything would fail too.
"""

from __future__ import annotations

import pytest

from aied_db.access import PermissionError_, Role, atlas_roles, guarded

from .factories import FACTORIES


# ── $rename / $pullAll / unknown operators / pipeline updates ────────────────

def test_rename_cannot_move_a_value_into_a_protected_field(db, admin_db):
    admin_db["labs"].insert_one(FACTORIES["labs"]())
    agent = guarded(db, Role.CURRICULUM_AGENT, "curriculum_agent")
    agent["labs"].update_one({}, {"$set": {"material_content": "approved"}})  # allowed field: fine
    with pytest.raises(PermissionError_, match="approval_status"):
        agent["labs"].update_one({}, {"$rename": {"material_content": "approval_status"}})
    assert admin_db["labs"].find_one({})["approval_status"] == "pending"


def test_unlisted_operators_fail_closed_for_field_limited_roles(db, admin_db):
    admin_db["assessment_results"].insert_one(FACTORIES["assessment_results"]())
    instructor = guarded(db, Role.INSTRUCTOR, "prof")
    criteria = admin_db["assessment_results"].find_one({})["criteria"]
    with pytest.raises(PermissionError_, match="criteria"):
        instructor["assessment_results"].update_one({}, {"$pullAll": {"criteria": [criteria[0]]}})
    with pytest.raises(PermissionError_, match="unsupported update operator"):
        instructor["assessment_results"].update_one({}, {"$futureOp": {"criteria": 1}})
    instructor["assessment_results"].update_one({}, {"$set": {"instructor_notes": "ok"}})  # still works


def test_pipeline_updates_refused_for_field_limited_roles(db, admin_db):
    admin_db["labs"].insert_one(FACTORIES["labs"]())
    agent = guarded(db, Role.CURRICULUM_AGENT, "curriculum_agent")
    with pytest.raises(PermissionError_, match="pipeline"):
        agent["labs"].update_one({}, [{"$set": {"approval_status": "approved"}}])


# ── aggregation reads through $lookup / $unionWith ───────────────────────────

def test_student_cannot_lookup_reference_solutions(db, admin_db):
    admin_db["assignments"].insert_one(FACTORIES["assignments"]())
    assignment_id = admin_db["assignments"].find_one({})["assignment_id"]
    admin_db["assignment_files"].insert_one(
        FACTORIES["assignment_files"](assignment_id=assignment_id, kind="solution")
    )
    student = guarded(db, Role.STUDENT, "stu")
    lookup = {"$lookup": {"from": "assignment_files", "localField": "assignment_id",
                          "foreignField": "assignment_id", "as": "leak"}}
    with pytest.raises(PermissionError_, match="assignment_files"):
        list(student["assignments"].aggregate([lookup]))
    with pytest.raises(PermissionError_, match="assignment_files"):
        list(student["assignments"].aggregate([{"$unionWith": "assignment_files"}]))
    with pytest.raises(PermissionError_, match="assignment_files"):
        list(student["assignments"].aggregate([{"$facet": {"x": [lookup]}}]))
    assert list(student["assignments"].aggregate([{"$match": {}}]))  # plain reads still fine


def test_a_role_may_lookup_what_it_may_read(db, admin_db):
    admin_db["assignments"].insert_one(FACTORIES["assignments"]())
    agent = guarded(db, Role.ASSESSMENT_AGENT, "assessment_agent")
    rows = list(agent["assignments"].aggregate([{"$lookup": {
        "from": "assignment_files", "localField": "assignment_id",
        "foreignField": "assignment_id", "as": "files"}}]))
    assert rows and "files" in rows[0]


# ── insert-time rules ────────────────────────────────────────────────────────

def test_agent_cannot_create_an_approved_lab(db):
    agent = guarded(db, Role.CURRICULUM_AGENT, "curriculum_agent")
    with pytest.raises(PermissionError_, match="approval_status"):
        agent["labs"].insert_one(FACTORIES["labs"](approval_status="approved"))
    agent["labs"].insert_one(FACTORIES["labs"]())  # a pending draft is fine


def test_agent_cannot_forge_an_instructor_grade(db):
    agent = guarded(db, Role.ASSESSMENT_AGENT, "assessment_agent")
    with pytest.raises(PermissionError_, match="status"):
        agent["assessment_results"].insert_one(
            FACTORIES["assessment_results"](status="instructor_graded"))
    with pytest.raises(PermissionError_, match="final_score"):
        agent["assessment_results"].insert_one(FACTORIES["assessment_results"](final_score=100.0))
    agent["assessment_results"].insert_one(FACTORIES["assessment_results"](status="needs_review"))


def test_agent_cannot_create_a_resolved_escalation(db):
    agent = guarded(db, Role.INTEGRITY_AGENT, "integrity_agent")
    with pytest.raises(PermissionError_, match="status"):
        agent["integrity_escalations"].insert_one(FACTORIES["integrity_escalations"](status="resolved"))
    agent["integrity_escalations"].insert_one(FACTORIES["integrity_escalations"]())


# ── students write only their own documents ─────────────────────────────────

def test_student_writes_are_confined_to_their_own_documents(db, admin_db):
    me = guarded(db, Role.STUDENT, "stu-1")
    with pytest.raises(PermissionError_, match="student_id"):
        me["student_files"].insert_one(FACTORIES["student_files"](student_id="stu-2"))
    me["student_files"].insert_one(FACTORIES["student_files"](student_id="stu-1"))

    admin_db["student_files"].insert_one(FACTORIES["student_files"](student_id="stu-2", file_id="theirs"))
    with pytest.raises(PermissionError_, match="student_id"):
        me["student_files"].delete_one({"file_id": "theirs"})
    with pytest.raises(PermissionError_, match="student_id"):
        me["student_files"].delete_many({})
    assert admin_db["student_files"].count_documents({"file_id": "theirs"}) == 1


def test_student_settings_must_be_their_own(db):
    me = guarded(db, Role.STUDENT, "stu-1")
    with pytest.raises(PermissionError_, match="user_id"):
        me["agent_configs"].insert_one(FACTORIES["agent_configs"](user_id=None))
    with pytest.raises(PermissionError_, match="user_id"):
        me["agent_configs"].update_one({"user_id": "stu-9"}, {"$set": {"value": 1}}, upsert=True)


# ── revisions ────────────────────────────────────────────────────────────────

def test_no_role_can_insert_a_revision_directly(db):
    for role in (Role.ADMIN, Role.CURRICULUM_AGENT, Role.ASSESSMENT_AGENT, Role.INTEGRITY_AGENT):
        with pytest.raises(PermissionError_):
            guarded(db, role, "x")["revisions"].insert_one(FACTORIES["revisions"]())


def test_snapshots_still_land_in_revisions(db, admin_db):
    admin_db["labs"].insert_one(FACTORIES["labs"]())
    guarded(db, Role.CURRICULUM_AGENT, "curriculum_agent")["labs"].update_one(
        {}, {"$set": {"title": "v2"}})
    assert admin_db["revisions"].count_documents({"collection": "labs"}) == 1


def test_atlas_roles_still_let_the_guard_snapshot():
    roles = {r["role"]: r for r in atlas_roles()}
    for name in ("aied_curriculum_agent", "aied_instructor", "aied_admin", "aied_guardian_agent"):
        rev = [p for p in roles[name]["privileges"] if p["resource"]["collection"] == "revisions"]
        assert rev and "insert" in rev[0]["actions"], name
    # an append-only agent never changes anything, so it never snapshots
    participant = roles["aied_participant_agent"]["privileges"]
    rev = [p for p in participant if p["resource"]["collection"] == "revisions"]
    assert not rev or "insert" not in rev[0]["actions"]
