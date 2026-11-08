"""Pure validation for a Goal completion contract and its optional TODO binding.

No Hermes imports: this module is importable with a bare stdlib interpreter.

The contract is the *condition the plan exists to satisfy*; it is never satisfied
by a checked todo box. Five fields, all required, all free text:

    outcome      what must exist at the end
    verification which tests/commands/artifacts prove it
    constraints  what must not change
    boundaries   where acting is permitted
    stop_when    when a human decision is required

The optional TODO snapshot links a mutable action plan to the goal for audit
only. ``prepare_goal`` copies its inputs and never mutates them.
"""

from __future__ import annotations

from typing import Any

CONTRACT_FIELDS = ("outcome", "verification", "constraints", "boundaries", "stop_when")
MAX_CONTRACT_FIELD_CHARS = 4000
TODO_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})
ACTIVE_TODO_STATUSES = frozenset({"pending", "in_progress"})


class ContractError(ValueError):
    """A rejected contract or TODO binding, carrying a stable machine code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": str(self)}


def _validate_contract(contract: Any) -> dict[str, str]:
    if not isinstance(contract, dict):
        raise ContractError("contract_invalid", "contract must be an object with five text fields")
    unknown = set(contract) - set(CONTRACT_FIELDS)
    if unknown:
        raise ContractError(
            "contract_field_unknown",
            f"unknown contract field(s): {', '.join(sorted(unknown))}",
        )
    cleaned: dict[str, str] = {}
    for field in CONTRACT_FIELDS:
        if field not in contract:
            raise ContractError("contract_field_missing", f"contract field {field!r} is required")
        value = contract[field]
        if not isinstance(value, str):
            raise ContractError("contract_field_type", f"contract field {field!r} must be text")
        text = value.strip()
        if not text:
            raise ContractError("contract_field_blank", f"contract field {field!r} must not be blank")
        if len(text) > MAX_CONTRACT_FIELD_CHARS:
            raise ContractError(
                "contract_field_too_long",
                f"contract field {field!r} exceeds {MAX_CONTRACT_FIELD_CHARS} characters",
            )
        cleaned[field] = text
    return cleaned


def _revision(value: Any, code: str, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(code, f"{label} must be a non-negative integer")
    return value


def _validate_todos(snapshot: Any, expected_revision: Any) -> tuple[int, list[str]]:
    if not isinstance(snapshot, dict):
        raise ContractError("todo_snapshot_invalid", "todo snapshot must be an object")
    revision = _revision(snapshot.get("revision"), "todo_revision_invalid", "todo revision")
    items = snapshot.get("todos")
    if not isinstance(items, list):
        raise ContractError("todo_snapshot_invalid", "todo snapshot must carry a 'todos' list")
    if not items:
        raise ContractError("todo_plan_empty", "the todo list is empty, so there is nothing to bind")
    if expected_revision is None:
        raise ContractError(
            "todo_revision_required",
            "an explicit todo binding needs expected_todo_revision from the todo_list result",
        )
    if _revision(expected_revision, "todo_revision_invalid", "expected_revision") != revision:
        raise ContractError(
            "todo_revision_stale",
            f"todo revision {revision} does not match expected_revision {expected_revision}; "
            "read the current todo_list again before binding",
        )
    ids: list[str] = []
    active = False
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ContractError("todo_snapshot_invalid", f"todo item {index} is not an object")
        todo_id = item.get("id")
        if not isinstance(todo_id, str) or not todo_id.strip():
            raise ContractError("todo_snapshot_invalid", f"todo item {index} has no id")
        status = item.get("status")
        if status not in TODO_STATUSES:
            raise ContractError("todo_snapshot_invalid", f"todo item {todo_id!r} has unknown status")
        active = active or status in ACTIVE_TODO_STATUSES
        ids.append(todo_id)
    if len(set(ids)) != len(ids):
        raise ContractError("todo_ids_duplicate", "todo ids must be unique")
    if not active:
        raise ContractError(
            "todo_plan_inactive",
            "the todo list has no pending or in_progress item, so binding it would not start work",
        )
    return revision, ids


def prepare_goal(
    todo_snapshot: dict[str, Any] | None,
    contract: Any,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Validate a contract and copy it; report the TODO binding it will be stored with.

    ``todo_snapshot=None`` is the normal standalone goal: no TODO is read or required.
    """
    cleaned = _validate_contract(contract)
    if todo_snapshot is None:
        if expected_revision is not None:
            raise ContractError(
                "todo_revision_without_snapshot",
                "expected_todo_revision was supplied but no todo snapshot was available",
            )
        return {"contract": dict(cleaned), "todo_revision": None, "todo_ids": []}
    revision, ids = _validate_todos(todo_snapshot, expected_revision)
    return {"contract": dict(cleaned), "todo_revision": revision, "todo_ids": ids}
