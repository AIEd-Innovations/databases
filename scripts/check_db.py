#!/usr/bin/env python3
"""Report what is actually on the server, without changing anything.

Read-only: every call here is a query or a command that reads. Run it before
`init_db.py` to see what you are connecting to, and after, to confirm what
landed.

    python scripts/check_db.py

The connection string is never printed — only the host, so a screenshot or a
pasted terminal cannot leak the password.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aied_db._env import load_env_file, uri_problem  # noqa: E402

# Read .env before anything touches os.environ for MONGODB_URI.
load_env_file()

from aied_db import client, schema  # noqa: E402


def _tick(ok: bool) -> str:
    return "ok  " if ok else "MISS"


def _diagnose(message: str) -> str:
    """Turn a driver error into the thing to go and change."""
    lowered = message.lower()
    if "does not exist" in lowered or "dns" in lowered:
        return (
            "  The cluster hostname does not resolve. Copy the string again from\n"
            "  Atlas > Connect > Drivers > Python — the host looks like\n"
            "  myname.ab12cde.mongodb.net, not cluster.mongodb.net."
        )
    if "auth" in lowered or "password" in lowered:
        return (
            "  Authentication failed. Check the database user's password in\n"
            "  Atlas > Database Access, and that <db_password> was replaced."
        )
    if "timed out" in lowered or "serverselection" in lowered.replace(" ", ""):
        return (
            "  Timed out before reaching the server. Add your current IP in\n"
            "  Atlas > Network Access > IP Access List. That is the usual cause."
        )
    return "  Check Atlas > Network Access and Atlas > Database Access."


def main() -> int:
    problem = uri_problem(os.environ.get("MONGODB_URI", ""))
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1

    try:
        client.ping()
    except client.ConnectionError_ as exc:
        print(f"error: {exc}", file=sys.stderr)
        print(_diagnose(str(exc)), file=sys.stderr)
        return 1

    database = client.get_db()
    admin = database.client["admin"]

    build = admin.command("buildInfo")
    # The hosts are safe to print; the URI is not.
    nodes = ", ".join(f"{host}:{port}" for host, port in (database.client.nodes or []))
    print(f"server      MongoDB {build.get('version')}")
    print(f"nodes       {nodes or 'unknown'}")
    try:
        hello = admin.command("hello")
        if hello.get("setName"):
            print(f"replica set {hello['setName']}")
        print(f"topology    {'Atlas / replica set' if hello.get('setName') else 'standalone'}")
    except Exception:  # pragma: no cover - older servers
        pass
    print(f"database    {database.name}")

    existing = set(database.list_collection_names())
    print(f"\ncollections {len(existing & set(schema.COLLECTIONS))} of {len(schema.COLLECTIONS)} present")

    missing_validator = []
    wrong_validator = []
    missing_indexes: dict[str, list[str]] = {}

    for name in sorted(schema.COLLECTIONS):
        if name not in existing:
            print(f"  MISS {name}")
            continue

        options = database[name].options()
        validator = options.get("validator")
        if not validator:
            missing_validator.append(name)
        elif validator != schema.validator_for(name):
            wrong_validator.append(name)

        have = set(database[name].index_information())
        want = {schema.index_name(keys) for keys, _u, _w in schema.COLLECTIONS[name]["indexes"]}
        absent = sorted(want - have)
        if absent:
            missing_indexes[name] = absent

        level = options.get("validationLevel", "-")
        print(
            f"  {_tick(bool(validator) and name not in wrong_validator)} {name:24} "
            f"validator={'yes' if validator else 'NO':3} level={level:8} "
            f"indexes={len(have)}/{len(want) + 1} docs={database[name].estimated_document_count()}"
        )

    if missing_validator:
        print(f"\nno validator:      {', '.join(missing_validator)}")
    if wrong_validator:
        print(f"validator is STALE: {', '.join(wrong_validator)}")
        print("  -> re-run python scripts/init_db.py")
    for name, absent in missing_indexes.items():
        print(f"missing indexes on {name}: {', '.join(absent)}")

    print("\nvector search")
    for collection_name in schema.SEARCH_INDEXES:
        if collection_name not in existing:
            print(f"  MISS {collection_name} does not exist yet")
            continue
        try:
            found = list(database[collection_name].list_search_indexes())
            if not found:
                print(f"  none on {collection_name} -> run init_db.py (needs Atlas)")
            for index in found:
                print(f"  {index.get('name')}: status={index.get('status')} queryable={index.get('queryable')}")
        except Exception as exc:
            print(f"  unavailable on {collection_name}: {str(exc).splitlines()[0]}")
            print("  -> $vectorSearch needs Atlas; everything except RAG retrieval still works")

    defaults = database["agent_config_defaults"].estimated_document_count() if "agent_config_defaults" in existing else 0
    revisions = database["revisions"].estimated_document_count() if "revisions" in existing else 0
    print(f"\nseeded defaults {defaults}")
    print(f"revisions       {revisions}")

    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
