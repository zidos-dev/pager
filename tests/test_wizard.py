import http.client
import http.server
import importlib.util
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wizard_setup", ROOT / "setup.py")
ws = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ws)

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
KEY = "sk-secretkey-0123456789"

FAKE_SSH = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
a = sys.argv[1:]
log = os.environ["SHIM_LOG"]
open(log + "/argv.jsonl", "a").write(json.dumps(a) + "\n")
i = 0
while i < len(a) and a[i].startswith("-"):
    i += 2 if a[i] in ("-o", "-p") else 1
cmd = a[i + 1]
data = sys.stdin.buffer.read()
open(log + "/stdin.bin", "ab").write(data)
env = dict(os.environ, HOME=os.environ["FAKE_HOME"])
p = subprocess.run(["bash", "-c", cmd], input=data, env=env, capture_output=True)
sys.stdout.buffer.write(p.stdout)
sys.stderr.buffer.write(p.stderr)
sys.exit(p.returncode)
"""

# wsl.exe: `-l -q` answers in UTF-16LE with BOM and NULs; otherwise `-d D -- bash -lc CMD` runs CMD locally.
FAKE_WSL = r"""#!/usr/bin/env python3
import json, os, subprocess, sys
a = sys.argv[1:]
log = os.environ["SHIM_LOG"]
if a[:2] == ["-l", "-q"]:
    sys.stdout.buffer.write("\ufeffUbuntu\r\nDebian\r\n".encode("utf-16-le"))
    sys.exit(0)
open(log + "/wsl.jsonl", "a").write(json.dumps(a) + "\n")
assert a[:4] == ["-d", "Ubuntu", "--", "bash"] or a[:2] == ["-d", "Ubuntu"], a
if "--exec" in a:
    sys.exit(0)
cmd = a[a.index("-lc") + 1]
data = sys.stdin.buffer.read()
open(log + "/stdin.bin", "ab").write(data)
sd = os.environ.get("FAKE_SYSTEMD_DIR")
if sd:
    cmd = cmd.replace("/run/systemd/system", sd)
