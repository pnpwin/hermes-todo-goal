# hermes-todo-goal

**English** · [Русский](README.ru.md)

The `goal_control` tool: an LLM manages the Goal of the **current chat** without a
manual `/goal` — create once, read status, and submit completion evidence.
TODO is optional. Lifecycle changes belong to native user controls.

**Status: local, unreleased hardening candidate. Not installed or deployed by this change.**
Plugin-only create-once replaces the earlier full-lifecycle/CAS plan; no core changes
are required. Initial creation still has a concurrency risk. Publication and installation
require separate approval; behavioural benefit and live continuation are not yet qualified.

## Historical verification (not acceptance of this candidate)

The following observations concern plugin `9d278b2` (runtime unchanged in `c42dd1c`,
0.1.1), not the current hardening diff. Current offline tests are described below;
no new live-model task/continuation experiment has qualified this candidate.

- Live model run (CLI `chat --cli`, gpt-6.1-sol): the agent discovered the bundled
  `goal-workflow` skill, called `goal_control` `status` → `create` → wrote the artifact →
  `status` again, and reported the same revision the plugin computed; the goal landed
  in `state_meta` under `goal:<session_key>`.
- Judge path: `GoalManager.evaluate_after_turn()` on that reply (the exact call Desktop
  makes at the turn boundary) returned `verdict: done` with a concrete reason and moved
  the state `active → done`, `turns_used 1/30`.
- `pause` → `resume` through the tool, revisions round-tripping, budget untouched.
- Verified boundary: an identical `create` on an active goal is a no-op, not an error.

Installing it, enabling it in a profile, restarting, and model-level E2E all need
separate approval. This directory on its own activates nothing.

### Surface note

Continuation belongs to the execution owner, not the CLI process. A legacy
in-process REPL and a gateway-backed CLI may follow different paths. A fresh `-z`
process does not prove a fresh gateway or loaded plugin. Directly exercising
`GoalManager.evaluate_after_turn()` does not prove automatic next-turn consumption.

## Why

In Hermes a Goal is a **completion contract**: five fields (`outcome`,
`verification`, `constraints`, `boundaries`, `stop_when`) that the native
machinery uses to keep working between turns and to check the result. A TODO is a
mutable action plan; ticking checkboxes is not evidence.

A Goal used to be settable only through a slash command, i.e. by a human. Now it
is a tool call, while continuation, the judge, quality gates and the turn budget
stay native: the plugin starts no second loop and never writes to SQLite directly.

## Actions

| action | arguments | what it does |
|---|---|---|
| `status` | — | read the Goal, its contract and `revision`; writes nothing |
| `create` | `contract`, `expected_todo_revision?` | create a Goal, independent of any TODO |
| `complete` | `expected_revision`, `evidence` | request verification; returns `verification_status: pending` |

Full semantics and error codes:
[skills/goal-workflow/references/tool-contract.md](skills/goal-workflow/references/tool-contract.md).

## Deliberate boundaries

- `create` requires no native record: active, paused, done and cleared records all
  block replacement (`goal_exists`). An identical active create is only a read-only
  no-op (`changed:false`). Corrupt/unreadable records are not treated as absence.
- Legacy `update`, `pause` and `resume` return `action_disabled`, without host reads
  or writes. Their adapter mutation paths have been removed.
- `complete` never writes `done`: the native judge decides at the turn boundary,
  after the gates pass. Evidence must be repeated in the reply — the judge reads the
  reply, not the tool result.
- The session comes from the turn context (`bound_session_key()`), never from model
  arguments: a child agent carrying its parent's `conversation_id` cannot reach
  another session's Goal.
- Every mutation is re-read from the host and compared field by field; a lost write
  yields `persistence_unverified`, not "success".
- The model cannot pass `session_id`, `profile`, `home`, `db_path`, `approved`,
  `reset_budget` or gate commands — such arguments are rejected before any side effect.

### Authority, audit and unresolved atomicity

- Contract changes, pauses and resumption require native user control. Neither
  `reason` nor `approved` grants authority. Never clear/recreate as a workaround.
- TODO binding reads the latest exactly paired persisted result from the trusted
  transcript ID. It is **not** the live TodoStore; Goal route and transcript IDs differ.
- Audit format v1 lives at `plugin-data/hermes-todo-goal/goals/<sha256[:32]>.json`.
  Binding belongs to native `created_at` and survives 50-event retention. Legacy
  matching create events are read without migration-on-read. If the originating
  legacy create is no longer retained, binding stays unknown/missing; a non-create
  write refuses rather than converting that uncertainty into confirmed null.
  Corrupt journals are
  preserved, not silently replaced. Completion evidence (up to 4000 characters)
  is retained as task data, without automatic secret redaction; do not submit secrets.
- Audit writes use a unique temporary file and an exclusive directory lock. Lock
  conflict fails explicitly; stale locks require operator recovery, never automatic
  deletion. File replacement is atomic, not a power-loss-safe native+audit transaction.
- Native success plus audit failure returns `ok:false`, `outcome:partial`, actual
  state/revision and `audit_status:failed`. Unreadable native effects return `unknown`,
  nullable state/revision/changed. Read status and report uncertainty; **do not retry**.
- Revision hashes/readback do **not** prevent concurrent UI/judge writes or ABA.
  The remaining native mutation is initial creation. Its last preflight is not CAS;
  a simultaneous native UI creation may still be overwritten. Do not manage a Goal
  concurrently. This is a scoped create-once design, not general race safety.

### Opt-in create-once routing

