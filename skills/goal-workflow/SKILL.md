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

- Directly requested, verifiable work likely to need continuation across turns →
  read `status`, then `create` only when state is null. Multiple tool calls alone
  do not justify a Goal. Any existing record (including done/cleared) blocks a new
  creation. This create-once policy does not require modifying Hermes core.
- The user says "just make a plan", "discuss", "explain", or asks a single question
  → do **not** create a goal; answer or use `todo_list` only.
- A short task you can finish in this turn with evidence → do **not** create a goal;
  the extra continuation loop only adds budget risk.
- The result criteria changed → ask the user to change the native goal contract.
  `update`, `pause` and `resume` are disabled (`action_disabled`). Never clear/recreate
  a goal to bypass this restriction.
- External information or a user decision is needed → report the blocker and ask
  the user to manage the goal through native controls. Never resume a paused goal.
- You believe the result exists → `complete` with evidence.

## Procedure

1. Decide whether a goal is warranted (see above). If not, stop here. Read `status`;
   create only if state is null. If a goal already exists, use it only when the task
   matches its contract; otherwise ask the user. Never recreate a done/cleared goal.
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
   `expected_todo_revision`. The plan is bound for audit only, from the latest
   exactly paired persisted transcript result, not a live TodoStore accessor.
4. Read `revision` from the result. Before `complete`, read status again and use
   its revision; the native continuation engine may have changed the state.
5. Work the plan, updating `todo_list` as you go. Todo updates never require a goal
   update and must not reset the goal.
6. When you think the result exists, run the verification you wrote down, then call
   `complete` with `evidence` (actual output, artifact path, diff summary).
7. Put the same evidence in your reply. The judge reads your turn output, not the
   tool result, and Hermes' quality gates must pass before the goal is done.

## Pitfalls

- **Do not create a goal for planning-only or question-only requests.** The user
  asked for an answer, not a background loop.
- **Do not use `create` to replace any goal record, including done/cleared.** It refuses; that refusal is
  correct. Ask the user how to handle the existing goal; do not overwrite it or
  clear/recreate it as a workaround for the contract-change refusal.
- **`complete` is a request, not a verdict.** If the response says
  `verification_status: pending`, the goal is still active: say exactly that, report
  what you verified, and let the next turn-boundary check run.
- **Never claim done from your own todo checkboxes.** Evidence is output, not intent.
- **Respect a pause.** Native state may stop on budget, gates, waits or user choice.
  Surface it and ask; do not work around it. All lifecycle writes are disabled.
- **Delegation and subagents do not inherit the goal.** Only the session that owns
  the goal can change it; a child call is refused by design.
- **No budget reset.** This tool has no lifecycle mutation or budget-grant action.

- **Partial/unknown is not success.** On `audit_unverified` or
  `persistence_unverified`, read status and report the actual state and uncertainty.
  Never automatically repeat a mutation. Corrupt audit is not an absent Goal.
- **Keep evidence task-scoped.** Completion evidence is persisted in the audit;
  do not include credentials or unrelated transcript contents.
- **Readback is not atomicity.** Initial creation can race with a native UI writer.
  Do not manage the Goal concurrently. Opt-in guidance is not a concurrency guarantee;
  model-level benefit and live continuation need separate empirical verification.

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
