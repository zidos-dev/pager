import asyncio
import socket
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import types
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sdk_runner  # noqa: E402

KEY = "sk-ant-TESTMARKER-9f3a1c"


# ---- fake SDK -------------------------------------------------------------------------------------------

class Msg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class SystemMessage(Msg):
    pass


class AssistantMessage(Msg):
    pass


class ResultMessage(Msg):
    pass


class PermissionResultAllow:
    def __init__(self, updated_input=None, updated_permissions=None):
        self.updated_input = updated_input
        self.updated_permissions = updated_permissions


class PermissionResultDeny:
    def __init__(self, message="", interrupt=False):
        self.message = message
        self.interrupt = interrupt


class ClaudeAgentOptions:
    def __init__(self, **kw):
        self.kw = kw

    def __getattr__(self, k):
        try:
            return self.__dict__["kw"][k]
        except KeyError:
            raise AttributeError(k)


class Sugg:
    def __init__(self, destination, mode="acceptEdits"):
        self.destination = destination
        self.mode = mode

    def to_dict(self):
        return {"type": "setMode", "destination": self.destination, "mode": self.mode}


class Ctx:
    def __init__(self, suggestions=(), title=None, tool_use_id="toolu_1"):
        self.suggestions = list(suggestions)
        self.title = title
        self.tool_use_id = tool_use_id


def result(subtype="success", cost=0.01, is_error=False):
    return ResultMessage(subtype=subtype, is_error=is_error, total_cost_usd=cost, session_id="x")


def init(src="apiKeyHelper"):
    return SystemMessage(subtype="init", data={"apiKeySource": src})


class FakeClient:
    instances = []
    script = None       # async generator function (client, text) -> messages after init
    api_key_source = "apiKeyHelper"
    hold_disconnect = None   # asyncio.Event: disconnect waits until it is set
    hold_connect = None      # asyncio.Event: connect waits until it is set
    pid_provider = None      # callable -> pid exposed as _transport._process.pid
    on_disconnect = None     # callable run when disconnect finishes

    def __init__(self, options):
        self.options = options
        self.queries = []
        self.connected = False
        self.disconnected = False
        self.interrupted = asyncio.Event()
        self.pending_text = None
        FakeClient.instances.append(self)

    async def connect(self):
        if FakeClient.hold_connect is not None:
            await FakeClient.hold_connect.wait()
        self.connected = True
        if FakeClient.pid_provider:
            self._transport = types.SimpleNamespace(
                _process=types.SimpleNamespace(pid=FakeClient.pid_provider()))

    async def query(self, text):
        self.queries.append(text)
        self.pending_text = text

    async def interrupt(self):
        self.interrupted.set()

    async def set_model(self, model):
        self.queries.append(("model", model))

    async def disconnect(self):
        if FakeClient.hold_disconnect is not None and not FakeClient.hold_disconnect.is_set():
            await FakeClient.hold_disconnect.wait()
        self.disconnected = True
        if FakeClient.on_disconnect:
            FakeClient.on_disconnect()

    async def receive_response(self):
        yield init(FakeClient.api_key_source)
        script = FakeClient.script
        if script is None:
            yield result()
            return
        async for m in script(self, self.pending_text):
            yield m


def make_fake():
    FakeClient.instances = []
    FakeClient.script = None
    FakeClient.api_key_source = "apiKeyHelper"
    FakeClient.hold_disconnect = FakeClient.hold_connect = None
    FakeClient.pid_provider = FakeClient.on_disconnect = None
    return types.SimpleNamespace(
        __version__="0.0-fake", ClaudeSDKClient=FakeClient, ClaudeAgentOptions=ClaudeAgentOptions,
        PermissionResultAllow=PermissionResultAllow, PermissionResultDeny=PermissionResultDeny,
        SystemMessage=SystemMessage, ResultMessage=ResultMessage, AssistantMessage=AssistantMessage)


# ---- harness --------------------------------------------------------------------------------------------

class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.keyfile = os.path.join(t, "key.env")
        with open(self.keyfile, "w") as f:
            f.write("OTHER=zzz\nANTHROPIC_API_KEY_1='%s'\n" % KEY)
        self.sock = os.path.join(t, "run", "sdk.sock")
        self.state = os.path.join(t, "run", "sdk.json")
        self.work = os.path.join(t, "work")
        os.mkdir(self.work)
        self.fake = make_fake()
        self._old_load = sdk_runner.load_sdk
        sdk_runner.load_sdk = lambda: self.fake
        self.addCleanup(setattr, sdk_runner, "load_sdk", self._old_load)
        self._env = dict(os.environ)
        os.environ["ANTHROPIC_API_KEY"] = "leak-should-vanish"
        os.environ["ANTHROPIC_BASE_URL"] = "x"
        os.environ["CC_SDK_KEY_FILE"] = self.keyfile
        os.environ["CC_SDK_KEY_VAR"] = "ANTHROPIC_API_KEY_1"
        self.addCleanup(self._restore_env)
        self.stderr = io.StringIO()
        self._redir = contextlib.redirect_stderr(self.stderr)
        self._redir.__enter__()
        self.addCleanup(self._redir.__exit__, None, None, None)
        await self.new_runner()

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)

    async def new_runner(self):
        self.runner = sdk_runner.Runner(self.sock, self.state)
        await self.runner.start()
        self.addAsyncCleanup(self.runner.close)

    async def call(self, method, **params):
        r, w = await asyncio.open_unix_connection(self.sock)
        w.write((json.dumps({"id": "1", "method": method, "params": params}) + "\n").encode())
        await w.drain()
        line = await r.readline()
        w.close()
        return json.loads(line)

    async def ok(self, method, **params):
        res = await self.call(method, **params)
        self.assertIn("result", res, res)
        return res["result"]

    async def err(self, method, code, **params):
        res = await self.call(method, **params)
        self.assertEqual(res.get("error", {}).get("code"), code, res)

    async def start(self, prompt="do it", **kw):
        return (await self.ok("session.start", cwd=self.work, prompt=prompt, **kw))["session"]

    async def wait_state(self, sid, state, timeout=3):
        for _ in range(int(timeout / 0.01)):
            s = (await self.ok("session.get", id=sid))["session"]
            if s["state"] == state:
                return s
            await asyncio.sleep(0.01)
        self.fail("state %s not reached, now %s" % (state, s["state"]))

    async def subscribe(self):
        r, w = await asyncio.open_unix_connection(self.sock)
        w.write((json.dumps({"id": "sub", "method": "events.subscribe"}) + "\n").encode())
        await w.drain()
        first = json.loads(await r.readline())
        self.assertEqual(first["result"], {"subscribed": True})
        self.addCleanup(w.close)
        return r, w

    async def event(self, r, name, timeout=3):
        while True:
            line = await asyncio.wait_for(r.readline(), timeout)
            e = json.loads(line)
            if e["event"] == name:
                return e["data"]


