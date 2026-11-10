"""Goal lifecycle policy: what the model may do to the goal of the *current* session.

The service owns policy and never touches Hermes directly; a host adapter owns
persistence and continuation. Hermes keeps its own continuation engine, judge,
quality gates and turn budget - this module never runs a second loop and never
marks a goal done on the model's word.

Design invariants (each is covered by a test in ``tests/test_service.py``):

* :meth:`GoalService.handle_status` is read-only.
* ``create`` never replaces a live goal; ``update`` preserves budget, gates,
  subgoals and wait barriers; ``pause`` never relabels a user pause; ``resume``
  never resets the turn budget; ``complete`` only *requests* verification.
* Every mutation is re-read from the host and compared field by field, because a
  silently dropped write must not be reported as success.
* The model cannot choose a session, profile, database or approval flag: any
  argument outside ``ALLOWED_ARGS`` is rejected before side effects.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol

from .contract import ContractError, prepare_goal

ACTIONS = ("status", "create", "update", "pause", "resume", "complete")

ALLOWED_ARGS: dict[str, frozenset[str]] = {
    "status": frozenset(),
    "create": frozenset({"contract", "expected_todo_revision"}),
    "update": frozenset({"contract", "expected_revision"}),
    "pause": frozenset({"reason", "expected_revision"}),
    "resume": frozenset({"reason", "expected_revision"}),
    "complete": frozenset({"evidence", "expected_revision"}),
}

AGENT_PAUSE_PREFIX = "agent:"
LIVE_STATUSES = frozenset({"active", "paused"})
MAX_REASON_CHARS = 500
MAX_EVIDENCE_CHARS = 4000

#: Fields ``update`` must not disturb. The host re-read is compared on these.
PRESERVED_ON_UPDATE = (
    "status", "turns_used", "max_turns", "created_at", "last_turn_at",
    "subgoals", "gates", "paused_reason", "waiting_on_pid", "waiting_on_session",
    "waiting_until", "waiting_on_delegations", "waiting_reason", "waiting_since",
)
WAIT_FIELDS = ("waiting_on_pid", "waiting_on_session", "waiting_until", "waiting_on_delegations")


class GoalError(Exception):
    """A refused operation with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


class HostAdapter(Protocol):
    """What the service needs from the surface that owns the goal.

    Implementations must persist through the host's own goal storage and return a
    plain dict snapshot (``dataclasses.asdict`` of the native state), never a
    second store of their own.
    """

    def session_key(self) -> str: ...

    def load(self) -> dict[str, Any] | None: ...

    def create(self, outcome: str, contract: dict[str, str]) -> dict[str, Any]: ...

    def replace_contract(
        self, snapshot: dict[str, Any], outcome: str, contract: dict[str, str]
    ) -> dict[str, Any]: ...

    def pause(self, reason: str) -> dict[str, Any]: ...

    def resume(self) -> dict[str, Any]: ...

    def todo_snapshot(self) -> dict[str, Any] | None: ...

    def audit(self, event: dict[str, Any]) -> bool: ...


def state_revision(snapshot: dict[str, Any] | None) -> str:
    """Opaque fingerprint of a whole native goal state (``"none"`` when absent)."""
    if snapshot is None:
        return "none"
    raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _text_arg(args: dict[str, Any], key: str, limit: int) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GoalError("arg_missing", f"{key!r} is required and must be non-blank text")
    text = value.strip()
    if len(text) > limit:
        raise GoalError("arg_too_long", f"{key!r} exceeds {limit} characters")
    return text