env = dict(os.environ, HOME=os.environ["FAKE_HOME"])
p = subprocess.run(["bash", "-c", cmd], input=data, env=env, capture_output=True)
sys.stdout.buffer.write(p.stdout)
sys.stderr.buffer.write(p.stderr)
sys.exit(p.returncode)
"""

FAKE_REC = "#!/bin/sh\nprintf '%s\\n' \"$0 $*\" >> \"$SHIM_LOG/rec.txt\"\n"

FAKE_SETUP = '#!/bin/bash\nprintf "%s\\n" "$@" > "$SETUP_ARGS"\n'


class FakeTG(http.server.ThreadingHTTPServer):
    def __init__(self):
        super().__init__(("127.0.0.1", 0), self.H)
        self.updates, self.sent, self.offsets, self.busy = [], [], [], False
        threading.Thread(target=self.serve_forever, daemon=True).start()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
            s, method = self.server, self.path.rsplit("/", 1)[1]
            code, res = 200, {"ok": True, "result": []}
            if not self.path.startswith("/bot" + TOKEN + "/"):
                code, res = 401, {"ok": False}
            elif method == "getMe":
                res["result"] = {"username": "my_bot", "first_name": "My Bot"}
            elif method == "getUpdates":
                if s.busy:
                    code, res = 409, {"ok": False}
                else:
                    off = body.get("offset")
                    if off is not None:
                        s.offsets.append(off)
                    res["result"] = [u for u in s.updates if off is None or u["update_id"] >= off]
            elif method == "sendMessage":
                s.sent.append(body)
            data = json.dumps(res).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)


def start_msg(uid=1, text="/start"):
    return {"update_id": uid, "message": {"chat": {"id": 42, "type": "private"},
                                          "from": {"id": 42, "first_name": "Ann"}, "text": text}}


class WizardBase(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        self.home, self.log, shim = tmp / "home", tmp / "log", tmp / "shim"
        self.shim = shim
        for d in (self.home, self.log, shim):
            d.mkdir()
        for name, body in (("ssh", FAKE_SSH), ("systemctl", "#!/bin/sh\necho active\n")):
            (shim / name).write_text(body)
            (shim / name).chmod(0o755)
        self.setup_sh = tmp / "fake-setup.sh"
        self.setup_sh.write_text(FAKE_SETUP)
        self.setup_args = tmp / "setup-args"
        self.tg = FakeTG()
        self.addCleanup(self.tg.server_close)
        self.addCleanup(self.tg.shutdown)
        env = {"PATH": f"{shim}:{os.environ['PATH']}", "SHIM_LOG": str(self.log), "FAKE_HOME": str(self.home),
               "HOME": str(self.home), "TG_API_BASE": f"http://127.0.0.1:{self.tg.server_port}",
               "WIZARD_SETUP_SH": str(self.setup_sh), "SETUP_ARGS": str(self.setup_args)}
        p = mock.patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        self.srv = ws.make_server(0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = self.srv.server_port

    def call(self, path, body=None, token=True, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        h = {"Host": host or f"127.0.0.1:{self.port}", "Content-Type": "application/json"}
        if token:
            h["X-Wizard-Token"] = self.srv.token
        c.request("GET" if body is None else "POST", path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if raw[:1] == b"{" else raw)

    def ssh_target(self):
        st, r = self.call("/api/target", {"mode": "ssh", "host": "box", "user": "me", "port": 2222})
        self.assertTrue(r["ok"], r)
        return r

    def full_flow(self, **install):
        self.ssh_target()
        self.assertTrue(self.call("/api/bot", {"token": TOKEN})[1]["ok"])
        self.tg.updates.append(start_msg())
        self.assertTrue(self.call("/api/owner", {})[1]["ok"])
        with mock.patch.object(ws, "model_check", return_value="ok"):
            self.assertTrue(self.call("/api/model", {"provider": "deepseek", "key": KEY})[1]["ok"])
        st, r = self.call("/api/install", {"lang": "en", **install})
        self.assertTrue(r["ok"], r)
        for _ in range(300):
            st, log = self.call("/api/install/log?from=0")
            if log["done"]:
                return log
            time.sleep(0.2)
        self.fail("install did not finish")


class WizardTests(WizardBase):
    def test_token_required(self):
        self.assertEqual(self.call("/api/state", token=False)[0], 403)
        self.assertEqual(self.call("/api/state", host="evil.example:%d" % self.port)[0], 403)
        self.assertEqual(self.call("/api/state")[0], 200)

    def test_static_missing_is_plain_404(self):
        st, body = self.call("/nope.txt")
        self.assertEqual(st, 404)

    def test_ssh_secret_via_stdin(self):
        log = self.full_flow()
        self.assertTrue(log["ok"], log)
        argv = (self.log / "argv.jsonl").read_text()
        self.assertTrue(argv)
        for secret in (TOKEN, KEY):
            self.assertNotIn(secret, argv)
            self.assertNotIn(secret, json.dumps(log))
        stdin = (self.log / "stdin.bin").read_bytes()
        self.assertIn(TOKEN.encode(), stdin)
        self.assertIn(KEY.encode(), stdin)
        self.assertNotIn(TOKEN, json.dumps(self.call("/api/state")[1]))
        self.assertTrue(any("ControlMaster=auto" in a for a in argv.splitlines()))
        tok = self.home / ".config/zidos-pager/token.env"
        self.assertEqual(stat.S_IMODE(tok.stat().st_mode), 0o600)

    def test_owner_from_start(self):
        self.ssh_target()
        self.assertEqual(self.call("/api/bot", {"token": "bad"})[1]["error"], "token")
        self.assertTrue(self.call("/api/bot", {"token": TOKEN})[1]["ok"])
        self.assertEqual(self.call("/api/owner", {})[1], {"ok": False, "waiting": True})
        self.tg.updates.append(start_msg(7))
        r = self.call("/api/owner", {})[1]
        self.assertEqual(r["owner"], {"id": 42, "name": "Ann"})
        self.assertIn(8, self.tg.offsets)  # acknowledged

    def test_bot_busy(self):
        self.ssh_target()
        self.tg.busy = True
        self.assertEqual(self.call("/api/bot", {"token": TOKEN})[1]["error"], "busy")

    def test_install_runs_setup(self):
        log = self.full_flow(group_id=-100123, name="Helper X", brief="be nice")
        self.assertTrue(log["ok"], log)
        args = self.setup_args.read_text().splitlines()
        for flag in ("--non-interactive", "--token-file", "--users", "--publish", "--with-sdk", "--sdk-key-file",
                     "--sdk-key-var", "--sdk-base-url", "--sdk-model"):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--users") + 1], "42")
        self.assertEqual(args[args.index("--publish") + 1], "none")
        self.assertEqual(args[args.index("--token-file") + 1], f"{self.home}/.config/zidos-pager/token.env")
        self.assertEqual(args[args.index("--sdk-key-var") + 1], "DEEPSEEK_API_KEY")
        envs = [args[i + 1] for i, a in enumerate(args) if a == "--env"]
        for e in ("CC_RESPONDER_CHAT=-100123", "CC_RESPONDER_NAME=Helper X", "CC_LANG=en",
                  f"CC_RESPONDER_BRIEF={self.home}/.config/zidos-pager/brief.md"):
            self.assertIn(e, envs)
        self.assertNotIn("--update", args)
        self.assertEqual((self.home / ".config/zidos-pager/brief.md").read_text().strip(), "be nice")
        self.assertEqual(self.tg.sent[0]["chat_id"], 42)

    def test_install_update_when_existing(self):
        unit = self.home / ".config/systemd/user/zidos-pager.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("")
        self.assertTrue(self.full_flow()["ok"])
        self.assertIn("--update", self.setup_args.read_text().splitlines())

    def test_install_codex_engine(self):
        self.ssh_target()
        self.call("/api/bot", {"token": TOKEN})
        self.tg.updates.append(start_msg())
        self.call("/api/owner", {})
        self.assertEqual(self.call("/api/model", {"provider": "codex"})[1]["note"], "codex login on the server")
        self.call("/api/install", {"lang": "ru"})
        for _ in range(300):
            log = self.call("/api/install/log?from=0")[1]
            if log["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(log["ok"], log)
        args = self.setup_args.read_text().splitlines()
        self.assertNotIn("--with-sdk", args)
        self.assertIn("CC_RESPONDER_ENGINE=codex", args)

    def fake_claude(self, signed):
        f = self.shim / "claude"
        f.write_text('#!/bin/sh\necho \'{"loggedIn": %s, "authMethod": "claude.ai"}\'\n' % ("true" if signed else "false"))
        f.chmod(0o755)

    def test_install_claude_code_login(self):
        self.fake_claude(True)
        self.ssh_target()
        pres = {p["id"]: p for p in self.call("/api/state")[1]["presets"]}
        self.assertTrue(pres["claude_login"]["available"] and pres["claude_login"]["login"])
        self.call("/api/bot", {"token": TOKEN})
        self.tg.updates.append(start_msg())
        self.call("/api/owner", {})
        with mock.patch.object(ws, "model_check") as mc:
            self.assertTrue(self.call("/api/model", {"provider": "claude_login", "model": "opus"})[1]["ok"])
        mc.assert_not_called()  # no key to check
        self.call("/api/install", {"lang": "ru"})
        for _ in range(300):
            log = self.call("/api/install/log?from=0")[1]
            if log["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(log["ok"], log)
        args = self.setup_args.read_text().splitlines()
        self.assertIn("--sdk-login", args)
        self.assertEqual(args[args.index("--sdk-model") + 1], "opus")
        self.assertNotIn("--sdk-key-file", args)
        self.assertNotIn("CC_RESPONDER_ENGINE=codex", args)

    def test_claude_code_login_needs_the_sign_in(self):
        self.fake_claude(False)
        self.ssh_target()
        self.assertFalse(next(p for p in self.call("/api/state")[1]["presets"] if p["id"] == "claude_login")["available"])
        self.assertEqual(self.call("/api/model", {"provider": "claude_login"})[1], {"ok": False, "error": "claude_login"})

    def test_groups(self):
        self.ssh_target()
        self.call("/api/bot", {"token": TOKEN})
        self.tg.updates += [
            {"update_id": 1, "my_chat_member": {"chat": {"id": -5, "type": "supergroup", "title": "Team"}}},
            {"update_id": 2, "message": {"chat": {"id": -6, "type": "group", "title": "Chat"}, "text": "hi"}},
            {"update_id": 3, "message": {"chat": {"id": 9, "type": "private"}, "text": "x"}},
        ]
        gs = self.call("/api/groups", {})[1]["groups"]
        self.assertEqual({(g["id"], g["title"]) for g in gs}, {(-5, "Team"), (-6, "Chat")})

    def test_model_check(self):
        for res, ok in (("ok", True), ("key", False), ("balance", False), ("net", False)):
            with mock.patch.object(ws, "model_check", return_value=res) as mc:
                r = self.call("/api/model", {"provider": "glm", "model": "m1", "base": "https://x.test", "key": KEY})[1]
            self.assertEqual(r["ok"], ok)
            if not ok:
                self.assertEqual(r["error"], res)
            mc.assert_called_once_with("https://x.test", KEY, "m1")
        self.assertNotIn(KEY, json.dumps(r))

    def test_darwin_local(self):
        with mock.patch.object(ws.platform, "system", return_value="Darwin"):
            r = self.call("/api/target", {"mode": "local"})[1]
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["os"], "Darwin")
            self.assertTrue(r["systemd"])
            self.assertNotIn("bwrap", r["missing"])
            self.assertNotIn("socat", r["missing"])
            pres = {p["id"]: p for p in self.call("/api/state")[1]["presets"]}
        self.assertFalse(pres["codex"]["available"])
        self.assertTrue(pres["deepseek"]["available"])

    def test_missing_blocks(self):
        probe = "os=Linux\npy=3.12\nno=bwrap\nsystemd=1\n"
        with mock.patch.object(ws.Target, "run", return_value=(0, probe)):
            r = self.call("/api/target", {"mode": "local"})[1]
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "missing")
        self.assertEqual(r["missing"], ["bwrap"])
        self.assertIn("bubblewrap", r["fix"])
        self.assertIsNone(self.call("/api/state")[1]["target"])

    def test_missing_venv_module_blocks_on_linux(self):  # Ubuntu without python3-venv: the SDK venv would fail at install
        probe = "os=Linux\npy=3.12\nno=venv\nsystemd=1\n"
        with mock.patch.object(ws.Target, "run", return_value=(0, probe)):
            r = self.call("/api/target", {"mode": "local"})[1]
        self.assertFalse(r["ok"])
        self.assertEqual(r["missing"], ["venv"])
        self.assertEqual(r["fix"], "sudo apt-get install -y python3-venv")


class BotsTests(WizardBase):
    BTOKEN = "777777:BBBBBBBBBBBBBBBBBBBBBBBBBBBB"

    def run_cli(self, path, body=None, reply=None):
        calls = []

        def run(_self, cmd, stdin=None, timeout=120):
            calls.append((cmd, stdin))
            return 0, "noise\n" + json.dumps(reply or {"ok": True})
        with mock.patch.object(ws.Target, "run", run):
            return self.call(path, body), calls

    def test_not_installed_and_token(self):
        for path, body in (("/api/bots", None), ("/api/bots/add", {"token": self.BTOKEN})):
            self.assertEqual(self.call(path, body, token=False)[0], 403)
        self.ssh_target()
        st, r = self.call("/api/bots/add", {"token": self.BTOKEN})
        self.assertEqual(r["error"], "notinstalled")
        self.assertEqual(self.call("/api/bots")[1]["error"], "notinstalled")

    def test_routes_use_cli_and_stdin_only(self):
        self.assertTrue(self.full_flow()["ok"])
        for sub in ("add", "save", "persona", "remove"):
            body = {"token": self.BTOKEN, "key": KEY, "slug": "x"}
            (st, r), calls = self.run_cli("/api/bots/" + sub, body, {"ok": True, "slug": "x"})
            self.assertEqual((st, r), (200, {"ok": True, "slug": "x"}))
            (cmd, stdin), = calls
            self.assertTrue(cmd.endswith(f"python3 zidos_pager.py bots {sub}"), cmd)
            self.assertEqual(json.loads(stdin), body)
            for secret in (self.BTOKEN, KEY):
                self.assertNotIn(secret, cmd)
        (st, r), calls = self.run_cli("/api/bots", None, {"ok": True, "bots": []})
        self.assertTrue(calls[0][0].endswith("bots list"))
        self.assertIsNone(calls[0][1])
        self.assertEqual(r["bots"], [])
        _, log = self.call("/api/install/log?from=0")
        self.assertNotIn(self.BTOKEN, json.dumps(log))
        self.assertNotIn(KEY, json.dumps(log))

    def test_cli_garbage_does_not_echo_output(self):
        self.assertTrue(self.full_flow()["ok"])
        with mock.patch.object(ws.Target, "run", lambda *a, **k: (1, "Traceback " + self.BTOKEN)):
            st, r = self.call("/api/bots/add", {"token": self.BTOKEN})
        self.assertEqual(r, {"ok": False, "error": "cli"})


class WindowsTests(WizardBase):
    def setUp(self):
        super().setUp()
        self.localapp = self.home / "LocalAppData"
        self.sd = self.home / "sd"
        self.sd.mkdir()
        for name, body in (("wsl.exe", FAKE_WSL), ("schtasks", FAKE_REC), ("wscript.exe", FAKE_REC),
                           ("bwrap", "#!/bin/sh\n"), ("socat", "#!/bin/sh\n")):
            (self.shim / name).write_text(body)
            (self.shim / name).chmod(0o755)
        for p in (mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.localapp), "FAKE_SYSTEMD_DIR": str(self.sd)}),
                  mock.patch.object(ws.platform, "system", return_value="Windows")):
            p.start()
            self.addCleanup(p.stop)

    def test_windows_wsl_probe_install(self):
        r = self.call("/api/target", {"mode": "local"})[1]
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["os"], "Windows")
        self.assertEqual(self.call("/api/state")[1]["target"]["os"], "Windows")
        pres = {p["id"]: p for p in self.call("/api/state")[1]["presets"]}
        self.assertFalse(pres["codex"]["available"])
        self.assertTrue(pres["deepseek"]["available"])
        self.assertTrue(self.call("/api/bot", {"token": TOKEN})[1]["ok"])
        self.tg.updates.append(start_msg())
        self.assertTrue(self.call("/api/owner", {})[1]["ok"])
        with mock.patch.object(ws, "model_check", return_value="ok"):
            self.assertTrue(self.call("/api/model", {"provider": "deepseek", "key": KEY})[1]["ok"])
        self.call("/api/install", {"lang": "en"})
        for _ in range(300):
            log = self.call("/api/install/log?from=0")[1]
            if log["done"]:
                break
            time.sleep(0.2)
        self.assertTrue(log["ok"], log)
        argv = (self.log / "wsl.jsonl").read_text()
        self.assertIn('["-d", "Ubuntu", "--", "bash", "-lc"', argv)
        self.assertIn("fake-setup.sh", argv)  # WIZARD_SETUP_SH override of deploy/setup.sh
        self.assertNotIn("setup-macos", argv)
        for secret in (TOKEN, KEY):
            self.assertNotIn(secret, argv)
            self.assertIn(secret.encode(), (self.log / "stdin.bin").read_bytes())
        self.assertIn("--with-sdk", self.setup_args.read_text().splitlines())

    def test_windows_wsl_missing_and_no_systemd(self):
        r = self.call("/api/target", {"mode": "local"})[1]
        self.assertTrue(r["ok"], r)
        os.environ["FAKE_SYSTEMD_DIR"] = str(self.sd / "absent")
        r = self.call("/api/target", {"mode": "local"})[1]
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "missing")
        self.assertIn("systemd", r["missing"])
        self.assertIn("/etc/wsl.conf", r["fix"])
        self.assertIn("wsl --shutdown", r["hint"])

    def test_windows_wsl_absent(self):
        (self.shim / "wsl.exe").unlink()
        with mock.patch.dict(os.environ, {"PATH": str(self.shim)}):
            r = self.call("/api/target", {"mode": "local"})[1]
        self.assertEqual((r["ok"], r["error"], r["fix"]), (False, "wsl", "wsl --install -d Ubuntu"))

    def test_windows_keepalive(self):
        log = []
        t = ws.Target("wsl", distro="Ubuntu")
        ws.Wizard()._wsl_keepalive(t, log.append)
        vbs = self.localapp / "zidos-pager" / "wsl-keepalive.vbs"
        txt = vbs.read_text()
        self.assertIn("wsl.exe -d Ubuntu --exec sleep infinity", txt)
        self.assertIn(", 0, False", txt)
        rec = (self.log / "rec.txt").read_text().splitlines()
        self.assertIn(f'/Create /TN zidos-pager-wsl /TR wscript.exe "{vbs}" /SC ONLOGON /RL LIMITED /F', rec[0])
        self.assertIn("/Run /TN zidos-pager-wsl", rec[1])
        self.assertIn("schtasks /Delete /TN zidos-pager-wsl /F", log[-1])

    def test_windows_ssh_no_controlmaster(self):
        self.ssh_target()
        argv = (self.log / "argv.jsonl").read_text()
        self.assertTrue(argv)
        self.assertNotIn("Control", argv)
        (self.shim / "ssh").write_text("#!/bin/sh\nexit 255\n")
        r = self.call("/api/target", {"mode": "ssh", "host": "box"})[1]
        self.assertEqual((r["ok"], r["error"]), (False, "auth"))
        self.assertIn("ssh-keygen", r["hint"])


if __name__ == "__main__":
    unittest.main()
