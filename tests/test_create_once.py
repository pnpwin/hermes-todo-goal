"""Create-once policy: no lifecycle writes after initial goal creation."""
import unittest
from test_service import FakeAdapter, contract, new_state
from src.service import GoalError, GoalService, state_revision


class CreateOnceTest(unittest.TestCase):
    def test_legacy_mutations_refuse_without_any_effect(self):
        for action in ("update", "pause", "resume"):
            with self.subTest(action=action):
                state = new_state(status="paused", paused_reason="agent:waiting") if action == "resume" else new_state()
                adapter = FakeAdapter(state=state)
                args: dict = {"action": action, "expected_revision": state_revision(state)}
                args.update({"contract": contract()} if action == "update" else {"reason": "user agreed"})
                with self.assertRaises(GoalError) as caught:
                    GoalService(adapter).handle(args)
                self.assertEqual(caught.exception.code, "action_disabled")
                self.assertEqual(adapter.state, state)
                self.assertEqual(adapter.writes, 0)
                self.assertEqual(adapter.audits, [])

    def test_disabled_actions_do_not_even_read_host(self):
        class NoAccess:
            def load(self):
                raise AssertionError("disabled action touched host")
        for action in ("update", "pause", "resume"):
            with self.subTest(action=action):
                with self.assertRaises(GoalError) as caught:
                    GoalService(NoAccess()).handle({"action": action, "approved": True})
                self.assertEqual(caught.exception.code, "action_disabled")

    def test_disabled_actions_preserve_all_native_states(self):
        for action in ("update", "pause", "resume"):
            for status in ("active", "paused", "done", "cleared"):
                with self.subTest(action=action, status=status):
                    state = new_state(status=status, paused_reason="user-paused",
                                      waiting_on_pid=123, turns_used=20)
                    adapter = FakeAdapter(state=state)
                    with self.assertRaises(GoalError) as caught:
                        GoalService(adapter).handle({"action": action, "reason": "approved"})
                    self.assertEqual(caught.exception.code, "action_disabled")
                    self.assertEqual(adapter.state, state)
                    self.assertEqual(adapter.writes, 0)
                    self.assertEqual(adapter.audits, [])

    def test_complete_requires_current_revision(self):
        adapter = FakeAdapter(state=new_state())
        with self.assertRaises(GoalError) as caught:
            GoalService(adapter).handle({"action": "complete", "expected_revision": "old", "evidence": "proof"})
        self.assertEqual(caught.exception.code, "revision_stale")
        self.assertEqual(adapter.writes, 0)
        self.assertEqual(adapter.audits, [])

    def test_create_never_replaces_any_record(self):
        for status in ("active", "paused", "done", "cleared", "unknown"):
            with self.subTest(status=status):
                state = new_state(status=status)
                adapter = FakeAdapter(state=state)
                with self.assertRaises(GoalError) as caught:
                    GoalService(adapter).handle({"action": "create", "contract": contract(outcome="other")})
                self.assertEqual(caught.exception.code, "goal_exists")
                self.assertEqual(adapter.state, state)
                self.assertEqual(adapter.writes, 0)
                self.assertEqual(adapter.audits, [])
