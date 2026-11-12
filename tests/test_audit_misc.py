"""Audit batch: S10 push fallback, S22 login limit, S23 ask cache, S25 CI, S26 tmux quoting, S27 safe open,
S28 custom key/redirects, S29 Telegram outside _plock."""
import http.server
import json
import os
import shlex
import sys
import tempfile
import threading
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import zidos_pager as cc  # noqa: E402
from test_zidos_pager import Env, FakeSdk, SID, perm_req, sdk_env  # noqa: E402


def perm_msgs(e):
    return [p for p in e.tg.of("sendMessage") if "🔐" in p["text"]]


class PushFallbackTests(unittest.TestCase):  # S10
    def setUp(self):
        self.now = [1000.0]
        self.e = sdk_env(self, FakeSdk(self))
        self.e.bot.clock = lambda: self.now[0]
        self.e.bot.push_notify = lambda *a, **k: True  # the push service accepts everything
        self.e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})

    def test_accepted_push_is_followed_by_telegram_when_nobody_looks(self):
        self.assertEqual(perm_msgs(self.e), [])
        self.now[0] += cc.PERM_PUSH_FALLBACK - 1
        self.e.bot.perm_push_fallback()
        self.assertEqual(perm_msgs(self.e), [])
        self.now[0] += 2
        self.e.bot.perm_push_fallback()
        self.assertEqual(len(perm_msgs(self.e)), 1)
        self.now[0] += 500
        self.e.bot.perm_push_fallback()
        self.assertEqual(len(perm_msgs(self.e)), 1)  # once

    def test_mini_app_touch_cancels_the_fallback(self):
        self.now[0] += 60
        self.e.bot.webapp_touch[SID] = self.now[0]
        self.now[0] += cc.PERM_PUSH_FALLBACK + 1
        self.e.bot.perm_push_fallback()
        self.assertEqual(perm_msgs(self.e), [])

    def test_closed_request_is_not_sent(self):
        self.e.bot.sdk_on_event({"event": "request.closed", "data": {"id": SID, "req": "r-00000001", "decision": "allow"}})
        self.now[0] += cc.PERM_PUSH_FALLBACK + 1
        self.e.bot.perm_push_fallback()
        self.assertEqual(perm_msgs(self.e), [])

    def test_zero_keeps_the_old_behaviour(self):
        e = sdk_env(self, FakeSdk(self))
        e.bot.clock = lambda: self.now[0]
        e.bot.push_notify = lambda *a, **k: True
        with mock.patch.object(cc, "PERM_PUSH_FALLBACK", 0):
            e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req(req="r-2")}})
            self.now[0] += 10000
            e.bot.perm_push_fallback()
        self.assertEqual(perm_msgs(e), [])

    def test_notify_tick_runs_the_fallback(self):
        with mock.patch.object(self.e.bot, "perm_push_fallback") as fb, mock.patch.object(self.e.bot, "_notify_tick"):
            self.e.bot.notify_tick()
        fb.assert_called_once()


class LoginLimitTests(unittest.TestCase):  # S22
    def test_cf_header_only_from_loopback(self):
        self.assertEqual(cc.client_ip("127.0.0.1", "1.2.3.4"), "1.2.3.4")
        self.assertEqual(cc.client_ip("::1", "1.2.3.4"), "1.2.3.4")
        self.assertEqual(cc.client_ip("10.0.0.5", "1.2.3.4"), "10.0.0.5")
        self.assertEqual(cc.client_ip("10.0.0.5", None), "10.0.0.5")
        self.assertEqual(cc.client_ip("127.0.0.1", None), "127.0.0.1")

    def test_attempts_dict_is_cleaned_and_failure_does_not_save(self):
        e = Env(self)
        now = [100.0]
        e.bot.clock = lambda: now[0]
        with mock.patch.object(e.bot.store, "save") as save:
            for i in range(5):
                self.assertEqual(e.bot.api_login("9.9.9.%d" % i, "nope")[0], 401)
            save.assert_not_called()
        self.assertEqual(len(e.bot.login_attempts), 5)
        now[0] += 61
        e.bot.api_login("8.8.8.8", "nope")
        self.assertEqual(list(e.bot.login_attempts), ["8.8.8.8"])


class AskCacheTests(unittest.TestCase):  # S23
    def test_unchanged_transcript_is_read_once_and_key_includes_path(self):
        e = Env(self)
        a, b = (os.path.join(e.tmp.name, n) for n in ("a.jsonl", "b.jsonl"))
        for p in (a, b):
            with open(p, "w") as f:
                f.write("x" * 10)
            os.utime(p, (5, 5))
        cur = [a]
        e.bot.transcript_path = lambda sid: cur[0]
        s = {"id": "sess1", "state": "working"}
        with mock.patch.object(cc, "unanswered_ask", return_value=None) as ua:
            e.bot.ask_parts(s)
            e.bot.ask_parts(s)
            self.assertEqual(ua.call_count, 1)
            cur[0] = b  # same size and mtime, other file
            e.bot.ask_parts(s)
            self.assertEqual(ua.call_count, 2)


class TmuxQuoteTests(unittest.TestCase):  # S26
    def test_path_with_space_survives_shlex(self):
        calls = []
        t = cc.Tmux("/opt/my tools/claude", lambda a, **k: (calls.append(a), (0, ""))[1], lambda s: None)
        self.assertTrue(t._open_window("s-1a2b3c4d"))
        self.assertEqual(shlex.split(calls[0][-1]), ["/opt/my tools/claude", "attach", "s-1a2b3c4d"])


