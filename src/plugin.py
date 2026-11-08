"""Native plugin registration: one tool, one bundled skill, no background work.

The handler is a thin JSON boundary. All policy lives in :mod:`src.service`, all
host access in :mod:`src.desktop`, so a failure here cannot leave a half-written
goal: the adapter writes only through the host's own goal storage and the service
re-reads it before reporting success.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .schema import GOAL_SCHEMA
from .service import GoalError, GoalService

ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = ROOT / "skills" / "goal-workflow" / "SKILL.md"
TOOL_NAME = "goal_control"
TOOLSET = "goals"
SKILL_NAME = "goal-workflow"
SKILL_DESCRIPTION = "Use when managing goals or linking execution plans."


def _refuse(code: str, message: str) -> str:
    return json.dumps({"ok": False, "error": {"code": code, "message": message}},
                      ensure_ascii=False)


def build_handler(adapter_factory: Callable[[], Any]) -> Callable[..., str]:
    """JSON-in/JSON-out handler over a per-call host adapter."""

    def handler(params: dict | None = None, **_kwargs: Any) -> str:
        try:
            result = GoalService(adapter_factory()).handle(params or {})
        except GoalError as exc:
            return json.dumps({"ok": False, "action": (params or {}).get("action"),
                               "error": exc.to_dict()}, ensure_ascii=False)
        except Exception as exc:  # never leak a traceback into the model's context
            first = (str(exc).splitlines() or [""])[0][:200]
            return _refuse("internal_error", f"{type(exc).__name__}: {first}".strip())
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
        handler = build_handler(HermesGoalAdapter)

    ctx.register_tool(
        name=TOOL_NAME,
        toolset=TOOLSET,
        schema=GOAL_SCHEMA,
        handler=handler,
        description=GOAL_SCHEMA["description"],
        emoji="🎯",
    )
    ctx.register_skill(SKILL_NAME, SKILL_PATH, description=SKILL_DESCRIPTION)
