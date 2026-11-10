"""Native adapter tests against the real installed Hermes goal storage.

Requires the Hermes runtime (repo on PYTHONPATH) and a throwaway HERMES_HOME, so
nothing here reads or writes the live profile:

    HERMES_DISABLE_LAZY_INSTALLS=1 HERMES_HOME=$(mktemp -d) \
        PYTHONPATH=/Users/somovis/.hermes/hermes-agent \
        /Users/somovis/.hermes/hermes-agent/venv/bin/python -B -m unittest \
        discover -s tests -p 'test_desktop.py' -v

``HERMES_DISABLE_LAZY_INSTALLS=1`` is required: pointing HERMES_HOME at a fresh
directory otherwise triggers Hermes' dependency bootstrap, which re-executes the
interpreter mid-run and breaks module imports.

These exercise the real ``GoalManager``, the real SessionDB and the real
todo-tool transcript shape. They do NOT call an LLM: the judge and continuation
loop belong to Hermes and are out of scope for an offline gate.
"""
import json
import os
import tempfile
import unittest

_TMP_HOME = tempfile.mkdtemp(prefix="goal-plugin-home-")
os.environ["HERMES_HOME"] = _TMP_HOME

from src import plugin  # noqa: E402
from src.desktop import HermesGoalAdapter, missing_symbols  # noqa: E402
from src.service import GoalError, GoalService, state_revision  # noqa: E402

CONTRACT = {
    "outcome": "Produce a working artifact",
    "verification": "Run artifact tests",
    "constraints": "Preserve the public API",
    "boundaries": "Task-owned files only",
    "stop_when": "Ask before deploying",
}

_todo_counter = 0


def _key() -> str:
    global _todo_counter
    _todo_counter += 1
    return f"test-session-{_todo_counter}"


