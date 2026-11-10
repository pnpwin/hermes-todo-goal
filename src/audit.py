"""Plugin-owned audit only; never authoritative goal state."""
import copy
import json
import math
import os
import tempfile
from pathlib import Path


class AuditError(Exception):
    pass


def append_event(path, event, *, keep=50):
    path = Path(path)
    after = event.get("after")
    generation = after.get("created_at") if isinstance(after, dict) else None
    if (type(generation) not in (int, float) or not math.isfinite(generation)
            or generation <= 0):
        raise AuditError("audit_generation_invalid")
    if type(keep) is not int or keep < 1:
        raise AuditError("audit_retention_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = Path(str(path) + ".lock")
    try:
        lock.mkdir()
    except OSError as exc:
        raise AuditError("audit_locked_or_unavailable") from exc
    try:
        _append_locked(path, event, generation, keep)
    finally:
        lock.rmdir()


def _append_locked(path, event, generation, keep):
    old = read_payload(path)
    if event.get("event") != "create" and old and old.get("generation") != generation:
        raise AuditError("audit_generation_mismatch")
    if event.get("event") == "create":
        binding = None
        if event.get("todo_revision") is not None:
            binding = {"revision": event["todo_revision"], "ids": event.get("todo_ids")}
            if not valid_binding(binding):
                raise AuditError("audit_binding_invalid")
    else:
        binding = binding_for(old, generation)
    payload = {"schema_version": 1, "generation": generation, "binding": binding,
               "events": ((old or {}).get("events", []) + [copy.deepcopy(event)])[-keep:]}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temp_name = handle.name
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)


def read_payload(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise AuditError("audit_unreadable") from exc
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise AuditError("audit_invalid")
    generation = data.get("generation")
    if (type(generation) not in (int, float) or not math.isfinite(generation)
            or generation <= 0):
        raise AuditError("audit_generation_invalid")
    version = data.get("schema_version", 0)
    if type(version) is not int or version not in (0, 1):
        raise AuditError("audit_version_unsupported")
    return data


def valid_binding(value):
    return (isinstance(value, dict)
            and type(value.get("revision")) is int and value["revision"] >= 0
            and isinstance(value.get("ids"), list)
            and all(isinstance(item, str) and bool(item) for item in value["ids"]))


def binding_for(data, generation):
    if not data or data.get("generation") != generation:
        return None
    if data.get("schema_version") == 1:
        value = data.get("binding")
        if value is not None and not valid_binding(value):
            raise AuditError("audit_binding_invalid")
        return copy.deepcopy(value)
    for event in reversed(data["events"]):
        if not isinstance(event, dict):
            continue
        after = event.get("after")
        if (event.get("event") == "create" and isinstance(after, dict)
                and after.get("created_at") == generation):
            if event.get("todo_revision") is None:
                return None
            value = {"revision": event.get("todo_revision"), "ids": event.get("todo_ids")}
            if not valid_binding(value):
                raise AuditError("audit_binding_invalid")
            return copy.deepcopy(value)
    return None
