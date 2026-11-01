"""What the validators accept, and what they must refuse.

mongomock ignores `$jsonSchema`, so these run against
`tests/jsonschema_check.py` — a stand-in for MongoDB's own validation. The
authoritative check is still `scripts/create_db.sh` against real Atlas.
"""

from __future__ import annotations

import pytest

from aied_db._util import now
from aied_db.schema import COLLECTIONS, validator_for
from aied_db.schema.lab_companion import EMBEDDING_DIMS

from .factories import FACTORIES
from .jsonschema_check import SchemaViolation, assert_valid, is_valid


@pytest.mark.parametrize("collection", sorted(FACTORIES))
def test_minimal_document_is_valid(collection):
    assert_valid(FACTORIES[collection](), COLLECTIONS[collection]["schema"], collection=collection)


@pytest.mark.parametrize("collection", sorted(FACTORIES))
def test_unknown_field_is_rejected(collection):
    """`additionalProperties: false` everywhere: a typo must not silently persist."""
    document = FACTORIES[collection](nonsense_field="x")
    assert not is_valid(document, COLLECTIONS[collection]["schema"])


@pytest.mark.parametrize("collection", sorted(FACTORIES))
def test_each_required_field_is_enforced(collection):
    schema = COLLECTIONS[collection]["schema"]
    for field in schema["required"]:
        document = FACTORIES[collection]()
        document.pop(field, None)
        assert not is_valid(document, schema), f"{collection}: missing {field} was accepted"


def test_validator_for_wraps_in_jsonschema():
    assert validator_for("users") == {"$jsonSchema": COLLECTIONS["users"]["schema"]}


# ── Specific rules worth their own test ──────────────────────────────────────


def test_user_role_is_an_enum():
    assert not is_valid(FACTORIES["users"](role="superuser"), COLLECTIONS["users"]["schema"])


def test_confidence_is_bounded_to_one():
    """The task3 draft left confidence unbounded; here it is 0..1."""
    schema = COLLECTIONS["integrity_records"]["schema"]
    assert is_valid(FACTORIES["integrity_records"](confidence=1.0), schema)
    assert not is_valid(FACTORIES["integrity_records"](confidence=1.4), schema)
    assert not is_valid(FACTORIES["integrity_records"](confidence=-0.1), schema)


def test_risk_level_is_an_enum():
    schema = COLLECTIONS["integrity_reports"]["schema"]
    assert not is_valid(FACTORIES["integrity_reports"](risk_level="catastrophic"), schema)


def test_embedding_must_have_the_index_dimensions():
    """A wrong-length embedding would break the Atlas vector index silently."""
    schema = COLLECTIONS["rag_vectors"]["schema"]
    assert is_valid(FACTORIES["rag_vectors"](), schema)
    assert not is_valid(FACTORIES["rag_vectors"](embedding=[0.1] * (EMBEDDING_DIMS - 1)), schema)
    assert not is_valid(FACTORIES["rag_vectors"](embedding=[0.1] * (EMBEDDING_DIMS + 1)), schema)


def test_assignment_needs_at_least_one_criterion():
    schema = COLLECTIONS["assignments"]["schema"]
    assert not is_valid(FACTORIES["assignments"](criteria=[]), schema)


def test_criterion_max_points_must_be_positive():
    """spec.py rejects zero or negative max_points; so does the validator."""
    schema = COLLECTIONS["assignments"]["schema"]
    criteria = [{"id": "c1", "name": "C1", "max_points": 0.0}]
    assert not is_valid(FACTORIES["assignments"](criteria=criteria), schema)


def test_assignment_id_pattern_matches_spec_py():
    schema = COLLECTIONS["assignments"]["schema"]
    assert is_valid(FACTORIES["assignments"](assignment_id="hw-2_linked_list"), schema)
    assert not is_valid(FACTORIES["assignments"](assignment_id="hw 1/../etc"), schema)


def test_result_status_is_an_enum():
    schema = COLLECTIONS["assessment_results"]["schema"]
    assert is_valid(FACTORIES["assessment_results"](status="needs_review"), schema)
    assert not is_valid(FACTORIES["assessment_results"](status="graded"), schema)


def test_score_may_be_null_when_grading_failed():
    """models.py has `score: Optional[float]` — None means grading failed."""
    schema = COLLECTIONS["assessment_results"]["schema"]
    assert is_valid(FACTORIES["assessment_results"](score=None), schema)


def test_feedback_and_provenance_may_be_null():
    schema = COLLECTIONS["assessment_results"]["schema"]
    assert is_valid(FACTORIES["assessment_results"](feedback=None, provenance=None), schema)


def test_feedback_still_validated_when_present():
    schema = COLLECTIONS["assessment_results"]["schema"]
    assert not is_valid(
        FACTORIES["assessment_results"](feedback={"summary": "ok", "unexpected": 1}), schema
    )


def test_booleans_are_not_integers():
    """SQLite stored 0/1; Mongo must hold real booleans."""
    schema = COLLECTIONS["guardian_sessions"]["schema"]
    assert not is_valid(FACTORIES["guardian_sessions"](escalated=1), schema)


def test_timestamps_must_be_dates_not_strings():
    """The stores convert ISO strings to dates on the way in."""
    schema = COLLECTIONS["interactions"]["schema"]
    assert not is_valid(FACTORIES["interactions"](timestamp="2026-10-28T00:00:00Z"), schema)


def test_concept_tags_is_an_array_not_a_json_string():
    schema = COLLECTIONS["guardian_questions"]["schema"]
    assert not is_valid(FACTORIES["guardian_questions"](concept_tags='["a"]'), schema)


def test_nullable_fields_accept_null_and_a_value():
    schema = COLLECTIONS["guardian_sessions"]["schema"]
    assert is_valid(FACTORIES["guardian_sessions"](ended_at=None, report_id=None), schema)
    assert is_valid(
        FACTORIES["guardian_sessions"](ended_at=now(), report_id="report-1"), schema
    )


def test_checker_reports_the_offending_path():
    with pytest.raises(SchemaViolation) as excinfo:
        assert_valid(
            FACTORIES["assessment_results"](status="nope"),
            COLLECTIONS["assessment_results"]["schema"],
            collection="assessment_results",
        )
    assert "status" in str(excinfo.value)
