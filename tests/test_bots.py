"""Group-assistant bots: registry, CLI, HTTP and the responder role. No network, no real services, HOME is temporary."""
import io
import json
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zidos_pager as cc  # noqa: E402
import test_zidos_pager as base  # noqa: E402

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123"
KEY = "sk-bots-SECRET-key-123"


class FakeBotTG(cc.TG):
    calls = []
    updates_error = None
    me = {"username": "Demo_Bot", "first_name": "Demo", "can_read_all_group_messages": True}
    fail_methods = ()

    def _open(self, method, req, timeout):
        FakeBotTG.calls.append((method, json.loads(req.data or b"{}")))
        if method == "getMe":
            return dict(FakeBotTG.me)
        if method == "getUpdates" and FakeBotTG.updates_error:
            raise cc.TGError("getUpdates: HTTP %s Conflict" % FakeBotTG.updates_error)
        if method in FakeBotTG.fail_methods:
            raise cc.TGError("%s: HTTP 400 Bad Request" % method)
        return True


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


class BotsCase(unittest.TestCase):
    def setUp(self):
        self.e = base.Env(self)
        self.cfg = self.e.cfg
        self.svc = []
        FakeBotTG.calls, FakeBotTG.updates_error, FakeBotTG.fail_methods = [], None, ()
        for p in (mock.patch.object(cc, "TG", lambda token, api="x": FakeBotTG(token, api)),
                  mock.patch.object(cc, "bots_service", lambda a, s: self.svc.append((a, s)) or True)):
            p.start()
            self.addCleanup(p.stop)
        import tempfile
        repo = tempfile.mkdtemp()  # a repo dir with the SDK runner installed
        self.addCleanup(shutil.rmtree, repo, True)
        os.makedirs(repo + "/.venv-sdk/bin")
        open(repo + "/.venv-sdk/bin/python", "w").close()
        p = mock.patch.object(cc, "BOTS_REPO", repo)
        p.start()
        self.addCleanup(p.stop)
        self.home = self.cfg.home
        self.cdir = os.path.join(self.home, ".config/zidos-pager/bots/demo_bot")
        self.sdir = os.path.join(self.home, ".local/state/zidos-pager/bots/demo_bot")

    def add(self):
        r = cc.bots_add(self.cfg, {"token": TOKEN})
        self.assertTrue(r["ok"], r)
        return r

    def save(self, verdict="ok", **body):
        body["slug"] = "demo_bot"
        with mock.patch.object(cc, "model_check", return_value=verdict) as chk:
            self.chk = chk
            return cc.bots_save(self.cfg, body)