def perm_script(holder, tool="Write", inp=None, suggestions=(), then=True):
    async def script(client, text):
        holder["result"] = await client.options.can_use_tool(
            tool, inp or {"file_path": "a"}, Ctx(suggestions))
        if then:
            yield result()
    return script


# ---- tests ----------------------------------------------------------------------------------------------

class StartTests(Base):
    async def test_zero_budget_means_no_limit(self):
        os.environ["CC_SDK_BUDGET"] = "0"
        await self.new_runner()
        s = await self.start()
        self.assertIsNone(s["budget_usd"])
        await self.wait_state(s["id"], "idle")
        self.assertIsNone(FakeClient.instances[-1].options.kw["max_budget_usd"])

    async def test_chat_session_gets_only_the_listed_tools(self):
        await self.new_runner()
        FakeClient.script = None
        s = await self.start(chat=True)
        await self.wait_state(s["id"], "idle")
        kw = FakeClient.instances[-1].options.kw
        self.assertEqual(kw["tools"], sdk_runner.CHAT_TOOLS)
        self.assertFalse({"SendMessage", "ListAgents", "Agent", "Task"} & set(kw["tools"]))
        c = await self.start()
        await self.wait_state(c["id"], "idle")
        self.assertNotIn("tools", FakeClient.instances[-1].options.kw)  # the owner's own sessions keep every tool

    async def _third_party(self, **extra):
        os.environ.update(CC_SDK_BASE_URL="https://llm.example/anthropic", CC_SDK_MODEL="flash",
                          CC_SDK_STRONG_MODEL="pro", CC_SDK_MAX_TURNS="7", **extra)
        await self.new_runner()
        FakeClient.script = None
        s = await self.start(budget_usd=2.0)
        await self.wait_state(s["id"], "idle")
        return s, FakeClient.instances[-1].options.kw

    async def test_third_party_endpoint_env_and_turns(self):
        _, kw = await self._third_party()
        self.assertEqual(kw["env"], {
            "ANTHROPIC_BASE_URL": "https://llm.example/anthropic", "ANTHROPIC_DEFAULT_OPUS_MODEL": "pro",
            "ANTHROPIC_DEFAULT_SONNET_MODEL": "flash", "ANTHROPIC_DEFAULT_HAIKU_MODEL": "flash",
            "ANTHROPIC_SMALL_FAST_MODEL": "flash", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
        self.assertEqual(kw["max_turns"], 7)
        self.assertIn("apiKeyHelper", json.loads(kw["settings"]))

    async def test_login_mode_runs_without_a_key_on_the_cli_sign_in(self):
        os.environ.update(CC_SDK_AUTH="login", CC_SDK_KEY_FILE="/nonexistent/keys.env", CC_SDK_BASE_URL="https://ignored.example")
        await self.new_runner()
        FakeClient.script = None
        FakeClient.api_key_source = "none"  # what the CLI reports on a claude.ai sign-in
        s = await self.start()
        await self.wait_state(s["id"], "idle")  # no «API key is not available»
        kw = FakeClient.instances[-1].options.kw
        self.assertNotIn("apiKeyHelper", json.loads(kw["settings"]))
        self.assertNotIn("env", kw)  # no third-party endpoint: the CLI talks to Anthropic with its own sign-in
        self.assertNotIn("max_budget_usd", {k: v for k, v in kw.items() if v is not None})

    async def test_login_mode_refuses_a_stray_key(self):
        os.environ.update(CC_SDK_AUTH="login")
        await self.new_runner()
        FakeClient.api_key_source = "ANTHROPIC_API_KEY"
        s = await self.start()
        await self.wait_state(s["id"], "error")

    async def test_key_mode_refuses_the_subscription(self):
        await self.new_runner()
        FakeClient.api_key_source = "none"
        s = await self.start()
        await self.wait_state(s["id"], "error")

    async def test_third_party_key_stays_out_of_env(self):
        _, kw = await self._third_party()
        self.assertNotIn(KEY, json.dumps(kw["env"]))
        self.assertFalse([k for k in kw["env"] if "KEY" in k or "TOKEN" in k])

    async def test_third_party_has_no_dollar_budget_and_cost_stays_zero(self):
        os.environ["CC_SDK_BUDGET"] = "5"
        s, kw = await self._third_party()
        self.assertNotIn("max_budget_usd", kw)  # the CLI prices unknown models like Claude
        self.assertEqual((await self.ok("session.get", id=s["id"]))["session"]["cost_usd"], 0.0)

    async def test_third_party_budget_unset_by_default(self):
        os.environ["CC_SDK_BASE_URL"] = "https://llm.example/anthropic"
        await self.new_runner()
        self.assertIsNone(self.runner.budget)
        self.assertEqual(self.runner.strong_model, "sonnet")
        self.assertEqual(self.runner.max_turns, 30)

    async def test_third_party_absent_plain_anthropic_unchanged(self):
        s = await self.start()
        await self.wait_state(s["id"], "idle")
        kw = FakeClient.instances[-1].options.kw
        self.assertNotIn("env", kw)
        self.assertNotIn("max_turns", kw)
        self.assertEqual(kw["max_budget_usd"], 1.0)
        self.assertEqual((await self.ok("session.get", id=s["id"]))["session"]["cost_usd"], 0.01)

    async def test_start_options_and_list(self):
        s = await self.start("first line\nsecond")
        self.assertTrue(s["id"].startswith("s-") and len(s["id"]) == 10)
        self.assertEqual(s["name"], "first line")
        self.assertEqual(s["model"], "sonnet")
        self.assertEqual(s["budget_usd"], 1.0)
        await self.wait_state(s["id"], "idle")
        lst = (await self.ok("session.list"))["sessions"]
        self.assertEqual([x["id"] for x in lst], [s["id"]])
        kw = FakeClient.instances[0].options.kw
        self.assertEqual(kw["session_id"], s["session_id"])
        self.assertNotIn("resume", kw)
        self.assertEqual(kw["setting_sources"], ["project"])
        self.assertEqual(kw["permission_mode"], "default")
        self.assertEqual(kw["max_budget_usd"], 1.0)
        self.assertFalse(kw.get("env"))
        self.assertNotIn(KEY, repr(kw))
        self.assertNotIn(KEY, repr(FakeClient.instances[0].options.kw))
        helper = json.loads(kw["settings"])["apiKeyHelper"]
        import shlex
        parts = shlex.split(helper)
        self.assertEqual(parts[0], sys.executable)
        self.assertEqual(parts[1], os.path.abspath(sdk_runner.__file__))
        self.assertEqual(parts[2:], ["key", "--file", self.keyfile, "--var", "ANTHROPIC_API_KEY_1"])
        self.assertEqual(FakeClient.instances[0].queries, ["first line\nsecond"])
        self.assertEqual(oct(stat.S_IMODE(os.stat(self.sock).st_mode)), "0o600")
        self.assertEqual(oct(stat.S_IMODE(os.stat(os.path.dirname(self.sock)).st_mode)), "0o700")

    async def test_chat_session_asks_the_owner_for_everything_but_reading(self):
        holder = {}

        async def script(client, text):
            for tool, inp in (("WebSearch", {"query": "x"}), ("AskUserQuestion", {"questions": []}),
                              ("WebFetch", {"url": "https://example.com/odds"})):
                holder[tool] = await client.options.can_use_tool(tool, inp, Ctx())
            holder["Bash"] = await client.options.can_use_tool("Bash", {"command": "git push", "dangerouslyDisableSandbox": True}, Ctx())
            yield ResultMessage(subtype="success", is_error=False, total_cost_usd=0.01, session_id="x",
                                result="Ответ: example.com")
        FakeClient.script = script
        r, _ = await self.subscribe()
        s = await self.start("вопрос", chat=True)
        self.assertTrue(s["chat"])
        req = (await self.event(r, "request.opened"))["request"]
        self.assertEqual((req["tool"], req["can_always"]), ("Bash", False))  # each change: the owner decides
        await self.err("session.answer", "bad_request", id=s["id"], req=req["req"], decision="always")
        await self.ok("session.answer", id=s["id"], req=req["req"], decision="allow")
        done = await self.event(r, "turn.result")
        self.assertEqual(done["result"], "Ответ: example.com")
        self.assertIsInstance(holder["WebSearch"], PermissionResultAllow)
        self.assertIsInstance(holder["WebFetch"], PermissionResultAllow)
        dns = {"www.example.com": "104.21.1.1", "www.ufc.com": "23.1.2.3", "rebind.example.com": "192.168.3.8",
               "tail.example.com": "100.86.88.122"}
        def fake_gai(host, *a, **k):
            if host not in dns:
                raise socket.gaierror("no such host")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (dns[host], 443))]
        self.addCleanup(setattr, socket, "getaddrinfo", socket.getaddrinfo)
        socket.getaddrinfo = fake_gai
        self.assertTrue(sdk_runner.chat_free("WebFetch", {"url": "https://www.example.com/ufc/333?x=1#a"}))
        self.assertTrue(sdk_runner.chat_free("WebFetch", {"url": "https://www.ufc.com/event/ufc-333"}))  # owner 24.10
        self.assertTrue(sdk_runner.chat_free("SandboxNetworkAccess", {"host": "www.ufc.com"}))
        for host in ("rebind.example.com", "tail.example.com", "localhost", "127.0.0.1", "nas.local", "unknown.example.com"):
            self.assertFalse(sdk_runner.chat_free("SandboxNetworkAccess", {"host": host}), host)
            self.assertFalse(sdk_runner.chat_free("WebFetch", {"url": "https://%s/" % host}), host)
        for url in ("http://127.0.0.1:18795/api/sessions", "https://evil.example/?d=x", "https://example.com.evil.example/",
                    "https://example.com\\@evil.example/", "https://user@example.com/", "https://evil.example\\.example.com/",
                    "https://example.com%2eevil.example/", "https://example.com :443/", "https://example.com:8080/"):
            self.assertFalse(sdk_runner.chat_free("WebFetch", {"url": url}), url)  # goes to the owner
        self.assertIsInstance(holder["AskUserQuestion"], PermissionResultDeny)
        self.assertIsInstance(holder["Bash"], PermissionResultAllow)
        kw = FakeClient.instances[0].options.kw
        self.assertEqual(kw["tools"], sdk_runner.CHAT_TOOLS)  # full consultation, but nothing that reaches other sessions
        self.assertEqual(kw["setting_sources"], [])
        self.assertTrue(kw["strict_mcp_config"])
        st = json.loads(kw["settings"])
        self.assertIn("apiKeyHelper", st)
        self.assertIn("Read(**/.env*)", st["permissions"]["deny"])
        sb = st["sandbox"]  # bash runs at once inside an OS boundary: reads the project only, writes nothing
        self.assertTrue(sb["enabled"] and sb["autoAllowBashIfSandboxed"] and sb["failIfUnavailable"])
        self.assertEqual(sb["filesystem"]["denyRead"], ["~/"])
        self.assertEqual(sb["filesystem"]["allowRead"], [os.path.realpath(self.work)])
        deny = sb["filesystem"]["denyWrite"]  # literal paths only: globs do nothing in the Linux sandbox
        self.assertFalse([x for x in deny if "*" in x])
        root = os.path.realpath(self.work)
        self.assertEqual(sorted(deny), sorted(os.path.join(root, x) for x in set(os.listdir(root)) | {".mcp.json", ".claude", ".git", ".envrc"}))
        self.assertEqual(sb["network"]["allowedDomains"], [])  # CC_CHAT_SITE unset: no site, never ["", "*."]
        os.environ["CC_CHAT_SITE"] = "example.org"
        self.assertEqual(sdk_runner.chat_sandbox(self.work)["network"]["allowedDomains"], ["example.org", "*.example.org"])
        del os.environ["CC_CHAT_SITE"]
        await self.runner.close()
        await self.new_runner()
        self.assertTrue(self.runner.sessions[s["id"]].chat)

    async def test_ordinary_session_result_text_stays_out_of_events(self):
        r, _ = await self.subscribe()
        s = await self.start()
        self.assertFalse(s["chat"])
        self.assertNotIn("result", await self.event(r, "turn.result"))

    async def test_no_key(self):
        os.unlink(self.keyfile)
        await self.err("session.start", "no_key", cwd=self.work, prompt="x")
        info = await self.ok("runner.info")
        self.assertFalse(info["key"])

    async def test_bad_cwd(self):
        await self.err("session.start", "bad_request", cwd="/nonexistent-dir-xyz", prompt="x")

    async def test_info(self):
        info = await self.ok("runner.info")
        self.assertEqual(info["key"], True)
        self.assertEqual(info["sdk_version"], "0.0-fake")


