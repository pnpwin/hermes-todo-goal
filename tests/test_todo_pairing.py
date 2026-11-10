import unittest
from src.todo_pairing import paired_with_todo_call


def call(name="todo_list", ident="c1"):
    return {"id": ident, "function": {"name": name, "arguments": "{}"}}


def is_todo(value):
    return value.get("function", {}).get("name") == "todo_list"


class PairingTest(unittest.TestCase):
    def test_exact_pair(self):
        self.assertTrue(paired_with_todo_call([
            {"role": "assistant", "tool_calls": [call()]},
            {"role": "tool", "tool_call_id": "c1"}], 1, is_todo))

    def test_wrong_id(self):
        self.assertFalse(paired_with_todo_call([
            {"role": "assistant", "tool_calls": [call()]},
            {"role": "tool", "tool_call_id": "other"}], 1, is_todo))

    def test_boundaries(self):
        for role in ("user", "system", "assistant"):
            with self.subTest(role=role):
                rows = [{"role": "assistant", "tool_calls": [call()]},
                        {"role": role}, {"role": "tool", "tool_call_id": "c1"}]
                self.assertFalse(paired_with_todo_call(rows, 2, is_todo))

    def test_missing_id(self):
        rows = [{"role": "assistant", "tool_calls": [call()]}, {"role": "tool"}]
        self.assertFalse(paired_with_todo_call(rows, 1, is_todo))

    def test_non_todo_matching_id(self):
        rows = [{"role": "assistant", "tool_calls": [call("terminal")]},
                {"role": "tool", "tool_call_id": "c1"}]
        self.assertFalse(paired_with_todo_call(rows, 1, is_todo))

    def test_malformed_calls(self):
        for calls in (None, {}, [None], ["todo_list"]):
            with self.subTest(calls=calls):
                rows = [{"role": "assistant", "tool_calls": calls},
                        {"role": "tool", "tool_call_id": "c1"}]
                self.assertFalse(paired_with_todo_call(rows, 1, is_todo))

    def test_invalid_position_or_role(self):
        self.assertFalse(paired_with_todo_call([], 0, is_todo))
        self.assertFalse(paired_with_todo_call([{"role": "user"}], 0, is_todo))
