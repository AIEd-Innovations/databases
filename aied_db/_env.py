"""Load a `.env` file into the environment, for this repo's own scripts.

The LIBRARY never does this: `client.py` reads `os.environ` and nothing else,
because a service owns its own configuration and should not have a package
reading files behind its back. ui-student and assessment_agent already load
their own `.env`.

The SCRIPTS here do need it: `python scripts/check_db.py` should work right
after someone writes `.env`, without them having to export anything first.

Deliberately dependency-free — `pymongo` stays the only runtime requirement,
so this is a small parser rather than python-dotenv.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

DEFAULT_FILENAME = ".env"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_env_file(path: Optional[Path] = None, *, override: bool = False) -> list[str]:
    """Read KEY=VALUE lines into `os.environ`. Returns the names it set.

    A variable already present in the environment wins unless `override` is
    true: an explicitly exported value should beat a stale file.

    Understands what people actually write in a `.env`: comments, blank
    lines, `export KEY=value`, and single or double quotes around the value
    (Atlas connection strings contain `&` and `?`, so they are usually
    quoted).
    """
    path = Path(path) if path is not None else repo_root() / DEFAULT_FILENAME
    if not path.is_file():
        return []

    loaded: list[str] = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key:
            continue
        if key in os.environ and not override:
            continue

        os.environ[key] = value
        loaded.append(key)

    return loaded


#: Placeholders that appear in this repo's own .env.example and in the string
#: Atlas hands you ("<db_password>"). Leaving one in is the most common setup
#: mistake, and the resulting DNS or auth error does not say so.
_PLACEHOLDERS = (
    "USER:PASSWORD",
    "<db_password>",
    "<password>",
    "<username>",
    "CLUSTER.mongodb.net",
    "cluster.mongodb.net",
    "username:password",
)


def uri_problem(uri: str) -> Optional[str]:
    """Describe what is obviously wrong with a connection string, or None.

    Checked before connecting, because an unreplaced placeholder fails with a
    DNS or authentication error that points nowhere near the real cause.
    """
    if not uri:
        return (
            "MONGODB_URI is empty. Put the Atlas connection string in .env:\n"
            "  Atlas > your cluster > Connect > Drivers > Python"
        )
    if not uri.startswith(("mongodb://", "mongodb+srv://")):
        return f"MONGODB_URI does not look like a connection string (starts with {uri[:12]!r})"

    for placeholder in _PLACEHOLDERS:
        if placeholder in uri:
            return (
                f"MONGODB_URI still contains the placeholder {placeholder!r}.\n"
                "  Replace the whole host and credentials with the string from\n"
                "  Atlas > your cluster > Connect > Drivers > Python, then put your\n"
                "  database user's real password where <db_password> is."
            )
    return None
