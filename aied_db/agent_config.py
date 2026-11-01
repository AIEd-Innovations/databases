"""Settings, resolved defaults -> course -> user.

Three layers, narrowest wins:

  1. `agent_config_defaults` — seeded from the read-only `aied_db/defaults.json`
     folder by `scripts/init_db.py`. Read-only at runtime.
  2. a course override an instructor set (`agent_configs`, scope "course")
  3. a personal override the user set (`agent_configs`, scope "user")

Before login there is no user, so `AgentConfig(db, agent, course_id=...)`
resolves layers 1-2 only; after login, pass `user_id` as well and layer 3
applies. That is the "before login: defaults + course, after login: + user"
rule from the Oct 26 meeting.

Each default carries its own rules — `editable_by`, `type`, `choices`,
`minimum`, `maximum` — so a new setting needs no new validation code, and
`set_*_override` refuses a value the default does not permit.

NOTE: as of v0.1.0 no agent reads this yet; they all still use hard-coded
values. Wiring them up is a separate task per repo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from ._util import new_id, now

#: Weight keys that must sum to 1.0 within one agent, per the Oct 26 rule
#: "grading weights must sum to 1".
WEIGHT_GROUPS: dict[str, tuple[str, ...]] = {
    "assessment": ("grading.code_weight", "grading.report_weight", "grading.manual_weight"),
}

#: Floating-point slack when checking that a weight group sums to 1.
WEIGHT_TOLERANCE = 1e-6

_BSON_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "int": (int,),
    "double": (float, int),
    "bool": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


class ConfigError(ValueError):
    """Raised when a setting does not exist, or a value is not permitted."""


@dataclass(frozen=True)
class Setting:
    """One resolved setting and where its value came from."""

    agent: str
    key: str
    value: Any
    source: str  # "default", "course" or "user"
    editable_by: tuple[str, ...]
    type: str


class AgentConfig:
    """Resolved settings for one agent, optionally for one course and user.

        config = AgentConfig(db, "guardian", course_id=course_id, user_id=user_id)
        threshold = config.get("escalation.violation_threshold")

    `db` may be guarded or raw; reads only need `find`.
    """

    def __init__(
        self,
        db,
        agent: str,
        *,
        course_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> None:
        self._db = db
        self._agent = agent
        self._course_id = course_id
        self._user_id = user_id

    # -------------------------------------------------------------- reading
    def defaults(self) -> dict[str, dict[str, Any]]:
        """Every default for this agent, by key."""
        cursor = self._db["agent_config_defaults"].find({"agent": self._agent})
        return {document["key"]: document for document in cursor}

    def _overrides(self, scope: str) -> dict[str, Any]:
        query: dict[str, Any] = {"agent": self._agent, "scope": scope}
        if scope == "course":
            if self._course_id is None:
                return {}
            query["course_id"] = self._course_id
        else:
            if self._user_id is None:
                return {}
            query["user_id"] = self._user_id
            # A personal override is still per-course when a course is in play.
            if self._course_id is not None:
                query["course_id"] = self._course_id
        cursor = self._db["agent_configs"].find(query)
        return {document["key"]: document["value"] for document in cursor}

    def resolve(self) -> dict[str, Setting]:
        """Every setting for this agent, with the winning layer recorded."""
        defaults = self.defaults()
        course = self._overrides("course")
        user = self._overrides("user")

        resolved: dict[str, Setting] = {}
        for key, default in defaults.items():
            value, source = default["value"], "default"
            if key in course:
                value, source = course[key], "course"
            if key in user:
                value, source = user[key], "user"
            resolved[key] = Setting(
                agent=self._agent,
                key=key,
                value=value,
                source=source,
                editable_by=tuple(default.get("editable_by") or ()),
                type=default["type"],
            )
        return resolved

    def get(self, key: str, default: Any = None) -> Any:
        """The winning value for `key`, or `default` when the key is unknown."""
        setting = self.resolve().get(key)
        return default if setting is None else setting.value

    def all(self) -> dict[str, Any]:
        """Every setting as a plain key -> value mapping."""
        return {key: setting.value for key, setting in self.resolve().items()}

    # ------------------------------------------------------------ validating
    def validate(self, key: str, value: Any, *, role: str) -> None:
        """Check one override against its default's rules. Raises `ConfigError`.

        `role` is the setter's role name (`Role.INSTRUCTOR.value` and so on),
        checked against the default's `editable_by`.
        """
        default = self._db["agent_config_defaults"].find_one(
            {"agent": self._agent, "key": key}
        )
        if default is None:
            raise ConfigError(
                f"{self._agent}.{key} is not a known setting. Add it to "
                f"aied_db/defaults.json (agent {self._agent!r}) first."
            )

        editable_by = list(default.get("editable_by") or ())
        if role not in editable_by:
            permitted = ", ".join(editable_by) or "nobody"
            raise ConfigError(
                f"{self._agent}.{key} is editable by {permitted}, not {role!r}"
            )

        expected = default["type"]
        allowed_types = _BSON_TYPE_CHECKS.get(expected, ())
        # bool is a subclass of int in Python; keep the two apart.
        if expected != "bool" and isinstance(value, bool):
            raise ConfigError(f"{self._agent}.{key} expects {expected}, got bool")
        if allowed_types and not isinstance(value, allowed_types):
            raise ConfigError(
                f"{self._agent}.{key} expects {expected}, got {type(value).__name__}"
            )

        choices = default.get("choices")
        if choices and value not in choices:
            raise ConfigError(
                f"{self._agent}.{key} must be one of {list(choices)}, got {value!r}"
            )

        minimum, maximum = default.get("minimum"), default.get("maximum")
        if minimum is not None and value < minimum:
            raise ConfigError(f"{self._agent}.{key} must be >= {minimum}, got {value!r}")
        if maximum is not None and value > maximum:
            raise ConfigError(f"{self._agent}.{key} must be <= {maximum}, got {value!r}")

    def check_weights(self, pending: Optional[Mapping[str, Any]] = None) -> None:
        """Verify this agent's weight group still sums to 1.0.

        `pending` is applied on top of the resolved values first, so a setter
        can test a change before writing it.
        """
        group = WEIGHT_GROUPS.get(self._agent)
        if not group:
            return

        values = self.all()
        if pending:
            values.update(pending)
        if not all(key in values for key in group):
            return  # the group is not fully seeded yet; nothing to check

        total = sum(float(values[key]) for key in group)
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            parts = ", ".join(f"{key}={values[key]}" for key in group)
            raise ConfigError(
                f"{self._agent} weights must sum to 1.0, got {total:g} ({parts})"
            )

    # -------------------------------------------------------------- writing
    def set_course_override(self, key: str, value: Any, *, set_by: str, role: str) -> None:
        """Set a course-wide override. Instructor-level, by default."""
        if self._course_id is None:
            raise ConfigError("a course override needs course_id on the AgentConfig")
        self.validate(key, value, role=role)
        self.check_weights({key: value})
        self._upsert("course", key, value, set_by=set_by)

    def set_user_override(self, key: str, value: Any, *, set_by: str, role: str) -> None:
        """Set a personal override for this user."""
        if self._user_id is None:
            raise ConfigError("a user override needs user_id on the AgentConfig")
        self.validate(key, value, role=role)
        self.check_weights({key: value})
        self._upsert("user", key, value, set_by=set_by)

    def _upsert(self, scope: str, key: str, value: Any, *, set_by: str) -> None:
        query = {
            "scope": scope,
            "agent": self._agent,
            "key": key,
            "course_id": self._course_id,
            "user_id": self._user_id if scope == "user" else None,
        }
        self._db["agent_configs"].update_one(
            query,
            {
                "$set": {**query, "value": value, "set_by": set_by, "updated_at": now()},
                "$setOnInsert": {"config_id": new_id()},
            },
            upsert=True,
        )


def seed_defaults(db, documents: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Write the packaged `defaults.json` into `agent_config_defaults`.

    Idempotent: re-running updates each default in place rather than
    duplicating it. Called by `scripts/init_db.py`, which holds the admin
    role — no other role may write this collection.
    """
    inserted = updated = 0
    for document in documents:
        result = db["agent_config_defaults"].update_one(
            {"agent": document["agent"], "key": document["key"]},
            {"$set": {**dict(document), "seeded_at": now()}},
            upsert=True,
        )
        if getattr(result, "upserted_id", None) is not None:
            inserted += 1
        else:
            updated += 1
    return {"inserted": inserted, "updated": updated}


