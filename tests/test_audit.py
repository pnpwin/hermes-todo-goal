import json
import tempfile
import unittest
from pathlib import Path
from src.audit import AuditError, append_event, binding_for, read_payload


def event(kind="create", generation=1.0, **extra):
    return {"event": kind, "after": {"created_at": generation}, **extra}


def concurrent_writer(path, ident, barrier, results):
    try:
        barrier.wait(timeout=10)
        append_event(path, event("pause", writer=ident))
    except AuditError:
        results.put((ident, False))
    else:
        results.put((ident, True))


class AuditTest(unittest.TestCase):
    def test_two_processes_never_report_success_for_a_lost_event(self):
        import multiprocessing
        ctx = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            append_event(path, event())
            barrier = ctx.Barrier(2)
            results = ctx.Queue()
            processes = [ctx.Process(target=concurrent_writer,
                args=(str(path), ident, barrier, results)) for ident in ("a", "b")]
            try:
                for process in processes:
                    process.start()
                outcomes = dict(results.get(timeout=15) for _ in processes)
                for process in processes:
                    process.join(timeout=15)
                    self.assertEqual(process.exitcode, 0)
                saved = {item["writer"] for item in read_payload(path)["events"] if "writer" in item}
                successful = {ident for ident, success in outcomes.items() if success}
                self.assertTrue(successful)
                self.assertEqual(saved, successful)
                self.assertEqual(len(saved), len(read_payload(path)["events"])-1)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                        process.join(timeout=5)
                results.close()

    def test_binding_survives_retention(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "goals" / "session.json"
            append_event(path, event(todo_revision=7, todo_ids=["a"]))
            for number in range(51):
                append_event(path, event("complete_requested", evidence=f"proof-{number}"))
            data = read_payload(path)
            self.assertEqual(len(data["events"]), 50)
            self.assertEqual(binding_for(data, 1.0), {"revision": 7, "ids": ["a"]})
            self.assertEqual(data["events"][-1]["evidence"], "proof-50")

    def test_legacy_binding_and_generation(self):
        data = {"generation": 1.0, "events": [event(todo_revision=7, todo_ids=["a"])]}
        self.assertEqual(binding_for(data, 1.0), {"revision": 7, "ids": ["a"]})
        self.assertIsNone(binding_for(data, 2.0))
        with self.assertRaises(AuditError):
            binding_for({"generation": 1.0, "events": []}, 1.0)

    def test_new_generation_clears_binding(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            append_event(path, event(todo_revision=7, todo_ids=["a"]))
            append_event(path, event(generation=2.0))
            data = read_payload(path)
            self.assertEqual(data["generation"], 2.0)
            self.assertIsNone(binding_for(data, 2.0))
            with self.assertRaises(AuditError):
                append_event(path, event("pause", generation=1.0))

    def test_missing_read_does_not_create_directory(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "missing" / "session.json"
            self.assertIsNone(read_payload(path))
            self.assertFalse(path.parent.exists())

    def test_corrupt_and_unsupported_files_are_preserved(self):
        values = ["broken json", "[]", json.dumps({"schema_version": 2, "events": []})]
        for value in values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "session.json"
                path.write_text(value)
                with self.assertRaises(AuditError):
                    append_event(path, event())
                self.assertEqual(path.read_text(), value)

    def test_replace_failure_keeps_original_and_cleans_own_temp(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            append_event(path, event())
            before = path.read_bytes()
            with patch("src.audit.os.replace", side_effect=OSError("failure")):
                with self.assertRaises(OSError):
                    append_event(path, event("pause"))
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(Path(root).iterdir()), [path])

    def test_invalid_stored_generation_is_refused(self):
        for generation in (True, None, -1, float("nan"), float("inf")):
            with self.subTest(generation=generation), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "session.json"
                path.write_text(json.dumps({"generation": generation, "events": []}))
                with self.assertRaises(AuditError):
                    read_payload(path)

    def test_new_create_does_not_replace_structurally_corrupt_binding(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            path.write_text(json.dumps({"schema_version": 1, "generation": 1.0,
                "events": [], "binding": {"revision": True, "ids": ["a"]}}))
            before = path.read_bytes()
            with self.assertRaises(AuditError):
                append_event(path, event(generation=2.0))
            self.assertEqual(path.read_bytes(), before)

    def test_non_create_does_not_invent_absent_binding_without_audit(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            with self.assertRaises(AuditError):
                append_event(path, event("pause"))
            self.assertFalse(path.exists())

    def test_binding_result_is_independent(self):
        value = {"schema_version": 1, "generation": 1.0, "events": [],
                 "binding": {"revision": 7, "ids": ["a"]}}
        result = binding_for(value, 1.0)
        result["ids"].append("b")
        self.assertEqual(value["binding"]["ids"], ["a"])

    def test_existing_lock_refuses_without_removing_it(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "session.json"
            lock = Path(str(path) + ".lock")
            lock.mkdir()
            with self.assertRaises(AuditError):
                append_event(path, event())
            self.assertTrue(lock.is_dir())
            self.assertFalse(path.exists())