class RegistryTests(BotsCase):
    def test_perms_save_preserves_sessions_and_get_returns_chat(self):
        self.add()
        path = self.cdir + "/perms.json"
        sessions = {"mode": "all", "allow": ["Read"], "deny": ["Bash"]}
        cc.bots_write(path, json.dumps({"sessions": sessions}))
        chat = {"mode": "edits", "allow": [" Read ", "Read"], "deny": ["Bash(rm:*)"]}
        result = self.save(perms=chat)
        self.assertTrue(result["ok"], result)
        with open(path) as f:
            saved = json.load(f)
        self.assertEqual(saved["sessions"], sessions)
        self.assertEqual(saved["chat"], dict(chat, allow=["Read"]))
        self.assertEqual(result["bot"]["perms"], saved["chat"])
        self.assertEqual(cc.bots_get(self.cfg, "demo_bot")["perms"], saved["chat"])
        self.assertEqual(mode(path), 0o600)

    def test_perms_invalid_rejected_before_any_save(self):
        self.add()
        for perms in ({"mode": "all"}, {"allow": ["bad rule"]}, None):
            self.assertEqual(self.save(perms=perms, about="not saved"), {"ok": False, "error": "perms"})
            self.assertEqual(cc.bots_read(self.cdir + "/about.md"), "")
            self.assertFalse(os.path.exists(self.cdir + "/perms.json"))

    def test_perms_unreadable_policy_is_not_replaced(self):
        self.add()
        path = self.cdir + "/perms.json"
        cc.bots_write(path, json.dumps({"sessions": {"deny": ["Bash"]}}))
        real_open = open
        def unreadable(name, *args, **kw):
            if name == path:
                raise PermissionError("policy unreadable")
            return real_open(name, *args, **kw)
        with mock.patch("builtins.open", side_effect=unreadable), mock.patch.object(cc, "perms_write") as write:
            self.assertEqual(self.save(perms={"mode": "edits"}), {"ok": False, "error": "perms"})
            write.assert_not_called()

    def test_empty_list(self):
        self.assertEqual(cc.bots_list(self.cfg), {"ok": True, "bots": []})

    def test_add_writes_registry_by_contract(self):
        r = self.add()
        self.assertEqual((r["slug"], r["username"], r["name"]), ("demo_bot", "demo_bot", "Demo"))
        self.assertEqual(self.svc, [("start", "demo_bot")])
        env = cc.env_read(self.cdir + "/env")
        self.assertEqual(env["TG_BOT_TOKEN"], TOKEN)
        self.assertEqual((env["CC_ROLE"], env["TG_ALLOWED_USERS"], env["CC_LANG"]), ("responder", "1", self.cfg.lang))
        self.assertEqual(env["CC_RESPONDER_BRIEF"], self.cdir + "/brief.md")
        self.assertEqual(env["TG_API_BASE"], self.cfg.api_base)  # a bot talks to the same Bot API server as the console
        self.assertEqual((env["CC_RESPONDER_RATE"], env["CC_RESPONDER_ENGINE"]), ("30", "sdk"))
        self.assertEqual((env["ZIDOS_PAGER_STATE"], env["CC_RESPONDER_LOG"]),
                         (self.sdir + "/state.json", self.sdir + "/responder.jsonl"))
        self.assertEqual((env["CC_SDK_SOCKET"], env["CC_SDK_ENV"]), (self.sdir + "/sdk.sock", self.cdir + "/sdk.env"))
        self.assertNotIn("CC_RESPONDER_CHAT", env)
        self.assertEqual(env["CC_RESPONDER_CWD"], os.path.join(self.home, "zidos-pager-knowledge", "demo_bot"))  # never the home
        self.assertTrue(os.path.isdir(env["CC_RESPONDER_CWD"]))
        sdk = cc.env_read(self.cdir + "/sdk.env")
        self.assertEqual((sdk["CC_SDK_SOCKET"], sdk["CC_SDK_STATE"], sdk["CC_SDK_KEY_FILE"]),
                         (self.sdir + "/sdk.sock", self.sdir + "/sdk-state", self.cdir + "/keys.env"))
        self.assertEqual(mode(self.cdir), 0o700)
        self.assertEqual(mode(self.sdir), 0o700)
        for name in ("env", "sdk.env", "keys.env", "about.md", "brief.md"):
            self.assertEqual(mode(os.path.join(self.cdir, name)), 0o600, name)
        self.assertEqual(cc.bots_list(self.cfg)["bots"][0]["slug"], "demo_bot")

    def test_privacy_mode_is_named_with_the_fix(self):
        # live 05.11: with privacy on, plain «@bot question» never reached the bot, only /cmd@bot did
        FakeBotTG.me = dict(FakeBotTG.me, can_read_all_group_messages=False)
        try:
            self.add()
            self.assertIn("/setprivacy", cc.bots_list(self.cfg)["bots"][0]["error"])
            self.save(rate=5)
            self.assertIn("/setprivacy", cc.bots_list(self.cfg)["bots"][0]["error"])
        finally:
            FakeBotTG.me = dict(FakeBotTG.me, can_read_all_group_messages=True)
        self.save(rate=6)
        self.assertNotIn("/setprivacy", cc.bots_list(self.cfg)["bots"][0]["error"])

    def test_add_errors(self):
        self.assertEqual(cc.bots_add(self.cfg, {"token": "nope"})["error"], "token")
        FakeBotTG.updates_error = 409
        self.assertEqual(cc.bots_add(self.cfg, {"token": TOKEN})["error"], "busy")
        self.assertFalse(os.path.exists(self.cdir))
        FakeBotTG.updates_error = None
        self.add()
        self.assertEqual(cc.bots_add(self.cfg, {"token": TOKEN})["error"], "exists")
        FakeBotTG.me = {"username": "Ab-cd"}
        try:
            self.assertEqual(cc.bots_add(self.cfg, {"token": TOKEN})["error"], "slug")
        finally:
            FakeBotTG.me = {"username": "Demo_Bot", "first_name": "Demo"}

    def test_save_validates_before_writing(self):
        self.add()
        self.assertEqual(self.save(cwd="/no/such/dir")["error"], "cwd")
        self.assertEqual(self.save(site="http://x.example")["error"], "site")
        self.assertEqual(self.save(chat="abc")["error"], "chat")
        self.assertEqual(cc.bots_save(self.cfg, {"slug": "../x"})["error"], "slug")
        self.assertEqual(cc.bots_save(self.cfg, {"slug": "ghost"})["error"], "missing")
        ok = self.save(cwd=self.home, site="https://x.example", chat=-100500, rate=5, about="a project", brief="# b")
        self.assertTrue(ok["ok"], ok)
        bot = ok["bot"]
        self.assertEqual((bot["cwd"], bot["site"], bot["chat"], bot["about"], bot["brief"]),
                         (self.home, "https://x.example", -100500, "a project", "# b"))
        self.assertEqual(cc.env_read(self.cdir + "/env")["CC_RESPONDER_RATE"], "5")
        self.assertEqual(cc.env_read(self.cdir + "/sdk.env")["CC_CHAT_SITE"], "https://x.example")
        self.assertIn(("restart", "demo_bot"), self.svc)

    def test_key_is_stored_only_after_model_check_ok(self):
        self.add()
        r = self.save(verdict="balance", provider="deepseek", key=KEY)
        self.assertEqual(r, {"ok": False, "error": "balance"})
        self.assertEqual(cc.env_read(self.cdir + "/keys.env"), {})
        self.assertNotIn("CC_SDK_MODEL", cc.env_read(self.cdir + "/sdk.env"))
        r = self.save(provider="deepseek", key=KEY)
        self.assertTrue(r["ok"], r)
        self.assertEqual(cc.env_read(self.cdir + "/keys.env"), {"DEEPSEEK_API_KEY": KEY})
        self.assertEqual(mode(self.cdir + "/keys.env"), 0o600)
        sdk = cc.env_read(self.cdir + "/sdk.env")
        self.assertEqual((sdk["CC_SDK_MODEL"], sdk["CC_SDK_KEY_VAR"], sdk["CC_SDK_BASE_URL"]),
                         ("deepseek-flash", "DEEPSEEK_API_KEY", "https://api.deepseek.com/anthropic"))
        self.assertEqual((r["bot"]["provider"], r["bot"]["model"]), ("deepseek", "deepseek-flash"))
        self.assertNotIn(KEY, json.dumps(r))
        self.assertNotIn(KEY, cc.bots_read(self.cdir + "/env") + cc.bots_read(self.cdir + "/sdk.env"))
        self.assertEqual(self.chk.call_args[0][1:], (KEY, "deepseek-flash"))
        # the stored key is reused when only the model changes
        self.assertTrue(self.save(provider="deepseek", model="deepseek-pro")["ok"])
        self.assertEqual(self.chk.call_args[0][1:], (KEY, "deepseek-pro"))
        self.assertEqual(self.save(provider="custom", model="m", base="http://plain", key=KEY)["error"], "base")
        self.assertEqual(self.save(provider="nope")["error"], "provider")
        self.assertEqual(self.save(provider="glm", model="bad model!")["error"], "model")

    def test_persona_save_applies_to_telegram_and_telegram_errors_do_not_fail(self):
        self.add()
        FakeBotTG.calls.clear()
        persona = {"name": "N" * 100, "short": "short", "description": "long"}
        r = self.save(persona=persona)
        self.assertTrue(r["ok"], r)
        sent = [(m, p) for m, p in FakeBotTG.calls if m.startswith("setMy")]
        self.assertEqual([m for m, _ in sent], ["setMyName", "setMyShortDescription", "setMyDescription"])
        self.assertEqual(sent[0][1]["name"], "N" * 64)
        self.assertEqual(r["bot"]["persona"]["name"], "N" * 64)
        self.assertEqual((r["bot"]["name"], r["bot"]["error"]), ("N" * 64, ""))
        FakeBotTG.fail_methods = ("setMyName",)
        r = self.save(persona=dict(persona, name="Other"))
        self.assertTrue(r["ok"])
        self.assertIn("setMyName", r["bot"]["error"])
        self.assertNotIn(TOKEN, json.dumps(r))

    def test_persona_repeat_and_empty_fields_do_not_touch_telegram(self):
        self.add()
        persona = {"name": "Helper", "short": "short", "description": "long"}
        self.assertTrue(self.save(persona=persona)["ok"])
        FakeBotTG.calls.clear()
        self.assertTrue(self.save(persona=persona)["ok"])
        self.assertEqual([m for m, _ in FakeBotTG.calls if m.startswith("setMy")], [])
        r = self.save(persona={"name": "Helper", "short": "new short", "description": ""})
        self.assertEqual([m for m, _ in FakeBotTG.calls if m.startswith("setMy")], ["setMyShortDescription"])
        self.assertEqual(r["bot"]["persona"], {"name": "Helper", "short": "new short", "description": "long"})

    def test_group_save_without_a_model_and_repeat_save_skip_model_check(self):
        self.add()
        r = self.save(provider="", chat=-100500)
        self.assertTrue(r["ok"], r)
        self.chk.assert_not_called()
        self.assertTrue(self.save(provider="deepseek", model="deepseek-flash", key=KEY)["ok"])
        self.assertTrue(self.save(provider="deepseek", model="deepseek-flash", base="", about="x")["ok"])
        self.chk.assert_not_called()
        self.assertTrue(self.save(provider="deepseek", model="deepseek-pro")["ok"])
        self.chk.assert_called_once()

    def test_bot_lists_stored_keys_and_base_but_never_the_key(self):
        self.add()
        self.assertTrue(self.save(provider="deepseek", key=KEY)["ok"])
        bot = cc.bots_list(self.cfg)["bots"][0]
        self.assertEqual((bot["keys"], bot["base"]), (["deepseek"], "https://api.deepseek.com/anthropic"))
        self.assertNotIn(KEY, json.dumps(bot))

    def test_limits_of_about_and_brief(self):
        self.add()
        self.assertTrue(self.save(about="a" * 8000, brief="b" * 20000)["ok"])
        self.assertEqual(self.save(about="a" * 8001)["error"], "about")
        self.assertEqual(self.save(brief="b" * 20001)["error"], "brief")

    def test_service_failure_and_missing_runner_go_to_error_txt(self):
        with mock.patch.object(cc, "bots_service", lambda a, s: False):
            r = cc.bots_add(self.cfg, {"token": TOKEN})
            self.assertTrue(r["ok"])
            self.assertEqual(cc.bots_read(self.cdir + "/error.txt"), "services did not start: run python3 setup.py again")
            FakeBotTG.fail_methods = ("setMyName",)
            r = self.save(persona={"name": "N", "short": "", "description": ""})
            self.assertTrue(r["ok"])
            self.assertIn("setMyName", r["bot"]["error"])
            self.assertIn("services did not start", r["bot"]["error"])
        FakeBotTG.fail_methods = ()
        self.assertEqual(self.save(about="x")["bot"]["error"], "")  # started: the text is cleared
        with mock.patch.object(cc, "BOTS_REPO", "/no/such/repo"):
            self.assertEqual(self.save(about="x")["bot"]["error"],
                             "SDK runner is not installed: run python3 setup.py again and choose a model")

    def test_codex_provider_needs_a_login(self):
        self.add()
        with mock.patch.object(cc.subprocess, "run", return_value=mock.Mock(returncode=1)):
            self.assertEqual(self.save(provider="codex")["error"], "codex")
        with mock.patch.object(cc.subprocess, "run", return_value=mock.Mock(returncode=0)):
            r = self.save(provider="codex")
        self.assertEqual((r["bot"]["engine"], r["bot"]["provider"]), ("codex", "codex"))

    def test_codex_model_is_saved_and_shown(self):  # 08.11 20:27: the Codex bot had no model choice in its card
        self.add()
        with mock.patch.object(cc.subprocess, "run", return_value=mock.Mock(returncode=0)):
            r = self.save(provider="codex", model="gpt-6.1-sol")
            self.assertEqual((r["bot"]["engine"], r["bot"]["model"]), ("codex", "gpt-6.1-sol"))
            self.assertEqual(cc.read_env_file(self.cdir + "/env")["CC_CODEX_MODEL"], "gpt-6.1-sol")
            r = self.save(provider="codex", model="gpt-6.2")  # the same provider, another model: still a change
            self.assertEqual(r["bot"]["model"], "gpt-6.2")
            self.assertEqual(self.save(provider="codex", model="bad name!")["error"], "model")
            r = self.save(provider="codex", model="")  # empty: the default of ~/.codex/config.toml
        self.assertNotIn("CC_CODEX_MODEL", cc.read_env_file(self.cdir + "/env"))
        self.assertEqual(r["bot"]["model"], "")

    def test_grok_and_gemini_need_their_login_file(self):  # no key, no `status` command: the CLI's login file is the proof
        self.add()
        for eng, var in (("grok", "CC_GROK_MODEL"), ("gemini", "CC_GEMINI_MODEL")):
            with mock.patch.object(cc, "login_ok", return_value=False):
                self.assertEqual(self.save(provider=eng)["error"], eng)
            with mock.patch.object(cc, "login_ok", return_value=True):
                r = self.save(provider=eng, model="m-1")
                self.assertEqual((r["bot"]["engine"], r["bot"]["provider"], r["bot"]["model"]), (eng, eng, "m-1"))
                self.assertEqual(cc.read_env_file(self.cdir + "/env")[var], "m-1")
                self.assertEqual(self.save(provider=eng, model="bad name!")["error"], "model")
                r = self.save(provider=eng, model="")  # empty: the CLI's own default
            self.assertNotIn(var, cc.read_env_file(self.cdir + "/env"))
            self.assertEqual(r["bot"]["model"], "")
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            self.assertFalse(cc.login_ok("grok", None))
            os.makedirs(os.path.join(home, ".grok"))
            open(os.path.join(home, ".grok", "auth.json"), "w").close()
            self.assertTrue(cc.login_ok("grok", None))
            self.assertFalse(cc.login_ok("gemini", None))

    def test_old_codex_env_works_without_migration(self):  # prod env files: only CC_RESPONDER_ENGINE=codex and CC_CODEX_MODEL
        cc.bots_write(self.cdir + "/env", "CC_RESPONDER_ENGINE=codex\nCC_CODEX_MODEL=gpt-6.1-sol\nTG_BOT_TOKEN=1:x\n")
        b = cc.bots_get(self.cfg, "demo_bot")
        self.assertEqual((b["engine"], b["provider"], b["model"]), ("codex", "codex", "gpt-6.1-sol"))
        self.assertEqual((cc.bots_engine({"CC_RESPONDER_ENGINE": "Codex"}), cc.bots_engine({"CC_RESPONDER_ENGINE": "nope"})), ("codex", "sdk"))
        self.assertEqual(cc.engine_var("sdk"), "CC_CODEX_MODEL")

    def test_remove_stops_services_and_deletes_dirs(self):
        self.add()
        self.assertEqual(cc.bots_remove(self.cfg, {"slug": "../etc"})["error"], "slug")
        self.assertEqual(cc.bots_remove(self.cfg, {"slug": "demo_bot"}), {"ok": True})
        self.assertEqual(self.svc[-1], ("stop", "demo_bot"))
        self.assertFalse(os.path.exists(self.cdir) or os.path.exists(self.sdir))
        self.assertEqual(cc.bots_remove(self.cfg, {"slug": "demo_bot"})["error"], "missing")

    def test_list_reads_groups_from_the_bot_state(self):
        self.add()
        os.makedirs(self.sdir, exist_ok=True)
        with open(self.sdir + "/state.json", "w") as f:
            json.dump({"groups": {"-100500": "Team"}}, f)
        self.save(chat=-100500)
        bot = cc.bots_list(self.cfg)["bots"][0]
        self.assertEqual((bot["groups"], bot["chat_title"], bot["active"]), ([{"id": -100500, "title": "Team"}], "Team", True))


