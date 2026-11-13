"""Demo API on synthetic data (demo2.json) + the real mini-app static files."""
import json, os, http.server, urllib.parse, mimetypes
from copy import deepcopy
from http.cookies import SimpleCookie
import threading
import time
DATA = os.environ.get("DATA_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "stand", "demo2.json"))
R = json.load(open(DATA))
DATA_LOCK = threading.Lock()
NEW = []  # sessions launched through POST /api/new
# a session with 60 entries opened by id (#s=LONG_SID); the server window is the last 24, `history/?before=` pages back
LONG_SID = "e5a7c9b1"
LONG = [{"role": "assistant", "uuid": "L%d" % i, "ts": "2026-11-12T20:%02d:00Z" % i, "text": "Entry %d: " % i + "a long line of the feed, " * 5} for i in range(60)]


# lists of the model sheet: the shape of the real api_model (presets with models; empty list = one preset model)
MODEL = {"provider": "deepseek", "model": "deepseek-flash", "base": "", "busy": 0, "group": {"on": False}, "presets": [
    {"id": "claude_login", "title": "Claude Code", "model": "sonnet", "stored": False, "login": True, "models": ["fable", "opus", "sonnet", "haiku", "claude-fable-5-1"], "labels": {"fable": "Fable 5.2", "opus": "Opus 5.5", "sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5", "claude-fable-5-1": "Fable 5.1"}},
    {"id": "deepseek", "title": "DeepSeek", "model": "deepseek-flash", "stored": True, "models": []},
    {"id": "glm", "title": "GLM", "model": "glm-5.3", "stored": True, "models": []},
    {"id": "kimi", "title": "Kimi", "model": "kimi-for-coding", "stored": False, "models": []},
    {"id": "openrouter", "title": "OpenRouter", "model": "x", "stored": False, "models": []},
    {"id": "anthropic", "title": "Claude API", "model": "sonnet", "stored": False, "models": ["fable", "opus", "sonnet", "haiku", "claude-fable-5-1"], "labels": {"fable": "Fable 5.2", "opus": "Opus 5.5", "sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5", "claude-fable-5-1": "Fable 5.1"}},
    {"id": "custom", "title": "", "model": "", "stored": False, "models": []}]}

# the shape of the real api_status: Claude from the demo quota + rows of CC_STATUS_FILE
PINS = []  # строки квот, вынесенные в шапку (сервер держит их рядом с env)
def quota_pinned():
    return [r for r in quota_status(False)["rows"] if r["id"] in PINS and r["pct"] is not None]

FILE_AGE = [float(os.environ.get("QUOTA_FILE_AGE", "240"))]  # age of the status file; a refresh rewrites it
def quota_status(refreshed):
    now = time.time()
    if refreshed: FILE_AGE[0] = 240.0
    return {"claude": dict(R["sessions"]["quota"], updated=now - 30), "can_refresh": True, "refreshed": refreshed, "updated": now - FILE_AGE[0], "min_interval": 300, "pins": list(PINS),
            "next_at": now + 60 if refreshed else None, "rows": [
                {"id": "codex", "title": "Codex", "sub": "week", "pct": 41.0, "reset": "Thu 09:00", "hot": False},
                {"id": "glm", "title": "GLM", "sub": "5 h", "pct": 88.0, "reset": "23:10", "hot": True},
                {"id": "gemini", "title": "Gemini", "sub": "day", "pct": 12.0, "reset": None, "hot": False},
                {"id": "mem", "title": "Shared memory", "sub": "12 480 notes · indexed 2 min ago", "pct": None, "reset": None, "hot": False}]}

def read_data():
    with open(DATA) as source:
        return json.load(source)


def write_data(data):
    # Атомарная замена не даёт фоновому опросу прочитать неполный JSON.
    temporary = DATA + ".tmp"
    with open(temporary, "w") as target:
        json.dump(data, target, ensure_ascii=False)
    os.replace(temporary, DATA)
APP = os.environ.get("APP_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "assets", "webapp"))
THEMES = {
 "tg-light": ":root{--tg-theme-bg-color:#fff;--tg-theme-secondary-bg-color:#efeff4;--tg-theme-section-bg-color:#fff;--tg-theme-text-color:#000;--tg-theme-hint-color:#8e8e93;--tg-theme-button-color:#007aff;--tg-theme-button-text-color:#fff;--tg-theme-link-color:#007aff;--tg-theme-destructive-text-color:#ff3b30}",
 "tg-dark": ":root{--tg-theme-bg-color:#1c1c1d;--tg-theme-secondary-bg-color:#000;--tg-theme-section-bg-color:#1c1c1d;--tg-theme-text-color:#fff;--tg-theme-hint-color:#98989e;--tg-theme-button-color:#3e88f7;--tg-theme-button-text-color:#fff;--tg-theme-link-color:#3e88f7;--tg-theme-destructive-text-color:#eb5545}",
}
BOTS = {"bots": [], "presets": []}


def bots_reset():
    """Synthetic /api/bots* state (README, "Several bots"): one active bot with a group, one without a group and with an API error."""
    BOTS["presets"] = [{"id": "anthropic", "title": "Claude", "model": "claude-sonnet-4-5", "models": ["fable", "opus", "sonnet", "haiku", "claude-fable-5-1"], "labels": {"fable": "Fable 5.2", "opus": "Opus 5.5", "sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5", "claude-fable-5-1": "Fable 5.1"}}, {"id": "kimi", "title": "Kimi", "model": "kimi-k2"},
                       {"id": "codex", "title": "Codex", "model": ""},
                       {"id": "grok", "title": "Grok", "model": ""}, {"id": "gemini", "title": "Gemini", "model": ""}, {"id": "custom", "title": "", "model": ""}]
    BOTS["bots"] = [
        {"slug": "shop_helper_bot", "username": "shop_helper_bot", "name": "Shop Helper", "chat": -1001, "chat_title": "Shop team", "groups": [{"id": -1001, "title": "Shop team"}, {"id": -1002, "title": "Support"}],
         "cwd": "/srv/shop", "git": True, "site": "https://shop.example", "provider": "anthropic", "model": "claude-sonnet-4-5", "engine": "sdk", "about": "An online shop for bikes.",
         "brief": "You help the shop team answer customers.", "persona": {"name": "Shop Helper", "short": "Answers about the shop", "description": "Ask me about orders."}, "keys": ["anthropic"], "base": "", "active": True, "error": "",
         "perms": {"mode": "ask", "allow": ["Read", "Grep"], "deny": ["Bash(git push:*)"]}},
        {"slug": "docs_guide_bot", "username": "docs_guide_bot", "name": "Docs Guide", "chat": None, "chat_title": "", "groups": [],
         "cwd": "", "git": False, "site": "", "provider": "kimi", "model": "kimi-k2", "engine": "sdk", "about": "", "brief": "", "persona": {}, "keys": [], "base": "", "active": False, "error": "401 invalid api key",
         "perms": {"mode": "ask", "allow": [], "deny": []}}]


PERMS = {}


def perms_reset():
    """Synthetic /api/perms (README «Permissions»): own sessions with rules, the group bot asks."""
    PERMS.clear()
    PERMS.update({"sessions": {"mode": "edits", "allow": ["Bash(git status:*)", "Bash(npm test:*)", "WebFetch(domain:docs.python.org)"], "deny": ["Bash(rm -rf:*)"]},
                  "chat": {"mode": "ask", "allow": ["Read"], "deny": []}, "group_on": True})


def perm_rule_ok(r):
    # as sdk_runner.perm_rule_parse: Tool or Tool(arg), the tool name is a word
    import re
    return bool(re.fullmatch(r"[A-Za-z_][\w.:-]*(\(.+\))?", r))


bots_reset()
perms_reset()


class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def static(self, head=False):
        """Identical GET/HEAD lengths; app_version cookie simulates a deployment.

        Cookies, rather than request interception, also cover WebKit background
        fetches. The error cookie is restricted to HEAD so the app stays usable.
        """
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        name = u.path[len("/app"):].strip("/") or "index.html"
        f = os.path.join(APP, name)
        if not os.path.isfile(f):
            self.send_response(404); self.end_headers(); return
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        failure = cookie["app_head_error"].value if "app_head_error" in cookie else ""
        if head and failure:
            if failure == "network":
                self.close_connection = True
                return
            self.send_response(503); self.end_headers(); return
        with open(f, "rb") as source:
            b = source.read()
        if name == "index.html":
            version = cookie["app_version"].value if "app_version" in cookie else os.environ.get("APP_VERSION", "")
            if version:
                b += ("<!-- synthetic deployment " + "x" * len(version[:128]) + " -->").encode()
            if q.get("theme", [""])[0] in THEMES:
                b = b.replace(b"</head>", ("<style>%s</style></head>" % THEMES[q["theme"][0]]).encode())
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(f)[0] or "text/html") + ("; charset=utf-8" if f.endswith(".html") else ""))
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head:
            self.wfile.write(b)
    def do_HEAD(self):
        # как сервер бота (zidos_pager.py — только do_GET): HEAD → 501
        self.send_response(501); self.end_headers()
    def j(self, o, code=200):
        b = json.dumps(o, ensure_ascii=False).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(b)
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        parts = urllib.parse.urlparse(self.path).path.strip("/").split("/")
        if parts == ["api", "perms"]:
            return self.perms_post(raw)
        if parts == ["api", "quota", "refresh"]:
            return self.j(quota_status(True))
        if parts == ["api", "quota", "pins"]:
            o = json.loads(raw or b"{}"); i = o.get("id")
            if i in PINS: PINS.remove(i)
            if o.get("on"): PINS.append(i)
            return self.j(quota_status(False))
        if len(parts) == 3 and parts[:2] == ["api", "bots"]:
            return self.bots_post(parts[2], raw)
        # Server-side receipts also cover WebKit's unobservable background fetches.
        if len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] == "send" and os.environ.get("SEND_LOG"):
            with DATA_LOCK, open(os.environ["SEND_LOG"], "a") as log:
                log.write(json.dumps({"sid": parts[2], "body": raw.decode("utf-8", errors="replace")}) + "\n")
        if parts == ["api", "new"]:
            ctype = self.headers.get("Content-Type", "").lower()
            if ctype.startswith("multipart/"):
                from email.parser import BytesParser
                from email.policy import HTTP
                msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + raw)
                fields = {}
                filenames = []
                for part in msg.iter_parts():
                    name = part.get_param("name", header="content-disposition")
                    if name == "file":
                        fn = part.get_filename()
                        if fn:
                            filenames.append(fn)
                    elif name:
                        fields[name] = part.get_content().strip()
                new_data = {"text": fields.get("text", ""), "cwd": fields.get("cwd", ""), "files": filenames}
            else:
                try:
                    new_data = json.loads(raw or b"{}")
                except Exception:
                    new_data = {}
            if os.environ.get("SEND_LOG"):
                with DATA_LOCK, open(os.environ["SEND_LOG"], "a") as log:
                    log.write(json.dumps({"new": new_data}) + "\n")
            # as the real api_new: {ok, id}; the new session is in the list and answers session/<id>
            with DATA_LOCK:  # in memory only: the shared fixture file stays clean for other checks
                sid = "n%07x" % (len(NEW) + 0x1000)
                name = (new_data.get("text") or "New session")[:40]
                NEW.insert(0, {"id": sid, "name": name, "base_name": name, "k": 1, "group": "work", "state": "work", "failed": False, "detail": "Working", "ago": "now",
                               "updated": int(time.time()), "topic": True, "watched": False, "current": False, "archived_at": None, "into": None, "ask": None, "kind": "claude", "cwd": new_data.get("cwd") or "~"})
            return self.j({"ok": True, "menu": False, "id": sid})
        if len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] == "key":  # как сервер: клавиша в терминал, в ответе экран
            key = json.loads(raw or b"{}").get("key")
            if key not in ("1", "2", "3", "4", "5", "up", "down", "tab", "enter", "esc", "y", "n"):
                return self.j({"ok": False, "error": "Key not allowed"}, 400)
            if os.environ.get("KEY_LOG"):
                with DATA_LOCK, open(os.environ["KEY_LOG"], "a") as log:
                    log.write(json.dumps({"sid": parts[2], "key": key}) + "\n")
            return self.j({"ok": True, "menu": False, "screen": None})
        if len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] == "answer":
            return self.answer(parts[2], raw)
        if len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] == "send":  # сервер отвечает после вставки в tmux (1–2 с)
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            time.sleep(min(5, max(0, float(cookie["send_delay"].value)))) if "send_delay" in cookie else None
            if "send_reply" in cookie and cookie["send_reply"].value == "fail":
                return self.j({"ok": False, "code": "error", "error": "Synthetic send failure"})
            with DATA_LOCK:  # как настоящий сервер: отправленное появляется в истории сессии
                current = read_data()
                hist = current["session"].get(parts[2], {}).setdefault("history", [])
                hist.append({"role": "user", "uuid": "sent-%d" % len(hist), "text": json.loads(raw or b"{}").get("text", "")})
                write_data(current)
        if len(parts) == 4 and parts[:2] == ["api", "session"] and parts[3] == "upload":  # как сервер: multipart file×N + caption → одно сообщение
            from email.parser import BytesParser
            from email.policy import HTTP
            msg = BytesParser(policy=HTTP).parsebytes(b"Content-Type: " + self.headers.get("Content-Type", "").encode() + b"\r\n\r\n" + raw)
            files, caption = [], None
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if name == "file":
                    files.append({"name": part.get_filename(), "size": len(part.get_payload(decode=True) or b"")})
                elif name == "caption":
                    caption = part.get_content().strip()
            if os.environ.get("UPLOAD_LOG"):
                with DATA_LOCK, open(os.environ["UPLOAD_LOG"], "a") as log:
                    log.write(json.dumps({"sid": parts[2], "caption": caption, "files": files}, ensure_ascii=False) + "\n")
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if "upload_reply" in cookie and cookie["upload_reply"].value == "fail":
                return self.j({"ok": False, "error": "Synthetic upload failure"})
            return self.j({"ok": True, "saved": [f["name"] for f in files]})
        if parts == ["api", "voice"]:  # как сервер: тело — запись audio/*, ответ {ok, text} или {ok:false, code, error}
            ctype = self.headers.get("Content-Type", "")
            if os.environ.get("VOICE_LOG"):
                with DATA_LOCK, open(os.environ["VOICE_LOG"], "a") as log:
                    log.write(json.dumps({"type": ctype, "size": len(raw)}) + "\n")
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if "voice_reply" in cookie and cookie["voice_reply"].value == "fail":
                return self.j({"ok": False, "code": "unavailable", "error": "Speech recognition is unavailable"})
            if not ctype.startswith("audio/") or not raw:
                return self.j({"ok": False, "code": "error", "error": "Unsupported audio type"}, 415)
            return self.j({"ok": True, "text": "проверка голоса", "seconds": 1.0, "engine": "stand"})
        if len(parts) != 4 or parts[:2] != ["api", "session"] or parts[3] != "perm":
            return self.j({"ok": True, "menu": False})
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return self.j({"ok": False, "code": "bad"}, 400)
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        scenario = cookie["perm_reply"].value if "perm_reply" in cookie else os.environ.get("perm_reply", os.environ.get("PERM_REPLY", "null"))
        delay = cookie["perm_delay"].value if "perm_delay" in cookie else os.environ.get("PERM_DELAY", "0")
        sid = parts[2]
        with DATA_LOCK:
            generation = read_data().get("_test_case")
            if os.environ.get("PERM_LOG"):
                with open(os.environ["PERM_LOG"], "a") as log:
                    log.write(json.dumps({"sid": sid, "body": body, "reply": scenario}, ensure_ascii=False) + "\n")
        time.sleep(min(5, max(0, float(delay))))
        if scenario == "network":
            self.close_connection = True
            return
        with DATA_LOCK:
            data = read_data()
            if data.get("_test_case") != generation:
                return self.j({"ok": False, "code": "closed"})
            session = data["session"].get(sid)
            perm = session.get("perm") if session else None
            if not perm:
                return self.j({"ok": False, "code": "closed"})
            option = next((o for o in perm["options"] if o["n"] == body.get("option")), None) if isinstance(body, dict) else None
            if option is None and isinstance(body, dict) and body.get("option") == 4 and perm.get("everywhere"):  # as the server: 4 = «Everywhere»
                option = {"n": 4, "label": "everywhere"}
            valid = option and (body.get("req") == perm.get("req") if perm["kind"] == "sdk" else body.get("label") == option["label"])
            if not valid or scenario == "bad":
                return self.j({"ok": False, "code": "bad", "error": "Synthetic invalid permission answer"}, 400)
            if scenario in ("error", "stopped"):
                return self.j({"ok": False, "code": scenario, "error": "Synthetic permission " + scenario})
            if scenario not in ("next", "null", "closed", "stale", "already"):
                return self.j({"ok": False, "code": "error", "error": "Unknown permission scenario"})
            fresh = None
            if scenario in ("next", "stale"):
                fresh = deepcopy(session.get("_perm_" + scenario, perm))
                if "_perm_" + scenario not in session:
                    fresh["body"] = "Synthetic " + scenario + " permission body"
                    fresh["req"] = "r-" + scenario if perm["kind"] == "sdk" else None
            session["perm"] = fresh
            for row in data["sessions"]["sessions"]:
                if row["id"] == sid:
                    row["perm"] = bool(fresh)
            write_data(data)
        if scenario == "stale":
            return self.j({"ok": False, "code": "stale", "perm": fresh})
        if scenario == "closed":
            return self.j({"ok": False, "code": "closed"})
        if scenario == "already":
            return self.j({"ok": True, "already": True})
        return self.j({"ok": True, "perm": fresh})
    def perms_post(self, raw):
        body = json.loads(raw or b"{}")
        if body.get("reset"):
            perms_reset(); return self.j({"ok": True})
        time.sleep(0.15)
        tgt = body.get("target")
        if tgt not in ("sessions", "chat"): return self.j({"ok": False, "error": "target"})
        if body.get("mode") not in ("ask", "edits", "all") or (tgt == "chat" and body["mode"] == "all"): return self.j({"ok": False, "error": "mode"})
        clean = {"mode": body["mode"]}
        for lst in ("allow", "deny"):
            out = []
            for i, r in enumerate(body.get(lst) or []):
                r = str(r).strip()
                if r and not perm_rule_ok(r): return self.j({"ok": False, "error": "rule", "list": lst, "index": i})
                if r and r not in out: out.append(r)
            clean[lst] = out
        PERMS[tgt] = clean
        return self.j({"ok": True, "sessions": PERMS["sessions"], "chat": PERMS["chat"]})
    def bots_post(self, action, raw):
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            return self.j({"ok": False, "error": "net"}, 400)
        time.sleep(0.15)
        find = lambda: next((b for b in BOTS["bots"] if b["slug"] == body.get("slug")), None)
        if action == "reset":
            bots_reset(); return self.j({"ok": True})
        if action == "add":
            token = str(body.get("token", "")).strip()
            if ":" not in token: return self.j({"ok": False, "error": "token"})
            name = "new_helper_bot"
            if any(b["slug"] == name for b in BOTS["bots"]): return self.j({"ok": False, "error": "exists"})
            BOTS["bots"].append({"slug": name, "username": name, "name": "New Helper", "chat": None, "chat_title": "", "groups": [], "cwd": "", "git": False, "site": "", "provider": "anthropic", "model": "claude-sonnet-4-5",
                                 "engine": "sdk", "about": "", "brief": "", "persona": {}, "active": True, "error": ""})
            return self.j({"ok": True, "slug": name, "username": name, "name": "New Helper"})
        b = find()
        if not b: return self.j({"ok": False, "error": "missing"})
        if action == "remove":
            BOTS["bots"].remove(b); return self.j({"ok": True})
        if action == "persona":
            if b["provider"] == "codex": return self.j({"ok": False, "error": "persona_codex"})
            if "fail" in body.get("about", ""): return self.j({"ok": False, "error": "persona_fail"})
            return self.j({"ok": True, "brief": "You are the assistant of: " + body.get("about", ""), "persona": {"name": "Generated Helper", "short": "Helps in the group", "description": "Ask me anything about the project."}})
        if action == "save":
            if body.get("cwd") and not body["cwd"].startswith(("/", "~")): return self.j({"ok": False, "error": "cwd"})
            if body.get("site") and not body["site"].startswith("https://"): return self.j({"ok": False, "error": "site"})
            if body.get("model") == "bad-model": return self.j({"ok": False, "error": "model"})
            if body.get("key") == "bad-key": return self.j({"ok": False, "error": "key"})
            pm = body.get("perms")
            if pm is not None and (pm.get("mode") == "all" or not all(perm_rule_ok(r) for r in pm.get("allow", []) + pm.get("deny", []))): return self.j({"ok": False, "error": "perms"})
            for k in ("about", "brief", "persona", "cwd", "site", "chat", "provider", "model", "base", "perms"):
                if k in body: b[k] = body[k]
            if body.get("key") and body.get("provider") and body["provider"] not in b["keys"]: b["keys"].append(body["provider"])
            if "persona" in body: b["name"] = body["persona"].get("name") or b["name"]
            groups = {g["id"]: g["title"] for g in b["groups"]}
            b["chat_title"] = groups.get(b["chat"], ""); b["active"] = bool(b["chat"]); b["error"] = ""
            return self.j({"ok": True, "bot": b})
        return self.j({"ok": False, "error": "net"})
    def answer(self, sid, raw):
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return self.j({"ok": False, "error": "Invalid JSON"}, 400)
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        scenario = cookie["answer_reply"].value if "answer_reply" in cookie else "ok"
        delay = cookie["answer_delay"].value if "answer_delay" in cookie else "0"
        with DATA_LOCK:
            generation = read_data().get("_test_case")
            if os.environ.get("ANSWER_LOG"):
                with open(os.environ["ANSWER_LOG"], "a") as log:
                    log.write(json.dumps({k: v for k, v in body.items() if k != "step"}, ensure_ascii=False) + "\n")
        time.sleep(min(5, max(0, float(delay))))
        if scenario == "network":
            self.close_connection = True
            return
        if scenario == "internal":  # the real server's crash path: 500 {"error": "internal"}
            return self.j({"error": "internal"}, 500)
        if scenario == "fail":
            return self.j({"ok": False, "error": "синтетическая ошибка"})
        if scenario not in ("ok", "next"):
            return self.j({"ok": False, "error": "Unknown answer scenario"}, 400)
        with DATA_LOCK:
            data = read_data()
            session = data["session"].get(sid)
            if data.get("_test_case") != generation or not session or not session.get("question"):
                return self.j({"ok": False, "error": "Question closed"})
            question = session["question"]
            if body.get("action") == "option" and question.get("multi"):
                # multi-select: toggle the mark, keep the question open
                option = question["options"][int(body["index"]) - 1]
                option["marked"] = not option.get("marked")
                for row in data["sessions"]["sessions"]:
                    if row["id"] == sid:
                        row["question"] = question
                        row["ask"] = question["question"]
                write_data(data)
                return self.j({"ok": True, "question": question})
            fresh = None
            if scenario == "next":
                fresh = deepcopy(session["question"])
                fresh.update(step=fresh.get("step", 0) + 1, n=max(2, fresh.get("n", 1)), header="Second step", question="Which cache expiry should we use?")
            session["question"] = fresh
            for row in data["sessions"]["sessions"]:
                if row["id"] == sid:
                    row["question"] = fresh
                    row["ask"] = fresh["question"] if fresh else None
            write_data(data)
        return self.j({"ok": True, "question": fresh})
    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); p = u.path
        if p.startswith("/app"):
            return self.static()
        if p.startswith("/f/"):  # превью файла владельца: портретный кадр с камеры iPhone, как в жизни (1320×2868)
            b = b'<svg xmlns="http://www.w3.org/2000/svg" width="1320" height="2868"><rect width="1320" height="2868" fill="#8a94a6"/><rect y="1900" width="1320" height="968" fill="#1d2230"/></svg>'
            self.send_response(200); self.send_header("Content-Type", "image/svg+xml"); self.end_headers(); return self.wfile.write(b)
        if p.startswith("/r/"):  # дневной отчёт: токен — доступ; данные из REPORT_FILE, токен «gone» — устаревшая ссылка
            if p.endswith(".json"):
                if p == "/r/gone.json" or not os.environ.get("REPORT_FILE"):
                    return self.j({"error": "not found"}, 404)
                return self.j(json.load(open(os.environ["REPORT_FILE"])))
            body = open(os.path.join(APP, "report.html"), "rb").read()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
        if p == "/api/sessions":
            if os.environ.get("FAIL_SESSIONS") == "1" or "fail_sessions=1" in self.headers.get("Cookie", ""):
                return self.j({"error": "synthetic failure"}, 500)
            d = read_data()["sessions"]
            if isinstance(d, dict): d["sessions"] = NEW + d["sessions"]
            if isinstance(d, dict): d["quota"] = dict(d.get("quota") or {}, pinned=quota_pinned())
            if isinstance(d, dict) and d.get("topics"):  # как сервер с темами: имя бота и тема каждой сессии (t.me/<bot>/<тема>)
                d["bot"] = "pager_demo_bot"
                for i, row in enumerate(d.get("sessions") or []): row.setdefault("thread", 800 + i)
            return self.j(d)
        if p == "/api/bots": return self.j(BOTS)
        if p == "/api/perms": return self.j(PERMS)
        if p == "/api/model": return self.j(MODEL)
        if p == "/api/quota": return self.j(quota_status(False))
        if p == "/api/commands": return self.j(R["commands"])
        if p == "/api/dirs":  # models as the real api_dirs: Claude Code (TUI) + the SDK runner's provider
            q_val = (q.get("q") or [None])[0]
            if not q_val:
                return self.j({"models": {"labels": {"fable": "Fable 5.2", "opus": "Opus 5.5", "sonnet": "Sonnet 5.5", "haiku": "Haiku 5.5", "claude-fable-5-1": "Fable 5.1"}, "claude": ["fable", "opus", "sonnet", "haiku", "claude-fable-5-1"], "sdk": {"provider": "glm", "title": "GLM", "model": "glm-5.3", "models": ["glm-5.3"]}}, **R["dirs"]})
            if len(q_val) > 4096:
                return self.j({"error": "too long"}, 400)
            SYNTH_TREE = ["~/code/zidos-pager", "~/code/zidos-core", "~/srv/vaultwarden", "~/.workflow"]
            name_part = "" if q_val.endswith("/") else q_val.rsplit("/", 1)[-1]
            hints = [path for path in SYNTH_TREE if path.lower().startswith(q_val.lower())
                     and not ("/." in path and not name_part.startswith("."))]
            hints.sort(key=lambda s: s.lower())
            exists = q_val.rstrip("/") in [path.rstrip("/") for path in SYNTH_TREE] or q_val in ("~", "~/")
            return self.j({"q": q_val, "hints": hints[:8], "exists": exists})
        if p == "/api/file": return self.j({"url": "/f/x"})
        if p.startswith("/api/session/"):
            current = read_data()
            parts = p.split("/")[3:]
            sid = parts[0]
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            delay = min(5, max(0, float(cookie["session_delay"].value))) if len(parts) == 1 and "session_delay" in cookie else 0
            if len(parts) == 1 and os.environ.get("GET_LOG"):
                with DATA_LOCK, open(os.environ["GET_LOG"], "a") as log:
                    log.write(json.dumps({"sid": sid, "delay": delay, "phase": "captured"}) + "\n")
            if delay:
                time.sleep(delay)
                if os.environ.get("GET_LOG"):
                    with DATA_LOCK, open(os.environ["GET_LOG"], "a") as log:
                        log.write(json.dumps({"sid": sid, "delay": delay, "phase": "completed"}) + "\n")
            if len(parts) == 2 and parts[1] == "screen": return self.j({"menu": False, "screen": "Proceed? [y/n]\n> "})
            if len(parts) == 2 and parts[1] == "subagents": return self.j(R["subagents"].get(sid, {"subagents": []}))
            if len(parts) == 3 and parts[1] == "subagent": return self.j(R["subagent"].get(parts[2]) or {"history": []})
            full = current["session"].get(sid, {}).get("history_all") or (LONG if sid == LONG_SID else None)
            if full is not None and len(parts) == 2 and parts[1] == "history":  # an older page, like the server: 24 entries before ?before=<uuid>
                before = (urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("before") or [""])[0]
                at = next((i for i, h in enumerate(full) if h.get("uuid") == before), -1)
                return self.j({"history": full[max(0, at - 24):at] if at >= 0 else [], "more": at > 24})
            current["sessions"]["sessions"] = NEW + current["sessions"]["sessions"]
            d = current["session"].get(sid) or next(({**r, "history": [], "question": None} for r in NEW if r["id"] == sid), None)
            if not d:
                base = next((s for s in current["sessions"]["sessions"] if s["id"] == sid), {"id": sid, "name": sid, "group": "work"})
                d = dict(base, history=[], cwd="~", question=None)
            d = dict(d)
            if full is not None:  # the server window: the last 24 entries and whether older ones exist
                d["history"], d["history_more"] = full[-24:], len(full) > 24
            if current["sessions"].get("topics"):
                d.setdefault("thread", 800 + next((i for i, s in enumerate(current["sessions"]["sessions"]) if s["id"] == sid), 0))
            return self.j(d)
        self.send_response(404); self.end_headers()
if __name__ == "__main__":
    http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("PORT", "18981"))), H).serve_forever()
