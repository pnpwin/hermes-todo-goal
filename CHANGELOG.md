# Changelog

## Unreleased — local hardening, not deployed

- Adopt plugin-only create-once: expose status/create/complete; all legacy update,
  pause and resume calls refuse. Never replace done/cleared/unreadable native records.
- Separate trusted transcript IDs from Goal route keys; deny delegated child use.
- Require exact TODO call-result provenance; preserve authoritative clears.
- Report partial/unknown native/audit effects without automatic retries.
- Persist completion evidence and generation-bound TODO binding in audit format v1;
  read without mutation, serialize audit writers and retain corrupt journals.
- Supersede the shared-CAS plan with the owner-approved reduced contract; no core
  changes. Initial create still races with simultaneous native UI writes.
- Default-off routing registers with literal true on a compatible host. No invented
  CAS capability. Historical verification does not qualify this candidate; no version bump.
- Correct the old blanket CLI/`-z` claim: continuation depends on the actual
  execution owner; direct judge invocation is not live next-turn acceptance.

## 0.1.1 — 2026-11-09

Docs and packaging only; the tool contract, policy and host adapter are unchanged.

- `README.md` is English by default, the Russian document moved to `README.ru.md`,
  with a language switch on the first line of both.
- README records the live CLI verification: model-driven `status → create →
  write artifact → status`, the judge call Desktop runs at the turn boundary
  returning `done`, `pause`/`resume` round-tripping, and the idempotent `create`.
- Documented the native surface limitation: the goal-continuation hook lives only in
  the TUI runtime, so classic `chat --cli` and `-z` runs never judge a goal themselves.
- Test commands in the README are portable (`python3`, `$HERMES_AGENT_DIR`) instead of
  one machine's pyenv path, and the hosted install command is listed first.

## 0.1.0 — 2026-11-08

First published version: the `goal_control` tool (status, create, update, pause,
resume, complete) over the native `GoalManager`, the bundled `goal-workflow` skill,
audit records under `plugin-data/<plugin>/goals/`, and 75 tests.
