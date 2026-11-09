#!/usr/bin/env python3
"""Zidos Pager install wizard: a local web page (127.0.0.1) that installs the bot on this machine or over ssh.

Secrets (bot token, API key) are held in memory, sent to the target only over stdin, never put in argv or logs.
"""
import argparse
import hmac
import io
import json
import locale
import mimetypes
import os
import platform
import re
import secrets
import shlex
import subprocess
import sys
import tarfile
import tempfile
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from zidos_pager import CLAUDE_LOGIN, LOGIN_ENGINES, MODEL_PRESETS, model_check  # noqa: E402  (stdlib-only module, no side effects)

WEB = ROOT / "assets" / "wizard"
FIXED = ["zidos_pager.py", "sdk_runner.py", "stt_runner.py", "LICENSE", "README*", "env.example", "deploy", "assets", "docs"]
CONF = "$HOME/.config/zidos-pager"
HOST_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@-]*")
LEGACY_EXISTING_CHECK = ("test -f ~/.config/systemd/user/zidos-pager.service || test -f ~/Library/LaunchAgents/dev.zidos.pager.bot.plist"
                         " || test -f ~/.config/systemd/user/cc-tgbot.service || test -f ~/Library/LaunchAgents/com.cctgbot.bot.plist")  # legacy
LAUNCHD = "dev.zidos.pager.bot"

# One python3 -c one-liner run on the target: line 1 of stdin = token, line 2 = JSON params.
TG_CODE = (
    "import sys,json,urllib.request as u,urllib.error as e\n"
    "t=sys.stdin.readline().strip();p=json.loads(sys.stdin.readline())\n"
    "r=u.Request(sys.argv[1]+'/bot'+t+'/'+sys.argv[2],json.dumps(p).encode(),{'Content-Type':'application/json'})\n"
    "try:print(u.urlopen(r,timeout=int(p.get('timeout',0))+20).read().decode())\n"
    "except e.HTTPError as x:print(json.dumps({'ok':False,'error_code':x.code}))\n"
    "except Exception:print(json.dumps({'ok':False,'error_code':0}))\n"
)

PROBE = r"""
echo os=$(uname -s)
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"  # a Mac: Homebrew is often missing from a non-login PATH
best=
for p in /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do  # the same order as the macOS installer
  v=$($p -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null) || continue
  [ -z "$best" ] && best=$v
  case $v in 3.[0-9]) ;; *) best=$v; break ;; esac
done
echo py=$best
for c in bwrap socat python3 brew; do command -v $c >/dev/null 2>&1 || echo no=$c; done
python3 -c 'import ensurepip' 2>/dev/null || echo no=venv  # Debian/Ubuntu: python3-venv is a separate package
PATH="$HOME/.local/bin:$PATH" claude auth status </dev/null 2>/dev/null | grep -q '"loggedIn": *true' && echo claude=1  # Claude Code signed in: no key needed
test -d /run/systemd/system && echo systemd=1
{ [ "$(id -u)" = 0 ] || sudo -n true 2>/dev/null; } && echo sudo=1
{ test -f ~/.config/systemd/user/zidos-pager.service || test -f ~/Library/LaunchAgents/dev.zidos.pager.bot.plist || test -f ~/.config/systemd/user/cc-tgbot.service || test -f ~/Library/LaunchAgents/com.cctgbot.bot.plist; } && echo existing=1  # legacy names too
true
"""


WSL_FIX = "wsl --install -d Ubuntu"
SYSTEMD_FIX = """sudo sh -c 'printf "[boot]\\nsystemd=true\\n" >> /etc/wsl.conf'"""


