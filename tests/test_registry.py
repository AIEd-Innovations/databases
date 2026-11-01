"""The registry itself: 25 collections, every one governed and documented."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from aied_db.access import POLICY, AGENT_ROLES, Role, natural_key_fields, permission_for
from aied_db.schema import COLLECTIONS, OWNERS, MODULES, MODULE_NAMES

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_COLLECTION_COUNT = 25


def test_collection_count():
    assert len(COLLECTIONS) == EXPECTED_COLLECTION_COUNT


def test_every_collection_has_a_policy_entry():
    """A new collection without a POLICY entry fails here, by design."""
    assert sorted(COLLECTIONS) == sorted(POLICY)


def test_every_collection_has_a_validator_and_an_index():
    for name, spec in COLLECTIONS.items():
        assert spec["schema"]["bsonType"] == "object", name
        assert spec["schema"]["additionalProperties"] is False, name
        assert spec["schema"].get("required"), f"{name} has no required fields"
        assert spec["indexes"], f"{name} has no indexes"


def test_every_collection_has_a_unique_natural_key():
    """`revisions.document_key` is derived from it, so every collection needs one."""
    for name in COLLECTIONS:
        assert natural_key_fields(name), f"{name} has no unique index"


def test_id_fields_are_in_the_schema():
    for name, spec in COLLECTIONS.items():
        properties = spec["schema"]["properties"]
        for field in natural_key_fields(name):
            assert field in properties, f"{name}.{field} is indexed but not in the schema"


def test_indexed_fields_exist_in_the_schema():
    for name, spec in COLLECTIONS.items():
        properties = spec["schema"]["properties"]
        for keys, _unique, _why in spec["indexes"]:
            for field, _direction in keys:
                root = field.split(".", 1)[0]
                assert root in properties, f"{name}: index on unknown field {field!r}"


def test_every_index_documents_itself():
    for name, spec in COLLECTIONS.items():
        for keys, _unique, why in spec["indexes"]:
            assert why.strip(), f"{name}: index {keys} has no explanation"


def test_owners_cover_every_collection():
    assert sorted(OWNERS) == sorted(COLLECTIONS)
    assert set(OWNERS.values()) == set(MODULE_NAMES[m] for m in MODULES)


# ── The Oct 26 rules, asserted against POLICY ────────────────────────────────


def test_no_agent_may_delete_anything():
    """Rule 3: agents never delete."""
    for collection in POLICY:
        for role in AGENT_ROLES:
            permission = permission_for(role, collection)
            assert "delete" not in permission.ops, f"{role.value} may delete {collection}"


def test_agents_may_only_update_named_fields():
    """Rule 2: an agent with update rights must name the fields."""
    for collection in POLICY:
        for role in AGENT_ROLES:
            permission = permission_for(role, collection)
            if "update" in permission.ops:
                assert permission.fields is not None, (
                    f"{role.value} has unrestricted update on {collection}"
                )
                assert permission.fields, f"{role.value} has an empty field list on {collection}"


def test_human_data_is_read_only_to_agents():
    """Rule 1: agents never overwrite human data."""
    human_owned = ["users", "student_files", "assignment_files"]
    for collection in human_owned:
        for role in AGENT_ROLES:
            permission = permission_for(role, collection)
            assert permission.ops <= {"find"}, (
                f"{role.value} may write {collection}, which is human data"
            )


def test_nobody_may_edit_or_delete_a_revision():
    """Rule 4: the trail is append-only, for every role including admin."""
    for role in Role:
        permission = permission_for(role, "revisions")
        assert "update" not in permission.ops, f"{role.value} may update revisions"
        assert "replace" not in permission.ops, f"{role.value} may replace revisions"
        assert "delete" not in permission.ops, f"{role.value} may delete revisions"


def test_agents_cannot_approve_a_lab():
    """An agent drafts a lab; only a human approves it."""
    approval_fields = {"approval_status", "approved_by", "approval_notes"}
    permission = permission_for(Role.CURRICULUM_AGENT, "labs")
    assert permission.fields is not None
    assert not (permission.fields & approval_fields)


def test_agents_cannot_review_a_grade():
    """The assessment agent may not set a final score or mark a result reviewed."""
    permission = permission_for(Role.ASSESSMENT_AGENT, "assessment_results")
    assert permission.ops == {"find", "insert"}


def test_agents_cannot_resolve_an_escalation():
    permission = permission_for(Role.INTEGRITY_AGENT, "integrity_escalations")
    assert "update" not in permission.ops
    instructor = permission_for(Role.INSTRUCTOR, "integrity_escalations")
    assert instructor.fields is not None and "status" in instructor.fields


def test_agents_cannot_change_settings():
    for role in AGENT_ROLES:
        for collection in ("agent_configs", "agent_config_defaults"):
            assert permission_for(role, collection).ops == {"find"}


def test_students_cannot_read_reference_solutions():
    """`assignment_files` holds the answer keys; students get no access at all."""
    assert permission_for(Role.STUDENT, "assignment_files").ops == frozenset()


def test_students_cannot_read_integrity_reports():
    assert permission_for(Role.STUDENT, "integrity_reports").ops == frozenset()


def test_unknown_collection_is_refused_loudly():
    with pytest.raises(PermissionError) as excinfo:
        permission_for(Role.ADMIN, "not_a_collection")
    assert "POLICY" in str(excinfo.value)


# ── Generated docs ───────────────────────────────────────────────────────────


def test_generated_docs_are_up_to_date():
    """`docs/collections.md` is generated; regenerate it after a schema change."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_docs.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_the_integration_guides_imports_all_resolve():
    """docs/integration.md is what other teams copy from.

    A snippet that does not import is worse than no guide, so every
    `from aied_db...` line in it is executed here.
    """
    import re

    text = (ROOT / "docs" / "integration.md").read_text()
    lines = re.findall(r"^from aied_db[\w.]* import .+$", text, re.MULTILINE)
    assert lines, "no import lines found — did the guide move?"
    for line in lines:
        exec(compile(line, "docs/integration.md", "exec"), {})


