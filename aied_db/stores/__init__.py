"""Drop-in Mongo replacements for ui-student's SQLite stores.

Same protocols (`agentic_system/store/base.py`), same dict shapes. The swap
in `agentic_system/api.py` and `server/main.py` is a constructor change:

    from aied_db import Role, get_db, guarded
    from aied_db.stores import MongoGuardianStore, MongoParticipantStore

    db = guarded(get_db(), Role.GUARDIAN_AGENT, actor_id="guardian_agent")
    g_store = MongoGuardianStore(db, course_id=course_id)

The participant and guardian stores are per-course; `MongoSystemStore` is not.
"""

from .guardian import MongoGuardianStore
from .participant import MongoParticipantStore
from .system import MongoSystemStore

__all__ = ["MongoGuardianStore", "MongoParticipantStore", "MongoSystemStore"]
