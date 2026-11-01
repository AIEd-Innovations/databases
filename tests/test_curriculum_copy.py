"""`schema/curriculum.py` is a COPY. This proves it still matches upstream.

curriculum-designer owns `services/schema.py`; this package holds a copy so
every service reads one definition of `labs` and `lab_files`. A copy that
drifts is worse than no copy, so these tests compare the two whenever a
`curriculum-designer` checkout sits next to this repo.

If one fails: edit the file UPSTREAM first, then re-copy it here. Do not fix
the copy in place.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from aied_db.schema import curriculum


@pytest.fixture
def upstream():
    """curriculum-designer's own `services/schema.py`, loaded from a sibling."""
    root = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "curriculum-designer"
    )
    path = os.path.normpath(os.path.join(root, "services", "schema.py"))
    if not os.path.isfile(path):
        pytest.skip("clone curriculum-designer next to this repo to check the copy")

    spec = importlib.util.spec_from_file_location("upstream_curriculum_schema", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_same_collections_are_defined(upstream):
    assert sorted(curriculum.COLLECTIONS) == sorted(upstream.COLLECTIONS)


@pytest.mark.parametrize("collection", ["labs", "lab_files"])
def test_the_validators_are_identical(upstream, collection):
    assert curriculum.COLLECTIONS[collection]["schema"] == upstream.COLLECTIONS[collection]["schema"]


@pytest.mark.parametrize("collection", ["labs", "lab_files"])
def test_the_indexes_are_identical(upstream, collection):
    assert curriculum.COLLECTIONS[collection]["indexes"] == upstream.COLLECTIONS[collection]["indexes"]


def test_the_enumerations_are_identical(upstream):
    for name in ("QUESTION_TYPES", "DIFFICULTY_TIERS", "APPROVAL_STATUSES", "MATERIAL_WEIGHTS", "FILE_KINDS"):
        assert getattr(curriculum, name) == getattr(upstream, name), name


def test_the_embedded_documents_are_identical(upstream):
    for name in ("QUIZ_QUESTION_SCHEMA", "RUBRIC_CRITERION_SCHEMA", "RUBRIC_SCHEMA", "FEEDBACK_ENTRY_SCHEMA"):
        assert getattr(curriculum, name) == getattr(upstream, name), name


def test_the_copy_says_where_it_came_from():
    """A reader must not mistake the copy for the source."""
    assert "COPY" in curriculum.__doc__
    assert "curriculum-designer" in curriculum.__doc__


def test_the_copy_does_not_carry_upstreams_apply_functions():
    """Applying the schema is `aied_db.schema.ensure_all`'s job, for all 25."""
    for name in ("ensure_all", "ensure_collection", "validator_for"):
        assert not hasattr(curriculum, name), f"curriculum.py should not define {name}"


def test_rubric_weights_are_each_bounded_to_one():
    """The three weights are 0..1 individually; summing to 1 is a rule the
    validator cannot express, so `AgentConfig.check_weights` carries it."""
    properties = curriculum.RUBRIC_SCHEMA["properties"]
    for weight in ("code_weight", "report_weight", "manual_weight"):
        assert properties[weight]["minimum"] == 0
        assert properties[weight]["maximum"] == 1
