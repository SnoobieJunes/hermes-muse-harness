import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from client import (
    MuseHarnessError,
    child_env,
    is_quota_failure,
    normalize_mcp_servers,
    pick_choice,
    serve_argv,
    uuid7,
    workspace_within,
)


class ClientTests(unittest.TestCase):
    def test_uuid7_shape(self):
        parts = uuid7().split("-")
        self.assertEqual(len(parts), 5)
        self.assertEqual(parts[2][0], "7")

    def test_subscription_env_drops_api_keys_but_fallback_restores_one(self):
        base = {
            "PATH": "/usr/bin",
            "META_API_KEY": "meta-secret",
            "MODEL_API_KEY": "model-secret",
            "MUSE_CODE_SUB_TOKEN": "minted",
            "HOME": "/Users/auston",
        }
        sub = child_env(base, billing="subscription")
        self.assertEqual(sub["PATH"], "/usr/bin")
        self.assertNotIn("META_API_KEY", sub)
        self.assertNotIn("MODEL_API_KEY", sub)
        self.assertNotIn("MUSE_CODE_SUB_TOKEN", sub)
        fallback = child_env(base, billing="api_key", api_key="meta-secret")
        self.assertEqual(fallback["META_API_KEY"], "meta-secret")
        self.assertNotIn("MUSE_CODE_SUB_TOKEN", fallback)
        self.assertNotIn("MODEL_API_KEY", fallback)

    def test_child_env_copies_allowlist_and_drops_foreign_secrets(self):
        base = {
            "PATH": "/usr/bin",
            "HOME": "/Users/auston",
            "USER": "auston",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "LC_CTYPE": "UTF-8",
            "TMPDIR": "/tmp",
            "XDG_CONFIG_HOME": "/Users/auston/.config",
            "TERM": "xterm",
            "SHELL": "/bin/zsh",
            "OPENAI_API_KEY": "sk-other",
            "TELEGRAM_BOT_TOKEN": "bot",
            "MUSE_CODE_SUB_TOKEN": "minted",
            "META_API_KEY": "meta-secret",
        }
        env = child_env(base, billing="subscription")
        self.assertEqual(env["PATH"], "/usr/bin")
        self.assertEqual(env["HOME"], "/Users/auston")
        self.assertEqual(env["USER"], "auston")
        self.assertEqual(env["LANG"], "en_US.UTF-8")
        self.assertEqual(env["LC_ALL"], "en_US.UTF-8")
        self.assertEqual(env["LC_CTYPE"], "UTF-8")
        self.assertEqual(env["TMPDIR"], "/tmp")
        self.assertEqual(env["XDG_CONFIG_HOME"], "/Users/auston/.config")
        self.assertNotIn("TERM", env)
        self.assertNotIn("SHELL", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("TELEGRAM_BOT_TOKEN", env)
        self.assertNotIn("MUSE_CODE_SUB_TOKEN", env)
        self.assertNotIn("META_API_KEY", env)

    def test_fallback_key_is_the_argument_not_the_parent_dict(self):
        base = {
            "PATH": "/usr/bin",
            "META_API_KEY": "from-parent",
            "MODEL_API_KEY": "also-parent",
            "OPENAI_API_KEY": "nope",
        }
        env = child_env(base, billing="api_key", api_key="scoped-secret")
        self.assertEqual(env["META_API_KEY"], "scoped-secret")
        self.assertNotIn("MODEL_API_KEY", env)
        self.assertNotIn("OPENAI_API_KEY", env)
        with self.assertRaises(MuseHarnessError):
            child_env(base, billing="api_key")

    def test_resolve_api_key_prefers_hermes_name(self):
        from client import resolve_api_key

        store = {
            "MODEL_API_KEY": "first",
            "META_API_KEY": "second",
            "META_MODEL_API_KEY": "third",
        }
        self.assertEqual(resolve_api_key(store.get), "first")
        self.assertEqual(resolve_api_key({"META_API_KEY": " only "}.get), "only")
        self.assertIsNone(resolve_api_key({}.get))

    def test_fallback_ignores_a_key_sitting_in_the_parent_dict(self):
        env = child_env({"PATH": "/usr/bin", "MODEL_API_KEY": "from-hermes"}, billing="api_key", api_key="scoped")
        self.assertEqual(env["META_API_KEY"], "scoped")
        self.assertNotIn("MODEL_API_KEY", env)

    def test_quota_signal_is_the_typed_field(self):
        self.assertTrue(is_quota_failure({
            "method": "turn/completed",
            "params": {"error": {"kind": "quota", "message": "cap", "retryable": False, "quota": {}}},
        }))
        self.assertFalse(is_quota_failure({
            "method": "turn/completed",
            "params": {"error": {"kind": "model", "message": "quota in the prose", "retryable": True}},
        }))

    def test_argv_has_no_yolo(self):
        argv = serve_argv(
            binary="muse", model="muse-wonder-full-1.0", provider="meta", contributor_mode="off"
        )
        self.assertNotIn("--yolo", argv)
        self.assertNotIn("--disable-sandbox", argv)
        self.assertEqual(argv[argv.index("--contributor-mode") + 1], "off")

    def test_mcp_allows_loopback_and_rejects_stdio_and_remote(self):
        cleaned = normalize_mcp_servers({
            "unreal": {"transport": "streamableHttp", "url": "http://127.0.0.1:8000/mcp"},
        })
        self.assertEqual(cleaned["unreal"]["url"], "http://127.0.0.1:8000/mcp")
        with self.assertRaises(MuseHarnessError):
            normalize_mcp_servers({"bad": {"transport": "stdio", "command": "/bin/bash", "args": ["-c", "id"]}})
        with self.assertRaises(MuseHarnessError):
            normalize_mcp_servers({"bad": {"transport": "streamableHttp", "url": "http://169.254.169.254/latest"}})
        with self.assertRaises(MuseHarnessError):
            normalize_mcp_servers({"bad": {"transport": "streamableHttp", "url": "http://user:pass@127.0.0.1:9/"}})

    def test_workspace_cannot_escape_session_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            inside = root / "proj"
            inside.mkdir()
            outside = root.parent
            self.assertEqual(workspace_within(str(inside), str(root)), os.path.realpath(inside))
            with self.assertRaises(MuseHarnessError):
                workspace_within(str(outside), str(inside))

    def test_pick_choice_denies_unless_allow(self):
        choices = [
            {"choiceId": "a", "decision": "approved"},
            {"choiceId": "d", "decision": "denied"},
            {"choiceId": "s", "decision": "approvedForSession"},
        ]
        self.assertEqual(pick_choice(choices, allow=False)["choiceId"], "d")
        self.assertEqual(pick_choice(choices, allow=True)["choiceId"], "a")

    def test_plugin_registers_declared_tool_without_model_approval_override(self):
        registered = []

        class Ctx:
            def register_tool(self, **kwargs):
                registered.append(("tool", kwargs["name"]))

            def register_command(self, name, **kwargs):
                registered.append(("command", name))

            def get_config(self, key, default=None):
                return default

        import importlib.util
        spec = importlib.util.spec_from_file_location("muse_harness_plugin", ROOT / "__init__.py")
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        plugin.register(Ctx())
        self.assertIn(("tool", "muse_turn"), registered)
        self.assertIn(("command", "muse"), registered)
        os.environ["MUSE_HARNESS_APPROVAL"] = "allowAll"
        try:
            self.assertEqual(plugin._approval(Ctx()), "denyUnmatched")
        finally:
            os.environ.pop("MUSE_HARNESS_APPROVAL", None)


if __name__ == "__main__":
    unittest.main()