class SafeOpenTests(unittest.TestCase):  # S27
    def setUp(self):
        self.d = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(__import__("shutil").rmtree, self.d, True)
        self.home = tempfile.mkdtemp(dir="/tmp")
        self.addCleanup(__import__("shutil").rmtree, self.home, True)
        self.f = os.path.join(self.d, "a.txt")
        with open(self.f, "w") as fh:
            fh.write("hello")

    def test_plain_file_and_allowed_symlink_open(self):
        link = os.path.join(self.d, "l.txt")
        os.symlink(self.f, link)
        for p in (self.f, link):
            with cc.open_file_ok(p, self.home) as fh:
                self.assertEqual(fh.read(), b"hello")

    def test_hard_link_is_refused(self):
        os.link(self.f, os.path.join(self.d, "h.txt"))
        self.assertIsNone(cc.open_file_ok(self.f, self.home))

    def test_symlink_swapped_after_the_check_is_refused(self):
        link = os.path.join(self.d, "swap.txt")
        os.symlink(self.f, link)
        # the check saw a good file; by the time of open the real path is the symlink itself
        with mock.patch.object(cc, "file_ok", return_value=(link, 5)), \
                mock.patch.object(cc.os.path, "realpath", side_effect=lambda p: p):
            self.assertIsNone(cc.open_file_ok(link, self.home))

    def test_symlink_to_forbidden_target_is_refused(self):
        secret = os.path.join(self.home, ".ssh_key")
        with open(secret, "w") as fh:
            fh.write("k")
        link = os.path.join(self.d, "s.txt")
        os.symlink(secret, link)
        with mock.patch.object(cc, "file_ok", return_value=(link, 1)):
            self.assertIsNone(cc.open_file_ok(link, self.home))

    def test_api_sendfile_refuses_hard_link(self):
        e = Env(self)
        os.link(self.f, os.path.join(self.d, "h2.txt"))
        code, obj = e.bot.api_sendfile(1, SID, self.f)
        self.assertNotEqual(code, 200)
        self.assertEqual(e.tg.of("sendDocument"), [])


class _Redirect(http.server.BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        _Redirect.seen.append((self.server.server_port, self.headers.get("x-api-key")))
        if self.server.server_port == _Redirect.first:
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:%d/v1/messages" % _Redirect.second)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

    do_GET = do_POST

    def log_message(self, *a):
        pass


class CustomKeyTests(unittest.TestCase):  # S28
    def test_redirect_does_not_carry_the_key(self):
        _Redirect.seen = []
        srvs = [http.server.HTTPServer(("127.0.0.1", 0), _Redirect) for _ in range(2)]
        _Redirect.first, _Redirect.second = (s.server_port for s in srvs)
        for s in srvs:
            threading.Thread(target=s.serve_forever, daemon=True).start()
            self.addCleanup(s.server_close)
            self.addCleanup(s.shutdown)
        verdict = cc.model_check("http://127.0.0.1:%d" % _Redirect.first, "SECRET", "m")
        self.assertEqual(verdict, "net")
        self.assertEqual([p for p, _ in _Redirect.seen], [_Redirect.first])

    def save(self, e, base):
        cc.env_update(e.bot.model_keys_path(), {"CUSTOM_API_KEY": "stored-key-123456"})
        cc.env_update(e.cfg.sdk_env, {"CC_SDK_BASE_URL": "https://mine.example/api"})
        with mock.patch.object(cc, "model_check", return_value="net") as mc:
            res = e.bot.api_model_save({"provider": "custom", "base": base, "model": "m1"})
        return res, mc

    def test_stored_key_is_not_sent_to_another_base(self):
        e = Env(self)
        res, mc = self.save(e, "https://evil.example/api")
        self.assertEqual(res, (200, {"ok": False, "error": "key"}))
        mc.assert_not_called()

    def test_stored_key_goes_to_the_saved_base(self):
        e = Env(self)
        res, mc = self.save(e, "https://mine.example/api")
        mc.assert_called_once()
        self.assertEqual(mc.call_args[0][:2], ("https://mine.example/api", "stored-key-123456"))


class PlockTests(unittest.TestCase):  # S29
    def test_slow_telegram_call_does_not_hold_plock(self):
        e = Env(self)
        started, release = threading.Event(), threading.Event()

        def slow_send(*a, **k):
            started.set()
            release.wait(10)
            return {"message_id": 7}
        e.bot.tg.send_rich = slow_send
        t = threading.Thread(target=e.bot.panel_publish, args=(1,))
        t.start()
        self.addCleanup(t.join, 10)
        self.addCleanup(release.set)
        self.assertTrue(started.wait(5))
        got = []

        def other():
            got.append(e.bot._plock.acquire(timeout=1))
            if got[0]:
                e.bot._plock.release()
        o = threading.Thread(target=other)
        o.start()
        o.join(5)
        self.assertEqual(got, [True])


class CiTests(unittest.TestCase):  # S25
    def test_workflow_has_cheap_checks(self):
        with open(os.path.join(HERE, "..", ".github", "workflows", "tests.yml"), encoding="utf-8") as f:
            y = f.read()
        self.assertIn("py_compile", y)
        self.assertIn("node --check", y)


if __name__ == "__main__":
    unittest.main()
