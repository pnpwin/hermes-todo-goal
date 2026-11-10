"""Desktop/CLI host adapter: the only module that touches the Hermes runtime.

Everything here is *version-bound*: it uses the installed Hermes' own session
context, goal storage and todo transcript instead of a parallel store. The
plugin never writes SQLite directly, never runs a second continuation loop and
never calls the judge.

Session identity comes from ``gateway.session_context.bound_session_key()``: the
route key the surface binds for the turn that is executing this tool. It has no
``os.environ`` fallback, so a thread that inherited no context cannot borrow
another session's goal. ``conversation_id`` is deliberately NOT used: a delegated
child carries its delegator's conversation id, so it would let a child drive the
parent's goal.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .service import GoalError

PLUGIN_NAME = "hermes-todo-goal"
AUDIT_EVENTS_KEPT = 50
TODO_TRANSCRIPT_LIMIT = 400

#: Symbols this adapter needs from the installed Hermes. ``missing_symbols()``
#: turns a host upgrade into one explicit refusal instead of an AttributeError.
REQUIRED_SYMBOLS = (
    ("hermes_cli.goals", "load_goal"),
    ("hermes_cli.goals", "_meta_key"),
    ("hermes_cli.goals", "_get_session_db"),
    ("hermes_cli.goals", "GoalManager"),
    ("hermes_cli.goals", "GoalContract"),
    ("gateway.session_context", "bound_session_key"),
    ("agent.delegation_context", "is_delegated_child_context"),
    ("tools.todo_tool", "is_todo_tool_call"),
    ("plugins.plugin_storage", "plugin_data_dir"),
)


def missing_symbols() -> list[str]:
    """Host symbols this build expects; empty when the adapter can run."""
    import importlib

    missing: list[str] = []
    for module_name, attribute in REQUIRED_SYMBOLS:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            missing.append(module_name)
            continue
        if not hasattr(module, attribute):
            missing.append(f"{module_name}.{attribute}")
    return missing


def _goals():
    from hermes_cli import goals

    return goals


def _session_key() -> str:
    from gateway.session_context import bound_session_key
    from agent.delegation_context import is_delegated_child_context

    if is_delegated_child_context():
        return ""
    return bound_session_key() or ""


def _max_turns() -> int:
    from hermes_cli.config import load_config

    try:
        configured = int((load_config().get("goals") or {}).get("max_turns", 20) or 20)
    except Exception:
        return 20
    return configured if configured > 0 else 20


def _parse_todo_payload(content: Any) -> dict[str, Any] | None:
    """The ``todo_list`` result envelope: ``{"todos": [...], "revision": int, ...}``."""
    if not isinstance(content, str) or not content.strip():
        return None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return None
    if not isinstance(payload.get("todos"), list):
        return None
    return {"revision": revision, "todos": payload["todos"]}


def _paired_with_todo_call(rows: list[dict[str, Any]], index: int) -> bool:
    """A todo result only counts when an assistant todo call precedes it.

    Mirrors the native hydration guard: a forged bare ``role: tool`` row must not
    seed a plan, and therefore must not be bindable to a goal.
    """
    from tools.todo_tool import is_todo_tool_call

    from .todo_pairing import paired_with_todo_call

    return paired_with_todo_call(rows, index, is_todo_tool_call)


class HermesGoalAdapter:
    """Persist goals through Hermes itself, for the session bound to this call."""

    def __init__(
        self,
        *,
        session_key_getter: Callable[[], str] = _session_key,
        goals_module=None,
        max_turns_getter: Callable[[], int] = _max_turns,
        data_dir_getter: Callable[[], Path] | None = None,
        transcript_id_getter: Callable[[], str] | None = None,
    ):
        self._session_key_getter = session_key_getter
        self._transcript_id_getter = transcript_id_getter or (lambda: "")
        self._goals_module = goals_module
        self._max_turns_getter = max_turns_getter
        self._data_dir_getter = data_dir_getter

    # ---- plumbing -----------------------------------------------------
    @property
    def goals(self):
        if self._goals_module is None:
            self._goals_module = _goals()
        return self._goals_module

    def session_key(self) -> str:
        key = (self._session_key_getter() or "").strip()
        if not key:
            raise GoalError(
                "no_session",
                "no session is bound to this tool call, so there is no goal to act on. "
                "Goals are per-session; run this from a live chat turn.",
            )
        return key

    def transcript_id(self) -> str:
        value = self._transcript_id_getter()
        if not isinstance(value, str) or not value or value != value.strip():
            raise GoalError("todo_unavailable", "No trusted transcript session ID was supplied.")
        return value

    def _store(self):
        db = self.goals._get_session_db()
        if db is None:
            raise GoalError(
                "store_unavailable",
                "the Hermes session store is unavailable, so goal state cannot be read or "
                "written safely; no change was made",
            )
        return db

    def _native(self):
        """Fresh native GoalManager for this call (never cached across turns)."""
        self.session_key()
        self._store()
        return self.goals.GoalManager(
            session_id=self.session_key(), default_max_turns=self._max_turns_getter()
        )

    # ---- reads --------------------------------------------------------
    def load(self) -> dict[str, Any] | None:
        key = self.session_key()
        # Native load_goal conflates missing, corrupt and failed reads. Never
        # authorize a create from that ambiguity. Use the registry-owned store.
        try:
            raw = self._store().get_meta(self.goals._meta_key(key))
        except GoalError:
            raise
        except Exception:
            raise GoalError("state_unreadable", "Goal record could not be read; no write allowed.") from None
        if raw is None:
            return None
        state = self.goals.load_goal(key)
        if state is None:
            raise GoalError("state_unreadable", "An existing goal record is unreadable; no write allowed.")
        return asdict(state)

    def todo_snapshot(self) -> dict[str, Any] | None:
        """Newest paired ``todo_list`` result from this session's own transcript."""
        db = self._store()
        try:
            rows = db.get_messages(
                self.transcript_id(), limit=TODO_TRANSCRIPT_LIMIT, latest=True,
                include_ancestors=True,
            )
        except Exception as exc:  # pragma: no cover - store-specific failures
            raise GoalError("todo_unavailable", f"todo transcript read failed: {type(exc).__name__}") from exc
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if row.get("role") != "tool":
                continue
            if not _paired_with_todo_call(rows, index):
                continue
            # A paired clear or invalid latest snapshot supersedes older plans.
            return _parse_todo_payload(row.get("content"))
        return None

    # ---- writes -------------------------------------------------------
    def create(self, outcome: str, contract: dict[str, str]) -> dict[str, Any]:
        # Atomic mkdir coordinates plugin instances/processes only. Native UI and
        # judge writers do not use it; this is not a shared native CAS.
        try:
            lock = self._audit_path(create=True).with_suffix(".create.lock")
            lock.mkdir()
        except OSError:
            raise GoalError("create_locked", "Goal creation is locked or unavailable; no native write made.") from None
        try:
            manager = self._native()
            if manager.state is not None or self.load() is not None:
                raise GoalError("goal_exists", "Create-once cannot replace any existing goal record.")
            state = manager.set(outcome, contract=self.goals.GoalContract(**contract))
            return asdict(state)
        finally:
            # Do not auto-remove a pre-existing/stale lock: fail closed instead.
            lock.rmdir()


    # ---- audit --------------------------------------------------------
    def _audit_path(self, *, create=False) -> Path:
        digest = hashlib.sha256(self.session_key().encode("utf-8")).hexdigest()[:32]
        if self._data_dir_getter is not None:
            base = Path(self._data_dir_getter())
        elif create:
            from plugins.plugin_storage import plugin_data_dir
            base = plugin_data_dir(PLUGIN_NAME)
        else:
            # Host plugin_data_dir() always mkdirs. Mirror its documented path
            # only for reads; writes still resolve through the supported API.
            from hermes_constants import get_hermes_home
            base = get_hermes_home() / "plugin-data" / PLUGIN_NAME
        root = base / "goals"
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root / f"{digest}.json"

    def audit_binding(self, generation):
        from .audit import AuditError, BindingUnavailable, binding_for, read_payload
        try:
            data = read_payload(self._audit_path())
            if data is None:
                return None, "missing"
            if data.get("generation") != generation:
                return None, "generation_mismatch"
            return binding_for(data, generation), "confirmed"
        except BindingUnavailable:
            return None, "missing"
        except (AuditError, OSError, ValueError, TypeError):
            return None, "failed"

    def audit(self, event: dict[str, Any]) -> bool:
        """Confirm audit separately; native mutation is never rolled back here."""
        from .audit import AuditError, append_event
        try:
            state = self.load()
            if state is None or state.get("created_at") != (event.get("after") or {}).get("created_at"):
                return False
            append_event(self._audit_path(create=True), event, keep=AUDIT_EVENTS_KEPT)
        except (AuditError, OSError, ValueError, TypeError, GoalError):
            return False
        return True
