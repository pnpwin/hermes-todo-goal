"""Offline native request-boundary probe; no LLM responses or network calls.

Like test_desktop.py, requires the installed Hermes runtime on PYTHONPATH.
Each arm gets a fresh subprocess/home; only config/discovery are fixture-bound.
The real prompt builder and agent turn run until the Chat Completions call,
which is intercepted after SDK serialization and before network access. This is delivery, not model behaviour.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROBE = r'''
import json, os, socket, sys
from unittest import mock
import httpx
from hermes_cli import plugins
from hermes_cli.plugins_context import PluginContext
from run_agent import AIAgent
from src import plugin

def no_network(*args, **kwargs):
    raise AssertionError("offline probe attempted network access")

def capture(self, request, *args, **kwargs):
    assert request.url.host == "127.0.0.1" and request.url.path.endswith("/chat/completions")
    payload = json.loads(request.read())
    messages = payload["messages"]
    system = "\n".join(m.get("content", "") for m in messages
                       if m.get("role") in ("system", "developer"))
    print("PROBE_RESULT=" + json.dumps({"enabled": sys.argv[1] == "on",
        "section_present": plugin.SECTION_TEXT in system,
        "system_chars": len(system), "requests_intercepted": 1,
        "request_model": payload.get("model"),
        "roles": [m.get("role") for m in messages],
        "inference_calls": 0}), flush=True)
    # Native transport runs in a worker thread. Exit this disposable subprocess
    # at the first request, not via a swallowed exception that triggers retries.
    os._exit(0)

manager = plugins.PluginManager()
manager._discovered = True
ctx = PluginContext(plugins.PluginManifest(name="hermes-todo-goal"), manager)
with mock.patch.object(ctx, "get_config", return_value=(sys.argv[1] == "on")):
    plugin.register(ctx)
with mock.patch.object(plugins, "_plugin_manager", manager), \
     mock.patch.object(plugins, "_ensure_plugins_discovered", return_value=manager), \
     mock.patch.object(httpx.Client, "send", capture), \
     mock.patch.object(socket.socket, "connect", no_network), \
     mock.patch.object(socket, "create_connection", no_network):
    agent = AIAgent(model="test/offline", provider="openrouter", api_key="offline-placeholder",
                    base_url="http://127.0.0.1:1/v1", skip_memory=True,
                    skip_context_files=True, enabled_toolsets=[], quiet_mode=True)
    agent.run_conversation("Write and verify a small artifact for this task.")
raise AssertionError("native run returned without reaching request boundary")
'''


class NativePromptDeliveryTest(unittest.TestCase):
    def test_opt_in_section_reaches_request_not_only_renderer(self):
        for arm in ("off", "on"):
            with self.subTest(arm=arm), tempfile.TemporaryDirectory(prefix="goal-prompt-") as home:
                env = dict(os.environ, HOME=home, HERMES_HOME=str(Path(home) / ".hermes"),
                           PYTHONPATH=str(Path(__file__).resolve().parents[1]) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                           HERMES_DISABLE_LAZY_INSTALLS="1", UV_OFFLINE="1")
                result = subprocess.run([sys.executable, "-B", "-c", PROBE, arm],
                                        cwd=Path(__file__).resolve().parents[1], env=env,
                                        capture_output=True, text=True, timeout=90)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                lines = [line for line in result.stdout.splitlines() if line.startswith("PROBE_RESULT=")]
                self.assertEqual(len(lines), 1, result.stdout + result.stderr)
                data = json.loads(lines[0].split("=", 1)[1])
                self.assertEqual(data["section_present"], arm == "on", data)
                self.assertEqual(data["requests_intercepted"], 1, data)
                self.assertEqual(data["inference_calls"], 0, data)
                print("prompt delivery", json.dumps(data, sort_keys=True))