def _project(snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    keys = ("goal", "status", "contract", "turns_used", "max_turns", "created_at",
            "last_verdict", "last_reason", "paused_reason", "subgoals", "gates",
            *WAIT_FIELDS, "waiting_reason")
    return {key: snapshot.get(key) for key in keys}


def _wait_active(snapshot: dict[str, Any]) -> bool:
    if snapshot.get("waiting_on_pid") or snapshot.get("waiting_on_session"):
        return True
    if snapshot.get("waiting_on_delegations"):
        return True
    return bool(snapshot.get("waiting_until"))


def _prepare(todo_snapshot, contract, expected_revision=None):
    try:
        return prepare_goal(todo_snapshot, contract, expected_revision)
    except ContractError as exc:
        raise GoalError(exc.code, str(exc)) from exc


class GoalService:
    def __init__(self, adapter: HostAdapter):
        self._adapter = adapter

    # ---- public entry point -------------------------------------------
    def handle(self, args: Any) -> dict[str, Any]:
        if not isinstance(args, dict):
            raise GoalError("args_invalid", "arguments must be an object")
        action = args.get("action")
        if not isinstance(action, str) or action not in ACTIONS:
            raise GoalError("unknown_action", f"action must be one of {', '.join(ACTIONS)}")
        unknown = set(args) - ALLOWED_ARGS[action] - {"action"}
        if unknown:
            raise GoalError(
                "arg_unknown",
                f"unsupported argument(s) for {action}: {', '.join(sorted(unknown))}. "
                "The session, profile and approval are decided by the host, not by the model.",
            )
        handler = getattr(self, f"_action_{action}")
        return handler(args)

    # ---- helpers ------------------------------------------------------
    def _state(self) -> dict[str, Any] | None:
        return self._adapter.load()

    def _require_state(self) -> dict[str, Any]:
        state = self._state()
        if state is None:
            raise GoalError("no_goal", "this session has no goal; create one first")
        return state

    def _require_revision(self, args: dict[str, Any], state: dict[str, Any]) -> None:
        expected = args.get("expected_revision")
        if not isinstance(expected, str) or not expected:
            raise GoalError(
                "revision_required",
                "expected_revision is required: read the goal with action=status first",
            )
        if expected != state_revision(state):
            raise GoalError(
                "revision_stale",
                "the goal changed since it was read; call action=status again and re-apply",
            )

    def _readback(self, expected: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
        confirmed = self._state()
        if confirmed is None:
            raise GoalError("persistence_unverified", "the goal could not be read back after the write")
        for field in fields:
            if confirmed.get(field) != expected.get(field):
                raise GoalError(
                    "persistence_unverified",
                    f"the host did not persist {field!r}; treat the outcome as unknown and report it",
                )
        return confirmed

    def _audit(self, event: str, *,
               before: dict[str, Any] | None = None,
               after: dict[str, Any] | None = None,
               reason: str = "",
               todo_revision: int | None = None,
               todo_ids: list[str] | None = None) -> None:
        key = self._adapter.session_key() or ""
        self._adapter.audit({
            "event": event,
            "session_key_hash": hashlib.sha256(key.encode("utf-8")).hexdigest()[:16] if key else "",
            "reason": reason,
            "todo_revision": todo_revision,
            "todo_ids": list(todo_ids or []),
            "before": _project(before),
            "after": _project(after),
        })

    def _result(self, action: str, state: dict[str, Any] | None, *, changed: bool,
                notice: str = "", **extra: Any) -> dict[str, Any]:
        result = {
            "ok": True,
            "action": action,
            "changed": changed,
            "revision": state_revision(state),
            "state": _project(state),
            "todo_binding": None,
            "notice": notice,
        }
        result.update(extra)
        return result

    # ---- actions ------------------------------------------------------
    def _action_status(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self._state()
        return self._result("status", state, changed=False,
                            notice="read-only status; no goal state was modified")

    def _action_create(self, args: dict[str, Any]) -> dict[str, Any]:
        todo = None
        expected_todo = args.get("expected_todo_revision")
        if expected_todo is not None:
            todo = self._adapter.todo_snapshot()
            if todo is None:
                raise GoalError(
                    "todo_unavailable",
                    "the current todo list could not be read, so the plan cannot be bound; "
                    "create the goal without expected_todo_revision",
                )
        prepared = _prepare(todo, args.get("contract"), expected_todo)
        outcome = prepared["contract"]["outcome"]

        state = self._state()
        if state is not None and state.get("status") in LIVE_STATUSES:
            same = (state.get("goal") == outcome
                    and (state.get("contract") or {}) == prepared["contract"])
            if same and state.get("status") == "active":
                return self._result(
                    "create", state, changed=False,
                    notice="this goal is already active with the same contract; nothing changed",
                )
            raise GoalError(
                "goal_exists",
                f"this session already has a {state.get('status')} goal; "
                "update it, resume it, or ask the user to clear it before creating another",
            )

        self._adapter.create(outcome, prepared["contract"])
        confirmed = self._readback(
            {"goal": outcome, "contract": prepared["contract"], "status": "active"},
            ("goal", "contract", "status"),
        )
        self._audit("create", after=confirmed, todo_revision=prepared["todo_revision"],
                    todo_ids=prepared["todo_ids"])
        result = self._result(
            "create", confirmed, changed=True,
            notice="goal created; Hermes continues and judges it after each turn against this contract",
        )
        if prepared["todo_revision"] is not None:
            result["todo_binding"] = {"revision": prepared["todo_revision"], "ids": prepared["todo_ids"]}
        return result

    def _action_update(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self._require_state()
        if state.get("status") not in LIVE_STATUSES:
            raise GoalError(
                "goal_not_live",
                f"the goal is {state.get('status')}, so its contract is closed; create a new goal instead",
            )
        self._require_revision(args, state)
        prepared = _prepare(None, args.get("contract"))
        outcome = prepared["contract"]["outcome"]
        if state.get("goal") == outcome and (state.get("contract") or {}) == prepared["contract"]:
            return self._result("update", state, changed=False,
                                notice="the goal already carries this exact outcome and contract")

        raise GoalError(
            "contract_change_requires_user",
            "This host has no trusted in-tool contract approval channel. "
            "Ask the user to change the contract through native goal control; "
            "do not replace or clear the goal as a workaround.",
        )

    def _action_pause(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self._require_state()
        self._require_revision(args, state)
        reason = _text_arg(args, "reason", MAX_REASON_CHARS)
        if state.get("status") == "paused":
            return self._result("pause", state, changed=False,
                                notice=f"the goal is already paused ({state.get('paused_reason')})")
        if state.get("status") != "active":
            raise GoalError("goal_not_active",
                            f"only an active goal can be paused, not a {state.get('status')} one")
        self._adapter.pause(f"{AGENT_PAUSE_PREFIX}{reason}")
        confirmed = self._readback(
            {"status": "paused", "paused_reason": f"{AGENT_PAUSE_PREFIX}{reason}",
             "turns_used": state.get("turns_used"), "subgoals": state.get("subgoals"),
             "gates": state.get("gates")},
            ("status", "paused_reason", "turns_used", "subgoals", "gates"),
        )
        self._audit("pause", before=state, after=confirmed, reason=reason)
        return self._result(
            "pause", confirmed, changed=True,
            notice="goal paused: no further automatic continuation. The current tool call and "
                   "any running process were not interrupted.",
        )

    def _action_resume(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self._require_state()
        self._require_revision(args, state)
        reason = _text_arg(args, "reason", MAX_REASON_CHARS)
        if state.get("status") != "paused":
            raise GoalError("goal_not_paused",
                            f"only a paused goal can be resumed, not a {state.get('status')} one")
        if _wait_active(state):
            raise GoalError(
                "wait_barrier",
                "the goal is parked on a wait barrier (process, child or deadline); "
                "resolving that is the user's decision, not a resume",
            )
        paused_reason = state.get("paused_reason")
        if (not isinstance(paused_reason, str)
                or not paused_reason.startswith(AGENT_PAUSE_PREFIX)
                or not paused_reason[len(AGENT_PAUSE_PREFIX):].strip()):
            raise GoalError(
                "pause_not_resumable",
                "Only an agent-owned pause can be resumed by this tool; "
                "user, unknown, gate and budget pauses require native user control.",
            )
        if state.get("turns_used", 0) >= state.get("max_turns", 0):
            raise GoalError("pause_not_resumable", "The remaining goal budget is exhausted.")
        self._adapter.resume()
        confirmed = self._readback(
            {"status": "active", "paused_reason": None,
             "turns_used": state.get("turns_used"), "max_turns": state.get("max_turns"),
             "subgoals": state.get("subgoals"), "gates": state.get("gates")},
            ("status", "paused_reason", "turns_used", "max_turns", "subgoals", "gates"),
        )
        self._audit("resume", before=state, after=confirmed, reason=reason)
        return self._result(
            "resume", confirmed, changed=True,
            notice="goal resumed with the remaining budget preserved; Hermes will continue "
                   "after this turn and judge the result against the contract",
        )

    def _action_complete(self, args: dict[str, Any]) -> dict[str, Any]:
        state = self._require_state()
        self._require_revision(args, state)
        evidence = _text_arg(args, "evidence", MAX_EVIDENCE_CHARS)
        if state.get("status") != "active":
            raise GoalError(
                "goal_not_active",
                f"the goal is {state.get('status')}; completion can only be requested for an "
                "active goal",
            )
        self._audit("complete_requested", before=state, after=state, reason="", )
        return self._result(
            "complete", state, changed=False,
            verification_status="pending",
            evidence=evidence,
            notice="completion requested, not granted: put this evidence in your reply. The "
                   "turn-boundary judge decides from it; quality gates must pass first.",
        )
