"""Who may write what, and the revision trail that makes writes recoverable.

This module is the single place the Oct 26 meeting's rules are expressed.
Services never touch a raw database:

    from aied_db import Role, get_db, guarded
    db = guarded(get_db(), Role.ASSESSMENT_AGENT, actor_id="assessment_agent")
    db.assessment_results.insert_one(result)     # allowed: its own output
    db.assignments.update_one(...)               # PermissionError: human data

The four rules, and where each one lives:

1. AGENTS NEVER OVERWRITE HUMAN DATA. Instructor uploads, student files,
   grading specs, reference solutions and courses are `read_only` to every
   agent role in `POLICY`.
2. AGENTS APPEND THEIR OWN OUTPUT, and may update only NAMED FIELDS of
   their own records. That is `Permission.fields` — an update touching any
   other field is refused.
3. AGENTS NEVER DELETE. No agent role has `delete` on anything.
4. NOTHING IS LOST. `update`, `replace` and `delete` copy the prior
   document into `revisions` BEFORE changing it, and nobody may update or
   delete a revision.

Humans write their own data: instructors own their courses, labs, specs and
reviews; students own their files and submissions. Agents cannot approve a
lab, review a grade, resolve an escalation, or change a setting — those are
human-only writes, so no agent role holds them.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional

from pymongo.errors import DuplicateKeyError

from ._util import IntegrityError, new_id, now, strip_id
from .schema import COLLECTIONS

READ = "find"
INSERT = "insert"
UPDATE = "update"
REPLACE = "replace"
DELETE = "delete"


class Role(str, Enum):
    """Every actor that can hold a connection to this database."""

    # Humans
    ADMIN = "admin"
    INSTRUCTOR = "instructor"
    STUDENT = "student"

    # Agents
    CURRICULUM_AGENT = "curriculum_agent"
    ASSESSMENT_AGENT = "assessment_agent"
    PARTICIPANT_AGENT = "participant_agent"
    GUARDIAN_AGENT = "guardian_agent"
    LAB_COMPANION_AGENT = "lab_companion_agent"
    INTEGRITY_AGENT = "integrity_agent"

    # The one-off data migration, and nothing else. Not for a running service.
    MIGRATION = "migration"


AGENT_ROLES = frozenset(
    {
        Role.CURRICULUM_AGENT,
        Role.ASSESSMENT_AGENT,
        Role.PARTICIPANT_AGENT,
        Role.GUARDIAN_AGENT,
        Role.LAB_COMPANION_AGENT,
        Role.INTEGRITY_AGENT,
    }
)


@dataclass(frozen=True)
class Permission:
    """What one role may do to one collection.

    `fields` restricts UPDATE to those top-level field names. None means any
    field; it is only ever None for human roles.
    """

    ops: frozenset[str] = dataclass_field(default_factory=frozenset)
    fields: Optional[frozenset[str]] = None

    def allows(self, op: str) -> bool:
        return op in self.ops


def _perm(*ops: str, fields: Optional[Iterable[str]] = None) -> Permission:
    return Permission(frozenset(ops), frozenset(fields) if fields is not None else None)


def read_only() -> Permission:
    return _perm(READ)


def append_only() -> Permission:
    """Insert new documents and read them back, but never change one."""
    return _perm(READ, INSERT)


def append_and_update(*fields: str) -> Permission:
    """Append, and update only these named fields of an existing document."""
    return _perm(READ, INSERT, UPDATE, fields=fields)


def update_fields_only(*fields: str) -> Permission:
    """Read, and update only these named fields — but never create a document.

    For human data an agent must annotate without authoring: the indexer
    stamps `chunk_count` on an instructor's upload, and cannot invent an
    upload of its own.
    """
    return _perm(READ, UPDATE, fields=fields)


def full() -> Permission:
    """Everything, including delete. Human roles only."""
    return _perm(READ, INSERT, UPDATE, REPLACE, DELETE)


def no_access() -> Permission:
    return Permission(frozenset())


# ── The policy ───────────────────────────────────────────────────────────────
# Read it as a grid: one row per collection, one entry per role that may touch
# it at all. A role absent from a row has NO access to that collection.

POLICY: dict[str, dict[Role, Permission]] = {
    # ---- system: humans only. No agent writes a user, course or enrollment.
    "users": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.STUDENT: read_only(),
        Role.MIGRATION: append_only(),
        **{role: read_only() for role in AGENT_ROLES},
    },
    "courses": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: _perm(READ, INSERT, UPDATE, REPLACE),
        Role.STUDENT: read_only(),
        Role.MIGRATION: append_only(),
        **{role: read_only() for role in AGENT_ROLES},
    },
    "enrollments": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: _perm(READ, INSERT, DELETE),
        Role.STUDENT: read_only(),
        Role.MIGRATION: append_only(),
        **{role: read_only() for role in AGENT_ROLES},
    },
    # ---- curriculum: the agent drafts, the instructor approves.
    # approval_status / approved_by / approval_notes are NOT in the agent's
    # field list: an agent may not approve its own lab.
    "labs": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: full(),
        Role.STUDENT: read_only(),
        Role.CURRICULUM_AGENT: append_and_update(
            "title",
            "spec_markdown",
            "quiz",
            "rubric",
            "learning_objectives",
            "difficulty",
            "estimated_duration_min",
            "material_weight",
            "material_content",
            "agent_instructions",
            "feedback_history",
            "version",
            "last_updated",
        ),
        Role.ASSESSMENT_AGENT: read_only(),
        Role.LAB_COMPANION_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "lab_files": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: full(),
        Role.STUDENT: read_only(),
        # Instructor uploads. The agent may append its extracted text, never edit a file.
        Role.CURRICULUM_AGENT: append_only(),
        Role.LAB_COMPANION_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    # ---- participant / guardian: the agents' own telemetry.
    "interactions": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.PARTICIPANT_AGENT: append_only(),
        Role.LAB_COMPANION_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "guardian_sessions": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        # The counters the running GuardianAgent updates, and nothing else.
        Role.GUARDIAN_AGENT: append_and_update(
            "question_count",
            "violation_count",
            "escalated",
            "status",
            "ended_at",
            "report_generated",
            "report_id",
        ),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "guardian_questions": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.GUARDIAN_AGENT: append_only(),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "guardian_violations": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.GUARDIAN_AGENT: append_only(),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "guardian_verifications": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.GUARDIAN_AGENT: append_only(),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    # ---- lab companion / RAG.
    "rag_documents": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: full(),
        Role.STUDENT: read_only(),
        # Human upload. The indexer may stamp what it DERIVED and nothing
        # else, and may not create a document of its own — no INSERT here.
        Role.LAB_COMPANION_AGENT: update_fields_only("chunk_count", "indexed_at"),
        Role.MIGRATION: append_only(),
    },
    "rag_vectors": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        # Derived output: the agent appends chunks. Re-indexing inserts a new
        # chunk_index; it does not edit an old one.
        Role.LAB_COMPANION_AGENT: append_only(),
        Role.MIGRATION: append_only(),
    },
    "student_files": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.STUDENT: _perm(READ, INSERT, DELETE),
        # The student's own work: every agent reads, none writes.
        Role.LAB_COMPANION_AGENT: read_only(),
        Role.ASSESSMENT_AGENT: read_only(),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "rag_queries": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.LAB_COMPANION_AGENT: append_only(),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    # ---- assessment: the instructor owns the spec, the agent owns the grade.
    "assignments": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: full(),
        Role.STUDENT: read_only(),  # repos must project criteria[].expected_answer away
        Role.ASSESSMENT_AGENT: read_only(),
        Role.CURRICULUM_AGENT: append_only(),  # lab import writes a copy
        Role.MIGRATION: append_only(),
    },
    "assignment_files": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: full(),
        # Reference solutions live here. Students get NO access at all, not
        # even read: kind == "solution" must never reach them.
        Role.ASSESSMENT_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    "assessment_results": {
        Role.ADMIN: full(),
        # The instructor's review sets these four and nothing else; a regrade
        # is a new submission, so even the instructor does not rewrite a score.
        Role.INSTRUCTOR: append_and_update(
            "status", "final_score", "instructor_notes", "reviewed_at"
        ),
        Role.STUDENT: read_only(),
        # Insert-only for the agent: it may not review its own grade.
        Role.ASSESSMENT_AGENT: append_only(),
        Role.INTEGRITY_AGENT: read_only(),
        Role.MIGRATION: append_only(),
    },
    # ---- integrity: the agent detects, the instructor resolves.
    "integrity_sessions": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.INTEGRITY_AGENT: append_and_update("last_activity_at", "status"),
        Role.MIGRATION: append_only(),
    },
    "integrity_records": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.INTEGRITY_AGENT: append_only(),
        Role.MIGRATION: append_only(),
    },
    "integrity_violations": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.INTEGRITY_AGENT: append_only(),
        Role.MIGRATION: append_only(),
    },
    "integrity_escalations": {
        Role.ADMIN: full(),
        # Only a human resolves an escalation.
        Role.INSTRUCTOR: append_and_update("status", "reviewer_notes", "resolved_by", "resolved_at"),
        # The agent raises it and may never close it.
        Role.INTEGRITY_AGENT: append_only(),
        Role.MIGRATION: append_only(),
    },
    "integrity_reports": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: read_only(),
        Role.STUDENT: no_access(),
        Role.INTEGRITY_AGENT: append_only(),
        Role.GUARDIAN_AGENT: append_only(),
        Role.MIGRATION: append_only(),
    },
    # ---- platform.
    # revisions is append-and-read for everyone, forever. No role may update
    # or delete one, including ADMIN: that is the point of rule 4.
    "revisions": {
        # Written only by GuardedCollection._record, which bypasses POLICY.
        # No role may insert one directly, or an agent could forge history.
        Role.ADMIN: read_only(),
        Role.INSTRUCTOR: read_only(),
        Role.MIGRATION: append_only(),  # importing history from the old stores
        **{role: read_only() for role in AGENT_ROLES},
    },
    # Seeded from the read-only aied_db/defaults.json by init_db.py.
    "agent_config_defaults": {
        Role.ADMIN: _perm(READ, INSERT, UPDATE, REPLACE),
        Role.INSTRUCTOR: read_only(),
        Role.STUDENT: read_only(),
        Role.MIGRATION: append_only(),
        **{role: read_only() for role in AGENT_ROLES},
    },
    # Overrides. Agents READ their settings and never write them.
    "agent_configs": {
        Role.ADMIN: full(),
        Role.INSTRUCTOR: full(),
        Role.STUDENT: full(),  # a student owns their personal overrides
        Role.MIGRATION: append_only(),
        **{role: read_only() for role in AGENT_ROLES},
    },
}


def natural_key_fields(collection: str) -> list[str]:
    """The fields that identify a document, from its first unique index.

    Used for `revisions.document_key`, so the trail refers to documents the
    way the rest of the system does rather than by Mongo's `_id`.
    """
    for keys, unique, _why in COLLECTIONS[collection]["indexes"]:
        if unique:
            return [field for field, _direction in keys]
    return []


class PermissionError_(PermissionError):
    """Raised when a role attempts an operation the policy does not grant."""


def _deny(role: Role, op: str, collection: str, detail: str = "") -> None:
    message = f"role {role.value!r} may not {op} {collection!r}"
    raise PermissionError_(f"{message}: {detail}" if detail else message)


def permission_for(role: Role, collection: str) -> Permission:
    if collection not in POLICY:
        raise PermissionError_(
            f"collection {collection!r} has no POLICY entry. Every collection "
            "needs one — add it to aied_db/access.py."
        )
    return POLICY[collection].get(role, Permission(frozenset()))


_MUTATING_OPERATORS = ("$set", "$unset", "$inc", "$push", "$addToSet", "$pull", "$pullAll", "$pop", "$rename", "$setOnInsert", "$min", "$max", "$mul", "$currentDate", "$bit")


def _touched_fields(update: Mapping[str, Any]) -> set[str]:
    """The top-level field names an update document would change."""
    touched: set[str] = set()
    for operator, operand in update.items():
        if not operator.startswith("$"):
            # A replacement document passed to update_one(): every field counts.
            touched.add(operator.split(".", 1)[0])
            continue
        if operator in _MUTATING_OPERATORS and isinstance(operand, Mapping):
            touched.update(str(name).split(".", 1)[0] for name in operand)
            if operator == "$rename":
                # {"$rename": {"a": "b"}} changes b too: it gets a's value.
                touched.update(str(dest).split(".", 1)[0] for dest in operand.values())
    return touched


def _pipeline_sources(pipeline: Any) -> set[str]:
    """Collections an aggregation pipeline reads besides its own.

    $lookup / $graphLookup / $unionWith pull documents from another
    collection, so a role must be allowed to read that one too — otherwise a
    student with read on `assignments` could $lookup into `assignment_files`
    and read reference solutions.
    """
    found: set[str] = set()
    if not isinstance(pipeline, (list, tuple)):
        return found
    for stage in pipeline:
        if not isinstance(stage, Mapping):
            continue
        for op, body in stage.items():
            if op in ("$lookup", "$graphLookup") and isinstance(body, Mapping):
                if isinstance(body.get("from"), str):
                    found.add(body["from"])
                found |= _pipeline_sources(body.get("pipeline"))
            elif op == "$unionWith":
                if isinstance(body, str):
                    found.add(body)
                elif isinstance(body, Mapping):
                    if isinstance(body.get("coll"), str):
                        found.add(body["coll"])
                    found |= _pipeline_sources(body.get("pipeline"))
            elif op == "$facet" and isinstance(body, Mapping):
                for sub in body.values():
                    found |= _pipeline_sources(sub)
    return found


# ── Rules a field list cannot express ────────────────────────────────────────

#: Values an agent may give these fields when it CREATES a document. Without
#: this, insert permission would let an agent create a lab already approved,
#: a result already instructor-graded, or an escalation already resolved —
#: the very decisions the update field lists reserve for humans.
INSERT_RULES: dict[str, dict[Role, dict[str, frozenset]]] = {
    "labs": {
        Role.CURRICULUM_AGENT: {
            "approval_status": frozenset({"pending"}),
            "approved_by": frozenset({None}),
            "approval_notes": frozenset({None}),
        },
    },
    "assessment_results": {
        Role.ASSESSMENT_AGENT: {
            "status": frozenset({"ai_graded", "needs_review"}),
            "final_score": frozenset({None}),
            "reviewed_at": frozenset({None}),
            "instructor_notes": frozenset({""}),
        },
    },
    "integrity_escalations": {
        Role.INTEGRITY_AGENT: {
            "status": frozenset({"pending_review"}),
            "reviewer_notes": frozenset({None}),
            "resolved_by": frozenset({None}),
            "resolved_at": frozenset({None}),
        },
    },
}

#: A student's writes are confined to their own documents: this field must
#: equal the guard's actor_id on every document they create, change or delete.
OWNER_FIELD: dict[tuple[str, Role], str] = {
    ("agent_configs", Role.STUDENT): "user_id",
    ("student_files", Role.STUDENT): "student_id",
}


# ── The guarded wrapper ──────────────────────────────────────────────────────


class GuardedCollection:
    """One collection, with the policy applied and revisions recorded.

    Reads pass straight through. Writes are checked first, and anything that
    destroys a prior state copies it into `revisions` in the same call.

    The revision is written BEFORE the change. If the change then fails, the
    trail has a spurious entry — which is the safe direction to fail in: a
    revision too many costs nothing, a revision too few loses data.
    """

    #: Collections whose revisions are not themselves revisioned, or the
    #: trail would recurse forever.
    _NEVER_REVISIONED = frozenset({"revisions"})

    def __init__(self, guard: "GuardedDatabase", name: str) -> None:
        self._guard = guard
        self._name = name
        self._collection = guard.database[name]
        self._permission = permission_for(guard.role, name)

    # -------------------------------------------------------------- helpers
    def _require(self, op: str) -> None:
        if not self._permission.allows(op):
            granted = ", ".join(sorted(self._permission.ops)) or "nothing"
            _deny(self._guard.role, op, self._name, f"granted: {granted}")

    def _check_fields(self, update: Any) -> None:
        allowed = self._permission.fields
        owner = OWNER_FIELD.get((self._name, self._guard.role))
        if not isinstance(update, Mapping):
            # An aggregation-pipeline update ([{"$set": ...}]) can compute any
            # field; only a role with unrestricted update may send one.
            if allowed is None and owner is None:
                return
            _deny(self._guard.role, UPDATE, self._name,
                  "pipeline-style updates are refused for this role; use $set")
        if owner is not None and owner in _touched_fields(update):
            _deny(self._guard.role, UPDATE, self._name, f"may not change {owner!r}")
        if allowed is None:
            return
        unknown = sorted(op for op in update if op.startswith("$") and op not in _MUTATING_OPERATORS)
        if unknown:
            # Fail closed: an operator we do not parse could touch any field.
            _deny(self._guard.role, UPDATE, self._name, f"unsupported update operator(s) {unknown}")
        touched = _touched_fields(update)
        forbidden = sorted(touched - allowed)
        if forbidden:
            _deny(
                self._guard.role,
                UPDATE,
                self._name,
                f"may only update {sorted(allowed)}; refused {forbidden}",
            )

    def _check_new(self, document: Mapping[str, Any], op: str = INSERT) -> None:
        """INSERT_RULES and ownership for a document about to be created."""
        rules = INSERT_RULES.get(self._name, {}).get(self._guard.role, {})
        for field, allowed in rules.items():
            if field in document and document[field] not in allowed:
                shown = sorted(map(repr, allowed))
                _deny(self._guard.role, op, self._name,
                      f"{field}={document[field]!r} is not allowed when creating (allowed: {', '.join(shown)})")
        self._check_owned([document], op)

    def _check_owned(self, documents: Iterable[Mapping[str, Any]], op: str) -> None:
        field = OWNER_FIELD.get((self._name, self._guard.role))
        if field is None:
            return
        for document in documents:
            if document.get(field) != self._guard.actor_id:
                _deny(self._guard.role, op, self._name, f"{field} must be your own id")

    def _check_upsert(self, filter: Mapping[str, Any], update: Any) -> None:
        """An upsert that inserts must obey the same rules as insert_one."""
        if not isinstance(update, Mapping):
            return
        new: dict[str, Any] = {k: v for k, v in (filter or {}).items()
                               if not k.startswith("$") and not isinstance(v, Mapping)}
        for operator in ("$set", "$setOnInsert"):
            if isinstance(update.get(operator), Mapping):
                new.update({k: v for k, v in update[operator].items() if "." not in k})
        self._check_new(new, INSERT)

    def _record(self, action: str, documents: Iterable[Mapping[str, Any]]) -> None:
        """Copy prior states into `revisions`. Rule 4: nothing is lost."""
        if self._name in self._NEVER_REVISIONED:
            return
        key_fields = natural_key_fields(self._name)
        entries = []
        for document in documents:
            before = strip_id(document) or {}
            document_key = {f: before[f] for f in key_fields if f in before}
            entries.append(
                {
                    "revision_id": new_id(),
                    "collection": self._name,
                    "document_key": document_key,
                    "course_id": before.get("course_id"),
                    "action": action,
                    "before": before,
                    "actor_id": self._guard.actor_id,
                    "actor_role": self._guard.role.value,
                    "recorded_at": now(),
                }
            )
        if entries:
            self._guard.database["revisions"].insert_many(entries)

    # ---------------------------------------------------------------- reads
    def find(self, *args, **kwargs):
        self._require(READ)
        return self._collection.find(*args, **kwargs)

    def find_one(self, *args, **kwargs):
        self._require(READ)
        return self._collection.find_one(*args, **kwargs)

    def count_documents(self, *args, **kwargs):
        self._require(READ)
        return self._collection.count_documents(*args, **kwargs)

    def distinct(self, *args, **kwargs):
        self._require(READ)
        return self._collection.distinct(*args, **kwargs)

    def aggregate(self, pipeline, *args, **kwargs):
        """Read-only aggregation. `$out` and `$merge` would write, so they are refused."""
        self._require(READ)
        for stage in pipeline:
            for writing in ("$out", "$merge"):
                if writing in stage:
                    _deny(
                        self._guard.role,
                        REPLACE,
                        self._name,
                        f"{writing} in an aggregation writes; use an explicit write instead",
                    )
        for source in sorted(_pipeline_sources(pipeline)):
            if not permission_for(self._guard.role, source).allows(READ):
                _deny(self._guard.role, READ, source, f"via $lookup/$unionWith from {self._name!r}")
        return self._collection.aggregate(pipeline, *args, **kwargs)

    # --------------------------------------------------------------- writes
    def insert_one(self, document, *args, **kwargs):
        self._require(INSERT)
        self._check_new(document)
        try:
            return self._collection.insert_one(dict(document), *args, **kwargs)
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc

    def insert_many(self, documents, *args, **kwargs):
        self._require(INSERT)
        documents = [dict(d) for d in documents]
        for document in documents:
            self._check_new(document)
        try:
            return self._collection.insert_many(documents, *args, **kwargs)
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc

    def update_one(self, filter, update, *args, upsert: bool = False, **kwargs):
        self._require(UPDATE)
        self._check_fields(update)
        if upsert:
            # An upsert may insert, so it needs that right too.
            self._require(INSERT)
        prior = self._collection.find_one(filter)
        if prior is not None:
            self._check_owned([prior], UPDATE)
            self._record(UPDATE, [prior])
        elif upsert:
            self._check_upsert(filter, update)
        try:
            return self._collection.update_one(filter, update, *args, upsert=upsert, **kwargs)
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc

    def update_many(self, filter, update, *args, **kwargs):
        self._require(UPDATE)
        self._check_fields(update)
        priors = list(self._collection.find(filter))
        self._check_owned(priors, UPDATE)
        self._record(UPDATE, priors)
        try:
            return self._collection.update_many(filter, update, *args, **kwargs)
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc

    def replace_one(self, filter, replacement, *args, upsert: bool = False, **kwargs):
        self._require(REPLACE)
        if upsert:
            self._require(INSERT)
        prior = self._collection.find_one(filter)
        self._check_new(replacement, REPLACE)
        if prior is not None:
            self._check_owned([prior], REPLACE)
            self._record(REPLACE, [prior])
        try:
            return self._collection.replace_one(
                filter, dict(replacement), *args, upsert=upsert, **kwargs
            )
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc

    def find_one_and_update(self, filter, update, *args, **kwargs):
        self._require(UPDATE)
        self._check_fields(update)
        if kwargs.get("upsert"):
            self._require(INSERT)
        prior = self._collection.find_one(filter)
        if prior is not None:
            self._check_owned([prior], UPDATE)
            self._record(UPDATE, [prior])
        elif kwargs.get("upsert"):
            self._check_upsert(filter, update)
        try:
            return self._collection.find_one_and_update(filter, update, *args, **kwargs)
        except DuplicateKeyError as exc:
            raise IntegrityError(str(exc)) from exc

    def delete_one(self, filter, *args, **kwargs):
        self._require(DELETE)
        prior = self._collection.find_one(filter)
        if prior is not None:
            self._check_owned([prior], DELETE)
            self._record(DELETE, [prior])
        return self._collection.delete_one(filter, *args, **kwargs)

    def delete_many(self, filter, *args, **kwargs):
        self._require(DELETE)
        priors = list(self._collection.find(filter))
        self._check_owned(priors, DELETE)
        self._record(DELETE, priors)
        return self._collection.delete_many(filter, *args, **kwargs)

    # ------------------------------------------------------------- escapes
    @property
    def unguarded(self):
        """The raw pymongo Collection.

        Only for `scripts/` and tests. A service that reaches for this has
        stopped being governed by POLICY, which is the whole point of the
        wrapper.
        """
        return self._collection

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GuardedCollection {self._name} as {self._guard.role.value}>"


class GuardedDatabase:
    """A `Database` seen through one role's permissions.

    Collections are reachable as attributes (`db.assessment_results`) or by
    item access (`db["assessment_results"]`), both of which return a
    `GuardedCollection`.
    """

    def __init__(self, database, role: Role, actor_id: str) -> None:
        if not isinstance(role, Role):
            role = Role(role)
        if not actor_id:
            raise ValueError(
                "actor_id is required: a write with no actor cannot be attributed "
                "in revisions, which defeats rule 4."
            )
        self.database = database
        self.role = role
        self.actor_id = actor_id
        self._cache: dict[str, GuardedCollection] = {}

    def __getitem__(self, name: str) -> GuardedCollection:
        if name not in self._cache:
            self._cache[name] = GuardedCollection(self, name)
        return self._cache[name]

    def __getattr__(self, name: str) -> GuardedCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def list_collection_names(self) -> list[str]:
        return self.database.list_collection_names()

    @property
    def name(self) -> str:
        return self.database.name

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GuardedDatabase {self.database.name} as {self.role.value}>"


def guarded(database, role: Role, actor_id: str) -> GuardedDatabase:
    """Wrap a database so every write goes through POLICY.

        db = guarded(get_db(), Role.GUARDIAN_AGENT, actor_id="guardian_agent")

    `actor_id` is recorded on every revision, so it must identify the human
    or the agent instance actually doing the work.
    """
    return GuardedDatabase(database, role, actor_id)


# ── Reading the trail ────────────────────────────────────────────────────────


def history(
    database,
    *,
    collection: Optional[str] = None,
    course_id: Optional[str] = None,
    document_key: Optional[Mapping[str, Any]] = None,
    actor_id: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """Prior states from `revisions`, newest first.

    Accepts a raw or a guarded database. Every filter is optional; with none
    of them it returns the most recent changes anywhere in the database.
    """
    source = database.database if isinstance(database, GuardedDatabase) else database

    query: dict[str, Any] = {}
    if collection is not None:
        query["collection"] = collection
    if course_id is not None:
        query["course_id"] = course_id
    if actor_id is not None:
        query["actor_id"] = actor_id
    if document_key:
        for field, value in document_key.items():
            query[f"document_key.{field}"] = value

    # Two writes can land in the same microsecond, which makes `recorded_at`
    # alone an ambiguous sort. `_id` is monotonic, so it breaks the tie in
    # insertion order and the trail reads deterministically.
    cursor = (
        source["revisions"]
        .find(query)
        .sort([("recorded_at", -1), ("_id", -1)])
        .limit(limit)
    )
    return [strip_id(doc) for doc in cursor]


# ── Atlas custom roles ───────────────────────────────────────────────────────


def atlas_roles(database_name: str = "aied") -> list[dict]:
    """POLICY rendered as Atlas custom role definitions.

    A second line of defence: the wrapper governs code that goes through this
    package, these grants govern the connection itself. Atlas has no
    field-level grant, so a role with `append_and_update` gets collection-level
    `update` here and relies on the wrapper for the field list — tightening,
    never loosening, what POLICY says.

    Apply with `python scripts/init_db.py --roles`. Whether the free tier
    permits custom roles is UNVERIFIED; the script reports what the server says.
    """
    action_for = {
        READ: "find",
        INSERT: "insert",
        UPDATE: "update",
        REPLACE: "update",
        DELETE: "remove",
    }

    definitions: list[dict] = []
    for role in Role:
        privileges: list[dict] = []
        for collection_name in sorted(POLICY):
            permission = POLICY[collection_name].get(role)
            if permission is None or not permission.ops:
                continue
            actions = sorted({action_for[op] for op in permission.ops})
            privileges.append(
                {
                    "resource": {"db": database_name, "collection": collection_name},
                    "actions": actions,
                }
            )
        # The guard snapshots into `revisions` with the caller's own
        # connection, so any role that can change or delete a document needs
        # insert there at the database level, even though POLICY lets no role
        # insert a revision directly through the wrapper.
        if any(
            perm.ops & {UPDATE, REPLACE, DELETE}
            for perm in (POLICY[c].get(role) for c in POLICY)
            if perm is not None
        ):
            for privilege in privileges:
                if privilege["resource"]["collection"] == "revisions":
                    privilege["actions"] = sorted(set(privilege["actions"]) | {"insert"})
                    break
            else:
                privileges.append(
                    {"resource": {"db": database_name, "collection": "revisions"}, "actions": ["insert"]}
                )
        if privileges:
            definitions.append(
                {
                    "role": f"aied_{role.value}",
                    "privileges": privileges,
                    "roles": [],
                }
            )
    return definitions


__all__ = [
    "AGENT_ROLES",
    "GuardedCollection",
    "GuardedDatabase",
    "POLICY",
    "Permission",
    "PermissionError_",
    "Role",
    "atlas_roles",
    "guarded",
    "history",
    "natural_key_fields",
    "permission_for",
    "update_fields_only",
]
