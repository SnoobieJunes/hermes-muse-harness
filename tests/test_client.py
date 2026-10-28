import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client import (
    MuseHarnessError,
    child_env,
    normalize_mcp_servers,
    pick_choice,
    serve_argv,
    uuid7,
)


class ClientTests(unittest.TestCase):
    def test_uuid7_shape(self):
        value = uuid7()
        parts = value.split("-")
        self.assertEqual(len(parts), 5)
        self.assertEqual(parts[2][0], "7")

    def test_child_env_strips_api_keys(self):
        env = child_env({
            "PATH": "/usr/bin",
            "META_API_KEY": "secret",
            "MODEL_API_KEY": "secret",
            "META_MODEL_API_KEY": "secret",
            "MUSE_CODE_SUB_TOKEN": "secret",
            "HOME": "/Users/auston",
        })
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertNotIn("META_API_KEY", env)
        self.assertNotIn("MODEL_API_KEY", env)
        self.assertNotIn("MUSE_CODE_SUB_TOKEN", env)

    def test_argv_has_no_yolo_and_forces_contributor_off(self):
        argv = serve_argv(binary="muse", model="muse-wonder-full-1.0", provider="meta", contributor_mode="off")
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("--disable-sandbox", argv)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--contributor-mode") + 1], "off")
        self.assertEqual(argv[argv.index("--model") + 1], "muse-wonder-full-1.0")

    def test_mcp_http_and_stdio(self):
        cleaned = normalize_mcp_servers({
            "unreal": {"transport": "streamableHttp", "url": "http://127.0.0.1:8000/mcp"},
            "local": {"transport": "stdio", "command": "server", "args": ["--stdio"]},
        })
        self.assertEqual(cleaned["unreal"]["url"], "http://127.0.0.1:8000/mcp")
        self.assertEqual(cleaned["local"]["args"], ["--stdio"])

    def test_mcp_rejects_shell_command(self):
        with self.assertRaises(MuseHarnessError):
            normalize_mcp_servers({"bad": {"transport": "stdio", "command": "sh -c id"}})

    def test_pick_choice_denies_unless_allow(self):
        choices = [
            {"choiceId": "a", "decision": "approved"},
            {"choiceId": "d", "decision": "denied"},
            {"choiceId": "s", "decision": "approvedForSession"},
        ]
        self.assertEqual(pick_choice(choices, allow=False)["choiceId"], "d")
        self.assertEqual(pick_choice(choices, allow=True)["choiceId"], "a")

    def test_plugin_registers_declared_tool(self):
        registered = []

        class Ctx:
            def register_tool(self, **kwargs):
                registered.append(kwargs["name"])

        import importlib.util
        spec = importlib.util.spec_from_file_location("muse_harness_plugin", ROOT / "__init__.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        plugin.register(Ctx())
        manifest = ROOT / "plugin.yaml"
        text = manifest.read_text()
        self.assertIn("muse_turn", text)
        self.assertEqual(registered, ["muse_turn"])
        self.assertIn("muse_turn", json.dumps(registered))


if __name__ == "__main__":
    unittest.main()
