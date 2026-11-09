"""register() against a stand-in Hermes: the slash command, the tools, the flash gate and the CLI.

The end-to-end tests need a compiler and Node.js (CI installs the real arduino-cli; developers can point
BENCHFORGE_DEV_AVR_GCC / BENCHFORGE_DEV_CORE at a bare avr-gcc). Without them they are skipped.
"""

import importlib.util
import json
import re
import sys
import tempfile
import types
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="benchforge-plugin-test-"))


def _install_fake_hermes():
    def plugin_data_dir(name):
        path = HOME / "plugin-data" / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    storage = types.ModuleType("plugins.plugin_storage")
    storage.plugin_data_dir = plugin_data_dir
    pkg = types.ModuleType("plugins")
    pkg.plugin_storage = storage
    sys.modules.setdefault("plugins", pkg)
    sys.modules["plugins.plugin_storage"] = storage


def load_plugin():
    name = "hermes_plugins.benchforge"
    if name in sys.modules:
        return sys.modules[name]
    sys.modules.setdefault("hermes_plugins", types.ModuleType("hermes_plugins"))
    sys.modules["hermes_plugins"].__path__ = []
    spec = importlib.util.spec_from_file_location(name, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class Handle:
    def __init__(self, log):
        self.log, self.ok = log, True

    def edit(self, text):
        self.log[-1] = text
        return {"ok": True}


class FakeResult:
    def __init__(self, parsed):
        self.parsed, self.text = parsed, json.dumps(parsed)


class FakeLlm:
    """Plays the three model jobs: fixes go to the prepared fixed sketch."""

    def __init__(self):
        self.calls = []
        self.fixed = (ROOT / "examples" / "greenhouse-fan" / "fixed.ino").read_text()
        self.bench = json.loads((ROOT / "examples" / "greenhouse-fan" / "bench.json").read_text())

    def complete_structured(self, **kw):
        self.calls.append(kw["purpose"])
        if kw["purpose"] == "benchforge.bench":
            return FakeResult({"bench": self.bench})
        if kw["purpose"] == "benchforge.sketch":
            return FakeResult({"sketch": (ROOT / "examples" / "greenhouse-fan" / "draft.ino").read_text()})
        assert "Failed check" in kw["input"][0]["text"]
        return FakeResult({"sketch": self.fixed, "change": "Timing by subtraction and a fail-safe fan."})


class FakeCtx:
    def __init__(self, answer="approve"):
        self.tools, self.commands, self.hooks, self.cli = {}, {}, {}, {}
        self.answer, self.posts, self.questions = answer, [], []
        self.llm = FakeLlm()

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert schema["name"] == name
        self.tools[name] = handler

    def register_command(self, name, handler, **kw):
        self.commands[name] = handler

    def register_hook(self, name, fn):
        self.hooks[name] = fn

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)

    def post_message(self, text):
        self.posts.append(text)
        return Handle(self.posts)

    def ask_choice(self, question, choices):
        self.questions.append(question)
        return self.answer


_install_fake_hermes()
plugin = load_plugin()


def ready() -> bool:
    return plugin._workspace().ready()[0]


class Registration(unittest.TestCase):
    def test_registers_what_plugin_yaml_declares(self):
        ctx = FakeCtx()
        plugin.register(ctx)
        manifest = (ROOT / "plugin.yaml").read_text()
        declared_tools = re.findall(r"^  - (\w+)$", manifest.split("provides_tools:")[1].split("provides_hooks:")[0], re.M)
        declared_hooks = re.findall(r"^  - (\w+)$", manifest.split("provides_hooks:")[1], re.M)
        self.assertEqual(sorted(ctx.tools), sorted(declared_tools))
        self.assertEqual(sorted(ctx.hooks), sorted(declared_hooks))
        self.assertEqual(list(ctx.commands), ["bench"])
        self.assertEqual(list(ctx.cli), ["bench"])

    def test_help_and_guide(self):
        ctx = FakeCtx()
        plugin.register(ctx)
        self.assertIn("/bench demo", ctx.commands["bench"](""))
        guide = json.loads(ctx.tools["bench_guide"]({}))
        self.assertIn("BENCH FORMAT", guide["format"])
        self.assertEqual(guide["example"]["name"], "greenhouse-fan")

    def test_gate_ignores_everything_but_flashing(self):
        ctx = FakeCtx()
        plugin.register(ctx)
        hook = ctx.hooks["pre_tool_call"]
        self.assertIsNone(hook(tool_name="read_file", args={"path": "/x"}, task_id=""))
        self.assertIsNone(hook(tool_name="terminal", args={"command": "ls -la"}, task_id=""))
        blocked = hook(tool_name="terminal", args={"command": "arduino-cli compile -u -p COM3 ."}, task_id="")
        self.assertEqual(blocked["action"], "block")


