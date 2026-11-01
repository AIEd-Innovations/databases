# Connecting your agent to the shared database

For the owners of `ui-student`, `assessment_agent`, `curriculum-designer`,
`participant-agent` and `ui-instructor`.

Every agent now reads and writes one MongoDB Atlas database — `aied`, 25
collections — instead of its own SQLite file, JSON folder or Cosmos
container. This page is what you need to do in your repo.

Ask Vishal for: the connection string, and your agent's database username and
password.

---

## 1. Install

```bash
pip install "aied-db @ git+https://github.com/AIEd-Innovations/databases.git@main"
```

Add the same line to your `requirements.txt` (or `pyproject.toml`). Pin a tag
rather than `main` once we cut one.

The only runtime dependency is `pymongo`.

## 2. Configure

Two variables, in your `.env`:

```
MONGODB_URI='mongodb+srv://<your-agent-user>:<password>@<host>/?retryWrites=true&w=majority'
MONGODB_DB=aied
```

- **`.env` must be gitignored.** The URI contains a password. Check your
  `.gitignore` before the first commit.
- Keep the single quotes. The URI contains `&`, which a shell would otherwise
  split.
- The package reads these from the environment and nothing else — it never
  reads a file, so load your `.env` the way your app already does
  (`python-dotenv`, your settings object, your deploy platform's env vars).
- Each agent gets **its own database user**, not a shared one. That is what
  makes the permissions below real rather than advisory.

> `ui-instructor` is the exception: it must **not** connect to the database.
> It is a browser bundle, and anything `VITE_`-prefixed ships to the browser,
> so an Atlas URI in it would be public. It keeps talking HTTP to the agents.

## 3. Connect once, at startup

```python
from aied_db import Role, get_db, guarded, ping

ping()  # before you serve traffic; fails loudly if Atlas is unreachable

db = guarded(get_db(), Role.GUARDIAN_AGENT, actor_id="guardian_agent")
```

`get_db()` holds one connection pool per process and caches it. Call it at
startup, not per request, and never build a `MongoClient` yourself.

**Always wrap it in `guarded()`.** A raw database bypasses both the access
rules and the revision trail. Pick the `Role` that matches your agent:

| Repo | Role |
| --- | --- |
| `ui-student` (guardian) | `Role.GUARDIAN_AGENT` |
| `ui-student` (participant) | `Role.PARTICIPANT_AGENT` |
| `ui-student` (lab companion) | `Role.LAB_COMPANION_AGENT` |
| `assessment_agent` | `Role.ASSESSMENT_AGENT` |
| `curriculum-designer` | `Role.CURRICULUM_AGENT` |
| `participant-agent` | `Role.PARTICIPANT_AGENT` |
| a human-facing API acting for an instructor | `Role.INSTRUCTOR` |
| a human-facing API acting for a student | `Role.STUDENT` |

`actor_id` is recorded on every revision. Use the agent's name for agent
roles, and the real user's id when you act for a person.

## 4. Use the store or repo for your data

Don't hand-write queries. Each agent has a class that already knows the
collection names, the `course_id` filtering and the document shapes.

### ui-student

The Mongo stores implement your existing protocols in
`agentic_system/store/base.py` — same methods, same plain-dict shapes,
timestamps still ISO-8601 strings ending in `Z`. The change is the
constructor:

```python
from aied_db import Role, get_db, guarded
from aied_db.stores import MongoGuardianStore, MongoParticipantStore, MongoSystemStore

db = guarded(get_db(), Role.GUARDIAN_AGENT, actor_id="guardian_agent")
g_store = MongoGuardianStore(db, course_id=course_id)
p_store = MongoParticipantStore(db, course_id=course_id)
s_store = MongoSystemStore(db)
```

Everything downstream keeps working. `course_id` replaces the per-course
SQLite file: it is stamped on every write and filtered on every read, and the
store's course always wins over a default passed by the agent.

Two differences worth knowing:

- `courses` no longer has `participant_db_path` or `guardian_db_path`. There
  are no file paths any more; the store drops those keys.
- SQLite enforced `courses.instructor_id -> users.id` and cascaded deletes.
  MongoDB does neither. Insert the instructor before the course, and see
  open question #8.

### assessment_agent

```python
from aied_db import Role, get_db, guarded
from aied_db.repos import AssessmentRepo

db = guarded(get_db(), Role.ASSESSMENT_AGENT, actor_id="assessment_agent")
repo = AssessmentRepo(db, course_id=course_id)
```

One method per route you already have. **Student-facing reads must pass
`for_student=True`** — that is what strips `criteria[].expected_answer`.
Reference solutions live in `assignment_files` with `kind: "solution"`, and
students have no access to that collection at all.

### curriculum-designer

You are already on this cluster through your own `services/storage.py`, and
your `services/schema.py` is byte-identical to our copy of it, so nothing is
broken today. But two packages are now applying validators to `labs` and
`lab_files`, and whichever `init_db` ran last wins.

Please switch to:

```python
from aied_db import Role, get_db, guarded

db = guarded(get_db(), Role.CURRICULUM_AGENT, actor_id="curriculum_agent")
```

and drop your own `scripts/init_db.py` and `scripts/check_db.py`. Until then,
**tell Vishal before you change `services/schema.py`**, or our copy goes
stale and the validator starts flip-flopping between deploys.

### participant-agent

You write to Azure Cosmos today. Use the store rather than inserting into
the collection yourself — it handles the conversions and both timestamps:

```python
from aied_db import Role, get_db, guarded
from aied_db.stores import MongoParticipantStore

db = guarded(get_db(), Role.PARTICIPANT_AGENT, actor_id="participant_agent")
store = MongoParticipantStore(db, course_id=course_id)
store.insert_interaction(record)   # your existing dict
```

Your record needs one change: **pass `course_id` through.** Cosmos
partitioned on `/student_id` and had no course; the store needs it to
isolate one course's data.

Everything else your record already contains works as-is. `timestamp` may
stay an ISO string — the store parses it. `classification_confidence` and
`feedback_score` are in the schema and are passed through.

**Every interaction gets two timestamps.** `timestamp` is when the
interaction happened, from your record; if you leave it out the store fills
in the current time rather than raising, because an interaction logged
without a time is worse than one with an approximate time. `logged_at` is
when the row was written, set by the store and never read from your record,
so it cannot be back-dated.

`logged_at` is **required by the validator**, so if you do insert into the
collection directly you must set it yourself — the server rejects the write
otherwise. That is the point: no interaction reaches the database
untimestamped.

## 5. What your role may and may not do

Enforced in `access.py`, from the Oct 26 meeting. A call that breaks these
raises `PermissionError_`; it does not fail silently.

1. **Agents never overwrite human data.** Instructor uploads, student files,
   grading specs, reference solutions and courses are read-only to every
   agent.
2. **Agents append their own output,** and may update only *named fields* of
   their own records — the guardian's session counters, the lab draft's
   content. An update touching any other field is refused.
3. **Agents never delete.** No agent role holds `delete` on any collection.
4. **Nothing is lost.** Every update, replace and delete copies the prior
   document into `revisions` first, and nobody — including admin — may edit
   or delete a revision.

So your agent cannot approve a lab, review a grade, resolve its own
escalation, or change a setting. Those are human actions and need a human
role.

Field-by-field tables, including who can write what, are in
[collections.md](collections.md).

## 6. Settings instead of constants

Thresholds and model names come from the database, so an instructor can
change them without a deploy. Defaults cascade: shipped default, then course
override, then user override.

```python
from aied_db import AgentConfig

config = AgentConfig(db, agent="guardian", course_id=course_id, user_id=student_id)
threshold = config.get("escalation.violation_threshold")  # 3 unless overridden
```

The 22 shipped values are in `aied_db/defaults.json`. Replace your module
constants with `config.get(...)` as you go — there is no rush, but a constant
is a setting an instructor cannot reach.

## 7. Moving your existing data

For ui-student's per-course SQLite folders:

```bash
python scripts/migrate_sqlite.py --course-dir path/to/data/courses/<course_id> --dry-run
python scripts/migrate_sqlite.py --course-dir path/to/data/courses/<course_id>
```

It converts ISO timestamps to BSON dates, SQLite `0`/`1` to real booleans and
`concept_tags` from a JSON string to an array, and stamps `course_id` on every
document.

It runs as `Role.MIGRATION`, which holds `insert` and `find` and nothing else,
so **it cannot overwrite or delete anything.** A row already in Mongo is
counted as skipped. Re-running is safe and inserts nothing the second time.
Always `--dry-run` first — it reads, converts, validates and reports without
connecting to write.

Assessment and Cosmos migrations do not exist yet. Talk to Vishal before
writing one.

## 8. Checking your work

```bash
python scripts/check_db.py
```

Read-only. Prints the server version, which collections exist, whether each
validator is current, which indexes are missing, and the vector index status.
It never prints the connection string, only the host.

If a write is rejected, the error says which field and why — `$jsonSchema`
validation with `additionalProperties: false` means **an unexpected field is
an error, not a warning.** Adding a field means a schema change here, not a
workaround in your repo. Open an issue or ask.

## 9. Don't

- **Don't** build your own `MongoClient`, or call `get_db()` without
  `guarded()`.
- **Don't** hardcode a connection string, or commit `.env`.
- **Don't** add a field the validator doesn't know about — it will be
  rejected.
- **Don't** run `init_db.py --strict` against `aied`. Validation is
  deliberately `moderate` until the migrated data is known clean.
- **Don't** point `MONGODB_DB` at `aied` when you're experimenting. Use a
  throwaway name.
- **Don't** connect from a browser bundle.

## Getting help

- Field tables: [collections.md](collections.md) (generated — don't edit)
- Decisions still open: [open-questions.md](open-questions.md)
- Anything else: Vishal, or an issue on `AIEd-Innovations/databases`.