class ServiceTests(unittest.TestCase):
    def run_service(self, system, action):
        calls = []
        with mock.patch.object(cc.platform, "system", return_value=system), mock.patch.object(cc.time, "sleep"), \
                mock.patch.object(cc.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or mock.Mock(returncode=0)):
            cc.bots_service(action, "demo_bot")
        return calls

    def test_linux_uses_systemd_user_templates(self):
        self.assertEqual(self.run_service("Linux", "start"), [["systemctl", "--user", "enable", "--now",
                                                              "zidos-pager-bot@demo_bot", "zidos-pager-bot-sdk@demo_bot"]])
        self.assertEqual(self.run_service("Linux", "stop")[0][2:4], ["disable", "--now"])
        self.assertEqual(self.run_service("Linux", "restart")[0][2], "restart")

    def test_macos_writes_private_plists_and_uses_launchctl(self):
        import plistlib
        import tempfile
        with tempfile.TemporaryDirectory() as home, mock.patch.dict(os.environ, {"HOME": home}):
            os.makedirs(home + "/.config/zidos-pager/bots/demo_bot")
            with open(home + "/.config/zidos-pager/bots/demo_bot/sdk.env", "w") as f:
                f.write("CC_SDK_MODEL=m1\nCC_CHAT_SITE=https://x.example\nOTHER=x\n")
            calls = self.run_service("Darwin", "start")
            self.assertTrue(any(c[:2] == ["launchctl", "bootstrap"] for c in calls))
            sdk = home + "/Library/LaunchAgents/dev.zidos.pager.sdk.demo_bot.plist"
            self.assertEqual(mode(sdk), 0o600)
            with open(sdk, "rb") as f:
                doc = plistlib.load(f)
            self.assertEqual(doc["Label"], "dev.zidos.pager.sdk.demo_bot")
            self.assertEqual(doc["EnvironmentVariables"]["CC_SDK_MODEL"], "m1")
            self.assertNotIn("OTHER", doc["EnvironmentVariables"])
            self.assertEqual(doc["EnvironmentVariables"]["CC_CHAT_SITE"], "https://x.example")
            with open(home + "/Library/LaunchAgents/dev.zidos.pager.bot.demo_bot.plist", "rb") as f:
                env = plistlib.load(f)["EnvironmentVariables"]
            self.assertTrue(env["ZIDOS_PAGER_ENV"].endswith("/bots/demo_bot/env"))
            self.assertTrue(env["PATH"].startswith(home + "/.local/bin:"))
            self.run_service("Darwin", "stop")
            self.assertFalse(os.path.exists(sdk))