class PermTests(Base):
    async def test_allow_flow_and_idempotent(self):
        holder = {}
        FakeClient.script = perm_script(holder)
        r, _ = await self.subscribe()
        s = await self.start()
        ev = await self.event(r, "request.opened")
        req = ev["request"]
        self.assertEqual(ev["id"], s["id"])
        self.assertEqual((req["kind"], req["tool"], req["can_always"]), ("perm", "Write", True))
        blocked = await self.wait_state(s["id"], "blocked")
        self.assertEqual(len(blocked["pending"]), 1)
        self.assertEqual(await self.ok("session.answer", id=s["id"], req=req["req"], decision="allow"),
                         {"ok": True})
        await self.wait_state(s["id"], "idle")
        self.assertIsInstance(holder["result"], PermissionResultAllow)
        self.assertIsNone(holder["result"].updated_input)
        self.assertEqual(await self.ok("session.answer", id=s["id"], req=req["req"], decision="allow"),
                         {"ok": True, "already": True})
        self.assertEqual(await self.ok("session.answer", id=s["id"], req="r-unknown", decision="deny"),
                         {"ok": True, "already": True})

    async def test_always_auto_allows_same_tool_only(self):
        res = []

        async def script(client, text):
            res.append(await client.options.can_use_tool("Write", {"file_path": "a"}, Ctx([Sugg("session")])))
            res.append(await client.options.can_use_tool("Write", {"file_path": "b"}, Ctx()))
            res.append(await client.options.can_use_tool("Bash", {"command": "ls"}, Ctx()))
            yield result()
        FakeClient.script = script
        r, _ = await self.subscribe()
        s = await self.start()
        req = (await self.wait_state(s["id"], "blocked"))["pending"][0]
        self.assertTrue(req["can_always"])
        await self.ok("session.answer", id=s["id"], req=req["req"], decision="always")
        # Write is auto-allowed, Bash asks
        first = await self.event(r, "request.opened")
        self.assertEqual(first["request"]["tool"], "Write")
        ev = await self.event(r, "request.opened")
        self.assertEqual(ev["request"]["tool"], "Bash")
        b = await self.wait_state(s["id"], "blocked")
        self.assertEqual(b["always"], ["Write"])
        await self.ok("session.answer", id=s["id"], req=ev["request"]["req"], decision="allow")
        await self.wait_state(s["id"], "idle")
        self.assertEqual(len(res), 3)
        for x in res:
            self.assertIsInstance(x, PermissionResultAllow)
            self.assertIsNone(x.updated_permissions)

    async def test_always_persisted_and_survives_restart(self):
        holder = {}
        FakeClient.script = perm_script(holder)
        s = await self.start()
        req = (await self.wait_state(s["id"], "blocked"))["pending"][0]
        await self.ok("session.answer", id=s["id"], req=req["req"], decision="always")
        await self.wait_state(s["id"], "idle")
        self.assertIsNone(holder["result"].updated_permissions)
        with open(self.state) as f:
            self.assertEqual(json.load(f)["sessions"][0]["always"], ["Write"])
        await self.runner.close()
        await self.new_runner()
        got = (await self.ok("session.get", id=s["id"]))["session"]
        self.assertEqual(got["always"], ["Write"])

    async def test_ask_never_auto_allowed_and_no_always(self):
        gate = asyncio.Event()

        async def script(client, text):
            await gate.wait()
            await client.options.can_use_tool(
                "AskUserQuestion", {"questions": []}, Ctx([Sugg("session")]))
            yield result()
        FakeClient.script = script
        s = await self.start()
        self.runner.sessions[s["id"]].always.append("AskUserQuestion")
        gate.set()
        b = await self.wait_state(s["id"], "blocked")
        req = b["pending"][0]
        self.assertEqual((req["kind"], req["can_always"]), ("ask", False))
        await self.err("session.answer", "bad_request", id=s["id"], req=req["req"],
                       decision="always", answers={"q": "a"})
        await self.ok("session.answer", id=s["id"], req=req["req"], decision="allow",
                      answers={"q": "a"})
        await self.wait_state(s["id"], "idle")

    async def test_ask_answers(self):
        holder = {}
        q = {"questions": [{"question": "Which?", "header": "h", "options": [], "multiSelect": False}]}
        FakeClient.script = perm_script(holder, tool="AskUserQuestion", inp=q)
        s = await self.start()
        req = (await self.wait_state(s["id"], "blocked"))["pending"][0]
        self.assertEqual(req["kind"], "ask")
        await self.err("session.answer", "bad_request", id=s["id"], req=req["req"], decision="allow")
        await self.ok("session.answer", id=s["id"], req=req["req"], decision="allow",
                      answers={"Which?": "A, B"})
        await self.wait_state(s["id"], "idle")
        self.assertEqual(holder["result"].updated_input, {**q, "answers": {"Which?": "A, B"}})

    async def test_deny_message_and_default(self):
        for msg, expect in (("No thanks", "No thanks"), (None, sdk_runner.DEFAULT_DENY)):
            holder = {}
            FakeClient.script = perm_script(holder)
            s = await self.start()
            req = (await self.wait_state(s["id"], "blocked"))["pending"][0]
            extra = {"message": msg} if msg else {}
            await self.ok("session.answer", id=s["id"], req=req["req"], decision="deny", **extra)
            await self.wait_state(s["id"], "idle")
            self.assertIsInstance(holder["result"], PermissionResultDeny)
            self.assertEqual(holder["result"].message, expect)
            self.assertFalse(holder["result"].interrupt)

    async def test_interrupt_denies_pending(self):
        holder = {}

        async def script(client, text):
            holder["result"] = await client.options.can_use_tool("Bash", {"command": "ls"}, Ctx())
            await client.interrupted.wait()
            yield result("error_during_execution", is_error=True)
        FakeClient.script = script
        r, _ = await self.subscribe()
        s = await self.start()
        await self.wait_state(s["id"], "blocked")
        await self.ok("session.interrupt", id=s["id"])
        closed = await self.event(r, "request.closed")
        self.assertEqual(closed["decision"], "interrupt")
        await self.wait_state(s["id"], "idle")
        self.assertIsInstance(holder["result"], PermissionResultDeny)
        self.assertTrue(holder["result"].interrupt)
        self.assertTrue(FakeClient.instances[0].interrupted.is_set())


