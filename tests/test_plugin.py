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
    def __init__(self, settings=None):
        self.tools = {}
        self.skills = {}
        self.settings = settings or {}
        self.sections = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_system_prompt_section(self, *args, **kwargs):
        self.sections.append((args, kwargs))

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

    def test_schema_exposes_three_actions(self):
        self.assertEqual(GOAL_SCHEMA["parameters"]["properties"]["action"]["enum"],
                         ["status", "create", "complete"])

    def test_schema_does_not_advertise_lifecycle_reason(self):
        self.assertNotIn("reason", GOAL_SCHEMA["parameters"]["properties"])

    def test_schema_forbids_unknown_arguments(self):
        params = GOAL_SCHEMA["parameters"]
        self.assertFalse(params["additionalProperties"])
        self.assertEqual(params["required"], ["action"])
        for forbidden in ("session_id", "profile", "approved", "reset_budget", "home", "db_path"):
            self.assertNotIn(forbidden, params["properties"])

    def test_description_matches_long_running_skill_policy(self):
        phrase = "likely to need continuation across turns"
        self.assertIn(phrase, GOAL_SCHEMA["description"])
        self.assertIn(phrase, plugin.SKILL_PATH.read_text())
        self.assertIn("short tasks", GOAL_SCHEMA["description"])

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
        result = json.loads(handler({"action": "complete", "expected_revision": "none",
                                     "evidence": "x"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "no_goal")

    def test_internal_error_never_leaks_a_traceback(self):
        handler = plugin.build_handler(lambda: StubAdapter(raises=RuntimeError("boom\nTraceback")))
        result = json.loads(handler({"action": "status"}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "internal_error")
        self.assertNotIn("Traceback", result["error"]["message"])

    def test_non_object_arguments_return_json(self):
        handler = plugin.build_handler(lambda: StubAdapter())
        result = json.loads(handler("status"))
        self.assertEqual(result["error"]["code"], "args_invalid")

    def test_internal_error_does_not_disclose_exception_text(self):
        handler = plugin.build_handler(lambda: StubAdapter(raises=RuntimeError("private-canary")))
        raw = handler({"action": "status"})
        self.assertNotIn("private-canary", raw)
        self.assertEqual(json.loads(raw)["error"]["code"], "internal_error")

    def test_failed_readback_reports_unknown_effect(self):
        from test_service import FakeAdapter, contract
        class BrokenReadback(FakeAdapter):
            def load(self):
                if self.writes:
                    raise OSError("private store failure")
                return super().load()
        adapter = BrokenReadback()
        result = json.loads(plugin.build_handler(lambda: adapter)(
            {"action": "create", "contract": contract()}))
        self.assertEqual(result.get("outcome"), "unknown")
        self.assertIsNone(result["changed"])
        self.assertIsNone(result["revision"])
        self.assertIsNone(result["state"])
        self.assertEqual(result["error"]["code"], "persistence_unverified")
        self.assertEqual(adapter.writes, 1)
        self.assertEqual(adapter.audits, [])

    def test_mutation_exception_preserves_unknown_effect(self):
        from test_service import FakeAdapter, contract, new_state
        from src.service import state_revision
        class WriteThenRaise(FakeAdapter):
            def _store(self, state):
                super()._store(state)
                raise OSError("private-write-error")
        for action in ("create",):
            with self.subTest(action=action):
                state = None if action == "create" else new_state()
                if action == "resume":
                    state = new_state(status="paused", paused_reason="agent:wait")
                adapter = WriteThenRaise(state=state)
                args = {"action": action}
                if action == "create":
                    args["contract"] = contract()
                else:
                    args.update(expected_revision=state_revision(state), reason="continue")
                raw = plugin.build_handler(lambda: adapter)(args)
                result = json.loads(raw)
                self.assertEqual(result.get("outcome"), "unknown")
                self.assertIsNone(result["changed"])
                self.assertEqual(result["revision"], state_revision(adapter.state))
                self.assertEqual(result["state"]["status"], adapter.state["status"])
                self.assertEqual(result["audit_status"], "not_attempted")
                self.assertIn("do not retry", result["error"]["message"])
                self.assertNotIn("private-write-error", raw)
                self.assertEqual(adapter.writes, 1)

    def test_status_result_is_json_serialisable(self):
        handler = plugin.build_handler(lambda: StubAdapter(state=None))
        result = json.loads(handler({"action": "status"}))
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], "none")


class RegistrationTest(unittest.TestCase):
    def test_routing_section_is_off_by_default(self):
        ctx = RecordingContext()
        with mock.patch("src.desktop.missing_symbols", return_value=[]):
            plugin.register(ctx)
        self.assertEqual(ctx.sections, [])
        self.assertIn("goal_control", ctx.tools)

    def test_routing_policy_requires_cross_turn_work(self):
        self.assertIn("Multiple tool calls alone do not justify a goal.", plugin.SECTION_TEXT)
        self.assertIn("Do not replace, rewrite or resume an existing", plugin.SECTION_TEXT)
        self.assertNotIn("status` first, then `update", plugin.SECTION_TEXT)
        self.assertLessEqual(len(plugin.SECTION_TEXT), 2000)

    def test_routing_requires_literal_opt_in(self):
        for setting, expected in ((True, 1), (False, 0), ("true", 0), (1, 0), (None, 0)):
            with self.subTest(setting=setting):
                ctx = RecordingContext({"auto_create": setting})
                with mock.patch("src.desktop.missing_symbols", return_value=[]):
                    plugin.register(ctx)
                self.assertEqual(len(ctx.sections), expected)
                self.assertIn("goal_control", ctx.tools)

    def test_create_once_opt_in_does_not_require_core_cas(self):
        ctx = RecordingContext({"auto_create": True})
        with mock.patch("src.desktop.missing_symbols", return_value=[]):
            plugin.register(ctx)
        self.assertEqual(len(ctx.sections), 1)
        self.assertEqual(ctx.sections[0], ((plugin.SECTION_ID, plugin.SECTION_TEXT),
                                         {"position": "after_memory", "max_chars": 2000}))

    def test_older_context_keeps_tool_and_skill(self):
        ctx = RecordingContext({"auto_create": True})
        ctx.register_system_prompt_section = None
        with mock.patch("src.desktop.missing_symbols", return_value=[]):
            plugin.register(ctx)
        self.assertIn("goal_control", ctx.tools)
        self.assertIn("goal-workflow", ctx.skills)

    def test_incompatible_host_never_registers_guidance(self):
        ctx = RecordingContext({"auto_create": True})
        with mock.patch("src.desktop.missing_symbols", return_value=["missing"]):
            plugin.register(ctx)
        self.assertEqual(ctx.sections, [])

    def test_section_failure_is_nonfatal_and_does_not_log_details(self):
        for failure in (ValueError, TypeError):
            with self.subTest(failure=failure):
                ctx = RecordingContext({"auto_create": True})
                ctx.register_system_prompt_section = mock.Mock(side_effect=failure("private-canary"))
                with mock.patch("src.desktop.missing_symbols", return_value=[]), self.assertLogs(
                    "src.plugin", level="WARNING") as captured:
                    plugin.register(ctx)
                self.assertIn("goal_control", ctx.tools)
                self.assertIn("goal-workflow", ctx.skills)
                self.assertNotIn("private-canary", str(captured.output))

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