class PersonaTests(BotsCase):
    GOOD = {"name": "Helper", "short": "s", "description": "d", "brief": "# Role"}

    def setUp(self):
        super().setUp()
        self.add()
        with mock.patch.object(cc, "model_check", return_value="ok"):
            cc.bots_save(self.cfg, {"slug": "demo_bot", "provider": "glm", "key": KEY})

    def persona(self, text=None, exc=None):
        def fake(base_, key, model, system, user):
            self.llm = (base_, key, model, user)
            if exc:
                raise exc
            return text
        with mock.patch.object(cc, "bots_llm", fake):
            return cc.bots_persona(self.cfg, {"slug": "demo_bot", "about": "my project"})

    def test_parses_json_even_in_fences_and_calls_this_bots_model(self):
        r = self.persona("```json\n%s\n```" % json.dumps(self.GOOD))
        self.assertEqual(r, {"ok": True, "brief": "# Role", "persona": {"name": "Helper", "short": "s", "description": "d"}})
        self.assertEqual(self.llm, ("https://api.z.ai/api/anthropic", KEY, "glm-5.3", "my project"))
        self.assertNotIn(KEY, json.dumps(r))

    def test_garbage_and_failures(self):
        self.assertEqual(self.persona("no json here"), {"ok": False, "error": "persona_fail"})
        self.assertEqual(self.persona(json.dumps({"name": "x"}))["error"], "persona_fail")
        self.assertEqual(self.persona(exc=OSError("net"))["error"], "persona_fail")

    def test_codex_engine(self):
        for eng in ("codex", "grok", "gemini"):
            cc.env_update(self.cdir + "/env", {"CC_RESPONDER_ENGINE": eng})
            self.assertEqual(self.persona("{}"), {"ok": False, "error": "persona_codex"})

    def test_request_shape(self):
        sent = {}

        class R:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def read(s):
                return json.dumps({"content": [{"type": "text", "text": "{}"}]}).encode()

        def urlopen(req, timeout=None):
            sent.update(url=req.full_url, key=req.get_header("X-api-key"), ver=req.get_header("Anthropic-version"),
                        body=json.loads(req.data))
            return R()
        with mock.patch.object(cc.urllib.request, "urlopen", urlopen):
            cc.bots_llm("", "k", "sonnet", "sys", "usr")
        self.assertEqual((sent["url"], sent["key"], sent["body"]["system"]), ("https://api.anthropic.com/v1/messages", "k", "sys"))
        self.assertTrue(sent["ver"])


