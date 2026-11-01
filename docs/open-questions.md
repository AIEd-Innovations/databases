# Open questions

Decisions that are **not** mine to make. Each one is implemented in the way
that keeps both options available, and flagged here rather than quietly
settled. Nothing below is a bug.

## 1. Two session models — merge them?

`guardian_sessions` (ui-student, running today) and `integrity_sessions`
(task3 draft) model the same thing.

**Where it stands:** both exist. `guardian_sessions` is what
`MongoGuardianStore` writes, because ui-student's agents depend on its exact
shape; `integrity_sessions` is what `IntegrityRepo` writes. Neither reads the
other.

**What the code already tells us:** `GuardianStore.get_session` must return
the session with its `questions` and `violations` embedded, and the running
agent updates three counters on it. The integrity draft's session has none of
that — it carries only `status` and `last_activity_at`. So the guardian model
is the richer one, and the likely merge is integrity's fields folded into it.

**Needed:** agreement with Rocio's team before either is removed.

## 2. Three question logs — which is the record?

A student's question can land in `interactions` (participant), in
`guardian_questions` (guardian) and in `rag_queries` (lab companion).

**Where it stands:** all three are written, because all three agents log today
and removing one would lose data nobody agreed to lose. `rag_queries` stores
only what was *retrieved* plus the question text, so it is the narrowest.

**Needed:** a decision on which is authoritative. Until then callers keep
logging where they already logged.

## 3. `assignments` (points) vs `labs` (weights)

**Where it stands:** separate, and `AssessmentRepo.import_from_lab` takes a
**copy** — quiz questions become criteria, `rubric_points` becomes
`max_points`. Editing the lab afterwards does not change the assignment
(there is a test for that).

**Needed:** confirmation that a copy, not a live reference, is what the team
wants. A copy means the two can drift; a reference would mean a lab edit
silently re-grades.

## 4. The task3 assessment draft is out of date

It describes test cases, report evaluation, anomaly reports, a review queue
and stored submission text. Sean's Oct 26 redesign removed all of them: the
agent only grades.

**Where it stands:** `schema/assessment.py` follows the code on `main`, as
decided. Review is a *status* on the result (`needs_review` ->
`instructor_graded`), not a second collection.

**Needed:** the draft's author to update it. Anyone reading the draft today
will build the wrong thing.

## 5. The 10% manual review weight — replace or adjust?

curriculum-designer's rubric reserves `manual_weight: 0.1`. The assessment
route's own comment says that if it survives the rubric redesign, review
becomes an *adjustment* rather than a *replacement*.

**Where it stands:** replacement. `AssessmentRepo.review_result` sets
`final_score`, which replaces `score` (and keeps `score` for the record).
`aied_db/defaults.json` ships `manual_weight: 0.1` for assessment, and the three
weights sum to 1.0.

**Needed:** a decision. Changing it later means changing one method and one
default, not the schema.

## 6. Which student preferences belong in `agent_configs`?

**Where it stands:** no setting is student-editable. Every `editable_by` in
`aied_db/defaults.json` is `admin` and/or `instructor`, and there is a test
asserting that, so making one student-editable is a deliberate act.

**Needed:** a list of preferences students should own.

## 7. Nothing reads `AgentConfig` yet

All six agents still use hard-coded constants. The defaults in
`aied_db/defaults.json` was copied *from* those constants, so seeding changes no
behaviour today.

**Needed:** one wiring task per repo, after the store swap.

---

## 8. NEW — MongoDB cannot enforce the foreign keys SQLite did

Found while writing the parity tests, and not in the original list.

`SqliteSystemStore` opens every connection with `PRAGMA foreign_keys = ON` and
declares:

- `courses.instructor_id REFERENCES users(id)`
- `enrollments.course_id REFERENCES courses(id) ON DELETE CASCADE`
- `enrollments.user_id REFERENCES users(id) ON DELETE CASCADE`

SQLite therefore **refused** a course whose instructor did not exist, and
**cascaded** enrollment deletes. MongoDB has no equivalent and a
`$jsonSchema` validator cannot express one, so after the migration:

- a course can reference a non-existent instructor, and
- deleting a user or a course leaves its enrollments behind as orphans.

Both are covered by tests in `tests/test_parity.py` that document the
difference rather than paper over it. `list_enrolled_users` already tolerates
a missing user — it skips the row, exactly as the SQL `JOIN` did.

**Options:** validate the reference in the service layer before inserting;
accept orphans and make every read tolerate them; or run a periodic integrity
check. **This one needs a decision before production data goes in.**