class SendTests(Base):
    async def test_busy_then_idle_send_same_client(self):
        gate = asyncio.Event()

        async def script(client, text):
            if text == "one":
                await gate.wait()
            yield result(cost=0.5 if text == "one" else 0.7)
        FakeClient.script = script
        s = await self.start("one")
        await self.wait_state(s["id"], "working")
        await self.err("session.send", "busy", id=s["id"], text="two")
        gate.set()
        await self.wait_state(s["id"], "idle")
        await self.ok("session.send", id=s["id"], text="two")
        done = None
        for _ in range(300):
            done = (await self.ok("session.get", id=s["id"]))["session"]
            if done["cost_usd"] == 0.7 and done["state"] == "idle":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(done["cost_usd"], 0.7)
        self.assertEqual(len(FakeClient.instances), 1)
        self.assertEqual(FakeClient.instances[0].queries, ["one", "two"])

    def test_sandboxed_bash_needs_no_one_leaving_the_sandbox_does(self):
        cmd = 'ls -t .chat-context/ 2>/dev/null | head -5 && f=$(ls -t .chat-context/H*.md | head -1) && head -c 3500 "$f"'
        self.assertTrue(sdk_runner.chat_free("Bash", {"command": cmd}))
        self.assertFalse(sdk_runner.chat_free("Bash", {"command": "touch x", "dangerouslyDisableSandbox": True}))
        self.assertFalse(sdk_runner.chat_free("Write", {"file_path": "x", "content": ""}))

    def test_owner_chat_sandbox_writes_in_the_project_but_not_into_git_or_settings(self):
        root = os.path.realpath(self.work)
        sb = sdk_runner.chat_sandbox(self.work, write=True)
        self.assertEqual(sb["filesystem"]["denyWrite"], [os.path.join(root, x) for x in (".mcp.json", ".claude", ".git", ".envrc")])
        self.assertEqual(sb["filesystem"]["allowRead"], [root])
        self.assertEqual(sb["network"], sdk_runner.chat_sandbox(self.work)["network"])

    async def test_send_switches_the_model_for_that_turn(self):
        s = await self.start("one")
        await self.wait_state(s["id"], "idle")
        await self.ok("session.send", id=s["id"], text="two", model="opus")
        await self.wait_state(s["id"], "idle")
        await self.ok("session.send", id=s["id"], text="three", model="")  # back to the default
        await self.wait_state(s["id"], "idle")
        await self.ok("session.send", id=s["id"], text="four")  # no model: as it is
        for _ in range(300):
            if FakeClient.instances[0].queries[-1] == "four":
                break
            await asyncio.sleep(0.01)
        self.assertEqual(FakeClient.instances[0].queries,
                         ["one", ("model", "opus"), "two", ("model", self.runner.model), "three", "four"])
        await self.wait_state(s["id"], "idle")
        await self.err("session.send", "bad_request", id=s["id"], text="x", model=1)

    async def test_resume_after_budget(self):
        async def script(client, text):
            yield result("error_max_budget_usd", cost=1.02, is_error=True)
        FakeClient.script = script
        r, _ = await self.subscribe()
        s = await self.start()
        b = await self.wait_state(s["id"], "budget")
        self.assertEqual(b["cost_usd"], 1.02)
        tr = await self.event(r, "turn.result")
        self.assertEqual(tr["subtype"], "error_max_budget_usd")
        self.assertTrue(FakeClient.instances[0].disconnected)
        FakeClient.script = None
        r2 = await self.ok("session.budget", id=s["id"], budget_usd=3)
        self.assertEqual(r2["session"]["budget_usd"], 3.0)
        await self.err("session.budget", "bad_request", id=s["id"], budget_usd=-1)
        await self.ok("session.send", id=s["id"], text="go on")
        await self.wait_state(s["id"], "idle")
        kw = FakeClient.instances[1].options.kw
        self.assertEqual(kw["resume"], s["session_id"])
        self.assertNotIn("session_id", kw)
        self.assertEqual(kw["max_budget_usd"], 3.0)
        self.assertEqual(FakeClient.instances[1].queries, ["go on"])

    async def test_resume_after_error_and_stopped_closed(self):
        async def script(client, text):
            yield result("error_max_turns", is_error=True)
        FakeClient.script = script
        s = await self.start()
        await self.wait_state(s["id"], "error")
        FakeClient.script = None
        await self.ok("session.send", id=s["id"], text="again")
        await self.wait_state(s["id"], "idle")
        self.assertEqual(FakeClient.instances[1].options.kw["resume"], s["session_id"])
        await self.ok("session.stop", id=s["id"])
        self.assertTrue(FakeClient.instances[1].disconnected)
        await self.ok("session.send", id=s["id"], text="after stop")  # stopped is resumable
        await self.wait_state(s["id"], "idle")
        self.assertEqual(FakeClient.instances[2].options.kw["resume"], s["session_id"])
        self.assertEqual(FakeClient.instances[2].queries, ["after stop"])
        await self.err("session.get", "not_found", id="s-00000000")

    async def test_stop_while_blocked(self):
        holder = {}
        FakeClient.script = perm_script(holder, then=False)
        s = await self.start()
        await self.wait_state(s["id"], "blocked")
        await self.ok("session.stop", id=s["id"])
        got = (await self.ok("session.get", id=s["id"]))["session"]
        self.assertEqual((got["state"], got["pending"]), ("stopped", []))
        self.assertTrue(FakeClient.instances[0].disconnected)


