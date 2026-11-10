"""GoalService policy tests against an in-memory host adapter (no Hermes imports).

Run: python3.15t -B -m unittest discover -s tests -p 'test_service.py' -v
"""
import copy
import unittest

from src.service import GoalError, GoalService, state_revision

CONTRACT = {
    "outcome": "Produce a working artifact",
    "verification": "Run artifact tests",
    "constraints": "Preserve the public API",
    "boundaries": "Task-owned files only",
    "stop_when": "Ask before deploying",
}


def contract(**overrides):
    data = dict(CONTRACT)
    data.update(overrides)
    return data


def new_state(**overrides):
    state = {
        "goal": "Produce a working artifact",
        "status": "active",
        "turns_used": 3,
        "max_turns": 20,
        "created_at": 1000.0,
        "last_turn_at": 1001.0,
        "last_verdict": "continue",
        "last_reason": "",
        "paused_reason": None,
        "consecutive_parse_failures": 0,
        "consecutive_transport_failures": 0,
        "subgoals": ["keep docs"],
        "gates": [{"command": "true", "timeout_seconds": 30, "max_retries": 1}],
        "waiting_on_pid": None,
        "waiting_on_session": None,
        "waiting_until": 0.0,
        "waiting_on_delegations": 0,
        "waiting_reason": None,
        "waiting_since": 0.0,
        "contract": dict(CONTRACT),
    }
    state.update(overrides)
    return state


class FakeAdapter:
    """Mirrors the native adapter's contract: persist, then re-read."""

    def __init__(self, state=None, todos=None, drop_on_write=None):
        self.state = copy.deepcopy(state)
        self.todos = todos
        self.drop_on_write = set(drop_on_write or ())
        self.audits = []
        self.writes = 0

    # reads
    def session_key(self):
        return "session-1"

    def load(self):
        return copy.deepcopy(self.state)

    def todo_snapshot(self):
        return copy.deepcopy(self.todos)

    # writes
    def _store(self, state):
        self.writes += 1
        for key in self.drop_on_write:
            state.pop(key, None)
        self.state = state
        return copy.deepcopy(state)

    def create(self, outcome, contract):
        return self._store(new_state(goal=outcome, contract=dict(contract),
                                     turns_used=0, last_verdict=None, subgoals=[],
                                     gates=[], created_at=2000.0, last_turn_at=0.0))

    def replace_contract(self, snapshot, outcome, contract):
        state = copy.deepcopy(snapshot)
        state["goal"] = outcome
        state["contract"] = dict(contract)
        state["last_verdict"] = None
        return self._store(state)

    def pause(self, reason):
        state = copy.deepcopy(self.state)
        state["status"] = "paused"
        state["paused_reason"] = reason
        return self._store(state)

    def resume(self):
        state = copy.deepcopy(self.state)
        state["status"] = "active"
        state["paused_reason"] = None
        return self._store(state)

    def audit(self, event):
        self.audits.append(event)
        return True


