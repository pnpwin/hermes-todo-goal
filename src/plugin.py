"""Native plugin registration: one tool, one bundled skill, no background work.

The handler is a thin JSON boundary. All policy lives in :mod:`src.service`, all
host access in :mod:`src.desktop`, so a failure here cannot leave a half-written
goal: the adapter writes only through the host's own goal storage and the service
re-reads it before reporting success.
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

# Always-on routing rule. The skill only fires when the model already decided to
# load it, which is exactly the case a user's wording may not create; this section
# is frozen into every new session's system prompt, so the decision happens there.
SECTION_ID = "goal-routing"
SECTION_TEXT = """## When to open a goal

Open a goal with `goal_control` (action `create`) before starting work when all of these hold:
- the request needs more than one step or tool call to finish;
- the user expects a result that can be checked afterwards (a file, a test, a command
  output, a diff);
- the user asked for the work to be done, not to discuss, plan, or explain it.

Do not open a goal for a question, a single action, a plan-only request, or exploration,
and do not open a second goal while one is active (`status` first, then `update`).

The contract is the completion test: `outcome` (what must exist), `verification` (a
command the user can re-run), `constraints` (what must not change), `boundaries` (where
acting is allowed), `stop_when` (when to ask instead of continuing). Finish the work,
then call `complete` with concrete evidence — command output, path, diff. Never claim
completion from intent or from a checked todo item: the turn-boundary judge decides."""


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

    # Older hosts have no prompt sections; the tool and skill still register there.
    register_section = getattr(ctx, "register_system_prompt_section", None)
    if register_section is None:
        return
    try:
        register_section(SECTION_ID, SECTION_TEXT, position="after_memory",
                         max_chars=2000)
    except (ValueError, TypeError) as exc:  # another plugin holds the id, or host moved on
        logger.warning("goal_control: prompt section %s not registered: %s", SECTION_ID, exc)
