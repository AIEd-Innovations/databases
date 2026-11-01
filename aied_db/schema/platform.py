"""Platform collections: revisions, agent_config_defaults, agent_configs.

These implement two of the Oct 26 rules.

NOTHING IS LOST. Every update, replace or delete that goes through
`access.guarded()` first copies the prior document into `revisions`. Nobody
— no agent, no instructor, no admin — may edit or delete a revision; that
is enforced in `access.POLICY`, and the Atlas custom roles grant only
`insert` and `find` on it.

SETTINGS CASCADE. `agent_config_defaults` is seeded from the read-only
`aied_db/defaults.json` file and is itself read-only at runtime. Overrides
live in `agent_configs`: an instructor may set one course-wide, a user may
set one for themselves. `agent_config.AgentConfig` resolves
defaults -> course -> user, and before login resolves only defaults -> course.
Each key carries its own `editable_by` plus type/choices/min/max, so the
checks travel with the setting rather than living in each agent.
"""

from __future__ import annotations

from typing import Any

from ._common import IndexSpec, collection, nullable

ACTIONS = ["update", "replace", "delete"]
VALUE_TYPES = ["string", "int", "double", "bool", "array", "object"]
EDITABLE_BY = ["admin", "instructor", "student", "nobody"]
SCOPES = ["course", "user"]

REVISION_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["revision_id", "collection", "document_key", "action", "before", "actor_id", "actor_role", "recorded_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "revision_id": {"bsonType": "string", "description": "UUID4"},
        "collection": {"bsonType": "string", "description": "Which collection the document came from"},
        "document_key": {
            "bsonType": "object",
            "description": "The natural key of the changed document, ex "
                           "{'course_id': ..., 'lab_id': ...}. Not _id, which is "
                           "meaningless outside this database",
        },
        "course_id": {
            "bsonType": nullable("string"),
            "description": "Lifted out of document_key when present, so a course's "
                           "history is one indexed query",
        },
        "action": {"enum": ACTIONS},
        "before": {
            "bsonType": "object",
            "description": "The document EXACTLY as it was, _id stripped. The whole point",
        },
        "actor_id": {"bsonType": "string", "description": "users.id, or the agent's actor id"},
        "actor_role": {"bsonType": "string", "description": "The access.Role in force"},
        "recorded_at": {"bsonType": "date"},
    },
}

AGENT_CONFIG_DEFAULT_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["agent", "key", "value", "type", "editable_by", "seeded_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "agent": {"bsonType": "string", "description": "ex: guardian, lab_companion, assessment"},
        "key": {"bsonType": "string", "description": "Dotted setting name, unique per agent"},
        "value": {"description": "Any BSON type; `type` says which is expected"},
        "type": {"enum": VALUE_TYPES},
        "choices": {
            "bsonType": ["array", "null"],
            "description": "When set, an override must be one of these",
        },
        "minimum": {"bsonType": ["double", "int", "null"]},
        "maximum": {"bsonType": ["double", "int", "null"]},
        "editable_by": {
            "bsonType": "array",
            "items": {"enum": EDITABLE_BY},
            "description": "Who may override it. Empty or ['nobody'] means fixed",
        },
        "description": {"bsonType": "string"},
        "source_file": {
            "bsonType": nullable("string"),
            "description": "The defaults file this was seeded from",
        },
        "seeded_at": {"bsonType": "date"},
    },
}

AGENT_CONFIG_SCHEMA: dict[str, Any] = {
    "bsonType": "object",
    "required": ["config_id", "scope", "agent", "key", "value", "set_by", "updated_at"],
    "additionalProperties": False,
    "properties": {
        "_id": {},
        "config_id": {"bsonType": "string", "description": "UUID4"},
        "scope": {"enum": SCOPES, "description": "'course' is the instructor's; 'user' is personal"},
        "agent": {"bsonType": "string"},
        "key": {"bsonType": "string", "description": "Must exist in agent_config_defaults"},
        "value": {"description": "Checked against the default's type/choices/min/max"},
        "course_id": {
            "bsonType": nullable("string"),
            "description": "Required when scope is 'course'; the course a user override applies to",
        },
        "user_id": {"bsonType": nullable("string"), "description": "Required when scope is 'user'"},
        "set_by": {"bsonType": "string", "description": "users.id of whoever set it"},
        "updated_at": {"bsonType": "date"},
    },
}

REVISION_INDEXES: IndexSpec = [
    ([("revision_id", 1)], True, "Primary key"),
    ([("collection", 1), ("recorded_at", -1)], False, "history(): one collection's changes"),
    ([("course_id", 1), ("recorded_at", -1)], False, "history(): one course's changes"),
    ([("actor_id", 1), ("recorded_at", -1)], False, "Everything one actor changed"),
]

AGENT_CONFIG_DEFAULT_INDEXES: IndexSpec = [
    ([("agent", 1), ("key", 1)], True, "One default per setting"),
]

AGENT_CONFIG_INDEXES: IndexSpec = [
    ([("config_id", 1)], True, "Primary key"),
    ([("scope", 1), ("course_id", 1), ("user_id", 1), ("agent", 1), ("key", 1)],
     True, "One override per setting per scope — re-setting updates in place"),
    ([("agent", 1), ("key", 1)], False, "Who has overridden a given setting"),
]

COLLECTIONS: dict[str, dict[str, Any]] = {
    "revisions": collection(REVISION_SCHEMA, REVISION_INDEXES),
    "agent_config_defaults": collection(AGENT_CONFIG_DEFAULT_SCHEMA, AGENT_CONFIG_DEFAULT_INDEXES),
    "agent_configs": collection(AGENT_CONFIG_SCHEMA, AGENT_CONFIG_INDEXES),
}
