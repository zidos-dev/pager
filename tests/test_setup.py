"""Installer contract tests: all service/network commands run as local fakes."""
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:TEST_SECRET_TOKEN"
SDK_SECRET = "sk-sdk-INSTALLER_SECRET"
GROQ_SECRET = "gsk-INSTALLER_SECRET"
TS_KEY = "tskey-api-INSTALLER_SECRET-xyz"

# A single executable dispatches by argv[0]. Its shebang is absolute so PATH can
# be restricted to this fixture, including when testing missing dependencies.
FAKE = r'''
import json, os, pathlib, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["SETUP_TEST_LOG"], "a") as f:
    f.write(json.dumps([name] + args) + "\n")
if name == "systemctl" and os.environ.get("SETUP_TEST_ENVLOG"):
    with open(os.environ["SETUP_TEST_ENVLOG"], "a") as f:
        f.write(os.environ.get("XDG_RUNTIME_DIR", "") + "|" + os.environ.get("DBUS_SESSION_BUS_ADDRESS", "") + "\n")
if name == "python3":
    if len(args) >= 3 and args[:2] == ["-m", "venv"]:
        dest = pathlib.Path(args[-1]).resolve()
        root = pathlib.Path(os.environ["SETUP_TEST_REPO"]).resolve()
        if root not in dest.parents:
            sys.exit("refusing fake venv outside temporary repository")
        (dest / "bin").mkdir(parents=True, exist_ok=True)
        for executable in ("python", "python3", "pip", "pip3"):
            (dest / "bin" / executable).symlink_to(os.environ["SETUP_TEST_FAKE"])
        sys.exit(0)
    if args[:2] == ["-m", "pip"]:
        sys.exit(0)
    if args[:1] == ["-"] and os.environ.get("SETUP_TEST_API_STATE"):
        # Exercise the installer's real API code without opening any sockets.
        import io, urllib.request, urllib.error
        state_file = pathlib.Path(os.environ["SETUP_TEST_API_STATE"])
        def urlopen(req, timeout=None):
            assert req.full_url.startswith("https://api.setup.test/api/v2/tailnet/-/")
            state = json.loads(state_file.read_text())
            state["auth"].append(req.get_header("Authorization"))
            path = req.full_url.removeprefix("https://api.setup.test")
            method = req.get_method()
            code, body, headers = 200, "{}", {}
            if method == "GET" and path == "/api/v2/tailnet/-/acl":
                body, headers = state["policy"], {"ETag": '"etag-1"'}
            elif method == "GET" and path == "/api/v2/tailnet/-/settings":
                body = json.dumps({"httpsEnabled": state["https"]})
            elif method == "POST" and path == "/api/v2/tailnet/-/acl":
                if req.get_header("If-match") != '"etag-1"':
                    code = 412
                else:
                    state["posts"].append([req.data.decode(), req.get_header("Content-type")])
            elif method == "PATCH" and path == "/api/v2/tailnet/-/settings":
                state["patches"].append(json.loads(req.data))
                code = state["patch_status"]
            else:
                code = 404
            state_file.write_text(json.dumps(state))
            response = io.BytesIO(body.encode())
            if code != 200:
                raise urllib.error.HTTPError(req.full_url, code, "fixture error", headers, response)
            response.status, response.headers = code, headers
            return response
        urllib.request.urlopen = urlopen
        sys.argv = args
        exec(compile(sys.stdin.read(), "<installer>", "exec"), {"__name__": "__main__"})
        sys.exit(0)
    os.execv(os.environ["SETUP_TEST_PYTHON"], [os.environ["SETUP_TEST_PYTHON"]] + args)
if name in ("python", "pip", "pip3"):
    if name.startswith("pip") or args[:2] == ["-m", "pip"]:
        sys.exit(0)
    os.execv(os.environ["SETUP_TEST_PYTHON"], [os.environ["SETUP_TEST_PYTHON"]] + args)
if name == "loginctl":
    print(os.environ.get("SETUP_TEST_LINGER", "yes"))
elif name == "tailscale":
    if args[:1] == ["status"]:
        if os.environ.get("SETUP_TEST_STATUS_HANG"):
            import time
            time.sleep(600)
        state = os.environ.get("SETUP_TEST_TS_STATE", "Running")
        lf = os.environ.get("SETUP_TEST_LOGIN_FLAG")
        if lf and os.path.exists(lf):
            state = "Running"
        data = {"BackendState": state, "Self": {"DNSName": os.environ.get("SETUP_TEST_DNS", "box.tail1234.ts.net.")}}
        ff = os.environ.get("SETUP_TEST_FUNNEL_FLAG")
        if ff and os.path.exists(ff):
            data["Self"]["CapMap"] = {"https://tailscale.com/cap/funnel": None}
            data["CertDomains"] = ["box.tail1234.ts.net"]
        mode = os.environ.get("SETUP_TEST_CAP_MODE")
        if mode:
            cap = os.environ.get("SETUP_TEST_CAP", "funnel")
            if mode == "ports-only":
                data["Self"]["CapMap"] = {"https://tailscale.com/cap/funnel-ports?ports=443,8443,10000": None}
            elif mode == "capabilities":
                data["Self"]["Capabilities"] = [cap]
            else:
                data["Self"]["CapMap"] = {cap: None}
            cert_location = os.environ.get("SETUP_TEST_CERT_LOCATION", "top")
            if cert_location != "missing":
                (data if cert_location == "top" else data["Self"])["CertDomains"] = ["box.tail1234.ts.net"]
        print(json.dumps(data))
    elif args[:1] == ["up"]:
        if os.environ.get("SETUP_TEST_LOGIN_PID"):
            pathlib.Path(os.environ["SETUP_TEST_LOGIN_PID"]).write_text(str(os.getpid()))
        print("\nTo authenticate, visit:\n\n\thttps://login.tailscale.com/a/abc123\n", flush=True)
        import time
        while not os.path.exists(os.environ["SETUP_TEST_LOGIN_FLAG"]):
            time.sleep(0.2)
    elif args[:2] == ["funnel", "status"]:
        host = "box.tail1234.ts.net:443"
        if "SETUP_TEST_FUNNEL_STATUS" in os.environ:
            print(os.environ["SETUP_TEST_FUNNEL_STATUS"])
            sys.exit(0)
        if os.environ.get("SETUP_TEST_EMPTY_FUNNEL_STATUS"):
            print("{}")
            sys.exit(0)
        print(json.dumps({"Web": {host: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18795"}}}},
                          "AllowFunnel": {host: True}}))
    elif args[:1] == ["funnel"] and os.environ.get("SETUP_TEST_FUNNEL_FLAG") and not os.path.exists(os.environ["SETUP_TEST_FUNNEL_FLAG"]):
        print("\nFunnel is not enabled on your tailnet.\nTo enable, visit:\n\n         https://login.tailscale.com/f/funnel?node=test\n", flush=True)
        import time
        time.sleep(600)
    elif args[:1] == ["funnel"] and os.environ.get("SETUP_TEST_FUNNEL_HANG"):
        # the real CLI prints the enablement link and waits until Funnel is enabled
        print("\nFunnel is not enabled on your tailnet.\nTo enable, visit:\n\n         https://login.tailscale.com/f/funnel?node=test\n", flush=True)
        import time
        time.sleep(600)
    elif args[:1] == ["funnel"] and os.environ.get("SETUP_TEST_FUNNEL_FAIL"):
        print("Enable Funnel at https://login.tailscale.com/f/funnel?node=test", file=sys.stderr)
        sys.exit(1)
    elif args[:1] == ["funnel"]:
        print("Available on the internet: https://box.tail1234.ts.net/")
elif name == "sudo":
    if args[:1] == ["-v"]:
        sys.exit(0)
    target = str(pathlib.Path(sys.argv[0]).parent / args[0])
    if os.environ.get("SETUP_TEST_SUDO_WRAPPER"):
        import signal, subprocess
        child = subprocess.Popen([target] + args[1:])
        signal.signal(signal.SIGTERM, lambda *_: child.terminate())
        sys.exit(child.wait())
    os.execv(target, [target] + args[1:])
elif name == "qrencode":
    print("[QR:" + args[-1] + "]")
elif name == "curl":
    print("{}")
elif name == "xdg-open" and os.environ.get("SETUP_TEST_BROWSER_HANG"):
    import time
    time.sleep(600)
'''


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="zidos-pager-setup-test-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir()
        self.repo = self.base / "checkout"
        self.repo.mkdir()
        shutil.copytree(ROOT / "deploy", self.repo / "deploy")
        for name in ("zidos_pager.py", "sdk_runner.py", "stt_runner.py", "env.example"):
            shutil.copy2(ROOT / name, self.repo / name)
        self.script = self.repo / "deploy" / "setup.sh"
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.log = self.base / "commands.jsonl"
        self.fake = self.base / "fake-command"
        self.fake.write_text("#!" + sys.executable + "\n" + FAKE)
        self.fake.chmod(0o755)
        for name in ("python3", "systemctl", "loginctl", "tailscale", "cloudflared", "curl", "ffmpeg", "ffprobe", "bwrap", "socat", "sudo", "xdg-open"):
            (self.bin / name).symlink_to(self.fake)
        for name in ("bash", "dirname", "basename", "mkdir", "chmod", "cat", "rm", "mv", "mktemp", "id", "cp", "readlink", "sed", "grep", "tr", "head", "tail", "touch", "date", "uname", "realpath", "install", "timeout", "sleep", "ln"):
            executable = shutil.which(name)
            if executable:
                (self.bin / name).symlink_to(executable)
        self.env = {"HOME": str(self.home), "PATH": str(self.bin),
                    "LANG": "C.UTF-8", "TMPDIR": str(self.base),
                    "XDG_CONFIG_HOME": str(self.home / ".config"),
                    "SETUP_TEST_LOG": str(self.log), "SETUP_TEST_REPO": str(self.repo),
                    "SETUP_TEST_FAKE": str(self.fake), "SETUP_TEST_PYTHON": sys.executable}
        self.config = self.home / ".config" / "zidos-pager"
        self.env_file = self.config / "env"
        self.tunnel_file = self.home / ".local/state/zidos-pager/tunnel-url"

    def run_setup(self, *args, stdin=""):
        result = subprocess.run([str(self.bin / "bash"), str(self.script), *args],
                                env=self.env, cwd=self.repo, input=stdin, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        if "SETUP_TEST_API_STATE" in self.env:
            state = json.loads(Path(self.env["SETUP_TEST_API_STATE"]).read_text())
            state["auth"] = set(state["auth"])
            self.api_state.update(state)
        for secret in (TOKEN, SDK_SECRET, GROQ_SECRET, TS_KEY):
            self.assertNotIn(secret, result.stdout, "installer disclosed a credential")
            if self.log.exists():
                self.assertNotIn(secret, self.log.read_text(), "installer passed a credential to a child command")
        return result

    def install(self, mode="funnel", *args):
        return self.run_setup("--non-interactive", "--token", TOKEN, "--users", "123,456", "--publish=" + mode, *args)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def snapshot_home(self):
        return {str(p.relative_to(self.home)): (stat.S_IMODE(p.stat().st_mode),
                p.read_bytes() if p.is_file() else None)
                for p in self.home.rglob("*")}

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout)

    def assert_private(self, path):
        self.assertTrue(path.is_file(), str(path))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, str(path))

    def assert_no_mutating_commands(self):
        for call in self.calls():
            if call[0] == "systemctl":
                self.assertFalse(set(call[1:]) & {"enable", "start", "restart", "stop", "disable", "daemon-reload"}, call)
            elif call[0] == "tailscale":
                self.assertIn(call[1:2], (["status"], ["version"], ["funnel"]), call)
                if call[1:2] == ["funnel"]:
                    self.assertEqual(call[2:3], ["status"], call)
            elif call[0] == "loginctl":
                self.assertIn("show-user", call, call)
            else:
                self.assertNotIn(call[0], ("cloudflared", "curl"), call)

    def test_help(self):
        result = self.run_setup("--help")
        self.assert_success(result)
        self.assertEqual(self.calls(), [])
        for option in ("--token", "--users", "--publish", "--dry-run", "--reconfigure-publish"):
            self.assertIn(option, result.stdout)
        self.assertEqual(self.snapshot_home(), {})
        self.assert_no_mutating_commands()

    def test_dry_run_leaves_home_and_services_unchanged(self):
        (self.home / "sentinel").write_text("existing user data\n")
        before = self.snapshot_home()
        result = self.install("funnel", "--dry-run")
        self.assert_success(result)
        self.assertEqual(self.snapshot_home(), before)
        self.assert_no_mutating_commands()

    def test_funnel_noninteractive_install(self):
        result = self.install()
        self.assert_success(result)
        self.assert_private(self.env_file)
        content = self.env_file.read_text()
        self.assertIn("TG_BOT_TOKEN=" + TOKEN, content)
        self.assertIn("TG_ALLOWED_USERS=123,456", content)
        self.assertNotIn("CC_WEBAPP_URL", content)
        self.assert_private(self.tunnel_file)
        self.assertEqual((self.tunnel_file).read_text().strip(), "https://box.tail1234.ts.net")
        self.assertEqual((self.config / "publish-mode").read_text().strip(), "funnel")
        units = self.home / ".config" / "systemd" / "user"
        bot_unit = (units / "zidos-pager.service").read_text()
        self.assertIn(str(self.repo / "zidos_pager.py"), bot_unit)
        self.assertNotIn("%h/zidos-pager/", bot_unit)
        self.assertTrue(any(c[0] == "tailscale" and c[1:] == ["funnel", "--bg", "18795"] for c in self.calls()), self.calls())

    def test_existing_env_refused_without_changes(self):
        self.config.mkdir(parents=True)
        self.env_file.write_text("TG_BOT_TOKEN=existing-private-value\n# keep this comment\n")
        self.env_file.chmod(0o640)
        before = self.snapshot_home()
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.snapshot_home(), before)
        self.assert_no_mutating_commands()

    def test_legacy_install_refused_without_update(self):
        legacy = self.home / ".config" / "cc-tgbot"  # legacy install
        legacy.mkdir(parents=True)
        (legacy / "env").write_text("TG_BOT_TOKEN=legacy-private-value\n")  # legacy env
        before = self.snapshot_home()
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("--update", result.stdout)
        self.assertEqual(self.snapshot_home(), before)
        self.assert_no_mutating_commands()

    def test_legacy_install_migrated_on_update(self):
        legacy = self.home / ".config" / "cc-tgbot"  # legacy install
        legacy.mkdir(parents=True)
        (legacy / "env").write_text("TG_BOT_TOKEN=legacy-private-value\nTG_ALLOWED_USERS=987\n")  # legacy env
        units = self.home / ".config/systemd/user"
        units.mkdir(parents=True)
        (units / "cc-tgbot.service").write_text("[Service]\n")  # legacy unit
        result = self.install("funnel", "--update")
        self.assertFalse((units / "cc-tgbot.service").exists())  # legacy unit retired
        self.assertIn("cc-tgbot.service", json.dumps(self.calls()))  # legacy unit disabled via systemctl
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertTrue(self.env_file.is_file())
        self.assertTrue(legacy.is_symlink())  # legacy paths keep resolving

    def test_legacy_units_survive_a_failed_update(self):
        legacy = self.home / ".config" / "cc-tgbot"  # legacy install
        legacy.mkdir(parents=True)
        (legacy / "env").write_text("TG_BOT_TOKEN=legacy-private-value\n")  # legacy env
        units = self.home / ".config/systemd/user"
        units.mkdir(parents=True)
        (units / "cc-tgbot.service").write_text("[Service]\n")  # legacy unit
        result = self.run_setup("--non-interactive", "--update", "--publish=funnel")  # no token: fails before install
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue((units / "cc-tgbot.service").exists())  # legacy unit still in place
        self.assertEqual((legacy / "env").read_text(), "TG_BOT_TOKEN=legacy-private-value\n")  # legacy path resolves

    def test_missing_tailscale_is_deterministic_and_actionable(self):
        (self.bin / "tailscale").unlink()
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("curl -fsSL https://tailscale.com/install.sh | sh", result.stdout)
        self.assert_no_mutating_commands()

    def test_tailscale_logged_out_has_login_hint(self):
        self.env["SETUP_TEST_TS_STATE"] = "NeedsLogin"
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("tailscale up", result.stdout.lower())
        self.assert_no_mutating_commands()

    def test_noninteractive_linger_advice_is_not_executed(self):
        self.env["SETUP_TEST_LINGER"] = "no"
        result = self.install("none")
        self.assert_success(result)
        self.assertIn("loginctl enable-linger", result.stdout)
        calls = [c for c in self.calls() if c[0] == "loginctl"]
        self.assertTrue(calls)
        self.assertTrue(all("show-user" in c and "enable-linger" not in c for c in calls), calls)

    def test_funnel_failure_preserves_enablement_link(self):
        self.env["SETUP_TEST_FUNNEL_FAIL"] = "1"
        result = self.install()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("https://login.tailscale.com/", result.stdout)

    def test_funnel_waiting_for_enablement_stops_with_link(self):
        self.env["SETUP_TEST_FUNNEL_HANG"] = "1"
        self.env["CC_SETUP_FUNNEL_WAIT"] = "2"
        import time
        t0 = time.monotonic()
        result = self.install()
        self.assertLess(time.monotonic() - t0, 30)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("https://login.tailscale.com/f/funnel?node=test", (result.stdout or "") + (result.stderr or ""))
        self.assertFalse((self.home / ".local/state/zidos-pager/tunnel-url").exists())

    def touch_later(self, path, delay=2.0):
        import threading
        timer = threading.Timer(delay, lambda: Path(path).write_text("1"))
        timer.start()
        self.addCleanup(timer.cancel)

    def interactive(self, *args, stdin=""):
        return self.run_setup("--token", TOKEN, "--users", "123,456", "--publish=funnel", *args, stdin=stdin)

    def run_functions(self, commands, *, interrupt=False):
        # Load the real function definitions without invoking installer main.
        source = self.script.read_text().rsplit('\nmain "$@"', 1)[0]
        proc = subprocess.Popen([str(self.bin / "bash")], env=self.env, cwd=self.repo,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, start_new_session=True)
        try:
            proc.stdin.write(source + "\n" + commands)
            proc.stdin.close()
            proc.stdin = None
            if interrupt:
                deadline = time.monotonic() + 3
                while not any(c == ["tailscale", "status", "--json"] for c in self.calls()):
                    self.assertLess(time.monotonic(), deadline, "status probe never started")
                    time.sleep(0.05)
                proc.send_signal(signal.SIGINT)
            try:
                output, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                self.fail("wait exceeded its elapsed-time deadline")
            if "SETUP_TEST_LOGIN_PID" in self.env:
                login_pid = int(Path(self.env["SETUP_TEST_LOGIN_PID"]).read_text())
                with self.assertRaises(ProcessLookupError, msg="login child survived await_ts"):
                    os.kill(login_pid, 0)
            return subprocess.CompletedProcess(proc.args, proc.returncode, output)
        finally:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.communicate()

    def test_funnel_probe_rejects_ports_only_capability(self):
        for mode in ("ports-only", "capabilities"):
            with self.subTest(mode=mode):
                self.env.update(SETUP_TEST_CAP_MODE=mode,
                                SETUP_TEST_CAP="https://tailscale.com/cap/funnel-ports?ports=443,8443,10000")
                result = self.run_functions("tailscale status --json | ts_funnel_ready\n")
                self.assertEqual(result.returncode, 1, result.stdout)

    def test_funnel_probe_accepts_legacy_capabilities_with_certs(self):
        for mode in ("capabilities", "capmap"):
            for cap in ("funnel", "https://tailscale.com/cap/funnel"):
                for location in ("top", "self", "missing"):
                    with self.subTest(mode=mode, cap=cap, certs=location):
                        self.env.update(SETUP_TEST_CAP_MODE=mode, SETUP_TEST_CAP=cap,
                                        SETUP_TEST_CERT_LOCATION=location)
                        result = self.run_functions("tailscale status --json | ts_funnel_ready\n")
                        self.assertEqual(result.returncode, int(location == "missing"), result.stdout)

    def test_await_ts_hung_status_deadline_cleans_login_child(self):
        pid_file = self.base / "login.pid"
        self.env.update(SETUP_TEST_STATUS_HANG="1", SETUP_TEST_LOGIN_PID=str(pid_file),
                        SETUP_TEST_LOGIN_FLAG=str(self.base / "never"),
                        CC_SETUP_FUNNEL_ENABLE_WAIT="2", CC_SETUP_POLL_STEP="10")
        result = self.run_functions('''
abort_hint='Retry login.'
tailscale up >"$TMPDIR/login.out" 2>&1 &
pid=$!
await_ts ts_running "$TMPDIR/login.out" 'https://login[^[:space:]]*' "$pid"
''')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertTrue(pid_file.exists(), result.stdout)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_await_ts_hung_browser_does_not_block_success_or_child_cleanup(self):
        self.env.update(SETUP_TEST_BROWSER_HANG="1", DISPLAY=":0",
                        SETUP_TEST_SUDO_WRAPPER="1", SETUP_TEST_LOGIN_PID=str(self.base / "login.pid"),
                        SETUP_TEST_LOGIN_FLAG=str(self.base / "never"),
                        CC_SETUP_FUNNEL_ENABLE_WAIT="2")
        result = self.run_functions('''
abort_hint='Retry login.'
timeout --kill-after=1 30 sudo tailscale up >"$TMPDIR/login.out" 2>&1 &
pid=$!
while ! grep -q https "$TMPDIR/login.out"; do sleep 0.05; done
await_ts ts_running "$TMPDIR/login.out" 'https://login[^[:space:]]*' "$pid" || exit $?
if kill -0 "$pid" 2>/dev/null; then exit 99; fi
''')
        self.assert_success(result)
        self.assertIn(["xdg-open", "https://login.tailscale.com/a/abc123"], self.calls())

    def test_await_ts_ctrl_c_cleans_login_child_during_hung_status(self):
        pid_file = self.base / "login.pid"
        self.env.update(SETUP_TEST_STATUS_HANG="1", SETUP_TEST_LOGIN_PID=str(pid_file),
                        SETUP_TEST_LOGIN_FLAG=str(self.base / "never"),
                        CC_SETUP_FUNNEL_ENABLE_WAIT="30")
        result = self.run_functions('''
abort_hint='Retry login.'
tailscale up >"$TMPDIR/login.out" 2>&1 &
pid=$!
while ! grep -q https "$TMPDIR/login.out"; do sleep 0.05; done
await_ts ts_running "$TMPDIR/login.out" 'https://login[^[:space:]]*' "$pid"
''', interrupt=True)
        self.assertEqual(result.returncode, 130, result.stdout)
        self.assertIn("Retry login.", result.stdout)
        with self.assertRaises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)

    def test_await_ts_caps_sleep_to_remaining_deadline(self):
        self.env.update(SETUP_TEST_TS_STATE="NeedsLogin", CC_SETUP_FUNNEL_ENABLE_WAIT="2",
                        CC_SETUP_POLL_STEP="10")
        result = self.run_functions("abort_hint='Retry login.'\nawait_ts ts_running\n")
        self.assertEqual(result.returncode, 1, result.stdout)

    def test_await_ts_ctrl_c_wins_over_successful_probe(self):
        result = self.run_functions('''
abort_hint='Retry login.'
interrupted_probe() { kill -INT "$$"; return 0; }
await_ts interrupted_probe
''')
        self.assertEqual(result.returncode, 130, result.stdout)

    def test_interactive_funnel_enable_waits_and_continues(self):
        flag = self.base / "funnel-enabled"
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(flag), CC_SETUP_POLL_STEP="1", DISPLAY=":0")
        (self.bin / "qrencode").symlink_to(self.fake)
        self.touch_later(flag)
        result = self.interactive()
        self.assert_success(result)
        link = "https://login.tailscale.com/f/funnel?node=test"
        self.assertIn(link, result.stdout)
        self.assertIn(["xdg-open", link], self.calls())
        self.assertIn(["qrencode", "-t", "ANSIUTF8", link], self.calls())
        funnel_runs = [c for c in self.calls() if c == ["tailscale", "funnel", "--bg", "18795"]]
        self.assertEqual(len(funnel_runs), 2, self.calls())
        self.assertEqual(self.tunnel_file.read_text().strip(), "https://box.tail1234.ts.net")

    def test_interactive_funnel_without_qrencode_or_display(self):
        flag = self.base / "funnel-enabled"
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(flag), CC_SETUP_POLL_STEP="1")
        self.touch_later(flag)
        result = self.interactive()
        self.assert_success(result)
        self.assertIn("phone", result.stdout)
        self.assertFalse(any(c[0] in ("xdg-open", "qrencode") for c in self.calls()))

    def test_noninteractive_wait_for_enable_flag(self):
        flag = self.base / "funnel-enabled"
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(flag), CC_SETUP_POLL_STEP="1")
        self.touch_later(flag)
        result = self.install("funnel", "--wait-for-enable")
        self.assert_success(result)

    def test_funnel_enable_wait_times_out_with_hint(self):
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(self.base / "never"), CC_SETUP_POLL_STEP="1",
                        CC_SETUP_FUNNEL_ENABLE_WAIT="2")
        result = self.interactive()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--reconfigure-publish --publish=funnel", result.stdout)
        self.assertFalse(self.tunnel_file.exists())

    def test_ctrl_c_while_waiting_gives_hint(self):
        import signal, time
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(self.base / "never"), CC_SETUP_POLL_STEP="1")
        proc = subprocess.Popen([str(self.bin / "bash"), str(self.script), "--token", TOKEN, "--users", "1",
                                 "--publish=funnel"], env=self.env, cwd=self.repo, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.addCleanup(proc.kill)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not any(c[1:2] == ["status"] and len(c) == 3 for c in self.calls()):
            time.sleep(0.2)
        time.sleep(1.5)
        proc.send_signal(signal.SIGINT)
        out, _ = proc.communicate(timeout=20)
        self.assertEqual(proc.returncode, 130, out)
        self.assertIn("--reconfigure-publish --publish=funnel", out)

    def test_interactive_login_flow(self):
        flag = self.base / "logged-in"
        self.env.update(SETUP_TEST_TS_STATE="NeedsLogin", SETUP_TEST_LOGIN_FLAG=str(flag), CC_SETUP_POLL_STEP="1")
        self.touch_later(flag, 6.0)
        result = self.interactive(stdin="y\n")
        self.assert_success(result)
        self.assertIn("https://login.tailscale.com/a/abc123", result.stdout)
        self.assertIn(["sudo", "tailscale", "up"], self.calls())
        self.assertEqual(self.tunnel_file.read_text().strip(), "https://box.tail1234.ts.net")

    def test_interactive_login_declined(self):
        self.env["SETUP_TEST_TS_STATE"] = "NeedsLogin"
        result = self.interactive(stdin="n\n")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(["sudo", "tailscale", "up"], self.calls())
        self.assertIn("tailscale up", result.stdout.lower())

    def start_api(self, policy, https=False, patch_status=200):
        self.api_state = {"policy": policy, "posts": [], "patches": [], "auth": [],
                          "https": https, "patch_status": patch_status}
        state_file = self.base / "api-state.json"
        state_file.write_text(json.dumps(self.api_state))
        self.api_state["auth"] = set()
        self.env.update(SETUP_TEST_API_STATE=str(state_file), CC_SETUP_TS_API="https://api.setup.test")
        return self.api_state

    def key_file(self, mode=0o600, content=None):
        path = self.base / "ts-api.env"
        path.write_text(content if content is not None else "TS_API_KEY=" + TS_KEY + "\n")
        path.chmod(mode)
        return path

    def check_key_hidden(self, result):
        self.assertNotIn(TS_KEY, result.stdout)
        self.assertNotIn(TS_KEY, self.log.read_text())

    POLICY = """// Access policy
{
\t"acls": [{"action": "accept", "src": ["*"], "dst": ["*:*"]}], // keep me
\t"tagOwners": {},
}
"""

    def test_api_key_adds_nodeattrs_and_enables_https(self):
        state = self.start_api(self.POLICY)
        flag = self.base / "funnel-enabled"
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(flag), CC_SETUP_POLL_STEP="1")
        self.touch_later(flag, 1.0)
        result = self.install("funnel", "--ts-api-key-file", str(self.key_file()))
        self.assert_success(result)
        self.check_key_hidden(result)
        self.assertEqual(state["auth"], {"Bearer " + TS_KEY})
        self.assertEqual(len(state["posts"]), 1)
        body, ctype = state["posts"][0]
        self.assertEqual(ctype, "application/hujson")
        self.assertIn("// Access policy", body)
        self.assertIn("// keep me", body)
        self.assertEqual(body.count('"nodeAttrs"'), 1)
        self.assertIn('"attr": ["funnel"]', body)
        self.assertTrue(body.index("{") < body.index("nodeAttrs") < body.index('"acls"'))
        self.assertEqual(state["patches"], [{"httpsEnabled": True}])
        self.assertEqual(self.tunnel_file.read_text().strip(), "https://box.tail1234.ts.net")

    def test_api_key_appends_to_existing_nodeattrs_without_duplicate(self):
        policy = '{\n  // c\n  "nodeAttrs": [\n    {"target": ["tag:x"], "attr": ["nextdns:abc"]}, // other\n  ],\n  "acls": []\n}\n'
        state = self.start_api(policy, https=True)
        self.env["CC_SETUP_POLL_STEP"] = "1"
        result = self.install("funnel", "--ts-api-key-file", str(self.key_file(content=TS_KEY + "\n")))
        self.assert_success(result)
        body = state["posts"][0][0]
        self.assertEqual(body.count('"nodeAttrs"'), 1)
        self.assertLess(body.index("autogroup:member"), body.index("nextdns:abc"))
        self.assertIn("// other", body)
        self.assertEqual(state["patches"], [])

    def test_api_key_policy_already_allows_funnel(self):
        policy = '{"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]}'
        state = self.start_api(policy, https=True)
        result = self.install("funnel", "--ts-api-key-file", str(self.key_file()))
        self.assert_success(result)
        self.assertEqual(state["posts"], [])
        self.assertIn("already allows", result.stdout)

    def test_api_key_file_must_be_private(self):
        result = self.install("funnel", "--ts-api-key-file", str(self.key_file(mode=0o644)))
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(TS_KEY, result.stdout)
        self.assertEqual(self.snapshot_home(), {})

    def test_api_key_requires_funnel(self):
        result = self.install("none", "--ts-api-key-file", str(self.key_file()))
        self.assertNotEqual(result.returncode, 0)

    def test_api_failure_falls_back_to_link(self):
        self.start_api(self.POLICY, patch_status=403)
        self.env.update(SETUP_TEST_FUNNEL_FLAG=str(self.base / "never"),
                        CC_SETUP_FUNNEL_ENABLE_WAIT="2", CC_SETUP_POLL_STEP="1")
        result = self.install("funnel", "--ts-api-key-file", str(self.key_file()))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("could not be enabled through the API", result.stdout)
        self.assertIn("https://login.tailscale.com/f/funnel?node=test", result.stdout)
        self.check_key_hidden(result)

    def test_dry_run_with_api_key_makes_no_calls(self):
        state = self.start_api(self.POLICY)
        result = self.install("funnel", "--dry-run", "--ts-api-key-file", str(self.key_file()))
        self.assert_success(result)
        self.assertEqual((state["posts"], state["patches"], state["auth"]), ([], [], set()))
        self.assertNotIn(TS_KEY, result.stdout)

    def test_quick_tunnel_warns_about_address_changes(self):
        result = self.install("quick")
        self.assert_success(result)
        output = result.stdout.lower()
        for word in ("restart", "pwa", "push"):
            self.assertIn(word, output)
        self.assertTrue("chang" in output or "new url" in output, output)
        self.assertEqual((self.config / "publish-mode").read_text().strip(), "quick")
        unit = self.home / ".config/systemd/user/zidos-pager-quick-tunnel.service"
        self.assertIn(str(self.repo / "deploy/zidos-pager-tunnel.sh"), unit.read_text())
        self.assertFalse(any(c[0] == "tailscale" for c in self.calls()))

    def test_none_does_not_publish(self):
        result = self.install("none")
        self.assert_success(result)
        self.assertEqual((self.config / "publish-mode").read_text().strip(), "none")
        self.assertFalse(any(c[0] in ("tailscale", "cloudflared", "curl") for c in self.calls()))

    def test_cloudflare_prints_instructions_without_mutations(self):
        self.seed_publisher("funnel")
        before = self.snapshot_home()
        result = self.run_setup("--reconfigure-publish", "--publish=cloudflare", "--cf-hostname", "app.example.com")
        self.assert_success(result)
        self.assertIn("cloudflare", result.stdout.lower())
        self.assertEqual(self.snapshot_home(), before)
        self.assert_no_mutating_commands()

    def test_cloudflare_does_not_require_api_token_file(self):
        missing_key = self.base / "missing-api-token"
        result = self.install("cloudflare", "--cf-hostname", "app.example.com", "--cf-api-token-file", str(missing_key))
        self.assert_success(result)
        self.assertIn("cloudflare", result.stdout.lower())
        self.assert_private(self.env_file)
        self.assertFalse((self.config / "publish-mode").exists())
        self.assertFalse(any(c[0] in ("tailscale", "cloudflared", "curl") for c in self.calls()))

    def test_repository_path_is_escaped_in_systemd_unit(self):
        relocated = self.base / "checkout with spaces%$"
        self.repo.rename(relocated)
        self.repo = relocated
        self.script = self.repo / "deploy/setup.sh"
        self.env["SETUP_TEST_REPO"] = str(self.repo)
        result = self.install("none")
        self.assert_success(result)
        unit = (self.home / ".config/systemd/user/zidos-pager.service").read_text()
        escaped = str(self.repo).replace("%", "%%").replace("$", "$$").replace(" ", r"\x20")
        self.assertIn(escaped + "/zidos_pager.py", unit)

    def test_conflicting_funnel_configuration_is_preserved(self):
        host = "box.tail1234.ts.net:443"
        configurations = (
            {"Web": {host: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:9999"}}}}},
            {"Web": {host: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:18795"},
                                          "/other": {"Proxy": "http://127.0.0.1:9999"}}}}},
            {"TCP": {"443": {"TCPForward": "127.0.0.1:9999"}}},
        )
        (self.home / "sentinel").write_text("existing user data\n")
        for config in configurations:
            with self.subTest(config=config):
                self.env["SETUP_TEST_FUNNEL_STATUS"] = json.dumps(config)
                self.log.unlink(missing_ok=True)
                before = self.snapshot_home()
                result = self.install()
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("443", result.stdout)
                self.assertEqual(self.snapshot_home(), before)
                self.assert_no_mutating_commands()

    def test_existing_quick_unit_without_env_is_preserved(self):
        unit = self.home / ".config/systemd/user/zidos-pager-quick-tunnel.service"
        unit.parent.mkdir(parents=True)
        unit.write_text("# existing manually managed service\n")
        before = self.snapshot_home()
        self.assertFalse(self.env_file.exists())
        result = self.install("quick")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(self.snapshot_home(), before)
        self.assert_no_mutating_commands()

    def test_relocated_runner_and_quick_executable_paths(self):
        relocated = self.base / "checkout with spaces%$'\"\\"
        self.repo.rename(relocated)
        self.repo = relocated
        self.script = self.repo / "deploy/setup.sh"
        self.env["SETUP_TEST_REPO"] = str(self.repo)
        sdk_key = self.base / "sdk-key.env"
        sdk_key.write_text("ANTHROPIC_API_KEY=" + SDK_SECRET + "\n")
        sdk_key.chmod(0o600)
        result = self.install("quick", "--with-sdk", "--sdk-key-file", str(sdk_key), "--with-stt")
        self.assert_success(result)
        argument_root = str(self.base) + r"/checkout\x20with\x20spaces%%$$\x27\x22\x5c"
        units = self.home / ".config/systemd/user"
        quick = (units / "zidos-pager-quick-tunnel.service").read_text()
        self.assertIn("ExecStart=/usr/bin/env " + argument_root + "/deploy/zidos-pager-tunnel.sh\n", quick)
        for runner in ("sdk", "stt"):
            unit = (units / ("zidos-pager-" + runner + ".service")).read_text()
            expected = ("ExecStart=/usr/bin/env " + argument_root + "/.venv-" + runner + "/bin/python -u "
                        + argument_root + "/" + runner + "_runner.py serve\n")
            self.assertIn(expected, unit)

    def test_invalid_publish_mode_is_rejected(self):
        result = self.install("invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot_home(), {})
        self.assert_no_mutating_commands()

    def test_assignment_like_repository_path_is_rejected(self):
        relocated = self.base / "checkout=other"
        self.repo.rename(relocated)
        self.repo = relocated
        self.script = self.repo / "deploy/setup.sh"
        self.env["SETUP_TEST_REPO"] = str(self.repo)
        result = self.install("none")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not contain '='", result.stdout)
        self.assertEqual(self.snapshot_home(), {})
        self.assert_no_mutating_commands()

    def test_unknown_option_is_rejected(self):
        result = self.run_setup("--does-not-exist")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot_home(), {})
        self.assert_no_mutating_commands()

    def test_reconfigure_preserves_unrelated_configuration(self):
        self.config.mkdir(parents=True)
        self.env_file.write_text("# local customization\nTG_BOT_TOKEN=old-value\nTG_ALLOWED_USERS=987\nCC_CUSTOM_SETTING=keep-me\n")
        before = self.env_file.read_bytes()
        result = self.run_setup("--reconfigure-publish", "--publish=none")
        self.assert_success(result)
        content = self.env_file.read_text()
        self.assertIn("CC_CUSTOM_SETTING=keep-me", content)
        self.assertIn("# local customization", content)
        self.assertEqual(self.env_file.read_bytes(), before)

    def test_reconfigure_conflicts_fail_before_mutations(self):
        for setting in ("CC_WEBAPP_URL=https://other.example", "CC_TUNNEL_FILE=/tmp/custom-tunnel-url", "CC_WEBAPP_PORT=19999"):
            with self.subTest(setting=setting):
                self.config.mkdir(parents=True, exist_ok=True)
                self.env_file.write_text("TG_BOT_TOKEN=old-value\nTG_ALLOWED_USERS=987\n" + setting + "\n")
                before = self.snapshot_home()
                self.log.unlink(missing_ok=True)
                result = self.run_setup("--reconfigure-publish", "--publish=funnel")
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertEqual(self.snapshot_home(), before)
                self.assert_no_mutating_commands()

    def test_funnel_status_works_without_self_dns(self):
        self.env["SETUP_TEST_DNS"] = ""
        result = self.install()
        self.assert_success(result)
        self.assertEqual((self.tunnel_file).read_text().strip(), "https://box.tail1234.ts.net")

    def test_funnel_falls_back_to_self_dns(self):
        self.env["SETUP_TEST_EMPTY_FUNNEL_STATUS"] = "1"
        result = self.install()
        self.assert_success(result)
        self.assertEqual(self.tunnel_file.read_text().strip(), "https://box.tail1234.ts.net")

    def test_null_funnel_status_falls_back_to_self_dns(self):
        self.env["SETUP_TEST_FUNNEL_STATUS"] = "null"
        result = self.install()
        self.assert_success(result)
        self.assertEqual(self.tunnel_file.read_text().strip(), "https://box.tail1234.ts.net")
        self.assert_private(self.tunnel_file)

    def seed_publisher(self, mode):
        self.config.mkdir(parents=True)
        self.env_file.write_text("TG_BOT_TOKEN=existing-value\nTG_ALLOWED_USERS=987\n")
        (self.config / "publish-mode").write_text(mode + "\n")
        self.tunnel_file.parent.mkdir(parents=True)
        self.tunnel_file.write_text("https://old.example\n")

    def test_reconfigure_quick_to_funnel_stops_quick_first(self):
        self.seed_publisher("quick")
        before = self.env_file.read_bytes()
        result = self.run_setup("--reconfigure-publish", "--publish=funnel")
        self.assert_success(result)
        calls = self.calls()
        stop = ["systemctl", "--user", "disable", "--now", "zidos-pager-quick-tunnel"]
        start = ["tailscale", "funnel", "--bg", "18795"]
        self.assertLess(calls.index(stop), calls.index(start))
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertEqual(self.tunnel_file.read_text().strip(), "https://box.tail1234.ts.net")
        self.assertEqual((self.config / "publish-mode").read_text().strip(), "funnel")

    def test_reconfigure_funnel_to_quick_stops_funnel_first(self):
        self.seed_publisher("funnel")
        before = self.env_file.read_bytes()
        result = self.run_setup("--reconfigure-publish", "--publish=quick")
        self.assert_success(result)
        calls = self.calls()
        stop = ["tailscale", "funnel", "reset"]
        start = ["systemctl", "--user", "enable", "--now", "zidos-pager-quick-tunnel"]
        self.assertLess(calls.index(stop), calls.index(start))
        self.assertFalse(self.tunnel_file.exists())
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertEqual((self.config / "publish-mode").read_text().strip(), "quick")

    def test_funnel_off_keeps_foreign_serve_config(self):
        self.seed_publisher("funnel")
        self.env["SETUP_TEST_FUNNEL_STATUS"] = json.dumps({"Web": {"box.tail1234.ts.net:443": {"Handlers": {
            "/": {"Proxy": "http://127.0.0.1:18795"}, "/other": {"Proxy": "http://127.0.0.1:3000"}}}}})
        result = self.run_setup("--reconfigure-publish", "--publish=none")
        self.assert_success(result)
        self.assertNotIn(["tailscale", "funnel", "reset"], self.calls())
        self.assertIn("other handlers", result.stderr or result.stdout)

    def test_reconfigure_funnel_to_none_removes_stale_url(self):
        self.seed_publisher("funnel")
        before = self.env_file.read_bytes()
        result = self.run_setup("--reconfigure-publish", "--publish=none")
        self.assert_success(result)
        self.assertIn(["tailscale", "funnel", "reset"], self.calls())
        self.assertFalse(self.tunnel_file.exists())
        self.assertEqual(self.env_file.read_bytes(), before)
        self.assertEqual((self.config / "publish-mode").read_text().strip(), "none")

    def test_optional_runners_dry_run_creates_no_venvs(self):
        before = self.snapshot_home()
        result = self.install("none", "--dry-run", "--with-sdk", "--with-stt",
                              "--sdk-key-file", str(self.base / "not-created-sdk-key"),
                              "--stt-groq-key-file", str(self.base / "not-created-groq-key"))
        self.assert_success(result)
        self.assertEqual(self.snapshot_home(), before)
        self.assertFalse((self.repo / ".venv-sdk").exists())
        self.assertFalse((self.repo / ".venv-stt").exists())
        self.assert_no_mutating_commands()
        self.assertFalse(any(c[0] in ("pip", "pip3") or c[1:3] == ["-m", "venv"] for c in self.calls()))

    def test_optional_runners_use_private_key_paths(self):
        sdk_key = self.base / "sdk-key.env"
        groq_key = self.base / "groq-key.env"
        sdk_key.write_text("ANTHROPIC_API_KEY=" + SDK_SECRET + "\n")
        groq_key.write_text("GROQ_API_KEY=" + GROQ_SECRET + "\n")
        sdk_key.chmod(0o600)
        groq_key.chmod(0o600)
        result = self.install("none", "--with-sdk", "--sdk-key-file", str(sdk_key),
                              "--with-stt", "--stt-groq-key-file", str(groq_key))
        self.assert_success(result)
        for runner in ("sdk", "stt"):
            path = self.config / (runner + ".env")
            self.assert_private(path)
            self.assertNotIn(TOKEN, path.read_text())
            self.assertNotIn(SDK_SECRET, path.read_text())
            self.assertNotIn(GROQ_SECRET, path.read_text())
            unit = (self.home / (".config/systemd/user/zidos-pager-" + runner + ".service")).read_text()
            self.assertIn(str(self.repo / (runner + "_runner.py")), unit)
            self.assertTrue((self.repo / (".venv-" + runner) / "bin/pip").is_file())
        self.assertIn(str(sdk_key), (self.config / "sdk.env").read_text())
        self.assertIn(str(groq_key), (self.config / "stt.env").read_text())
        self.assertIn("CC_STT_ENGINES=local,groq", (self.config / "stt.env").read_text())
        pip_calls = [c for c in self.calls() if c[0] == "pip"]
        self.assertTrue(any("claude-agent-sdk" in c for c in pip_calls), pip_calls)
        self.assertTrue(any("faster-whisper" in c for c in pip_calls), pip_calls)

    def test_sdk_third_party_endpoint_options_reach_sdk_env(self):
        sdk_key = self.base / "sdk-key.env"
        sdk_key.write_text("DEEPSEEK_API_KEY=" + SDK_SECRET + "\n")
        sdk_key.chmod(0o600)
        result = self.install("none", "--with-sdk", "--sdk-key-file", str(sdk_key),
                              "--sdk-base-url", "https://api.deepseek.com/anthropic",
                              "--sdk-model", "deepseek-v4-flash", "--sdk-key-var", "DEEPSEEK_API_KEY")
        self.assert_success(result)
        text = (self.config / "sdk.env").read_text()
        self.assertIn('CC_SDK_BASE_URL="https://api.deepseek.com/anthropic"', text)
        self.assertIn("CC_SDK_MODEL=deepseek-v4-flash", text)
        self.assertIn("CC_SDK_KEY_VAR=DEEPSEEK_API_KEY", text)
        self.assertNotIn("CC_SDK_BUDGET", text)
        self.assertNotIn(SDK_SECRET, text)

    def test_sdk_endpoint_options_need_with_sdk_and_valid_values(self):
        for args in (("--sdk-base-url", "https://x.example"), ("--sdk-model", "m"), ("--sdk-key-var", "K")):
            result = self.install("none", *args)
            self.assertNotEqual(result.returncode, 0, args)
        for args in (("--sdk-base-url", "ftp://x"), ("--sdk-model", "a b"), ("--sdk-key-var", "1X")):
            result = self.install("none", "--with-sdk", *args)
            self.assertNotEqual(result.returncode, 0, args)
        self.assertEqual(self.snapshot_home(), {})
        self.assert_no_mutating_commands()

    def test_with_sdk_without_bwrap_or_socat_fails_early_with_a_hint(self):
        for missing in ("bwrap", "socat"):
            (self.bin / missing).unlink()
            result = self.install("none", "--with-sdk")
            (self.bin / missing).symlink_to(self.fake)
            self.assertNotEqual(result.returncode, 0, missing)
            self.assertIn("bubblewrap", result.stdout)
            self.assertEqual(self.snapshot_home(), {})
            self.assert_no_mutating_commands()
        (self.bin / "bwrap").unlink()  # a dry run changes nothing and does not stop on it
        self.assert_success(self.install("none", "--dry-run", "--with-sdk",
                                         "--sdk-key-file", str(self.base / "not-created-sdk-key")))

    def token_file(self, text, mode=0o600):
        path = self.base / "token.env"
        path.write_text(text)
        path.chmod(mode)
        return path

    def test_token_file_bare_and_keyed(self):
        for text in (TOKEN + "\n", "TG_BOT_TOKEN=" + TOKEN + "\n"):
            shutil.rmtree(self.home / ".config", ignore_errors=True)
            result = self.run_setup("--non-interactive", "--token-file", str(self.token_file(text)),
                                    "--users", "1", "--publish=none")
            self.assert_success(result)
            self.assertIn("TG_BOT_TOKEN=" + TOKEN + "\n", self.env_file.read_text())

    def test_token_file_rejects_bad_mode_format_and_double_source(self):
        path = self.token_file(TOKEN + "\n", 0o644)
        args = ("--non-interactive", "--users", "1", "--publish=none")
        self.assertNotEqual(self.run_setup("--token-file", str(path), *args).returncode, 0)
        bad = self.token_file("bad token value\n")
        self.assertNotEqual(self.run_setup("--token-file", str(bad), *args).returncode, 0)
        self.assertNotEqual(self.run_setup("--token-file", str(self.token_file(TOKEN)), "--token", TOKEN, *args).returncode, 0)
        self.assertFalse(self.env_file.exists())

    def test_env_pairs_accepted_and_appended_after_token_and_users(self):
        result = self.install("none", "--env", "CC_SDK_X=1", "--env=TG_API_BASE=https://api.example/bot")
        self.assert_success(result)
        self.assertEqual(self.env_file.read_text(), "TG_BOT_TOKEN=" + TOKEN + "\nTG_ALLOWED_USERS=123,456\nCC_SDK_X=1\nTG_API_BASE=https://api.example/bot\n")
        self.assert_private(self.env_file)

    def test_env_pairs_rejected(self):
        for pair in ("FOO=1", "cc_x=1", "TG_BOT_TOKEN=x", "TG_ALLOWED_USERS=1", "CC_X", "CC_X=a\nTG_BOT_TOKEN=evil"):
            result = self.install("none", "--env", pair)
            self.assertNotEqual(result.returncode, 0, pair)
        self.assertEqual(self.snapshot_home(), {})

    def test_update_rewrites_reuses_venv_and_restarts(self):
        sdk_key = self.base / "sdk-key.env"
        sdk_key.write_text("ANTHROPIC_API_KEY=" + SDK_SECRET + "\n")
        sdk_key.chmod(0o600)
        first = ("none", "--with-sdk", "--sdk-key-file", str(sdk_key))
        self.assert_success(self.install(*first))
        self.assertNotEqual(self.install(*first).returncode, 0)  # refused without --update
        self.log.unlink()
        result = self.install(*first, "--update", "--env", "CC_NEW=2")
        self.assert_success(result)
        self.assertIn("CC_NEW=2", self.env_file.read_text())
        calls = self.calls()
        self.assertFalse(any(c[1:3] == ["-m", "venv"] for c in calls), calls)
        self.assertTrue(any(c[0] == "pip" and "-U" in c and "claude-agent-sdk" in c for c in calls), calls)
        self.assertTrue(any(c[0] == "systemctl" and "restart" in c and "zidos-pager-sdk" in c for c in calls), calls)

    def test_update_dry_run_changes_nothing(self):
        self.assert_success(self.install("none"))
        before = self.snapshot_home()
        self.assert_success(self.install("none", "--update", "--dry-run"))
        self.assertEqual(self.snapshot_home(), before)

    def test_xdg_runtime_dir_exported_for_systemctl(self):
        runtime = Path("/run/user/%d" % os.getuid())
        if not runtime.is_dir():
            self.skipTest("no /run/user/UID on this host")
        envlog = self.base / "env.log"
        self.env["SETUP_TEST_ENVLOG"] = str(envlog)
        self.assert_success(self.install("none"))
        seen = envlog.read_text().splitlines()
        self.assertTrue(seen and all(l.split("|")[0] == str(runtime) for l in seen), seen)
        if (runtime / "bus").is_socket():
            self.assertTrue(all(l.split("|")[1] == "unix:path=%s/bus" % runtime for l in seen), seen)


if __name__ == "__main__":
    unittest.main()