def test_the_integration_guide_names_only_real_roles():
    from aied_db import Role

    text = (ROOT / "docs" / "integration.md").read_text()
    for name in re.findall(r"Role\.([A-Z_]+)", text):
        assert hasattr(Role, name), f"docs/integration.md names Role.{name}, which does not exist"


def test_the_integration_guide_points_at_scripts_that_exist():
    text = (ROOT / "docs" / "integration.md").read_text()
    for script in re.findall(r"python (scripts/[\w./]+\.py)", text):
        assert (ROOT / script).is_file(), f"docs/integration.md runs {script}, which is missing"


# ── the Atlas Admin API payloads ─────────────────────────────────────────────


def _atlas_roles_script():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "atlas_roles_script", ROOT / "scripts" / "atlas_roles.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_driver_action_has_an_atlas_admin_api_name():
    """A missing mapping would silently drop a privilege from a role."""
    from aied_db.access import atlas_roles

    script = _atlas_roles_script()
    used = {
        action
        for definition in atlas_roles("aied")
        for privilege in definition["privileges"]
        for action in privilege["actions"]
    }
    assert used <= set(script.ACTIONS), f"unmapped actions: {used - set(script.ACTIONS)}"


def test_the_api_payload_keeps_every_privilege():
    """The Admin API groups by action, the driver by collection. Same content."""
    from aied_db.access import atlas_roles

    script = _atlas_roles_script()
    for definition in atlas_roles("aied"):
        expected = {
            (privilege["resource"]["collection"], script.ACTIONS[action])
            for privilege in definition["privileges"]
            for action in privilege["actions"]
        }
        payload = script.to_api_payload(definition)
        actual = {
            (resource["collection"], entry["action"])
            for entry in payload["actions"]
            for resource in entry["resources"]
        }
        assert actual == expected, definition["role"]


def test_no_atlas_api_payload_grants_remove_on_revisions():
    """Rule 4 has to survive the translation into Atlas roles."""
    from aied_db.access import atlas_roles

    script = _atlas_roles_script()
    for definition in atlas_roles("aied"):
        payload = script.to_api_payload(definition)
        for entry in payload["actions"]:
            if entry["action"] != "REMOVE":
                continue
            for resource in entry["resources"]:
                assert resource["collection"] != "revisions", definition["role"]
