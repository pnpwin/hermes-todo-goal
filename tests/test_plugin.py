"""Tool schema, JSON boundary and registration tests (no Hermes runtime needed).

Run: python3.15t -B -m unittest discover -s tests -p 'test_plugin.py' -v
"""
import json
import unittest
from unittest import mock

from src import plugin
from src.schema import GOAL_SCHEMA
from src.service import GoalError

CONTRACT = {
    "outcome": "Produce a working artifact",
    "verification": "Run artifact tests",
    "constraints": "Preserve the public API",
    "boundaries": "Task-owned files only",
    "stop_when": "Ask before deploying",
}


class RecordingContext:
    def __init__(self):
        self.tools = {}
        self.skills = {}

    def register_tool(self, name, toolset, schema, handler, **kwargs):
        self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler, **kwargs}

    def register_skill(self, name, path, description=""):
        self.skills[name] = {"path": path, "description": description}


class StubAdapter:
    def __init__(self, state=None, raises=None):
        self.state = state
        self.raises = raises

    def session_key(self):
        return "s"

    def load(self):
        if self.raises:
            raise self.raises
        return self.state

    def todo_snapshot(self):
        return None

    def audit(self, event):
        return True


class SchemaTest(unittest.TestCase):
    def test_tool_is_named_goal_control(self):
        self.assertEqual(GOAL_SCHEMA["name"], "goal_control")
        self.assertEqual(plugin.TOOL_NAME, "goal_control")

    def test_schema_exposes_six_actions(self):
        action = GOAL_SCHEMA["parameters"]["properties"]["action"]
        self.assertEqual(action["enum"],
                         ["status", "create", "update", "pause", "resume", "complete"])

    def test_schema_forbids_unknown_arguments(self):
        params = GOAL_SCHEMA["parameters"]
        self.assertFalse(params["additionalProperties"])
        self.assertEqual(params["required"], ["action"])
        for forbidden in ("session_id", "profile", "approved", "reset_budget", "home", "db_path"):
            self.assertNotIn(forbidden, params["properties"])

    def test_contract_requires_all_five_fields(self):
        contract = GOAL_SCHEMA["parameters"]["properties"]["contract"]
        self.assertEqual(sorted(contract["required"]),
                         sorted(["outcome", "verification", "constraints", "boundaries", "stop_when"]))
        self.assertFalse(contract["additionalProperties"])


class HandlerTest(unittest.TestCase):
    def test_unknown_argument_is_refused(self):
        handler = plugin.build_handler(lambda: StubAdapter())
        result = json.loads(handler({"action": "create", "contract": CONTRACT,
                                     "session_id": "other"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "arg_unknown")

    def test_goal_error_becomes_structured_refusal(self):
        handler = plugin.build_handler(lambda: StubAdapter())
        result = json.loads(handler({"action": "status", "expected_revision": "x"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "arg_unknown")  # status takes no arguments

    def test_missing_goal_is_reported_as_json(self):
        handler = plugin.build_handler(lambda: StubAdapter(state=None))
        result = json.loads(handler({"action": "pause", "expected_revision": "none",
                                     "reason": "x"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "no_goal")

    def test_internal_error_never_leaks_a_traceback(self):
        handler = plugin.build_handler(lambda: StubAdapter(raises=RuntimeError("boom\nTraceback")))
        result = json.loads(handler({"action": "status"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "internal_error")
        self.assertNotIn("Traceback", result["error"]["message"])

    def test_status_result_is_json_serialisable(self):
        handler = plugin.build_handler(lambda: StubAdapter(state=None))
        result = json.loads(handler({"action": "status"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], "none")


class RegistrationTest(unittest.TestCase):
    def test_registers_one_tool_and_one_bundled_skill(self):
        ctx = RecordingContext()
        with mock.patch("src.desktop.missing_symbols", return_value=[]):
            plugin.register(ctx)
        self.assertEqual(list(ctx.tools), ["goal_control"])
        self.assertEqual(ctx.tools["goal_control"]["toolset"], "goals")
        self.assertEqual(list(ctx.skills), ["goal-workflow"])
        self.assertTrue(ctx.skills["goal-workflow"]["path"].is_file())

    def test_incompatible_host_registers_an_explicit_refusal(self):
        ctx = RecordingContext()
        with mock.patch("src.desktop.missing_symbols", return_value=["hermes_cli.goals.load_goal"]):
            plugin.register(ctx)
        result = json.loads(ctx.tools["goal_control"]["handler"]({"action": "status"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "host_incompatible")
        self.assertIn("hermes_cli.goals.load_goal", result["error"]["message"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
