#!/usr/bin/env python3
"""Create the per-agent Atlas custom roles, via the Atlas Admin API.

`init_db.py --roles` cannot do this. Atlas reserves `createRole` for its own
control plane, so a driver connection — even one authenticated as
`atlasAdmin` — is refused:

    not authorized on admin to execute command { createRole: ... }

Custom roles have to be created through the Atlas Admin API (or by hand in
the UI, which means re-typing 25 collections ten times). This script does it.

    python scripts/atlas_roles.py --print              # the payloads, no network
    python scripts/atlas_roles.py --print --role aied_guardian_agent
    python scripts/atlas_roles.py --create             # create them in Atlas

`--create` needs an Atlas API key with the **Project Owner** role, and the
project id, in your `.env`:

    ATLAS_PUBLIC_KEY=xxxxxxxx
    ATLAS_PRIVATE_KEY=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    ATLAS_GROUP_ID=xxxxxxxxxxxxxxxxxxxxxxxx

Get them in Atlas: Project Settings -> Access Manager -> Applications ->
API Keys -> Create API Key. The project id is in the URL of your project, or
under Project Settings -> General.

These keys administer the whole project. They are not the database password
and no agent should ever hold them.

Creating a role grants nothing on its own. Assign it to a database user —
one user per agent — in Database Access -> Add New Database User -> Built-in
Role -> Custom Roles.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aied_db._env import load_env_file  # noqa: E402
from aied_db.access import atlas_roles  # noqa: E402
from aied_db.client import db_name  # noqa: E402

load_env_file()

BASE = "https://cloud.mongodb.com/api/atlas/v2"
ACCEPT = "application/vnd.atlas.2023-01-01+json"

#: The driver's action names are lowercase; the Admin API wants them upper.
ACTIONS = {"find": "FIND", "insert": "INSERT", "update": "UPDATE", "remove": "REMOVE"}


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def to_api_payload(definition: dict[str, Any]) -> dict[str, Any]:
    """Convert one `atlas_roles()` entry into an Admin API custom role.

    The driver groups privileges by collection; the Admin API groups them by
    action, each with the resources it applies to. Same information, inverted.
    """
    by_action: dict[str, list[dict[str, str]]] = {}
    for privilege in definition["privileges"]:
        resource = privilege["resource"]
        for action in privilege["actions"]:
            api_action = ACTIONS.get(action)
            if api_action is None:
                fail(f"no Atlas Admin API name for the action {action!r}")
            by_action.setdefault(api_action, []).append(
                {"db": resource["db"], "collection": resource["collection"]}
            )

    return {
        "roleName": definition["role"],
        "actions": [
            {"action": action, "resources": resources}
            for action, resources in sorted(by_action.items())
        ],
        "inheritedRoles": [
            {"role": inherited["role"], "db": inherited["db"]}
            for inherited in definition.get("roles", [])
        ],
    }


def request(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    """One Admin API call, with the digest auth Atlas requires."""
    public = os.environ.get("ATLAS_PUBLIC_KEY")
    private = os.environ.get("ATLAS_PRIVATE_KEY")
    if not public or not private:
        fail(
            "ATLAS_PUBLIC_KEY and ATLAS_PRIVATE_KEY are not set. See the top of "
            "this file for where to get them."
        )

    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, BASE, public, private)
    opener = urllib.request.build_opener(
        urllib.request.HTTPDigestAuthHandler(manager)
    )

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Accept", ACCEPT)
    if data:
        req.add_header("Content-Type", ACCEPT)

    try:
        with opener.open(req, timeout=30) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()
    except urllib.error.URLError as exc:
        fail(f"cannot reach the Atlas Admin API: {exc.reason}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--print", action="store_true", dest="show",
                       help="print the payloads and exit; touches no network")
    group.add_argument("--create", action="store_true",
                       help="create the roles in Atlas")
    parser.add_argument("--role", help="only this one role")
    parser.add_argument("--db", default=None,
                        help="database the roles apply to (default: MONGODB_DB)")
    args = parser.parse_args()

    database = args.db or db_name()
    definitions = atlas_roles(database)
    if args.role:
        definitions = [d for d in definitions if d["role"] == args.role]
        if not definitions:
            fail(f"no such role: {args.role}")

    payloads = [to_api_payload(definition) for definition in definitions]

    if args.show:
        print(f"# {len(payloads)} custom roles for the '{database}' database.")
        print("# POST each to "
              f"{BASE}/groups/$ATLAS_GROUP_ID/customDBRoles/roles")
        print(json.dumps(payloads, indent=2))
        return 0

    group_id = os.environ.get("ATLAS_GROUP_ID")
    if not group_id:
        fail("ATLAS_GROUP_ID is not set (Atlas -> Project Settings -> General).")

    path = f"/groups/{group_id}/customDBRoles/roles"
    created = existed = failed = 0
    for payload in payloads:
        name = payload["roleName"]
        status, text = request("POST", path, payload)
        if status in (200, 201):
            print(f"  created   {name}")
            created += 1
        elif status == 409:
            # Already there. Bring it up to date rather than leaving a stale one.
            status, text = request("PATCH", f"{path}/{name}", payload)
            if status in (200, 201):
                print(f"  updated   {name}")
                existed += 1
            else:
                print(f"  FAILED    {name}: {status} {text[:160]}")
                failed += 1
        else:
            print(f"  FAILED    {name}: {status} {text[:160]}")
            failed += 1

    print(f"\n{created} created, {existed} updated, {failed} failed.")
    if failed:
        print(
            "\nA 401 means the API key is wrong; a 403 means it lacks the "
            "Project Owner role.",
            file=sys.stderr,
        )
        return 1

    print(
        "\nRoles exist but grant nothing yet. Next: Atlas -> Database Access -> "
        "Add New Database User, one per agent, Custom Roles -> the matching "
        "aied_* role."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