def alive(pid):
    try:
        with open("/proc/%d/stat" % pid) as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


class RobustnessTests(Base):
    async def test_send_waits_for_old_cleanup(self):  # finding 1
        FakeClient.hold_disconnect = asyncio.Event()
        n = []

        async def script(client, text):
            n.append(text)
            if len(n) == 1:
                yield result("error_max_budget_usd", is_error=True)
            else:
                await client.options.can_use_tool("Bash", {"command": "ls"}, Ctx())
                yield result()
        FakeClient.script = script
        s = await self.start()
        await self.wait_state(s["id"], "budget")
        send = asyncio.ensure_future(self.call("session.send", id=s["id"], text="again"))
        await asyncio.sleep(0.15)
        self.assertEqual(len(FakeClient.instances), 1)  # not spawned while the old one cleans up
        FakeClient.hold_disconnect.set()
        self.assertIn("result", await send)
        await self.wait_state(s["id"], "blocked")
        await asyncio.sleep(0.2)
        got = (await self.ok("session.get", id=s["id"]))["session"]
        self.assertEqual((got["state"], len(got["pending"])), ("blocked", 1))
        self.assertIs(self.runner.sessions[s["id"]].client, FakeClient.instances[1])

    async def test_load_clears_stale_pid_of_all_states(self):  # finding 2
        await self.runner.close()
        with open(self.state, "w") as f:
            json.dump({"sessions": [{"id": "s-00000001", "session_id": "u", "cwd": self.work,
                                     "state": "idle", "cli_pid": os.getpid()}]}, f)
        await self.new_runner()
        self.assertIsNone(self.runner.sessions["s-00000001"].cli_pid)
        with open(self.state) as f:
            self.assertIsNone(json.load(f)["sessions"][0]["cli_pid"])

    async def test_load_kills_leftover_cli_only_with_its_starttime(self):
        await self.runner.close()
        env = dict(os.environ, CLAUDE_CODE_ENTRYPOINT="sdk-py")
        for shift, killed in ((1, False), (0, True)):  # a reused PID has another starttime
            p = await asyncio.create_subprocess_exec("bash", "-c", "exec -a claude sleep 60", env=env)
            await asyncio.sleep(0.2)
            self.assertTrue(sdk_runner.is_our_cli(p.pid))
            with open(self.state, "w") as f:
                json.dump({"sessions": [{"id": "s-00000001", "session_id": "u", "cwd": self.work, "state": "idle",
                                         "cli_pid": p.pid, "cli_st": sdk_runner._starttime(p.pid) + shift}]}, f)
            await self.new_runner()
            try:
                await asyncio.wait_for(p.wait(), 1)
            except asyncio.TimeoutError:
                pass
            self.assertEqual(p.returncode is not None, killed)
            if p.returncode is None:
                p.kill()
                await p.wait()
            await self.runner.close()

    async def test_orphaned_descendants_are_killed(self):  # finding 3
        procs = []

        async def spawn():
            p = await asyncio.create_subprocess_exec("sh", "-c", "sleep 60 & wait")
            procs.append(p)
            await asyncio.sleep(0.3)
            return p.pid
        pid = await spawn()
        FakeClient.pid_provider = lambda: pid
        FakeClient.on_disconnect = lambda: os.kill(pid, 9)  # CLI dies, its children are orphaned
        kids = sdk_runner._children_map().get(pid, [])
        self.assertTrue(kids)
        async def script(client, text):
            yield result("error_max_budget_usd", is_error=True)
        FakeClient.script = script
        s = await self.start()
        await self.wait_state(s["id"], "budget")
        await asyncio.sleep(0.5)
        self.assertFalse(any(alive(k) for k in kids))
        for p in procs:
            await p.wait()

    async def test_snapshot_skips_reused_pid(self):  # finding 3, pid reuse guard
        p = await asyncio.create_subprocess_exec("sleep", "60")
        snap = sdk_runner.snapshot_tree(p.pid)
        self.assertEqual(snap[0][0], p.pid)
        sdk_runner.kill_snapshot([(p.pid, snap[0][1] + 1)])
        await asyncio.sleep(0.1)
        self.assertTrue(alive(p.pid))
        sdk_runner.kill_snapshot(snap)
        await asyncio.wait_for(p.wait(), 3)

    async def test_subscriber_writer_closed_on_eof(self):  # finding 6
        r, w = await self.subscribe()
        srv = next(iter(self.runner.subscribers))
        w.write(b"junk" * 1000)
        await w.drain()
        w.close()
        for _ in range(100):
            if srv.is_closing():
                break
            await asyncio.sleep(0.01)
        self.assertTrue(srv.is_closing())
        self.assertFalse(self.runner.subscribers)

    async def test_interrupt_during_connect(self):  # finding 7
        FakeClient.hold_connect = asyncio.Event()
        s = await self.start()
        await asyncio.sleep(0.05)
        await self.ok("session.interrupt", id=s["id"])
        FakeClient.hold_connect.set()
        await self.wait_state(s["id"], "idle")
        self.assertEqual(FakeClient.instances[0].queries, [])
        FakeClient.hold_connect = None
        await self.ok("session.send", id=s["id"], text="next")
        await asyncio.sleep(0.1)
        self.assertEqual(FakeClient.instances[0].queries, ["next"])

    async def test_runner_env_scrubbed_of_bot_vars(self):  # finding 8
        os.environ["TG_BOT_TOKEN"] = "t"
        os.environ["ZIDOS_PAGER_ENV"] = "e"
        sdk_runner.Runner(self.sock + "2", self.state + "2")
        self.assertNotIn("TG_BOT_TOKEN", os.environ)
        self.assertNotIn("ZIDOS_PAGER_ENV", os.environ)

    def test_unit_and_env_example(self):  # finding 8
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "deploy", "zidos-pager-sdk.service")) as f:
            self.assertIn("EnvironmentFile=-%h/.config/zidos-pager/sdk.env", f.read())
        with open(os.path.join(root, "env.example")) as f:
            self.assertIn("sdk.env", f.read())


