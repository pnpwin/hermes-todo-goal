"""The single model-facing contract for the ``goal_control`` tool.

Every field the model may set is listed here; everything else (session, profile,
home, database path, approval flags, budget, shell gates) is deliberately absent.
The runtime rejects unknown arguments too, so a stale model schema cannot smuggle
one past the handler.
"""

from __future__ import annotations

from .contract import CONTRACT_FIELDS, MAX_CONTRACT_FIELD_CHARS
from .service import ACTIONS

_TOOL_DESCRIPTION = (
    "Manage the Goal of the CURRENT chat session: status, create, update, pause, resume, "
    "complete. A Goal is the completion contract Hermes itself keeps judging after each "
    "turn; it is independent of any todo list. Use 'create' when the user asks for work to "
    "be carried through (not for 'just make a plan'). 'update' only accepts an identical "
    "contract as a no-op; changes require native user control. 'pause'/'resume' manage "
    "continuation, and 'complete' to submit evidence for verification. 'complete' does not "
    "mark the goal done - Hermes' own judge and quality gates decide that at the turn "
    "boundary, so put the evidence in your final reply as well. Todos are optional: pass "
    "expected_todo_revision to bind an existing plan for audit only."
)

FIELD_HINTS = {
    "outcome": "What must exist at the end.",
    "verification": "Which tests, commands or artifacts prove it.",
    "constraints": "What must not change.",
    "boundaries": "Where acting is permitted.",
    "stop_when": "When a human decision is required instead of continuing.",
}

GOAL_SCHEMA: dict = {
    "name": "goal_control",
    "description": _TOOL_DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": list(ACTIONS)},
            "contract": {
                "type": "object",
                "description": (
                    "Required for create and update: the completion contract. All five fields "
                    "are required and must be non-blank text describing the result, not a "
                    "checklist of steps."
                ),
                "properties": {
                    field: {
                        "type": "string",
                        "maxLength": MAX_CONTRACT_FIELD_CHARS,
                        "description": FIELD_HINTS[field],
                    }
                    for field in CONTRACT_FIELDS
                },
                "required": list(CONTRACT_FIELDS),
                "additionalProperties": False,
            },
            "expected_revision": {
                "type": "string",
                "description": (
                    "Opaque revision returned by action=status. Required for update, pause, "
                    "resume and complete so a change made elsewhere is not overwritten."
                ),
            },
            "expected_todo_revision": {
                "type": "integer",
                "minimum": 0,
                "description": (
                    "Optional, create only: the revision from the latest todo_list result. "
                    "Binds the plan to the goal for audit; it does not make the goal depend "
                    "on todos and never counts as proof of completion."
                ),
            },
            "reason": {
                "type": "string",
                "maxLength": 500,
                "description": "Why the goal is being paused or resumed. Required for both.",
            },
            "evidence": {
                "type": "string",
                "maxLength": 4000,
                "description": (
                    "Complete only: concrete evidence of the result (test output, artifact "
                    "path, diff). Not a claim that the work is done."
                ),
            },
        },
        "required": ["action"],
        "additionalProperties": False,
    },
}
