# hermes-todo-goal

**English** · [Русский](README.ru.md)

The `goal_control` tool: an LLM manages the Goal of the **current chat** without a
manual `/goal` — create, read, update, pause, resume, and request verified
completion. TODO is optional.

**Status: installed in the default profile and exercised end to end through the CLI.
Not yet exercised from the Desktop/TUI surface.**

## What is verified

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

The goal-continuation hook (`_maybe_continue_goal_after_turn`) is wired only into the
TUI runtime. A classic `hermes chat --cli` REPL or a `-z` one-shot therefore creates
and reads the goal correctly but never judges or continues it by itself; that happens
on the next TUI/Desktop turn.

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
| `update` | `contract`, `expected_revision` | replace the contract in place; budget, gates, subgoals and wait survive |
| `pause` | `expected_revision`, `reason` | stop auto-continuation (does not interrupt the current call) |
| `resume` | `expected_revision`, `reason` | continue with the remaining budget, without resetting `turns_used` |
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

### Known limitation: confirming `update`

In this Hermes build the tool has no trusted confirmation channel
(`PluginContext.ask_choice` works only from a slash command, and `clarify` needs an
agent callback that a registry tool does not receive). So `update` does not prompt;
instead it:

1. records before/after in the audit trail at
   `plugin-data/hermes-todo-goal/goals/<sha256>.json` (session key is hashed, last
   50 events kept);
2. returns a `notice` the skill is required to show the user;
3. never touches budget, gates, subgoals or wait state.

This is a limitation precisely because weakening the contract is not technically
blocked. A hard gate needs a host API for in-tool confirmation — a separate task,
not something to work around.

## Installation (not performed)

Published at https://github.com/pnpwin/hermes-todo-goal (MIT; CI green on
3.11/3.12; 62 Hermes-independent tests pass in a clean clone).

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

Not run yet. Once loaded, the tool appears as `goal_control` in the `goals`
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

License: MIT ([LICENSE](LICENSE)).
