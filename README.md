# hermes-todo-goal

**English** · [Русский](README.ru.md)

The `goal_control` tool: an LLM manages the Goal of the **current chat** without a
manual `/goal` — create, read, update, pause, resume, and request verified
completion. TODO is optional.

**Status: local, unreleased hardening candidate. Not installed or deployed by this change.**
Native CAS across UI/judge/plugin writers remains an external dependency; this is
not qualified for automatic activation. Publication and installation require separate approval.

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
| `update` | `contract`, `expected_revision` | identical contract only (read-only no-op); changes require native user control |
| `pause` | `expected_revision`, `reason` | stop auto-continuation (does not interrupt the current call) |
| `resume` | `expected_revision`, `reason` | resume only a nonempty `agent:` pause with remaining budget and no wait barrier |
| `complete` | `expected_revision`, `evidence` | request verification; returns `verification_status: pending` |

Full semantics and error codes:
[skills/goal-workflow/references/tool-contract.md](skills/goal-workflow/references/tool-contract.md).

## Deliberate boundaries

- `create` never replaces a live Goal: a different contract while `active`/`paused` →
  `goal_exists`. The identical contract while `active` is an idempotent no-op
  (`changed: false`); a `done` goal may be replaced by a new one.
- `resume` does not bypass `budget`/gate/auth stops and does not lift a wait barrier.
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

- Non-identical `update` returns `contract_change_requires_user`. There is no trusted
  in-tool approval channel; `reason`/`approved` text cannot grant authority. Do not
  clear/recreate a live goal to bypass this restriction.
- User, unknown, gate and budget pauses cannot be resumed by the model. An `agent:`
  reason identifies the pause owner, not proof that a user has answered.
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
  Full automatic activation remains blocked on a shared native CAS implementation.

### Automatic routing remains off

`plugins.entries.hermes-todo-goal.settings.auto_create` defaults to false and
requires a literal boolean true plus a qualified shared native CAS capability.
This candidate intentionally reports that capability unavailable, so even an opt-in
cannot inject the section on the current host. Positive registration tests stub the
capability; they do not qualify prompt delivery or model behaviour.
Routing means directly requested, verifiable work likely to need multiple turns,
not merely multiple tool calls. Existing goals are not automatically rewritten.

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

## Verification

From the repository root:

```sh
# 1. Hermes-independent core: contract, policy, schema, registration (stdlib only)
python3 -B -m unittest discover -s tests -p 'test_contract.py' -v
python3 -B -m unittest discover -s tests -p 'test_service.py' -v
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
- The race "user pauses exactly during an update" on a real UI: a unit test covers
  generation checking, not cross-process locking.

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