def wsl_distro():
    """Default WSL distro name from `wsl.exe -l -q` (UTF-16LE output), or None."""
    try:
        p = subprocess.run(["wsl.exe", "-l", "-q"], capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    txt = p.stdout.decode("utf-16-le", errors="replace").replace("\x00", "").replace("\ufeff", "")
    names = [x.strip() for x in txt.splitlines() if x.strip() and not x.strip().startswith("docker-desktop")]
    return names[0] if p.returncode == 0 and names else None


class Target:
    """Runs shell commands on this machine (local), in WSL2 (Windows local) or through system ssh with a shared control connection."""

    def __init__(self, mode, host="", user="", port=22, distro=""):
        self.mode, self.host, self.user, self.port, self.distro = mode, host, user, int(port), distro
        self.win = platform.system() == "Windows"
        self.os = platform.system() if mode == "local" else "Windows" if mode == "wsl" else ""
        self.dir = tempfile.mkdtemp(prefix="ccw")  # mode 0700
        self.master = None
        if mode == "ssh":
            self.dest = f"{user}@{host}" if user else host
            ctl = [] if self.win else ["-o", f"ControlPath={self.dir}/%C"]  # Windows OpenSSH has no ControlMaster
            self.opts = [*ctl, "-o", "StrictHostKeyChecking=accept-new", "-p", str(self.port)]

    def _ssh(self, extra, cmd):
        mux = [] if self.win else ["-o", "ControlMaster=auto", "-o", "ControlPersist=600"]
        return ["ssh", *extra, *self.opts, *mux, self.dest, cmd]

    def run(self, cmd, stdin=None, timeout=120):
        if self.mode == "wsl":
            argv = ["wsl.exe", "-d", self.distro, "--", "bash", "-lc", cmd]
        else:
            argv = ["bash", "-c", cmd] if self.mode == "local" else self._ssh([], cmd)
        try:
            p = subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, "timeout"
        out = p.stdout.decode(errors="replace")
        return p.returncode, out or (p.stderr.decode(errors="replace") if p.returncode else "")

    def connect(self):
        """ssh: True when a key/master connection works; else start the interactive master in this terminal."""
        if self.mode in ("local", "wsl"):
            return True
        if subprocess.run(self._ssh(["-o", "BatchMode=yes"], "true"), capture_output=True).returncode == 0:
            return True
        if self.win:
            return False
        if self.master is None or self.master.poll() is not None:
            print(f"\nssh {self.dest}: enter the password / confirm the host here if asked.", flush=True)
            self.master = subprocess.Popen(["ssh", "-N", "-o", "ControlMaster=yes", *self.opts, self.dest])
        return False

    def close(self):
        if self.master and self.master.poll() is None:
            self.master.terminate()


def tar_files(root, names, *, git=False):
    """tar.gz bytes of the given relative names (directories recursed, caches skipped)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for n in names:
            p = root / n
            for f in ([p] if p.is_file() else sorted(p.rglob("*")) if p.is_dir() else []):
                if f.is_file() and "__pycache__" not in f.parts and ".git" not in f.relative_to(root).parts:
                    tf.add(f, arcname=f.relative_to(root).as_posix())
    return buf.getvalue()


def code_tar():
    names = None
    if (ROOT / ".git").exists():
        r = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            names = r.stdout.splitlines()
    if names is None:
        names = [str(p.relative_to(ROOT)) for pat in FIXED for p in ROOT.glob(pat)]
    return tar_files(ROOT, names)


def now_lang():
    loc = (locale.getlocale()[0] or os.environ.get("LANG", "") or "")
    return "ru" if loc.lower().startswith("ru") else "en"


class Wizard:
    def __init__(self):
        self.target = None
        self.info = {}
        self.token = self.bot = self.owner = self.model = None
        self.groups = {}
        self.offset = None
        self.lock = threading.Lock()
        self.job = {"lines": [], "done": False, "ok": False, "error": ""}
        self.api_base = os.environ.get("TG_API_BASE", "https://api.telegram.org").rstrip("/")

    # --- telegram on the target ---
    def tg(self, method, params=None, timeout=60):
        cmd = f"python3 -c {shlex.quote(TG_CODE)} {shlex.quote(self.api_base)} {method}"
        rc, out = self.target.run(cmd, f"{self.token}\n{json.dumps(params or {})}\n".encode(), timeout)
        try:
            return json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"ok": False, "error_code": 0}

    def need(self, *what):
        if self.target is None:
            return {"ok": False, "error": "target"}
        if "bot" in what and not self.token:
            return {"ok": False, "error": "bot"}
        return None

    # --- api ---
    def state(self, _=None):
        no_codex = self.info.get("os") in ("Darwin", "Windows")
        pres = [{"id": k, "title": v["title"], "model": v["model"], "available": not (no_codex and v["base"] is None)}
                for k, v in MODEL_PRESETS.items()]
        # Claude Code's own sign-in on the target: offered once the probe has seen it (or before any probe)
        pres.insert(0, dict(CLAUDE_LOGIN, login=True, available=self.info.get("claude", True) if self.info else True))
        return {"lang": now_lang(), "presets": pres,
                "target": self.info or None, "bot": self.bot, "owner": self.owner,
                "groups": [{"id": k, "title": v} for k, v in self.groups.items()],
                "installed": self.job["done"] and self.job["ok"]}

    def set_target(self, b):
        mode = b.get("mode")
        if mode == "local":
            sysname = platform.system()
            if sysname == "Windows":
                d = wsl_distro()
                if not d:
                    return {"ok": False, "error": "wsl", "fix": WSL_FIX,
                            "hint": "install WSL2 with this command in an administrator terminal, reboot Windows, then connect again"}
                t = Target("wsl", distro=d)
            elif sysname not in ("Linux", "Darwin"):
                return {"ok": False, "error": "unsupported"}
            else:
                t = Target("local")
        elif mode == "ssh":
            host, user, port = str(b.get("host", "")), str(b.get("user", "")), str(b.get("port") or 22)
            if not HOST_RE.fullmatch(host) or (user and not HOST_RE.fullmatch(user)) or not port.isdigit():
                return {"ok": False, "error": "host"}
            t = Target("ssh", host, user, port)
        else:
            return {"ok": False, "error": "mode"}
        if not t.connect():
            hint = ("set up an SSH key: ssh-keygen, then ssh-copy-id or add the key on the server" if t.win
                    else "enter the password in the terminal")
            return {"ok": False, "error": "auth", "hint": hint}
        rc, out = t.run(PROBE, timeout=60)
        kv = {}
        for line in out.splitlines():
            k, _, v = line.partition("=")
            kv.setdefault(k, []).append(v)
        osn = t.os or (kv.get("os") or [""])[0]
        t.os = osn
        hint = ""
        py = (kv.get("py") or [""])[0]
        no = set(kv.get("no", []))
        okpy = bool(re.fullmatch(r"\d+\.\d+", py)) and tuple(map(int, py.split("."))) >= (3, 10)
        dar = osn == "Darwin"
        wsl_nosd = t.mode == "wsl" and "systemd" not in kv
        missing = [] if okpy and "python3" not in no else ["python3>=3.10"]
        if not dar:
            missing += [c for c in ("bwrap", "socat", "venv") if c in no]
        fix = ""
        if missing and dar:
            # the Command Line Tools bring Python 3.9 only: without Homebrew the python.org installer is the way
            fix = "https://www.python.org/downloads/macos/" if "brew" in no else "brew install python@3.12"
        elif missing:
            pk = {"bwrap": "bubblewrap", "socat": "socat", "venv": "python3-venv", "python3>=3.10": "python3"}
            fix = "sudo apt-get install -y " + " ".join(pk[m] for m in missing)
        if wsl_nosd:
            missing.append("systemd")
            fix = (fix + "\n" if fix else "") + SYSTEMD_FIX
            hint = "then run `wsl --shutdown` in Windows and connect again"
        res = {"ok": True, "os": osn, "python": py, "missing": missing, "systemd": dar or "systemd" in kv,
               "existing": "existing" in kv, "sudo": "sudo" in kv, "fix": fix, "hint": hint, "error": ""}
        if missing:
            t.close()
            return {**res, "ok": False, "error": "missing"}
        self.target, self.info = t, {"mode": mode, "os": osn, "claude": "claude" in kv}
        return res

    def set_bot(self, b):
        if (e := self.need()):
            return e
        tok = str(b.get("token", "")).strip()
        if not re.fullmatch(r"\d+:[\w-]{20,}", tok):
            return {"ok": False, "error": "token"}
        self.token = tok
        r = self.tg("getMe")
        if not r.get("ok"):
            self.token = None
            return {"ok": False, "error": "token" if r.get("error_code") in (401, 404) else "net"}
        if self.tg("getUpdates", {"timeout": 0, "limit": 1}).get("error_code") == 409:
            self.token = None
            return {"ok": False, "error": "busy"}
        self.bot = {"username": r["result"].get("username", ""), "name": r["result"].get("first_name", "")}
        return {"ok": True, **self.bot}

    def poll(self, timeout):
        """One getUpdates; remembers groups; returns the updates and acknowledges them."""
        p = {"timeout": timeout, "allowed_updates": ["message", "my_chat_member"]}
        if self.offset is not None:
            p["offset"] = self.offset
        r = self.tg("getUpdates", p, timeout + 30)
        ups = r.get("result") or []
        for u in ups:
            self.offset = max(self.offset or 0, u["update_id"] + 1)
            m = u.get("message") or u.get("my_chat_member") or {}
            chats = [m.get("chat") or {}]
            for c in chats:
                if c.get("type") in ("group", "supergroup"):
                    self.groups[c["id"]] = c.get("title", str(c["id"]))
        if ups:
            self.tg("getUpdates", {"timeout": 0, "limit": 1, "offset": self.offset})
        return ups

    def get_owner(self, _):
        if (e := self.need("bot")):
            return e
        if self.owner:
            return {"ok": True, "owner": self.owner}
        ups = self.poll(25)
        for u in ups:
            m = u.get("message") or {}
            if (m.get("chat") or {}).get("type") == "private" and m.get("from"):
                f = m["from"]
                self.owner = {"id": f["id"], "name": f.get("first_name") or f.get("username") or str(f["id"])}
                return {"ok": True, "owner": self.owner}
        return {"ok": False, "waiting": True}

    def set_model(self, b):
        prov = b.get("provider")
        if prov == CLAUDE_LOGIN["id"]:  # no key: the installer checks `claude auth status` on the target
            model = str(b.get("model") or CLAUDE_LOGIN["model"]).strip()
            if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,120}", model):
                return {"ok": False, "error": "model"}
            if self.info and not self.info.get("claude"):
                return {"ok": False, "error": "claude_login"}
            self.model = {"provider": prov, "model": model, "login": True}
            return {"ok": True}
        if prov not in MODEL_PRESETS:
            return {"ok": False, "error": "provider"}
        pre = MODEL_PRESETS[prov]
        if pre["base"] is None:
            self.model = {"provider": prov}
            return {"ok": True, "note": "codex login on the server"}
        base = str(b.get("base") or pre["base"]).strip()
        model = str(b.get("model") or pre["model"]).strip()
        key = str(b.get("key", "")).strip()
        if not key or not model:
            return {"ok": False, "error": "key"}
        res = model_check(base, key, model)
        if res != "ok":
            return {"ok": False, "error": res}
        self.model = {"provider": prov, "base": base, "model": model, "key": key, "var": pre["var"] or "CUSTOM_API_KEY"}
        return {"ok": True}

    def get_groups(self, _):
        if (e := self.need("bot")):
            return e
        self.poll(0)
        return {"groups": [{"id": k, "title": v} for k, v in self.groups.items()]}

    def start_install(self, b):
        if (e := self.need("bot")):
            return e
        if not self.owner or not self.model:
            return {"ok": False, "error": "incomplete"}
        if self.job["lines"] and not self.job["done"]:
            return {"ok": False, "error": "running"}
        self.job = {"lines": [], "done": False, "ok": False, "error": ""}
        threading.Thread(target=self._install, args=(dict(b), self.job), daemon=True).start()
        return {"ok": True, "job": 1}

    def bots(self, sub, body=None):
        """Extra bots: one CLI call on the target; JSON goes over stdin only (token/key never reach argv or the install log)."""
        if (e := self.need()):
            return e
        if not (self.job["done"] and self.job["ok"]):
            return {"ok": False, "error": "notinstalled"}
        # same PATH as PROBE: a Mac's Homebrew python3 first
        cmd = f'export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH" XDG_RUNTIME_DIR="${{XDG_RUNTIME_DIR:-/run/user/$(id -u)}}"; cd ~/zidos-pager && python3 zidos_pager.py bots {sub}'
        stdin = None if body is None else json.dumps(body).encode()
        rc, out = self.target.run(cmd, stdin, 180)
        try:
            r = json.loads(out.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"ok": False, "error": "net" if rc == 124 else "cli"}  # stderr is not echoed: it may hold secrets
        return r if isinstance(r, dict) else {"ok": False, "error": "cli"}

    def install_log(self, q):
        n = int((q.get("from") or ["0"])[0] or 0)
        j = self.job
        return {"lines": j["lines"][n:], "done": j["done"], "ok": j["ok"], "error": j["error"]}

    # --- install job ---
    def _install(self, b, job):
        t, m = self.target, self.model
        secrets_ = [self.token, m.get("key")]

        def log(s):
            for x in secrets_:
                if x:
                    s = s.replace(x, "***")
            job["lines"].append(s)

        def step(title, cmd, stdin=None, timeout=300):
            log(title)
            rc, out = t.run(cmd, stdin, timeout)
            for line in out.strip().splitlines()[-30:]:
                log("  " + line)
            if rc != 0:
                raise RuntimeError(f"{title}: exit {rc}")

        def put(path, data):
            return f"umask 077; mkdir -p {CONF}; cat > {path}", data.encode()

        try:
            dar = t.os == "Darwin"
            existing = t.run(LEGACY_EXISTING_CHECK)[0] == 0
            step("upload code", "mkdir -p ~/zidos-pager && tar -xzf - -C ~/zidos-pager", code_tar())
            tokf, keyf, briefp = f'"{CONF}/token.env"', f'"{CONF}/sdk-key.env"', f'"{CONF}/brief.md"'
            step("write token", *put(tokf, f"TG_BOT_TOKEN={self.token}\n"))
            if "key" in m:
                step("write key", *put(keyf, f"{m['var']}={m['key']}\n"))
            brief = str(b.get("brief") or "").strip()
            if brief:
                step("write brief", *put(briefp, brief + "\n"))
            folder = str(b.get("folder") or "").strip()
            if folder:
                fp = Path(folder).expanduser()
                if not fp.is_dir():
                    raise RuntimeError("folder is not a directory")
                step("upload folder", "mkdir -p ~/zidos-pager-knowledge && tar -xzf - -C ~/zidos-pager-knowledge",
                     tar_files(fp, [p.name for p in fp.iterdir()]))
            script = os.environ.get("WIZARD_SETUP_SH") or ("deploy/setup-macos.sh" if dar else "deploy/setup.sh")
            args = ["--non-interactive", "--token-file", tokf, "--users", shlex.quote(str(self.owner["id"]))]
            if not dar:
                args += ["--publish", "none"]  # the macOS installer has no publishing step
            if "key" in m:
                args += ["--with-sdk", "--sdk-key-file", keyf, "--sdk-key-var", shlex.quote(m["var"]),
                         "--sdk-model", shlex.quote(m["model"])]
                if m["base"]:  # empty: Anthropic itself
                    args += ["--sdk-base-url", shlex.quote(m["base"])]
            elif m.get("login"):
                args += ["--with-sdk", "--sdk-login", "--sdk-model", shlex.quote(m["model"])]
            q = lambda k, v: shlex.quote(f"{k}={v}")  # noqa: E731
            envs = [q("CC_LANG", b.get("lang") or "en")]
            if b.get("group_id"):
                envs.append(q("CC_RESPONDER_CHAT", b["group_id"]))
            if b.get("group_id"):  # no folder given: an empty one, or the group assistant stays off
                step("knowledge folder", "mkdir -p ~/zidos-pager-knowledge")
                envs.append('"CC_RESPONDER_CWD=$HOME/zidos-pager-knowledge"')
            if b.get("name"):
                envs.append(q("CC_RESPONDER_NAME", b["name"]))
            if brief:
                envs.append('"CC_RESPONDER_BRIEF=$HOME/.config/zidos-pager/brief.md"')
            if m.get("provider") in LOGIN_ENGINES:
                envs.append("CC_RESPONDER_ENGINE=" + m["provider"])
            if os.environ.get("TG_API_BASE"):  # a test Telegram: the installed bot must use it too
                envs.append(q("TG_API_BASE", self.api_base))
            for e in envs:
                args += ["--env", e]
            if existing:
                args.append("--update")
            step("run setup", f"cd ~/zidos-pager && bash {script} " + " ".join(args), timeout=1800)
            if dar:
                chk = f"launchctl print gui/$(id -u)/{LAUNCHD} >/dev/null"
            else:
                chk = "export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}; systemctl --user is-active zidos-pager"
            step("check service", chk, timeout=60)
            log("send test message")
            ru = b.get("lang") == "ru"
            r = self.tg("sendMessage", {"chat_id": self.owner["id"],
                                        "text": "Zidos Pager установлен и работает." if ru else "Zidos Pager is installed and running."})
            if not r.get("ok"):
                raise RuntimeError("test message failed")
            if t.mode == "wsl":
                self._wsl_keepalive(t, log)
            job["ok"] = True
        except Exception as e:  # noqa: BLE001
            job["error"] = str(e)
            log("error: " + str(e))
        job["done"] = True


def _wsl_keepalive(self, t, log):
    """Keep WSL alive without an open terminal: hidden `sleep infinity` started at logon by a scheduled task."""
    d = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "zidos-pager"
    vbs = d / "wsl-keepalive.vbs"
    log("keep WSL running: " + str(vbs))
    d.mkdir(parents=True, exist_ok=True)
    vbs.write_text('CreateObject("WScript.Shell").Run "wsl.exe -d %s --exec sleep infinity", 0, False\r\n' % t.distro)
    for title, args in (("schtasks create", ["/Create", "/TN", "zidos-pager-wsl", "/TR", f'wscript.exe "{vbs}"',
                                             "/SC", "ONLOGON", "/RL", "LIMITED", "/F"]),
                        ("schtasks run", ["/Run", "/TN", "zidos-pager-wsl"])):
        log(title)
        p = subprocess.run(["schtasks", *args], capture_output=True, timeout=60)
        if p.returncode:
            raise RuntimeError(f"{title}: exit {p.returncode}")
    log("undo: schtasks /Delete /TN zidos-pager-wsl /F")


Wizard._wsl_keepalive = _wsl_keepalive


class Handler(BaseHTTPRequestHandler):
    server_version = "wizard"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _guard(self):
        if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            self._send(403, {"error": "host"})
            return None
        u = urlparse(self.path)
        if u.path.startswith("/api/") and not hmac.compare_digest(self.headers.get("X-Wizard-Token", ""), self.server.token):
            self._send(403, {"error": "token"})
            return None
        return u

    def do_GET(self):
        u = self._guard()
        if not u:
            return
        w = self.server.wizard
        if u.path == "/api/state":
            return self._send(200, w.state())
        if u.path == "/api/bots":
            return self._send(200, w.bots("list"))
        if u.path == "/api/install/log":
            return self._send(200, w.install_log(parse_qs(u.query)))
        if u.path.startswith("/api/"):
            return self._send(404, {"error": "nf"})
        f = (WEB / ("index.html" if u.path == "/" else u.path.lstrip("/"))).resolve()
        if WEB.resolve() in f.parents and f.is_file():
            return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "application/octet-stream")
        self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def do_POST(self):
        u = self._guard()
        if not u:
            return
        route = {"/api/target": "set_target", "/api/bot": "set_bot", "/api/owner": "get_owner", "/api/model": "set_model",
                 "/api/groups": "get_groups", "/api/install": "start_install"}.get(u.path)
        bsub = u.path[len("/api/bots/"):] if u.path.startswith("/api/bots/") else ""
        if bsub in ("add", "save", "persona", "remove"):
            route = "bots"
        if not route:
            return self._send(404, {"error": "nf"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._send(400, {"error": "json"})
        try:
            w = self.server.wizard
            self._send(200, w.bots(bsub, body) if route == "bots" else getattr(w, route)(body))
        except Exception:  # noqa: BLE001  (never echo internals: they may hold secrets)
            self._send(500, {"ok": False, "error": "internal"})


def make_server(port=0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.token, srv.wizard = secrets.token_urlsafe(24), Wizard()
    return srv


def main():
    ap = argparse.ArgumentParser(description="Zidos Pager install wizard")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=0)
    a = ap.parse_args()
    srv = make_server(a.port)
    url = f"http://127.0.0.1:{srv.server_port}/?t={srv.token}"
    print("Zidos Pager wizard:", url, flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if srv.wizard.target:
            srv.wizard.target.close()


if __name__ == "__main__":
    main()