class NativeAdapterTest(unittest.TestCase):
    def setUp(self):
        self.key = _key()
        self.transcript = self.key + "-transcript"
        self.adapter = HermesGoalAdapter(session_key_getter=lambda: self.key,
                                        transcript_id_getter=lambda: self.transcript)
        self.service = GoalService(self.adapter)

    def tearDown(self):
        from hermes_cli import goals

        goals.clear_goal(self.key)

    def test_host_symbols_present(self):
        self.assertEqual(missing_symbols(), [])

    def test_create_and_status_roundtrip(self):
        created = self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        self.assertTrue(created["changed"])
        self.assertEqual(created["state"]["status"], "active")

        status = self.service.handle({"action": "status"})
        self.assertEqual(status["state"]["goal"], CONTRACT["outcome"])
        self.assertEqual(status["state"]["contract"], dict(CONTRACT))
        self.assertEqual(status["revision"], created["revision"])

    def test_duplicate_create_is_unchanged(self):
        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        again = self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        self.assertFalse(again["changed"])

    def test_update_preserves_budget_gates_subgoals(self):
        from hermes_cli import goals

        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        manager = goals.GoalManager(session_id=self.key)
        manager.add_subgoal("keep the docs")
        manager.add_gate("true")
        state = goals.load_goal(self.key)
        state.turns_used = 5
        goals.save_goal(self.key, state)

        before = self.service.handle({"action": "status"})
        with self.assertRaises(GoalError) as caught:
            self.service.handle({
                "action": "update",
                "expected_revision": before["revision"],
                "contract": {**CONTRACT, "verification": "Run artifact tests twice"},
            })
        self.assertEqual(caught.exception.code, "contract_change_requires_user")
        after = self.service.handle({"action": "status"})["state"]
        self.assertEqual(after, before["state"])
        self.assertEqual(after["contract"]["verification"], CONTRACT["verification"])
        self.assertEqual(after["turns_used"], 5)
        self.assertEqual(after["subgoals"], ["keep the docs"])
        self.assertEqual(len(after["gates"]), 1)

    def test_pause_and_resume_preserve_turns(self):
        from hermes_cli import goals

        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        state = goals.load_goal(self.key)
        state.turns_used = 4
        goals.save_goal(self.key, state)

        paused = self.service.handle({
            "action": "pause", "expected_revision": state_revision(self.adapter.load()),
            "reason": "waiting for a decision",
        })
        self.assertEqual(paused["state"]["status"], "paused")
        self.assertTrue(paused["state"]["paused_reason"].startswith("agent:"))

        resumed = self.service.handle({
            "action": "resume", "expected_revision": paused["revision"], "reason": "user agreed",
        })
        self.assertEqual(resumed["state"]["status"], "active")
        self.assertEqual(resumed["state"]["turns_used"], 4)

    def test_no_session_binding_is_refused(self):
        adapter = HermesGoalAdapter(session_key_getter=lambda: "")
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({"action": "status"})
        self.assertEqual(ctx.exception.code, "no_session")

    def test_goal_is_per_session(self):
        other = HermesGoalAdapter(session_key_getter=lambda: self.key + "-other")
        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        try:
            self.assertIsNone(GoalService(other).handle({"action": "status"})["state"])
        finally:
            from hermes_cli import goals

            goals.clear_goal(self.key + "-other")

    def test_todo_snapshot_uses_newest_paired_result(self):
        payload = {"todos": [{"id": "a", "content": "x", "status": "pending"}], "revision": 3,
                   "summary": {"total": 1}}
        self._append_todo_turn(json.dumps(payload))
        snapshot = self.adapter.todo_snapshot()
        self.assertEqual(snapshot, {"revision": 3, "todos": payload["todos"]})

    def test_todo_snapshot_ignores_unpaired_tool_row(self):
        payload = json.dumps({"todos": [{"id": "a", "content": "x", "status": "pending"}],
                              "revision": 9})
        db = self.adapter._store()
        db.ensure_session(self.transcript, source="test")
        db.append_message(self.transcript, "user", "hello")
        db.append_message(self.transcript, "tool", payload, tool_name="todo_list")
        self.assertIsNone(self.adapter.todo_snapshot())

    def test_create_with_todo_binding_uses_live_transcript(self):
        payload = {"todos": [{"id": "a", "content": "x", "status": "in_progress"}], "revision": 4}
        self._append_todo_turn(json.dumps(payload))
        bound = self.service.handle({
            "action": "create", "contract": dict(CONTRACT), "expected_todo_revision": 4,
        })
        self.assertEqual(bound["todo_binding"], {"revision": 4, "ids": ["a"]})

    def test_turn_boundary_manager_sees_tool_created_goal(self):
        """What tui_gateway._active_goal_manager builds at the next turn boundary."""
        from hermes_cli import goals

        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        boundary = goals.GoalManager(session_id=self.key)
        self.assertTrue(boundary.is_active())
        self.assertEqual(boundary.state.contract.verification, CONTRACT["verification"])
        self.assertEqual(boundary.state.max_turns, 20)

        paused = self.service.handle({
            "action": "pause", "expected_revision": state_revision(self.adapter.load()),
            "reason": "waiting for input",
        })
        self.assertEqual(paused["state"]["status"], "paused")
        self.assertFalse(goals.GoalManager(session_id=self.key).is_active())

    def test_json_tool_boundary_over_the_real_adapter(self):
        handler = plugin.build_handler(
            lambda: HermesGoalAdapter(session_key_getter=lambda: self.key))
        created = json.loads(handler({"action": "create", "contract": dict(CONTRACT)}))
        self.assertTrue(created["ok"])
        self.assertTrue(created["changed"])
        status = json.loads(handler({"action": "status"}))
        self.assertEqual(status["state"]["goal"], CONTRACT["outcome"])
        self.assertEqual(status["revision"], created["revision"])
        repeat = json.loads(handler({"action": "create", "contract": dict(CONTRACT)}))
        self.assertTrue(repeat["ok"])
        self.assertFalse(repeat["changed"])
        bad = json.loads(handler({"action": "update", "expected_revision": "stale",
                                  "contract": dict(CONTRACT)}))
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "revision_stale")

    def test_audit_file_records_mutation(self):
        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        path = self.adapter._audit_path()
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["events"][-1]["event"], "create")
        self.assertNotIn(self.key, path.read_text(encoding="utf-8"))

    def test_dispatch_uses_distinct_transcript_identity(self):
        from gateway.session_context import scoped_session_key
        from tools.registry import registry
        from model_tools import handle_function_call
        from hermes_cli import goals
        transcript = self.key + "-transcript"
        self._append_todo_turn(json.dumps({"revision": 4, "todos": [
            {"id": "a", "content": "task", "status": "pending"}]}), session_id=transcript)

        class Context:
            def register_tool(self, **kwargs):
                registry.register(**kwargs)
            def register_skill(self, *args, **kwargs):
                pass

        plugin.register(Context())
        with scoped_session_key(self.key):
            raw = handle_function_call("goal_control", {
                "action": "create", "contract": dict(CONTRACT), "expected_todo_revision": 4,
            }, session_id=transcript, conversation_id="untrusted-parent",
                enabled_tools=["goal_control"])
        result = json.loads(raw)
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["todo_binding"], {"revision": 4, "ids": ["a"]})
        self.assertIsNotNone(goals.load_goal(self.key))
        self.assertIsNone(goals.load_goal(transcript))

    def test_delegated_child_cannot_use_parent_goal(self):
        from gateway.session_context import scoped_session_key
        from agent.delegation_context import delegated_child_context
        handler = plugin.build_handler(HermesGoalAdapter)
        with scoped_session_key(self.key), delegated_child_context("child"):
            result = json.loads(handler({"action": "create", "contract": dict(CONTRACT)}))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "no_session")
        self.assertIsNone(self.adapter.load())

    def test_missing_transcript_refuses_binding_but_allows_standalone(self):
        from gateway.session_context import scoped_session_key
        from test_plugin import RecordingContext
        ctx = RecordingContext()
        plugin.register(ctx)
        handler = ctx.tools["goal_control"]["handler"]
        with scoped_session_key(self.key):
            bound = json.loads(handler({"action": "create", "contract": dict(CONTRACT),
                "expected_todo_revision": 4}, conversation_id=self.transcript))
            self.assertFalse(bound["ok"])
            self.assertEqual(bound["error"]["code"], "todo_unavailable")
            self.assertIsNone(self.adapter.load())
            standalone = json.loads(handler({"action": "create", "contract": dict(CONTRACT)}))
            self.assertTrue(standalone["ok"], standalone)

    def test_todo_pairing_rejects_wrong_call_or_boundary(self):
        from src.desktop import _paired_with_todo_call
        assistant = {"role": "assistant", "tool_calls": [
            {"id": "c1", "function": {"name": "todo_list", "arguments": "{}"}}]}
        for between, ident in (([], "wrong"), ([{"role": "user"}], "c1"),
                                ([{"role": "assistant", "content": "new"}], "c1")):
            with self.subTest(between=between, ident=ident):
                rows = [assistant, *between, {"role": "tool", "tool_call_id": ident}]
                self.assertFalse(_paired_with_todo_call(rows, len(rows)-1))

    def test_latest_invalid_paired_todo_does_not_revive_old_plan(self):
        self._append_todo_turn(json.dumps({"revision": 1, "todos": []}))
        self._append_todo_turn(json.dumps({"revision": True, "todos": []}))
        self.assertIsNone(self.adapter.todo_snapshot())

    def test_latest_paired_clear_is_authoritative(self):
        self._append_todo_turn(json.dumps({"revision": 1, "todos": [
            {"id": "a", "content": "task", "status": "pending"}]}))
        self._append_todo_turn(json.dumps({"revision": 2, "todos": []}))
        self.assertEqual(self.adapter.todo_snapshot(), {"revision": 2, "todos": []})

    def test_unpaired_new_result_does_not_hide_paired_snapshot(self):
        self._append_todo_turn(json.dumps({"revision": 1, "todos": []}))
        self.adapter._store().append_message(self.transcript, "tool",
            json.dumps({"revision": 99, "todos": []}), tool_call_id="wrong")
        self.assertEqual(self.adapter.todo_snapshot(), {"revision": 1, "todos": []})

    def test_todo_native_alias_and_bridge_predicate(self):
        from src.desktop import _paired_with_todo_call
        from tools.tool_search_catalog import TOOL_CALL_NAME
        entry = {"name": "todo_list", "arguments": {}}
        for name, args, expected in (
            ("todo", {}, True),
            (TOOL_CALL_NAME, {"calls": [entry]}, True),
            (TOOL_CALL_NAME, {"calls": [entry, entry]}, False),
        ):
            with self.subTest(name=name, args=args):
                rows = [{"role": "assistant", "tool_calls": [{"id": "x", "function":
                    {"name": name, "arguments": json.dumps(args)}}]},
                    {"role": "tool", "tool_call_id": "x"}]
                self.assertEqual(_paired_with_todo_call(rows, 1), expected)

    def test_bound_goal_status_survives_adapter_reconstruction(self):
        self._append_todo_turn(json.dumps({"revision": 4, "todos": [
            {"id": "a", "content": "task", "status": "pending"}]}))
        self.service.handle({"action": "create", "contract": dict(CONTRACT),
                             "expected_todo_revision": 4})
        restored = GoalService(HermesGoalAdapter(session_key_getter=lambda: self.key))
        result = restored.handle({"action": "status"})
        self.assertEqual(result["todo_binding"], {"revision": 4, "ids": ["a"]})
        self.assertEqual(result["audit_status"], "confirmed")

    def test_status_does_not_create_audit_directories(self):
        from pathlib import Path
        from hermes_cli import goals
        goals.GoalManager(self.key).set(CONTRACT["outcome"], contract=goals.GoalContract(**CONTRACT))
        root = Path(_TMP_HOME) / (self.key + "-read-only")
        adapter = HermesGoalAdapter(session_key_getter=lambda: self.key,
                                    data_dir_getter=lambda: root)
        status = GoalService(adapter).handle({"action": "status"})
        self.assertTrue(status["ok"])
        self.assertFalse(root.exists())
        self.assertEqual(status["audit_status"], "missing")

    def test_new_goal_replaces_audit_generation_and_binding(self):
        from hermes_cli import goals
        self._append_todo_turn(json.dumps({"revision": 4, "todos": [
            {"id": "a", "content": "task", "status": "pending"}]}))
        first = self.service.handle({"action": "create", "contract": dict(CONTRACT),
                                     "expected_todo_revision": 4})
        goals.clear_goal(self.key)
        second = self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        data = json.loads(self.adapter._audit_path().read_text())
        self.assertNotEqual(first["state"]["created_at"], second["state"]["created_at"])
        self.assertEqual(data["generation"], second["state"]["created_at"])
        self.assertIsNone(self.service.handle({"action": "status"})["todo_binding"])

    def test_audit_parent_failure_reports_partial_native_state(self):
        from pathlib import Path
        root = Path(_TMP_HOME) / (self.key + "-not-a-directory")
        root.write_text("keep")
        adapter = HermesGoalAdapter(session_key_getter=lambda: self.key,
                                    data_dir_getter=lambda: root)
        result = GoalService(adapter).handle({"action": "create", "contract": dict(CONTRACT)})
        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "partial")
        self.assertEqual(result["state"]["status"], "active")
        self.assertEqual(root.read_text(), "keep")

    def test_corrupt_audit_status_is_read_only(self):
        self.service.handle({"action": "create", "contract": dict(CONTRACT)})
        path = self.adapter._audit_path()
        path.write_text("broken-json")
        status = self.service.handle({"action": "status"})
        self.assertTrue(status["ok"])
        self.assertEqual(status["audit_status"], "failed")
        self.assertIsNone(status["todo_binding"])
        self.assertIn("audit_warning", status)
        self.assertEqual(path.read_text(), "broken-json")

    def test_default_audit_read_does_not_call_creating_host_helper(self):
        from pathlib import Path
        from unittest.mock import patch
        root = Path(_TMP_HOME) / (self.key + "-default-read")
        with patch("hermes_constants.get_hermes_home", return_value=root), patch(
            "plugins.plugin_storage.plugin_data_dir", side_effect=AssertionError("write on read")):
            self.assertEqual(self.adapter.audit_binding(1.0), (None, "missing"))
        self.assertFalse(root.exists())

    def test_two_profile_processes_isolate_same_route_key(self):
        import subprocess
        import sys
        from pathlib import Path
        code = '''import json, os
from src.desktop import HermesGoalAdapter
from src.service import GoalService
from hermes_cli import goals
adapter = HermesGoalAdapter(session_key_getter=lambda: "same-route")
contract = {key: key for key in ("outcome", "verification", "constraints", "boundaries", "stop_when")}
result = GoalService(adapter).handle({"action": "create", "contract": contract})
print(json.dumps({"ok": result["ok"], "audit": str(adapter._audit_path()), "generation": result["state"]["created_at"]}))
goals.clear_goal("same-route")
'''
        receipts = []
        for name in ("profile-a", "profile-b"):
            home = Path(_TMP_HOME) / self.key / name
            home.mkdir(parents=True)
            env = {**os.environ, "HOME": str(home), "HERMES_HOME": str(home / ".hermes"),
                   "HERMES_DISABLE_LAZY_INSTALLS": "1", "UV_OFFLINE": "1"}
            child = subprocess.run([sys.executable, "-B", "-c", code], env=env,
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(child.returncode, 0, child.stderr)
            receipt = json.loads(child.stdout)
            self.assertTrue(receipt["ok"], receipt)
            self.assertTrue(Path(receipt["audit"]).is_relative_to(home))
            receipts.append(receipt)
        self.assertNotEqual(receipts[0]["audit"], receipts[1]["audit"])
        for receipt in receipts:
            self.assertEqual(json.loads(Path(receipt["audit"]).read_text())["generation"],
                             receipt["generation"])

    # ---- helpers ------------------------------------------------------
    def _append_todo_turn(self, payload_json: str, session_id=None) -> None:
        db = self.adapter._store()
        sid = session_id or self.transcript
        db.ensure_session(sid, source="test")
        db.append_message(sid, "user", "plan this")
        db.append_message(
            sid, "assistant", "",
            tool_calls=[{"id": "call-1", "type": "function",
                         "function": {"name": "todo_list", "arguments": "{}"}}],
        )
        db.append_message(sid, "tool", payload_json, tool_name="todo_list",
                          tool_call_id="call-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
