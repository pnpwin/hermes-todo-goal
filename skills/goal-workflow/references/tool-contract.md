# `goal_control` reference

One action per call. Unknown arguments are rejected before any effect: the session,
profile, home, database and approval are decided by the host, never by the model.

| action | arguments | effect |
|---|---|---|
| `status` | — | Read the current goal, its contract and its `revision`. Never writes. |
| `create` | `contract`, optional `expected_todo_revision` | Create the goal for this session. Refused when a live goal exists. |
| `update` | `contract`, `expected_revision` | Rewrite headline + contract in place. Budget, gates, subgoals and wait state are preserved. |
| `pause` | `expected_revision`, `reason` | Stop automatic continuation. Does not interrupt the running call or any process. |
| `resume` | `expected_revision`, `reason` | Continue with the remaining budget. Never resets `turns_used`. |
| `complete` | `expected_revision`, `evidence` | Ask for verification. Returns `verification_status: pending`; never sets `done`. |

## Contract fields

`outcome`, `verification`, `constraints`, `boundaries`, `stop_when` — all five are
required, non-blank, max 4000 characters each, and unknown fields are rejected.
These are data, not shell commands: writing `pytest ...` in `verification` does not
register a Hermes quality gate. Gates stay a user action (`/goal gate add`).

## What `create` refuses

- an `active` or `paused` goal already exists (`goal_exists`) — update, resume or ask
  the user to clear it;
- the same goal is already active with the same contract → `changed: false`, no write;
- a `done` or `cleared` goal does not block a new one: that work is over.

## Error codes

| code | meaning | what to do |
|---|---|---|
| `no_session` | no session is bound to this call | run from a live chat turn; do not retry blindly |
| `no_goal` | the session has no goal | `create` first |
| `goal_exists` | a live goal would be replaced | `update`, `resume`, or ask the user |
| `goal_not_live` / `goal_not_active` / `goal_not_paused` | action does not fit the status | `status`, then choose |
| `revision_required` | mutation without `expected_revision` | `status` first |
| `revision_stale` | the goal moved since it was read | `status` again, re-apply |
| `persistence_unverified` | the host did not persist a field | report the unknown outcome; do not claim success |
| `store_unavailable` | the session store is unavailable | stop; no change was made |
| `todo_unavailable` | the plan could not be read | create the goal without the binding |
| `todo_revision_stale` / `todo_plan_empty` / `todo_plan_inactive` | the todo binding is not usable | re-read `todo_list`; binding is optional |
| `wait_barrier` | the goal is parked on a process, child or deadline | ask the user; do not resume |
| `pause_not_resumable` | the stop came from budget, gates or auth | surface it; needs a user decision |
| `host_incompatible` | this Hermes build lacks a required symbol | report the listed symbols; do not work around |
| `arg_unknown` / `arg_missing` / `arg_too_long` | the call shape is wrong | fix the arguments |

## Guarantees

- Goal storage, the continuation loop, the judge and the turn budget are Hermes'
  own; this tool never runs a second loop and never calls SQLite directly.
- The session is resolved from the host's turn context, so a delegated child or a
  stale conversation id cannot reach the parent's goal.
- Every mutation is re-read from the host and compared field by field, and recorded
  in `plugin-data/hermes-todo-goal/goals/<sha256>.json` (session key hashed, last 50
  events).
