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
    "Create-once Goal control for the CURRENT chat: status, create, complete. "
    "Use create for directly requested, verifiable work likely to need continuation across turns; "
    "not short tasks, questions, exploration or plan-only requests. Multiple tool calls alone "
    "do not justify a Goal. Read status first: create only when state is null. "
    "Never replace active, paused, done or cleared goals. Update, pause and resume are disabled; "
    "use native user control, never clear/recreate as a workaround. Complete only records "
    "evidence for verification; it never changes native state or marks done. Repeat evidence "
    "in your reply: the native judge and gates decide at the turn boundary. Optional "
    "expected_todo_revision binds a persisted plan for audit only. Initial creation is not "
    "atomic against simultaneous native UI writes; do not manage the Goal concurrently."
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
                    "Required for create: the completion contract. All five fields "
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
                    "Opaque revision returned by action=status. Required for complete "
                    "for stale-snapshot preflight rejection. This is not "
                    "an atomic compare-and-set or an ABA-safe version."
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
