#!/usr/bin/env python3
"""Create or update the `aied` database: validators, indexes, defaults.

Idempotent — run it on every deploy. It never drops a collection and never
deletes a document.

    python scripts/init_db.py              # apply schema + indexes + seed defaults
    python scripts/init_db.py --ping       # just check the connection, change nothing
    python scripts/init_db.py --show       # print what WOULD be applied, change nothing
    python scripts/init_db.py --strict     # validationLevel "strict" instead of "moderate"
    python scripts/init_db.py --roles      # also create the Atlas custom roles
    python scripts/init_db.py --no-vector  # skip the Atlas vector-search index

Reads MONGODB_URI and MONGODB_DB from the environment (keep them in .env,
which is gitignored). Seeding `agent_config_defaults` is an admin write, so
this script holds Role.ADMIN — it is the only thing that does.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pymongo.errors import OperationFailure  # noqa: E402
from aied_db._env import load_env_file, uri_problem  # noqa: E402

# Read .env before anything touches os.environ for MONGODB_URI.
load_env_file()

from aied_db import Role, client, guarded, schema  # noqa: E402
from aied_db.access import atlas_roles  # noqa: E402
from aied_db.agent_config import defaults_path, load_defaults, seed_defaults  # noqa: E402

ACTOR = "init_db"


def show() -> None:
    """Print the plan without touching the server."""
    print(f"database: {client.db_name()}")
    print(f"collections: {len(schema.COLLECTIONS)}\n")
    for name in sorted(schema.COLLECTIONS):
        spec = schema.COLLECTIONS[name]
        required = spec["schema"].get("required", [])
        print(f"  {name}")
        print(f"      owner    {schema.OWNERS[name]}")
        print(f"      required {len(required)} fields, {len(spec['schema']['properties'])} total")
        for keys, unique, why in spec["indexes"]:
            flag = "unique" if unique else "      "
            print(f"      index    {flag} {schema.index_name(keys):52} {why}")
    for collection_name, definitions in schema.SEARCH_INDEXES.items():
        for definition in definitions:
            print(f"\n  search index {collection_name}.{definition['name']} ({definition['type']})")

    defaults = load_defaults()
    print(f"\ndefaults to seed: {len(defaults)} settings from {defaults_path()}")


NOT_AUTHORIZED_HELP = """
  The database user can read and write but not change collection settings.
  Applying a validator to an existing collection runs collMod, which Atlas's
  "Read and write to any database" role does not include.

  Fix, in Atlas > Database Access > your user > Edit:
    Specific Privileges > Add: dbAdmin  on database  {db}
  (or temporarily "Atlas admin"), save, wait ~1 minute, re-run this script.
  Nothing was dropped; re-running is safe.
"""


def apply(*, validation_level: str, with_vector: bool, with_roles: bool) -> int:
    database = client.get_db()
    print(f"connected to {database.name} (validationLevel={validation_level})")

    try:
        results = schema.ensure_all(database, validation_level=validation_level)
    except OperationFailure as exc:
        if exc.code == 13:  # Unauthorized
            print(f"\nerror: {exc.details.get('errmsg', exc) if exc.details else exc}", file=sys.stderr)
            print(NOT_AUTHORIZED_HELP.format(db=database.name), file=sys.stderr)
            return 1
        raise
    created = sum(1 for action in results.values() if action == "created")
    print(f"collections: {created} created, {len(results) - created} updated")
    for name in sorted(results):
        print(f"  {results[name]:8} {name}")

    if with_vector:
        print("\nAtlas vector search:")
        for label, outcome in schema.ensure_search_indexes(database).items():
            print(f"  {outcome:12} {label}")
            if outcome.startswith("unavailable"):
                print(
                    "    note: $vectorSearch needs Atlas. Everything else is in place; "
                    "RAG retrieval will not work until this index exists."
                )

    # Seeding defaults is an admin write, and goes through the guard so it is
    # recorded in revisions like any other.
    admin_db = guarded(database, Role.ADMIN, actor_id=ACTOR)
    counts = seed_defaults(admin_db, load_defaults())
    print(f"\nagent_config_defaults: {counts['inserted']} inserted, {counts['updated']} updated")

    if with_roles:
        print("\nAtlas custom roles:")
        failures = 0
        for definition in atlas_roles(database.name):
            try:
                database.client["admin"].command(
                    "createRole",
                    definition["role"],
                    privileges=definition["privileges"],
                    roles=definition["roles"],
                )
                print(f"  created      {definition['role']}")
            except Exception as exc:  # noqa: BLE001 - the server's message is the useful part
                message = str(exc)
                if "already exists" in message:
                    print(f"  exists       {definition['role']}")
                elif "not authorized on admin" in message:
                    # Atlas reserves createRole for its own control plane, so
                    # this fails even as atlasAdmin. Say so once, not ten
                    # times with the whole privilege list attached.
                    print(
                        "  unavailable  Atlas does not allow createRole over a driver "
                        "connection,\n               even for atlasAdmin.\n\n"
                        "  Use the Atlas Admin API instead:\n"
                        "      python scripts/atlas_roles.py --print   # the payloads\n"
                        "      python scripts/atlas_roles.py --create  # needs an Atlas API key\n"
                    )
                    break
                else:
                    failures += 1
                    print(f"  failed       {definition['role']}: {message.splitlines()[0]}")
        if failures:
            print(
                "  note: custom roles need an Atlas tier and a user that may grant them. "
                "They are optional — the guarded() wrapper enforces POLICY either way."
            )

    print("\ndone")
    return 0


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
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ping", action="store_true", help="check the connection and exit")
    parser.add_argument("--show", action="store_true", help="print the plan without applying it")
    parser.add_argument("--strict", action="store_true", help='use validationLevel "strict"')
    parser.add_argument("--roles", action="store_true", help="also create the Atlas custom roles")
    parser.add_argument("--no-vector", action="store_true", help="skip the vector-search index")
    args = parser.parse_args()

    if args.show:
        show()
        return 0

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
    print("ping ok")
    if args.ping:
        return 0

    try:
        return apply(
            validation_level="strict" if args.strict else schema.DEFAULT_VALIDATION_LEVEL,
            with_vector=not args.no_vector,
            with_roles=args.roles,
        )
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