@unittest.skipUnless(ready(), "needs a compiler and Node.js (see the module docstring)")
class EndToEnd(unittest.TestCase):
    def test_bench_request_approve_build_fix_and_flash(self):
        ctx = FakeCtx()
        plugin.register(ctx)
        reply = ctx.commands["bench"]("greenhouse fan on A0/D7 with a heartbeat LED", source=None)
        self.assertIn("✅ All green", reply, reply)
        self.assertEqual(ctx.llm.calls[:2], ["benchforge.bench", "benchforge.sketch"])
        self.assertIn("benchforge.fix", ctx.llm.calls)
        self.assertTrue(any("Approve these 8 checks" in q for q in ctx.questions))
        self.assertTrue(any("Fix 1: ✅" in p for p in ctx.posts), ctx.posts)
        gif = Path(re.search(r"MEDIA:(\S+\.gif)", reply).group(1))
        self.assertEqual(gif.read_bytes()[:6], b"GIF89a")
        self.assertGreater(gif.stat().st_size, 10_000)
        bundle = Path(re.search(r"MEDIA:(\S+\.zip)", reply).group(1))
        names = zipfile.ZipFile(bundle).namelist()
        self.assertIn("greenhouse-fan.ino", names)
        self.assertTrue(any(n.endswith(".receipt.json") for n in names))
        hex_path = re.search(r"--input-file (\S+\.hex)", reply).group(1)
        hook = ctx.hooks["pre_tool_call"]
        ok = hook(tool_name="terminal", task_id="",
                  args={"command": f"arduino-cli upload -p /dev/ttyACM0 --fqbn arduino:avr:uno --input-file {hex_path}"})
        self.assertIsNone(ok)

    def test_cancel_builds_nothing(self):
        ctx = FakeCtx(answer="cancel")
        plugin.register(ctx)
        self.assertIn("Cancelled", ctx.commands["bench"]("a fan", source=None))
        self.assertEqual(ctx.llm.calls, ["benchforge.bench"])

    def test_no_buttons_saves_a_draft_to_approve_later(self):
        ctx = FakeCtx(answer=None)
        plugin.register(ctx)
        reply = ctx.commands["bench"]("a fan", source=None)
        did = re.search(r"/bench approve ([0-9a-f]{6})", reply).group(1)
        reply = ctx.commands["bench"](f"approve {did}", source=None)
        self.assertIn("✅ All green", reply, reply)

    def test_agent_tool_reports_the_failure_then_the_pass(self):
        ctx = FakeCtx()
        plugin.register(ctx)
        bench = json.loads((ROOT / "examples" / "button-toggle" / "bench.json").read_text())
        draft = (ROOT / "examples" / "button-toggle" / "draft.ino").read_text()
        res = json.loads(ctx.tools["bench_test"]({"bench": bench, "sketch": draft}))
        self.assertFalse(res["passed"])
        self.assertIn("Bouncy button", res["fix"])
        fixed = (ROOT / "examples" / "button-toggle" / "fixed.ino").read_text()
        res = json.loads(ctx.tools["bench_test"]({"bench": bench, "sketch": fixed}))
        self.assertTrue(res["passed"])
        self.assertIn("/bench approve", res["approval_needed"])

    def test_agent_tool_explains_a_bad_bench(self):
        ctx = FakeCtx()
        plugin.register(ctx)
        res = json.loads(ctx.tools["bench_test"]({"bench": {"name": "x"}, "sketch": "void setup(){}void loop(){}"}))
        self.assertIn("bench is not valid", res["error"])


if __name__ == "__main__":
    unittest.main()
