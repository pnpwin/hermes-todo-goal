"""Contract validation tests: pure stdlib, no Hermes imports.

Run: python3.15t -B -m unittest discover -s tests -p 'test_contract.py' -v
"""
import copy
import unittest

from src.contract import ContractError, prepare_goal


def sample_todos():
    return {
        "revision": 7,
        "todos": [
            {"id": "inspect", "content": "Inspect input", "status": "completed"},
            {"id": "build", "content": "Build artifact", "status": "in_progress"},
            {"id": "verify", "content": "Verify artifact", "status": "pending"},
        ],
    }


def sample_contract():
    return {
        "outcome": "Produce a working artifact",
        "verification": "Run artifact tests and inspect the output file",
        "constraints": "Preserve the existing public API",
        "boundaries": "Only task-owned source and tests",
        "stop_when": "Ask if deployment or credentials are needed",
    }


class ContractTest(unittest.TestCase):
    def test_goal_without_todo_is_valid(self):
        contract = sample_contract()
        result = prepare_goal(None, contract)
        self.assertEqual(result, {
            "contract": contract,
            "todo_revision": None,
            "todo_ids": [],
        })
        self.assertIsNot(result["contract"], contract)

    def test_rejects_todo_revision_without_snapshot(self):
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(None, sample_contract(), expected_revision=4)
        self.assertEqual(ctx.exception.code, "todo_revision_without_snapshot")

    def test_explicit_binding_requires_matching_revision(self):
        result = prepare_goal(sample_todos(), sample_contract(), expected_revision=7)
        self.assertEqual(result["todo_revision"], 7)
        self.assertEqual(result["todo_ids"], ["inspect", "build", "verify"])

    def test_rejects_stale_revision(self):
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(sample_todos(), sample_contract(), expected_revision=6)
        self.assertEqual(ctx.exception.code, "todo_revision_stale")

    def test_rejects_boolean_revision(self):
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(sample_todos(), sample_contract(), expected_revision=True)
        self.assertEqual(ctx.exception.code, "todo_revision_invalid")

    def test_rejects_missing_expected_revision(self):
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(sample_todos(), sample_contract())
        self.assertEqual(ctx.exception.code, "todo_revision_required")

    def test_requires_every_contract_field(self):
        for field in sample_contract():
            with self.subTest(field=field):
                contract = sample_contract()
                del contract[field]
                with self.assertRaises(ContractError) as ctx:
                    prepare_goal(None, contract)
                self.assertEqual(ctx.exception.code, "contract_field_missing")

    def test_rejects_blank_contract_fields(self):
        for field in sample_contract():
            with self.subTest(field=field):
                contract = sample_contract()
                contract[field] = " \n "
                with self.assertRaises(ContractError) as ctx:
                    prepare_goal(None, contract)
                self.assertEqual(ctx.exception.code, "contract_field_blank")

    def test_rejects_nontext_contract_fields(self):
        contract = sample_contract()
        contract["verification"] = ["looks done"]
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(None, contract)
        self.assertEqual(ctx.exception.code, "contract_field_type")

    def test_rejects_overlong_contract_fields(self):
        contract = sample_contract()
        contract["boundaries"] = "x" * 4001
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(None, contract)
        self.assertEqual(ctx.exception.code, "contract_field_too_long")

    def test_rejects_unknown_contract_fields(self):
        contract = sample_contract()
        contract["session_id"] = "another-session"
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(None, contract)
        self.assertEqual(ctx.exception.code, "contract_field_unknown")

    def test_rejects_non_mapping_contract(self):
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(None, ["outcome"])
        self.assertEqual(ctx.exception.code, "contract_invalid")

    def test_rejects_empty_plan(self):
        with self.assertRaises(ContractError) as ctx:
            prepare_goal({"revision": 0, "todos": []}, sample_contract(), expected_revision=0)
        self.assertEqual(ctx.exception.code, "todo_plan_empty")

    def test_rejects_plan_with_no_active_items(self):
        todos = sample_todos()
        for item in todos["todos"]:
            item["status"] = "completed"
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(todos, sample_contract(), expected_revision=7)
        self.assertEqual(ctx.exception.code, "todo_plan_inactive")

    def test_rejects_duplicate_todo_identifiers(self):
        todos = sample_todos()
        todos["todos"][2]["id"] = "build"
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(todos, sample_contract(), expected_revision=7)
        self.assertEqual(ctx.exception.code, "todo_ids_duplicate")

    def test_rejects_negative_snapshot_revision(self):
        todos = sample_todos()
        todos["revision"] = -1
        with self.assertRaises(ContractError) as ctx:
            prepare_goal(todos, sample_contract(), expected_revision=-1)
        self.assertEqual(ctx.exception.code, "todo_revision_invalid")

    def test_preserves_inputs_on_success(self):
        todos, contract = sample_todos(), sample_contract()
        before = copy.deepcopy((todos, contract))
        prepare_goal(todos, contract, expected_revision=7)
        self.assertEqual((todos, contract), before)

    def test_preserves_inputs_on_rejection(self):
        todos, contract = sample_todos(), sample_contract()
        before = copy.deepcopy((todos, contract))
        with self.assertRaises(ContractError):
            prepare_goal(todos, contract, expected_revision=8)
        self.assertEqual((todos, contract), before)

    def test_returned_contract_does_not_alias_input(self):
        contract = sample_contract()
        result = prepare_goal(None, contract)
        result["contract"]["outcome"] = "mutated"
        self.assertEqual(contract["outcome"], "Produce a working artifact")

    def test_retains_completed_and_cancelled_ids_for_audit(self):
        todos = sample_todos()
        todos["todos"][0]["status"] = "cancelled"
        result = prepare_goal(todos, sample_contract(), expected_revision=7)
        self.assertEqual(result["todo_ids"], ["inspect", "build", "verify"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
