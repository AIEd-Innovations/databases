# aied-db

The shared MongoDB layer for the CSU-AIEIC **Agentic Design Lab Framework**.

One database (`aied`), 25 collections, each with a `$jsonSchema` validator and
its indexes. Every agent in the [AIEd-Innovations](https://github.com/AIEd-Innovations)
org reads and writes through this package instead of its own SQLite file, JSON
folder or Cosmos container.

```
pip install "aied-db @ git+https://github.com/AIEd-Innovations/databases.git@v0.1.0"
```

## Quickstart

```python
from aied_db import Role, get_db, guarded, ping

ping()                       # at startup, before serving traffic
db = guarded(get_db(), Role.ASSESSMENT_AGENT, actor_id="assessment_agent")
```

Never use `get_db()` on its own in a service. `guarded()` is what applies the
access rules and records revisions; a raw database bypasses both.

Configuration is environment-only — copy `.env.example` to `.env`:

```
MONGODB_URI='mongodb+srv://USER:PASSWORD@CLUSTER.mongodb.net/?retryWrites=true&w=majority'
MONGODB_DB=aied
```

`.env` is gitignored. It holds the Atlas password; never commit it.

## Setting it up

```bash
bash scripts/create_db.sh          # venv, install, connect, create everything, test
python scripts/check_db.py         # read-only: what is actually on the server
python scripts/init_db.py --show   # print the plan, change nothing
python scripts/init_db.py --ping   # check the connection only
python scripts/init_db.py --strict # validationLevel "strict", once the data is clean
python scripts/atlas_roles.py --print   # the per-agent Atlas roles, as API payloads
python scripts/atlas_roles.py --create  # create them (needs an Atlas API key)
```

`check_db.py` reports the server version, which collections exist, whether
each validator is present and current, which indexes are missing and the
state of the vector index. It changes nothing and never prints the
connection string — only the host.

Everything is idempotent: re-running never drops a collection or deletes a
document.

## What is in here

```
aied_db/
  client.py       get_client() / get_db() / ping() / close(); one pool per process
  _util.py        to_dt / to_iso, IntegrityError, strip_id, new_id, now
  access.py       Role, POLICY, guarded(), history(), atlas_roles()
  agent_config.py AgentConfig: defaults <- course <- user
  schema/         one module per agent, merged into a registry in __init__.py
  stores/         MongoSystemStore, MongoParticipantStore, MongoGuardianStore
  repos/          AssessmentRepo, IntegrityRepo, LabCompanionRepo
  defaults.json   read-only factory settings for every agent (mode 444)
scripts/          init_db.py, check_db.py, atlas_roles.py, migrate_sqlite.py, gen_docs.py
docs/             integration.md, collections.md (GENERATED), open-questions.md
tests/            382 tests: mongomock, a $jsonSchema checker, SQLite parity
```

| Group | Collections |
| --- | --- |
| `system` | users, courses, enrollments |
| `curriculum` | labs, lab_files |
| `participant_guardian` | interactions, guardian_sessions, guardian_questions, guardian_violations, guardian_verifications |
| `lab_companion` | rag_documents, rag_vectors, student_files, rag_queries |
| `assessment` | assignments, assignment_files, assessment_results |
| `integrity` | integrity_sessions, integrity_records, integrity_violations, integrity_escalations, integrity_reports |
| `platform` | revisions, agent_config_defaults, agent_configs |

Field-by-field tables are in [docs/collections.md](docs/collections.md), which
is **generated** — run `python scripts/gen_docs.py` after any schema change.

**If you own another repo in the org, start with
[docs/integration.md](docs/integration.md)** — install, env, roles, the
store/repo for your agent, and how to move your existing data.

## The rules (from the Oct 26 meeting)

Enforced in `access.py`. Don't weaken them.

1. **Agents never overwrite human data.** Instructor uploads, student files,
   grading specs, reference solutions and courses are read-only to every agent.
2. **Agents append their own output,** and may update only *named fields* of
   their own records — the guardian's session counters, the lab draft's
   content. An update touching anything else is refused.
3. **Agents never delete.** No agent role holds `delete` on any collection.
4. **Nothing is lost.** Every update, replace and delete copies the prior
   document into `revisions` first, and nobody — including admin — may edit or
   delete a revision.

Humans write their own data. Agents cannot approve a lab, review a grade,
resolve their own escalation, or change a setting.

`POLICY` in `access.py` is the single place to change who can write what.

## Secrets

Two things must never reach a student:

- `assignments.criteria[].expected_answer` — quiz answer keys. Student-facing
  reads pass `for_student=True`, which strips them.
- `assignment_files` with `kind: "solution"` — reference solutions. Students
  have *no* access to that collection at all, not even read.

## Swapping ui-student over

The Mongo stores implement ui-student's own protocols
(`agentic_system/store/base.py`) — same methods, same dict shapes, ISO
timestamps ending in `Z`. The change in `agentic_system/api.py` is the
constructor:

```python
from aied_db import Role, get_db, guarded
from aied_db.stores import MongoGuardianStore, MongoParticipantStore

db = guarded(get_db(), Role.GUARDIAN_AGENT, actor_id="guardian_agent")
g_store = MongoGuardianStore(db, course_id=course_id)
```

`course_id` replaces the per-course SQLite file: it is stamped on every write
and filtered on every read. The store's course always wins over an agent's
default.

## Working on it

```bash
pytest -q                     # 382 tests, no server needed
python scripts/gen_docs.py    # after any schema change
```

- A new collection needs a schema module entry **and** a `POLICY` entry —
  `tests/test_registry.py` fails otherwise — then regenerated docs.
- `schema/curriculum.py` is a **copy**. curriculum-designer owns the original;
  edit it there, then re-copy. `tests/test_curriculum_copy.py` checks the two
  still match when that repo is cloned alongside.
- Clone `ui-student` next to this repo to run the 15 parity tests against its
  real SQLite stores. They skip otherwise, so check the skip count.
- mongomock cannot hold a validator, run `$vectorSearch`, or honour a
  collation. `tests/test_atlas_smoke.py` covers those against a real server:

```bash
AIED_DB_ATLAS_TEST=1 MONGODB_DB=aied_smoke pytest tests/test_atlas_smoke.py -v
```

## Status

**Live on Atlas** (MongoDB 8.0, `aied`) as of 2026-10-29. All 25 collections
exist with current validators and their full index sets; the vector index is
`READY` and queryable; the 22 factory settings are seeded.

Verified on the real server, not just mongomock: validators reject a bad
document, the unique indexes hold, email uniqueness and lookup are
case-insensitive, dates round-trip to the millisecond, and `$vectorSearch`
ranks by similarity while the `course_id` filter keeps one course's material
out of another's answers.

```bash
AIED_DB_ATLAS_TEST=1 MONGODB_DB=aied_smoke pytest tests/test_atlas_smoke.py -v
```

Still an empty database apart from one lab curriculum-designer wrote on
Oct 25 — no agent reads through this package yet. Migrating the existing
SQLite and JSON data is the next piece of work.

Open questions for the team are in
[docs/open-questions.md](docs/open-questions.md). They are flagged, not
silently decided.

Other repos aren't owned here: changes to them go in a branch and a PR for
their owner to review.
