"""Fable in the new-session model list, and version labels next to the aliases. No network."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zidos_pager as cc  # noqa: E402
import test_zidos_pager as base  # noqa: E402

CHOICES = ["fable", "opus", "sonnet", "haiku", "claude-fable-5-1"]
LABELS = {
    "fable": "Fable 5.2",
    "opus": "Opus 5.5",
    "sonnet": "Sonnet 5.5",
    "haiku": "Haiku 5.5",
    "claude-fable-5-1": "Fable 5.1",
}


class ModelLabelTests(unittest.TestCase):
    def test_alias_and_labels(self):
        self.assertEqual(cc.bots_alias("", "fable"), "claude-fable-5-2")
        self.assertEqual(cc.bots_alias("", "opus"), "claude-opus-5-5")
        self.assertEqual(cc.bots_alias("", "sonnet"), "claude-sonnet-5-5")
        self.assertEqual(cc.bots_alias("", "haiku"), "claude-haiku-5-5")
        self.assertEqual(cc.bots_alias("https://example.test", "fable"), "fable")  # a custom base keeps the name
        self.assertEqual(cc.bots_alias("", "claude-fable-5-1"), "claude-fable-5-1")
        for name, label in LABELS.items():
            self.assertEqual(cc.model_label(name), label, name)
        self.assertEqual(cc.model_label("glm-5.3"), "glm-5.3")
        self.assertEqual(cc.model_label("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(cc.MODEL_CHOICES["claude_login"], CHOICES)
        self.assertEqual(cc.MODEL_CHOICES["anthropic"], CHOICES)

    def test_new_session_accepts_fable(self):
        e = base.Env(self)
        for model in ("fable", "claude-fable-5-1", "opus", "sonnet", "haiku"):
            code, res = e.bot.api_new(1, {"text": "t", "cwd": "/tmp", "model": model})
            self.assertEqual(code, 200, res)
            cmd = [r for r in e.runs if "--bg" in r[0]][-1][0]
            self.assertEqual(cmd[cmd.index("--model") + 1], model)  # the alias goes to the CLI as chosen
        code, res = e.bot.api_new(1, {"text": "t", "cwd": "/tmp", "model": "glm-5.3"})
        self.assertEqual((code, res["error"]), (400, "model"))

    def test_api_dirs_and_presets_carry_labels(self):
        e = base.Env(self)
        models = e.bot.api_dirs()["models"]
        self.assertEqual(models["claude"], CHOICES)
        self.assertEqual({k: models["labels"][k] for k in CHOICES}, LABELS)
        got = e.bot.api_dispatch(1, "GET", "/api/model", None)[1]
        login = next(p for p in got["presets"] if p["id"] == "claude_login")
        self.assertEqual(login["models"], CHOICES)
        self.assertEqual(login["labels"], LABELS)
        deep = next(p for p in got["presets"] if p["id"] == "deepseek")
        self.assertEqual(deep["models"], [])
        self.assertNotIn("labels", deep)
        bots = e.bot.api_dispatch(1, "GET", "/api/bots", None)[1]
        anth = next(p for p in bots["presets"] if p["id"] == "anthropic")
        self.assertEqual(anth["models"], CHOICES)
        self.assertEqual(anth["labels"], LABELS)
        kimi = next(p for p in bots["presets"] if p["id"] == "kimi")
        self.assertEqual(kimi["models"], [])
        self.assertNotIn("labels", kimi)