class CliTests(BotsCase):
    def cli(self, *argv, stdin=""):
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", io.StringIO(stdin)), mock.patch.object(sys, "stdout", out):
            rc = cc.bots_main(self.cfg, list(argv))
        self.assertEqual(rc, 0)
        return json.loads(out.getvalue())

    def test_json_in_json_out_and_exit_zero_on_errors(self):
        self.assertEqual(self.cli("list"), {"ok": True, "bots": []})
        self.assertEqual(self.cli("add", stdin=json.dumps({"token": TOKEN}))["slug"], "demo_bot")
        self.assertEqual(self.cli("remove", stdin=json.dumps({"slug": "ghost"})), {"ok": False, "error": "missing"})
        self.assertEqual(self.cli("save", stdin="not json")["error"], "json")
        self.assertFalse(self.cli("nope")["ok"])


class HttpTests(unittest.TestCase):
    def setUp(self):
        self.w = base.WebEnv(self)
        self.bot = self.w.e.bot
        self.calls = []
        for name in cc.BOTS_ACTIONS:
            p = mock.patch.dict(cc.BOTS_ACTIONS, {name: lambda cfg, body, n=name: self.calls.append((n, body)) or {"ok": True}})
            p.start()
            self.addCleanup(p.stop)

    def test_owner_only(self):
        w = self.w
        for method, path in (("GET", "/api/bots"), ("POST", "/api/bots/add")):
            self.assertEqual(w.req(method, path, {} if method == "POST" else None, auth=False)[0], 401)
            self.assertEqual(w.req(method, path, {} if method == "POST" else None, auth=base.make_init_data(user_id=2))[0], 401)
        self.assertEqual(self.calls, [])

    def test_routes_are_thin_wrappers(self):
        w = self.w
        st, obj, _ = w.req("GET", "/api/bots")
        def row(k, p):
            models = cc.MODEL_CHOICES.get(k, [])
            out = {"id": k, "title": p["title"], "model": p["model"], "models": models}
            if models:
                out["labels"] = {m: cc.model_label(m) for m in models}
            return out
        self.assertEqual((st, obj), (200, {"bots": [], "presets": [row(k, p) for k, p in cc.MODEL_PRESETS.items()]}))
        self.assertNotIn("stored", obj["presets"][0])
        for action in ("add", "save", "persona", "remove"):
            st, obj, _ = w.req("POST", "/api/bots/" + action, {"slug": "x"})
            self.assertEqual((st, obj), (200, {"ok": True}))
        self.assertEqual([c[0] for c in self.calls], ["add", "save", "persona", "remove"])
        self.assertEqual(w.req("POST", "/api/bots/other", {})[0], 405)
        self.assertEqual(w.req("GET", "/api/bots/add")[0], 405)


class ResponderRoleTests(unittest.TestCase):
    def setUp(self):
        self.e = base.Env(self)
        self.e.cfg.role = "responder"
        self.e.cfg.responder_chat = -100500
        self.e.cfg.responder_name = "Helper"
        self.bot = self.e.bot
        self.tg = self.e.tg

    def test_config_role(self):
        self.assertEqual(cc.Config(env_path="/none", environ={"HOME": "/x"}).role, "console")
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "env")
            with open(p, "w") as f:
                f.write("TG_BOT_TOKEN=T\nCC_ROLE=responder\n")
            self.assertEqual(cc.Config(env_path=p, environ={"HOME": t}).role, "responder")

    def test_config_sdk_env_comes_from_the_env_file_first(self):
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            p = os.path.join(t, "env")
            with open(p, "w") as f:
                f.write("TG_BOT_TOKEN=T\nCC_SDK_ENV=/x/sdk.env\n")
            self.assertEqual(cc.Config(env_path=p, environ={"HOME": t, "CC_SDK_ENV": "/y/sdk.env"}).sdk_env, "/x/sdk.env")
            self.assertEqual(cc.Config(env_path="/none", environ={"HOME": t, "CC_SDK_ENV": "/y/sdk.env"}).sdk_env, "/y/sdk.env")

    def test_report_word_is_an_ordinary_question_for_a_bot(self):
        self.bot.chat_username = "helper_bot"
        self.bot.responder_git = lambda: True
        self.bot.report_request = mock.Mock()
        self.bot.responder_next = mock.Mock()
        self.bot.responder_on_message({"message_id": 5, "chat": {"id": -100500, "type": "supergroup"}, "from": {"id": 9},
                                       "text": "@helper_bot отчёт"})
        self.bot.report_request.assert_not_called()
        self.assertEqual(self.bot._resp_queue[0]["q"], "отчёт")

    def test_run_forever_starts_no_console_machinery(self):
        threads = []

        class T:
            def __init__(s, target=None, args=(), **kw):
                threads.append((getattr(target, "__name__", ""), args))

            def start(s):
                pass
        self.bot.poll_once = lambda: self.bot.stop_event.set()
        with mock.patch.object(cc.threading, "Thread", T), mock.patch.object(cc, "WebServer", side_effect=AssertionError("web")):
            self.bot.run_forever()
        self.assertEqual(sorted(n for n, _ in threads), ["_loop", "sdk_watch"])
        self.assertEqual([a[0].__name__ for n, a in threads if n == "_loop"], ["responder_typing_tick"])
        self.assertIsNone(self.bot.webapp_server)
        self.assertEqual(self.tg.of("setMyCommands") + self.tg.of("setChatMenuButton"), [])

    def test_groups_are_recorded_and_left_groups_dropped(self):
        grp = {"id": -100500, "type": "supergroup", "title": "Team"}
        self.bot.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": grp, "from": {"id": 9}, "text": "hi"}})
        self.bot.handle_update({"update_id": 2, "my_chat_member": {"chat": {"id": -7, "type": "group", "title": "Other"},
                                                                  "new_chat_member": {"status": "member"}}})
        self.assertEqual(self.bot.store.data["groups"], {"-100500": "Team", "-7": "Other"})
        self.bot.handle_update({"update_id": 3, "my_chat_member": {"chat": {"id": -7, "type": "group"},
                                                                  "new_chat_member": {"status": "kicked"}}})
        self.assertEqual(self.bot.store.data["groups"], {"-100500": "Team"})
        self.assertEqual(json.loads(cc.bots_read(self.bot.store.path))["groups"], {"-100500": "Team"})

    def private(self, uid, text="hi"):
        self.bot.handle_update({"update_id": 1, "message": {"message_id": 1, "chat": {"id": uid, "type": "private"},
                                                            "from": {"id": uid}, "text": text}})

    def test_private_stranger_silent_owner_gets_one_status_line(self):
        self.private(555)
        self.assertEqual(self.tg.of("sendMessage"), [])
        self.private(1, "/s")
        sent = self.tg.of("sendMessage")
        self.assertEqual(len(sent), 1)
        self.assertIn("Helper", sent[0]["text"])
        self.assertIn("-100500", sent[0]["text"])
        self.assertEqual(self.e.runs, [])  # nothing of the console ran

    def test_only_confirmation_buttons_are_handled(self):
        with mock.patch.object(self.bot, "on_callback") as cb:
            for data in ("s:abc", "new", "pr:1", "bl:x:run"):
                self.bot.handle_update({"update_id": 1, "callback_query": {"id": "c", "data": data, "from": {"id": 1},
                                                                         "message": {"chat": {"id": 1, "type": "private"}}}})
            self.bot.handle_update({"update_id": 2, "callback_query": {"id": "c", "data": "pr:1", "from": {"id": 5},
                                                                     "message": {"chat": {"id": 5, "type": "private"}}}})
        self.assertEqual([c[0][0]["data"] for c in cb.call_args_list], ["pr:1", "bl:x:run"])

    def test_stranger_press_gets_a_popup_only_for_them(self):
        """A group member pressing 🔐 / backlog: a popup to them (the spinner stops), nothing runs, nothing in the group."""
        with mock.patch.object(self.bot, "on_callback") as cb:
            self.bot.handle_update({"update_id": 1, "callback_query": {"id": "q5", "data": "pr:1", "from": {"id": 5},
                                                                     "message": {"chat": {"id": -100500, "type": "supergroup"}}}})
            self.bot.handle_update({"update_id": 2, "callback_query": {"id": "q6", "data": "zz", "from": {"id": 5},
                                                                     "message": {"chat": {"id": -100500, "type": "supergroup"}}}})
        cb.assert_not_called()
        ans = self.tg.of("answerCallbackQuery")
        self.assertEqual([a["callback_query_id"] for a in ans], ["q5", "q6"])
        self.assertEqual(ans[0]["text"], cc._t("Only the owner can press this button."))
        self.assertNotIn("text", ans[1])
        self.assertEqual(self.tg.of("sendMessage"), [])

    def test_allowed_updates_include_membership(self):
        tg = base.FakeTG()
        tg.get_updates(0)
        self.assertIn("my_chat_member", tg.of("getUpdates")[0]["allowed_updates"])