class KeySubcommandTests(unittest.TestCase):
    def run_key(self, *args):
        import subprocess
        return subprocess.run([sys.executable, sdk_runner.__file__, "key", *args],
                              capture_output=True, text=True, env={"PATH": os.environ.get("PATH", "")})

    def test_prints_key_without_newline(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as f:
            f.write("A=1\nK='%s'\n" % KEY)
            f.flush()
            r = self.run_key("--file", f.name, "--var", "K")
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, KEY, ""))

    def test_missing_key_exit_1_empty(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as f:
            f.write("A=1\n")
            f.flush()
            for args in (("--file", f.name, "--var", "NOPE"), ("--file", f.name + ".none", "--var", "A")):
                r = self.run_key(*args)
                self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "", ""))


class ScrubTests(unittest.TestCase):
    def test_scrub_env(self):
        old = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(old)))
        for k in ("ANTHROPIC_API_KEY", "TG_BOT_TOKEN", "ZIDOS_PAGER_ENV"):
            os.environ[k] = "x"
        os.environ["KEEP_ME"] = "1"
        sdk_runner.scrub_env()
        self.assertEqual([k for k in ("ANTHROPIC_API_KEY", "TG_BOT_TOKEN", "ZIDOS_PAGER_ENV") if k in os.environ], [])
        self.assertIn("KEEP_ME", os.environ)


