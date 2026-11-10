"""Native plugin registration: one tool, one bundled skill, no background work.

The handler is a thin JSON boundary. All policy lives in :mod:`src.service`, all
host access in :mod:`src.desktop`. Native state and audit are not one transaction:
the service verifies native readback and reports partial/unknown effects honestly.
The adapter writes goals only through the host's own storage.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

from .schema import GOAL_SCHEMA
from .service import GoalError, GoalService

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "goal-workflow" / "SKILL.md"
TOOL_NAME = "goal_control"
TOOLSET = "goals"
SKILL_NAME = "goal-workflow"
SKILL_DESCRIPTION = "Use when managing goals or linking execution plans."

# Optional pre-selection guidance; authority and lifecycle remain host/service checks.
SECTION_ID = "goal-routing"
SECTION_TEXT = """## Long-running goal routing

Use goal_control for a directly requested, verifiable outcome that is likely to
need continuation across agent turns or waiting for task-owned work to finish.
The user need not say 'goal'. Multiple tool calls alone do not justify a goal.
Do not create a goal for questions, explanations, exploration, plan-only requests,
or short tasks that can be completed and verified in the current turn.

Before creating a goal, read status. Do not replace, rewrite or resume an existing
goal just because a new request arrived. If it is unrelated or user-paused, ask the
user through the native control; reason text is not authorization. Use only the
current session. Never increase budget, bypass gates or choose another profile.

Describe outcome, verification, constraints, boundaries and stop_when from the
user's request. A TODO is optional and is never evidence of completion. Carry out
the authorized work and run the verification. Request complete with concrete
evidence and include it in the reply. Complete does not mark done: Hermes' native
judge and gates decide. On a partial or unknown write outcome, read status and
report the uncertainty; never automatically retry the mutation."""


def _atomicity_available() -> bool:
    """No shared host CAS seam is qualified yet; never infer it from config.

    Replace only after the companion host implementation and all-writer native
    acceptance (including UI/judge/ABA) are approved. Registration unit tests may
    stub this probe; that does not enable the production capability.
    """
    return False


def _refuse(code: str, message: str) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}},
                      ensure_ascii=False)


def build_handler(adapter_factory: Callable[..., Any], *, dispatch_session: bool = False) -> Callable[..., str]:
    """JSON-in/JSON-out handler over a per-call host adapter."""

    def handler(params: dict | None = None, *, session_id: str | None = None,
                **_kwargs: Any) -> str:
        try:
            adapter = (adapter_factory(session_id=session_id)
                       if dispatch_session else adapter_factory())
            result = GoalService(adapter).handle(params if params is not None else {})
        except GoalError as exc:
            action = params.get("action") if isinstance(params, dict) else None
            return json.dumps({"ok": False, "action": action,
                               "error": exc.to_dict(), **exc.details}, ensure_ascii=False)
        except Exception as exc:  # never leak a traceback into the model's context
            return _refuse("internal_error", type(exc).__name__)
        return json.dumps(result, ensure_ascii=False)

    return handler


def register(ctx) -> None:
    from .desktop import HermesGoalAdapter, missing_symbols

    missing = missing_symbols()
    if missing:
        # Registered but explicitly unavailable: discovery stays stable and every
        # call fails closed with the exact host symbols this build expects.
        handler = lambda params=None, **_kw: _refuse(
            "host_incompatible",
            "this Hermes build is missing the host API the goal tool needs: " + ", ".join(missing),
        )
    else:
        handler = build_handler(
            lambda *, session_id=None: HermesGoalAdapter(
                transcript_id_getter=lambda: session_id or ""),
            dispatch_session=True,
        )

    ctx.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=GOAL_SCHEMA,
        handler=handler,
        description=GOAL_SCHEMA["description"],
        emoji="🎯",
    )
    ctx.register_skill(SKILL_NAME, SKILL_PATH, description=SKILL_DESCRIPTION)

    # A config opt-in is not a host atomicity capability.
    get_config = getattr(ctx, "get_config", None)
    register_section = getattr(ctx, "register_system_prompt_section", None)
    if get_config is None or register_section is None:
        return
    if get_config("auto_create", False) is not True:
        return
    if missing or not _atomicity_available():
        return
    try:
        register_section(SECTION_ID, SECTION_TEXT, position="after_memory",
                         max_chars=2000)
    except (ValueError, TypeError) as exc:  # another plugin holds the id, or host moved on
        logger.warning("goal routing section unavailable: %s", type(exc).__name__)