if __name__ == "__main__":
    unittest.main()


class LoginBoxTests(unittest.TestCase):
    def login_cmd(self, eng, real_rel, auth_rel):
        with tempfile.TemporaryDirectory() as home:
            real = os.path.join(home, real_rel)
            os.makedirs(os.path.dirname(real))
            open(real, "w").close()
            os.chmod(real, 0o755)
            auth = os.path.join(home, auth_rel)
            os.makedirs(os.path.dirname(auth), exist_ok=True)
            open(auth, "w").close()
            with mock.patch.dict(os.environ, {"HOME": home}), mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/bwrap" if x == "bwrap" else real):
                return home, auth, cc.login_boxed(eng, "/tmp", "вопрос", "m1")

    def test_grok_box_has_no_web_search_and_only_its_auth_file(self):
        home, auth, cmd = self.login_cmd("grok", ".grok/downloads/grok-1/grok", ".grok/auth.json")
        self.assertIn("--disable-web-search", cmd)
        off = cmd[cmd.index("--disallowed-tools") + 1].split(",")
        self.assertTrue({"run_terminal_command", "write", "search_replace", "spawn_subagent"} <= set(off))
        self.assertEqual(cmd[cmd.index("--permission-mode") + 1], "plan")
        self.assertEqual(cmd[cmd.index("-m") + 1], "m1")
        self.assertEqual([cmd[i + 1] for i, c in enumerate(cmd) if c == "--bind"], [auth])
        self.assertNotIn(os.path.join(home, ".grok"), [cmd[i + 1] for i, c in enumerate(cmd) if c in ("--ro-bind", "--bind")])  # never the whole directory
        self.assertEqual(cc._grok_answer('{"type":"assistant"}\nnoise\n{"type":"result","result":"kumquat","is_error":false}\n'), "kumquat")
        self.assertIsNone(cc._grok_answer('{"type":"result","result":"x","is_error":true}'))

    def test_gemini_box_has_only_its_token_and_a_read_only_agent(self):
        home, auth, cmd = self.login_cmd("gemini", "agyhome/agy", ".gemini/antigravity-cli/antigravity-oauth-token")
        self.assertEqual([cmd[i + 1] for i, c in enumerate(cmd) if c == "--bind"], [auth])
        self.assertNotIn(os.path.join(home, ".gemini"), [cmd[i + 1] for i, c in enumerate(cmd) if c in ("--ro-bind", "--bind")])
        self.assertEqual((cmd[cmd.index("--mode") + 1], cmd[cmd.index("--agent") + 1], cmd[cmd.index("--model") + 1]), ("plan", "chatro", "m1"))
        agent = cmd.index(os.path.join(home, ".gemini/antigravity-cli/agents/chatro.md"))
        self.assertEqual(cmd[agent - 2], "--ro-bind")
        self.assertIn("tools: [read_file, list_dir, grep_search]", cc.GEMINI_CHAT_AGENT)  # no web, no commands, no writes

    def test_a_binary_in_local_bin_mounts_itself_not_the_owner_scripts_beside_it(self):
        """agy lies bare in ~/.local/bin, grok is a symlink there: the box sees the binary or its package, never ~/.local/bin."""
        home, auth, cmd = self.login_cmd("gemini", ".local/bin/agy", ".gemini/antigravity-cli/antigravity-oauth-token")
        binds = [cmd[i + 1] for i, c in enumerate(cmd) if c in ("--ro-bind", "--bind")]
        self.assertNotIn(os.path.join(home, ".local", "bin"), binds)
        self.assertIn(os.path.join(home, ".local", "bin", "agy"), binds)
        with tempfile.TemporaryDirectory() as home:
            real = os.path.join(home, ".grok", "downloads", "grok-1")
            os.makedirs(os.path.dirname(real)); open(real, "w").close()
            link = os.path.join(home, ".local", "bin", "grok")
            os.makedirs(os.path.dirname(link)); os.symlink(real, link)
            open(os.path.join(home, ".grok", "auth.json"), "w").close()
            with mock.patch.dict(os.environ, {"HOME": home}), mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/bwrap" if x == "bwrap" else link):
                cmd = cc.login_boxed("grok", "/tmp", "- a brief in a list")
            binds = [cmd[i + 1] for i, c in enumerate(cmd) if c in ("--ro-bind", "--bind")]
            self.assertNotIn(os.path.join(home, ".local", "bin"), binds)
            self.assertEqual(cmd[cmd.index("-p") + 1], " - a brief in a list")  # a value, not a flag