class ServiceTest(unittest.TestCase):
    def service(self, **kwargs):
        return GoalService(FakeAdapter(**kwargs))

    def test_audit_failure_reports_confirmed_native_effect(self):
        adapter = FakeAdapter()
        adapter.audit = lambda event: False
        result = GoalService(adapter).handle({"action": "create", "contract": contract()})
        self.assertFalse(result["ok"])
        self.assertEqual(result["outcome"], "partial")
        self.assertTrue(result["changed"])
        self.assertEqual(result["native_status"], "confirmed")
        self.assertEqual(result["audit_status"], "failed")
        self.assertEqual(result["state"]["status"], "active")
        self.assertEqual(result["revision"], state_revision(adapter.state))
        self.assertEqual(adapter.writes, 1)

    def test_audit_exception_is_partial_for_each_action(self):
        def broken(event):
            raise OSError("private-canary")
        for action in ("create", "pause", "resume", "complete"):
            with self.subTest(action=action):
                state = None if action == "create" else new_state()
                if action == "resume":
                    state = new_state(status="paused", paused_reason="agent:wait")
                adapter = FakeAdapter(state=state)
                adapter.audit = broken
                args = {"action": action}
                if action == "create":
                    args["contract"] = contract()
                else:
                    args["expected_revision"] = state_revision(state)
                    args["evidence" if action == "complete" else "reason"] = "proof"
                result = GoalService(adapter).handle(args)
                self.assertFalse(result["ok"])
                self.assertEqual(result["outcome"], "partial")
                self.assertEqual(result["changed"], action != "complete")
                self.assertEqual(adapter.writes, int(action != "complete"))
                self.assertNotIn("private-canary", str(result))

    def test_audit_failure_with_failed_read_is_unknown(self):
        adapter = FakeAdapter()
        def broken(event):
            adapter.load = lambda: None
            return False
        adapter.audit = broken
        result = GoalService(adapter).handle({"action": "create", "contract": contract()})
        self.assertEqual(result["outcome"], "unknown")
        self.assertIsNone(result["state"])
        self.assertIsNone(result["revision"])
        self.assertIsNone(result["changed"])
        self.assertIsNone(result["todo_binding"])

    def test_status_without_goal_is_read_only(self):
        adapter = FakeAdapter()
        result = GoalService(adapter).handle({"action": "status"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["revision"], "none")
        self.assertIsNone(result["state"])
        self.assertEqual(adapter.writes, 0)
        self.assertEqual(adapter.audits, [])

    def test_status_reports_revision_and_projection(self):
        adapter = FakeAdapter(state=new_state())
        result = GoalService(adapter).handle({"action": "status"})
        self.assertEqual(result["revision"], state_revision(adapter.state))
        self.assertEqual(result["state"]["status"], "active")
        self.assertEqual(result["state"]["turns_used"], 3)
        self.assertEqual(adapter.writes, 0)

    def test_revision_changes_with_turn_count(self):
        self.assertNotEqual(state_revision(new_state()), state_revision(new_state(turns_used=4)))

    def test_revision_is_stable_for_equal_states(self):
        self.assertEqual(state_revision(new_state()), state_revision(copy.deepcopy(new_state())))

    def test_create_without_todo(self):
        adapter = FakeAdapter()
        result = GoalService(adapter).handle({"action": "create", "contract": contract()})
        self.assertTrue(result["ok"])
        self.assertTrue(result["changed"])
        self.assertIsNone(result["todo_binding"])
        self.assertEqual(adapter.state["status"], "active")
        self.assertEqual(adapter.state["contract"], contract())
        self.assertEqual(len(adapter.audits), 1)
        self.assertEqual(adapter.audits[0]["event"], "create")

    def test_create_rejects_existing_goal(self):
        adapter = FakeAdapter(state=new_state())
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({"action": "create", "contract": contract(outcome="Other")})
        self.assertEqual(ctx.exception.code, "goal_exists")
        self.assertEqual(adapter.writes, 0)

    def test_duplicate_create_is_unchanged(self):
        adapter = FakeAdapter(state=new_state(status="active"))
        result = GoalService(adapter).handle({"action": "create", "contract": contract()})
        self.assertFalse(result["changed"])
        self.assertEqual(adapter.writes, 0)
        self.assertEqual(adapter.state["turns_used"], 3)

    def test_create_allowed_after_cleared(self):
        adapter = FakeAdapter(state=new_state(status="cleared"))
        result = GoalService(adapter).handle({"action": "create", "contract": contract()})
        self.assertTrue(result["changed"])
        self.assertEqual(adapter.state["status"], "active")

    def test_create_with_todo_binding(self):
        todos = {"revision": 5, "todos": [{"id": "a", "content": "x", "status": "pending"}]}
        adapter = FakeAdapter(todos=todos)
        result = GoalService(adapter).handle({
            "action": "create", "contract": contract(), "expected_todo_revision": 5,
        })
        self.assertEqual(result["todo_binding"], {"revision": 5, "ids": ["a"]})
        self.assertEqual(adapter.audits[0]["todo_revision"], 5)

    def test_create_with_stale_todo_binding_is_rejected(self):
        todos = {"revision": 6, "todos": [{"id": "a", "content": "x", "status": "pending"}]}
        adapter = FakeAdapter(todos=todos)
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({
                "action": "create", "contract": contract(), "expected_todo_revision": 5,
            })
        self.assertEqual(ctx.exception.code, "todo_revision_stale")
        self.assertEqual(adapter.writes, 0)

    def test_create_with_binding_but_no_snapshot_is_rejected(self):
        adapter = FakeAdapter(todos=None)
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({
                "action": "create", "contract": contract(), "expected_todo_revision": 0,
            })
        self.assertEqual(ctx.exception.code, "todo_unavailable")

    def test_contract_change_requires_user_control(self):
        state = new_state()
        adapter = FakeAdapter(state=state)
        with self.assertRaises(GoalError) as caught:
            GoalService(adapter).handle({"action": "update",
                "expected_revision": state_revision(state),
                "contract": contract(verification="Explain why tests were skipped")})
        self.assertEqual(caught.exception.code, "contract_change_requires_user")
        self.assertEqual(adapter.state, state)
        self.assertEqual(adapter.writes, 0)
        self.assertEqual(adapter.audits, [])

    def test_update_preserves_budget_subgoals_gates_and_wait(self):
        state = new_state(waiting_on_pid=4242, waiting_reason="waiting")
        adapter = FakeAdapter(state=state)
        with self.assertRaises(GoalError) as caught:
            GoalService(adapter).handle({
                "action": "update", "expected_revision": state_revision(state),
                "contract": contract(verification="New proof"),
            })
        self.assertEqual(caught.exception.code, "contract_change_requires_user")
        self.assertEqual(adapter.writes, 0)
        new = adapter.state
        for key in ("turns_used", "max_turns", "created_at", "subgoals", "gates",
                    "waiting_on_pid", "waiting_reason", "status"):
            self.assertEqual(new[key], state[key], key)
        self.assertEqual(new, state)
        self.assertEqual(adapter.audits, [])

    def test_update_requires_matching_revision(self):
        adapter = FakeAdapter(state=new_state())
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({
                "action": "update", "expected_revision": "stale", "contract": contract(),
            })
        self.assertEqual(ctx.exception.code, "revision_stale")
        self.assertEqual(adapter.writes, 0)

    def test_update_without_goal_is_rejected(self):
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter()).handle({
                "action": "update", "expected_revision": "none", "contract": contract(),
            })
        self.assertEqual(ctx.exception.code, "no_goal")

    def test_update_noop_does_not_write(self):
        state = new_state()
        adapter = FakeAdapter(state=state)
        result = GoalService(adapter).handle({
            "action": "update", "expected_revision": state_revision(state), "contract": contract(),
        })
        self.assertFalse(result["changed"])
        self.assertEqual(adapter.writes, 0)

    def test_pause_detects_lost_native_fields(self):
        state = new_state()
        adapter = FakeAdapter(state=state, drop_on_write={"gates"})
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({
                "action": "pause", "expected_revision": state_revision(state),
                "reason": "waiting",
            })
        self.assertEqual(ctx.exception.code, "persistence_unverified")

    def test_pause_marks_agent_reason_and_keeps_budget(self):
        state = new_state()
        adapter = FakeAdapter(state=state)
        GoalService(adapter).handle({
            "action": "pause", "expected_revision": state_revision(state), "reason": "waiting on user",
        })
        self.assertEqual(adapter.state["status"], "paused")
        self.assertEqual(adapter.state["paused_reason"], "agent:waiting on user")
        self.assertEqual(adapter.state["turns_used"], 3)

    def test_pause_requires_reason(self):
        state = new_state()
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter(state=state)).handle({
                "action": "pause", "expected_revision": state_revision(state), "reason": "  ",
            })
        self.assertEqual(ctx.exception.code, "arg_missing")

    def test_pause_refuses_unfinished_goal_states(self):
        for status in ("done", "cleared"):
            with self.subTest(status=status):
                state = new_state(status=status)
                with self.assertRaises(GoalError) as ctx:
                    GoalService(FakeAdapter(state=state)).handle({
                        "action": "pause", "expected_revision": state_revision(state),
                        "reason": "x",
                    })
                self.assertEqual(ctx.exception.code, "goal_not_active")

    def test_pause_keeps_user_pause_reason(self):
        state = new_state(status="paused", paused_reason="user-paused")
        adapter = FakeAdapter(state=state)
        result = GoalService(adapter).handle({
            "action": "pause", "expected_revision": state_revision(state), "reason": "again",
        })
        self.assertFalse(result["changed"])
        self.assertEqual(adapter.state["paused_reason"], "user-paused")
        self.assertEqual(adapter.writes, 0)

    def test_resume_refuses_user_pause(self):
        state = new_state(status="paused", paused_reason="user-paused")
        adapter = FakeAdapter(state=state)
        with self.assertRaises(GoalError) as caught:
            GoalService(adapter).handle({
                "action": "resume", "expected_revision": state_revision(state),
                "reason": "the user agreed",
            })
        self.assertEqual(caught.exception.code, "pause_not_resumable")
        self.assertEqual(adapter.state, state)
        self.assertEqual(adapter.writes, 0)
        self.assertEqual(adapter.audits, [])

    def test_resume_refuses_unowned_pause(self):
        for reason in (None, "", "agent:", "agent:  ", "unknown", "gate-failed"):
            with self.subTest(reason=reason):
                state = new_state(status="paused", paused_reason=reason)
                adapter = FakeAdapter(state=state)
                with self.assertRaises(GoalError) as caught:
                    GoalService(adapter).handle({"action": "resume",
                        "expected_revision": state_revision(state), "reason": "go"})
                self.assertEqual(caught.exception.code, "pause_not_resumable")
                self.assertEqual(adapter.state, state)
                self.assertEqual(adapter.writes, 0)

    def test_resume_refuses_exhausted_agent_budget(self):
        state = new_state(status="paused", paused_reason="agent:waiting", turns_used=20)
        adapter = FakeAdapter(state=state)
        with self.assertRaises(GoalError) as caught:
            GoalService(adapter).handle({"action": "resume",
                "expected_revision": state_revision(state), "reason": "go"})
        self.assertEqual(caught.exception.code, "pause_not_resumable")
        self.assertEqual(adapter.state, state)
        self.assertEqual(adapter.writes, 0)

    def test_resume_preserves_turns_used(self):
        state = new_state(status="paused", paused_reason="agent:waiting")
        adapter = FakeAdapter(state=state)
        result = GoalService(adapter).handle({
            "action": "resume", "expected_revision": state_revision(state), "reason": "user agreed",
        })
        self.assertTrue(result["changed"])
        self.assertEqual(adapter.state["status"], "active")
        self.assertEqual(adapter.state["turns_used"], 3)
        self.assertIsNone(adapter.state["paused_reason"])

    def test_resume_refuses_budget_stop(self):
        state = new_state(status="paused", paused_reason="budget-exhausted")
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter(state=state)).handle({
                "action": "resume", "expected_revision": state_revision(state), "reason": "go on",
            })
        self.assertEqual(ctx.exception.code, "pause_not_resumable")

    def test_resume_refuses_wait_barrier(self):
        state = new_state(status="paused", paused_reason="agent:waiting-on-child",
                          waiting_on_delegations=1)
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter(state=state)).handle({
                "action": "resume", "expected_revision": state_revision(state), "reason": "go on",
            })
        self.assertEqual(ctx.exception.code, "wait_barrier")

    def test_resume_requires_paused_goal(self):
        state = new_state()
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter(state=state)).handle({
                "action": "resume", "expected_revision": state_revision(state), "reason": "go",
            })
        self.assertEqual(ctx.exception.code, "goal_not_paused")

    def test_complete_requests_verification_without_marking_done(self):
        state = new_state()
        adapter = FakeAdapter(state=state)
        result = GoalService(adapter).handle({
            "action": "complete", "expected_revision": state_revision(state),
            "evidence": "tests: 12 passed; artifact /tmp/out.csv",
        })
        self.assertEqual(result["verification_status"], "pending")
        self.assertFalse(result["changed"])
        self.assertEqual(adapter.state["status"], "active")
        self.assertEqual(adapter.writes, 0)
        self.assertIn("12 passed", result["evidence"])
        self.assertTrue(result["notice"])

    def test_complete_rejects_paused_goal(self):
        state = new_state(status="paused", paused_reason="user-paused")
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter(state=state)).handle({
                "action": "complete", "expected_revision": state_revision(state), "evidence": "done",
            })
        self.assertEqual(ctx.exception.code, "goal_not_active")

    def test_complete_persists_evidence(self):
        state = new_state()
        adapter = FakeAdapter(state=state)
        evidence = "tests: verified; report.csv"
        GoalService(adapter).handle({"action": "complete",
            "expected_revision": state_revision(state), "evidence": evidence})
        self.assertEqual(adapter.audits[-1].get("evidence"), evidence)
        self.assertEqual(adapter.state, state)
        self.assertEqual(adapter.writes, 0)

    def test_complete_requires_evidence(self):
        state = new_state()
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter(state=state)).handle({
                "action": "complete", "expected_revision": state_revision(state), "evidence": "",
            })
        self.assertEqual(ctx.exception.code, "arg_missing")

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter()).handle({"action": "mark_done"})
        self.assertEqual(ctx.exception.code, "unknown_action")

    def test_session_selector_argument_is_rejected_before_side_effects(self):
        adapter = FakeAdapter()
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({
                "action": "create", "contract": contract(), "session_id": "other",
            })
        self.assertEqual(ctx.exception.code, "arg_unknown")
        self.assertEqual(adapter.writes, 0)

    def test_model_approval_argument_is_rejected(self):
        state = new_state()
        adapter = FakeAdapter(state=state)
        with self.assertRaises(GoalError) as ctx:
            GoalService(adapter).handle({
                "action": "update", "expected_revision": state_revision(state),
                "contract": contract(), "approved": True,
            })
        self.assertEqual(ctx.exception.code, "arg_unknown")
        self.assertEqual(adapter.writes, 0)

    def test_non_dict_arguments_are_rejected(self):
        with self.assertRaises(GoalError) as ctx:
            GoalService(FakeAdapter()).handle("status")
        self.assertEqual(ctx.exception.code, "args_invalid")


if __name__ == "__main__":
    unittest.main(verbosity=2)