`plugins.entries.hermes-todo-goal.settings.auto_create` defaults to false. Literal
boolean true registers guidance on a compatible host, without a CAS capability stub
or core changes. This does not enable a second loop or grant lifecycle permissions.
Routing means directly requested, verifiable work likely to need multiple turns,
not merely multiple tool calls. The old shared-CAS activation blocker is superseded
by this reduced contract and its explicit residual risk.

## Installation (published release, not approval to deploy this candidate)

Published releases: https://github.com/pnpwin/hermes-todo-goal (MIT).
The commands below install that repository; they do not publish this local candidate.
Historical CI is not evidence of CI execution on the current local changes.

A bare path is not accepted by `hermes plugins install`: it is parsed as a GitHub
`owner/repo` (`_resolve_git_url` yields `https://github.com/Users/somovis.git` plus
a subdirectory). Three working routes:

**A. Managed install from the hosted repo** (records provenance in
`plugins/.install-metadata.json`, so `plugins update` works):

```sh
hermes plugins install pnpwin/hermes-todo-goal --enable
```

**B. Managed install from a local checkout** — same provenance, via a `file://`
URL:

```sh
cd /path/to/hermes-todo-goal
hermes plugins install "file://$PWD" --enable
```

**C. Manual directory** (no provenance, no updates — how `hermes-skillcraft` and
`ast-index` live on this machine):

```sh
cp -R /path/to/hermes-todo-goal ~/.hermes/plugins/
hermes plugins enable hermes-todo-goal
```

Once loaded, the tool appears as `goal_control` in the `goals`
toolset (plugin toolsets are enabled automatically; nothing to tick in
`hermes tools`) and the skill as `goal-workflow`.

### Is a restart needed

- A new CLI/TUI process — no: every process discovers plugins at startup.
- An already running Desktop/gateway: `install --enable` / `enable` sends
  `reload-plugins` over the control socket (and wakes the Desktop backend). If it
  answers, the plugin loads in-process, but **Python tools stay deferred to the next
  session** (`plugins_activation.py`: "Python tools and system-prompt sections stay
  deferred to the next session"). Open a new chat.
- If the gateway did not answer (`restart_required`): quit and reopen Hermes
  Desktop, or run `hermes gateway restart`.

## Initial-create locking

Plugin creates for the same profile/session use an atomic directory lock next to
that session's audit file (`.create.lock`). A concurrent call or unavailable lock
returns `create_locked` before native mutation. An abandoned lock is not removed
automatically: operator inspection is required. This coordinates plugin writers,
not native UI/judge writers, and does not claim shared CAS or eliminate that race.

## Verification

From the repository root:

```sh
# 1. Hermes-independent core: contract, policy, schema, registration (stdlib only)
python3 -B -m unittest discover -s tests -p 'test_contract.py' -v
python3 -B -m unittest discover -s tests -p 'test_service.py' -v
python3 -B -m unittest discover -s tests -p 'test_create_once.py' -v
python3 -B -m unittest discover -s tests -p 'test_plugin.py' -v
python3 -B -m unittest discover -s tests -p 'test_todo_pairing.py' -v
python3 -B -m unittest discover -s tests -p 'test_audit.py' -v

# 2. Native integration: real GoalManager and SessionDB against a throwaway HERMES_HOME.
#    HERMES_DISABLE_LAZY_INSTALLS=1 is required: without it a foreign HERMES_HOME
#    triggers the dependency bootstrap and re-executes the interpreter.
HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_HOME=$(mktemp -d) \
  PYTHONPATH="$HERMES_AGENT_DIR" \
  "$HERMES_AGENT_DIR/venv/bin/python" -B -m unittest discover -s tests \
  -p 'test_desktop.py' -v

# Optional native prompt delivery: real AIAgent + SDK serialization, intercepted
# before HTTP; section off/on, no inference and no model-behavior claim.
HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_HOME=$(mktemp -d) \
  PYTHONPATH="$HERMES_AGENT_DIR" \
  "$HERMES_AGENT_DIR/venv/bin/python" -B -m unittest discover -s tests \
  -p 'test_prompt_delivery.py' -v

# 3. Packaging checks (without installing)
hermes plugins validate . --json
hermes plugins doctor --ci .
hermes skillcraft check skills/goal-workflow
```

`test_plugin.py` uses a recording fake context rather than a real `PluginManager`;
actual registration is covered by `plugins doctor` and manifest shape by
`plugins validate`.

## What is not verified

- Desktop/TUI surface end to end: the toolset is enabled there and the judge call was
  exercised directly, but no Desktop turn has created and closed a goal yet.
- Continuation actually re-queueing a next turn in a live session (only the decision
  path was run).
- Other surfaces (Telegram, ACP, Kanban workers) — not exercised.
- Initial create racing against native UI writes: no shared CAS is claimed.
- Model-level A/B benefit of the revised create-once policy.

## Layout

```text
plugin.yaml                     manifest (provides_tools: [goal_control])
__init__.py                     from .src.plugin import register
src/contract.py                 contract and TODO-binding validation (no Hermes)
src/service.py                  action policy, readback checking (no Hermes)
src/schema.py                   tool schema
src/desktop.py                  host adapter: session context, GoalManager, TODO transcript
src/plugin.py                   register(): 1 tool + 1 bundled skill
skills/goal-workflow/SKILL.md   agent behaviour
tests/                          contract, service, plugin, desktop (native)
```

License: MIT ([LICENSE](LICENSE)). Changes: [CHANGELOG.md](CHANGELOG.md).
