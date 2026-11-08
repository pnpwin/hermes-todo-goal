---
name: goal-workflow
description: "Use when managing goals or linking execution plans."
version: 0.1.0
author: Ilya Somov
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [goals, autonomy, todo, execution]
---

# Goal workflow

A Goal is a **completion contract** for the current chat: Hermes keeps working
across turns and its own judge decides, against that contract, whether the job is
done. A todo list is a **mutable action plan**; checking boxes proves nothing.

Goal and todo are independent. Create a goal with no plan when the outcome is what
matters; bind a todo list only when you already have one worth auditing.

Everything below goes through the `goal_control` tool. Never type `/goal` into a
reply — slash commands are user actions, not tool calls.

## When to Use

- The user asks for work to be carried through to a verified result → `create`.
- The user says "just make a plan", "discuss", "explain", or asks a single question
  → do **not** create a goal; answer or use `todo_list` only.
- A short task you can finish in this turn with evidence → do **not** create a goal;
  the extra continuation loop only adds budget risk.
- The result criteria changed → `update` (in place, same budget).
- New external information is needed and the user must decide → `pause`.
- The user says continue / the answer arrived → `resume`.
- You believe the result exists → `complete` with evidence.

## Procedure

1. Decide whether a goal is warranted (see above). If not, stop here.
2. Write the five-field contract from the request, not from your plan:
   `outcome`, `verification`, `constraints`, `boundaries`, `stop_when`.
   - `outcome` — what must exist at the end.
   - `verification` — which command, test or artifact proves it.
   - `constraints` — what must not change.
   - `boundaries` — where you may act (files, services, environment).
   - `stop_when` — when you must ask instead of continuing. Include deployment,
     credentials, publishing, spending and destructive changes by default.
   If the user set no extra limits, say so in the field rather than inventing
   permission you do not have.
3. If a plan exists, call `todo_list` and keep its `revision`. Then `create` with
   `expected_todo_revision`. The plan is bound for audit only.
4. Read `revision` from the `create` result and keep it. Every later mutation needs
   the current revision; re-read with `status` when a call reports `revision_stale`.
5. Work the plan, updating `todo_list` as you go. Todo updates never require a goal
   update and must not reset the goal.
6. When you think the result exists, run the verification you wrote down, then call
   `complete` with `evidence` (actual output, artifact path, diff summary).
7. Put the same evidence in your reply. The judge reads your turn output, not the
   tool result, and Hermes' quality gates must pass before the goal is done.

## Pitfalls

- **Do not create a goal for planning-only or question-only requests.** The user
  asked for an answer, not a background loop.
- **Do not use `create` to replace a live goal.** It refuses; that refusal is
  correct. Use `update` for a contract change, or ask the user to clear it.
- **`complete` is a request, not a verdict.** If the response says
  `verification_status: pending`, the goal is still active: say exactly that, report
  what you verified, and let the next turn-boundary check run.
- **Never claim done from your own todo checkboxes.** Evidence is output, not intent.
- **Respect a pause.** If a call reports `pause_not_resumable` or `wait_barrier`,
  the stop came from the budget, a failed gate, a parked process or the user.
  Surface it and ask; do not look for a way around it.
- **Delegation and subagents do not inherit the goal.** Only the session that owns
  the goal can change it; a child call is refused by design.
- **`update` does not grant new budget.** If the goal hit `max_turns`, changing the
  contract will not extend it.

## Verification

Before reporting completion to the user, confirm all of:

1. `status` shows the contract you were given, and the goal is `active` until the
   judge closes it.
2. The verification command in the contract was actually run, with its real output.
3. No constraint or boundary was crossed; anything that had to be skipped is named
   explicitly.
4. The evidence appears in your reply, not only in the tool result.

See [references/tool-contract.md](references/tool-contract.md) for every action's
arguments and error codes.