class BadVarTests(Base):
    async def test_invalid_key_var_is_no_key(self):
        self.runner.key_var = "A B; rm"
        await self.err("session.start", "no_key", cwd=self.work, prompt="x")
        self.assertFalse((await self.ok("runner.info"))["key"])


class AuthTests(Base):
    async def test_wrong_api_key_source(self):
        FakeClient.api_key_source = "ANTHROPIC_API_KEY"  # the old way is not accepted either
        s = await self.start()
        e = await self.wait_state(s["id"], "error")
        self.assertEqual(e["detail"], "not authenticated with the API key")
        for _ in range(100):
            if FakeClient.instances[0].disconnected:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(FakeClient.instances[0].disconnected)


class SecretTests(Base):
    async def test_key_never_leaks(self):
        holder = {}
        FakeClient.script = perm_script(holder)
        r, _ = await self.subscribe()
        s = await self.start("secret prompt body")
        req = (await self.wait_state(s["id"], "blocked"))["pending"][0]
        await self.ok("session.answer", id=s["id"], req=req["req"], decision="allow")
        await self.wait_state(s["id"], "idle")
        replies = json.dumps([await self.call("session.list"), await self.call("runner.info"),
                              await self.call("session.get", id=s["id"])])
        self.assertNotIn(KEY, replies)
        with open(self.state) as f:
            raw = f.read()
        self.assertNotIn(KEY, raw)
        self.assertEqual(oct(stat.S_IMODE(os.stat(self.state).st_mode)), "0o600")
        log = self.stderr.getvalue()
        self.assertNotIn(KEY, log)
        self.assertNotIn("secret prompt body", log)
        self.assertNotIn("file_path", log)
        self.assertTrue(log)
        self.assertFalse([k for k in os.environ if k.startswith("ANTHROPIC_")])

    async def test_failure_log_has_no_key(self):
        class Boom(FakeClient):
            async def connect(self):
                raise RuntimeError("connect failed with " + KEY)
        self.fake.ClaudeSDKClient = Boom
        s = await self.start()
        e = await self.wait_state(s["id"], "error")
        self.assertNotIn(KEY, json.dumps(e))
        self.assertNotIn(KEY, self.stderr.getvalue())