class ModelCheckTests(unittest.TestCase):
    def test_cli_aliases_become_model_ids_without_a_base(self):
        for fn in (lambda: cc.model_check("", "k", "sonnet"), lambda: cc.bots_llm("", "k", "sonnet", "s", "u")):
            with mock.patch.object(cc.urllib.request, "urlopen", side_effect=OSError) as op:
                try:
                    fn()
                except OSError:
                    pass
            self.assertEqual(json.loads(op.call_args[0][0].data)["model"], "claude-sonnet-5-5")
        with mock.patch.object(cc.urllib.request, "urlopen", side_effect=OSError) as op:
            try:
                cc.bots_llm("https://x.example", "k", "sonnet", "s", "u")
            except OSError:
                pass
        body = json.loads(op.call_args[0][0].data)
        self.assertEqual((body["model"], body["max_tokens"]), ("sonnet", 8000))

    def test_http_codes_map_to_verdicts(self):
        import io
        import urllib.error
        for code, verdict in ((401, "key"), (402, "balance"), (429, "limit"), (404, "model"), (500, "net")):
            err = urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))
            with mock.patch.object(cc.urllib.request, "urlopen", side_effect=err):
                self.assertEqual(cc.model_check("https://x.example", "k", "m"), verdict, code)


class PersonaNoModelTests(BotsCase):
    def test_persona_before_a_model_says_so(self):
        self.add()
        r = cc.bots_persona(self.cfg, {"slug": "demo_bot", "about": "A coffee shop"})
        self.assertEqual(r, {"ok": False, "error": "persona_nomodel"})


class AdoptTests(BotsCase):
    """08.11 owner: «Почему нельзя бота прям в боте настраивать?» — the group bot of the main env becomes a registry bot."""

    def main_setup(self, engine="codex"):
        t = self.home
        os.makedirs(t + "/proj")
        cc.bots_write(t + "/brief.md", "Be kind.")
        cc.bots_write(t + "/keys/api.env", "ANTHROPIC_API_KEY_1=%s\n" % KEY)
        cc.bots_write(t + "/sdk.env", "CC_SDK_KEY_FILE=%s/keys/api.env\nCC_SDK_KEY_VAR=ANTHROPIC_API_KEY_1\n"
                                      "CC_SDK_MODEL=sonnet\nCC_SDK_BUDGET=0\n" % t)
        cc.bots_write(t + "/perms.json", json.dumps({"sessions": {"mode": "all"}, "chat": {"mode": "edits", "allow": ["Read"]}}))
        log = t + "/st/responder.jsonl"
        cc.bots_write(log, '{"q": "hi"}\n{"who": "Анна", "from": 5}\n')
        cc.bots_write(t + "/st/relay.json", '{"mids": [1], "until": 9999999999}')
        cc.bots_write(t + "/st/reports/r1.md", "report")
        cc.bots_write(self.cfg.state_path, json.dumps({"offset": 7, "chat_offset": 70, "chat_tail": [{"who": "A", "text": "x"}], "resp_mids": [3],
                                                       "backlog": [{"id": "b1", "status": "open"}], "bl_seq": 1,
                                                       "responder": {"sid": "s1"}}))
        env = cc.read_env_file(self.cfg.env_path)
        cc.env_update(self.cfg.env_path, {
            "CC_SDK_ENV": t + "/sdk.env", "CC_RESPONDER_TOKEN": TOKEN, "CC_RESPONDER_CHAT": "-100500",
            "CC_RESPONDER_CWD": t + "/proj", "CC_RESPONDER_BRIEF": t + "/brief.md", "CC_RESPONDER_ENGINE": engine,
            "CC_CODEX_MODEL": "gpt-6.1-sol", "CC_RESPONDER_RATE": "12", "CC_RESPONDER_LOG": log})
        self.assertEqual(env["TG_BOT_TOKEN"], "T")
        return cc.Config(env_path=self.cfg.env_path, environ={"HOME": t})

    def test_env_group_bot_moves_into_the_registry_once(self):
        cfg = self.main_setup()
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(cc.bots_adopt(cfg), "demo_bot")
        self.assertNotIn(TOKEN, err.getvalue())
        self.assertNotIn(KEY, err.getvalue())
        self.assertEqual(self.svc, [("start", "demo_bot")])
        env = cc.env_read(self.cdir + "/env")
        self.assertEqual((env["TG_BOT_TOKEN"], env["CC_ROLE"], env["CC_RESPONDER_CHAT"], env["CC_RESPONDER_CWD"]),
                         (TOKEN, "responder", "-100500", self.home + "/proj"))
        self.assertEqual((env["CC_RESPONDER_ENGINE"], env["CC_CODEX_MODEL"], env["CC_RESPONDER_RATE"]), ("codex", "gpt-6.1-sol", "12"))
        self.assertEqual(env["CC_RESPONDER_BRIEF"], self.cdir + "/brief.md")
        self.assertEqual(cc.bots_read(self.cdir + "/brief.md"), "Be kind.")
        self.assertEqual(cc.perms_read(self.cdir + "/perms.json")["chat"], {"mode": "edits", "allow": ["Read"], "deny": []})
        sdk = cc.env_read(self.cdir + "/sdk.env")
        self.assertEqual((sdk["CC_SDK_MODEL"], sdk["CC_SDK_BUDGET"], sdk["CC_SDK_KEY_VAR"]), ("sonnet", "0", "ANTHROPIC_API_KEY"))
        self.assertEqual(cc.env_read(self.cdir + "/keys.env"), {"ANTHROPIC_API_KEY": KEY})
        self.assertEqual(mode(self.cdir + "/keys.env"), 0o600)
        with open(self.sdir + "/state.json") as f:
            st = json.load(f)
        self.assertEqual((st["chat_tail"], st["resp_mids"], st["bl_seq"]), ([{"who": "A", "text": "x"}], [3], 1))
        self.assertEqual(st["groups"], {"-100500": "-100500"})  # getChat gave no title here: the id, never «no group»
        self.assertNotIn("responder", st)  # its session belongs to the console's runner
        self.assertEqual(st["offset"], 70)  # the console's position on this token, not the console's own «offset»
        self.assertEqual(cc.bots_read(self.sdir + "/responder.jsonl"), '{"q": "hi"}\n{"who": "Анна", "from": 5}\n')
        self.assertIn('"mids": [1]', cc.bots_read(self.sdir + "/relay.json"))
        self.assertEqual(cc.bots_read(self.sdir + "/reports/r1.md"), "report")
        main = cc.env_read(cfg.env_path)
        self.assertFalse([k for k in main if k in cc.ADOPT_ENV], main)
        self.assertEqual(main["TG_BOT_TOKEN"], "T")
        backups = [x for x in os.listdir(os.path.dirname(cfg.env_path)) if x.startswith("env.bak-adopt-")]
        self.assertEqual(len(backups), 1)
        b = cc.bots_get(cc.Config(env_path=cfg.env_path, environ={"HOME": self.home}), "demo_bot")
        self.assertEqual((b["chat"], b["chat_title"], b["engine"], b["provider"]), (-100500, "-100500", "codex", "codex"))
        # second start: nothing to adopt
        self.svc.clear()
        self.assertIsNone(cc.bots_adopt(cc.Config(env_path=cfg.env_path, environ={"HOME": self.home})))
        self.assertEqual(self.svc, [])

    def test_a_half_done_adoption_is_redone(self):
        cfg = self.main_setup()
        os.makedirs(self.cdir)
        cc.bots_write(self.cdir + "/.adopting", "")
        cc.bots_write(self.cdir + "/brief.md", "partial")
        self.assertEqual(cc.bots_adopt(cfg), "demo_bot")
        self.assertEqual(cc.bots_read(self.cdir + "/brief.md"), "Be kind.")
        self.assertFalse(os.path.exists(self.cdir + "/.adopting"))

    def test_the_same_bot_already_added_only_clears_the_env(self):
        cfg = self.main_setup()
        self.add()
        cc.bots_write(self.cdir + "/brief.md", "mine")
        self.svc.clear()
        self.assertEqual(cc.bots_adopt(cfg), "demo_bot")
        self.assertEqual(cc.bots_read(self.cdir + "/brief.md"), "mine")
        self.assertNotIn("CC_RESPONDER_TOKEN", cc.env_read(cfg.env_path))

    def test_telegram_down_leaves_everything(self):
        cfg = self.main_setup()
        with mock.patch.object(FakeBotTG, "get_me", side_effect=cc.TGError("getMe: HTTP 502")):
            self.assertIsNone(cc.bots_adopt(cfg))
        self.assertFalse(os.path.exists(self.cdir))
        self.assertIn("CC_RESPONDER_TOKEN", cc.env_read(cfg.env_path))

    def test_only_the_console_adopts(self):
        cfg = self.main_setup()
        cfg.role = "responder"
        self.assertIsNone(cc.bots_adopt(cfg))
        self.assertFalse(os.path.exists(self.cdir))


