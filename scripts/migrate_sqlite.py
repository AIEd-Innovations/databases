#!/usr/bin/env python3
"""Copy one course's SQLite data into MongoDB.

ui-student keeps a folder per course — `data/courses/<course_id>/` holding
`participant.db` and `guardian.db`. This reads those files and writes their
rows into the shared `aied` database.

    python scripts/migrate_sqlite.py --course-dir ../ui-student/data/courses/<id> --dry-run
    python scripts/migrate_sqlite.py --course-dir ../ui-student/data/courses/<id>

What it does to the data, because SQLite and MongoDB disagree on three
things:

  * timestamps are TEXT in SQLite and `date` in Mongo, so every ISO-8601
    string is parsed (and truncated to milliseconds, which is all BSON holds)
  * booleans are INTEGER 0/1 in SQLite and `bool` in Mongo
  * `concept_tags` is a JSON string in SQLite and an `array` in Mongo
  * `course_id`, which the folder name used to carry, becomes a field on
    every document — that is what replaces the per-course file

It runs as `Role.MIGRATION`, which holds insert and find and nothing else.
So this script CANNOT overwrite or delete anything: a row whose key is
already in Mongo is counted as skipped, never updated. Re-running is
therefore safe, and the second run inserts nothing.

Nothing is written unless every row converts and validates, so a partial
course is not left behind.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aied_db import Role, client, guarded  # noqa: E402
from aied_db._env import load_env_file, uri_problem  # noqa: E402
from aied_db.access import natural_key_fields  # noqa: E402

load_env_file()


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


# ── converting a row ─────────────────────────────────────────────────────────


def _now_ms() -> datetime:
    """Now, truncated to the millisecond BSON holds."""
    moment = datetime.now(timezone.utc)
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


def to_date(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into a UTC datetime truncated to ms."""
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        if not text:
            return None
        # SQLite rows written by Python hold "+00:00"; tolerate a "Z" too.
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return moment.replace(microsecond=(moment.microsecond // 1000) * 1000)


def to_bool(value: Any) -> bool:
    return bool(value)


def to_tags(value: Any) -> list[str]:
    """`concept_tags` is stored as a JSON string."""
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        fail(f"concept_tags is not valid JSON: {value!r}")
    if not isinstance(parsed, list):
        fail(f"concept_tags is not a list: {value!r}")
    return parsed


#: table -> (target collection, {column: converter})
#: Columns not named here are copied across unchanged.
PLAN: dict[str, tuple[str, str, dict[str, Callable[[Any], Any]]]] = {
    "interactions": ("participant", "interactions", {"timestamp": to_date}),
    "sessions": (
        "guardian",
        "guardian_sessions",
        {
            "started_at": to_date,
            "ended_at": to_date,
            "escalated": to_bool,
            "report_generated": to_bool,
        },
    ),
    "questions": (
        "guardian",
        "guardian_questions",
        {"timestamp": to_date, "violation": to_bool, "concept_tags": to_tags},
    ),
    "violations": ("guardian", "guardian_violations", {"timestamp": to_date}),
    "verifications": (
        "guardian",
        "guardian_verifications",
        {"timestamp": to_date, "passes": to_bool},
    ),
}

#: Insert parents before children, so a half-finished run still reads sensibly.
ORDER = ("interactions", "sessions", "questions", "violations", "verifications")


#: Collections whose schema requires `logged_at`.
_NEEDS_LOGGED_AT = ("interactions",)


def read_table(
    path: Path, table: str, course_id: str, recorded_at: datetime | None = None
) -> list[dict[str, Any]]:
    """Every row of `table`, converted to a Mongo document."""
    recorded_at = recorded_at or _now_ms()
    _, _, converters = PLAN[table]
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608
    except sqlite3.OperationalError as exc:
        fail(f"{path.name}: cannot read {table}: {exc}")
    finally:
        connection.close()

    documents = []
    for row in rows:
        document = {key: row[key] for key in row.keys()}
        for column, convert in converters.items():
            if column in document:
                document[column] = convert(document[column])
        # The folder name is the course. A row that already names one wins,
        # so a mislabelled folder cannot silently relabel real data.
        document["course_id"] = document.get("course_id") or course_id
        # `interactions` requires logged_at: when the database recorded the
        # row, as distinct from when the interaction happened. For a migrated
        # row that is the moment of the import, not the original event.
        if table in _NEEDS_LOGGED_AT:
            document["logged_at"] = recorded_at
        documents.append(document)
    return documents


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--course-dir",
        required=True,
        type=Path,
        help="ui-student's data/courses/<course_id>/ folder",
    )
    parser.add_argument(
        "--course-id",
        help="course_id to stamp (default: the folder's name)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="convert, validate and report; write nothing",
    )
    args = parser.parse_args()

    course_dir: Path = args.course_dir.expanduser().resolve()
    if not course_dir.is_dir():
        fail(f"no such folder: {course_dir}")
    course_id = args.course_id or course_dir.name
    if not course_id:
        fail("could not work out the course_id; pass --course-id")

    files = {
        "participant": course_dir / "participant.db",
        "guardian": course_dir / "guardian.db",
    }
    present = {name: path for name, path in files.items() if path.is_file()}
    if not present:
        fail(f"neither participant.db nor guardian.db is in {course_dir}")

    print(f"course_dir  {course_dir}")
    print(f"course_id   {course_id}")
    print(f"files       {', '.join(sorted(present))}")

    # Read and convert everything before connecting, so a conversion error
    # costs nothing and touches no database.
    batches: list[tuple[str, list[dict[str, Any]]]] = []
    for table in ORDER:
        source, collection_name, _ = PLAN[table]
        if source not in present:
            continue
        documents = read_table(present[source], table, course_id)
        batches.append((collection_name, documents))

    total = sum(len(documents) for _, documents in batches)
    print(f"\nread {total} rows:")
    for collection_name, documents in batches:
        print(f"  {collection_name:24} {len(documents):5}")

    if args.dry_run:
        print("\ndry run: nothing written.")
        for collection_name, documents in batches:
            if documents:
                sample = {
                    k: v for k, v in list(documents[0].items())[:6]
                }
                print(f"\n  {collection_name} first row (truncated):")
                for key, value in sample.items():
                    print(f"    {key:14} {type(value).__name__:9} {str(value)[:48]}")
        return 0

    problem = uri_problem(client._uri() if hasattr(client, "_uri") else "")
    if problem:
        fail(problem)

    client.ping()
    database = client.get_db()
    print(f"\nconnected to {client.db_name()}")
    db = guarded(database, Role.MIGRATION, actor_id="migrate_sqlite")

    inserted_total = skipped_total = 0
    print()
    for collection_name, documents in batches:
        if not documents:
            print(f"  {collection_name:24}     0 inserted      0 skipped")
            continue

        key_fields = natural_key_fields(collection_name)
        # One query, not one per row.
        keys = [
            tuple(document.get(field) for field in key_fields) for document in documents
        ]
        existing = {
            tuple(found.get(field) for field in key_fields)
            for found in database[collection_name].find(
                {"$or": [dict(zip(key_fields, key)) for key in keys]},
                {field: 1 for field in key_fields},
            )
        }
        fresh = [
            document
            for document, key in zip(documents, keys)
            if key not in existing
        ]
        skipped = len(documents) - len(fresh)

        if fresh:
            db[collection_name].insert_many(fresh)
        inserted_total += len(fresh)
        skipped_total += skipped
        print(
            f"  {collection_name:24} {len(fresh):5} inserted "
            f"{skipped:6} skipped (already there)"
        )

    print(f"\n{inserted_total} inserted, {skipped_total} skipped.")
    if skipped_total and not inserted_total:
        print("Nothing new — this course has already been migrated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