def load_defaults(path: Path | None = None) -> list[dict[str, Any]]:
    """Read the packaged `defaults.json` into `agent_config_defaults` documents.

    The file holds one entry per agent: `{"agents": [{"agent": ...,
    "settings": [...]}]}`. Keys starting with `_` are comments and are
    skipped. `source_file` records where each default came from, so a seeded
    value can be traced back.

    `path` is for tests; leave it unset to read the file that ships with the
    package.
    """
    path = Path(path) if path is not None else defaults_path()
    if not path.is_file():
        raise FileNotFoundError(f"no defaults file at {path}")

    data = json.loads(path.read_text())
    documents: list[dict[str, Any]] = []
    for entry in data["agents"]:
        agent = entry["agent"]
        for setting in entry["settings"]:
            if setting.get("key", "").startswith("_"):
                continue
            documents.append(
                {
                    "agent": agent,
                    "key": setting["key"],
                    "value": setting["value"],
                    "type": setting["type"],
                    "choices": setting.get("choices"),
                    "minimum": setting.get("minimum"),
                    "maximum": setting.get("maximum"),
                    "editable_by": list(setting.get("editable_by") or []),
                    "description": setting.get("description", ""),
                    "source_file": path.name,
                }
            )
    return documents


def defaults_path() -> Path:
    """The `defaults.json` that ships inside this package."""
    return Path(__file__).resolve().parent / "defaults.json"


__all__ = [
    "AgentConfig",
    "ConfigError",
    "Setting",
    "WEIGHT_GROUPS",
    "defaults_path",
    "load_defaults",
    "seed_defaults",
]