class AdoptMainTests(BotsCase):
    def test_main_adopts_before_the_console_starts_and_rereads_the_env(self):
        cfgs, seen = [], []
        def config():
            cfgs.append(mock.Mock(lang="en", token="T", allowed={1}))
            return cfgs[-1]
        bot = mock.Mock()
        with mock.patch.object(cc, "Config", side_effect=config), mock.patch.object(cc, "migrate_legacy_paths"), mock.patch.object(cc, "set_lang"), \
                mock.patch.object(cc, "bots_adopt", side_effect=lambda c: seen.append(c) or "demo_bot"), \
                mock.patch.object(cc, "Bot", side_effect=lambda c: seen.append(c) or bot), mock.patch.object(sys, "argv", ["zp"]):
            self.assertEqual(cc.main(), 0)
        self.assertEqual(seen, [cfgs[0], cfgs[1]])  # adopt on the first read, the console on the env without the group bot
        with mock.patch.object(cc, "bots_adopt") as adopt, mock.patch.object(sys, "argv", ["zp", "bots", "list"]), \
                mock.patch.object(cc, "bots_main", return_value=0), mock.patch.object(cc, "migrate_legacy_paths"), mock.patch.object(cc, "set_lang"):
            cc.main()
        adopt.assert_not_called()  # the `bots` command never moves anything


class AdoptRelayTests(BotsCase):
    main_setup = AdoptTests.main_setup

    def test_relay_goes_through_the_adopted_bot(self):
        """`zidos_pager.py relay` (the owner's messages to the group, used 08.11) read the token from the main env."""
        cfg = self.main_setup()
        cc.bots_adopt(cfg)
        msg = os.path.join(self.home, "msg.txt")
        cc.bots_write(msg, "Привет")
        sent = []
        def send_one(tg, chat, text, **kw):
            sent.append((TOKEN in tg.url, chat))
            return {"message_id": 42}
        with mock.patch.object(FakeBotTG, "send_one", send_one):
            self.assertEqual(cc.relay_main(cc.Config(env_path=cfg.env_path, environ={"HOME": self.home}), ["--to", "Анна", msg]), 0)
        self.assertEqual(sent, [(True, -100500)])
        with open(self.sdir + "/relay.json") as f:
            self.assertEqual(json.load(f)["mids"], [1, 42])


class AdoptFailTests(BotsCase):
    main_setup = AdoptTests.main_setup

    def test_a_failed_adoption_keeps_the_env_and_the_console_starts(self):
        cfg = self.main_setup()
        bot = mock.Mock()
        with mock.patch.object(cc, "bots_adopt_copy", side_effect=OSError("disk")), \
                mock.patch.object(cc, "Config", return_value=cfg), mock.patch.object(cc, "migrate_legacy_paths"), mock.patch.object(cc, "set_lang"), \
                mock.patch.object(cc, "Bot", return_value=bot), mock.patch.object(sys, "argv", ["zp"]):
            self.assertEqual(cc.main(), 0)
        bot.run_forever.assert_called_once()
        self.assertEqual(cc.env_read(cfg.env_path)["CC_RESPONDER_TOKEN"], TOKEN)
        self.assertTrue(os.path.exists(self.cdir + "/.adopting"))  # the next start redoes it