class RestartTests(Base):
    async def test_working_becomes_interrupted(self):
        holder = {}
        FakeClient.script = perm_script(holder, then=False)
        s = await self.start()
        await self.wait_state(s["id"], "blocked")
        await self.runner.close()
        with open(self.state) as f:
            self.assertIn(s["id"], f.read())
        await self.new_runner()
        got = (await self.ok("session.get", id=s["id"]))["session"]
        self.assertEqual((got["state"], got["pending"]), ("interrupted", []))
        self.assertTrue(got["detail"])
        FakeClient.script = None
        await self.ok("session.send", id=s["id"], text="back")
        await self.wait_state(s["id"], "idle")
        self.assertEqual(FakeClient.instances[-1].options.kw["resume"], s["session_id"])


class EventTests(Base):
    async def test_multiple_subscribers_and_dead_one(self):
        r1, w1 = await self.subscribe()
        r2, w2 = await self.subscribe()
        w1.close()
        await asyncio.sleep(0.05)
        s = await self.start()
        created = await self.event(r2, "session.created")
        self.assertEqual(created["session"]["id"], s["id"])
        st = await self.event(r2, "session.state")
        self.assertEqual(st["session"]["id"], s["id"])
        await self.wait_state(s["id"], "idle")

    async def test_bad_requests(self):
        await self.err("nope.method", "bad_request")
        r, w = await asyncio.open_unix_connection(self.sock)
        w.write(b"not json\n")
        await w.drain()
        self.assertEqual(json.loads(await r.readline())["error"]["code"], "bad_request")
        w.close()


class KillTests(unittest.IsolatedAsyncioTestCase):
    async def test_kill_tree_kills_descendants(self):
        p = await asyncio.create_subprocess_exec(
            "sh", "-c", "sleep 60 & sleep 60; true")
        await asyncio.sleep(0.3)
        kids = sdk_runner._children_map().get(p.pid, [])
        self.assertTrue(kids)
        sdk_runner.kill_tree(p.pid)
        await asyncio.wait_for(p.wait(), 3)
        await asyncio.sleep(0.2)
        for k in kids:
            self.assertFalse(os.path.exists("/proc/%d/cmdline" % k) and
                             open("/proc/%d/stat" % k).read().split(")")[1].split()[0] != "Z")

    def test_client_pid(self):
        c = types.SimpleNamespace(_transport=types.SimpleNamespace(_process=types.SimpleNamespace(pid=4242)))
        self.assertEqual(sdk_runner.client_pid(c), 4242)
        self.assertIsNone(sdk_runner.client_pid(types.SimpleNamespace()))

    def test_read_key_quotes(self):
        with tempfile.NamedTemporaryFile("w", suffix=".env") as f:
            f.write('# c\nexport A="q1"\nB=plain\n')
            f.flush()
            self.assertEqual(sdk_runner.read_key(f.name, "A"), "q1")
            self.assertEqual(sdk_runner.read_key(f.name, "B"), "plain")
            self.assertIsNone(sdk_runner.read_key(f.name, "C"))


class PsFallbackTests(unittest.TestCase):
    """No /proc (macOS): the process tree comes from ps."""
    PS = ("  1     0 Mon Nov  3 03:00:00 2026\n 10     1 Mon Nov  3 03:01:00 2026\n"
          " 11    10 Mon Nov  3 03:02:00 2026\n 12    10 Mon Nov  3 03:03:00 2026\ngarbage\n")

    def patched(self, out):
        real = os.path.exists
        stack = contextlib.ExitStack()
        stack.enter_context(unittest.mock.patch.object(os.path, "exists", lambda p: False if p == "/proc/self" else real(p)))
        stack.enter_context(unittest.mock.patch.object(sdk_runner, "_ps", lambda *a: out(a)))
        self.addCleanup(stack.close)

    def test_ps_children(self):
        calls = []
        self.patched(lambda a: calls.append(a) or self.PS)
        self.assertEqual(sdk_runner._children_map(), {0: [1], 1: [10], 10: [11, 12]})
        self.assertEqual(calls, [("-A", "-o", "pid=,ppid=,lstart=")])

    def test_ps_starttime_and_cli(self):
        self.patched(lambda a: {"lstart=": "Mon Nov  3 03:01:00 2026\n", "command=": "/x/claude --print\n"}[a[-1]])
        self.assertEqual(sdk_runner._starttime(10), "Mon Nov  3 03:01:00 2026")
        self.assertTrue(sdk_runner.is_our_cli(10))


if __name__ == "__main__":
    unittest.main()
