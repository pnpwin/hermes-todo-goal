"""Pair persisted TODO results with their exact nearest assistant call."""


def paired_with_todo_call(rows, index, is_todo_call):
    if not 0 <= index < len(rows):
        return False
    result = rows[index]
    call_id = result.get("tool_call_id")
    if result.get("role") != "tool" or not isinstance(call_id, str) or not call_id:
        return False
    for prior in reversed(rows[:index]):
        role = prior.get("role")
        if role in {"user", "system"}:
            return False
        if role == "assistant":
            calls = prior.get("tool_calls")
            if not isinstance(calls, list):
                return False
            return any(isinstance(call, dict) and call.get("id") == call_id
                       and is_todo_call(call) for call in calls)
    return False
