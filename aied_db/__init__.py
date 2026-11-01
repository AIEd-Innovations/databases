"""aied_db — the shared MongoDB layer for the Agentic Design Lab Framework.

One database (`aied`), 25 collections, each with a `$jsonSchema` validator
and its indexes. Every agent in the AIEd-Innovations org reads and writes
through here instead of its own SQLite file, JSON folder or Cosmos container.

Typical use in a service:

    from aied_db import Role, get_db, guarded, ping

    ping()                      # at startup, before serving traffic
    db = guarded(get_db(), Role.ASSESSMENT_AGENT, actor_id="assessment_agent")

Never `get_db()` on its own in a service: `guarded()` is what applies the
access rules and records revisions.
"""

from ._util import IntegrityError, new_id, now, strip_id, to_dt, to_iso
from .access import POLICY, GuardedDatabase, PermissionError_, Role, atlas_roles, guarded, history
from .agent_config import AgentConfig
from .client import close, db_name, get_client, get_db, ping
from .schema import COLLECTIONS, ensure_all, ensure_search_indexes

__version__ = "0.1.0"

__all__ = [
    "AgentConfig",
    "COLLECTIONS",
    "GuardedDatabase",
    "IntegrityError",
    "POLICY",
    "PermissionError_",
    "Role",
    "__version__",
    "atlas_roles",
    "close",
    "db_name",
    "ensure_all",
    "ensure_search_indexes",
    "get_client",
    "get_db",
    "guarded",
    "history",
    "new_id",
    "now",
    "ping",
    "strip_id",
    "to_dt",
    "to_iso",
]
