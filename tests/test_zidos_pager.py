import base64
import json
import socket
import re
import struct
import os
import sys
import tempfile
import threading
import time
import unittest
import types
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import zidos_pager as cc  # noqa: E402

cc.set_lang("ru")  # the suite checks the original (Russian) texts; language switching has its own tests


def line(obj):
    return (json.dumps(obj, ensure_ascii=False) + "\n").encode()


def asst(uuid, *blocks, side=False):
    return line({"type": "assistant", "isSidechain": side, "uuid": uuid,
                 "message": {"content": [dict(type="text", text=b) if isinstance(b, str) else b for b in blocks]}})


class FakeTG(cc.TG):
    """Настоящий TG, но сеть подменена: записывает вызовы."""

    def __init__(self):
        super().__init__("T", "http://x")
        self.calls = []
        self.n = 100
        self.fail = {}
        self.files = {}
        self.errors = {}
        self.topics = True

    def _open(self, method, req, timeout):
        ct = req.get_header("Content-type") or ""
        params = json.loads(req.data) if ct == "application/json" else {"raw": req.data}
        self.calls.append((method, params))
        f = self.fail.get(method)
        if f and f(params):
            raise cc.TGError("%s: HTTP 400 %s" % (method, "Bad Request: can't parse entities"))
        if method == "getFile":
            f = self.files.get(params.get("file_id"))
            if not f:
                raise cc.TGError("getFile: HTTP 400 Bad Request: wrong file_id")
            self.n += 1
            return {"file_path": f[0], "file_size": f[2] if len(f) > 2 else len(f[1])}
        err = self.errors.get(method)
        if err and err[0](params):
            raise cc.TGError("%s: HTTP 400 %s" % (method, err[1]))
        self.n += 1
        if method == "createForumTopic":
            return {"message_thread_id": 500 + self.n}
        if method == "getMe":
            return {"has_topics_enabled": self.topics}
        if method in ("deleteMessages", "deleteForumTopic", "editForumTopic", "pinChatMessage", "deleteMessage",
                      "editMessageReplyMarkup", "answerCallbackQuery", "sendChatAction", "setMessageReaction"):
            return True
        return {"message_id": self.n}

    def fetch_file(self, file_path, limit=cc.TG_DL_LIMIT):
        for f in self.files.values():
            if f[0] == file_path:
                if len(f[1]) > limit:
                    raise cc.TGError("download: file is too big")
                return f[1]
        raise cc.TGError("download: not found")

    def of(self, method):
        return [p for m, p in self.calls if m == method]


class Env:
    def __init__(self, tc):
        self.tmp = tempfile.TemporaryDirectory()
        tc.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.jobs = os.path.join(t, "jobs")
        os.makedirs(self.jobs)
        envf = os.path.join(t, "env")
        with open(envf, "w") as f:
            f.write("TG_BOT_TOKEN=T\nTG_ALLOWED_USERS=1\nCC_VAPID_SUB=mailto:admin@example.com\nCC_JOBS_DIR=%s\nZIDOS_PAGER_STATE=%s\nCC_DEFAULT_CWD=%s\nCC_RATE_FILE=%s\n"
                    % (self.jobs, os.path.join(t, "st", "state.json"), t, os.path.join(t, "rate.json")))
        self.cfg = cc.Config(env_path=envf, environ={"HOME": t})
        self.agents = []
        self.runs = []
        self.tg = FakeTG()
        self.sent = []
        dead_tmux = cc.Tmux("claude", lambda *a, **k: (1, ""), lambda s: None)  # реальный tmux не трогаем
        self.bot = cc.Bot(self.cfg, tg=self.tg, runner=self.runner, tmux=dead_tmux,
                          spawn=lambda fn, *a: fn(*a), sleep=lambda s: None)
        self.bot.injector = self.inject
        self.bot.claude_missing = lambda: False  # these tests model a machine with Claude Code
        self.inject_result = (True, "")

    def runner(self, args, timeout=30, cwd=None, input_text=None):
        self.runs.append((args, cwd))
        if "agents" in args:
            return 0, json.dumps(self.agents)
        if "--bg" in args:
            return 0, "backgrounded · abcd1234 · Новая\n"
        return 0, ""

    def inject(self, sid, text):
        self.sent.append((sid, text))
        return self.inject_result

    def job(self, sid, **st):
        os.makedirs(os.path.join(self.jobs, sid), exist_ok=True)
        with open(os.path.join(self.jobs, sid, "state.json"), "w") as f:
            json.dump(st, f)

    def msg(self, text, mid=1, reply=None, uid=1, ctype="private", thread=None):
        m = {"message_id": mid, "from": {"id": uid}, "chat": {"id": uid, "type": ctype}, "text": text}
        if thread:
            m["message_thread_id"] = thread
            m["is_topic_message"] = True
        if reply:
            m["reply_to_message"] = {"message_id": reply}
        return {"update_id": mid, "message": m}

    def cb(self, data, mid=50, text="orig"):
        return {"update_id": 9, "callback_query": {"id": "q", "from": {"id": 1}, "data": data,
                                                    "message": {"message_id": mid, "text": text,
                                                                "chat": {"id": 1, "type": "private"}}}}


class TranscriptTests(unittest.TestCase):
    def test_texts_and_skips(self):
        data = (asst("u1", "привет", {"type": "tool_use", "name": "x"}, "второй блок")
                + asst("u2", "сайдчейн", side=True)
                + line({"type": "user", "message": {"content": "вопрос"}})
                + asst("u3", {"type": "tool_use"}) + asst("u4", "   "))
        texts, used = cc.parse_transcript_chunk(data)
        self.assertEqual(texts, [("u1", "привет\n\nвторой блок")])
        self.assertEqual(used, len(data))

    def test_partial_last_line_and_offsets(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.jsonl")
            full = asst("a", "один")
            second = asst("b", "два")
            with open(p, "wb") as f:
                f.write(full + second[:10])
            texts, off = cc.read_new_texts(p, 0)
            self.assertEqual([t for _, t in texts], ["один"])
            self.assertEqual(off, len(full))
            with open(p, "ab") as f:
                f.write(second[10:])
            texts, off2 = cc.read_new_texts(p, off)
            self.assertEqual([t for _, t in texts], ["два"])
            self.assertEqual(off2, len(full) + len(second))
            self.assertEqual(cc.read_new_texts(p, off2), ([], off2))
            self.assertEqual([t for _, t in cc.tail_texts(p, 1)], ["два"])

    def test_fallback_path(self):
        self.assertEqual(cc.fallback_transcript_path("/home/user/.claude/jobs/x", "sid", "/h"),
                         "/h/.claude/projects/-home-user--claude-jobs-x/sid.jsonl")


class SessionTests(unittest.TestCase):
    def test_merge_sort_missing_id_and_broken_state(self):
        e = Env(self)
        e.job("a", state="done", detail="d", updatedAt="2026-10-14T10:00:00Z", output={"result": "ok"})
        e.job("b", state="working", updatedAt="2026-10-14T11:00:00Z")
        e.job("c", state="blocked", detail="ждёт", updatedAt="2026-10-14T09:00:00Z")
        os.makedirs(os.path.join(e.jobs, "f"))
        with open(os.path.join(e.jobs, "f", "state.json"), "w") as fh:
            fh.write("{битый")
        agents = [{"id": "a", "name": "A", "state": "done"}, {"id": "b", "name": "B", "state": "working"},
                  {"id": "c", "name": "C", "state": "blocked"}, {"name": "без id"},
                  {"id": "f", "name": "F", "state": "failed"}, {"id": "z", "name": "Z", "state": "working"}]
        s = cc.sort_sessions(cc.merge_sessions(agents, e.jobs))
        self.assertEqual([x["id"] for x in s], ["c", "b", "z", "f", "a"])
        self.assertEqual(s[-1]["result"], "ok")


class TextTests(unittest.TestCase):
    def test_split_message(self):
        text = "\n".join("строка %03d" % i for i in range(1000))
        chunks = cc.split_message(text, 4000)
        self.assertTrue(len(chunks) > 1 and all(len(c) <= 4000 for c in chunks))
        self.assertEqual("\n".join(chunks), text)
        self.assertTrue(all(len(c) <= 4000 for c in cc.split_message("я" * 9000)))
        self.assertEqual(cc.split_message("  "), [])

    def test_parse_new(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(cc.parse_new_args(d + " сделай X", "/def"), (d, "сделай X"))
            self.assertEqual(cc.parse_new_args("/нет/такой сделай", "/def"), ("/def", "/нет/такой сделай"))
            self.assertEqual(cc.parse_new_args("просто текст", "/def"), ("/def", "просто текст"))
            self.assertEqual(cc.parse_new_args("", "/def"), ("/def", ""))

    def test_transitions(self):
        s = lambda i, st: {"id": i, "state": st}
        out, prev = cc.find_transitions({}, [s("a", "working"), s("b", "done")])
        self.assertEqual([x["id"] for x in out], ["b"])
        out, prev = cc.find_transitions(prev, [s("a", "blocked"), s("b", "done")])
        self.assertEqual([x["id"] for x in out], ["a"])
        out, prev = cc.find_transitions(prev, [s("a", "blocked"), s("b", "done")])
        self.assertEqual(out, [])

    def test_md_to_html(self):
        h = cc.md_to_html("# Заголовок\nТекст **жирный** и `a<b` & [ссылка](https://x.io/?a=1&b=2)\n"
                          "```python\nif a < b: print('&')\n```\nконец <tag>")
        self.assertIn("<b>Заголовок</b>", h)
        self.assertIn("<b>жирный</b>", h)
        self.assertIn("<code>a&lt;b</code>", h)
        self.assertIn('<a href="https://x.io/?a=1&amp;b=2">ссылка</a>', h)
        self.assertIn("<pre>if a &lt; b: print('&amp;')</pre>", h)
        self.assertIn("конец &lt;tag&gt;", h)
        self.assertNotIn("**", h)

    def test_md_bare_urls_link_in_web(self):
        h = cc.md_to_html("Превью: **https://go.example.ru/preview/a-b/index.html** и https://x.io/?a=1&b=2.\n"
                          "[тут](https://y.io/p) `https://code.io` (см. https://z.io/q)", web=True)
        self.assertIn('<b><a href="https://go.example.ru/preview/a-b/index.html" rel="noopener noreferrer" '
                      'target="_blank">https://go.example.ru/preview/a-b/index.html</a></b>', h)
        self.assertIn('<a href="https://x.io/?a=1&amp;b=2" rel="noopener noreferrer" target="_blank">'
                      'https://x.io/?a=1&amp;b=2</a>.', h)  # точка в конце предложения — не часть ссылки
        self.assertIn('<a href="https://y.io/p" rel="noopener noreferrer" target="_blank">тут</a>', h)
        self.assertEqual(h.count("https://y.io/p"), 1)  # markdown-ссылка не оборачивается второй раз
        self.assertIn("<code>https://code.io</code>", h)
        self.assertIn('target="_blank">https://z.io/q</a>)', h)
        self.assertNotIn("<a ", cc.md_to_html("https://x.io"))  # Telegram сам делает адреса ссылками
        self.assertIn('&lt;<a href="https://x.ru" rel="noopener noreferrer" target="_blank">https://x.ru</a>&gt;',
                      cc.md_to_html("<https://x.ru>", web=True))  # an escaped «>» is not part of the address

    def test_md_table(self):
        md = ("Насколько слабее\n| | Сервер | N5 Air |\n|---|:---:|---:|\n| CPU | **16** ядер | 8 ядер |\n"
              "| RAM | `96 ГБ` | 32 <ГБ> |\nЦифры — оценка.")
        h = cc.md_to_html(md, web=True)
        self.assertIn('<div class="tbl"><table><thead><tr><th></th><th style="text-align:center">Сервер</th>'
                      '<th style="text-align:right">N5 Air</th></tr></thead><tbody>', h)
        self.assertIn("<td>CPU</td><td style=\"text-align:center\"><b>16</b> ядер</td>", h)
        self.assertIn("<code>96 ГБ</code>", h)
        self.assertIn("32 &lt;ГБ&gt;", h)
        self.assertNotIn("|---", h)
        self.assertIn("Насколько слабее\n<div", h)
        self.assertIn("</table></div>Цифры", h)  # строки таблицы не оставляют пустых строк в pre-wrap
        self.assertNotIn("\n", h[h.index("<div"):h.index("</div>")])
        tg = cc.md_to_html(md)  # Telegram: таблиц нет — моноширинный блок
        self.assertIn("<pre>| | Сервер | N5 Air |", tg)
        self.assertNotIn("<table", tg)
        # не таблица: одиночная строка с | и таблица внутри ``` не трогаются
        self.assertEqual(cc.md_to_html("a | b", web=True), "a | b")
        self.assertIn('<th style="text-align:right">b</th>', cc.md_to_html("| a | b |\n|:-|--:|\n| 1 | 2 |", web=True))  # GFM: one dash is enough
        self.assertIn("<pre>| a |\n|---|</pre>", cc.md_to_html("```\n| a |\n|---|\n```", web=True))

    def test_split_markdown_reopens_fence(self):
        code = "\n".join("x = %d" % i for i in range(60))
        chunks = cc.split_markdown("вступление\n```py\n" + code + "\n```\nхвост", 200)
        self.assertTrue(len(chunks) > 2)
        for c in chunks:
            self.assertEqual(c.count("```") % 2, 0, c)
            self.assertLessEqual(len(c), 200)
        self.assertTrue(chunks[1].startswith("```py"))
        for c, h in cc.render_chunks("```\n" + "<&>" * 3000 + "\n```"):
            self.assertLessEqual(len(h), 4096)


GHOST_PANE = "──────\n❯\xa0\x1b[7mО\x1b[27m\x1b[2mтветь одним словом: четыре\x1b[0m\n──────\n"


class TmuxTests(unittest.TestCase):
    def make(self, pane_after_paste):
        calls = []
        state = {"pasted": False}

        def runner(args, timeout=30, cwd=None, input_text=None):
            calls.append(args)
            if args[:2] == ["tmux", "has-session"]:
                return 0, ""
            if args[:2] == ["tmux", "paste-buffer"]:
                state["pasted"] = True
            if args[:2] == ["tmux", "capture-pane"]:  # с -e и без
                return 0, pane_after_paste if state["pasted"] else GHOST_PANE
            return 0, ""
        clock = {"t": 0.0}

        def sleep(s):
            clock["t"] += s
        return cc.Tmux("claude", runner, sleep, lambda: clock["t"]), calls

    def test_enter_sent_when_text_visible(self):
        tm, calls = self.make("──────\n❯ привет, это мой текст\n──────\n")
        self.assertEqual(tm.send_text("abc12345", "привет, это мой текст\nвторая"), (True, ""))
        self.assertIn(["tmux", "send-keys", "-t", "cctg-abc12345", "Enter"], calls)

    def test_pasted_marker(self):
        tm, calls = self.make("──────\n❯ [Pasted text #1 +40 lines]\n──────\n")
        self.assertTrue(tm.send_text("abc12345", "очень длинное\n" * 50)[0])

    def test_ghost_suggestion_blocks_enter(self):
        tm, calls = self.make(GHOST_PANE)
        ok, err = tm.send_text("abc12345", "совсем другой текст")
        self.assertFalse(ok)
        self.assertIn("Enter не нажат", err)
        self.assertNotIn(["tmux", "send-keys", "-t", "cctg-abc12345", "Enter"], calls)

    def test_short_word_in_history_does_not_count(self):
        # «да» есть в истории выше, а в поле ввода только тусклая подсказка
        pane = ("❯ да\n● Принято\n\n"
                "\x1b[39m❯\xa0\x1b[2mда, продолжай\x1b[0m\n──────\n")
        tm, calls = self.make(pane)
        ok, err = tm.send_text("abc12345", "да")
        self.assertFalse(ok)
        self.assertNotIn(["tmux", "send-keys", "-t", "cctg-abc12345", "Enter"], calls)

    def test_real_input_line_counts(self):
        pane = ("❯ да\n● Принято\n\n\x1b[39m❯ \x1b[38;2;255;255;255mда\x1b[39m\n──────\n")
        tm, calls = self.make(pane)
        self.assertEqual(tm.send_text("abc12345", "да"), (True, ""))

    def test_empty_never_sends(self):
        tm, calls = self.make("")
        self.assertFalse(tm.send_text("abc12345", "  \n")[0])
        self.assertEqual(calls, [])

    def test_not_ready_message(self):
        def runner(args, **kw):
            return 0, "загрузка..."
        clock = {"t": 0.0}
        tm = cc.Tmux("claude", runner, lambda s: clock.__setitem__("t", clock["t"] + s), lambda: clock["t"])
        ok, err = tm.send_text("abc12345", "x")
        self.assertFalse(ok)
        self.assertIn("не появилась строка ввода за 40 с", err)

    def test_cleanup_exact_names(self):
        calls = []
        clock = {"t": 0.0}

        def runner(args, **kw):
            calls.append(args)
            return 0, "cctg-old\nother\ncctg-new\n" if args[1] == "ls" else ""
        tm = cc.Tmux("claude", runner, lambda s: None, lambda: clock["t"])
        tm.last_used["cctg-old"] = -2000
        tm.last_used["cctg-new"] = 0
        self.assertEqual(tm.cleanup(), ["cctg-old"])
        self.assertIn(["tmux", "kill-session", "-t", "cctg-old"], calls)
        self.assertEqual(sum(1 for c in calls if c[1] == "kill-session"), 1)


class BotTests(unittest.TestCase):
    def test_allowlist(self):
        e = Env(self)
        e.bot.handle_update(e.msg("/s", uid=2))
        e.bot.handle_update(e.msg("/s", ctype="group"))
        e.bot.handle_update(e.cb("lst"))  # свой, но проверим чужого ниже
        e.tg.calls.clear()
        u = e.cb("lst")
        u["callback_query"]["from"]["id"] = 5
        e.bot.handle_update(u)
        self.assertEqual(e.tg.calls, [])

    def test_keyboard_buttons_not_sent_to_session(self):
        e = Env(self)
        e.bot.make_current(1, "s1")
        for b in (cc.btn("list"), cc.btn("cur"), cc.btn("new"), cc.btn("screen")):
            e.bot.handle_update(e.msg(b))
        self.assertEqual(e.sent, [])
        self.assertTrue(e.tg.of("sendMessage"))

    def test_plain_text_goes_to_current_with_reaction(self):
        e = Env(self)
        e.bot.make_current(1, "s1")
        e.bot.handle_update(e.msg("сделай это", mid=7))
        self.assertEqual(e.sent, [("s1", "сделай это")])
        r = e.tg.of("setMessageReaction")[0]
        self.assertEqual((r["message_id"], r["reaction"]), (7, [{"type": "emoji", "emoji": "👀"}]))

    def test_reaction_failure_falls_back_to_short_reply(self):
        e = Env(self)
        e.job("s1", name="Альфа", state="working")
        e.bot.make_current(1, "s1")
        e.tg.fail["setMessageReaction"] = lambda p: True
        e.bot.handle_update(e.msg("текст", mid=7))
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "📨 → Альфа")

    def test_error_replies_with_screen_button(self):
        e = Env(self)
        e.bot.make_current(1, "s1")
        e.inject_result = (False, "Сессия не отвечает: не появилась строка ввода за 40 с. Откройте 📺 Экран.")
        e.bot.handle_update(e.msg("текст", mid=7))
        m = e.tg.of("sendMessage")[-1]
        self.assertIn("Сессия не отвечает", m["text"])
        self.assertEqual(m["reply_parameters"]["message_id"], 7)
        self.assertEqual(m["reply_markup"]["inline_keyboard"][0][0]["text"], "📺 Экран")

    def test_no_session_hint(self):
        e = Env(self)
        e.bot.handle_update(e.msg("привет"))
        self.assertEqual(e.sent, [])
        self.assertIn("Сначала выберите сессию", e.tg.of("sendMessage")[0]["text"])

    def test_reply_routes_to_non_current_session(self):
        e = Env(self)
        e.job("x", name="Икс", state="blocked")
        e.bot.make_current(1, "cur")
        e.bot.say(1, "🟡 Икс ждёт", sid="x")
        mid = e.tg.n
        e.bot.handle_update(e.msg("да", mid=30, reply=mid))
        self.assertEqual(e.sent, [("x", "да")])
        self.assertEqual(e.bot.store.chat(1)["session"], "cur")
        self.assertIn("x", e.bot.store.chat(1)["fw"])  # ответ от X придёт и без переключения
        e.bot.handle_update(e.msg("в текущую", mid=31))
        self.assertEqual(e.sent[-1], ("cur", "в текущую"))
        # карта сохраняется в state.json
        saved = json.load(open(e.cfg.state_path))
        self.assertEqual(saved["msgmap"]["1:%d" % mid], "x")

    def test_msgmap_limit(self):
        e = Env(self)
        for i in range(1100):
            e.bot.remember(1, [{"message_id": i}], "s")
        self.assertEqual(len(e.bot.store.data["msgmap"]), 1000)

    def test_new_dialog(self):
        e = Env(self)
        e.agents = [{"id": "a", "name": "A", "cwd": "/tmp", "state": "done"},
                    {"id": "b", "name": "B", "cwd": "/tmp", "state": "done"}]
        e.bot.handle_update(e.msg(cc.btn("new")))
        m = e.tg.of("sendMessage")[-1]
        self.assertEqual(m["text"], "Где запускать новую сессию?")
        btns = [r[0] for r in m["reply_markup"]["inline_keyboard"]]
        self.assertEqual(btns[-1]["text"], "✖ Отмена")
        self.assertEqual(len([b for b in btns if b["callback_data"].startswith("nd:")]), 2)
        e.bot.handle_update(e.cb("nd:1"))
        fr = e.tg.of("sendMessage")[-1]
        self.assertEqual(fr["reply_markup"]["input_field_placeholder"], "Что сделать?")
        self.assertEqual(e.sent, [])
        e.bot.handle_update(e.msg("Напиши тесты", mid=40))
        cmd, cwd = [r for r in e.runs if "--bg" in r[0]][-1]
        self.assertEqual(cmd[1:6], ["--bg", "--agent", "claude", "--model", "opus"])
        self.assertEqual((cmd[-1], cwd), ("Напиши тесты", "/tmp"))
        self.assertEqual(e.bot.store.chat(1)["session"], "abcd1234")
        self.assertIsNone(e.bot.store.chat(1)["pending_new"])
        self.assertEqual(e.sent, [])

    def test_new_with_args_immediate_and_cancel(self):
        e = Env(self)
        e.bot.handle_update(e.msg("/new сделай что-то"))
        self.assertEqual([r for r in e.runs if "--bg" in r[0]][-1][0][-1], "сделай что-то")
        e.bot.handle_update(e.msg(cc.btn("new")))
        e.bot.handle_update(e.cb("nx"))
        self.assertIsNone(e.bot.store.chat(1)["pending_new"])

    def test_markdown_parse_fallback(self):
        e = Env(self)
        e.tg.fail["sendMessage"] = lambda p: p.get("parse_mode") == "HTML"
        e.tg.send_md(1, "**жирный** текст", header="🔵 Сессия")
        sm = e.tg.of("sendMessage")
        self.assertEqual(sm[0]["parse_mode"], "HTML")
        self.assertNotIn("parse_mode", sm[1])
        self.assertEqual(sm[1]["text"], "🔵 Сессия\n\n**жирный** текст")

    def test_long_answer_goes_as_document(self):
        e = Env(self)
        e.tg.send_md(1, "слово " * 4000)
        self.assertEqual(len(e.tg.of("sendMessage")), 1)
        self.assertLessEqual(len(e.tg.of("sendMessage")[0]["text"]), 3700)
        doc = e.tg.of("sendDocument")[0]["raw"]
        self.assertIn(b'name="document"', doc)
        self.assertIn("слово слово".encode(), doc)

    def test_forwarder_header_dedupe_and_reply(self):
        e = Env(self)
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").write(asst("old", "старое"))
        e.job("s1", name="Альфа", state="working", linkScanPath=tr)
        e.bot.make_current(1, "s1")
        e.bot.handle_update(e.msg("вопрос", mid=7))
        with open(tr, "ab") as f:
            f.write(asst("n1", "ответ **жирно**") + asst("n1", "дубль"))
        e.bot.forward_tick()
        sm = e.tg.of("sendMessage")
        self.assertEqual(len(sm), 1)
        self.assertTrue(sm[0]["text"].startswith("🔵 Альфа\n\nответ <b>жирно</b>"))
        self.assertEqual(sm[0]["reply_parameters"]["message_id"], 7)
        e.bot.forward_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), 1)

    def test_status_message_lifecycle(self):
        e = Env(self)
        t = {"v": 1000.0}
        e.bot.clock = lambda: t["v"]
        e.job("s1", name="Альфа", state="working", detail="читает файл")
        e.bot.make_current(1, "s1")
        e.bot.handle_update(e.msg("go", mid=7))
        e.bot.status_tick()
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "🔵 Альфа работает: читает файл")
        self.assertEqual(len(e.tg.of("sendChatAction")), 1)
        e.job("s1", name="Альфа", state="working", detail="пишет код")
        t["v"] += 2
        e.bot.status_tick()
        self.assertEqual(e.tg.of("editMessageText"), [])  # не чаще раза в 5 с
        t["v"] += 4
        e.bot.status_tick()
        self.assertIn("пишет код", e.tg.of("editMessageText")[0]["text"])
        e.job("s1", name="Альфа", state="done")
        e.bot.status_tick()
        self.assertEqual(len(e.tg.of("deleteMessage")), 1)

    def test_notifications_first_pass_silent_and_once(self):
        e = Env(self)
        e.bot.set_watch(1, "a", True)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.agents = [{"id": "a", "name": "A", "state": "blocked"}]
        e.job("a", state="blocked", detail="ждёт «да»")
        e.bot.notify_tick()
        e.bot.notify_tick()
        sm = e.tg.of("sendMessage")
        self.assertEqual(len(sm), 1)
        self.assertEqual(sm[0]["text"], "🟡 A ждёт: ждёт «да»")
        kb = [b["text"] for r in sm[0]["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(kb, ["✅ Да", "❌ Нет", "💬 Ответить", "📺 Экран"])

    def test_first_pass_silent(self):
        e = Env(self)
        e.agents = [{"id": "a", "name": "A", "state": "blocked"}]
        e.bot.notify_tick()
        self.assertEqual(e.tg.calls, [])

    def test_done_notice_deduped_after_forward(self):
        e = Env(self)
        e.bot.set_watch(1, "a", True)
        t = {"v": 1000.0}
        e.bot.clock = lambda: t["v"]
        e.agents = [{"id": "a", "name": "Альфа", "state": "working"}]
        e.bot.notify_tick()
        e.bot.turn_forwarded.add("a")  # ответы этого хода уже пересланы
        e.job("a", state="done", output={"result": "длинный результат"})
        e.agents = [{"id": "a", "name": "Альфа", "state": "done"}]
        e.bot.notify_tick()
        self.assertEqual(e.tg.of("sendMessage"), [])  # «готово» не шлём вовсе

    def test_done_notice_full_when_not_forwarded(self):
        e = Env(self)
        e.bot.set_watch(1, "a", True)
        e.agents = [{"id": "a", "name": "Альфа", "state": "working"}]
        e.bot.notify_tick()
        e.job("a", state="done", output={"result": "итог"})
        e.agents = [{"id": "a", "name": "Альфа", "state": "done"}]
        e.bot.notify_tick()
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "✅ Альфа: итог")

    def test_watch_toggle_and_default_silence(self):
        e = Env(self)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.agents = [{"id": "a", "name": "A", "state": "failed"}]
        e.bot.notify_tick()
        self.assertEqual(e.tg.of("sendMessage"), [])  # по умолчанию уведомлений нет
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.bot.handle_update(e.cb("watch:a"))
        self.assertTrue(e.bot.is_watched("a"))
        self.assertEqual(e.tg.of("answerCallbackQuery")[-1]["text"], "Уведомления включены")
        e.agents = [{"id": "a", "name": "A", "state": "failed"}]
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), 1)
        e.bot.handle_update(e.cb("watch:a"))
        self.assertFalse(e.bot.is_watched("a"))

    def test_quick_yes_sends_and_strips_buttons(self):
        e = Env(self)
        e.bot.handle_update(e.cb("qk:a:y", mid=60, text="🟡 A ждёт: ок?"))
        self.assertEqual(e.sent, [("a", "да")])
        self.assertTrue(e.tg.of("editMessageReplyMarkup"))
        self.assertEqual(e.tg.of("editMessageText")[-1]["text"], "🟡 A ждёт: ок?\n→ отправлено: да")

    def test_screen_buttons_and_key(self):
        e = Env(self)
        panes = {"t": "❯ меню <1>\n\n\n"}
        keys = []

        def runner(args, timeout=30, cwd=None, input_text=None):
            if args[1] == "send-keys":
                keys.append(args[-1])
            if args[1] == "capture-pane":
                return 0, panes["t"]
            return 0, ""
        e.bot.tmux = cc.Tmux("claude", runner, lambda s: None)
        e.bot.make_current(1, "s1")
        e.bot.handle_update(e.msg(cc.btn("screen")))
        m = e.tg.of("sendMessage")[-1]
        self.assertEqual(m["parse_mode"], "HTML")
        self.assertEqual(m["text"], "<pre>❯ меню &lt;1&gt;</pre>")
        flat = [b["text"] for r in m["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(flat, ["1", "2", "3", "4", "↑", "↓", "⏎", "Esc", "🔄", "✖ Закрыть"])
        panes["t"] = "❯ после\n"
        e.bot.handle_update(e.cb("key:s1:dn", mid=77))
        self.assertEqual(keys, ["Down"])
        self.assertEqual(e.tg.of("editMessageText")[-1]["message_id"], 77)
        e.bot.handle_update(e.cb("cl", mid=77))
        self.assertEqual(e.tg.of("deleteMessage")[-1]["message_id"], 77)

    def test_card_buttons_and_current_mark(self):
        e = Env(self)
        e.job("s1", name="Альфа", state="blocked", detail="нужен ответ", cwd=os.path.expanduser("~/code"))
        e.agents = [{"id": "s1", "name": "Альфа", "state": "blocked"}]
        e.bot.handle_update(e.cb("s:s1"))
        m = [x for x in e.tg.of("sendMessage") if x["text"].endswith("· s1")][-1]
        self.assertIn("ждёт вас", m["text"])
        self.assertIn("▶ 🟡 Альфа", m["text"])
        self.assertTrue(m["text"].endswith("~/code · s1"))
        flat = [b["text"] for r in m["reply_markup"]["inline_keyboard"] for b in r]
        self.assertNotIn("💬 Писать сюда", flat)
        self.assertIn("🔔 Следить", flat)
        self.assertIn("📜 История", flat)
        self.assertEqual(flat[-1], "📋 Сессии")
        e.bot.make_current(1, "other")
        e.bot.send_card(1, "s1")
        flat = [b["text"] for r in e.tg.of("sendMessage")[-1]["reply_markup"]["inline_keyboard"] for b in r]
        self.assertIn("💬 Писать сюда", flat)

    def test_setmycommands(self):
        e = Env(self)
        e.tg.set_commands()
        cmds = [c["command"] for c in e.tg.of("setMyCommands")[0]["commands"]]
        self.assertEqual(cmds, ["s", "new", "quota", "screen", "stop", "pwa", "devices", "help"])


class ConfigTests(unittest.TestCase):
    def test_allowlist_only_from_env_and_quotes(self):
        with tempfile.TemporaryDirectory() as d:
            h = os.path.join(d, ".other-tool")
            os.makedirs(h)
            open(os.path.join(h, ".env"), "w").write('TELEGRAM_ALLOWED_USERS="11, 22"\n')  # чужие файлы не читаются
            envf = os.path.join(d, "env")
            open(envf, "w").write("TG_BOT_TOKEN='tok'\n")
            cfg = cc.Config(env_path=envf, environ={"HOME": d})
            self.assertEqual((cfg.token, cfg.allowed), ("tok", set()))
            open(envf, "w").write("TG_BOT_TOKEN='tok'\nTG_ALLOWED_USERS=\"11, 22\"\n")
            cfg = cc.Config(env_path=envf, environ={"HOME": d})
            self.assertEqual(cfg.allowed, {11, 22})
            self.assertEqual(cfg.api_base, "https://api.telegram.org")

    def test_defaults_are_relative_to_home(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cc.Config(env_path=os.path.join(d, "none"), environ={"HOME": d, "PATH": "/nonexistent"})
            self.assertEqual(cfg.default_cwd, d)
            self.assertEqual(cfg.claude_bin, os.path.join(d, ".local/bin/claude"))
            self.assertEqual(cfg.jobs_dir, os.path.join(d, ".claude/jobs"))
            self.assertEqual(cfg.state_path, os.path.join(d, ".local/state/zidos-pager/state.json"))
            self.assertEqual((cfg.lang, cfg.vapid_sub, cfg.chain_generic_dirs), ("en", "", []))
            fake = os.path.join(d, "bin")
            os.makedirs(fake)
            open(os.path.join(fake, "claude"), "w").write("#!/bin/sh\n")
            os.chmod(os.path.join(fake, "claude"), 0o755)
            cfg = cc.Config(env_path=os.path.join(d, "none"), environ={"HOME": d, "PATH": fake, "CC_LANG": "RU",
                                                                      "CC_VAPID_SUB": " mailto:a@example.com ",
                                                                      "CC_CHAIN_GENERIC_DIRS": "~/x:/srv/y"})
            self.assertEqual(cfg.claude_bin, os.path.join(fake, "claude"))  # shutil.which
            self.assertEqual((cfg.lang, cfg.vapid_sub), ("ru", "mailto:a@example.com"))
            self.assertEqual(cfg.chain_generic_dirs, [os.path.expanduser("~/x"), "/srv/y"])
            self.assertEqual(cc.Config(env_path=os.path.join(d, "none"), environ={"HOME": d, "CC_LANG": "de"}).lang, "en")

    def test_missing_allowlist_exit_code(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, "env")
            open(envf, "w").write("TG_BOT_TOKEN=T\n")
            p = subprocess.run([sys.executable, cc.__file__], capture_output=True, text=True,
                               env={"HOME": d, "ZIDOS_PAGER_ENV": envf})
            self.assertEqual(p.returncode, 2)
            self.assertIn("TG_ALLOWED_USERS", p.stderr)

    def test_no_token_exit_code(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            p = subprocess.run([sys.executable, cc.__file__], capture_output=True, text=True,
                               env={"HOME": d, "ZIDOS_PAGER_ENV": os.path.join(d, "none")})
            self.assertNotEqual(p.returncode, 0)
            self.assertIn("TG_BOT_TOKEN", p.stderr)


# ---------------------------------------------------------------- снимки экрана (фикстуры)

BORDER = "─" * 40
DIM = "\x1b[2m"
RST = "\x1b[0m"
EMPTY_PROMPT = "● готово\n%s\n❯\xa0\n%s\n  ? for shortcuts\n" % (BORDER, BORDER)
FOOTER_Y = "%s\n❯\xa0\n%s\n  Ctrl+Y to paste deleted text\n" % (BORDER, BORDER)
MENU_FOOT = "Enter to select · ↑/↓ to navigate · Esc to cancel\n"
PANEL_S = ("▔▔▔▔ Advisor Tool (experimental) is on\n   Settings  Status   Config   Usage   Stats\n\n"
           "   Current session\n   ████████▌        85% used\n   Resets 10:39am\n")
RESUME_PROMPT = ("● готово\n\n✻ Worked for 1m 20s · done Thursday 9:37 AM\n\n%s\n  Resume this conversation?\n\n"
                 "  This conversation has been inactive for 15h 31m and is 340.2k tokens long.\n\n"
                 "  ❯ 1. Resume\n    2. Start a new conversation  Claude can look this one up later\n\n"
                 "  Enter to confirm · Esc to resume\n" % BORDER)
MENU_S = ("Размер?\n\n❯ 1. S\n    маленький\n  2. M\n  3. L\n  4. Type something\n  5. Chat about this\n\n" + MENU_FOOT)
MENU_TABS = ("←  ☐ Размер  ☐ Добавки  ✔ Submit  →\n\nДобавки?\n\n"
             "❯ 1. [ ] Сыр\n  2. [ ] Лук\n  3. Type something\n  4. Chat about this\n\n" + MENU_FOOT)
MENU_TABS_CHECKED = MENU_TABS.replace("1. [ ] Сыр", "1. [✔] Сыр")
REVIEW = ("Review your answers\n\n ● Размер?\n   → S\n\nReady to submit your answers?\n\n"
          "❯ 1. Submit answers\n  2. Cancel\n\n" + MENU_FOOT)


class FakeScreen:
    """Подменный tmux: экран меняется по нажатиям клавиш и вставке."""

    def __init__(self, screen):
        self.screen = screen
        self.keys, self.calls = [], []
        self.on_key = self.on_paste = None

    def runner(self, args, timeout=30, cwd=None, input_text=None):
        self.calls.append(args)
        c = args[1]
        if c == "capture-pane":
            return 0, self.screen
        if c == "send-keys":
            ks = args[4:]
            self.keys.append(ks)
            if self.on_key:
                self.on_key(ks, self)
        elif c == "paste-buffer" and self.on_paste:
            self.on_paste(self)
        return 0, ""

    def tmux(self):
        clock = {"t": 0.0}
        return cc.Tmux("claude", self.runner, lambda s: clock.__setitem__("t", clock["t"] + s), lambda: clock["t"])

    @property
    def flat_keys(self):
        return [k for ks in self.keys for k in ks]


CLEAR_1 = ["C-e", "C-u", "BSpace", "C-e", "C-u"]


class MultiLineField:
    """Model of the Claude Code input field: C-u clears the current (last) line, BSpace on an empty line joins it."""

    def __init__(self, fs, lines, visible=True):
        self.fs, self.lines, self.visible = fs, list(lines), visible
        fs.on_key = self.on_key
        self.render()

    def set(self, lines):
        self.lines = list(lines)
        self.render()

    def on_key(self, ks, f):
        for k in ks:
            if k == "C-u":
                self.lines[-1] = ""
            elif k == "BSpace" and self.lines[-1] == "" and len(self.lines) > 1:
                self.lines.pop()
            elif k == "C-l":
                self.visible = True
        self.render()

    def render(self):
        if not self.visible:
            return
        rows = ["❯\xa0" if not self.lines[0] else "❯ " + self.lines[0]] + ["  " + l for l in self.lines[1:] if l]
        self.fs.screen = "● готово\n%s\n%s\n%s\n" % (BORDER, "\n".join(rows), BORDER)


class InputTextTests(unittest.TestCase):
    def test_empty_field_with_nbsp(self):
        self.assertEqual(cc.input_text(EMPTY_PROMPT), "")

    def test_ghost_suggestion_is_not_input(self):
        pane = "%s\n❯\xa0\x1b[7mО\x1b[27m%sтветь одним словом%s\n%s\n" % (BORDER, DIM, RST, BORDER)
        self.assertEqual(cc.input_text(pane), "")
        pane2 = "%s\n❯ %sподсказка целиком%s\n%s\n" % (BORDER, DIM, RST, BORDER)
        self.assertEqual(cc.input_text(pane2), "")

    def test_real_leftover_text(self):
        pane = "● ответ\n%s\n❯ старый текст\n%s\n" % (BORDER, BORDER)
        self.assertEqual(cc.input_text(pane), "старый текст")

    def test_multiline_input_merged(self):
        pane = "%s\n❯ первая строка\n  вторая строка\n  третья\n%s\n  ? for shortcuts\n" % (BORDER, BORDER)
        self.assertEqual(cc.input_text(pane), "первая строка\nвторая строка\nтретья")

    def test_ctrl_y_hint_is_not_input(self):
        self.assertEqual(cc.input_text(FOOTER_Y), "")
        self.assertEqual(cc.input_text("%s\n❯ текст   Ctrl+Y to paste deleted text\n%s\n" % (BORDER, BORDER)), "")

    def test_prompt_without_frame_below_is_not_input(self):
        self.assertEqual(cc.input_text("● ответ\n❯ старый запрос\n● ещё ответ\n"), "")
        self.assertFalse(cc.input_field_found("● ответ\n❯ старый запрос\n● ещё ответ\n"))
        self.assertTrue(cc.input_field_found(EMPTY_PROMPT))

    def test_history_prompt_ignored(self):
        pane = "❯ да\n● ок\n%s\n❯\xa0\n%s\n" % (BORDER, BORDER)
        self.assertEqual(cc.input_text(pane), "")

    def test_truecolor_param_two_is_not_dim(self):
        pane = "%s\n❯ \x1b[38;5;2mзелёный\x1b[39m\n%s\n" % (BORDER, BORDER)
        self.assertEqual(cc.input_text(pane), "зелёный")

    def test_menu_active(self):
        self.assertTrue(cc.menu_active(MENU_S))
        self.assertTrue(cc.menu_active(MENU_TABS))
        self.assertTrue(cc.menu_active(REVIEW))
        self.assertTrue(cc.menu_active("x\nвыбор\nto navigate · Esc to cancel"))
        self.assertFalse(cc.menu_active(EMPTY_PROMPT))
        self.assertFalse(cc.menu_active(FOOTER_Y))
        self.assertTrue(cc.pane_ready(MENU_S))

    def test_parse_marks(self):
        self.assertEqual(cc.parse_marks(MENU_TABS_CHECKED), {1: True, 2: False})
        self.assertEqual(cc.parse_marks(MENU_S), {})


class SafeSendTests(unittest.TestCase):
    def test_leftover_cleared_then_pasted_then_enter(self):
        fs = FakeScreen("%s\n❯ старый текст\n%s\n" % (BORDER, BORDER))
        fs.on_key = lambda ks, f: setattr(f, "screen", "%s\n❯\xa0\n%s\n" % (BORDER, BORDER)) \
            if "C-u" in ks else None
        fs.on_paste = lambda f: setattr(f, "screen", "%s\n❯ новый текст\n%s\n" % (BORDER, BORDER))
        self.assertEqual(fs.tmux().send_text("abc12345", "новый текст"), (True, ""))
        names = [c[1] for c in fs.calls]
        self.assertLess(names.index("send-keys"), names.index("paste-buffer"))
        self.assertEqual(fs.keys, [CLEAR_1, ["Enter"]])

    def test_multiline_leftover_cleared_line_by_line(self):
        fs = FakeScreen("")
        field = MultiLineField(fs, ["a", "b", "c"])
        fs.on_paste = lambda f: field.set(["привет мир"])
        self.assertTrue(fs.tmux().send_text("abc12345", "привет мир")[0])
        self.assertEqual(fs.keys[0], ["C-e", "C-u", "BSpace"] * 3 + ["C-e", "C-u"])
        self.assertEqual(field.lines, ["привет мир"])

    def test_clear_input_empties_multiline_field(self):
        fs = FakeScreen("")
        field = MultiLineField(fs, ["раз", "два", "", "три"])
        self.assertTrue(fs.tmux().clear_input("abc12345"))
        self.assertEqual(field.lines, [""])

    def test_not_cleared_gives_error_without_paste(self):
        fs = FakeScreen("%s\n❯ упрямый текст\n%s\n" % (BORDER, BORDER))
        ok, err = fs.tmux().send_text("abc12345", "новый")
        self.assertFalse(ok)
        self.assertEqual(err, cc.err_foreign_input())
        self.assertIn("чужой текст", err)
        self.assertEqual(fs.keys.count(CLEAR_1), 3)
        self.assertIn(["C-l"], fs.keys)
        self.assertNotIn("paste-buffer", [c[1] for c in fs.calls])
        self.assertNotIn(["Enter"], fs.keys)

    def test_redraw_fixes_stuck_foreign_text(self):
        fs = FakeScreen("%s\n❯ упрямый текст\n%s\n" % (BORDER, BORDER))
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT) if ks == ["C-l"] else None
        fs.on_paste = lambda f: setattr(f, "screen", "%s\n❯ новый\n%s\n" % (BORDER, BORDER))
        self.assertEqual(fs.tmux().send_text("abc12345", "новый"), (True, ""))
        self.assertIn(["C-l"], fs.keys)
        self.assertEqual(fs.keys[-1], ["Enter"])

    def test_resize_cures_garbled_screen_where_ctrl_l_does_not(self):
        # a session drawn for another client's width: «text» in the field that no key removes, Ctrl+L keeps it
        fs = FakeScreen("%s\n❯ ова            п\n%s\n" % (BORDER, BORDER))
        run = fs.runner

        def runner(args, timeout=30, cwd=None, input_text=None):
            if args[1] == "resize-window" and args[args.index("-x") + 1] == str(cc.PANE_W):
                fs.screen = EMPTY_PROMPT
            return run(args, timeout, cwd, input_text)
        fs.on_paste = lambda f: setattr(f, "screen", "%s\n❯ новый\n%s\n" % (BORDER, BORDER))
        t = cc.Tmux("claude", runner, lambda s: None, lambda: 0.0)
        self.assertEqual(t.send_text("abc12345", "новый"), (True, ""))
        widths = [c[c.index("-x") + 1] for c in fs.calls if c[1] == "resize-window"]
        self.assertEqual(widths, [str(cc.PANE_W - 1), str(cc.PANE_W)])  # there and back: the window keeps its size

    def test_unconfirmed_paste_revealed_by_redraw_is_sent(self):
        fs = FakeScreen(EMPTY_PROMPT)
        field = MultiLineField(fs, [""], visible=False)
        fs.on_paste = lambda f: field.set(["привет мир"])
        self.assertEqual(fs.tmux().send_text("abc12345", "привет мир"), (True, ""))
        self.assertEqual(fs.keys[-2:], [["C-l"], ["Enter"]])

    def test_unconfirmed_paste_is_removed_from_field(self):
        fs = FakeScreen(EMPTY_PROMPT)
        field = MultiLineField(fs, [""])
        fs.on_paste = lambda f: field.set(["совсем другое", "и ещё строка"])
        ok, err = fs.tmux().send_text("abc12345", "привет мир")
        self.assertFalse(ok)
        self.assertIn("Enter", err)
        self.assertNotIn(["Enter"], fs.keys)
        self.assertEqual(field.lines, [""])

    def test_no_input_field_redraws_then_errors(self):
        fs = FakeScreen("● ответ\n❯ да\n● ещё\n")  # ❯ only from history, no frame
        ok, err = fs.tmux().send_text("abc12345", "привет")
        self.assertEqual((ok, err), (False, cc.err_no_input_field()))
        self.assertIn("поле ввода", err)
        self.assertEqual(fs.keys, [["C-l"]])
        self.assertNotIn("paste-buffer", [c[1] for c in fs.calls])

    def test_no_input_field_found_after_redraw(self):
        fs = FakeScreen("● ответ\n❯ да\n● ещё\n")
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT) if ks == ["C-l"] else None
        fs.on_paste = lambda f: setattr(f, "screen", "%s\n❯ привет\n%s\n" % (BORDER, BORDER))
        self.assertEqual(fs.tmux().send_text("abc12345", "привет"), (True, ""))

    def test_menu_active_returns_menu_code_without_paste(self):
        fs = FakeScreen(MENU_S)
        self.assertEqual(fs.tmux().send_text("abc12345", "привет"), (False, cc.ERR_MENU))
        names = [c[1] for c in fs.calls]
        self.assertNotIn("paste-buffer", names)
        self.assertEqual(fs.keys, [])

    def test_paste_checked_against_input_not_history(self):
        fs = FakeScreen(EMPTY_PROMPT)
        # вставка «не дошла»: в истории выше есть тот же текст, а поле пустое
        fs.on_paste = lambda f: setattr(f, "screen", "❯ привет мир\n●ok\n" + EMPTY_PROMPT)
        ok, err = fs.tmux().send_text("abc12345", "привет мир")
        self.assertFalse(ok)
        self.assertNotIn(["Enter"], fs.keys)

    def test_ensure_accepts_menu_screen(self):
        fs = FakeScreen(MENU_S)
        ok, err = fs.tmux().ensure("abc12345")
        self.assertTrue(ok)
        self.assertEqual(fs.keys, [])  # a question menu is the owner's to answer

    def test_resume_prompt_confirmed_then_text_sent(self):
        # 23.10: a long idle session reopened with --resume asked «Resume this conversation?»;
        # the mini-app showed «pick an option above» with nothing above and every message bounced
        fs = FakeScreen(RESUME_PROMPT)
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT) if ks == ["Enter"] else None
        self.assertTrue(cc.resume_prompt_active(RESUME_PROMPT))
        ok, err = fs.tmux().ensure("abc12345")
        self.assertTrue(ok, err)
        self.assertEqual(fs.keys[0], ["Enter"])
        self.assertEqual(fs.screen, EMPTY_PROMPT)


# ---------------------------------------------------------------- вопросы

def ask_line(tool_id, questions, uuid="ask1"):
    return line({"type": "assistant", "isSidechain": False, "uuid": uuid, "message": {"content": [
        {"type": "text", "text": "Сейчас уточню"},
        {"type": "tool_use", "id": tool_id, "name": "AskUserQuestion", "input": {"questions": questions}}]}})


def answer_line(tool_id, content):
    return line({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": content}]}})


Q_SIZE = {"question": "Размер?", "header": "Размер", "multiSelect": False,
          "options": [{"label": "S", "description": "маленький"}, {"label": "M", "description": ""},
                      {"label": "L", "description": ""}]}
Q_TOP = {"question": "Добавки?", "header": "Добавки", "multiSelect": True,
         "options": [{"label": "Сыр", "description": ""}, {"label": "Лук", "description": ""}]}


class QuestionParseTests(unittest.TestCase):
    def test_ask_and_answer_events(self):
        data = (ask_line("toolu_1", [Q_SIZE, Q_TOP])
                + answer_line("toolu_1", 'User has answered: "Размер?"="S", "Добавки?"="Сыр, Лук"')
                + line({"type": "system", "subtype": "turn_duration", "durationMs": 5})
                + line({"type": "assistant", "isSidechain": True, "uuid": "x", "message": {"content": [
                    {"type": "tool_use", "id": "t9", "name": "AskUserQuestion", "input": {"questions": [Q_SIZE]}}]}}))
        ev, used = cc.parse_transcript_events(data)
        self.assertEqual(used, len(data))
        self.assertEqual([e["kind"] for e in ev], ["text", "ask", "answer", "turn_end"])
        self.assertEqual(ev[1]["tool_id"], "toolu_1")
        self.assertEqual(len(ev[1]["questions"]), 2)
        self.assertTrue(ev[1]["questions"][1]["multi"])
        self.assertEqual(cc.answer_summary(ev[2]["content"]), "Размер? — S; Добавки? — Сыр, Лук")
        texts, _ = cc.parse_transcript_chunk(data)  # прежний формат не сломан
        self.assertEqual(texts, [("ask1", "Сейчас уточню")])

    def test_tool_result_list_content(self):
        ev, _ = cc.parse_transcript_events(answer_line("t", [{"type": "text", "text": "да"}]))
        self.assertEqual(ev, [{"kind": "answer", "tool_id": "t", "content": "да"}])


class QuestionClosedByTranscriptTests(unittest.TestCase):
    """A question registered for the mini-app (a push, no chat message) of a session nobody follows in Telegram:
    forward_tick never reads its transcript, so the answer there did not close it — the session stayed «waiting
    for you» while it worked, until its next question pushed the old one out (owner 27.10 11:28)."""
    def setUp(self):
        self.e = Env(self)
        self.tr = os.path.join(self.e.tmp.name, "t.jsonl")
        with open(self.tr, "wb") as f:
            f.write(ask_line("toolu_q", [Q_SIZE]))
        self.e.job("s1", name="Альфа", state="working", linkScanPath=self.tr)
        self.e.agents = [{"id": "s1", "pid": 1, "name": "Альфа", "status": "busy", "state": "working"}]
        self.e.bot.list_sessions(True)
        self.e.bot.store.data.setdefault("questions", {})["1:toolu_q"] = {
            "sid": "s1", "tool_id": "toolu_q", "chat": 1, "name": "Альфа", "questions": [
                {"question": "Размер?", "header": "Размер", "multi": False, "options": [{"label": "S", "description": ""}]}],
            "step": 0, "marks": {}, "pushed": True, "mid": None}

    def row(self):
        return {r["id"]: r for r in self.e.bot.api_sessions(1)["sessions"]}["s1"]

    def test_open_while_unanswered(self):
        self.assertEqual(self.row()["group"], "wait")

    def test_answer_in_transcript_closes_it(self):
        with open(self.tr, "ab") as f:
            f.write(answer_line("toolu_q", 'User has answered: "Размер?"="S"'))
        self.assertEqual((self.row()["group"], self.row()["ask"]), ("work", None))
        self.assertIsNone(self.e.bot.pending_question("s1"))
        self.assertTrue(self.e.bot.store.data["questions"]["1:toolu_q"]["closed"])

    def test_answer_far_from_the_end_closes_it(self):
        # images make transcripts grow by megabytes after the answer (bb447700: 2.6 MB past it)
        with open(self.tr, "ab") as f:
            f.write(answer_line("toolu_q", 'User has answered: "Размер?"="S"'))
            for i in range(12):
                f.write(line({"type": "user", "message": {"content": [{"type": "text", "text": "x" * 300000}]}}))
        self.assertEqual(self.row()["group"], "work")


class QuestionBotTests(unittest.TestCase):
    def setUp(self):
        self.e = Env(self)
        e = self.e
        self.tr = os.path.join(e.tmp.name, "t.jsonl")
        open(self.tr, "wb").close()
        e.job("s1", name="Альфа", state="working", linkScanPath=self.tr)
        e.bot.make_current(1, "s1")
        self.fs = FakeScreen(MENU_S)
        e.bot.tmux = self.fs.tmux()
        e.bot.injector = e.bot.tmux.send_text

    def feed(self, data):
        with open(self.tr, "ab") as f:
            f.write(data)
        self.e.bot.forward_tick()

    def buttons(self, msg):
        return {b["text"]: b["callback_data"] for r in msg["reply_markup"]["inline_keyboard"] for b in r}

    def last_q(self):
        return [m for m in self.e.tg.of("sendMessage") if "❓" in m["text"]][-1]

    def test_single_question_message_and_press(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        m = self.last_q()
        self.assertEqual(m["parse_mode"], "HTML")
        self.assertIn("<b>❓ Альфа</b>", m["text"])
        # header «Размер» содержится в вопросе — не повторяем
        self.assertIn("<b>❓ Альфа</b>\nРазмер?", m["text"])
        self.assertNotIn("Размер: Размер?", m["text"])
        self.assertIn("S — маленький", m["text"])
        self.assertNotIn("Вопрос 1 из", m["text"])
        b = self.buttons(m)
        self.assertEqual(list(b)[:3], ["S", "M", "L"])
        self.assertIn("✏️ Свой ответ", b)
        self.assertIn("✖ Отмена", b)
        self.assertNotIn("📺 Экран", b)
        last_row = [x["text"] for x in m["reply_markup"]["inline_keyboard"][-1]]
        self.assertEqual(last_row, ["✏️ Свой ответ", "✖ Отмена"])
        self.assertTrue(all(len(v.encode()) <= 64 for v in b.values()))
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        self.e.bot.handle_update(self.e.cb(b["M"], mid=77))
        self.assertEqual(self.fs.flat_keys, ["2"])
        ed = self.e.tg.of("editMessageText")[-1]
        self.assertEqual(ed["message_id"], 77)
        self.assertIn("Ответ отправлен", ed["text"])
        self.assertNotIn("reply_markup", ed)
        # ответ появился в транскрипте -> сообщение закрывается
        self.feed(answer_line("toolu_1", 'User has answered: "Размер?"="M"'))
        ed = self.e.tg.of("editMessageText")[-1]
        self.assertIn("❓ Размер — ✔ M", ed["text"])
        self.assertNotIn("reply_markup", ed)

    def test_answer_in_terminal_closes_message(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        self.feed(answer_line("toolu_1", 'User has answered: "Размер?"="L"'))
        ed = self.e.tg.of("editMessageText")[-1]
        self.assertIn("❓ Размер — ✔ L", ed["text"])
        self.assertNotIn("reply_markup", ed)
        self.assertEqual(self.fs.keys, [])  # ответ из терминала: в tmux ничего не нажимали

    def test_stale_button_when_menu_gone(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        b = self.buttons(self.last_q())
        self.fs.screen = EMPTY_PROMPT
        self.e.bot.handle_update(self.e.cb(b["S"], mid=78))
        self.assertEqual(self.fs.keys, [])
        self.assertIn("Вопрос уже закрыт", self.e.tg.of("editMessageText")[-1]["text"])

    def test_multi_question_key_sequence(self):
        self.feed(ask_line("toolu_1", [Q_SIZE, Q_TOP]))
        m = self.last_q()
        self.assertIn("Вопрос 1 из 2", m["text"])
        # 1) одиночный выбор: цифра, переход к следующему вопросу
        self.fs.on_key = lambda ks, f: setattr(f, "screen", MENU_TABS) if ks == ["1"] else None
        self.e.bot.handle_update(self.e.cb(self.buttons(m)["S"], mid=80))
        ed = self.e.tg.of("editMessageText")[-1]
        self.assertIn("Вопрос 2 из 2", ed["text"])
        b = {x["text"]: x["callback_data"] for r in ed["reply_markup"]["inline_keyboard"] for x in r}
        self.assertIn("☐ Сыр", b)
        self.assertIn("Далее →", b)
        # 2) multiSelect: цифра отмечает и остаётся; отметка читается с экрана
        self.fs.on_key = lambda ks, f: setattr(f, "screen", MENU_TABS_CHECKED) if ks == ["1"] else None
        self.e.bot.handle_update(self.e.cb(b["☐ Сыр"], mid=80))
        ed = self.e.tg.of("editMessageText")[-1]
        self.assertIn("Вопрос 2 из 2", ed["text"])
        b = {x["text"]: x["callback_data"] for r in ed["reply_markup"]["inline_keyboard"] for x in r}
        self.assertIn("☑ Сыр", b)
        # 3) «Далее →» = Right, появляется экран Review
        self.fs.on_key = lambda ks, f: setattr(f, "screen", REVIEW) if ks == ["Right"] else None
        self.e.bot.handle_update(self.e.cb(b["Далее →"], mid=80))
        ed = self.e.tg.of("editMessageText")[-1]
        self.assertIn("Проверьте ответы", ed["text"])
        self.assertIn("Размер — S", ed["text"])
        b = {x["text"]: x["callback_data"] for r in ed["reply_markup"]["inline_keyboard"] for x in r}
        self.assertEqual([k for k in b if k != "📺 Экран"], ["✅ Отправить ответы", "✖ Отмена"])
        # 4) Submit = цифра 1
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        self.e.bot.handle_update(self.e.cb(b["✅ Отправить ответы"], mid=80))
        self.assertEqual(self.fs.flat_keys, ["1", "1", "Right", "1"])

    def test_cancel_sends_escape(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        self.e.bot.handle_update(self.e.cb(self.buttons(self.last_q())["✖ Отмена"], mid=81))
        self.assertEqual(self.fs.flat_keys, ["Escape"])

    def test_own_answer_flow(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        b = self.buttons(self.last_q())
        self.e.bot.handle_update(self.e.cb(b["✏️ Свой ответ"], mid=82))
        fr = self.e.tg.of("sendMessage")[-1]
        self.assertEqual(fr["text"], "Ваш ответ на: Размер?")
        self.assertTrue(fr["reply_markup"]["force_reply"])
        self.assertEqual(self.fs.keys, [])
        text = "ровно 42 см"
        self.fs.on_key = lambda ks, f: setattr(f, "screen", MENU_S.replace("4. Type something", "❯ 4. Type something")) \
            if ks == ["4"] else None
        self.fs.on_paste = lambda f: setattr(f, "screen", MENU_S.replace("4. Type something", "❯ 4. " + text))
        self.e.sent.clear()
        self.e.bot.handle_update(self.e.msg(text, mid=90, reply=self.e.tg.n))
        self.assertEqual(self.e.sent, [])  # не как обычное сообщение
        names = [c[1] for c in self.fs.calls]
        self.assertEqual(self.fs.flat_keys, ["4", "Enter"])
        self.assertLess(names.index("send-keys"), names.index("paste-buffer"))
        self.assertNotIn("C-u", self.fs.flat_keys)
        self.assertTrue(self.e.tg.of("setMessageReaction"))

    def test_own_answer_not_confirmed_no_enter(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        self.e.bot.handle_update(self.e.cb(self.buttons(self.last_q())["✏️ Свой ответ"], mid=82))
        self.e.bot.handle_update(self.e.msg("текст который не появится", mid=91))
        self.assertNotIn("Enter", self.fs.flat_keys)
        self.assertIn("Enter не нажат", self.e.tg.of("sendMessage")[-1]["text"])

    def test_plain_text_during_menu_not_pasted_and_question_resent(self):
        self.feed(ask_line("toolu_1", [Q_SIZE]))
        before = len([m for m in self.e.tg.of("sendMessage") if "❓" in m["text"]])
        self.e.bot.handle_update(self.e.msg("просто текст", mid=95))
        sm = self.e.tg.of("sendMessage")
        self.assertTrue(any("Сессия ждёт ответа на вопрос" in m["text"] for m in sm))
        self.assertEqual(len([m for m in sm if "❓" in m["text"]]), before + 1)
        self.assertNotIn("paste-buffer", [c[1] for c in self.fs.calls])
        self.assertEqual(self.fs.keys, [])

    def test_plain_text_during_menu_without_known_question(self):
        self.e.bot.handle_update(self.e.msg("просто текст", mid=96))
        m = self.e.tg.of("sendMessage")[-1]
        self.assertIn("Сессия ждёт ответа на вопрос", m["text"])
        self.assertEqual(m["reply_markup"]["inline_keyboard"][0][0]["text"], "📺 Экран")

    def test_unwatched_other_session_questions_not_sent(self):
        e = self.e
        tr2 = os.path.join(e.tmp.name, "t2.jsonl")
        open(tr2, "wb").close()
        e.job("s2", name="Бета", state="working", linkScanPath=tr2)
        e.bot.make_current(1, "s2")
        e.bot.make_current(1, "s2")
        self.feed(b"")
        with open(self.tr, "ab") as f:
            f.write(ask_line("toolu_9", [Q_SIZE]))
        e.bot.forward_tick()
        self.assertEqual([m for m in e.tg.of("sendMessage") if "❓" in m["text"]], [])
        # с подпиской — приходит
        e.bot.set_watch(1, "s1", True)
        with open(self.tr, "ab") as f:
            f.write(ask_line("toolu_10", [Q_SIZE], uuid="ask2"))
        e.bot.forward_tick()
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "❓" in m["text"]]), 1)


class SubscriptionTests(unittest.TestCase):
    def test_new_session_is_watched_and_announced(self):
        e = Env(self)
        e.bot.handle_update(e.msg("/new сделай что-то"))
        self.assertTrue(e.bot.is_watched("abcd1234"))
        self.assertTrue(any("🔔 уведомления включены" in m["text"] for m in e.tg.of("sendMessage")))
        self.assertTrue(e.bot.store.chat(1)["fw"]["abcd1234"]["turn"])

    def test_new_dialog_session_is_watched(self):
        e = Env(self)
        e.bot.handle_update(e.msg(cc.btn("new")))
        e.bot.handle_update(e.cb("nd:0"))
        e.bot.handle_update(e.msg("задача", mid=40))
        self.assertTrue(e.bot.is_watched("abcd1234"))

    def test_silent_without_subscription_and_message_with_last_text(self):
        e = Env(self)
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").write(asst("u1", "Итоговый текст модели"))
        e.job("a", name="A", state="blocked", detail="нужно ваше да", linkScanPath=tr)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.agents = [{"id": "a", "name": "A", "state": "blocked"}]
        e.bot.notify_tick()
        self.assertEqual(e.tg.of("sendMessage"), [])
        e.bot.set_watch(1, "a", True)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.agents = [{"id": "a", "name": "A", "state": "blocked"}]
        e.bot.notify_tick()
        m = e.tg.of("sendMessage")[-1]
        self.assertEqual(m["text"], "🟡 A ждёт: нужно ваше да\n\nИтоговый текст модели")
        kb = [b["text"] for r in m["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(kb, ["✅ Да", "❌ Нет", "💬 Ответить", "📺 Экран"])

    def test_old_muted_key_ignored(self):
        e = Env(self)
        os.makedirs(os.path.dirname(e.cfg.state_path), exist_ok=True)
        with open(e.cfg.state_path, "w") as f:
            json.dump({"muted": ["a"], "chats": {}}, f)
        bot = cc.Bot(e.cfg, tg=e.tg, runner=e.runner)
        self.assertEqual(bot.store.data["watched"], [])
        self.assertNotIn("muted", bot.store.data)


class TurnForwardingTests(unittest.TestCase):
    def test_forward_only_during_turn_started_from_telegram(self):
        e = Env(self)
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").close()
        e.job("s1", name="Альфа", state="working", linkScanPath=tr)
        e.bot.make_current(1, "s1")

        def add(b):
            with open(tr, "ab") as f:
                f.write(b)
            e.bot.forward_tick()
        add(asst("t1", "ход из терминала"))
        self.assertEqual(e.tg.of("sendMessage"), [])
        e.bot.handle_update(e.msg("вопрос из телеграма", mid=5))
        add(asst("t2", "ответ на телеграм"))
        self.assertEqual([m["text"].split("\n\n")[-1] for m in e.tg.of("sendMessage")], ["ответ на телеграм"])
        add(line({"type": "system", "subtype": "turn_duration"}))
        add(asst("t3", "следующий ход из терминала"))
        self.assertEqual(len(e.tg.of("sendMessage")), 1)

    def test_turn_ends_when_state_leaves_working(self):
        e = Env(self)
        t = {"v": 1000.0}
        e.bot.clock = lambda: t["v"]
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").close()
        e.job("s1", name="Альфа", state="working", linkScanPath=tr)
        e.bot.make_current(1, "s1")
        e.bot.handle_update(e.msg("go", mid=5))
        e.bot.forward_tick()
        self.assertTrue(e.bot.store.chat(1)["fw"]["s1"]["turn"])
        e.job("s1", name="Альфа", state="done", linkScanPath=tr)
        e.bot.forward_tick()  # state != working, записей нет
        self.assertFalse(e.bot.store.chat(1)["fw"]["s1"]["turn"])

    def test_watched_session_outside_turn_forwards_nothing(self):
        e = Env(self)
        tr = os.path.join(e.tmp.name, "w.jsonl")
        open(tr, "wb").close()
        e.job("w", name="Вэ", state="working", linkScanPath=tr)
        e.bot.set_watch(1, "w", True)
        with open(tr, "ab") as f:
            f.write(asst("x1", "промежуточный текст"))
        e.bot.forward_tick()
        self.assertEqual(e.tg.of("sendMessage"), [])

    def test_help_mentions_notifications_and_questions(self):
        self.assertIn("🔔 Уведомлять", cc.help_default())
        self.assertIn("Свой ответ", cc.help_default())


# ---------------------------------------------------------------- топики, трекер, квота, самоочистка

NOW = 1_000_000_000.0


def topic_env(tc, topics=True):
    e = Env(tc)
    e.t = {"v": NOW}
    e.bot.clock = lambda: e.t["v"]
    e.bot.topics_enabled = topics
    e.job("a", name="Альфа", state="working", cwd="/tmp", detail="читает файл")
    e.agents = [{"id": "a", "name": "Альфа", "state": "working", "cwd": "/tmp"}]
    return e


def thread_of(e, sid="a"):
    return e.bot.store.chat(1)["topics"][sid]["thread"]


def route_env(tc, reply, rc=0):
    """Topics env where `claude -p` (the router) answers `reply`; e.router lists its inputs."""
    e = topic_env(tc)
    e.router = []
    orig = e.bot.run

    def run(args, timeout=30, cwd=None, input_text=None):
        if "-p" in args and (input_text or "").startswith(cc.route_prompt()):
            e.router.append(input_text)
            return rc, reply
        return orig(args, timeout, cwd, input_text)
    e.bot.run = run
    return e


def stub_router(e, reply):
    """`claude -p` (the All router) answers `reply`; other commands run as before."""
    orig = e.bot.run
    e.bot.run = lambda args, timeout=30, cwd=None, input_text=None: (
        (0, reply) if "-p" in args and (input_text or "").startswith(cc.route_prompt())
        else orig(args, timeout, cwd, input_text))


def bg_runs(e):
    return [r for r in e.runs if "--bg" in r[0]]


class AllFeedTests(unittest.TestCase):
    def test_all_model_sid_routes_to_session_and_cleans_feed(self):
        e = route_env(self, 'ok {"sid": "a", "cwd": "/tmp"} ok')
        e.bot.handle_update(e.msg("продолжай", mid=9))
        self.assertEqual(e.sent, [("a", "продолжай")])
        self.assertEqual(bg_runs(e), [])
        self.assertIn(9, [d["message_id"] for d in e.tg.of("deleteMessage")])
        m = e.tg.of("sendMessage")[-1]
        self.assertIn("rt:9", json.dumps(m["reply_markup"]))
        self.assertTrue(any(x["text"] == "💬 продолжай" and x.get("message_thread_id") == thread_of(e)
                            for x in e.tg.of("sendMessage")))
        pl = json.loads(e.router[0][len(cc.route_prompt()):])
        self.assertEqual(pl["sessions"][0]["id"], "a")

    def test_all_model_null_error_junk_or_foreign_values_start_new(self):
        d = "/outside/of/the/set"
        for reply, rc, want in (('{"sid": null, "cwd": null}', 0, None), ("", 1, None), ("мусор", 0, None),
                                ('{"sid": "zzz", "cwd": null}', 0, None), ('{"sid": null, "cwd": "%s"}' % d, 0, None)):
            e = route_env(self, reply, rc)
            e.bot.handle_update(e.msg("новая задача", mid=9))
            self.assertEqual(e.sent, [], reply)
            self.assertEqual(len(bg_runs(e)), 1, reply)
            self.assertEqual(bg_runs(e)[0][1], e.cfg.default_cwd, reply)
            self.assertIn(9, [x["message_id"] for x in e.tg.of("deleteMessage")])

    def test_all_candidates_include_done_but_not_old_or_stopped(self):
        # live run 20.10: a background session that answered drops out of `claude agents` without --all
        e = route_env(self, '{"sid": null, "cwd": null}')
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))
        for sid, state, ago in (("fresh_done", "done", 600), ("old_done", "done", 13 * 3600), ("halt", "stopped", 60)):
            e.job(sid, name=sid, state=state, updatedAt=iso(e.t["v"] - ago))
        e.agents = [{"id": s, "name": s, "state": st} for s, st in (("a", "working"), ("fresh_done", "done"),
                                                                    ("old_done", "done"), ("halt", "stopped"))]
        e.bot.handle_update(e.msg("про готовую", mid=9))
        ids = {x["id"] for x in json.loads(e.router[0][len(cc.route_prompt()):])["sessions"]}
        self.assertEqual(ids, {"a", "fresh_done"})

    def test_all_model_cwd_from_set_is_used_for_new_session(self):
        e = route_env(self, '{"sid": null, "cwd": "/tmp"}')
        e.bot.handle_update(e.msg("новая задача", mid=9))
        self.assertEqual(bg_runs(e)[0][1], "/tmp")

    def test_all_failed_new_session_keeps_message(self):
        e = route_env(self, '{"sid": null, "cwd": null}')
        orig = e.runner
        e.runner = lambda args, *a, **k: (1, "") if "--bg" in args else orig(args, *a, **k)
        e.bot.run = lambda args, timeout=30, cwd=None, input_text=None: (
            (0, "{}") if "-p" in args else e.runner(args, timeout, cwd, input_text))
        e.bot.handle_update(e.msg("новая задача", mid=9))
        self.assertNotIn(9, [x["message_id"] for x in e.tg.of("deleteMessage")])

    def test_all_claude_missing_starts_sdk_without_model(self):
        e = route_env(self, '{"sid": "a"}')
        e.bot.claude_missing = lambda: True
        calls = []
        e.bot.do_new = lambda chat, cwd, text, sdk=False: calls.append((cwd, text, sdk)) or "sdk-1"
        e.bot.handle_update(e.msg("задача", mid=9))
        self.assertEqual(calls, [(e.cfg.default_cwd, "задача", True)])
        self.assertEqual(e.router, [])

    def test_all_two_messages_in_a_row_make_one_session(self):
        e = route_env(self, '{"sid": null, "cwd": null}')
        e.bot.handle_update(e.msg("первая часть", mid=9))
        e.t["v"] += 30
        e.bot.handle_update(e.msg("вторая часть", mid=10))
        self.assertEqual(len(bg_runs(e)), 1)
        self.assertEqual(len(e.router), 1)
        self.assertEqual(e.sent, [("abcd1234", "вторая часть")])
        e.t["v"] += 121
        e.bot.handle_update(e.msg("третья", mid=11))
        self.assertEqual(len(e.router), 2)

    def test_all_reply_to_session_message_goes_there_without_model(self):
        e = route_env(self, '{"sid": null}')
        e.bot.store.data.setdefault("msgmap", {})["1:77"] = "a"
        e.bot.handle_update(e.msg("ответ", mid=9, reply=77))
        self.assertEqual(e.sent, [("a", "ответ")])
        self.assertEqual(e.router, [])

    def test_all_explicit_path_starts_session_there_without_model(self):
        e = route_env(self, '{"sid": "a"}')
        with tempfile.TemporaryDirectory() as d:
            e.bot.handle_update(e.msg(d + " сделай Х", mid=9))
            self.assertEqual(e.router, [])
            self.assertEqual(bg_runs(e)[0][1], d)
            self.assertEqual(bg_runs(e)[0][0][-1], "сделай Х")

    def test_all_button_redoes_last_route_as_new_session(self):
        e = route_env(self, '{"sid": "a", "cwd": null}')
        e.bot.handle_update(e.msg("продолжай", mid=9))
        e.bot.handle_update(e.cb("rt:9", mid=60))
        self.assertEqual(len(bg_runs(e)), 1)
        self.assertEqual(bg_runs(e)[0][0][-1], "продолжай")
        self.assertIsNone(e.bot.store.chat(1)["route_last"])
        e.bot.handle_update(e.cb("rt:9", mid=61))  # outdated now
        self.assertEqual(len(bg_runs(e)), 1)

    def test_all_button_outdated_mid_does_nothing(self):
        e = route_env(self, '{"sid": "a", "cwd": null}')
        e.bot.handle_update(e.msg("продолжай", mid=9))
        e.bot.handle_update(e.cb("rt:3", mid=60))
        self.assertEqual(bg_runs(e), [])

    def test_all_greeting_is_single_and_survives_clean(self):
        e = route_env(self, "")
        e.bot.handle_update(e.msg("/start", mid=5))
        g1 = e.bot.store.chat(1)["greet_mid"]
        self.assertIn(cc.panel_title(), [m["text"] for m in e.tg.of("sendMessage") if "👋" in m["text"]][0])
        e.bot.handle_update(e.msg("/start", mid=6))
        g2 = e.bot.store.chat(1)["greet_mid"]
        self.assertNotEqual(g1, g2)
        self.assertIn(g1, [d["message_id"] for d in e.tg.of("deleteMessage")])
        e.bot.handle_update(e.msg(cc.btn("clean"), mid=13))
        ids = [i for c in e.tg.of("deleteMessages") for i in c["message_ids"]]
        self.assertNotIn(g2, ids)

    def test_all_greeting_sent_at_start_only_when_missing(self):
        e = topic_env(self)
        e.bot.greet_missing()
        e.bot.greet_missing()
        hello = [m for m in e.tg.of("sendMessage") if m["text"].startswith("👋")]
        self.assertEqual(len(hello), 1)
        self.assertTrue(e.bot.store.chat(1).get("greet_mid"))

    def test_all_first_text_sends_greeting_once(self):
        e = route_env(self, '{"sid": null}')
        e.bot.handle_update(e.msg("раз", mid=9))
        e.t["v"] += 200
        e.bot.handle_update(e.msg("два", mid=10))
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "👋" in m["text"]]), 1)


class TopicTests(unittest.TestCase):
    def test_detect_topics(self):
        e = Env(self)
        e.tg.topics = True
        self.assertTrue(e.bot.detect_topics())
        e.tg.topics = False
        e.bot.topics_enabled = False
        self.assertFalse(e.bot.detect_topics())

    def test_text_in_topic_goes_to_that_session(self):
        e = topic_env(self)
        e.job("b", name="Бета", state="working")
        e.bot.ensure_topic(1, "a")
        e.bot.ensure_topic(1, "b")
        e.bot.handle_update(e.msg("привет бета", mid=9, thread=thread_of(e, "b")))
        e.bot.handle_update(e.msg("привет альфа", mid=10, thread=thread_of(e, "a")))
        self.assertEqual(e.sent, [("b", "привет бета"), ("a", "привет альфа")])
        self.assertTrue(e.tg.of("setMessageReaction"))

    def test_main_chat_text_is_reacted_and_routed(self):
        e = route_env(self, '{"sid": null, "cwd": null}')
        e.bot.handle_update(e.msg("просто текст", mid=9))
        self.assertEqual(e.sent, [])
        self.assertEqual(e.tg.of("setMessageReaction")[0]["message_id"], 9)
        self.assertNotIn(cc.hint_main(), [m["text"] for m in e.tg.of("sendMessage")])

    def test_unknown_topic(self):
        e = topic_env(self)
        e.bot.handle_update(e.msg("текст", mid=9, thread=4242))
        self.assertEqual(e.sent, [])
        self.assertIn("не привязан", e.tg.of("sendMessage")[-1]["text"])

    def test_answers_and_status_go_to_topic(self):
        e = topic_env(self)
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").close()
        e.job("a", name="Альфа", state="working", detail="читает файл", linkScanPath=tr)
        e.bot.ensure_topic(1, "a")
        th = thread_of(e)
        e.bot.handle_update(e.msg("вопрос", mid=9, thread=th))
        with open(tr, "ab") as f:
            f.write(asst("x1", "ответ **модели**"))
        e.bot.forward_tick()
        e.bot.status_tick()
        sends = [m for m in e.tg.of("sendMessage") if "message_thread_id" in m or "ответ" in m["text"]]
        self.assertTrue(all(m.get("message_thread_id") == th for m in sends), sends)
        self.assertTrue(any("<b>модели</b>" in m["text"] for m in sends))
        self.assertTrue(any(m["text"].startswith("🔵 Альфа работает") for m in sends))
        self.assertEqual(e.tg.of("sendChatAction")[0]["message_thread_id"], th)

    def test_state_change_does_not_rename_topic_but_name_change_does(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        e.job("a", name="Альфа", state="done")
        e.t["v"] += 11
        e.bot.topics_tick()
        e.job("a", name="Альфа", state="blocked")
        e.t["v"] += 11
        e.bot.topics_tick()
        self.assertEqual(e.tg.of("editForumTopic"), [])  # состояние менялось — служебных сообщений нет
        e.job("a", name="Альфа 2", state="blocked")
        e.t["v"] += 11
        e.bot.topics_tick()
        e.t["v"] += cc.TOPIC_RENAME_SETTLE
        e.bot.topics_tick()
        eds = e.tg.of("editForumTopic")
        self.assertEqual([x["name"] for x in eds], ["Альфа 2"])

    def test_quick_name_changes_rename_the_topic_once(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        e.job("a", name="Помоги. Изучить с целью", state="working")
        e.t["v"] += 11
        e.bot.topics_tick()
        e.job("a", name="Оценка mdw: архитектура", state="working")
        e.t["v"] += 30
        e.bot.topics_tick()
        e.t["v"] += cc.TOPIC_RENAME_SETTLE
        e.bot.topics_tick()
        self.assertEqual([x["name"] for x in e.tg.of("editForumTopic")], ["Оценка mdw: архитектура"])

    def test_question_shows_in_title(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        e.bot.store.data["questions"]["1:t1"] = {"sid": "a", "closed": False, "chat": 1, "tool_id": "t1"}
        self.assertEqual(e.bot.topic_title("a"), "Альфа")  # состояние в названии не показываем: каждое переименование — служебное сообщение

    def test_cleanup_rules(self):
        e = topic_env(self)
        for sid, st in (("done_old", "done"), ("done_new", "done"), ("work_old", "working"),
                        ("gone", "done"), ("quest_old", "done"), ("fail_old", "failed"),
                        ("block_old", "blocked")):
            e.job(sid, name=sid, state=st)
            e.bot.ensure_topic(1, sid)
        topics = e.bot.store.chat(1)["topics"]
        for sid in ("done_old", "work_old", "gone", "quest_old", "fail_old", "block_old"):
            topics[sid]["activity"] = NOW - 25 * 3600
        e.bot.store.data["questions"]["1:q"] = {"sid": "quest_old", "closed": False, "chat": 1, "tool_id": "q"}
        states = {"work_old": "working", "fail_old": "failed", "block_old": "blocked"}  # claude agents agrees with state.json
        e.agents = [{"id": s, "name": s, "state": states.get(s, "done")} for s in
                    ("done_old", "done_new", "work_old", "quest_old", "fail_old", "block_old")]
        e.bot.topic_cleanup()
        deleted = {d["message_thread_id"] for d in e.tg.of("deleteForumTopic")}
        gone = {sid for sid in ("done_old", "done_new", "work_old", "gone", "quest_old", "fail_old", "block_old")
                if sid not in topics}
        self.assertEqual(gone, {"done_old", "gone", "fail_old"})
        self.assertEqual(len(deleted), 3)

    def test_cleanup_drops_stopped_at_once(self):
        e = topic_env(self)
        for sid in ("stop_new", "stop_q", "done_new"):
            e.job(sid, name=sid, state="done")
            e.bot.ensure_topic(1, sid)
        e.bot.store.data["questions"]["1:q"] = {"sid": "stop_q", "closed": False, "chat": 1, "tool_id": "q"}
        e.agents = [{"id": "stop_new", "name": "s", "state": "stopped"}, {"id": "stop_q", "name": "q", "state": "stopped"},
                    {"id": "done_new", "name": "d", "state": "done"}]
        e.bot.topic_cleanup()
        self.assertEqual(set(e.bot.store.chat(1)["topics"]), {"stop_q", "done_new"})  # no TTL for a stopped one
        self.assertIn("stop_q", e.bot.done_topics(1))

    def test_stop_confirmation_drops_the_topic(self):
        e = topic_env(self)
        e.job("a", name="Альфа", state="working")
        e.bot.ensure_topic(1, "a")
        e.bot.handle_update(e.cb("stpy:a", mid=61))
        self.assertTrue([r for r in e.runs if r[0][-2:] == ["stop", "a"]])
        self.assertNotIn("a", e.bot.store.chat(1)["topics"])
        self.assertTrue(e.tg.of("deleteForumTopic"))

    def test_cleanup_keeps_claude_topics_while_claude_is_missing(self):
        e = topic_env(self)
        e.job("a", name="Альфа", state="working")
        e.bot.ensure_topic(1, "a")
        e.bot.ensure_topic(1, "s-0badf00d")
        e.bot.claude_missing = lambda: True  # mid-update: the list has SDK sessions only
        e.bot.list_sessions = lambda show_all=False: [{"id": "s-1a2b3c4d", "state": "working"}]
        e.bot.topic_cleanup()
        self.assertEqual(set(e.bot.store.chat(1)["topics"]), {"a"})

        e = topic_env(self)
        e.cfg.topic_ttl_hours = 1
        e.job("x", name="x", state="done")
        e.bot.ensure_topic(1, "x")
        e.agents = [{"id": "x", "name": "x", "state": "done"}]
        e.t["v"] += 1800
        e.bot.topic_cleanup()
        self.assertIn("x", e.bot.store.chat(1)["topics"])
        e.t["v"] += 3600
        e.bot.topic_cleanup()
        self.assertNotIn("x", e.bot.store.chat(1)["topics"])

    def test_cleanup_skips_on_failure_or_empty_list(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        e.agents = []
        e.bot.topic_cleanup()
        self.assertIn("a", e.bot.store.chat(1)["topics"])

    def test_close_topic_button_with_confirmation(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        e.bot.handle_update(e.cb("tpc:a"))
        self.assertIn("Закрыть топик", e.tg.of("sendMessage")[-1]["text"])  # подтверждение
        self.assertEqual(e.tg.of("deleteForumTopic"), [])
        e.bot.handle_update(e.cb("tpd:a"))
        self.assertEqual(len(e.tg.of("deleteForumTopic")), 1)
        self.assertNotIn("a", e.bot.store.chat(1)["topics"])
        self.assertFalse([r for r in e.runs if "stop" in r[0]])  # сессию не трогаем

    def test_watch_creates_topic_and_notifies_into_it(self):
        e = topic_env(self)
        e.bot.notify_tick()
        e.bot.handle_update(e.cb("watch:a"))
        self.assertEqual(len(e.tg.of("createForumTopic")), 1)
        th = thread_of(e)
        e.job("a", name="Альфа", state="blocked", detail="ждёт")
        e.agents = [{"id": "a", "name": "Альфа", "state": "blocked"}]
        e.bot.notify_tick()
        m = e.tg.of("sendMessage")[-1]
        self.assertTrue(m["text"].startswith("🟡 Альфа ждёт"))
        self.assertEqual(m["message_thread_id"], th)

    def test_new_session_gets_topic_and_notifications(self):
        e = topic_env(self)
        e.bot.handle_update(e.msg("/new сделай что-то"))
        self.assertTrue(e.bot.is_watched("abcd1234"))
        self.assertEqual([c["name"] for c in e.tg.of("createForumTopic")].count("Новая"), 1)
        th = thread_of(e, "abcd1234")
        card = [m for m in e.tg.of("sendMessage") if m["text"].endswith("· abcd1234")][0]
        self.assertEqual(card["message_thread_id"], th)

    def test_dead_topic_falls_back_to_main_chat(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        th = thread_of(e)
        e.tg.errors["sendMessage"] = (lambda p: p.get("message_thread_id") == th, "Bad Request: message thread not found")
        e.bot.say(1, "тест", sid="a")
        self.assertNotIn("a", e.bot.store.chat(1)["topics"])
        self.assertNotIn("message_thread_id", e.tg.of("sendMessage")[-1])

    def test_status_tracked_per_session_not_per_chat(self):
        e = topic_env(self)
        e.job("b", name="Бета", state="working", detail="работает")
        e.bot.ensure_topic(1, "a")
        e.bot.ensure_topic(1, "b")
        e.bot.handle_update(e.msg("а", mid=9, thread=thread_of(e, "a")))
        e.bot.status_tick()
        e.bot.handle_update(e.msg("б", mid=10, thread=thread_of(e, "b")))
        e.bot.status_tick()
        statuses = [m for m in e.tg.of("sendMessage") if "работает:" in m["text"]]
        self.assertEqual({m["message_thread_id"] for m in statuses}, {thread_of(e, "a"), thread_of(e, "b")})
        e.job("a", name="Альфа", state="done")
        e.bot.status_tick()
        self.assertEqual(len(e.tg.of("deleteMessage")), 1)  # статус A убран, статус B жив
        e.job("b", name="Бета", state="done")
        e.bot.status_tick()
        self.assertEqual(len(e.tg.of("deleteMessage")), 2)
        self.assertEqual(e.bot.status, {})

    def test_card_in_fallback_mode_is_not_deleted_by_next_card(self):
        e = topic_env(self, topics=False)
        e.bot.send_card(1, "a")
        e.bot.send_card(1, "a")
        self.assertEqual([d["message_id"] for d in e.tg.of("deleteMessage")], [101])  # one card per session

    def test_fallback_mode_without_topics_works_as_before(self):
        e = topic_env(self, topics=False)
        e.bot.handle_update(e.cb("s:a"))
        self.assertEqual(e.tg.of("createForumTopic"), [])
        self.assertEqual(e.bot.store.chat(1)["session"], "a")
        card = [m for m in e.tg.of("sendMessage") if m["text"].endswith("· a")][0]
        self.assertNotIn("message_thread_id", card)
        e.bot.handle_update(e.msg("в текущую", mid=9))
        self.assertEqual(e.sent, [("a", "в текущую")])
        self.assertEqual(e.bot.help_text(), cc.help_default())


class DashboardTests(unittest.TestCase):
    def test_help_topics_is_short_and_mentions_key_things(self):
        self.assertLessEqual(len(cc.help_topics().splitlines()), 10)
        self.assertTrue(cc.help_topics().startswith("Как пользоваться"))
        for w in ("топик", "новую сессию", "кнопками", "🔔"):
            self.assertIn(w, cc.help_topics())

    def test_quota_format(self):
        d = {"updated_at": NOW - 300, "five_hour": {"used_percentage": 59.0, "resets_at": NOW + 3600},
             "seven_day": {"used_percentage": 43.0, "resets_at": 0}}
        txt = cc.quota_text(d, NOW)
        self.assertEqual(txt, "Claude: 5 ч — 59 %, сброс в " + cc.fmt_reset(NOW + 3600, NOW)
                         + " · 7 дн — 43 % · данные 5 мин назад")
        self.assertNotIn("▓", txt)
        self.assertNotIn("░", txt)

    def test_quota_unknown_reset_and_stale(self):
        d = {"updated_at": NOW - 40 * 60, "five_hour": {"used_percentage": 10, "resets_at": 0},
             "seven_day": {"used_percentage": 100, "resets_at": 0}}
        txt = cc.quota_text(d, NOW)
        self.assertIn("5 ч — 10 % · 7 дн — 100 %", txt)  # без полосок и без «время сброса неизвестно»
        self.assertNotIn("неизвестно", txt)
        self.assertNotIn("▓", txt + cc.quota_detail(d, NOW))
        self.assertIn("данные 40 мин назад", txt)
        self.assertIn("⚠️ данные устарели", txt)

    def test_quota_detail_format(self):
        d = {"updated_at": NOW - 120, "five_hour": {"used_percentage": 59.0, "resets_at": NOW + 3600},
             "seven_day": {"used_percentage": 43.0}}
        self.assertEqual(cc.quota_detail(d, NOW).split("\n"), [
            "Квота Claude", "5 часов — 59 %, сброс в " + cc.fmt_reset(NOW + 3600, NOW), "7 дней — 43 %",
            "", "Обновлено 2 мин назад"])
        d["updated_at"] = NOW - 3600
        self.assertTrue(cc.quota_detail(d, NOW).splitlines()[-1].startswith("⚠️ Данные старые"))

    def test_humanize_detail(self):
        h = cc.humanize_detail
        self.assertEqual(h("You've hit your session limit · resets 6:30am (Europe/Belgrade)"),
                         "лимит сессии · сброс в 6:30")
        self.assertEqual(h("You've hit your session limit · resets 4:10pm (Europe/Belgrade)"),
                         "лимит сессии · сброс в 16:10")
        self.assertEqual(h("You've reached your Fable limit · resets Oct 20"), "лимит Fable")
        self.assertEqual(h('API Error: 400 {"error":"blocked by safeguards"}'),
                         "ошибка API: защита отклонила запрос")
        self.assertEqual(h("читает файл  config.py"), "читает файл config.py")
        self.assertEqual(h(None), "")

    def test_ago_short(self):
        self.assertEqual(cc.ago_short(NOW - 10, NOW), "сейчас")
        self.assertEqual(cc.ago_short(NOW - 12 * 60, NOW), "12 мин")
        self.assertEqual(cc.ago_short(NOW - 3 * 3600, NOW), "3 ч")
        self.assertEqual(cc.ago_short(NOW - 2 * 86400, NOW), "2 дн")
        self.assertEqual(cc.ago_short(0, NOW), "")

    def test_clean_batches_of_100(self):
        e = topic_env(self, topics=False)
        for i in range(250):
            e.bot._log_msg(1, {"message_id": 1000 + i}, None)
        e.bot.clean_chat(1)
        self.assertEqual([len(c["message_ids"]) for c in e.tg.of("deleteMessages")], [100, 100, 50])

    def test_message_log_keeps_48_hours(self):
        e = topic_env(self, topics=False)
        e.bot._log_msg(1, {"message_id": 1}, None)
        e.t["v"] += 49 * 3600
        e.bot._log_msg(1, {"message_id": 2}, None)
        self.assertEqual([x[1] for x in e.bot.store.data["msglog"]], [2])
        e.bot._log_msg(1, {"message_id": 3}, 55)  # сообщения в топиках в журнал не идут
        self.assertEqual([x[1] for x in e.bot.store.data["msglog"]], [2])



BASE = 1_791_000_000.0  # 2026-10 — «сейчас» для тестов пульта


def iso(ts):
    from datetime import datetime as _dt, timezone as _tz
    return _dt.fromtimestamp(ts, _tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mk(e, sid, state, minutes=1, name=None, **extra):
    detail = extra.pop("detail", "детали " + sid)
    e.job(sid, name=name or sid, state=state, detail=detail, cwd="/tmp",
          updatedAt=iso(BASE - minutes * 60), **extra)
    e.agents.append({"id": sid, "name": name or sid, "state": state, "cwd": "/tmp"})


def panel_env(tc, topics=True):
    e = topic_env(tc, topics)
    e.t["v"] = BASE
    e.agents = []
    e.cfg.rate_file = os.path.join(e.tmp.name, "rate.json")
    with open(e.cfg.rate_file, "w") as f:
        json.dump({"updated_at": BASE - 60, "five_hour": {"used_percentage": 59.0, "resets_at": BASE + 3600},
                   "seven_day": {"used_percentage": 43.0, "resets_at": 0}}, f)
    return e


def pth(e):
    return e.bot.panel_state(1)["thread"]


def panel_msgs(e):
    return [m for m in e.tg.of("sendMessage") if m.get("message_thread_id") == pth(e)]


class PanelTests(unittest.TestCase):
    def test_grouping_order_counters_quota_and_ask(self):
        e = panel_env(self)
        tr = os.path.join(e.tmp.name, "ask.jsonl")
        with open(tr, "wb") as f:
            f.write(ask_line("toolu_1", [Q_SIZE]))
        mk(e, "w1", "blocked", 5, detail="You've hit your session limit · resets 6:30am (Europe/Belgrade)")
        mk(e, "w2", "blocked", 9)
        mk(e, "ask", "working", 3, linkScanPath=tr, detail="Размер?")
        for i in range(3):
            mk(e, "r%d" % i, "working", 10 + i)
        mk(e, "fail", "failed", 20, detail="упала")
        for i in range(9):
            mk(e, "d%d" % i, "done", 30 + i)
        mk(e, "long", "working", 50, name="очень длинное имя сессии которое надо обрезать")
        e.bot.store.chat(1)["topics"]["w1"] = {"thread": 5, "title": "x", "last_edit": 0, "activity": 0}
        e.bot.store.data["watched"].append("r0")
        html, plain, markup = e.bot.panel_render(1)
        lines = plain.split("\n")
        self.assertEqual(lines[0], "🟡 3 ждут · 🔵 4 в работе · ✅ 10 готово")
        self.assertEqual(lines[1], "Квота · 5 ч 59 % · 7 дн 43 %")
        self.assertLess(plain.index("ЖДУТ ВАС"), plain.index("В РАБОТЕ"))
        self.assertNotIn("ГОТОВО", plain)  # готовые в списке не показываются
        self.assertNotIn("d0", plain)
        # имя жирным, под ним курсивом «что дальше · давность»
        self.assertIn("❓ <b>ask</b>\n<i>вопрос: Размер? · 3 мин</i>", html)
        self.assertIn("<b>w1</b>\n<i>лимит сессии · сброс в 6:30 · 5 мин</i>", html)
        self.assertIn("🔔", [l for l in lines if l.startswith("r0")][0])
        # 7 активных, на странице PANEL_PAGE=6; последний (самый старый) уходит на 2-ю
        self.assertNotIn("очень длинное", plain)
        rows = markup["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in rows[:6]],
                         ["s:ask", "s:w1", "s:w2", "s:r0", "s:r1", "s:r2"])
        self.assertEqual(rows[0][0]["text"], "❓ ask")
        self.assertEqual(rows[1][0]["text"], "🟡 w1 💬")
        self.assertEqual([len(r) for r in rows[:6]], [1] * 6)
        self.assertEqual([b["text"] for b in rows[6]], ["1 / 2", "Ещё ▶"])
        self.assertEqual(rows[6][1]["callback_data"], "pp:1")
        self.assertEqual([(b["text"], b["callback_data"]) for b in rows[7]],
                         [("🗂 Архив 10", "pa:1"), ("📊 Квота", "pm:quota")])
        self.assertEqual([(b["text"], b["callback_data"]) for b in rows[8]],
                         [("✏️ Имена", "pm:names"), ("❓ Справка", "pm:help")])
        self.assertTrue(plain.endswith("Напишите задачу сюда — запущу новую сессию."))
        self.assertNotIn("▓", plain)

    def test_pagination_13_active_in_3_pages(self):
        e = panel_env(self)
        for i in range(13):
            mk(e, "r%02d" % i, "working", i + 1)
        e.bot.panel_publish(1)
        _, plain, markup = e.bot.panel_render(1)
        rows = markup["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in rows[:6]], ["s:r%02d" % i for i in range(6)])
        self.assertEqual([b["text"] for b in rows[6]], ["1 / 3", "Ещё ▶"])
        e.bot.handle_update(e.cb("pp:1"))
        ed = e.tg.of("editMessageText")[-1]
        rows = ed["reply_markup"]["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in rows[:6]], ["s:r%02d" % i for i in range(6, 12)])
        self.assertEqual([(b["text"], b["callback_data"]) for b in rows[6]],
                         [("◀ Назад", "pp:0"), ("2 / 3", "pp:1"), ("Ещё ▶", "pp:2")])
        self.assertIn("r06", ed["text"])
        self.assertNotIn("r12", ed["text"])
        e.bot.handle_update(e.cb("pp:2"))
        rows = e.tg.of("editMessageText")[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in rows[:1]], ["s:r12"])
        self.assertEqual([b["text"] for b in rows[1]], ["◀ Назад", "3 / 3"])

    def test_show_all_lists_only_done_sessions(self):
        e = panel_env(self)
        mk(e, "act", "working", 1)
        for i in range(8):
            mk(e, "d%02d" % i, "done", i + 1)
        mk(e, "bad", "failed", 30, detail="API Error: 400 safeguards")
        e.bot.panel_publish(1)
        _, plain, markup = e.bot.panel_render(1)
        self.assertNotIn("ГОТОВО", plain)
        self.assertIn("🗂 Архив 9", str(markup))
        e.bot.handle_update(e.cb("pa:1"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("ГОТОВО", ed["text"])
        self.assertNotIn("В РАБОТЕ", ed["text"])
        self.assertNotIn("act", ed["text"].replace("action", ""))
        rows = ed["reply_markup"]["inline_keyboard"]
        self.assertEqual([r[0]["callback_data"] for r in rows[:6]], ["s:d%02d" % i for i in range(6)])
        self.assertEqual([b["text"] for b in rows[-2]], ["◀ К активным", "🧹 Убрать топики"])
        self.assertEqual([b["callback_data"] for b in rows[-2]], ["pa:0", "pm:clean"])
        self.assertEqual([b["text"] for b in rows[-1]], ["🗂 Всё готовое в архив"])
        e.bot.handle_update(e.cb("pp:1"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("🔴 <b>bad</b>", ed["text"])
        self.assertIn("ошибка API: защита отклонила запрос", ed["text"])
        e.bot.handle_update(e.cb("pa:0"))
        self.assertIn("В РАБОТЕ", e.tg.of("editMessageText")[-1]["text"])

    def test_empty_list(self):
        e = panel_env(self)
        _, plain, _ = e.bot.panel_render(1)
        self.assertTrue(plain.startswith("Сессий нет"))

    def test_quota_stale_marker_in_line(self):
        e = panel_env(self)
        with open(e.cfg.rate_file, "w") as f:
            json.dump({"updated_at": BASE - 3600, "five_hour": {"used_percentage": 10},
                       "seven_day": {"used_percentage": 20}}, f)
        _, plain, _ = e.bot.panel_render(1)
        self.assertIn("Квота · 5 ч 10 % · 7 дн 20 % ⚠️ данные устарели", plain)

    def test_panel_created_in_own_topic_pinned_and_edited_only_on_change(self):
        e = panel_env(self)
        mk(e, "a1", "working", 1)
        e.bot.panel_publish(1)
        self.assertEqual([c["name"] for c in e.tg.of("createForumTopic")], ["🎛 Агенты"])
        self.assertEqual(len(panel_msgs(e)), 1)
        pin = e.tg.of("pinChatMessage")[0]
        self.assertTrue(pin["disable_notification"])
        e.bot.panel_tick()
        e.bot.panel_tick()
        self.assertEqual(e.tg.of("editMessageText"), [])
        self.assertEqual(len(panel_msgs(e)), 1)
        mk(e, "b1", "blocked", 1)  # состав изменился
        e.bot.panel_tick()
        self.assertEqual(len(e.tg.of("editMessageText")), 1)
        e.bot.panel_tick()
        self.assertEqual(len(e.tg.of("editMessageText")), 1)

    def test_number_button_opens_topic_with_card_and_rescans_question(self):
        e = panel_env(self)
        mk(e, "a1", "working", 1, name="Альфа")
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("s:a1", mid=e.bot.panel_state(1)["mid"]))
        th = thread_of(e, "a1")
        card = [m for m in e.tg.of("sendMessage") if m["text"].endswith("· a1")][0]
        self.assertEqual(card["message_thread_id"], th)
        self.assertNotEqual(th, pth(e))
        self.assertIn("💬", str(e.bot.panel_render(1)[2]))  # у кнопки сессии появился значок топика
        # ничего лишнего в топике пульта: только само сообщение
        self.assertEqual(len(panel_msgs(e)), 1)
        # повторное открытие: fw-запись есть, но scan_ask снова взведён
        e.bot.store.chat(1)["fw"]["a1"].pop("scan_ask", None)
        e.bot.handle_update(e.cb("s:a1"))
        self.assertTrue(e.bot.store.chat(1)["fw"]["a1"]["scan_ask"])

    def test_scan_ask_set_on_subscribe_and_topic_creation(self):
        e = panel_env(self)
        mk(e, "a1", "working", 1)
        c = e.bot.store.chat(1)
        e.bot.init_fw(c, "a1")
        c["fw"]["a1"].pop("scan_ask")
        e.bot.set_watch(1, "a1", True)
        self.assertTrue(c["fw"]["a1"]["scan_ask"])
        c["fw"]["a1"].pop("scan_ask")
        e.bot.ensure_topic(1, "a1")
        self.assertTrue(c["fw"]["a1"]["scan_ask"])

    def test_text_in_panel_topic_asks_confirmation_inside_panel_then_launches(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        n_before = len(panel_msgs(e))
        mid = e.bot.panel_state(1)["mid"]
        e.bot.handle_update(e.msg("Сделай тесты", mid=9, thread=pth(e)))
        self.assertEqual([d["message_id"] for d in e.tg.of("deleteMessage")], [9])  # сообщение владельца убрано
        self.assertEqual(len(panel_msgs(e)), n_before)  # никаких новых сообщений в топике
        ed = e.tg.of("editMessageText")[-1]
        self.assertEqual(ed["message_id"], mid)
        self.assertIn("Новая сессия", ed["text"])
        self.assertIn("«Сделай тесты»", ed["text"])
        self.assertIn("→ папка " + cc.tilde(e.cfg.default_cwd), ed["text"])
        self.assertEqual([b["text"] for r in ed["reply_markup"]["inline_keyboard"] for b in r],
                         ["▶ Запустить", "📁 Другая папка", "✖ Отмена"])
        self.assertEqual([r for r in e.runs if "--bg" in r[0]], [])
        e.bot.handle_update(e.cb("ngo", mid=mid))
        cmd, cwd = [r for r in e.runs if "--bg" in r[0]][-1]
        self.assertEqual((cmd[-1], cwd), ("Сделай тесты", e.cfg.default_cwd))
        self.assertTrue(e.bot.is_watched("abcd1234"))
        self.assertIn("abcd1234", e.bot.store.chat(1)["topics"])
        # пульт вернулся к списку с отметкой «запущено»
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("🆕 запущено: Новая", ed["text"])
        self.assertNotIn("▶ Запустить", str(ed["reply_markup"]))
        self.assertEqual([c["name"] for c in e.tg.of("createForumTopic")].count("🎛 Агенты"), 1)
        # повторное нажатие не запускает вторую сессию
        runs = len([r for r in e.runs if "--bg" in r[0]])
        e.bot.handle_update(e.cb("ngo", mid=mid))
        self.assertEqual(len([r for r in e.runs if "--bg" in r[0]]), runs)
        # отметка гаснет через минуту
        e.t["v"] += 61
        e.bot.panel_tick()
        self.assertNotIn("🆕 запущено", e.tg.of("editMessageText")[-1]["text"])

    def test_path_in_first_line_is_cwd(self):
        e = panel_env(self)
        d = os.path.join(e.tmp.name, "proj")
        os.makedirs(d)
        self.assertEqual(cc.parse_task_text(d + "\nчто-то сделай\nвторая строка", "/def"),
                         (d, "что-то сделай\nвторая строка"))
        self.assertEqual(cc.parse_task_text("/нет/такой\nзадача", "/def"), ("/def", "/нет/такой\nзадача"))
        self.assertEqual(cc.parse_task_text(d, "/def"), (d, ""))
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg(d + "\nпочини баг", mid=9, thread=pth(e)))
        self.assertIn("→ папка " + d, e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.cb("ngo"))
        cmd, cwd = [r for r in e.runs if "--bg" in r[0]][-1]
        self.assertEqual((cmd[-1], cwd), ("почини баг", d))

    def test_absolute_path_and_task_on_one_line(self):
        e = panel_env(self)
        d = os.path.join(e.tmp.name, "proj")
        os.makedirs(d)
        self.assertEqual(cc.parse_task_text(d + " почини баг", "/def"), (d, "почини баг"))
        self.assertEqual(cc.parse_task_text("/нет/такой почини", "/def"), ("/def", "/нет/такой почини"))
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg(d + " почини баг", mid=9, thread=pth(e)))
        self.assertNotIn("Не знаю такой команды", str(e.tg.calls))
        self.assertIn("→ папка " + d, e.tg.of("editMessageText")[-1]["text"])

    def test_path_without_task_is_rejected(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg("/tmp", mid=9, thread=pth(e)))
        self.assertIn("После пути напишите", e.tg.of("editMessageText")[-1]["text"])
        self.assertEqual([r for r in e.runs if "--bg" in r[0]], [])

    def test_cancel_does_not_launch(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg("случайный текст", mid=9, thread=pth(e)))
        e.bot.handle_update(e.cb("ncx"))
        self.assertEqual([r for r in e.runs if "--bg" in r[0]], [])
        self.assertIsNone(e.bot.store.chat(1)["pending_task"])
        self.assertNotIn("Новая сессия", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.cb("ngo"))  # старая кнопка — ничего не запускает
        self.assertEqual([r for r in e.runs if "--bg" in r[0]], [])

    def test_other_folder_buttons_inside_panel(self):
        e = panel_env(self)
        mk(e, "x1", "done", 1)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg("задача", mid=9, thread=pth(e)))
        e.bot.handle_update(e.cb("ndl"))
        ed = e.tg.of("editMessageText")[-1]
        flat = [(b["text"], b["callback_data"]) for r in ed["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(flat[0], ("📁 " + cc.tilde(e.cfg.default_cwd), "nt:0"))
        self.assertIn(("📁 /tmp", "nt:1"), flat)
        e.bot.handle_update(e.cb("nt:1"))
        cmd, cwd = [r for r in e.runs if "--bg" in r[0]][-1]
        self.assertEqual((cmd[-1], cwd), ("задача", "/tmp"))

    def test_owner_commands_in_panel_topic_are_deleted_and_become_modes(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        n = len(panel_msgs(e))
        e.bot.handle_update(e.msg("/quota", mid=11, thread=pth(e)))
        self.assertIn("Квота Claude", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.msg("/help", mid=12, thread=pth(e)))
        self.assertIn("Как пользоваться", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.msg("/stop", mid=13, thread=pth(e)))
        self.assertEqual([d["message_id"] for d in e.tg.of("deleteMessage")], [11, 12, 13])
        self.assertEqual(len(panel_msgs(e)), n)

    def test_quota_mode_details_back_and_auto_return(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:quota"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("5 часов — 59 %, сброс в " + cc.fmt_reset(BASE + 3600, BASE), ed["text"])
        self.assertIn("7 дней — 43 %", ed["text"])
        self.assertIn("Обновлено 1 мин назад", ed["text"])
        self.assertNotIn("▓", ed["text"])
        self.assertEqual(ed["reply_markup"]["inline_keyboard"][0][0]["text"], "◀ Назад")
        e.t["v"] += 60
        e.bot.panel_tick()
        self.assertIn("Квота Claude", e.tg.of("editMessageText")[-1]["text"])  # ещё в режиме квоты
        e.t["v"] += 61
        e.bot.panel_tick()
        self.assertIn("Сессий нет", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.cb("pm:quota"))
        e.bot.handle_update(e.cb("pm:list"))
        self.assertIn("Сессий нет", e.tg.of("editMessageText")[-1]["text"])
        self.assertEqual(len(panel_msgs(e)), 1)  # квота — не отдельное сообщение

    def test_new_session_confirmation_survives_idle(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg("важная задача", mid=9, thread=pth(e)))
        e.t["v"] += 121
        e.bot.panel_tick()
        self.assertIsNotNone(e.bot.store.chat(1)["pending_task"])
        self.assertIn("Новая сессия", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.cb("ngo"))
        self.assertEqual([r for r in e.runs if "--bg" in r[0]][-1][0][-1], "важная задача")

    def test_help_mode(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:help"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("Как пользоваться", ed["text"])
        self.assertIn("Нажмите сессию в пульте", ed["text"])
        self.assertEqual(ed["reply_markup"]["inline_keyboard"][-1][0]["callback_data"], "pm:list")

    def test_clean_done_topics_mode_with_confirmation(self):
        e = panel_env(self)
        mk(e, "dn", "done", 1)
        mk(e, "wk", "working", 1)
        mk(e, "fl", "failed", 1)
        for sid in ("dn", "wk", "fl"):
            e.bot.ensure_topic(1, sid)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:clean"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("Будет удалено топиков: 2", ed["text"])
        self.assertEqual(e.tg.of("deleteForumTopic"), [])
        e.bot.handle_update(e.cb("pcy"))
        self.assertEqual(len(e.tg.of("deleteForumTopic")), 2)
        self.assertEqual(list(e.bot.store.chat(1)["topics"]), ["wk"])
        self.assertIn("Убрано топиков: 2", e.tg.of("editMessageText")[-1]["text"])
        self.assertFalse([r for r in e.runs if "stop" in r[0]])

    def test_deleted_panel_topic_is_recreated(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        old = pth(e)
        mk(e, "a1", "working", 1)
        e.tg.errors["editMessageText"] = (lambda p: p.get("message_id") == e.bot.panel_state(1)["mid"],
                                          "Bad Request: message thread not found")
        e.bot.panel_publish(1)
        self.assertEqual([c["name"] for c in e.tg.of("createForumTopic")], ["🎛 Агенты", "🎛 Агенты"])
        self.assertNotEqual(pth(e), old)
        self.assertEqual(e.tg.of("sendMessage")[-1]["message_thread_id"], pth(e))
        self.assertEqual(len(e.tg.of("pinChatMessage")), 2)

    def test_edit_not_found_sends_new_pinned_message(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        first = e.bot.panel_state(1)["mid"]
        mk(e, "a1", "working", 1)
        e.tg.errors["editMessageText"] = (lambda p: True, "Bad Request: message to edit not found")
        e.bot.panel_publish(1)
        self.assertNotEqual(e.bot.panel_state(1)["mid"], first)
        self.assertEqual(len(e.tg.of("createForumTopic")), 1)  # тема на месте
        self.assertEqual(len(e.tg.of("pinChatMessage")), 2)
        self.assertEqual(e.tg.of("sendMessage")[-1]["message_thread_id"], pth(e))

    def test_start_removes_reply_keyboard_and_recreates_panel(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        old = e.bot.panel_state(1)["mid"]
        e.bot.handle_update(e.msg("/start", mid=3))
        self.assertIn(old, [d["message_id"] for d in e.tg.of("deleteMessage")])
        self.assertNotEqual(e.bot.panel_state(1)["mid"], old)
        main = [m for m in e.tg.of("sendMessage") if "message_thread_id" not in m]
        self.assertEqual(main[-1]["text"], "Пульт — в топике «🎛 Агенты».")
        self.assertEqual(main[-1]["reply_markup"], {"remove_keyboard": True})
        hint_mid = e.bot.temp[-1][2]
        e.t["v"] += 61
        e.bot.temp_tick()  # подсказка в главном чате самоудаляется
        self.assertIn(hint_mid, [d["message_id"] for d in e.tg.of("deleteMessage")])

    def test_s_recreates_panel_with_hint(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg("/s", mid=3))
        main = [m for m in e.tg.of("sendMessage") if "message_thread_id" not in m]
        self.assertEqual(main[-1]["text"], "Пульт — в топике «🎛 Агенты».")
        self.assertNotIn("reply_markup", main[-1])

    def test_fallback_without_topics_panel_in_main_chat(self):
        e = panel_env(self, topics=False)
        mk(e, "a1", "working", 1)
        e.bot.handle_update(e.msg("/s", mid=3))
        self.assertEqual(e.tg.of("createForumTopic"), [])
        m = e.tg.of("sendMessage")[-1]
        self.assertNotIn("message_thread_id", m)
        self.assertIn("🔵 1 в работе", m["text"])
        self.assertIn("➕ Новая", str(m["reply_markup"]))
        # /start в обычном чате убирает клавиатуру
        e.bot.handle_update(e.msg("/start", mid=4))
        rm = [x for x in e.tg.of("sendMessage") if x.get("reply_markup") == {"remove_keyboard": True}]
        self.assertEqual(len(rm), 1)
        # открытие номера работает как раньше: текущая сессия + карточка, в пульте появляется ▶
        e.bot.handle_update(e.cb("s:a1"))
        self.assertEqual(e.bot.store.chat(1)["session"], "a1")
        # в новом виде пульта отметки ▶ у текущей сессии нет (см. отчёт)

    def test_old_tracker_message_is_removed(self):
        e = panel_env(self)
        e.bot.store.data["tracker"]["1"] = {"mid": 55, "text": "📌 Трекер"}
        e.bot.store.data["singles"]["1:list"] = 56
        e.bot.remove_old_tracker()
        self.assertEqual(sorted(d["message_id"] for d in e.tg.of("deleteMessage")), [55, 56])
        self.assertEqual(e.bot.store.data["tracker"], {})

    def test_clean_keeps_panel_message(self):
        e = panel_env(self, topics=False)
        e.bot.handle_update(e.msg("/s", mid=11))
        panel = e.bot.panel_state(1)["mid"]
        e.bot.handle_update(e.msg(cc.btn("clean"), mid=13))
        ids = [i for c in e.tg.of("deleteMessages") for i in c["message_ids"]]
        self.assertNotIn(panel, ids)
        self.assertIn(11, ids)
        self.assertIn(13, ids)

    def test_tk_main_chat_text_not_forwarded_to_current_session(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.msg("просто так", mid=9))
        self.assertEqual(e.sent, [])
        self.assertNotIn(cc.hint_main(), [m["text"] for m in e.tg.of("sendMessage")])


# ---------------------------------------------------------------- автоимена

def names_env(tc, topics=False):
    e = panel_env(tc, topics)
    e.fs = FakeScreen(EMPTY_PROMPT)
    e.bot.tmux = e.fs.tmux()
    e.names_reply = ""
    e.name_inputs = []
    orig = e.runner

    def runner(args, timeout=30, cwd=None, input_text=None):
        if "-p" in args:
            e.runs.append((args, cwd))
            e.name_inputs.append(input_text)
            return 0, e.names_reply
        return orig(args, timeout, cwd, input_text)
    e.bot.run = runner

    def inj(sid, text):
        e.sent.append((sid, text))
        if text.startswith("/rename "):
            for a in e.agents:
                if a["id"] == sid:
                    a["name"] = text[len("/rename "):]
                    path = os.path.join(e.jobs, sid, "state.json")
                    with open(path) as f:
                        st = json.load(f)
                    st["name"], st["nameSource"] = a["name"], "user"
                    with open(path, "w") as f:
                        json.dump(st, f)
        return True, ""
    e.bot.injector = inj
    return e


def mkn(e, sid, state="done", pid=None, name="billing service handoff reading", source="auto", minutes=1,
        task="Починить импорт писем"):
    e.job(sid, name=name, state=state, detail="detail " + sid, cwd="/tmp", nameSource=source, intent=task,
          updatedAt=iso(BASE - minutes * 60))
    a = {"id": sid, "name": name, "state": state, "cwd": "/tmp"}
    if pid:
        a["pid"] = pid
    e.agents.append(a)


def reply_for(**names):
    return "Вот имена:\n" + json.dumps(names, ensure_ascii=False) + "\nГотово."


class NameParsingTests(unittest.TestCase):
    def test_parse_noise_extra_keys_long_and_non_strings(self):
        out = ('Конечно! ```json\n{"a": "Проект: дизайн", "zzz": "Чужой", "b": "' + "очень длинное имя " * 5
               + '", "c": 42, "d": "  «Сайт: вход».  ", "e": ""}\n``` Удачи')
        res = cc.parse_names(out, {"a", "b", "c", "d", "e"})
        self.assertEqual(res["a"], "Проект: дизайн")
        self.assertNotIn("zzz", res)
        self.assertLessEqual(len(res["b"]), 28)
        self.assertNotIn("c", res)
        self.assertEqual(res["d"], "Сайт: вход")
        self.assertNotIn("e", res)

    def test_parse_garbage(self):
        self.assertEqual(cc.parse_names("нет json", {"a"}), {})
        self.assertEqual(cc.parse_names("{битый", {"a"}), {})
        self.assertEqual(cc.parse_names('["a"]', {"a"}), {})
        self.assertEqual(cc.parse_names(None, {"a"}), {})

    def test_prompt_constant(self):
        self.assertIn("не длиннее 28 символов", cc.name_prompt())
        self.assertTrue(cc.name_prompt().endswith("Сессии:"))


class AutoNameTests(unittest.TestCase):
    def test_candidates_only_auto_and_unprocessed(self):
        e = names_env(self)
        mkn(e, "a1", source="auto")
        mkn(e, "u1", source="user", name="Моё имя")
        mkn(e, "n1", source="auto", name="n1")
        e.bot.store.data["names"]["done1"] = {"name": "x"}
        mkn(e, "done1", source="auto")
        sessions = e.bot.list_sessions(True)
        self.assertEqual(sorted(x["id"] for x in e.bot.name_candidates(sessions)), ["a1", "n1"])

    def test_suggest_names_calls_haiku_with_payload_and_handles_failure(self):
        e = names_env(self)
        mkn(e, "a1", task="Починить импорт писем")
        e.names_reply = reply_for(a1="Почта: импорт")
        sessions = e.bot.list_sessions(True)
        self.assertEqual(e.bot.suggest_names(sessions), {"a1": "Почта: импорт"})
        cmd = [r for r in e.runs if "-p" in r[0]][-1][0]
        self.assertEqual(cmd[1:], ["-p", "--model", "haiku", "--output-format", "text"])
        sent = e.name_inputs[-1]
        self.assertTrue(sent.startswith(cc.name_prompt()))
        payload = json.loads(sent[len(cc.name_prompt()):])
        self.assertEqual(payload[0]["id"], "a1")
        self.assertEqual(payload[0]["task"], "Починить импорт писем")
        self.assertEqual(payload[0]["status"], "detail a1")
        e.bot.run = lambda *a, **k: (1, "")
        self.assertEqual(e.bot.suggest_names(sessions), {})

    def test_generation_creates_unapproved_proposals_and_skips_junk(self):
        e = names_env(self)
        mkn(e, "a1")
        mkn(e, "a2")
        e.names_reply = reply_for(a1="Почта: импорт", a2="Без задачи")
        e.bot.names_tick()
        names = e.bot.store.data["names"]
        self.assertEqual(names["a1"]["name"], "Почта: импорт")
        self.assertFalse(names["a1"]["approved"])
        self.assertTrue(names["a2"]["skip"])
        self.assertEqual(e.bot.name_proposals(), [("a1", "billing service handoff reading", "Почта: импорт")])
        self.assertEqual(e.sent, [])  # без согласия ничего не переименовано
        # не чаще раза в 10 минут
        n = len(e.name_inputs)
        mkn(e, "a3")
        e.t["v"] += 300
        e.bot.names_tick()
        self.assertEqual(len(e.name_inputs), n)
        e.t["v"] += 400
        e.bot.names_tick()
        self.assertEqual(len(e.name_inputs), n + 1)

    def test_batches_up_to_60(self):
        e = names_env(self)
        for i in range(75):
            mkn(e, "s%03d" % i)
        e.names_reply = "{}"
        e.bot.names_tick()
        payload = json.loads(e.name_inputs[0][len(cc.name_prompt()):])
        self.assertEqual(len(payload), 60)

    def test_live_idle_session_gets_rename_and_is_applied_after_confirmation(self):
        e = names_env(self)
        mkn(e, "a1", state="done", pid=111)
        e.names_reply = reply_for(a1="Почта: импорт")
        e.bot.names_tick()
        e.bot.handle_update(e.cb("pm:names"))
        e.bot.handle_update(e.cb("nm:all"))
        self.assertEqual(e.sent, [("a1", "/rename Почта: импорт")])
        rec = e.bot.store.data["names"]["a1"]
        self.assertTrue(rec["applied"])
        self.assertFalse(rec["alias"])
        self.assertEqual(e.bot.display_name("a1"), "Почта: импорт")  # теперь настоящее имя из agents
        e.bot.names_tick()
        self.assertEqual(len(e.sent), 1)  # повторно не переименовываем

    def test_rename_not_confirmed_keeps_alias_and_retries_at_most_3_times(self):
        e = names_env(self)
        mkn(e, "a1", pid=1)
        e.bot.store.data["names"]["a1"] = {"name": "Новое", "approved": True, "applied": False, "alias": False,
                                           "source_name": "x"}
        e.bot.injector = lambda sid, text: (e.sent.append((sid, text)) or (True, ""))  # имя в agents не меняется
        for _ in range(5):
            e.bot.names_tick()
        self.assertEqual(len(e.sent), 3)
        self.assertEqual(e.bot.display_name("a1"), "Новое")  # алиас остался

    def test_live_working_session_is_renamed_right_away(self):
        e = names_env(self)
        mkn(e, "a1", state="working", pid=7)
        e.bot.store.data["names"]["a1"] = {"name": "Задача: тест", "approved": True, "applied": False,
                                           "alias": False, "source_name": "x"}
        e.bot.names_tick()
        self.assertEqual(e.sent, [("a1", "/rename Задача: тест")])  # /rename срабатывает и во время работы

    def test_stopped_session_renamed_offline_in_transcript_and_state(self):
        e = names_env(self)
        mkn(e, "s1", state="done", pid=None)
        d = tempfile.mkdtemp(); self.addCleanup(lambda: __import__("shutil").rmtree(d))
        tr = os.path.join(d, "t.jsonl"); open(tr, "w").write('{"type":"user"}\n')
        st_path = os.path.join(e.bot.cfg.jobs_dir, "s1", "state.json")
        st = json.load(open(st_path)); st.update({"linkScanPath": tr, "sessionId": "uuid-s1"}); json.dump(st, open(st_path, "w"))
        e.agents[-1]["sessionId"] = "uuid-s1"
        e.bot.store.data["names"]["s1"] = {"name": "Архив: отчёт", "approved": True, "applied": False,
                                           "alias": False, "source_name": "x"}
        e.bot.names_tick()
        self.assertEqual(e.fs.calls, [])  # к остановленной не подключаемся
        self.assertTrue(e.bot.store.data["names"]["s1"]["applied"])
        self.assertEqual(json.load(open(st_path))["name"], "Архив: отчёт")
        lines = [json.loads(l) for l in open(tr)]
        self.assertEqual(lines[-2], {"type": "custom-title", "customTitle": "Архив: отчёт", "sessionId": "uuid-s1"})
        self.assertEqual(lines[-1]["type"], "agent-name")

    def test_stopped_session_without_transcript_renamed_via_state_json(self):
        e = names_env(self)
        mkn(e, "a1", state="done", pid=None)
        e.bot.store.data["names"]["a1"] = {"name": "Архив: отчёт", "approved": True, "applied": False,
                                           "alias": False, "source_name": "x"}
        e.bot.names_tick()
        self.assertEqual(e.fs.calls, [])  # ни has-session, ни attach
        self.assertEqual(e.sent, [])
        rec = e.bot.store.data["names"]["a1"]
        self.assertTrue(rec["applied"] and not rec["alias"])
        st = json.load(open(os.path.join(e.bot.cfg.jobs_dir, "a1", "state.json")))
        self.assertEqual((st["name"], st["nameSource"]), ("Архив: отчёт", "user"))

    def test_busy_input_or_menu_is_not_touched(self):
        e = names_env(self)
        mkn(e, "a1", pid=1)
        e.bot.store.data["names"]["a1"] = {"name": "Имя", "approved": True, "applied": False, "alias": False,
                                           "source_name": "x"}
        e.fs.screen = "%s\n❯ владелец печатает\n%s\n" % (BORDER, BORDER)
        e.bot.names_tick()
        self.assertEqual(e.sent, [])
        self.assertEqual(e.fs.keys, [])  # C-u не нажимали
        e.fs.screen = MENU_S
        e.bot.names_tick()
        self.assertEqual(e.sent, [])
        e.fs.screen = EMPTY_PROMPT
        e.bot.names_tick()
        self.assertEqual(len(e.sent), 1)

    def test_one_rename_per_tick(self):
        e = names_env(self)
        for sid in ("a1", "a2", "a3"):
            mkn(e, sid, pid=1)
            e.bot.store.data["names"][sid] = {"name": "Имя " + sid, "approved": True, "applied": False,
                                              "alias": False, "source_name": "x"}
        e.bot.names_tick()
        self.assertEqual(len(e.sent), 1)
        e.bot.names_tick()
        self.assertEqual(len(e.sent), 2)

    def test_display_name_in_panel_buttons_card_and_topic_title(self):
        e = names_env(self, topics=True)
        mkn(e, "a1", state="working", pid=None, name="NEW-SESSION-PROMPT.md")
        e.bot.panel_publish(1)
        e.bot.ensure_topic(1, "a1")
        thread = e.bot.store.chat(1)["topics"]["a1"]["thread"]
        e.bot.store.data["names"]["a1"] = {"name": "Проект: дизайн", "approved": True, "applied": False,
                                           "alias": True, "source_name": "x"}
        html, plain, markup = e.bot.panel_render(1)
        self.assertIn("Проект: дизайн", plain)
        self.assertNotIn("NEW-SESSION-PROMPT", plain)
        self.assertIn("Проект: дизайн", markup["inline_keyboard"][0][0]["text"])
        self.assertTrue(e.bot.card_text("a1").startswith("🔵 Проект: дизайн"))
        self.assertEqual(e.bot.topic_title("a1"), "Проект: дизайн")
        e.t["v"] += 11
        e.bot.topics_tick()
        e.t["v"] += cc.TOPIC_RENAME_SETTLE
        e.bot.topics_tick()
        ed = e.tg.of("editForumTopic")[-1]
        self.assertEqual((ed["name"], ed["message_thread_id"]), ("Проект: дизайн", thread))
        # уведомление тоже использует алиас
        e.bot.set_watch(1, "a1", True)
        e.bot.notify_tick()  # первый проход только запоминает состояния
        e.agents[0]["state"] = "blocked"
        e.job("a1", name="NEW-SESSION-PROMPT.md", state="blocked", detail="нужен ответ")
        e.bot.notify_tick()
        self.assertTrue(any("Проект: дизайн ждёт" in m["text"] for m in e.tg.of("sendMessage")))


class NamesPanelTests(unittest.TestCase):
    def setUp(self):
        self.e = names_env(self)

    def seed(self, n):
        e = self.e
        for i in range(n):
            sid = "s%02d" % i
            mkn(e, sid, name="old name %d" % i)
            e.bot.store.data["names"][sid] = {"name": "Имя %d" % i, "approved": False, "applied": False,
                                              "alias": False, "source_name": "old name %d" % i}

    def test_list_bottom_row_has_names_button_short_labels(self):
        e = self.e
        mkn(e, "a1", state="working")
        mkn(e, "d1", state="done")
        _, _, markup = e.bot.panel_render(1)
        rows = markup["inline_keyboard"][-3:-1]  # последний ряд — «➕ Новая» (режим без топиков)
        self.assertEqual([[b["text"] for b in r] for r in rows],
                         [["🗂 Архив 1", "📊 Квота"], ["✏️ Имена", "❓ Справка"]])
        self.assertEqual(rows[1][0]["callback_data"], "pm:names")
        self.assertTrue(all(len(r) == 2 for r in rows))  # «🗂 Архив N» не лезет в ряд из четырёх

    def test_names_mode_lists_old_to_new_with_paging(self):
        e = self.e
        self.seed(10)
        e.bot.store.data["names_last_gen"] = e.t["v"]  # генерацию не запускаем
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:names"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("Новые имена", ed["text"])
        self.assertIn("<b>Имя 0</b>\n<i>было: old name 0</i>", ed["text"])
        self.assertIn("<b>Имя 7</b>\n<i>было: old name 7</i>", ed["text"])
        self.assertNotIn("old name 8", ed["text"])
        flat = [b["text"] for r in ed["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(flat[:2], ["✅ Применить все", "🔁 Авто: выкл"])
        self.assertIn("1 / 2", flat)
        self.assertIn("Ещё ▶", flat)
        self.assertEqual(flat[-1], "◀ Назад")
        e.bot.handle_update(e.cb("nm:1"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("<b>Имя 8</b>\n<i>было: old name 8</i>", ed["text"])
        self.assertNotIn("old name 0 →", ed["text"])

    def test_apply_all_approves_and_applies_to_live_and_aliases_stopped(self):
        e = self.e
        mkn(e, "live", pid=5, name="old live")
        mkn(e, "dead", pid=None, name="old dead")
        for sid in ("live", "dead"):
            e.bot.store.data["names"][sid] = {"name": "Новое " + sid, "approved": False, "applied": False,
                                              "alias": False, "source_name": "old " + sid}
        e.bot.store.data["names_last_gen"] = e.t["v"]
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:names"))
        e.bot.handle_update(e.cb("nm:all"))
        self.assertEqual(e.sent, [("live", "/rename Новое live")])
        self.assertEqual(e.bot.display_name("dead"), "Новое dead")
        self.assertIn("Применяю имена: 2", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.cb("pm:names"))
        self.assertIn("Все имена уже понятные.", e.tg.of("editMessageText")[-1]["text"])

    def test_auto_toggle_and_auto_approval_of_new_proposals(self):
        e = self.e
        e.bot.store.data["names_last_gen"] = e.t["v"]
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:names"))
        self.assertIn("Все имена уже понятные.", e.tg.of("editMessageText")[-1]["text"])
        self.assertIn("🔁 Авто: выкл", str(e.tg.of("editMessageText")[-1]["reply_markup"]))
        e.bot.handle_update(e.cb("nm:auto"))
        self.assertTrue(e.bot.store.data["auto_names"])
        self.assertIn("🔁 Авто: вкл", str(e.tg.of("editMessageText")[-1]["reply_markup"]))
        mkn(e, "a1", pid=3)
        e.names_reply = reply_for(a1="Почта: импорт")
        e.t["v"] += 700
        e.bot.names_tick()  # генерация -> сразу одобрено -> применено в тот же тик
        self.assertEqual(e.sent, [("a1", "/rename Почта: импорт")])
        e.bot.handle_update(e.cb("nm:auto"))
        self.assertFalse(e.bot.store.data["auto_names"])

    def test_generating_state_and_empty_state(self):
        e = self.e
        e.bot.names_busy = True
        e.bot.panel_mode(1, "names")
        _, plain, markup = e.bot.panel_render(1)
        self.assertIn("Придумываю имена…", plain)
        self.assertEqual([b["text"] for r in markup["inline_keyboard"] for b in r], ["◀ Назад"])
        e.bot.names_busy = False
        _, plain, _ = e.bot.panel_render(1)
        self.assertIn("Все имена уже понятные.", plain)

    def test_opening_names_starts_generation(self):
        e = self.e
        mkn(e, "a1", pid=1)
        e.names_reply = reply_for(a1="Почта: импорт")
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:names"))
        self.assertEqual(len(e.name_inputs), 1)
        self.assertIn("Почта: импорт", e.tg.of("editMessageText")[-1]["text"])

    def test_names_mode_auto_returns_to_list(self):
        e = self.e
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:names"))
        e.t["v"] += 121
        e.bot.panel_tick()
        self.assertEqual(e.bot.panel_state(1)["mode"], "list")


class ManualAndNewNameTests(unittest.TestCase):
    def test_card_has_rename_button(self):
        flat = [b["text"] for r in cc.card_markup("a")["inline_keyboard"] for b in r]
        self.assertIn("✏️ Имя", flat)
        self.assertEqual([b["callback_data"] for r in cc.card_markup("a")["inline_keyboard"] for b in r
                          if b["text"] == "✏️ Имя"], ["rn:a"])

    def test_manual_rename_via_force_reply_and_manual_is_protected(self):
        e = names_env(self)
        mkn(e, "a1", pid=9)
        e.bot.handle_update(e.cb("rn:a1"))
        fr = e.tg.of("sendMessage")[-1]
        self.assertTrue(fr["text"].startswith("Новое имя для «"))
        self.assertEqual(fr["reply_markup"]["input_field_placeholder"], "до 28 символов")
        self.assertEqual(fr["reply_markup"]["force_reply"], True)
        e.bot.handle_update(e.msg("«Моё важное дело и ещё очень длинный хвост».", mid=50))
        self.assertEqual(e.sent, [("a1", "/rename " + cc.clean_name("Моё важное дело и ещё очень длинный хвост"))])
        rec = e.bot.store.data["names"]["a1"]
        self.assertTrue(rec["manual"] and rec["applied"])
        self.assertIsNone(e.bot.store.chat(1)["pending_rename"])
        confirm = e.tg.of("sendMessage")[-1]["text"]
        self.assertIn("Имя обновлено", confirm)
        # подтверждение и сообщение владельца самоудаляются
        e.t["v"] += 61
        e.bot.temp_tick()
        self.assertIn(50, [d["message_id"] for d in e.tg.of("deleteMessage")])
        # автоматика ручное имя не трогает
        self.assertEqual(e.bot.apply_name("a1", "Автоимя", manual=False), "manual")
        self.assertEqual(e.bot.store.data["names"]["a1"]["name"], rec["name"])
        self.assertNotIn("a1", [x["id"] for x in e.bot.name_candidates(e.bot.list_sessions(True))])

    def test_manual_rename_of_stopped_session_is_alias_without_attach(self):
        e = names_env(self)
        mkn(e, "a1", pid=None)
        e.bot.handle_update(e.cb("rn:a1"))
        e.bot.handle_update(e.msg("Ручное", mid=51))
        self.assertEqual(e.fs.calls, [])
        self.assertEqual(e.bot.display_name("a1"), "Ручное")
        self.assertIn("Имя обновлено", e.tg.of("sendMessage")[-1]["text"])  # остановленная: имя записано в state.json

    def test_command_cancels_pending_rename(self):
        e = names_env(self)
        mkn(e, "a1", pid=9)
        e.bot.handle_update(e.cb("rn:a1"))
        e.bot.handle_update(e.msg("/quota", mid=52))
        self.assertIsNone(e.bot.store.chat(1)["pending_rename"])

    def test_new_session_from_bot_gets_name_from_task(self):
        e = names_env(self)
        e.names_reply = reply_for(abcd1234="Тесты: прогон набора")
        e.bot.handle_update(e.msg("/new прогони весь набор тестов"))
        payload = json.loads(e.name_inputs[0][len(cc.name_prompt()):])
        self.assertEqual(payload[0]["id"], "abcd1234")
        self.assertEqual(payload[0]["task"], "прогони весь набор тестов")
        rec = e.bot.store.data["names"]["abcd1234"]
        self.assertTrue(rec["approved"])
        self.assertEqual(e.bot.display_name("abcd1234"), "Тесты: прогон набора")  # сразу, пока сессия работает
        self.assertEqual(e.sent, [])  # working -> /rename отложен
        # сессия появилась в agents и перестала работать
        mkn(e, "abcd1234", state="done", pid=77, name="Новая")
        e.bot.names_tick()
        self.assertEqual(e.sent, [("abcd1234", "/rename Тесты: прогон набора")])

    def test_new_session_junk_name_is_ignored(self):
        e = names_env(self)
        e.names_reply = reply_for(abcd1234="Без задачи")
        e.bot.handle_update(e.msg("/new ээээ"))
        self.assertNotIn("abcd1234", e.bot.store.data["names"])


# ---------------------------------------------------------------- цепочки хендоффов

HO = "/home/x/proj/HANDOFF-20261013-1000.md"


def write_line(path, ts_iso, tool="Write"):
    return line({"type": "assistant", "isSidechain": False, "uuid": "w" + ts_iso, "timestamp": ts_iso,
                 "message": {"content": [{"type": "tool_use", "id": "t1", "name": tool,
                                          "input": {"file_path": path, "content": "x"}}]}})


def chain_session(e, sid, created_min, state="working", intent="", transcript=b"", name=None, pid=None):
    tr = os.path.join(e.tmp.name, sid + ".jsonl")
    with open(tr, "wb") as f:
        f.write(transcript)
    e.job(sid, name=name or sid, state=state, detail="detail " + sid, cwd="/tmp", intent=intent,
          createdAt=iso(BASE - created_min * 60), updatedAt=iso(BASE - 60), linkScanPath=tr)
    a = {"id": sid, "name": name or sid, "state": state, "cwd": "/tmp"}
    if pid:
        a["pid"] = pid
    e.agents.append(a)


class ChainTests(unittest.TestCase):
    def test_handoff_paths_regex(self):
        home = "/home/u"
        txt = ("Продолжи по ~/proj/HANDOFF-1.md и ещё (/a/b/handoff-2.md), а /a/readme.md не хендофф; "
               "повтор ~/proj/HANDOFF-1.md и ../rel/handoff.md")
        self.assertEqual(cc.handoff_paths(txt, home), ["/home/u/proj/HANDOFF-1.md", "/a/b/handoff-2.md"])
        self.assertEqual(cc.handoff_paths("/a/x/../y/HandOff.md", home), ["/a/y/HandOff.md"])
        self.assertEqual(cc.handoff_paths(None, home), [])

    def test_spinoff_writer_still_working_is_not_superseded(self):
        # the session wrote a handoff for a parallel project and keeps working: not a continuation
        e = panel_env(self)
        chain_session(e, "p1", 100, "working", transcript=write_line(HO, iso(BASE - 90 * 60)))
        chain_session(e, "c1", 60, "working", intent="Прочитай %s и веди новый проект" % HO)
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {})
        self.assertFalse(e.bot.is_old("p1"))

    def test_chain_built_by_full_path(self):
        e = panel_env(self)
        chain_session(e, "p1", 100, "done", transcript=write_line(HO, iso(BASE - 90 * 60)))
        chain_session(e, "c1", 60, "working", intent="Прочитай %s и продолжай" % HO)
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {"c1": "p1"})
        self.assertTrue(e.bot.is_old("p1"))
        self.assertFalse(e.bot.is_old("c1"))
        self.assertEqual(e.bot.chain_k("c1"), 2)
        self.assertEqual(e.bot.chain_head("p1"), "c1")

    def test_no_false_link_for_same_basename_in_other_folder(self):
        e = panel_env(self)
        other = "/home/x/other/HANDOFF-20261013-1000.md"  # тот же basename, другая папка
        chain_session(e, "p1", 100, "done", transcript=write_line(HO, iso(BASE - 90 * 60)))
        chain_session(e, "p2", 100, "done", transcript=write_line(other, iso(BASE - 90 * 60)))
        chain_session(e, "c2", 60, "working", intent="Продолжи по " + other)
        chain_session(e, "c3", 50, "working", intent="Продолжи по /home/x/third/HANDOFF-20261013-1000.md")
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {"c2": "p2"})

    def test_write_after_child_creation_is_not_a_parent(self):
        e = panel_env(self)
        chain_session(e, "late", 100, "done", transcript=write_line(HO, iso(BASE - 10 * 60)))  # позже ребёнка
        chain_session(e, "c1", 60, "working", intent="Продолжи по " + HO)
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {})

    def test_edit_counts_and_latest_write_before_child_wins(self):
        e = panel_env(self)
        chain_session(e, "p_old", 200, "done", transcript=write_line(HO, iso(BASE - 190 * 60)))
        chain_session(e, "p_new", 150, "done", transcript=write_line(HO, iso(BASE - 100 * 60), "Edit"))
        chain_session(e, "c1", 60, "working", intent=HO)
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {"c1": "p_new"})

    def test_incremental_scan_reads_only_new_bytes(self):
        e = panel_env(self)
        first = write_line(HO, iso(BASE - 90 * 60)) + line({"type": "user", "message": {"content": "x"}})
        chain_session(e, "p1", 100, "done", transcript=first)
        chain_session(e, "c1", 60, "working", intent=HO)
        with mock.patch.object(cc.json, "loads", wraps=json.loads) as loads:
            e.bot.chains_scan()
            relevant_first = [c for c in loads.call_args_list if isinstance(c.args[0], bytes)
                              and b"andoff" in c.args[0].lower()]
        self.assertEqual(len(relevant_first), 1)  # json.loads только для подходящей строки транскрипта
        ent = e.bot.store.data["chain_scan"]["p1"]
        self.assertEqual(ent["offset"], len(first))
        self.assertEqual(list(ent["writes"]), [os.path.normpath(HO)])
        other = "/home/x/proj/HANDOFF-NEW.md"
        extra = write_line(other, iso(BASE - 30 * 60)) + b'{"type": "assistant", "x": "Write tool_use andoff"'  # хвост не дописан
        with open(os.path.join(e.tmp.name, "p1.jsonl"), "ab") as f:
            f.write(extra)
        calls = []
        real = cc.scan_handoff_writes

        def spy(path, offset, *a, **k):
            r = real(path, offset, *a, **k)
            calls.append((os.path.basename(path), offset, r[1]))
            return r
        with mock.patch.object(cc, "scan_handoff_writes", spy):
            e.bot.chains_scan()
        p1 = [c for c in calls if c[0] == "p1.jsonl"][0]
        self.assertEqual(p1[1], len(first))  # читаем с прошлого смещения
        self.assertEqual(p1[2], len(first) + len(write_line(other, iso(BASE - 30 * 60))))  # недописанная строка осталась
        self.assertIn(os.path.normpath(other), e.bot.store.data["chain_scan"]["p1"]["writes"])
        self.assertIn(os.path.normpath(HO), e.bot.store.data["chain_scan"]["p1"]["writes"])

    def chain3(self, topics=False):
        e = panel_env(self, topics)
        chain_session(e, "A", 300, "blocked", name="Сайт: бэкенд")
        chain_session(e, "B", 200, "working", name="Сайт: продолжение")
        chain_session(e, "C", 100, "working", name="Сайт: хендофф 22")
        e.bot.store.data["chain_links"] = {"B": "A", "C": "B"}
        e.bot.list_sessions(True)
        return e

    def test_panel_hides_superseded_counts_and_marks_continuation(self):
        e = self.chain3()
        html, plain, markup = e.bot.panel_render(1)
        lines = plain.split("\n")
        self.assertEqual(lines[0], "🔵 1 в работе")  # счётчик сменившихся только в архиве
        self.assertNotIn("ЖДУТ ВАС", plain)  # A blocked, но сменилась
        self.assertEqual([r[0]["callback_data"] for r in markup["inline_keyboard"] if r[0]["callback_data"].startswith("s:")],
                         ["s:C"])
        self.assertIn("<b>Сайт: хендофф 22 №3</b>\n<i>↪ текущая · detail C", html)
        self.assertNotIn("Сайт: бэкенд", plain)

    def test_archive_mode_has_two_sections(self):
        e = self.chain3()
        chain_session(e, "D", 400, "done", name="Готовая")
        e.bot.panel_publish(1)
        _, plain, markup = e.bot.panel_render(1)
        self.assertIn("🗂 Архив 3", str(markup))  # 1 готовая + 2 сменившихся
        e.bot.handle_update(e.cb("pa:1"))
        ed = e.tg.of("editMessageText")[-1]["text"]
        self.assertLess(ed.index("СМЕНИЛИСЬ"), ed.index("ГОТОВО"))
        self.assertIn("→ продолжена в «Сайт: хендофф 22 №3»", ed)  # A -> B, B -> C (по отображаемым именам)
        self.assertIn("→ продолжена в «🗂 Сайт: продолжение №2»", ed)
        self.assertIn("Готовая", ed)
        self.assertNotIn("Сайт: продолжение\n<i>detail", ed)

    def test_manual_marks_override_automatics(self):
        e = self.chain3()
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("cm:C"))  # текущую вручную пометили старой
        self.assertEqual(e.bot.store.data["chain_manual"]["C"], "old")
        self.assertTrue(e.bot.is_old("C"))
        self.assertIn("🗂 Архив", str(e.tg.of("editMessageText")[-1]["reply_markup"]))  # число старых — на кнопке архива
        e.bot.handle_update(e.cb("cm:A"))  # автоматически старая -> вручную текущая
        self.assertEqual(e.bot.store.data["chain_manual"]["A"], "current")
        self.assertFalse(e.bot.is_old("A"))
        self.assertIn("ЖДУТ ВАС", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.cb("cm:C"))
        self.assertEqual(e.bot.store.data["chain_manual"]["C"], "current")
        e.bot.chains_scan()  # пересчёт автоматики ручное не затирает
        self.assertEqual(e.bot.store.data["chain_manual"]["A"], "current")

    def test_card_with_open_question_says_waiting_not_running(self):
        e = self.chain3()
        e.bot.store.data.setdefault("questions", {})["1:q1"] = {
            "sid": "C", "closed": False, "chat": 1, "tool_id": "q1", "step": 0,
            "questions": [{"question": "Какой размер?", "options": [{"label": "S"}, {"label": "M"}]}]}
        text = e.bot.card_text("C")
        self.assertTrue(text.startswith("❓ Сайт: хендофф 22"), text)
        self.assertIn("ждёт вас", text.splitlines()[1])
        self.assertNotIn("работает", text.splitlines()[1])
        self.assertNotIn("running", text)

    def test_open_question_on_old_session_is_not_asking(self):
        e = self.chain3()
        e.bot.store.data.setdefault("questions", {})["1:q2"] = {
            "sid": "A", "closed": False, "chat": 1, "tool_id": "q2", "step": 0,
            "questions": [{"question": "Какой размер?", "options": [{"label": "S"}, {"label": "M"}]}]}
        self.assertFalse(e.bot.is_asking("A", e.bot.session_info("A")))
        self.assertFalse(e.bot.card_text("A").splitlines()[1].startswith("❓"))

    def test_card_of_old_session_and_jump_to_current(self):
        e = self.chain3(topics=True)
        text = e.bot.card_text("A")
        self.assertEqual(text.splitlines()[0], "🗂 Старая: продолжена в «🗂 Сайт: продолжение №2»")
        flat = {b["text"]: b["callback_data"] for r in cc.card_markup("A", True, False, True, True)["inline_keyboard"]
                for b in r}
        self.assertEqual(flat["➡ К текущей"], "ch:A")
        self.assertEqual(flat["↩ Не старая"], "cm:A")
        self.assertIn("🗂 Старая", [b["text"] for r in cc.card_markup("C", True, False, True, False)["inline_keyboard"]
                                   for b in r])
        cur = e.bot.card_text("C")
        self.assertIn("↪ Текущая (№3), предыдущая: «🗂 Сайт: продолжение №2»", cur)
        e.bot.handle_update(e.cb("ch:A"))
        th = thread_of(e, "C")  # открыт топик головы цепочки
        card = [m for m in e.tg.of("sendMessage") if m["text"].rstrip().endswith("· C")][-1]
        self.assertEqual(card["message_thread_id"], th)

    def test_card_uses_display_name_for_parent(self):
        e = self.chain3()
        e.bot.store.data["names"]["B"] = {"name": "Сайт: тесты", "approved": True, "applied": False,
                                          "alias": True, "source_name": "x"}
        self.assertIn("предыдущая: «🗂 Сайт: тесты №2»", e.bot.card_text("C"))
        self.assertIn("продолжена в «🗂 Сайт: тесты №2»", e.bot.card_text("A"))

    def test_topic_of_superseded_has_archive_prefix_and_is_cleaned_by_ttl(self):
        e = self.chain3(topics=True)
        e.bot.ensure_topic(1, "A")
        e.bot.ensure_topic(1, "C")
        self.assertEqual(e.bot.store.chat(1)["topics"]["A"]["title"], "🗂 Сайт: бэкенд №1")
        self.assertFalse(e.bot.store.chat(1)["topics"]["C"]["title"].startswith("🗂"))
        e.t["v"] += 25 * 3600
        e.bot.store.chat(1)["topics"]["C"]["activity"] = e.t["v"]
        e.bot.topic_cleanup()  # A blocked, но сменилась -> удаляется; C работает -> остаётся
        self.assertNotIn("A", e.bot.store.chat(1)["topics"])
        self.assertIn("C", e.bot.store.chat(1)["topics"])

    def test_notifications_and_questions_suppressed_for_superseded(self):
        e = self.chain3()
        e.bot.store.data["watched"] += ["A", "C"]
        e.agents[0]["state"] = "working"
        e.job("A", name="Сайт: бэкенд", state="working")
        e.job("C", name="Сайт: хендофф 22", state="working")
        e.bot.notify_tick()
        e.agents[0]["state"] = "blocked"
        e.agents[2]["state"] = "blocked"
        e.job("A", name="Сайт: бэкенд", state="blocked", detail="нужен ответ A")
        e.job("C", name="Сайт: хендофф 22", state="blocked", detail="нужен ответ C")
        e.bot.notify_tick()
        texts = [m["text"] for m in e.tg.of("sendMessage")]
        self.assertTrue(any("Сайт: хендофф 22 №3 ждёт" in x for x in texts))
        self.assertFalse(any("Сайт: бэкенд ждёт" in x for x in texts))
        # вопросы из сменившейся не отправляются
        qs = cc.normalize_questions({"questions": [Q_SIZE]})
        e.bot.on_ask(1, "A", {"tool_id": "t1", "questions": qs})
        self.assertEqual(e.bot.store.data["questions"], {})
        e.bot.on_ask(1, "C", {"tool_id": "t2", "questions": qs})
        self.assertEqual(len(e.bot.store.data["questions"]), 1)


# ---------------------------------------------------------------- мини-апп: HTTP API

import hashlib
import hmac
import http.client
import urllib.parse
import subprocess
import signal

TOKEN = "T"


def make_init_data(token=TOKEN, user_id=1, auth_date=None, bad_hash=False, drop_hash=False):
    fields = {"auth_date": str(int(auth_date if auth_date is not None else time.time())), "query_id": "AAHx",
              "user": json.dumps({"id": user_id, "first_name": "Владелец"}, ensure_ascii=False)}
    check = "\n".join("%s=%s" % (k, fields[k]) for k in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    if bad_hash:
        h = h[:-1] + ("0" if h[-1] != "0" else "1")
    if not drop_hash:
        fields["hash"] = h
    return urllib.parse.urlencode(fields)


class WebEnv:
    def __init__(self, tc, topics=False):
        self.e = names_env(tc, topics)
        e = self.e
        e.t["v"] = BASE
        self.static = os.path.join(e.tmp.name, "webapp")
        os.makedirs(self.static)
        with open(os.path.join(self.static, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!doctype html><title>Список</title>")
        with open(os.path.join(self.static, "app.js"), "w") as f:
            f.write("console.log(1)")
        with open(os.path.join(e.tmp.name, "secret.txt"), "w") as f:
            f.write("секрет")
        self.srv = cc.WebServer(e.bot, "127.0.0.1", 0, self.static)
        self.srv.start()
        tc.addCleanup(self.srv.stop)

    def req(self, method, path, body=None, headers=None, auth=True, raw_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.srv.port, timeout=10)
        h = dict(headers or {})
        if auth is True:
            h["X-Tg-Init-Data"] = make_init_data()
        elif isinstance(auth, str):
            h["X-Tg-Init-Data"] = auth
        data = raw_body
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json"
        conn.request(method, path, body=data, headers=h)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        try:
            obj = json.loads(payload.decode("utf-8"))
        except ValueError:
            obj = payload
        return r.status, obj, r


class ReportRouteTests(unittest.TestCase):
    def test_report_page_and_data_need_only_the_token(self):
        w = WebEnv(self)
        with open(os.path.join(w.static, "report.html"), "w") as f:
            f.write("<!doctype html><title>Отчёт</title>")
        w.e.bot.cfg.responder_log = os.path.join(w.e.tmp.name, "state", "responder.jsonl")
        w.e.bot.run = lambda args, timeout=30, cwd=None, input_text=None: (0, "")
        token, _ = w.e.bot.report_build()
        st, obj, _ = w.req("GET", "/r/%s.json" % token, auth=False)
        self.assertEqual((st, obj["counts"]["site"]), (200, 0))
        st, body, r = w.req("GET", "/r/%s" % token, auth=False)
        self.assertEqual(st, 200)
        self.assertIn("Отчёт", body.decode())
        self.assertEqual(w.req("GET", "/r/%s.json" % ("x" * 20), auth=False)[0], 404)


class InitDataTests(unittest.TestCase):
    def test_verify(self):
        now = 1_800_000_000
        self.assertEqual(cc.verify_init_data(make_init_data(auth_date=now), TOKEN, now)["id"], 1)
        self.assertIsNone(cc.verify_init_data(make_init_data(auth_date=now, bad_hash=True), TOKEN, now))
        self.assertIsNone(cc.verify_init_data(make_init_data(auth_date=now, drop_hash=True), TOKEN, now))
        self.assertIsNone(cc.verify_init_data(make_init_data(token="чужой", auth_date=now), TOKEN, now))
        self.assertIsNone(cc.verify_init_data(make_init_data(auth_date=now - 24 * 3600 - 5), TOKEN, now))
        self.assertIsNotNone(cc.verify_init_data(make_init_data(auth_date=now - 23 * 3600), TOKEN, now))
        self.assertIsNone(cc.verify_init_data(make_init_data(auth_date=now + 3600), TOKEN, now))
        self.assertIsNone(cc.verify_init_data("", TOKEN, now))
        self.assertIsNone(cc.verify_init_data("a=1&a=2&hash=x", TOKEN, now))
        self.assertIsNone(cc.verify_init_data(make_init_data(auth_date=now), "", now))


class WebAuthStaticTests(unittest.TestCase):
    def test_api_requires_valid_init_data_and_does_not_touch_store(self):
        w = WebEnv(self)
        e = w.e
        mk(e, "a1", "working", 1)
        e.bot.list_sessions(True)
        before = json.dumps(e.bot.store.data, sort_keys=True)
        runs = len(e.runs)
        cases = [make_init_data(bad_hash=True), make_init_data(auth_date=time.time() - 25 * 3600),
                 make_init_data(user_id=999), make_init_data(drop_hash=True), "", "garbage"]
        for init in cases:
            for method, path, body in (("GET", "/api/sessions", None), ("GET", "/api/session/a1", None),
                                       ("POST", "/api/session/a1/send", {"text": "x"}),
                                       ("POST", "/api/new", {"text": "x"})):
                st, obj, _ = w.req(method, path, body, auth=init)
                self.assertEqual((st, obj), (401, {"error": "auth"}), (init, path))
        st, obj, _ = w.req("GET", "/api/sessions", auth=False)
        self.assertEqual((st, obj), (401, {"error": "auth"}))
        self.assertEqual(json.dumps(e.bot.store.data, sort_keys=True), before)
        self.assertEqual(len(e.runs), runs)
        self.assertEqual(e.sent, [])
        st, obj, _ = w.req("GET", "/api/sessions")
        self.assertEqual(st, 200)

    def test_dev_bypass_needs_env_local_address_and_header(self):
        w = WebEnv(self)
        e = w.e
        st, _, _ = w.req("GET", "/api/sessions", headers={"X-Dev": "1"}, auth=False)
        self.assertEqual(st, 401)  # без CC_WEBAPP_DEV
        e.cfg.webapp_dev = True
        st, _, _ = w.req("GET", "/api/sessions", auth=False)
        self.assertEqual(st, 401)  # без заголовка X-Dev
        st, _, _ = w.req("GET", "/api/sessions", headers={"X-Dev": "0"}, auth=False)
        self.assertEqual(st, 401)
        st, _, _ = w.req("GET", "/api/sessions", headers={"X-Dev": "1", "Cf-Connecting-Ip": "1.2.3.4"}, auth=False)
        self.assertEqual(st, 401)  # запрос пришёл через туннель
        st, obj, _ = w.req("GET", "/api/sessions", headers={"X-Dev": "1"}, auth=False)
        self.assertEqual(st, 200)
        self.assertIn("sessions", obj)

    def test_served_page_versions_its_own_assets(self):
        w = WebEnv(self)
        with open(os.path.join(w.static, "index.html"), "w") as f:
            f.write('<link rel="stylesheet" href="/app/app.js"><script src="/app/nope.js"></script>'
                    '<script src="https://telegram.org/js/telegram-web-app.js"></script>')
        os.utime(os.path.join(w.static, "app.js"), (1700000000, 1700000000))
        body = w.req("GET", "/app/", auth=False)[1].decode()
        self.assertIn('href="/app/app.js?v=1700000000"', body)  # a cached old copy is never paired with the new page
        self.assertIn('src="/app/nope.js"', body)  # a missing file stays as it is
        self.assertIn('src="https://telegram.org/js/telegram-web-app.js"', body)
        self.assertEqual(w.req("GET", "/app/app.js?v=1700000000", auth=False)[0], 200)

    def test_static_without_auth_and_traversal_blocked(self):
        w = WebEnv(self)
        st, body, r = w.req("GET", "/app/", auth=False)
        self.assertEqual(st, 200)
        self.assertIn("Список".encode(), body)
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        self.assertTrue(r.getheader("Content-Type").startswith("text/html"))
        st, body, r = w.req("GET", "/app/app.js", auth=False)
        self.assertEqual(st, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("application/javascript"))
        self.assertEqual(w.req("GET", "/app", auth=False)[0], 200)
        for bad in ("/app/../secret.txt", "/app/%2e%2e/secret.txt", "/app/..%2fsecret.txt",
                    "/app//etc/passwd", "/app/nope.js", "/app/sub/../../secret.txt"):
            self.assertEqual(w.req("GET", bad, auth=False)[0], 404, bad)
        self.assertEqual(w.req("GET", "/", auth=False)[0], 404)
        self.assertEqual(w.req("POST", "/app/", {"x": 1}, auth=False)[0], 404)
        st, body, r = w.req("HEAD", "/app/index.html", auth=False)  # the mini-app's update check
        self.assertEqual((st, body), (200, b""))
        self.assertTrue(int(r.getheader("Content-Length")) > 0)
        self.assertEqual(w.req("HEAD", "/app/nope.js", auth=False)[:2], (404, b""))
        self.assertEqual(w.req("HEAD", "/api/sessions", auth=False)[0], 405)

    def test_body_limits_and_bad_json(self):
        w = WebEnv(self)
        mk(w.e, "a1", "working", 1)
        w.e.bot.list_sessions(True)
        st, obj, _ = w.req("POST", "/api/session/a1/send", raw_body=b"x" * 70000,
                           headers={"Content-Type": "application/json"})
        self.assertEqual((st, obj), (413, {"error": "too large"}))
        st, obj, _ = w.req("POST", "/api/session/a1/send", raw_body=b"{oops",
                           headers={"Content-Type": "application/json"})
        self.assertEqual((st, obj), (400, {"error": "bad json"}))
        st, obj, _ = w.req("POST", "/api/session/a1/send", raw_body=b"[1]")
        self.assertEqual(st, 400)


class CodexBoxTests(unittest.TestCase):
    def test_box_sees_only_the_login_of_codex_and_no_bot_environment(self):
        with tempfile.TemporaryDirectory() as home:
            os.makedirs(os.path.join(home, ".codex"))
            auth = os.path.join(home, ".codex", "auth.json")
            with open(auth, "w") as f:
                f.write("{}")
            with mock.patch.dict(os.environ, {"HOME": home}), mock.patch("shutil.which", side_effect=lambda x: "/usr/bin/" + x):
                cmd = cc.codex_boxed("/tmp", "вопрос", "m1")
                os.remove(auth)
                with self.assertRaises(OSError):  # not signed in: the fallback fails closed
                    cc.codex_boxed("/tmp", "вопрос")
        i = cmd.index("--tmpfs", cmd.index("--tmpfs", cmd.index("--tmpfs") + 1) + 1)
        self.assertEqual(cmd[i + 1:i + 5], [os.path.join(home, ".codex"), "--bind", auth, auth])
        self.assertIn("--clearenv", cmd)
        self.assertIn("/run/systemd/resolve", cmd)
        self.assertEqual(cmd[cmd.index("-m") + 1], "m1")


class ModelSheetTests(unittest.TestCase):
    def setUp(self):
        self.w = WebEnv(self)
        self.e = self.w.e
        cfg = self.e.bot.cfg
        cfg.sdk_env = os.path.join(self.e.tmp.name, "conf", "sdk.env")
        cfg.env_path = os.path.join(self.e.tmp.name, "conf", "env")
        os.makedirs(os.path.dirname(cfg.sdk_env))
        with open(cfg.sdk_env, "w") as f:
            f.write("# keep me\nCC_SDK_BUDGET=2\n")
        with open(cfg.env_path, "w") as f:
            f.write("TG_BOT_TOKEN=1:x\n")
        self.runs = []
        self.rc = 0
        self.e.bot.run = lambda cmd, **kw: (self.runs.append(cmd) or (self.rc, ""))

    def save(self, verdict="ok", **body):
        with mock.patch.object(cc, "model_check", return_value=verdict) as chk:
            self.chk = chk
            return self.e.bot.api_dispatch(1, "POST", "/api/model", body)

    def test_key_is_saved_after_the_provider_accepts_it_and_never_returned(self):
        st, obj = self.save(provider="deepseek", key="sk-secret-123")
        self.assertEqual((st, obj), (200, {"ok": True, "restarted": True}))
        env = cc.env_read(self.e.bot.cfg.sdk_env)
        self.assertEqual(env["CC_SDK_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual((env["CC_SDK_MODEL"], env["CC_SDK_KEY_VAR"], env["CC_SDK_BUDGET"]), ("deepseek-flash", "DEEPSEEK_API_KEY", "2"))
        keys = self.e.bot.model_keys_path()
        self.assertEqual(os.stat(keys).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.e.bot.cfg.sdk_env).st_mode & 0o777, 0o600)
        self.assertIn("# keep me", open(self.e.bot.cfg.sdk_env).read())
        self.assertNotIn("sk-secret-123", open(self.e.bot.cfg.sdk_env).read())
        self.assertIn(["systemctl", "--user", "restart", "zidos-pager-sdk"], self.runs)
        _, got = self.e.bot.api_dispatch(1, "GET", "/api/model", None)
        self.assertNotIn("sk-secret-123", json.dumps(got))
        self.assertEqual(got["provider"], "deepseek")
        self.assertTrue(next(p for p in got["presets"] if p["id"] == "deepseek")["stored"])

    def test_a_refused_key_changes_nothing(self):
        for verdict in ("key", "balance", "model", "net"):
            st, obj = self.save(verdict, provider="glm", key="sk-secret-123")
            self.assertEqual(obj, {"ok": False, "error": verdict})
        self.assertFalse(os.path.exists(self.e.bot.model_keys_path()))
        self.assertNotIn("CC_SDK_MODEL", cc.env_read(self.e.bot.cfg.sdk_env))
        self.assertEqual(self.runs, [])

    def test_bad_input_never_reaches_the_provider(self):
        for body in ({"provider": "custom", "base": "http://x.example", "key": "sk-secret-123", "model": "m"},
                     {"provider": "custom", "base": "https://x.example/\nA=1", "key": "sk-secret-123", "model": "m"},
                     {"provider": "glm", "key": "bad key\nCC_SDK_BASE_URL=x"},
                     {"provider": "glm", "key": "sk-secret-123", "model": "a b\nC=1"},
                     {"provider": "glm"}):
            st, obj = self.save(**body)
            self.assertEqual(st, 200)
            self.assertFalse(obj["ok"], body)
        self.chk.assert_not_called()
        self.assertEqual(self.save(provider="nope")[0], 400)

    def test_a_stored_key_is_reused(self):
        self.save(provider="glm", key="sk-secret-123")
        self.save(provider="glm", model="glm-5.2")
        self.assertEqual(self.chk.call_args[0], ("https://api.z.ai/api/anthropic", "sk-secret-123", "glm-5.2"))

    def test_codex_needs_its_login_and_switches_back(self):
        self.rc = 1
        self.assertEqual(self.save(provider="codex")[1], {"ok": False, "error": "codex"})
        self.rc = 0
        self.assertTrue(self.save(provider="codex")[1]["ok"])
        self.assertEqual(self.e.bot.cfg.responder_engine, "codex")
        self.assertEqual(self.e.bot.api_dispatch(1, "GET", "/api/model", None)[1]["group"]["engine"], "codex")
        self.assertEqual(cc.env_read(self.e.bot.cfg.env_path)["CC_RESPONDER_ENGINE"], "codex")
        self.save(provider="glm", key="sk-secret-123")  # my sessions' model leaves the group bot alone
        self.assertEqual(self.e.bot.cfg.responder_engine, "codex")
        self.assertEqual(self.save(target="group", engine="sdk")[1]["ok"], True)
        self.assertEqual(self.e.bot.cfg.responder_engine, "sdk")
        self.assertNotIn("CC_RESPONDER_ENGINE", cc.env_read(self.e.bot.cfg.env_path))
        self.assertEqual(cc.env_read(self.e.bot.cfg.env_path)["TG_BOT_TOKEN"], "1:x")
        got = self.e.bot.api_dispatch(1, "GET", "/api/model", None)[1]
        self.assertEqual(got["provider"], "glm")
        self.assertNotIn("codex", [p["id"] for p in got["presets"]])  # Codex is a group engine, not a sessions model

    def test_claude_code_login_needs_its_sign_in_and_stores_no_key(self):
        out = {"v": '{"loggedIn": false}'}
        self.e.bot.run = lambda cmd, **kw: (self.runs.append(cmd) or (0, out["v"] if "auth" in cmd else ""))
        self.assertEqual(self.save(provider="claude_login", model="opus")[1], {"ok": False, "error": "claude_login"})
        self.assertNotIn("CC_SDK_AUTH", cc.env_read(self.e.bot.cfg.sdk_env))
        out["v"] = '{"loggedIn": true, "authMethod": "claude.ai"}'
        self.save(provider="glm", key="sk-secret-123")
        self.assertEqual(self.save(provider="claude_login", model="opus")[1], {"ok": True, "restarted": True})
        env = cc.env_read(self.e.bot.cfg.sdk_env)
        self.assertEqual((env["CC_SDK_AUTH"], env["CC_SDK_MODEL"]), ("login", "opus"))
        self.assertNotIn("CC_SDK_BASE_URL", env)  # GLM's address is gone, or the CLI would send the sign-in there
        self.assertEqual(self.runs[-1], ["systemctl", "--user", "restart", "zidos-pager-sdk"])
        self.chk.assert_not_called()  # no key check: the CLI signs in by itself
        got = self.e.bot.api_dispatch(1, "GET", "/api/model", None)[1]
        self.assertEqual((got["provider"], got["model"]), ("claude_login", "opus"))
        self.assertEqual(next(p for p in got["presets"] if p["id"] == "claude_login")["models"], ["opus", "sonnet", "haiku"])
        self.save(provider="glm", key="sk-secret-123")
        self.assertNotIn("CC_SDK_AUTH", cc.env_read(self.e.bot.cfg.sdk_env))  # a key provider switches login off again

    def test_codex_model_is_chosen_or_left_default(self):
        self.assertTrue(self.save(provider="codex", model="gpt-6.1-sol")[1]["ok"])
        self.assertEqual(cc.env_read(self.e.bot.cfg.env_path)["CC_CODEX_MODEL"], "gpt-6.1-sol")
        self.assertEqual(self.e.bot.cfg.codex_model, "gpt-6.1-sol")
        self.assertEqual(self.e.bot.api_dispatch(1, "GET", "/api/model", None)[1]["group"]["codex_model"], "gpt-6.1-sol")
        self.assertTrue(self.save(target="group", engine="codex", model="gpt-6.1-sol")[1]["ok"])
        self.assertEqual(self.save(provider="codex", model="a b\nC=1")[1], {"ok": False, "error": "model"})
        self.assertTrue(self.save(provider="codex")[1]["ok"])
        self.assertNotIn("CC_CODEX_MODEL", cc.env_read(self.e.bot.cfg.env_path))

    def test_failed_restart_is_reported(self):
        self.rc = 1
        self.assertEqual(self.save(provider="glm", key="sk-secret-123")[1], {"ok": True, "restarted": False})


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.w = WebEnv(self)
        self.e = self.w.e

    def test_sessions_from_cache_without_running_claude(self):
        e = self.e
        tr = os.path.join(e.tmp.name, "ask.jsonl")
        with open(tr, "wb") as f:
            f.write(ask_line("toolu_1", [Q_SIZE]))
        mk(e, "w1", "blocked", 5, detail="You've hit your session limit · resets 6:30am (Europe/Belgrade)")
        mk(e, "ask", "working", 3, linkScanPath=tr)
        mk(e, "wk", "working", 2)
        mk(e, "dn", "done", 10)
        mk(e, "fl", "failed", 11, detail="упала")
        mk(e, "oldp", "done", 400, name="Родитель")
        mk(e, "kid", "working", 1, name="Ребёнок")
        e.bot.store.data["chain_links"] = {"kid": "oldp"}
        e.bot.store.chat(1)["topics"]["wk"] = {"thread": 5, "title": "x", "last_edit": 0, "activity": 0}
        e.bot.store.data["watched"].append("w1")
        e.bot.list_sessions(True)
        n_runs = len(e.runs)
        st, obj, _ = self.w.req("GET", "/api/sessions")
        self.assertEqual(st, 200)
        self.assertEqual(len(e.runs), n_runs)  # claude agents на запросе не вызывался
        rows = {r["id"]: r for r in obj["sessions"]}
        self.assertEqual({k: v["group"] for k, v in rows.items()},
                         {"w1": "wait", "ask": "wait", "wk": "work", "dn": "done", "fl": "done",
                          "oldp": "old", "kid": "work"})
        self.assertEqual(rows["w1"]["detail"], "лимит сессии · сброс в 6:30")
        self.assertTrue(rows["w1"]["watched"])
        self.assertEqual(rows["ask"]["detail"], "вопрос: Размер?")
        self.assertEqual(rows["ask"]["ask"], {"question": "Размер?", "options": ["S", "M", "L"]})
        wq = rows["ask"]["wait_question"]
        self.assertEqual((wq["question"], wq["multi"], wq["n"], wq["step"]), ("Размер?", False, 1, 0))
        self.assertEqual([o["label"] for o in wq["options"]], ["S", "M", "L"])
        self.assertIn("description", wq["options"][0])
        self.assertIsNone(rows["w1"]["wait_question"])
        self.assertIsNone(rows["w1"]["perm_body"])
        self.assertTrue(rows["fl"]["failed"] and rows["fl"]["group"] == "done")
        self.assertTrue(rows["wk"]["topic"])
        self.assertEqual(rows["kid"]["k"], 2)
        self.assertTrue(rows["kid"]["current"])
        self.assertTrue(rows["kid"]["name"].endswith("№2"))
        self.assertEqual(rows["kid"]["base_name"], "Ребёнок")
        self.assertEqual(rows["oldp"]["into"], {"id": "kid", "name": rows["kid"]["name"]})
        self.assertIsNone(rows["dn"]["into"])
        self.assertEqual(rows["dn"]["ago"], "10 мин")
        self.assertIsInstance(rows["dn"]["updated"], float)
        self.assertEqual(obj["topics"], False)
        q = obj["quota"]
        self.assertEqual((q["h5"], q["d7"], q["stale"]), (59.0, 43.0, False))
        self.assertRegex(q["h5_reset"], r"^\d\d:\d\d$")

    def test_quota_nulls_when_no_file(self):
        self.e.cfg.rate_file = os.path.join(self.e.tmp.name, "nope.json")
        st, obj, _ = self.w.req("GET", "/api/sessions")
        self.assertEqual(obj["quota"], {"h5": None, "d7": None, "stale": False, "updated": None, "h5_reset": None})

    def test_session_detail_history_question_prev_next(self):
        e = self.e
        tr = os.path.join(e.tmp.name, "d.jsonl")
        with open(tr, "wb") as f:
            for i in range(8):
                f.write(asst("u%d" % i, "ответ **%d** <b>" % i))
            f.write(ask_line("toolu_9", [Q_SIZE, Q_TOP], uuid="askx"))
        mk(e, "par", "done", 300, name="Предок")
        mk(e, "cur", "working", 2, name="Текущая", linkScanPath=tr)
        e.job("cur", name="Текущая", state="working", cwd=os.path.expanduser("~/code"), linkScanPath=tr,
              updatedAt=iso(BASE - 60), detail="d")
        e.bot.store.data["chain_links"] = {"cur": "par"}
        e.bot.list_sessions(True)
        sent_before = len(e.tg.of("sendMessage"))
        n_runs = len(e.runs)
        st, d, _ = self.w.req("GET", "/api/session/cur")
        self.assertEqual(st, 200)
        self.assertEqual(len(e.runs), n_runs)
        self.assertEqual(d["cwd"], "~/code")
        self.assertEqual(d["prev"]["id"], "par")
        self.assertIsNone(d["next"])
        self.assertEqual(self.w.req("GET", "/api/session/par")[1]["next"]["id"], "cur")
        roles = [x["role"] for x in d["history"]]
        self.assertEqual(roles, ["assistant"] * 9)  # 8 ответов + «Сейчас уточню»
        self.assertEqual(d["history"][-1]["uuid"], "askx")
        self.assertEqual(d["history"][0]["html"], "ответ <b>0</b> &lt;b&gt;")
        qn = d["question"]
        self.assertEqual((qn["tool_id"], qn["step"], qn["n"], qn["multi"], qn["review"]),
                         ("toolu_9", 0, 2, False, False))
        self.assertEqual([o["label"] for o in qn["options"]], ["S", "M", "L"])
        self.assertEqual(qn["options"][0]["description"], "маленький")
        self.assertFalse(any(o["marked"] for o in qn["options"]))
        self.assertEqual(len(qn["answers"]), 2)
        # вопрос зарегистрирован, но сообщение в чат не отправлено
        self.assertEqual(len(e.tg.of("sendMessage")), sent_before)
        self.assertEqual(len(e.bot.store.data["questions"]), 1)
        self.assertIsNone(e.bot.pending_question("cur")["mid"])
        # повторный запрос берёт историю из кэша (size, mtime)
        with mock.patch.object(cc, "parse_history", side_effect=AssertionError("кэш не сработал")):
            self.assertEqual(self.w.req("GET", "/api/session/cur")[0], 200)
        self.assertEqual(self.w.req("GET", "/api/session/nope")[0], 404)

    def test_send_goes_through_deliver(self):
        e = self.e
        mk(e, "a1", "working", 1)
        e.bot.list_sessions(True)
        calls = []
        orig = e.bot.deliver
        e.bot.deliver = lambda chat, sid, text, origin="chat": (
            calls.append((chat, sid, text, origin)) or orig(chat, sid, text, origin))
        st, obj, _ = self.w.req("POST", "/api/session/a1/send", {"text": "  привет  "})
        self.assertEqual((st, obj), (200, {"ok": True, "error": None, "code": None}))
        self.assertEqual(calls, [(1, "a1", "привет", "webapp")])
        self.assertEqual(e.sent, [("a1", "привет")])
        self.assertFalse(e.bot.store.chat(1)["fw"].get("a1", {}).get("turn"))  # ответ владелец видит в мини-аппе
        st, obj, _ = self.w.req("POST", "/api/session/a1/send", {"text": "  "})
        self.assertEqual(obj["code"], "empty")
        e.inject_result = (False, cc.ERR_MENU)
        e.bot.injector = e.inject
        st, obj, _ = self.w.req("POST", "/api/session/a1/send", {"text": "x"})
        self.assertEqual((obj["ok"], obj["code"]), (False, "menu"))
        e.inject_result = (False, "Сессия не отвечает")
        st, obj, _ = self.w.req("POST", "/api/session/a1/send", {"text": "x"})
        self.assertEqual((obj["ok"], obj["code"], obj["error"]), (False, "error", "Сессия не отвечает"))

    def test_send_does_not_wait_for_the_global_lock(self):
        e = self.e
        mk(e, "a1", "working", 1)
        e.bot.list_sessions(True)
        res = []
        with e.bot.api_lock:  # another mini-app POST is busy: the paste into a session must not queue behind it
            t = threading.Thread(target=lambda: res.append(self.w.req("POST", "/api/session/a1/send", {"text": "x"})[1]))
            t.start()
            t.join(5)
            self.assertFalse(t.is_alive())
        self.assertEqual(res, [{"ok": True, "error": None, "code": None}])

    def question_setup(self, questions, screen=MENU_S):
        e = self.e
        tr = os.path.join(e.tmp.name, "q.jsonl")
        with open(tr, "wb") as f:
            f.write(ask_line("toolu_1", questions))
        mk(e, "s1", "working", 1, linkScanPath=tr)
        e.bot.list_sessions(True)
        self.fs = FakeScreen(screen)
        e.bot.tmux = self.fs.tmux()
        e.bot.make_current(1, "s1")
        return tr

    def test_new_question_is_shown_while_an_old_answer_still_waits_for_confirmation(self):
        e = self.e
        tr = self.question_setup([Q_SIZE])
        self.assertIsNotNone(self.w.req("GET", "/api/session/s1")[1]["question"])
        q = e.bot.pending_question("s1")
        q["waiting"] = True  # answered from the mini-app; the tick that closes it never ran for this session
        with open(tr, "ab") as f:
            f.write(answer_line("toolu_1", "ok") + ask_line("toolu_2", [Q_SIZE], uuid="ask2"))
        obj = self.w.req("GET", "/api/session/s1")[1]
        self.assertIsNotNone(obj["question"])  # the new ask, not «nothing»
        self.assertEqual(obj["question"]["tool_id"], "toolu_2")
        self.assertTrue(q["closed"])

    def test_answer_option_presses_digit_and_closes(self):
        e = self.e
        self.question_setup([Q_SIZE])
        e.bot.forward_tick()  # вопрос ушёл и в чат (текущая сессия)
        chat_q = [m for m in e.tg.of("sendMessage") if "❓" in m["text"]]
        self.assertEqual(len(chat_q), 1)
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "option", "index": 2})
        self.assertEqual((st, obj["ok"], obj["question"]), (200, True, None))
        self.assertEqual(self.fs.flat_keys, ["2"])
        # сообщение-вопрос в чате обновлено теми же механизмами
        self.assertIn("Ответ отправлен", e.tg.of("editMessageText")[-1]["text"])
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "option", "index": 1})
        self.assertFalse(obj["ok"])

    def test_answer_option_with_preview_presses_digit_then_enter(self):
        # with previews the TUI lays options out side by side: a digit only moves the cursor, Enter selects
        e = self.e
        q_prev = dict(Q_SIZE, options=[dict(o, preview="[%s]" % o["label"]) for o in Q_SIZE["options"]])
        self.question_setup([q_prev])
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT) if ks == ["Enter"] else None
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "option", "index": 1})
        self.assertEqual((st, obj["ok"], obj["question"]), (200, True, None))
        self.assertEqual(self.fs.flat_keys, ["1", "Enter"])

    def test_own_answer_with_preview_goes_through_chat_about_this(self):
        # with previews there is no "Type something" row and a digit is ignored: «Chat about this» ends the question
        # and frees the prompt, the text then goes as a message (on the real TUI a pasted text was dropped)
        q_prev = dict(Q_SIZE, options=[dict(o, preview="[%s]" % o["label"]) for o in Q_SIZE["options"]])
        self.question_setup([q_prev])
        text = "ровно 42"
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT) if ks == ["Enter"] and "Enter to select" in f.screen else None
        self.fs.on_paste = lambda f: setattr(f, "screen", EMPTY_PROMPT.replace("❯\xa0", "❯\xa0" + text))
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "own", "text": text})
        self.assertTrue(obj["ok"], obj)
        self.assertEqual(self.fs.flat_keys, ["Down"] * 4 + ["Enter", "Enter"])
        self.assertIsNone(obj["question"])

    def test_answer_multi_question_sequence(self):
        e = self.e
        self.question_setup([Q_SIZE, Q_TOP])
        self.fs.on_key = lambda ks, f: setattr(f, "screen", MENU_TABS) if ks == ["1"] else None
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "option", "index": 1})
        self.assertTrue(obj["ok"])
        self.assertEqual((obj["question"]["step"], obj["question"]["multi"]), (1, True))
        self.assertFalse(obj["question"]["options"][0]["marked"])
        self.fs.on_key = lambda ks, f: setattr(f, "screen", MENU_TABS_CHECKED) if ks == ["1"] else None
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "option", "index": 1})
        self.assertTrue(obj["question"]["options"][0]["marked"])
        self.fs.on_key = lambda ks, f: setattr(f, "screen", REVIEW) if ks == ["Right"] else None
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "next"})
        self.assertTrue(obj["question"]["review"])
        self.assertEqual(obj["question"]["answers"][0]["answer"], "S")
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "submit"})
        self.assertEqual(self.fs.flat_keys, ["1", "1", "Right", "1"])
        self.assertIsNone(obj["question"])

    def test_answer_cancel_and_own(self):
        e = self.e
        self.question_setup([Q_SIZE])
        self.fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT) if ks == ["Escape"] else None
        st, obj, _ = self.w.req("POST", "/api/session/s1/answer", {"action": "cancel"})
        self.assertEqual(self.fs.flat_keys, ["Escape"])
        # «свой ответ»
        e2 = WebEnv(self)
        e = e2.e
        tr = os.path.join(e.tmp.name, "q.jsonl")
        with open(tr, "wb") as f:
            f.write(ask_line("toolu_1", [Q_SIZE]))
        mk(e, "s1", "working", 1, linkScanPath=tr)
        e.bot.list_sessions(True)
        fs = FakeScreen(MENU_S)
        e.bot.tmux = fs.tmux()
        text = "ровно 42"
        fs.on_paste = lambda f: setattr(f, "screen", MENU_S.replace("4. Type something", "❯ 4. " + text))
        st, obj, _ = e2.req("POST", "/api/session/s1/answer", {"action": "own", "text": text})
        self.assertEqual(obj["ok"], True)
        self.assertEqual(fs.flat_keys, ["4", "Enter"])
        self.assertNotIn("C-u", fs.flat_keys)
        st, obj, _ = e2.req("POST", "/api/session/s1/answer", {"action": "bogus"})
        self.assertFalse(obj["ok"])

    def test_watch_rename_stop(self):
        e = self.e
        mk(e, "a1", "done", 1)
        e.bot.list_sessions(True)
        st, obj, _ = self.w.req("POST", "/api/session/a1/watch", {"on": True})
        self.assertEqual(obj, {"ok": True, "watched": True})
        self.assertTrue(e.bot.is_watched("a1"))
        st, obj, _ = self.w.req("POST", "/api/session/a1/watch", {"on": False})
        self.assertFalse(e.bot.is_watched("a1"))
        st, obj, _ = self.w.req("POST", "/api/session/a1/rename", {"name": "Новое имя"})
        self.assertTrue(obj["ok"]); self.assertIn(obj["status"], ("applied", "failed", "alias"))  # остановленная: без attach
        self.assertTrue(e.bot.store.data["names"]["a1"]["manual"])
        self.assertEqual(e.bot.display_name("a1"), "Новое имя")
        st, obj, _ = self.w.req("POST", "/api/session/a1/rename", {"name": "  "})
        self.assertFalse(obj["ok"])
        st, obj, _ = self.w.req("POST", "/api/session/a1/stop")
        self.assertTrue(obj["ok"])
        self.assertTrue([r for r in e.runs if r[0][-2:] == ["stop", "a1"]])
        self.assertEqual(self.w.req("GET", "/api/session/a1/stop")[0], 405)

    def test_dirs_and_new(self):
        e = self.e
        mk(e, "a1", "done", 1)
        e.bot.list_sessions(True)
        n_runs = len(e.runs)
        st, obj, _ = self.w.req("GET", "/api/dirs")
        self.assertEqual(len(e.runs), n_runs)
        self.assertEqual(obj["dirs"][0], cc.tilde(e.cfg.default_cwd))
        self.assertIn("/tmp", obj["dirs"])
        st, obj, _ = self.w.req("POST", "/api/new", {"text": "Сделай тесты", "cwd": "/tmp"})
        self.assertEqual((st, obj), (200, {"ok": True, "id": "abcd1234"}))
        cmd, cwd = [r for r in e.runs if "--bg" in r[0]][-1]
        self.assertEqual((cmd[-1], cwd), ("Сделай тесты", "/tmp"))
        self.assertTrue(e.bot.is_watched("abcd1234"))
        st, obj, _ = self.w.req("POST", "/api/new", {"text": "x", "cwd": "/нет/такой"})
        self.assertEqual(st, 400)
        st, obj, _ = self.w.req("POST", "/api/new", {"text": " "})
        self.assertEqual(st, 400)
        st, obj, _ = self.w.req("POST", "/api/new", {"text": "дома"})  # папка по умолчанию
        self.assertEqual([r for r in e.runs if "--bg" in r[0]][-1][1], e.cfg.default_cwd)


class TunnelTests(unittest.TestCase):
    def test_new_url_sets_menu_button_and_panel_button(self):
        e = names_env(self)
        e.cfg.tunnel_file = os.path.join(e.tmp.name, "tunnel-url")
        mk(e, "a1", "working", 1)
        e.bot.panel_publish(1)
        self.assertNotIn("web_app", str(e.bot.panel_render(1)[2]))  # URL нет — всё как раньше
        e.bot.webapp_tick()
        self.assertEqual(e.tg.of("setChatMenuButton"),
                         [{"chat_id": 1, "menu_button": {"type": "commands"}}])
        self.assertTrue(e.bot.webapp_menu_initialized)
        e.bot.webapp_tick()
        self.assertEqual(len(e.tg.of("setChatMenuButton")), 1)
        with open(e.cfg.tunnel_file, "w") as f:
            f.write("https://abc-def.trycloudflare.com\n")
        e.bot.webapp_tick()
        mb = e.tg.of("setChatMenuButton")
        self.assertEqual(len(mb), 2)
        self.assertEqual(mb[-1], {"chat_id": 1, "menu_button": {
            "type": "web_app", "text": "Сессии", "web_app": {"url": "https://abc-def.trycloudflare.com/app/"}}})
        first_row = e.tg.of("editMessageText")[-1]["reply_markup"]["inline_keyboard"][0]
        self.assertEqual(first_row, [{"text": "📋 Открыть список",
                                      "web_app": {"url": "https://abc-def.trycloudflare.com/app/"}}])
        e.bot.webapp_tick()  # тот же адрес — ничего не делаем
        self.assertEqual(len(e.tg.of("setChatMenuButton")), 2)
        with open(e.cfg.tunnel_file, "w") as f:
            f.write("https://new-one.trycloudflare.com\n")
        e.bot.webapp_tick()
        self.assertEqual(len(e.tg.of("setChatMenuButton")), 3)
        self.assertIn("new-one", str(e.tg.of("editMessageText")[-1]["reply_markup"]))
        # адрес исчез — меню возвращается к командам, кнопка из пульта убирается
        os.remove(e.cfg.tunnel_file)
        e.bot.webapp_tick()
        self.assertEqual(e.tg.of("setChatMenuButton")[-1],
                         {"chat_id": 1, "menu_button": {"type": "commands"}})
        self.assertNotIn("web_app", str(e.tg.of("editMessageText")[-1]["reply_markup"]))
        e.bot.webapp_tick()
        self.assertEqual(len(e.tg.of("setChatMenuButton")), 4)

    def test_initial_menu_button_reset_error_is_retried(self):
        e = names_env(self)
        e.cfg.webapp_url_env = ""
        e.cfg.tunnel_file = os.path.join(e.tmp.name, "missing-url")
        e.tg.errors["setChatMenuButton"] = (lambda p: True, "Bad Request: boom")
        e.bot.webapp_tick()
        self.assertIsNone(e.bot.webapp_url_cur)
        self.assertFalse(e.bot.webapp_menu_initialized)
        del e.tg.errors["setChatMenuButton"]
        e.bot.webapp_tick()
        self.assertIsNone(e.bot.webapp_url_cur)
        self.assertTrue(e.bot.webapp_menu_initialized)
        self.assertEqual(e.tg.of("setChatMenuButton"),
                         [{"chat_id": 1, "menu_button": {"type": "commands"}}] * 2)
        e.bot.webapp_tick()
        self.assertEqual(len(e.tg.of("setChatMenuButton")), 2)

    def test_env_url_has_priority_and_garbage_is_ignored(self):
        e = names_env(self)
        e.cfg.tunnel_file = os.path.join(e.tmp.name, "tunnel-url")
        with open(e.cfg.tunnel_file, "w") as f:
            f.write("http://not-https\n")
        self.assertIsNone(e.bot.webapp_url())
        with open(e.cfg.tunnel_file, "w") as f:
            f.write("https://file.trycloudflare.com\n")
        self.assertEqual(e.bot.webapp_url(), "https://file.trycloudflare.com")
        e.cfg.webapp_url_env = "https://env.example.org"
        self.assertEqual(e.bot.webapp_url(), "https://env.example.org")

    def test_menu_button_error_is_retried(self):
        e = names_env(self)
        e.cfg.webapp_url_env = "https://x.example.org"
        e.tg.errors["setChatMenuButton"] = (lambda p: True, "Bad Request: boom")
        e.bot.webapp_tick()
        self.assertIsNone(e.bot.webapp_url_cur)
        del e.tg.errors["setChatMenuButton"]
        e.bot.webapp_tick()
        self.assertEqual(e.bot.webapp_url_cur, "https://x.example.org")

    def test_menu_button_removal_error_is_retried(self):
        e = names_env(self)
        e.cfg.webapp_url_env = "https://x.example.org"
        e.bot.webapp_tick()
        e.cfg.webapp_url_env = ""
        e.cfg.tunnel_file = os.path.join(e.tmp.name, "missing-url")
        e.tg.errors["setChatMenuButton"] = (lambda p: True, "Bad Request: boom")
        e.bot.webapp_tick()
        self.assertEqual(e.bot.webapp_url_cur, "https://x.example.org")
        del e.tg.errors["setChatMenuButton"]
        e.bot.webapp_tick()
        self.assertIsNone(e.bot.webapp_url_cur)
        self.assertEqual(e.tg.of("setChatMenuButton")[-1],
                         {"chat_id": 1, "menu_button": {"type": "commands"}})
        self.assertEqual(len(e.tg.of("setChatMenuButton")), 3)

    def test_tunnel_script_writes_url_atomically_and_cleans_up(self):
        script = os.path.join(os.path.dirname(os.path.abspath(cc.__file__)), "deploy", "zidos-pager-tunnel.sh")
        with tempfile.TemporaryDirectory() as d:
            fake = os.path.join(d, "cloudflared")
            with open(fake, "w") as f:
                f.write('#!/bin/bash\necho "2026 INF Requesting new quick Tunnel"\n'
                        'echo "2026 INF |  https://abc-def-12.trycloudflare.com  |"\nexec sleep 30\n')
            os.chmod(fake, 0o755)
            out = os.path.join(d, "state", "tunnel-url")
            env = dict(os.environ, CLOUDFLARED_BIN=fake, CC_TUNNEL_FILE=out, HOME=d)
            pr = subprocess.Popen(["bash", script], env=env, stderr=subprocess.DEVNULL, start_new_session=True)
            try:
                for _ in range(100):
                    if os.path.exists(out):
                        break
                    time.sleep(0.05)
                with open(out) as f:
                    self.assertEqual(f.read().strip(), "https://abc-def-12.trycloudflare.com")
                self.assertFalse([x for x in os.listdir(os.path.dirname(out)) if ".tmp" in x])
            finally:
                os.killpg(pr.pid, signal.SIGTERM)
                pr.wait(10)
            # cloudflared завершился сам -> код возврата сохраняется, устаревший адрес удалён
            with open(fake, "w") as f:
                f.write('#!/bin/bash\necho "https://zzz.trycloudflare.com"\nexit 3\n')
            r = subprocess.run(["bash", script], env=env, stderr=subprocess.DEVNULL, timeout=20)
            self.assertEqual(r.returncode, 3)
            self.assertFalse(os.path.exists(out))

    def test_service_unit_files(self):
        base = os.path.dirname(os.path.abspath(cc.__file__))
        unit = open(os.path.join(base, "deploy", "zidos-pager-tunnel.service"), encoding="utf-8").read()
        for want in ("Restart=always", "RestartSec=10", "cloudflared tunnel --no-autoupdate", " run",
                     "Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin"):
            self.assertIn(want, unit)
        script = open(os.path.join(base, "deploy", "zidos-pager-tunnel.sh"), encoding="utf-8").read()
        self.assertIn("tunnel --no-autoupdate --url", script)
        self.assertIn("cloudflared", script)
        self.assertNotIn("/home/", script)
        self.assertTrue(os.access(os.path.join(base, "deploy", "zidos-pager-tunnel.sh"), os.X_OK))


# ---------------------------------------------------------------- история, слеш-команды, экран, клавиши

def user_line(content, origin="human", meta=False, uuid="u1", ts="2026-10-14T10:00:00Z", prompt_source="typed"):
    obj = {"type": "user", "uuid": uuid, "timestamp": ts, "message": {"role": "user", "content": content},
           "promptSource": prompt_source}
    if origin:
        obj["origin"] = {"kind": origin}
    else:
        obj["origin"] = None
    if meta:
        obj["isMeta"] = True
    return line(obj)


def asst_ts(uuid, text, ts):
    return line({"type": "assistant", "isSidechain": False, "uuid": uuid, "timestamp": ts,
                 "message": {"content": [{"type": "text", "text": text}]}})


class HistoryParseTests(unittest.TestCase):
    def test_roles_order_and_filtering(self):
        data = b"".join([
            user_line("Привет, сделай X", uuid="h1", ts="t1"),
            asst_ts("a1", "Делаю **X**", "t2"),
            user_line("<task-notification>done</task-notification>", origin="task-notification", uuid="tn"),
            line({"type": "user", "uuid": "tr", "message": {"content": [{"type": "tool_result", "content": "x"}]},
                  "origin": None}),
            user_line("Base directory for skill: …большой текст скилла", meta=True, uuid="meta"),
            user_line("<local-command-caveat>Caveat: ...</local-command-caveat>", origin=None, uuid="cav"),
            user_line("<command-name>/login</command-name> <command-message>login</command-message> "
                      "<command-args></command-args>", origin=None, uuid="c1", ts="t3"),
            user_line("<local-command-stdout>\x1b[1mLogin successful\x1b[0m <b>ok</b></local-command-stdout>",
                      origin=None, uuid="o1", ts="t4"),
            user_line("<command-name>/model</command-name><command-message>model</command-message>"
                      "<command-args>opus  high</command-args>", origin=None, uuid="c2"),
            user_line("<local-command-stderr>ошибка</local-command-stderr>", origin=None, uuid="o2"),
            user_line("[Request interrupted by user]", uuid="int", ts="t5"),
            user_line("<system-reminder>секрет</system-reminder>", uuid="sr"),
            line({"type": "user", "isSidechain": True, "uuid": "sc", "message": {"content": "из сабагента"},
                  "origin": {"kind": "human"}}),
            user_line("Очередной вопрос", uuid="h2", prompt_source="queued"),
        ])
        h = cc.parse_history(data)
        self.assertEqual([(x["role"], x["uuid"]) for x in h],
                         [("user", "h1"), ("assistant", "a1"), ("command", "c1"), ("output", "o1"),
                          ("command", "c2"), ("output", "o2"), ("note", "int"), ("user", "h2")])
        self.assertEqual(h[0]["text"], "Привет, сделай X")
        self.assertEqual(h[0]["ts"], "t1")
        self.assertEqual(h[1]["html"], "Делаю <b>X</b>")
        self.assertEqual(h[2]["text"], "/login")
        self.assertEqual(h[3]["text"], "Login successful ok")  # теги и ANSI убраны
        self.assertEqual(h[4]["text"], "/model opus high")
        self.assertEqual(h[5]["text"], "ошибка")
        self.assertEqual(h[6]["text"], "прервано")

    def test_relay_keeps_watching_earlier_messages(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        log = os.path.join(td.name, "responder.jsonl")
        with open(log, "w") as f:
            f.write(json.dumps({"who": "Анна", "from": 5}) + "\n")
        with open(os.path.join(td.name, "relay.json"), "w") as f:
            json.dump({"mids": [55, 62], "until": time.time() + 3600}, f)
        txt = os.path.join(td.name, "t.txt")
        with open(txt, "w") as f:
            f.write("Анна, вопрос")
        cfg = types.SimpleNamespace(responder_token="T", responder_chat=-1, responder_log=log, api_base="http://x")
        sent = []
        with mock.patch.object(cc.TG, "send_one", lambda self, chat, text, **kw: sent.append((text, kw)) or {"message_id": 72}):
            self.assertEqual(cc.relay_main(cfg, ["--to", "Анна", "--reply", "65", txt]), 0)
        with open(os.path.join(td.name, "relay.json")) as f:
            self.assertEqual(json.load(f)["mids"], [55, 62, 72])  # not 65: that is Anna's own message
        self.assertIn('tg://user?id=5', sent[0][0])
        self.assertEqual(sent[0][1]["reply_to"], 65)
        self.assertNotEqual(cc.relay_main(cfg, ["--to", "Кто-то", txt]), 0)  # an unknown addressee is never guessed

    def test_relay_html_mentions_the_addressee(self):
        self.assertEqual(cc.relay_html("Анна, привет! <b>", "Анна", 5),
                         '<a href="tg://user?id=5">Анна</a>, привет! &lt;b&gt;')
        self.assertEqual(cc.relay_html("Вопрос", "Анна", 5), '<a href="tg://user?id=5">Анна</a>\nВопрос')

    def test_thinking_summary_stands_in_for_a_reply_the_log_lost(self):
        data = (asst("a1", {"type": "thinking", "thinking": "Проверил **set_model**"}, {"type": "tool_use", "name": "Bash", "input": {}})
                + asst("a2", {"type": "thinking", "thinking": ""}, {"type": "tool_use", "name": "Bash", "input": {}})
                + asst("a3", {"type": "thinking", "thinking": "рассуждение"}, "Ответ"))
        h = cc.parse_history(data)
        self.assertEqual([(x["uuid"], bool(x.get("thinking"))) for x in h], [("a1", True), ("a3", False)])
        self.assertIn("<b>set_model</b>", h[0]["html"])
        self.assertNotIn("рассуждение", h[1]["html"])  # a real reply is shown as it is

    def test_output_limit_and_last_24(self):
        big = "<local-command-stdout>" + "я" * 5000 + "</local-command-stdout>"
        data = b"".join([user_line(big, origin=None, uuid="big")] +
                         [user_line("msg %d" % i, uuid="m%d" % i) for i in range(30)])
        h = cc.parse_history(data)
        self.assertEqual(len(h), 24)
        self.assertEqual(h[0]["uuid"], "m6")
        self.assertEqual(h[-1]["text"], "msg 29")
        self.assertEqual(len(cc.parse_history(user_line(big, origin=None))[0]["text"]), 2000)


class PastedContentHistoryTests(unittest.TestCase):
    BLOCK = ('<pasted_content id="8d03">\nФайлы от владельца (2):\n• /p/a.jpeg (98 КБ, image/jpeg)\n'
             '• /p/b.jpeg (93 КБ, image/jpeg)\nРазберись с ошибками\n</pasted_content id="8d03">')

    def test_wrapper_tags_removed(self):
        h = cc.parse_history(user_line(self.BLOCK))
        self.assertEqual(len(h), 1)
        self.assertTrue(h[0]["text"].startswith("Файлы от владельца (2):"))
        self.assertNotIn("pasted_content", h[0]["text"])
        self.assertTrue(h[0]["text"].endswith("Разберись с ошибками"))

    def test_text_around_and_several_blocks(self):
        raw = "До\n" + self.BLOCK + "\nМежду\n<pasted_content>\nвторой\n</pasted_content>\nПосле"
        t = cc.parse_history(user_line(raw))[0]["text"]
        self.assertNotIn("pasted_content", t)
        self.assertEqual([x for x in t.split("\n") if x][0], "До")
        for part in ("Между", "второй", "После", "Разберись с ошибками"):
            self.assertIn(part, t)


def live_env(tc, screen=EMPTY_PROMPT, live=True):
    w = WebEnv(tc)
    e = w.e
    mk(e, "s1", "working", 1)
    if live:
        e.agents[-1]["pid"] = 4242
    e.bot.list_sessions(True)
    fs = FakeScreen(screen)
    e.bot.tmux = fs.tmux()
    e.bot.injector = e.bot.tmux.send_text  # настоящий путь Tmux.send_text на подменённом tmux
    return w, e, fs


def type_into(fs, text, after_enter=None):
    """Подмена: вставка показывает текст в поле ввода, Enter открывает экран after_enter (или чистое поле)."""
    fs.on_paste = lambda f: setattr(f, "screen", "%s\n❯ %s\n%s\n" % (BORDER, text, BORDER))
    fs.on_key = lambda ks, f: setattr(f, "screen", after_enter or EMPTY_PROMPT) if ks == ["Enter"] else None


class WebCommandTests(unittest.TestCase):
    def test_history_in_session_detail_is_chronological_and_cached(self):
        w = WebEnv(self)
        e = w.e
        tr = os.path.join(e.tmp.name, "h.jsonl")
        with open(tr, "wb") as f:
            f.write(user_line("Первое", uuid="h1") + asst_ts("a1", "Ответ", "t2")
                    + user_line("<task-notification/>", origin="task-notification")
                    + user_line("<command-name>/usage</command-name><command-args></command-args>", origin=None,
                                uuid="c1")
                    + user_line("<local-command-stdout>Расход: 5%</local-command-stdout>", origin=None, uuid="o1"))
        mk(e, "s1", "done", 1, linkScanPath=tr)
        e.bot.list_sessions(True)
        st, d, _ = w.req("GET", "/api/session/s1")
        self.assertEqual([x["role"] for x in d["history"]], ["user", "assistant", "command", "output"])
        self.assertEqual(d["history"][3]["text"], "Расход: 5%")
        with mock.patch.object(cc, "parse_history", side_effect=AssertionError("кэш (size, mtime) не сработал")):
            self.assertEqual(w.req("GET", "/api/session/s1")[0], 200)
        with open(tr, "ab") as f:  # файл вырос — кэш сброшен
            f.write(user_line("Второе", uuid="h2"))
        st, d, _ = w.req("GET", "/api/session/s1")
        self.assertEqual(d["history"][-1]["text"], "Второе")

    def test_commands_list_requires_auth(self):
        w = WebEnv(self)
        st, obj, _ = w.req("GET", "/api/commands", auth=False)
        self.assertEqual((st, obj), (401, {"error": "auth"}))
        st, obj, _ = w.req("GET", "/api/commands")
        self.assertEqual(st, 200)
        by = {c["cmd"]: c for c in obj["commands"]}
        self.assertEqual(list(by), ["/compact", "/model", "/effort", "/context", "/usage", "/status", "/fast",
                                    "/advisor", "/rename", "/clear"])
        self.assertTrue(by["/compact"]["free_arg"])
        self.assertEqual(by["/model"]["args"], ["opus", "sonnet", "haiku", "fable"])
        self.assertEqual(by["/effort"]["args"], ["low", "medium", "high", "xhigh", "max"])
        self.assertTrue(by["/clear"]["danger"])
        self.assertIn("Прежний разговор останется на диске", by["/clear"]["confirm"])
        self.assertEqual(w.req("POST", "/api/commands", {})[0], 405)

    def test_command_validation(self):
        w, e, fs = live_env(self)
        for bad in ("model opus", "/model\nopus", "/" + "x" * 200, "", "   ", None):
            st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": bad})
            self.assertEqual((st, obj["ok"], obj["code"]), (400, False, "bad"), bad)
        self.assertEqual(fs.calls, [])  # ничего не отправлялось в tmux

    def test_command_sent_via_send_text_without_marking_a_turn(self):
        w, e, fs = live_env(self)
        type_into(fs, "/effort high")
        sends_before = len(e.tg.of("sendMessage"))
        st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": " /effort high "})
        self.assertEqual((st, obj), (200, {"ok": True, "menu": False}))
        self.assertEqual(fs.flat_keys, ["Enter"])
        self.assertIn("paste-buffer", [c[1] for c in fs.calls])
        self.assertNotIn("a1", e.bot.store.chat(1)["fw"])  # ход не отмечался
        self.assertEqual(len(e.tg.of("sendMessage")), sends_before)
        self.assertEqual(e.bot.status, {})

    def test_command_that_opens_menu_returns_screen(self):
        w, e, fs = live_env(self)
        type_into(fs, "/model", after_enter=MENU_S)
        st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": "/model"})
        self.assertEqual(st, 200)
        self.assertTrue(obj["ok"] and obj["menu"])
        self.assertIn("Enter to select", obj["screen"])
        self.assertIn("❯ 1. S", obj["screen"])

    def test_command_that_opens_settings_panel_returns_screen(self):
        # /usage, /status, /config, /context open a tabbed panel: neither a menu nor an input line
        w, e, fs = live_env(self)
        type_into(fs, "/usage", after_enter=PANEL_S)
        st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": "/usage"})
        self.assertTrue(obj["ok"] and obj["menu"])
        self.assertIn("85% used", obj["screen"])
        st, obj, _ = w.req("GET", "/api/session/s1/screen")
        self.assertTrue(obj["menu"])  # the sheet stays open until Esc closes the panel

    def test_send_while_settings_panel_is_open_does_not_hang(self):
        w, e, fs = live_env(self, screen=PANEL_S)
        t0 = time.monotonic()
        ok, err = e.bot.tmux.send_text("s1", "hello")
        self.assertEqual((ok, err), (False, cc.ERR_MENU))
        self.assertLess(time.monotonic() - t0, 5)
        self.assertNotIn("kill-session", [c[1] for c in fs.calls])

    def test_panel_active_detects_tab_bar_only(self):
        self.assertTrue(cc.panel_active(PANEL_S))
        self.assertFalse(cc.panel_active("❯ \n"))
        self.assertFalse(cc.panel_active("Settings are fine. Status ok."))

    def test_command_while_question_menu_is_active(self):
        w, e, fs = live_env(self, screen=MENU_S)
        st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": "/usage"})
        self.assertEqual((obj["ok"], obj["code"], obj["menu"]), (False, "menu", True))
        self.assertIn("Enter to select", obj["screen"])
        self.assertNotIn("paste-buffer", [c[1] for c in fs.calls])

    def test_stopped_session_is_never_attached(self):
        w, e, fs = live_env(self, live=False)
        st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": "/model opus"})
        self.assertEqual((obj["ok"], obj["code"]), (False, "stopped"))
        st, obj, _ = w.req("GET", "/api/session/s1/screen")
        self.assertEqual(obj, {"menu": False, "screen": None, "stopped": True})
        st, obj, _ = w.req("POST", "/api/session/s1/key", {"key": "enter"})
        self.assertEqual(obj["stopped"], True)
        self.assertEqual(fs.calls, [])  # ни has-session, ни new-session (attach), ни send-keys

    def test_screen_rewidth_is_throttled_per_session(self):
        w, e, fs = live_env(self)
        resizes = lambda: [c for c in fs.calls if c[1] == "resize-window"]
        w.req("GET", "/api/session/s1/screen")
        self.assertEqual(len(resizes()), 2)  # there and back: a garbled session re-renders
        w.req("GET", "/api/session/s1/screen")
        self.assertEqual(len(resizes()), 2)  # key presses bring the screen each time: no resize storm
        self.assertFalse([c for c in fs.calls if c[1] == "send-keys"])  # no Ctrl+L: an open menu is left alone
        e.t["v"] += cc.REWIDTH_EVERY
        w.req("GET", "/api/session/s1/screen")
        self.assertEqual(len(resizes()), 4)

    def test_screen_of_live_session(self):
        w, e, fs = live_env(self, screen="одна\n\n\n" + "\n".join("строка %d" % i for i in range(40)) + "\n\n\n")
        st, obj, _ = w.req("GET", "/api/session/s1/screen")
        self.assertEqual(st, 200)
        self.assertFalse(obj["menu"])
        lines = obj["screen"].split("\n")
        self.assertEqual(len(lines), 30)
        self.assertEqual(lines[-1], "строка 39")  # пустой хвост убран
        fs.screen = MENU_S
        self.assertTrue(w.req("GET", "/api/session/s1/screen")[1]["menu"])
        self.assertEqual(w.req("POST", "/api/session/s1/screen", {})[0], 405)

    def test_key_mapping_and_whitelist(self):
        w, e, fs = live_env(self, screen=MENU_S)
        expected = {"1": "1", "9": "9", "up": "Up", "down": "Down", "left": "Left", "right": "Right",
                    "tab": "Tab", "enter": "Enter", "esc": "Escape"}
        for key, tmux_key in expected.items():
            fs.keys.clear()
            st, obj, _ = w.req("POST", "/api/session/s1/key", {"key": key})
            self.assertEqual(st, 200, key)
            self.assertTrue(obj["ok"])
            self.assertEqual(fs.flat_keys, [tmux_key], key)
            self.assertTrue(obj["menu"])  # ответ — то же, что /screen
            self.assertIn("Enter to select", obj["screen"])
        for bad in ("0", "10", "F1", "ctrl-c", "", "Up", None, "C-c"):
            fs.keys.clear()
            st, obj, _ = w.req("POST", "/api/session/s1/key", {"key": bad})
            self.assertEqual(st, 400, bad)
            self.assertEqual(fs.flat_keys, [])

    def test_send_with_slash_goes_to_command_path(self):
        w, e, fs = live_env(self)
        e.bot.deliver = lambda *a, **k: self.fail("deliver не должен вызываться для слеш-команды")
        type_into(fs, "/compact", after_enter=MENU_S)
        st, obj, _ = w.req("POST", "/api/session/s1/send", {"text": "/compact"})
        self.assertEqual(st, 200)
        self.assertTrue(obj["ok"] and obj["menu"])
        self.assertIn("screen", obj)
        # обычный текст по-прежнему идёт через deliver
        e.bot.deliver = lambda chat, sid, text, origin="chat": (True, "")
        st, obj, _ = w.req("POST", "/api/session/s1/send", {"text": "привет"})
        self.assertEqual(obj, {"ok": True, "error": None, "code": None})


# ---------------------------------------------------------------- «одно событие — одно место»

def method_names(e, since):
    return [m for m, _ in e.tg.calls[since:]]


class OneEventOnePlaceTests(unittest.TestCase):
    def session(self, topics=False, transcript=True):
        w = WebEnv(self)
        e = w.e
        e.bot.topics_enabled = topics
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").close()
        mk(e, "s1", "working", 1, linkScanPath=tr)
        e.bot.list_sessions(True)
        e.bot.set_watch(1, "s1", True)
        e.bot.make_current(1, "s1")
        e.bot.notify_tick()  # первый проход только запоминает состояния
        return w, e, tr

    def set_state(self, e, state, detail="d"):
        e.job("s1", name="s1", state=state, detail=detail, cwd="/tmp", linkScanPath=e.bot.transcript_path("s1"),
              updatedAt=iso(BASE - 30))
        e.agents[0]["state"] = state

    def test_send_from_webapp_leaves_chat_and_topic_untouched(self):
        for topics in (False, True):
            w, e, tr = self.session(topics)
            base = len(e.tg.calls)
            runs_before = len(e.sent)
            st, obj, _ = w.req("POST", "/api/session/s1/send", {"text": "сделай X"})
            self.assertTrue(obj["ok"])
            self.assertEqual(len(e.sent), runs_before + 1)
            fw = e.bot.store.chat(1)["fw"].get("s1", {})
            self.assertFalse(fw.get("turn"))  # ход не отмечен
            self.assertEqual(e.bot.status, {})
            with open(tr, "ab") as f:
                f.write(asst("x1", "ответ модели"))
            e.bot.forward_tick()
            self.set_state(e, "working", "читает")
            e.bot.status_tick()
            self.set_state(e, "blocked", "нужен ответ")
            e.bot.notify_tick()
            self.set_state(e, "done", "готово")
            e.bot.notify_tick()
            e.bot.status_tick()
            chat_methods = {"sendMessage", "editMessageText", "setMessageReaction", "sendChatAction",
                            "deleteMessage", "sendDocument"}
            leaked = [m for m in method_names(e, base) if m in chat_methods]
            self.assertEqual(leaked, [], (topics, leaked))

    def test_send_from_chat_forwards_once_and_no_done_notice(self):
        for topics in (False, True):
            w, e, tr = self.session(topics)
            if topics:
                e.bot.ensure_topic(1, "s1")
            e.bot.handle_update(e.msg("вопрос", mid=9, thread=(thread_of(e, "s1") if topics else None)))
            with open(tr, "ab") as f:
                f.write(asst("x1", "итоговый ответ"))
            e.bot.forward_tick()
            e.bot.forward_tick()
            fwd = [m for m in e.tg.of("sendMessage") if "итоговый ответ" in m["text"]]
            self.assertEqual(len(fwd), 1, topics)
            if topics:
                self.assertEqual(fwd[0]["message_thread_id"], thread_of(e, "s1"))
            self.set_state(e, "done", "готово")
            n = len(e.tg.of("sendMessage"))
            e.bot.notify_tick()
            texts = [m["text"] for m in e.tg.of("sendMessage")[n:]]
            self.assertEqual(texts, [], texts)  # ни «готово», ни «закончила»

    def test_blocked_after_forwarded_turn_is_sent_unless_identical(self):
        w, e, tr = self.session()
        e.bot.handle_update(e.msg("вопрос", mid=9))
        with open(tr, "ab") as f:
            f.write(asst("x1", "ответ"))
        e.bot.forward_tick()
        self.set_state(e, "blocked", "нужно решение")
        e.bot.notify_tick()
        self.assertTrue(e.tg.of("sendMessage")[-1]["text"].startswith("🟡 s1 ждёт: нужно решение"))
        # то же, но последнее пересланное сообщение совпадает с текстом уведомления -> не дублируем
        w2, e2, tr2 = self.session()
        e2.bot.handle_update(e2.msg("вопрос", mid=9))
        e2.bot.turn_forwarded.add("s1")
        self.set_state(e2, "failed", "упала")
        s = [x for x in e2.bot.list_sessions(True) if x["id"] == "s1"][0]
        e2.bot.last_forward_text["s1"] = cc.notification_text(s, last=None)
        n = len(e2.tg.of("sendMessage"))
        e2.bot.notify_tick()
        self.assertEqual(len(e2.tg.of("sendMessage")), n)

    def test_notifications_suppressed_for_10_minutes_after_webapp_action(self):
        w, e, tr = self.session()
        w.req("POST", "/api/session/s1/watch", {"on": True})
        n = len(e.tg.of("sendMessage"))
        self.set_state(e, "blocked", "нужен ответ")
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), n)  # подавлено
        self.set_state(e, "working", "дальше")
        e.bot.notify_tick()
        e.t["v"] += 601
        self.set_state(e, "failed", "упала")
        e.bot.notify_tick()
        self.assertTrue(e.tg.of("sendMessage")[-1]["text"].startswith("🔴 s1 упала"))

    def test_each_webapp_action_marks_the_session(self):
        w, e, tr = self.session()
        for path, body in (("send", {"text": "x"}), ("watch", {"on": True}), ("rename", {"name": "Имя"}),
                           ("key", {"key": "enter"}), ("command", {"text": "/status"}), ("answer", {"action": "next"})):
            e.bot.webapp_touch.clear()
            w.req("POST", "/api/session/s1/" + path, body)
            self.assertIn("s1", e.bot.webapp_touch, path)

    def test_question_not_published_after_webapp_action_but_available_in_api(self):
        w, e, tr = self.session()
        w.req("POST", "/api/session/s1/send", {"text": "привет"})
        with open(tr, "ab") as f:
            f.write(ask_line("toolu_7", [Q_SIZE]))
        n = len(e.tg.of("sendMessage"))
        e.bot.forward_tick()
        self.assertEqual([m for m in e.tg.of("sendMessage")[n:] if "❓" in m["text"]], [])
        st, d, _ = w.req("GET", "/api/session/s1")
        self.assertEqual(d["question"]["tool_id"], "toolu_7")
        self.assertIsNone(e.bot.pending_question("s1")["mid"])
        # через 10 минут тихий вопрос можно опубликовать
        e.t["v"] += 601
        e.bot.on_ask(1, "s1", {"tool_id": "toolu_7", "questions": e.bot.pending_question("s1")["questions"]})
        self.assertTrue([m for m in e.tg.of("sendMessage")[n:] if "❓" in m["text"]])

    def test_published_question_is_still_edited_after_answer_from_webapp(self):
        w, e, tr = self.session()
        with open(tr, "ab") as f:
            f.write(ask_line("toolu_8", [Q_SIZE]))
        e.bot.forward_tick()  # опубликован в чате до действий в мини-аппе
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "❓" in m["text"]]), 1)
        fs = FakeScreen(MENU_S)
        e.bot.tmux = fs.tmux()
        e.agents[0]["pid"] = 5
        e.bot.list_sessions(True)
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        st, obj, _ = w.req("POST", "/api/session/s1/answer", {"action": "option", "index": 1})
        self.assertTrue(obj["ok"])
        with open(tr, "ab") as f:
            f.write(answer_line("toolu_8", 'User has answered: "Размер?"="S"'))
        e.bot.forward_tick()
        self.assertIn("❓ Размер — ✔ S", e.tg.of("editMessageText")[-1]["text"])

    def test_command_from_webapp_sends_nothing_to_chat(self):
        w, e, fs = live_env(self)
        type_into(fs, "/status")
        base = len(e.tg.calls)
        w.req("POST", "/api/session/s1/command", {"text": "/status"})
        self.assertEqual([m for m in method_names(e, base) if m in ("sendMessage", "editMessageText")], [])


# ---------------------------------------------------------------- архив и остановка

def archive_env(tc, topics=True):
    w = WebEnv(tc)
    e = w.e
    e.bot.topics_enabled = topics
    e.agents.clear()
    return w, e


def stop_runs(e, sid="s1"):
    return [r for r in e.runs if r[0][-2:] == ["stop", sid]]


class ArchiveTests(unittest.TestCase):
    def test_archive_live_session_stops_marks_drops_topic_and_unsubscribes(self):
        w, e = archive_env(self)
        mk(e, "s1", "working", 1)
        e.agents[-1]["pid"] = 77
        e.bot.list_sessions(True)
        e.bot.set_watch(1, "s1", True)
        e.bot.ensure_topic(1, "s1")
        self.assertIn("s1", e.bot.store.chat(1)["topics"])
        st, obj, _ = w.req("POST", "/api/session/s1/archive", {})
        self.assertEqual((st, obj), (200, {"ok": True, "stopped": True, "error": None}))
        self.assertEqual(len(stop_runs(e)), 1)
        self.assertTrue(e.bot.is_archived("s1"))
        self.assertFalse(e.bot.is_watched("s1"))
        self.assertNotIn("s1", e.bot.store.chat(1)["topics"])
        self.assertEqual(len(e.tg.of("deleteForumTopic")), 1)
        self.assertIn("s1", e.bot.webapp_touch)
        st, rows, _ = w.req("GET", "/api/sessions")
        row = [r for r in rows["sessions"] if r["id"] == "s1"][0]
        self.assertEqual(row["group"], "archived")
        self.assertIsInstance(row["archived_at"], float)
        self.assertEqual(w.req("GET", "/api/session/s1")[1]["group"], "archived")
        # топик для архивной заново не создаётся
        self.assertIsNone(e.bot.ensure_topic(1, "s1"))

    def test_archive_stopped_session_does_not_call_stop(self):
        w, e = archive_env(self)
        mk(e, "s1", "done", 1)  # без pid
        e.bot.list_sessions(True)
        st, obj, _ = w.req("POST", "/api/session/s1/archive", {})
        self.assertEqual((obj["ok"], obj["stopped"]), (True, False))
        self.assertEqual(stop_runs(e), [])
        self.assertTrue(e.bot.is_archived("s1"))

    def test_archive_without_stop_flag_and_failed_stop(self):
        w, e = archive_env(self)
        mk(e, "s1", "working", 1)
        e.agents[-1]["pid"] = 5
        e.bot.list_sessions(True)
        st, obj, _ = w.req("POST", "/api/session/s1/archive", {"stop": False})
        self.assertEqual((obj["ok"], obj["stopped"]), (True, False))
        self.assertEqual(stop_runs(e), [])
        e.bot.unarchive_session("s1")
        orig = e.bot.run
        e.bot.run = lambda args, **k: (1, "") if args[-2:] == ["stop", "s1"] else orig(args, **k)
        st, obj, _ = w.req("POST", "/api/session/s1/archive", {})
        self.assertEqual((obj["ok"], obj["stopped"]), (False, False))
        self.assertFalse(e.bot.is_archived("s1"))  # не остановили — в архив не убираем

    def test_unarchive_and_stop_endpoint(self):
        w, e = archive_env(self)
        mk(e, "s1", "done", 1)
        e.bot.list_sessions(True)
        w.req("POST", "/api/session/s1/archive", {})
        st, obj, _ = w.req("POST", "/api/session/s1/unarchive", {})
        self.assertEqual((st, obj), (200, {"ok": True}))
        self.assertFalse(e.bot.is_archived("s1"))
        self.assertEqual(w.req("GET", "/api/sessions")[1]["sessions"][0]["group"], "done")
        st, obj, _ = w.req("POST", "/api/session/s1/stop", {})
        self.assertEqual((obj["ok"], obj["stopped"]), (True, True))
        self.assertEqual(w.req("GET", "/api/session/s1/archive")[0], 405)

    def test_auto_unarchive_when_session_comes_alive(self):
        w, e = archive_env(self)
        mk(e, "s1", "done", 1)
        e.bot.list_sessions(True)
        e.bot.store.data["archived"]["s1"] = BASE
        # 1) процесс появился и занят
        e.agents[0].update({"pid": 9, "status": "busy"})
        e.bot.list_sessions(True)
        self.assertFalse(e.bot.is_archived("s1"))
        # 2) свежая работа после метки (updatedAt > ts + 60, state working)
        e.bot.store.data["archived"]["s1"] = BASE
        e.agents[0].pop("pid"), e.agents[0].pop("status")
        e.job("s1", name="s1", state="working", detail="d", cwd="/tmp", updatedAt=iso(BASE + 30))
        e.agents[0]["state"] = "working"
        e.bot.list_sessions(True)
        self.assertTrue(e.bot.is_archived("s1"))  # всего +30 с — ещё не считается
        e.job("s1", name="s1", state="blocked", detail="d", cwd="/tmp", updatedAt=iso(BASE + 120))
        e.agents[0]["state"] = "blocked"
        e.bot.list_sessions(True)
        self.assertFalse(e.bot.is_archived("s1"))
        # 3) done и после метки — не оживление
        e.bot.store.data["archived"]["s1"] = BASE
        e.job("s1", name="s1", state="done", detail="d", cwd="/tmp", updatedAt=iso(BASE + 500))
        e.agents[0]["state"] = "done"
        e.bot.list_sessions(True)
        self.assertTrue(e.bot.is_archived("s1"))

    def test_unarchive_brings_back_an_old_session(self):
        w, e = archive_env(self)
        mk(e, "par", "working", 1)
        mk(e, "kid", "working", 1)
        e.bot.store.data["chain_links"] = {"kid": "par"}
        e.bot.list_sessions(True)
        self.assertTrue(e.bot.is_old("par"))
        st, obj, _ = w.req("POST", "/api/session/par/unarchive", {})
        self.assertEqual((st, obj), (200, {"ok": True}))
        self.assertFalse(e.bot.is_old("par"))

    def test_archive_done_counts_only_plain_done(self):
        w, e = archive_env(self)
        mk(e, "d1", "done", 420)
        mk(e, "d2", "failed", 421)
        mk(e, "wk", "working", 1)
        mk(e, "bl", "blocked", 2)
        mk(e, "oldp", "done", 500)
        mk(e, "kid", "working", 1)
        mk(e, "gone", "done", 430)
        mk(e, "fresh", "done", 30)  # finished a turn half an hour ago: still in use, not archived
        e.bot.store.data["chain_links"] = {"kid": "oldp"}
        e.bot.list_sessions(True)
        e.bot.store.data["archived"]["gone"] = BASE
        n = len(e.runs)
        st, obj, _ = w.req("POST", "/api/archive_done", {})
        self.assertEqual((st, obj), (200, {"ok": True, "count": 2}))
        self.assertTrue(e.bot.is_archived("d1") and e.bot.is_archived("d2"))
        self.assertFalse(any(e.bot.is_archived(x) for x in ("wk", "bl", "oldp", "kid", "fresh")))
        self.assertEqual([r for r in e.runs[n:] if "stop" in r[0]], [])  # без stop
        self.assertEqual(w.req("POST", "/api/archive_done", {})[1]["count"], 0)

    def test_groups_in_panel_and_archive_mode_sections(self):
        w, e = archive_env(self, topics=False)
        mk(e, "wk", "working", 1)
        mk(e, "dn", "done", 5)
        mk(e, "arc1", "done", 3)
        mk(e, "oldp", "done", 500)
        mk(e, "kid", "working", 1)
        e.bot.store.data["chain_links"] = {"kid": "oldp"}
        e.bot.list_sessions(True)
        e.bot.store.data["archived"]["arc1"] = BASE - 2 * 86400
        _, plain, markup = e.bot.panel_render(1)
        self.assertIn("🔵 2 в работе", plain.split("\n")[0])  # arc1 не считается
        self.assertNotIn("arc1", plain)
        self.assertIn("🗂 Архив 3", str(markup))  # old + archived + done
        e.bot.panel_state(1)["show_all"] = True
        html, plain, markup = e.bot.panel_render(1)
        self.assertLess(plain.index("СМЕНИЛИСЬ"), plain.index("В АРХИВЕ"))
        self.assertLess(plain.index("В АРХИВЕ"), plain.index("ГОТОВО"))
        self.assertIn("arc1\nв архиве · 2 дн", plain)
        self.assertIn("🗂 Всё готовое в архив", str(markup))
        self.assertIn("📦 arc1", str(markup))
        api = {r["id"]: r["group"] for r in e.bot.api_sessions(1)["sessions"]}
        self.assertEqual(api, {"wk": "work", "dn": "done", "arc1": "archived", "oldp": "old", "kid": "work"})

    def test_panel_archive_all_with_confirmation(self):
        w, e = archive_env(self, topics=False)
        mk(e, "d1", "done", 400)
        mk(e, "d2", "done", 401)
        mk(e, "wk", "working", 1)
        e.bot.list_sessions(True)
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pa:1"))
        e.bot.handle_update(e.cb("pm:arch"))
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("Убрать в архив готовых сессий: 2", ed["text"])
        self.assertEqual([b["callback_data"] for r in ed["reply_markup"]["inline_keyboard"] for b in r], ["pay", "pa:1"])
        self.assertFalse(e.bot.is_archived("d1"))  # без подтверждения ничего не меняется
        e.bot.handle_update(e.cb("pay"))
        self.assertTrue(e.bot.is_archived("d1") and e.bot.is_archived("d2") and not e.bot.is_archived("wk"))
        self.assertIn("В архив: 2", e.tg.of("editMessageText")[-1]["text"])

    def test_notifications_and_questions_suppressed_for_archived(self):
        w, e = archive_env(self, topics=False)
        mk(e, "s1", "working", 1)
        e.bot.list_sessions(True)
        e.bot.notify_tick()
        e.bot.store.data["archived"]["s1"] = BASE
        e.bot.set_watch(1, "s1", True)  # даже при подписке
        e.job("s1", name="s1", state="blocked", detail="ждёт", cwd="/tmp", updatedAt=iso(BASE - 50))
        e.agents[0]["state"] = "blocked"
        n = len(e.tg.of("sendMessage"))
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), n)
        e.bot.on_ask(1, "s1", {"tool_id": "t9", "questions": cc.normalize_questions({"questions": [Q_SIZE]})})
        self.assertEqual(e.bot.store.data["questions"], {})

    def test_card_buttons_and_confirmation_in_telegram(self):
        w, e = archive_env(self, topics=False)
        mk(e, "s1", "working", 1)
        e.agents[-1]["pid"] = 3
        e.bot.list_sessions(True)
        flat = {b["text"]: b["callback_data"] for r in cc.card_markup("s1")["inline_keyboard"] for b in r}
        self.assertEqual(flat["🗂 В архив"], "arc:s1")
        self.assertNotIn("↩ Из архива", flat)
        flat = {b["text"]: b["callback_data"] for r in cc.card_markup("s1", archived=True)["inline_keyboard"] for b in r}
        self.assertEqual(flat["↩ Из архива"], "uar:s1")
        self.assertNotIn("🗂 В архив", flat)
        e.bot.handle_update(e.cb("arc:s1"))
        m = e.tg.of("sendMessage")[-1]
        self.assertEqual(m["text"], "Остановить и убрать в архив? Разговор сохранится.")
        self.assertEqual([(b["text"], b["callback_data"]) for r in m["reply_markup"]["inline_keyboard"] for b in r],
                         [("Да", "arcy:s1"), ("Отмена", "cl")])
        self.assertFalse(e.bot.is_archived("s1"))
        self.assertEqual(stop_runs(e), [])
        e.bot.handle_update(e.cb("arcy:s1"))
        self.assertTrue(e.bot.is_archived("s1"))
        self.assertEqual(len(stop_runs(e)), 1)
        self.assertTrue(e.bot.card_text("s1").startswith("📦 В архиве"))
        e.bot.handle_update(e.cb("uar:s1"))
        self.assertFalse(e.bot.is_archived("s1"))

    def test_opening_archived_session_does_not_recreate_topic(self):
        w, e = archive_env(self)
        mk(e, "s1", "done", 1)
        e.bot.list_sessions(True)
        e.bot.store.data["archived"]["s1"] = BASE
        before = len(e.tg.of("createForumTopic"))
        e.bot.handle_update(e.cb("s:s1"))
        self.assertEqual([c["name"] for c in e.tg.of("createForumTopic")[before:]], [])
        self.assertTrue([m for m in e.tg.of("sendMessage") if m["text"].startswith("📦 В архиве")])

    def test_model_and_effort_hints(self):
        w, e = archive_env(self)
        st, obj, _ = w.req("GET", "/api/commands")
        by = {c["cmd"]: c for c in obj["commands"]}
        self.assertEqual(by["/model"]["hint"], "Меняет и умолчание для новых сессий")
        self.assertEqual(by["/effort"]["hint"], "Меняет и умолчание для новых сессий")
        self.assertEqual(by["/compact"]["hint"], "Необязательно: что сохранить")


# ---------------------------------------------------------------- файлы туда и обратно

from datetime import datetime as _dt


def write_file(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)

    def h(self, *parts):
        return os.path.join(self.home, *parts)

    def test_symlink_into_forbidden_and_size_limit(self):
        secret = write_file(self.h(".ssh", "id_rsa"))
        os.symlink(secret, self.h("docs-link.txt"))
        self.assertIsNone(cc.file_ok(self.h("docs-link.txt"), self.home))  # realpath проверяется тоже
        good = write_file(self.h("docs", "a.txt"))
        os.symlink(good, self.h("alias.txt"))
        self.assertIsNotNone(cc.file_ok(self.h("alias.txt"), self.home))
        big = self.h("big.bin")
        with open(big, "wb") as f:
            f.truncate(cc.FILE_MAX + 1)
        self.assertIsNone(cc.file_ok(big, self.home))
        with open(big, "wb") as f:
            f.truncate(cc.FILE_MAX)
        self.assertIsNotNone(cc.file_ok(big, self.home))

    def test_find_files_in_text_filters_dedups_and_limits(self):
        for i in range(8):
            write_file(self.h("out", "f%d.txt" % i))
        write_file(self.h(".ssh", "id_rsa"))
        pdf = write_file(self.h("out", "рапорт.pdf"), b"%PDF")
        text = ("Готово: `%s` и ~/out/f0.txt, ещё %s. Секрет %s, нет файла /nope/x.md, "
                "ссылка https://example.com/out/f1.txt, папка %s\\n" % (pdf, pdf, self.h(".ssh", "id_rsa"), self.h("out"))
                + " ".join("~/out/f%d.txt" % i for i in range(8)))
        files = cc.find_files(text, self.home)
        self.assertEqual(len(files), 6)
        self.assertEqual(files[0], {"path": pdf, "name": "рапорт.pdf", "size": 4, "kind": "pdf"})
        self.assertEqual([f["name"] for f in files[1:3]], ["f0.txt", "f1.txt"])
        self.assertEqual(len({f["path"] for f in files}), 6)
        self.assertEqual(cc.find_files("нет путей", self.home), [])

    def test_kinds(self):
        for name, kind in (("a.PNG", "image"), ("a.pdf", "pdf"), ("a.html", "html"), ("a.md", "text"),
                           ("a.tar", "archive"), ("a.mp4", "video"), ("a.mp3", "audio"), ("a.bin", "other"),
                           ("noext", "other")):
            self.assertEqual(cc.file_kind(name), kind, name)

    def test_history_assistant_entries_carry_files(self):
        pdf = write_file(self.h("out", "r.pdf"), b"12345")
        data = asst_ts("a1", "Файл: %s" % pdf, "t1") + asst_ts("a2", "без файлов", "t2")
        h = cc.parse_history(data, home=self.home)
        self.assertEqual(h[0]["files"], [{"path": pdf, "name": "r.pdf", "size": 5, "kind": "pdf"}])
        self.assertEqual(h[1]["files"], [])

    def test_safe_dirname_keeps_name_and_never_escapes(self):
        self.assertEqual(cc.safe_dirname("zidos-pager: подтверждения SDK №4"), "zidos-pager: подтверждения SDK №4")
        for bad in ("..", "../..", "/etc", "a/../b", "x\x00y"):
            d = cc.safe_dirname(bad)
            self.assertNotIn("/", d)
            self.assertNotIn(d, ("", ".", ".."))  if d else None
        self.assertEqual(cc.safe_dirname(".."), "")  # empty -> the caller falls back to the sid

    def test_inbox_folder_stable_and_collision_suffix(self):
        e = panel_env(self)
        mk(e, "s1", "working", 1, name="Альфа")
        mk(e, "s2", "working", 1, name="Альфа")
        e.bot.list_sessions(True)
        self.assertEqual(e.bot.inbox_folder("s1"), "Альфа")
        self.assertEqual(e.bot.inbox_folder("s2"), "Альфа (s2)")
        e.bot.session_name = lambda sid: "Другое"  # переименование не плодит папки
        self.assertEqual(e.bot.inbox_folder("s1"), "Альфа")
        self.assertEqual(e.bot.store.data["inbox_dirs"], {"s1": "Альфа", "s2": "Альфа (s2)"})

    def test_save_inbox_folder_modes(self):
        p = cc.save_inbox(self.home, b"1", "a.txt", BASE, "Папка")
        day = _dt.fromtimestamp(BASE).strftime("%Y-%m-%d")
        self.assertEqual(p, os.path.join(self.home, "tg-inbox", "Папка", day, "a.txt"))
        for d in (os.path.join(self.home, "tg-inbox", "Папка"), os.path.dirname(p)):
            self.assertEqual(oct(os.stat(d).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o600")

    def test_inbox_saving_no_overwrite_and_modes(self):
        p1 = cc.save_inbox(self.home, b"1", "отчёт.pdf", BASE)
        p2 = cc.save_inbox(self.home, b"2", "отчёт.pdf", BASE)
        p3 = cc.save_inbox(self.home, b"3", "../../etc/passwd", BASE)
        day = _dt.fromtimestamp(BASE).strftime("%Y-%m-%d")
        self.assertEqual(p1, os.path.join(self.home, "tg-inbox", day, "отчёт.pdf"))
        self.assertEqual(os.path.basename(p2), "отчёт-1.pdf")
        self.assertEqual(os.path.dirname(p3), os.path.dirname(p1))  # имя безопасно: путь не уехал
        self.assertEqual(os.path.basename(p3), "passwd")
        self.assertEqual(oct(os.stat(p1).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(os.path.dirname(p1)).st_mode & 0o777), "0o700")
        self.assertEqual(oct(os.stat(os.path.join(self.home, "tg-inbox")).st_mode & 0o777), "0o700")
        self.assertEqual(open(p1, "rb").read(), b"1")


def file_msg(kind="document", mid=60, thread=None, caption=None, reply=None, **extra):
    m = {"message_id": mid, "from": {"id": 1}, "chat": {"id": 1, "type": "private"}}
    if kind == "document":
        m["document"] = {"file_id": "F1", "file_name": "Отчёт.pdf", "file_size": 5, "mime_type": "application/pdf"}
    elif kind == "photo":
        m["photo"] = [{"file_id": "P1", "file_unique_id": "u1", "width": 90, "height": 90, "file_size": 100},
                      {"file_id": "P2", "file_unique_id": "u2", "width": 1280, "height": 960, "file_size": 5000},
                      {"file_id": "P0", "file_unique_id": "u0", "width": 320, "height": 240, "file_size": 900}]
    else:
        m[kind] = {"file_id": "X1", "file_unique_id": "ux", "file_size": 7}
    m["document" if kind == "document" else kind].update(extra) if extra else None
    if thread:
        m["message_thread_id"] = thread
    if caption:
        m["caption"] = caption
    if reply:
        m["reply_to_message"] = {"message_id": reply}
    return {"update_id": mid, "message": m}


class TelegramFileInTests(unittest.TestCase):
    def topic_session(self):
        e = panel_env(self)
        mk(e, "s1", "working", 1, name="Альфа")
        e.bot.list_sessions(True)
        e.bot.ensure_topic(1, "s1")
        return e

    def test_document_from_topic_saved_and_delivered(self):
        e = self.topic_session()
        e.tg.files["F1"] = ("documents/file_1.pdf", b"%PDF-")
        e.bot.handle_update(file_msg(thread=thread_of(e, "s1"), caption="посмотри"))
        day = _dt.fromtimestamp(BASE).strftime("%Y-%m-%d")
        path = os.path.join(e.cfg.home, "tg-inbox", "Альфа", day, "Отчёт.pdf")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(open(path, "rb").read(), b"%PDF-")
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        for d in (os.path.dirname(path), os.path.dirname(os.path.dirname(path))):
            self.assertEqual(oct(os.stat(d).st_mode & 0o777), "0o700")
        self.assertEqual(e.sent, [("s1", "Файл от владельца: %s (5 Б, application/pdf)\nпосмотри" % path)])
        r = e.tg.of("setMessageReaction")[0]
        self.assertEqual(r["message_id"], 60)
        # тот же файл ещё раз — суффикс, без перезаписи
        e.bot.handle_update(file_msg(mid=61, thread=thread_of(e, "s1")))
        self.assertTrue(os.path.exists(os.path.join(os.path.dirname(path), "Отчёт-1.pdf")))
        self.assertIn("Отчёт-1.pdf", e.sent[-1][1])

    def test_photo_picks_largest_and_voice_video_audio(self):
        e = self.topic_session()
        th = thread_of(e, "s1")
        e.tg.files["P2"] = ("photos/p2.jpg", b"JPEGDATA")
        e.bot.handle_update(file_msg("photo", thread=th))
        self.assertIn("photo_u2.jpg (8 Б, image/jpeg)", e.sent[-1][1])
        for kind, fname, mime in (("video", "video_ux.mp4", "video/mp4"),):  # voice/audio go to speech recognition (Stt*Tests)
            e.tg.files["X1"] = ("m/" + kind, b"1234567")
            e.bot.handle_update(file_msg(kind, mid=70, thread=th))
            self.assertIn("%s (7 Б, %s)" % (fname, mime), e.sent[-1][1], kind)

    def test_too_big_files_get_hint(self):
        e = self.topic_session()
        th = thread_of(e, "s1")
        e.tg.files["F1"] = ("documents/big.bin", b"x", 25 * 1024 * 1024)  # getFile сообщает размер
        e.bot.handle_update(file_msg(thread=th))
        self.assertEqual(e.sent, [])
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "Файл больше 20 МБ — пришлите через мини-апп")
        self.assertEqual(e.tg.of("sendMessage")[-1]["message_thread_id"], th)
        # размер известен сразу в сообщении
        upd = file_msg(thread=th)
        upd["message"]["document"]["file_size"] = 30 * 1024 * 1024
        n = len(e.tg.of("getFile"))
        e.bot.handle_update(upd)
        self.assertEqual(len(e.tg.of("getFile")), n)  # даже не просим файл
        self.assertFalse(os.path.exists(os.path.join(e.cfg.home, "tg-inbox")))

    def test_file_in_all_is_routed_and_panel_file_gets_hint(self):
        e = self.topic_session()
        stub_router(e, '{"sid": "s1", "cwd": null}')
        e.tg.files["F1"] = ("documents/file_1.pdf", b"%PDF-")
        e.bot.handle_update(file_msg(mid=62, caption="глянь"))  # All: routed like text
        self.assertEqual(len(e.sent), 1)
        self.assertEqual(e.sent[0][0], "s1")
        self.assertTrue(e.sent[0][1].startswith("Файл от владельца: ") and e.sent[0][1].endswith("\nглянь"))
        self.assertIn(62, [d["message_id"] for d in e.tg.of("deleteMessage")])  # All stays clean
        self.assertFalse([m for m in e.tg.of("sendMessage") if "Пришлите файл" in m["text"]])
        e.bot.panel_publish(1)
        e.bot.handle_update(file_msg(mid=63, thread=pth(e)))  # топик пульта
        self.assertEqual(len(e.sent), 1)  # nothing new reaches a session
        self.assertIn("Файл нужно прислать в топик сессии", e.tg.of("editMessageText")[-1]["text"])
        self.assertIn(63, [d["message_id"] for d in e.tg.of("deleteMessage")])  # в пульте лишнее убирается

    def test_file_without_topics_goes_to_current_or_replied_session(self):
        e = panel_env(self, topics=False)
        mk(e, "s1", "working", 1)
        mk(e, "s2", "working", 1)
        e.bot.list_sessions(True)
        e.bot.make_current(1, "s1")
        e.tg.files["F1"] = ("d/f.pdf", b"12345")
        e.bot.handle_update(file_msg(mid=64))
        self.assertEqual(e.sent[-1][0], "s1")
        e.bot.say(1, "про s2", sid="s2")
        e.bot.handle_update(file_msg(mid=65, reply=e.tg.n))
        self.assertEqual(e.sent[-1][0], "s2")

    def test_file_in_all_goes_to_chosen_session_folder(self):
        e = self.topic_session()
        stub_router(e, '{"sid": "s1", "cwd": null}')
        e.tg.files["F1"] = ("documents/file_1.pdf", b"%PDF-")
        e.bot.handle_update(file_msg(mid=62, caption="глянь"))
        day = _dt.fromtimestamp(BASE).strftime("%Y-%m-%d")
        path = os.path.join(e.cfg.home, "tg-inbox", "Альфа", day, "Отчёт.pdf")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(e.sent, [("s1", "Файл от владельца: %s (5 Б, application/pdf)\nглянь" % path)])
        self.assertEqual(e.bot.store.data["inbox_dirs"]["s1"], "Альфа")

    def test_file_in_all_new_session_gets_caption_folder_bound_to_sid(self):
        e = route_env(self, '{"sid": null, "cwd": null}')
        e.tg.files["F1"] = ("documents/file_1.pdf", b"%PDF-")
        e.bot.handle_update(file_msg(mid=62, caption="разбери счёт"))
        day = _dt.fromtimestamp(e.t["v"]).strftime("%Y-%m-%d")
        path = os.path.join(e.cfg.home, "tg-inbox", "разбери счёт", day, "Отчёт.pdf")
        self.assertTrue(os.path.exists(path))
        self.assertEqual(len(bg_runs(e)), 1)
        self.assertEqual(e.bot.store.data["inbox_dirs"]["abcd1234"], "разбери счёт")
        self.assertIn(path, e.bot.store.chat(1)["route_last"]["text"])

    def test_download_error_is_reported(self):
        e = self.topic_session()
        e.bot.handle_update(file_msg(thread=thread_of(e, "s1")))  # file_id неизвестен
        self.assertEqual(e.sent, [])
        self.assertIn("Не получилось скачать файл", e.tg.of("sendMessage")[-1]["text"])


def multipart(fields, file=None, boundary="----ccbound"):
    out = b""
    for k, v in fields.items():
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode()
    if file:
        out += ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
                "Content-Type: application/octet-stream\r\n\r\n" % (boundary, file[0])).encode() + file[1] + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return out, "multipart/form-data; boundary=" + boundary


class WebFilesTests(unittest.TestCase):
    def setUp(self):
        self.w = WebEnv(self)
        self.e = self.w.e
        mk(self.e, "s1", "working", 1, name="Альфа")
        self.e.bot.list_sessions(True)
        self.home = self.e.cfg.home

    def test_upload_multipart_saves_and_delivers_as_webapp(self):
        e = self.e
        body, ctype = multipart({"caption": "вот лог"}, ("лог 1.txt", "строка\n".encode() * 20000))
        self.assertGreater(len(body), cc.MAX_BODY)  # больше обычного лимита 64 КБ
        st, obj, _ = self.w.req("POST", "/api/session/s1/upload", raw_body=body, headers={"Content-Type": ctype})
        self.assertEqual(st, 200, obj)
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["size"], len("строка\n".encode()) * 20000)
        self.assertTrue(os.path.isfile(obj["path"]))
        self.assertEqual(oct(os.stat(obj["path"]).st_mode & 0o777), "0o600")
        day = _dt.fromtimestamp(BASE).strftime("%Y-%m-%d")
        self.assertEqual(os.path.dirname(obj["path"]), os.path.join(self.home, "tg-inbox", "Альфа", day))
        self.assertEqual(oct(os.stat(os.path.dirname(obj["path"])).st_mode & 0o777), "0o700")
        self.assertEqual(len(e.sent), 1)
        self.assertTrue(e.sent[0][1].startswith("Файл от владельца: %s (" % obj["path"]))
        self.assertTrue(e.sent[0][1].endswith("\nвот лог"))
        self.assertIn("s1", e.bot.webapp_touch)
        self.assertFalse(e.bot.store.chat(1)["fw"].get("s1", {}).get("turn"))  # origin webapp: без пересылки хода

    def test_upload_errors(self):
        body, ctype = multipart({"caption": "x"})
        st, obj, _ = self.w.req("POST", "/api/session/s1/upload", raw_body=body, headers={"Content-Type": ctype})
        self.assertEqual(st, 400)
        st, obj, _ = self.w.req("POST", "/api/session/s1/upload", raw_body=b"{}",
                                headers={"Content-Type": "application/json"})
        self.assertEqual(st, 400)
        body, ctype = multipart({}, ("a.txt", b"x"))
        st, obj, _ = self.w.req("POST", "/api/session/s1/upload", raw_body=body, headers={"Content-Type": ctype},
                                auth=False)
        self.assertEqual(st, 401)
        st, obj, _ = self.w.req("POST", "/api/session/" + urllib.parse.quote("нет") + "/upload", raw_body=body, headers={"Content-Type": ctype})
        self.assertEqual(st, 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.w.srv.port, timeout=10)  # заявленный размер > 51 МБ
        conn.putrequest("POST", "/api/session/s1/upload")
        conn.putheader("X-Tg-Init-Data", make_init_data())
        conn.putheader("Content-Type", ctype)
        conn.putheader("Content-Length", str(cc.UPLOAD_MAX + 1))
        conn.endheaders()
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()

    def test_download_token_flow(self):
        e = self.e
        path = write_file(os.path.join(self.home, "out", "Отчёт №1.txt"), b"hello" * 100000)
        st, obj, _ = self.w.req("GET", "/api/file?path=" + urllib.parse.quote(path))
        self.assertEqual(st, 200)
        url = obj["url"]
        self.assertTrue(url.startswith("/f/"))
        # без initData, с подписью
        st, data, r = self.w.req("GET", url, auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(data, b"hello" * 100000)
        self.assertEqual(r.getheader("Content-Disposition"),
                         "attachment; filename*=UTF-8''" + urllib.parse.quote("Отчёт №1.txt"))
        self.assertEqual(r.getheader("Content-Type"), "text/plain")
        self.assertEqual(r.getheader("Content-Length"), str(500000))
        # «~/…» тоже принимается
        st, obj, _ = self.w.req("GET", "/api/file?path=" + urllib.parse.quote("~/out/Отчёт №1.txt"))
        self.assertEqual(st, 200)
        # просроченный
        e.t["v"] += 601
        self.assertEqual(self.w.req("GET", url, auth=False)[0], 403)
        e.t["v"] -= 601
        # подделка подписи / пути
        b64, exp, sig = url[3:].split(".")
        forged = "%s.%s.%s" % (b64, exp, sig[:-1] + ("0" if sig[-1] != "0" else "1"))
        self.assertEqual(self.w.req("GET", "/f/" + forged, auth=False)[0], 403)
        other = base64.urlsafe_b64encode(b"/etc/hostname").rstrip(b"=").decode()
        self.assertEqual(self.w.req("GET", "/f/%s.%s.%s" % (other, exp, sig), auth=False)[0], 403)
        self.assertEqual(self.w.req("GET", "/f/" + urllib.parse.quote("мусор"), auth=False)[0], 403)
        # токен на запрещённый путь (даже с верной подписью) не отдаёт файл
        secret = write_file(os.path.join(self.home, ".ssh", "id_rsa"), b"KEY")
        tok = cc.make_file_token(secret, e.cfg.token, e.bot.clock())
        self.assertEqual(self.w.req("GET", "/f/" + tok, auth=False)[0], 403)
        # выдача токена: запрещённые пути и отсутствие авторизации
        self.assertEqual(self.w.req("GET", "/api/file?path=" + urllib.parse.quote(secret))[0], 403)
        self.assertEqual(self.w.req("GET", "/api/file?path=/etc/hostname")[0], 403)
        self.assertEqual(self.w.req("GET", "/api/file?path=" + urllib.parse.quote(path), auth=False)[0], 401)
        # файл удалён после выдачи токена
        os.remove(path)
        self.assertEqual(self.w.req("GET", url, auth=False)[0], 403)

    def test_sendfile_to_topic_and_main_chat(self):
        e = self.e
        path = write_file(os.path.join(self.home, "out", "r.pdf"), b"%PDF-1")
        e.bot.topics_enabled = True
        e.bot.ensure_topic(1, "s1")
        th = thread_of(e, "s1")
        st, obj, _ = self.w.req("POST", "/api/session/s1/sendfile", {"path": path})
        self.assertEqual((st, obj), (200, {"ok": True}))
        raw = e.tg.of("sendDocument")[-1]["raw"]
        self.assertIn(b'name="message_thread_id"\r\n\r\n%d' % th, raw)
        self.assertIn(b'filename="r.pdf"', raw)
        self.assertIn(b"Content-Type: application/pdf", raw)
        self.assertIn(b"%PDF-1", raw)
        e.bot.topics_enabled = False
        self.w.req("POST", "/api/session/s1/sendfile", {"path": path})
        self.assertNotIn(b"message_thread_id", e.tg.of("sendDocument")[-1]["raw"])
        secret = write_file(os.path.join(self.home, ".ssh", "id_rsa"), b"KEY")
        st, obj, _ = self.w.req("POST", "/api/session/s1/sendfile", {"path": secret})
        self.assertEqual((st, obj["ok"]), (403, False))
        self.assertEqual(len(e.tg.of("sendDocument")), 2)  # запрещённое не ушло


class CcSendTests(unittest.TestCase):
    SCRIPT = os.path.join(os.path.dirname(os.path.abspath(cc.__file__)), "deploy", "cc-send")

    def run_script(self, args, env_extra):
        env = {"PATH": os.environ["PATH"], **env_extra}
        return subprocess.run([sys.executable, self.SCRIPT] + args, env=env, capture_output=True, text=True,
                              timeout=30)

    def test_script_creates_atomic_request(self):
        self.assertTrue(os.access(self.SCRIPT, os.X_OK))
        with tempfile.TemporaryDirectory() as d:
            f = write_file(os.path.join(d, "доклад.pdf"))
            box = os.path.join(d, "outbox")
            r = self.run_script([f, "готовый", "доклад"], {"HOME": d, "CC_OUTBOX": box,
                                                          "CLAUDE_JOB_DIR": "/home/x/.claude/jobs/abcd1234/"})
            self.assertEqual(r.returncode, 0, r.stderr)
            names = os.listdir(box)
            self.assertEqual(len(names), 1)
            self.assertTrue(names[0].endswith(".json") and not names[0].startswith("."))
            req = json.load(open(os.path.join(box, names[0])))
            self.assertEqual((req["sid"], req["path"], req["caption"]), ("abcd1234", f, "готовый доклад"))
            self.assertIn("ts", req)
            self.assertEqual(oct(os.stat(os.path.join(box, names[0])).st_mode & 0o777), "0o600")
            r = self.run_script([f, "--session", "deadbeef"], {"HOME": d, "CC_OUTBOX": box})
            self.assertEqual(r.returncode, 0)
            self.assertEqual(sorted(json.load(open(os.path.join(box, n)))["sid"] for n in os.listdir(box)),
                             ["abcd1234", "deadbeef"])
            self.assertEqual(self.run_script([f], {"HOME": d, "CC_OUTBOX": box}).returncode, 2)  # нет сессии
            r = self.run_script([os.path.join(d, "нет.pdf")], {"HOME": d, "CC_OUTBOX": box,
                                                               "CLAUDE_JOB_DIR": "/x/abcd1234"})
            self.assertEqual(r.returncode, 1)
            self.assertEqual(len(os.listdir(box)), 2)

    def test_bot_sends_outbox_requests(self):
        e = panel_env(self)
        box = os.path.join(e.tmp.name, "outbox")
        e.cfg.outbox_dir = box
        mk(e, "s1", "working", 1, name="Альфа")
        e.bot.list_sessions(True)
        e.bot.ensure_topic(1, "s1")
        f = write_file(os.path.join(e.cfg.home, "out", "доклад.pdf"), b"%PDF")
        r = self.run_script([f, "готовый", "доклад", "--session", "s1"], {"HOME": e.cfg.home, "CC_OUTBOX": box})
        self.assertEqual(r.returncode, 0, r.stderr)
        e.bot.outbox_tick()
        raw = e.tg.of("sendDocument")[-1]["raw"]
        self.assertIn("📎 Альфа: готовый доклад".encode(), raw)
        self.assertIn(b'name="message_thread_id"\r\n\r\n%d' % thread_of(e, "s1"), raw)
        self.assertIn('filename="доклад.pdf"'.encode(), raw)
        self.assertEqual(os.listdir(box), [])  # заявка удалена
        # запрещённый путь / нет файла -> сообщение об ошибке, заявка удалена
        secret = write_file(os.path.join(e.cfg.home, ".ssh", "id_rsa"), b"KEY")
        before = len(e.tg.of("sendDocument"))
        for path in (secret, os.path.join(e.cfg.home, "нет.txt")):
            with open(os.path.join(box, "%s.json" % abs(hash(path))), "w") as fh:
                json.dump({"sid": "s1", "path": path, "caption": "", "ts": 1}, fh)
        with open(os.path.join(box, "bad.json"), "w") as fh:
            fh.write("{не json")
        e.bot.outbox_tick()
        self.assertEqual(len(e.tg.of("sendDocument")), before)
        errs = [m for m in e.tg.of("sendMessage") if m["text"].startswith("Не смог отправить файл")]
        self.assertEqual(len(errs), 2)
        self.assertEqual(errs[0]["message_thread_id"], thread_of(e, "s1"))
        self.assertEqual(os.listdir(box), [])

    def test_unknown_session_goes_to_main_chat(self):
        e = panel_env(self)
        box = os.path.join(e.tmp.name, "outbox2")
        e.cfg.outbox_dir = box
        f = write_file(os.path.join(e.cfg.home, "x.txt"), b"1")
        self.run_script([f, "--session", "ffffffff"], {"HOME": e.cfg.home, "CC_OUTBOX": box})
        e.bot.outbox_tick()
        raw = e.tg.of("sendDocument")[-1]["raw"]
        self.assertNotIn(b"message_thread_id", raw)
        self.assertIn("📎 ffffffff".encode(), raw)


# ---------------------------------------------------------------- правила цепочек: имена передач, папки, копии

class ChainRulesTests(unittest.TestCase):
    def setUp(self):
        self.e = panel_env(self)

    def sess(self, sid, minutes, name=None, intent="", transcript=b""):
        chain_session(self.e, sid, minutes, "done", intent=intent, transcript=transcript, name=name or sid)

    def links(self):
        self.e.bot.chains_scan()
        return self.e.bot.store.data["chain_links"]

    def test_handoff_file_name_patterns(self):
        for name in ("HANDOFF-1.md", "NEW-SESSION-PROMPT.md", "PROMPT-NEW-SESSION.md", "next-session.md",
                     "session-prompt.md", "Resume-plan.md", "notes-handoff-v2.md"):
            self.assertEqual(cc.handoff_paths("см. /var/tmp/x/%s" % name, "/h"), ["/var/tmp/x/" + name], name)
        for name in ("README.md", "notes.md", "plan.md"):
            self.assertEqual(cc.handoff_paths("см. /var/tmp/x/%s" % name, "/h"), [], name)
        # слово в имени папки не делает файл передачей
        self.assertEqual(cc.handoff_paths("/var/tmp/handoff/notes.md", "/h"), [])
        self.assertTrue(cc.is_handoff_name("/a/b/NEW-SESSION-PROMPT.md"))

    def test_folder_line_links_by_creation_time(self):
        self.sess("p1", 300, intent="Выполни план /var/tmp/zt-plan/NEW-SESSION-PROMPT.md")
        self.sess("p2", 200, intent="Продолжай по /var/tmp/zt-plan/HANDOFF-20261013-1000.md")
        self.sess("p3", 100, intent="Ещё раз: /var/tmp/zt-plan/HANDOFF-20261013-2200.md")
        self.sess("other", 150, intent="Другое: /var/tmp/other-plan/NEW-SESSION-PROMPT.md")
        self.assertEqual(self.links(), {"p2": "p1", "p3": "p2"})  # родитель — ближайшая более ранняя

    def test_generic_folders_do_not_link(self):
        for kind in ("/var/tmp", "/tmp", "HOME", "HOME/notes"):
            e = panel_env(self)
            folder = kind.replace("HOME", e.cfg.home)
            e.cfg.chain_generic_dirs = [e.cfg.home + "/notes"]  # CC_CHAIN_GENERIC_DIRS
            chain_session(e, "a", 300, "done", intent="Continue %s/HANDOFF-1.md" % folder)
            chain_session(e, "b", 100, "done", intent="Continue %s/NEW-SESSION-PROMPT.md" % folder)
            e.bot.chains_scan()
            self.assertEqual(e.bot.store.data["chain_links"], {}, folder)
        # a subfolder of a generic folder is already a line; without configuration a normal folder links
        e = panel_env(self)
        e.cfg.chain_generic_dirs = [e.cfg.home + "/notes"]
        chain_session(e, "a", 300, "done", intent="~/notes/track-a/PROMPT-NEW-SESSION.md")
        chain_session(e, "b", 100, "done", intent="~/notes/track-a/HANDOFF-2.md")
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {"b": "a"})
        e = panel_env(self)
        chain_session(e, "a", 300, "done", intent="~/notes/PROMPT-NEW-SESSION.md")
        chain_session(e, "b", 100, "done", intent="~/notes/HANDOFF-2.md")
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"], {"b": "a"})

    def test_numbered_copies_form_a_chain(self):
        self.sess("x1", 300, name="design system lead setup")
        self.sess("x2", 200, name="Design System Lead Setup  (2)")
        self.sess("x3", 100, name="design system lead setup (3)")
        self.sess("y2", 150, name="Одиночка (2)")  # оригинала «Одиночка» нет
        self.assertEqual(self.links(), {"x2": "x1", "x3": "x2"})
        self.assertEqual(self.e.bot.chain_k("x3"), 3)

    def test_numbered_copy_falls_back_to_nearest_earlier_number(self):
        self.sess("x1", 300, name="api product track handoff")
        self.sess("x3", 100, name="api product track handoff (3)")  # (2) нет -> оригинал
        self.sess("z2", 300, name="zz (2)")
        self.sess("z4", 100, name="zz (4)")  # (3) нет -> ближайший более ранний (2)
        self.assertEqual(self.links(), {"x3": "x1", "z4": "z2"})

    def test_original_name_from_store_survives_renaming(self):
        self.sess("n1", 300, name="Сайт: бэкенд")  # уже переименована ботом
        self.sess("n2", 100, name="Сайт: бэкенд №2")
        names = self.e.bot.store.data["names"]
        names["n1"] = {"source_name": "design system lead setup (2)", "name": "Сайт: бэкенд", "approved": True}
        names["n2"] = {"source_name": "design system lead setup (3)", "name": "Сайт: бэкенд", "approved": True}
        self.sess("n0", 400, name="design system lead setup")
        self.assertEqual(self.links(), {"n1": "n0", "n2": "n1"})

    def test_priority_writer_over_copy_over_folder(self):
        ho = "/var/tmp/line/HANDOFF-1.md"
        # писатель важнее и копии, и папки
        self.sess("w", 400, name="Писатель", transcript=write_line(ho, iso(BASE - 390 * 60)))
        self.sess("f", 300, name="Папочный", intent="x /var/tmp/line/NEW-SESSION-PROMPT.md")
        self.sess("c0", 250, name="Doc")
        self.sess("child", 100, name="Doc (2)", intent="Продолжи по " + ho)
        links = self.links()
        self.assertEqual(links["child"], "w")  # не c0 (копия) и не f (ближайшая в папке)
        # копия важнее папки
        e = panel_env(self)
        chain_session(e, "o", 400, "done", name="Doc")
        chain_session(e, "m", 300, "done", name="Другое", intent="/var/tmp/line2/HANDOFF-1.md")
        chain_session(e, "k", 100, "done", name="Doc (2)", intent="/var/tmp/line2/HANDOFF-2.md")
        e.bot.chains_scan()
        self.assertEqual(e.bot.store.data["chain_links"]["k"], "o")
        self.assertEqual(e.bot.store.data["chain_links"].get("m"), None)

    def test_new_session_prompt_written_by_parent_counts_as_writer(self):
        np = "/var/tmp/zt-plan/NEW-SESSION-PROMPT.md"
        self.sess("par", 300, name="Родитель", transcript=write_line(np, iso(BASE - 280 * 60)))
        self.sess("mid", 250, name="Середина", intent="Прочти " + np)
        self.assertEqual(self.links(), {"mid": "par"})
        self.assertIn(os.path.normpath(np), self.e.bot.store.data["chain_scan"]["par"]["writes"])

    def test_no_cycles(self):
        # P («Z (2)», старше) записал передачу, по которой стартовал Q («Z», младше): писатель Q->P, копия P->Q невозможна
        ho = "/var/tmp/cyc/HANDOFF-1.md"
        self.sess("P", 300, name="Z (2)", transcript=write_line(ho, iso(BASE - 290 * 60)))
        self.sess("Q", 100, name="Z", intent=ho)
        links = self.links()
        self.assertEqual(links, {"Q": "P"})
        self.assertEqual(self.e.bot.chain_k("Q"), 2)
        # защита как функция
        self.assertTrue(cc.Bot.chain_would_cycle({"b": "a", "c": "b"}, "a", "c"))
        self.assertTrue(cc.Bot.chain_would_cycle({}, "a", "a"))
        self.assertFalse(cc.Bot.chain_would_cycle({"b": "a"}, "c", "b"))
        # цикл «по кругу» из нескольких правил не появляется
        e = panel_env(self)
        for i, (sid, mins) in enumerate((("a", 300), ("b", 200), ("c", 100))):
            chain_session(e, sid, mins, "done", name="Loop (%d)" % (i + 1), intent="/var/tmp/loop/HANDOFF-%d.md" % i)
        e.bot.chains_scan()
        for sid in "abc":
            self.assertLessEqual(e.bot.chain_k(sid), 3)

    def test_manual_marks_stay_stronger(self):
        self.sess("x1", 300, name="Book")
        self.sess("x2", 100, name="Book (2)")
        self.links()
        self.assertTrue(self.e.bot.is_old("x1") and not self.e.bot.is_old("x2"))
        self.e.bot.store.data["chain_manual"] = {"x1": "current", "x2": "old"}
        self.e.bot.chains_scan()
        self.assertFalse(self.e.bot.is_old("x1"))
        self.assertTrue(self.e.bot.is_old("x2"))
        self.assertEqual(self.e.bot.store.data["chain_manual"], {"x1": "current", "x2": "old"})

    def test_chain_scan_is_rebuilt_once_after_rule_change(self):
        self.sess("a", 100, name="A")
        self.e.bot.store.data["chain_scan"] = {"a": {"offset": 999999, "writes": {"/old": 1}}}
        self.e.bot.chains_scan()
        self.assertEqual(self.e.bot.store.data["chain_scan_ver"], 2)
        self.assertNotIn("/old", self.e.bot.store.data["chain_scan"]["a"]["writes"])
        self.e.bot.store.data["chain_scan"]["a"]["writes"]["/keep"] = 5
        self.e.bot.chains_scan()
        self.assertIn("/keep", self.e.bot.store.data["chain_scan"]["a"]["writes"])  # повторно не сбрасывается


# ---------------------------------------------------------------- PWA: вход, устройства, Web Push

from cryptography.hazmat.primitives import hashes as _hashes, serialization as _ser
from cryptography.hazmat.primitives.asymmetric import ec as _ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature as _enc_sig
from cryptography.hazmat.primitives.ciphers.aead import AESGCM as _AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF as _HKDF


def _b64d(t):
    return base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))


def rfc8291_decrypt(body, ua_private, auth_secret):
    """Расшифровка по RFC 8291 §4 — независимо от кода бота."""
    salt, rs, idlen = body[:16], struct.unpack(">I", body[16:20])[0], body[20]
    as_public, ct = body[21:21 + idlen], body[21 + idlen:]
    assert rs == 4096 and idlen == 65
    ua_public = ua_private.public_key().public_bytes(_ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)
    shared = ua_private.exchange(_ec.ECDH(), _ec.EllipticCurvePublicKey.from_encoded_point(_ec.SECP256R1(), as_public))
    ikm = _HKDF(algorithm=_hashes.SHA256(), length=32, salt=auth_secret,
                info=b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = _HKDF(algorithm=_hashes.SHA256(), length=16, salt=salt, info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = _HKDF(algorithm=_hashes.SHA256(), length=12, salt=salt, info=b"Content-Encoding: nonce\x00").derive(ikm)
    plain = _AESGCM(cek).decrypt(nonce, ct, b"")
    assert plain.endswith(b"\x02")
    return plain[:-1]


class PushCryptoTests(unittest.TestCase):
    def test_rfc8291_appendix_a_vector(self):
        # тестовый вектор RFC 8291, приложение A
        as_priv = _ec.derive_private_key(int.from_bytes(_b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                                         _ec.SECP256R1())
        body = cc.webpush_encrypt(b"When I grow up, I want to be a watermelon",
                                  "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
                                  "BTBZMqHH6r4Tts7J_aSIgg", salt=_b64d("DGv6ra1nlYgDCS1FRnbzlw"), as_private=as_priv)
        self.assertEqual(cc.b64u(body),
                         "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6Tlz"
                         "AC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

    def test_roundtrip_with_random_keys_and_header_layout(self):
        ua = _ec.generate_private_key(_ec.SECP256R1())
        auth = os.urandom(16)
        pub = ua.public_key().public_bytes(_ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)
        payload = json.dumps({"title": "Альфа", "body": "ждёт вас"}, ensure_ascii=False).encode()
        body = cc.webpush_encrypt(payload, cc.b64u(pub), cc.b64u(auth))
        self.assertEqual(struct.unpack(">I", body[16:20])[0], 4096)
        self.assertEqual(body[20], 65)
        self.assertEqual(body[21], 4)  # несжатая точка
        self.assertEqual(rfc8291_decrypt(body, ua, auth), payload)
        self.assertNotEqual(cc.webpush_encrypt(payload, cc.b64u(pub), cc.b64u(auth)), body)  # новая соль/ключ каждый раз

    def test_vapid_key_file_and_jwt(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cfg", "vapid.pem")
            key = cc.load_vapid_key(path)
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
            self.assertEqual(cc.vapid_public_key(cc.load_vapid_key(path)), cc.vapid_public_key(key))  # ключ создаётся один раз
            pub = cc.b64u_dec(cc.vapid_public_key(key))
            self.assertEqual((len(pub), pub[0]), (65, 4))
            jwt = cc.vapid_jwt(key, "https://push.example.org", 1_800_000_000, "mailto:admin@example.com")
            h, c, sig = jwt.split(".")
            self.assertEqual(json.loads(_b64d(h)), {"typ": "JWT", "alg": "ES256"})
            claims = json.loads(_b64d(c))
            self.assertEqual(claims, {"aud": "https://push.example.org", "exp": 1_800_000_000 + 12 * 3600,
                                      "sub": "mailto:admin@example.com"})
            raw = _b64d(sig)
            self.assertEqual(len(raw), 64)
            key.public_key().verify(_enc_sig(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                                    ("%s.%s" % (h, c)).encode(), _ec.ECDSA(_hashes.SHA256()))
            with self.assertRaises(Exception):
                key.public_key().verify(_enc_sig(1, 2), ("%s.%s" % (h, c)).encode(), _ec.ECDSA(_hashes.SHA256()))


def device_login(w, e, ip_header=None, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)"):
    token = e.bot.new_login_token(1)
    h = {"User-Agent": ua}
    if ip_header:
        h["Cf-Connecting-Ip"] = ip_header
    st, obj, _ = w.req("POST", "/api/login", {"token": token}, headers=h, auth=False)
    return st, obj


class PwaUrlTests(unittest.TestCase):
    def test_pwa_command_uses_current_url_file_and_env_has_priority(self):
        e = names_env(self)
        e.cfg.webapp_url_env = ""
        e.cfg.tunnel_file = os.path.join(e.tmp.name, "tunnel-url")
        for url in ("https://first.example.org/", "https://second.example.org/"):
            with open(e.cfg.tunnel_file, "w") as f:
                f.write(url + "\n")
            e.bot.handle_update(e.msg("/pwa"))
            m = e.tg.of("sendMessage")[-1]
            link = m["reply_markup"]["inline_keyboard"][0][0]["url"]
            self.assertTrue(link.startswith(url.rstrip("/") + "/app/#login="))
            token = link.split("#login=")[1]
            st, obj = e.bot.api_login("127.0.0.1", token)
            self.assertEqual((st, obj["ok"]), (200, True))
        e.cfg.webapp_url_env = "https://env.example.org/"
        e.bot.handle_update(e.msg("/pwa"))
        link = e.tg.of("sendMessage")[-1]["reply_markup"]["inline_keyboard"][0][0]["url"]
        self.assertTrue(link.startswith("https://env.example.org/app/#login="))
        e.cfg.webapp_url_env = ""
        os.remove(e.cfg.tunnel_file)
        logins_before = dict(e.bot.store.data["logins"])
        e.bot.handle_update(e.msg("/pwa"))
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "PWA ещё не настроено.")
        self.assertEqual(e.bot.store.data["logins"], logins_before)


class PwaLoginTests(unittest.TestCase):
    def setUp(self):
        self.w = WebEnv(self)
        self.e = self.w.e
        self.e.cfg.webapp_url_env = "https://cc.example.com"

    def test_pwa_command_message(self):
        e = self.e
        e.bot.handle_update(e.msg("/pwa"))
        m = e.tg.of("sendMessage")[-1]
        token = m["reply_markup"]["inline_keyboard"][0][0]["url"].split("#login=")[1]
        self.assertEqual(m["reply_markup"]["inline_keyboard"][0][0]["url"], "https://cc.example.com/app/#login=" + token)
        self.assertIn("\n\n" + token + "\n\n", m["text"])  # код отдельной строкой — вставить в приложении (iPhone)
        self.assertIn("«Поделиться» → «На экран Домой»", m["text"])
        self.assertIn("https://cc.example.com/app/", m["text"])
        self.assertNotIn(token, json.dumps(e.bot.store.data))  # в store только хэш
        self.assertEqual(len(token), 43)
        # без URL — честный ответ, токен не создаётся
        e2 = WebEnv(self).e
        e2.bot.handle_update(e2.msg("/pwa"))
        self.assertEqual(e2.tg.of("sendMessage")[-1]["text"], "PWA ещё не настроено.")
        self.assertEqual(e2.bot.store.data["logins"], {})

    def test_pwa_button_in_help_mode(self):
        e = self.e
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("pm:help"))
        flat = [(b["text"], b["callback_data"]) for r in e.tg.of("editMessageText")[-1]["reply_markup"]["inline_keyboard"]
                for b in r]
        self.assertIn(("📱 Приложение", "pwa"), flat)
        e.bot.handle_update(e.cb("pwa"))
        self.assertIn("#login=", e.tg.of("sendMessage")[-1]["reply_markup"]["inline_keyboard"][0][0]["url"])

    def test_login_success_single_use_and_hash_only_storage(self):
        e = self.e
        st, obj = device_login(self.w, e)
        self.assertEqual(st, 200)
        dev = obj["device_token"]
        self.assertTrue(obj["ok"] and len(dev) == 43)
        rec = e.bot.store.data["devices"][cc.sha256_hex(dev)]
        self.assertEqual((rec["uid"], rec["label"]), (1, "iPhone"))
        self.assertLessEqual(len(rec["ua"]), 120)
        self.assertNotIn(dev, json.dumps(e.bot.store.data))
        # повторное использование токена и неверный токен
        token = e.bot.new_login_token(1)
        self.assertEqual(self.w.req("POST", "/api/login", {"token": token}, auth=False)[0], 200)
        st, obj, _ = self.w.req("POST", "/api/login", {"token": token}, auth=False)
        self.assertEqual((st, obj), (401, {"error": "login"}))
        self.assertEqual(self.w.req("POST", "/api/login", {"token": "nope"}, auth=False)[1], {"error": "login"})
        self.assertEqual(self.w.req("POST", "/api/login", {}, auth=False)[0], 401)

    def test_login_expired_and_rate_limit(self):
        e = self.e
        token = e.bot.new_login_token(1)
        e.t["v"] += 601
        self.assertEqual(self.w.req("POST", "/api/login", {"token": token}, auth=False)[0], 401)
        for _ in range(4):  # с просроченным это 5 попыток в минуту с одного адреса, шестая — отказ
            self.assertEqual(self.w.req("POST", "/api/login", {"token": "x"}, auth=False)[0], 401)
        good = e.bot.new_login_token(1)
        st, obj, _ = self.w.req("POST", "/api/login", {"token": good}, auth=False)
        self.assertEqual(st, 429)
        self.assertEqual(obj, {"error": "rate"})
        # другой IP (Cf-Connecting-Ip) считается отдельно, а одноразовый токен при отказе не сгорел
        st, obj, _ = self.w.req("POST", "/api/login", {"token": good}, headers={"Cf-Connecting-Ip": "9.9.9.9"},
                                auth=False)
        self.assertEqual(st, 200)
        e.t["v"] += 61  # окно в минуту прошло
        self.assertEqual(self.w.req("POST", "/api/login", {"token": "x"}, auth=False)[0], 401)

    def test_device_token_authorizes_api(self):
        e = self.e
        mk(e, "s1", "done", 1)
        e.bot.list_sessions(True)
        dev = device_login(self.w, e)[1]["device_token"]
        h = {"X-Device-Token": dev}
        st, obj, _ = self.w.req("GET", "/api/sessions", headers=h, auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(obj["sessions"][0]["id"], "s1")
        self.assertEqual(self.w.req("GET", "/api/sessions", headers={"X-Device-Token": "stranger"}, auth=False)[0], 401)
        self.assertEqual(self.w.req("GET", "/api/sessions", auth=False)[0], 401)
        # last_seen обновляется не чаще раза в 5 минут
        key = cc.sha256_hex(dev)
        seen = e.bot.store.data["devices"][key]["last_seen"]
        e.t["v"] += 100
        self.w.req("GET", "/api/sessions", headers=h, auth=False)
        self.assertEqual(e.bot.store.data["devices"][key]["last_seen"], seen)
        e.t["v"] += 300
        self.w.req("GET", "/api/sessions", headers=h, auth=False)
        self.assertEqual(e.bot.store.data["devices"][key]["last_seen"], e.t["v"])
        # отозванный, просроченный (скользящие 90 дней) и устройство чужого пользователя
        e.t["v"] += 89 * 86400
        self.assertEqual(self.w.req("GET", "/api/sessions", headers=h, auth=False)[0], 200)  # окно скользит
        e.t["v"] += 91 * 86400
        self.assertEqual(self.w.req("GET", "/api/sessions", headers=h, auth=False)[0], 401)
        self.assertNotIn(key, e.bot.store.data["devices"])
        dev2 = device_login(self.w, e)[1]["device_token"]
        e.bot.store.data["devices"][cc.sha256_hex(dev2)]["uid"] = 999  # uid вне allowed
        self.assertEqual(self.w.req("GET", "/api/sessions", headers={"X-Device-Token": dev2}, auth=False)[0], 401)

    def test_devices_command_and_revocation(self):
        e = self.e
        d1 = device_login(self.w, e, ua="Mozilla/5.0 (Android 14)")[1]["device_token"]
        d2 = device_login(self.w, e, ua="Mozilla/5.0 (iPhone)")[1]["device_token"]
        for d, ep in ((d1, "https://push.example/a"), (d2, "https://push.example/b")):
            e.bot.store.data["push"][ep] = {"uid": 1, "device_hash": cc.sha256_hex(d), "p256dh": "x", "auth": "y",
                                            "created": 1, "fails": 0}
        e.bot.handle_update(e.msg("/devices"))
        m = e.tg.of("sendMessage")[-1]
        self.assertIn("Android", m["text"])
        self.assertIn("iPhone", m["text"])
        btns = [(b["text"], b["callback_data"]) for r in m["reply_markup"]["inline_keyboard"] for b in r]
        self.assertEqual(btns[-1], ("Выйти везде", "dvall"))
        cb = [c for t, c in btns if t.startswith("Отключить: Android")][0]
        self.assertLessEqual(len(cb.encode()), 64)
        e.bot.handle_update(e.cb(cb, mid=44))
        self.assertNotIn(cc.sha256_hex(d1), e.bot.store.data["devices"])
        self.assertEqual(list(e.bot.store.data["push"]), ["https://push.example/b"])  # подписка ушла вместе с устройством
        self.assertEqual(self.w.req("GET", "/api/sessions", headers={"X-Device-Token": d1}, auth=False)[0], 401)
        self.assertEqual(self.w.req("GET", "/api/sessions", headers={"X-Device-Token": d2}, auth=False)[0], 200)
        e.bot.handle_update(e.cb("dvall", mid=44))
        self.assertEqual((e.bot.store.data["devices"], e.bot.store.data["push"]), ({}, {}))
        self.assertEqual(self.w.req("GET", "/api/sessions", headers={"X-Device-Token": d2}, auth=False)[0], 401)
        e.bot.handle_update(e.msg("/devices"))
        self.assertIn("Устройств нет", e.tg.of("sendMessage")[-1]["text"])


class PwaStaticTests(unittest.TestCase):
    ASSETS = os.path.join(os.path.dirname(os.path.abspath(cc.__file__)), "assets", "webapp")

    def server(self):
        w = WebEnv(self)
        w.srv.static_dir = self.ASSETS
        return w

    def test_manifest_content_and_headers(self):
        w = self.server()
        st, obj, r = w.req("GET", "/app/manifest.webmanifest", auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(r.getheader("Content-Type"), "application/manifest+json")
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        self.assertEqual((obj["name"], obj["short_name"]), ("Claude Sessions", "Sessions"))
        self.assertEqual((obj["start_url"], obj["scope"], obj["display"]), ("/app/", "/app/", "standalone"))
        self.assertEqual((obj["background_color"], obj["theme_color"]), ("#f1f2f5", "#111214"))
        self.assertEqual([(i["src"], i["sizes"], i["purpose"]) for i in obj["icons"]],
                         [("/app/icon-192.png", "192x192", "any"), ("/app/icon-512.png", "512x512", "any"),
                          ("/app/icon-maskable-512.png", "512x512", "maskable")])

    def test_service_worker_headers_and_cache_rules(self):
        w = self.server()
        st, body, r = w.req("GET", "/app/sw.js", auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(r.getheader("Service-Worker-Allowed"), "/app/")
        self.assertEqual(r.getheader("Cache-Control"), "no-cache")
        self.assertTrue(r.getheader("Content-Type").startswith("application/javascript"))
        js = body.decode()
        # оболочка кэшируется, данные и файлы — никогда
        shell = js[js.index("const SHELL"):js.index("];", js.index("const SHELL"))]
        for want in ("/app/'", "/app/index.html", "manifest.webmanifest", "icon-192.png", "icon-512.png",
                     "icon-maskable-512.png"):
            self.assertIn(want, shell)
        self.assertNotIn("/api", shell)
        self.assertIn("url.pathname.startsWith('/api/')", js)
        self.assertIn("url.pathname.startsWith('/f/')", js)
        self.assertLess(js.index("startsWith('/api/')"), js.index("respondWith"))  # исключение раньше кэширования
        self.assertIn("SHELL.includes(url.pathname)", js)
        self.assertIn("fetch(req)", js)  # network-first
        self.assertIn("caches.match", js)  # откат на кэш
        for want in ("addEventListener('push'", "addEventListener('notificationclick'", "showNotification",
                     "data.url", "openWindow", "focus()"):
            self.assertIn(want, js)
        self.assertEqual(w.req("GET", "/app/manifest.webmanifest", auth=False)[2].getheader("Service-Worker-Allowed"),
                         None)

    def test_png_served_as_image(self):
        w = WebEnv(self)
        with open(os.path.join(w.static, "icon-192.png"), "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n")
        st, body, r = w.req("GET", "/app/icon-192.png", auth=False)
        self.assertEqual((st, r.getheader("Content-Type")), (200, "image/png"))


def push_env(tc, sub_count=1):
    """Окружение с подпиской: push_post подменён, тело расшифровывается ключом получателя."""
    w = WebEnv(tc)
    e = w.e
    e.cfg.vapid_file = os.path.join(e.tmp.name, "vapid", "vapid.pem")
    e.sent_pushes = []
    e.push_status = {}
    e.subs = []
    for i in range(sub_count):
        priv = _ec.generate_private_key(_ec.SECP256R1())
        auth = os.urandom(16)
        ep = "https://fcm.googleapis.com/fcm/send/%d" % i
        e.bot.store.data["push"][ep] = {
            "uid": 1, "device_hash": "dev%d" % i, "created": 1, "fails": 0, "auth": cc.b64u(auth),
            "p256dh": cc.b64u(priv.public_key().public_bytes(_ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint))}
        e.subs.append((ep, priv, auth))

    def post(endpoint, headers, data):
        e.sent_pushes.append((endpoint, headers, data))
        return e.push_status.get(endpoint, 201)
    e.bot.push_post = post
    return w, e


def decode_push(e, i=-1):
    ep, headers, data = e.sent_pushes[i]
    sub = [s for s in e.subs if s[0] == ep][0]
    return json.loads(rfc8291_decrypt(data, sub[1], sub[2]).decode()), headers


class PushSendTests(unittest.TestCase):
    def test_headers_vapid_jwt_and_decryptable_payload(self):
        w, e = push_env(self)
        mk(e, "s1", "blocked", 1, name="Альфа")
        e.bot.list_sessions(True)
        self.assertTrue(e.bot.push_notify(1, "s1", "ждёт вас: нужен ответ", "high"))
        payload, h = decode_push(e)
        self.assertEqual(payload, {"title": "Альфа", "body": "ждёт вас: нужен ответ", "tag": "s1",
                                   "data": {"url": "/app/#s=s1"}})
        self.assertEqual((h["TTL"], h["Urgency"], h["Content-Encoding"], h["Content-Type"]),
                         ("3600", "high", "aes128gcm", "application/octet-stream"))
        m = re.match(r"vapid t=([\w.-]+), k=([\w-]+)$", h["Authorization"])
        self.assertTrue(m, h["Authorization"])
        jwt, k = m.groups()
        self.assertEqual(k, cc.vapid_public_key(e.bot.vapid_key()))
        hh, cc_, sig = jwt.split(".")
        claims = json.loads(_b64d(cc_))
        self.assertEqual((claims["aud"], claims["sub"]), ("https://fcm.googleapis.com", "mailto:admin@example.com"))
        self.assertEqual(claims["exp"], int(e.bot.clock()) + 12 * 3600)
        pub = _ec.EllipticCurvePublicKey.from_encoded_point(_ec.SECP256R1(), _b64d(k))
        raw = _b64d(sig)
        pub.verify(_enc_sig(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                   ("%s.%s" % (hh, cc_)).encode(), _ec.ECDSA(_hashes.SHA256()))

    def test_gone_and_error_statuses(self):
        w, e = push_env(self, sub_count=3)
        mk(e, "s1", "blocked", 1)
        e.bot.list_sessions(True)
        e.push_status = {e.subs[0][0]: 410, e.subs[1][0]: 500}
        self.assertTrue(e.bot.push_notify(1, "s1", "x"))  # третья подписка приняла
        push = e.bot.store.data["push"]
        self.assertNotIn(e.subs[0][0], push)  # 410 -> удалена
        self.assertEqual(push[e.subs[1][0]]["fails"], 1)  # прочее -> fails+1
        e.push_status = {e.subs[1][0]: 500}
        for _ in range(4):
            e.bot.push_notify(1, "s1", "x")
        self.assertNotIn(e.subs[1][0], e.bot.store.data["push"])  # после 5 неудач — удалена
        e.push_status = {e.subs[2][0]: 404}
        self.assertFalse(e.bot.push_notify(1, "s1", "x"))
        self.assertEqual(e.bot.store.data["push"], {})

    def test_body_truncated_and_no_subscription(self):
        w, e = push_env(self)
        mk(e, "s1", "blocked", 1)
        e.bot.list_sessions(True)
        e.bot.push_notify(1, "s1", "я" * 500)
        self.assertLessEqual(len(decode_push(e)[0]["body"]), 120)
        w2, e2 = push_env(self, sub_count=0)
        self.assertFalse(e2.bot.push_notify(1, "s1", "x"))
        self.assertEqual(e2.sent_pushes, [])

    def test_subscribe_api_requires_device_token(self):
        w = WebEnv(self)
        e = w.e
        e.cfg.vapid_file = os.path.join(e.tmp.name, "vapid", "vapid.pem")
        priv = _ec.generate_private_key(_ec.SECP256R1())
        sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/x", "keys": {
            "p256dh": cc.b64u(priv.public_key().public_bytes(_ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)),
            "auth": cc.b64u(os.urandom(16))}}
        st, obj, _ = w.req("GET", "/api/push/key")
        self.assertEqual(st, 200)
        self.assertEqual(len(cc.b64u_dec(obj["key"])), 65)
        self.assertEqual(oct(os.stat(e.cfg.vapid_file).st_mode & 0o777), "0o600")
        self.assertEqual(w.req("POST", "/api/push/subscribe", sub)[0], 403)  # initData без устройства
        dev = device_login(w, e)[1]["device_token"]
        h = {"X-Device-Token": dev}
        st, obj, _ = w.req("POST", "/api/push/subscribe", sub, headers=h, auth=False)
        self.assertEqual((st, obj), (200, {"ok": True}))
        rec = e.bot.store.data["push"][sub["endpoint"]]
        self.assertEqual((rec["uid"], rec["device_hash"], rec["fails"]), (1, cc.sha256_hex(dev), 0))
        bad = dict(sub, endpoint="http://fcm.googleapis.com/x")
        self.assertEqual(w.req("POST", "/api/push/subscribe", bad, headers=h, auth=False)[0], 400)
        bad = dict(sub, keys={"p256dh": "AAAA", "auth": "BBBB"})
        self.assertEqual(w.req("POST", "/api/push/subscribe", bad, headers=h, auth=False)[0], 400)
        st, obj, _ = w.req("POST", "/api/push/unsubscribe", {"endpoint": sub["endpoint"]}, headers=h, auth=False)
        self.assertEqual(st, 200)
        self.assertEqual(e.bot.store.data["push"], {})


class PushRoutingTests(unittest.TestCase):
    def setup_watched(self, **kw):
        w, e = push_env(self, **kw)
        mk(e, "s1", "working", 1, name="Альфа")
        e.bot.list_sessions(True)
        e.bot.set_watch(1, "s1", True)
        e.bot.notify_tick()
        return w, e

    def set_state(self, e, state, detail="", result=None):
        e.job("s1", name="Альфа", state=state, detail=detail, cwd="/tmp", updatedAt=iso(BASE - 30),
              **({"output": {"result": result}} if result else {}))
        e.agents[0]["state"] = state

    def test_transition_goes_to_push_not_telegram(self):
        w, e = self.setup_watched()
        n = len(e.tg.of("sendMessage"))
        self.set_state(e, "blocked", "нужен ответ")
        e.bot.notify_tick()
        payload, h = decode_push(e)
        self.assertEqual((payload["title"], payload["body"], payload["tag"], h["Urgency"]),
                         ("Альфа", "ждёт вас: нужен ответ", "s1", "high"))
        self.assertEqual(len(e.tg.of("sendMessage")), n)
        self.set_state(e, "done", "x", result="СЕКРЕТНЫЙ текст ответа модели")
        e.bot.notify_tick()
        payload, h = decode_push(e)
        self.assertEqual((payload["body"], h["Urgency"]), ("готово", "normal"))
        self.assertNotIn("СЕКРЕТНЫЙ", json.dumps(payload, ensure_ascii=False))  # текстов модели в push нет
        self.set_state(e, "failed", "упала")
        e.bot.notify_tick()
        self.assertEqual(decode_push(e)[0]["body"], "ошибка: упала")
        self.assertEqual(len(e.tg.of("sendMessage")), n)

    def test_fallback_to_telegram_without_or_with_failed_subscriptions(self):
        w, e = self.setup_watched(sub_count=0)
        self.set_state(e, "blocked", "нужен ответ")
        e.bot.notify_tick()
        self.assertTrue(e.tg.of("sendMessage")[-1]["text"].startswith("🟡 Альфа ждёт"))
        w, e = self.setup_watched()
        e.push_status = {e.subs[0][0]: 500}
        self.set_state(e, "blocked", "нужен ответ")
        e.bot.notify_tick()  # push не ушёл -> Telegram
        self.assertEqual(len(e.sent_pushes), 1)
        self.assertTrue(e.tg.of("sendMessage")[-1]["text"].startswith("🟡 Альфа ждёт"))
        self.assertEqual(e.bot.store.data["push"][e.subs[0][0]]["fails"], 1)
        # у единственной подписки fails>0 — до восстановления идём в Telegram без push
        e.push_status = {}
        self.set_state(e, "failed", "упала")
        n = len(e.sent_pushes)
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n)
        self.assertTrue(e.tg.of("sendMessage")[-1]["text"].startswith("🔴 Альфа упала"))

    def test_gone_subscription_falls_back_to_telegram(self):
        w, e = self.setup_watched()
        e.push_status = {e.subs[0][0]: 410}
        self.set_state(e, "blocked", "нужен ответ")
        e.bot.notify_tick()
        self.assertEqual(e.bot.store.data["push"], {})
        self.assertTrue(e.tg.of("sendMessage")[-1]["text"].startswith("🟡 Альфа ждёт"))

    def test_question_goes_to_push_once(self):
        w, e = self.setup_watched()
        qs = cc.normalize_questions({"questions": [Q_SIZE]})
        n = len(e.tg.of("sendMessage"))
        e.bot.on_ask(1, "s1", {"tool_id": "toolu_5", "questions": qs})
        self.assertEqual(decode_push(e)[0]["body"], "вопрос: Размер?")
        self.assertEqual(len(e.tg.of("sendMessage")), n)  # сообщения-вопроса в чате нет
        e.bot.on_ask(1, "s1", {"tool_id": "toolu_5", "questions": qs})
        self.assertEqual(len(e.sent_pushes), 1)  # повторно не уведомляем
        self.assertEqual(w.req("GET", "/api/session/s1")[1]["question"]["tool_id"], "toolu_5")  # доступен в приложении

    def test_question_falls_back_when_push_fails_or_session_not_watched(self):
        w, e = self.setup_watched()
        e.push_status = {e.subs[0][0]: 500}
        qs = cc.normalize_questions({"questions": [Q_SIZE]})
        e.bot.on_ask(1, "s1", {"tool_id": "toolu_6", "questions": qs})
        self.assertTrue([m for m in e.tg.of("sendMessage") if "❓" in m["text"]])
        w2, e2 = push_env(self)
        mk(e2, "s2", "working", 1)
        e2.bot.list_sessions(True)  # без подписки на уведомления push не используется
        e2.bot.on_ask(1, "s2", {"tool_id": "toolu_7", "questions": qs})
        self.assertEqual(e2.sent_pushes, [])
        self.assertTrue([m for m in e2.tg.of("sendMessage") if "❓" in m["text"]])

    def test_suppression_after_webapp_action_covers_both_channels(self):
        w, e = self.setup_watched()
        w.req("POST", "/api/session/s1/watch", {"on": True})  # действие из приложения
        n = len(e.tg.of("sendMessage"))
        self.set_state(e, "blocked", "нужен ответ")
        e.bot.notify_tick()
        self.assertEqual((e.sent_pushes, len(e.tg.of("sendMessage"))), ([], n))
        e.t["v"] += 601
        self.set_state(e, "failed", "упала")
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), 1)


# ---------------------------------------------------------------- навигатор по сабтаскам

def sub_line(agent, typ, content, ts, uuid, sidechain=True):
    return line({"parentUuid": None, "isSidechain": sidechain, "agentId": agent, "type": typ, "uuid": uuid,
                 "timestamp": ts, "message": {"role": typ, "content": content}})


def sub_assistant(agent, text, ts, uuid, tools=1):
    blocks = [{"type": "text", "text": text}] if text else []
    blocks += [{"type": "tool_use", "id": "t%s%d" % (uuid, i), "name": "Read", "input": {}} for i in range(tools)]
    return sub_line(agent, "assistant", blocks, ts, uuid)


def parent_agent_call(tool_id, ts, desc="задача"):
    return line({"type": "assistant", "timestamp": ts, "uuid": "p" + tool_id, "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Agent", "input": {"description": desc, "prompt": "сделай"}}]}})


def parent_result(tool_id, ts):
    return line({"type": "user", "timestamp": ts, "uuid": "r" + tool_id, "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}})


def parent_note(agent, status, ts, kind="task-notification"):
    return line({"type": "user", "timestamp": ts, "uuid": "n%s%s" % (agent, ts), "origin": {"kind": kind},
                 "message": {"content": "<task-notification>\n<task-id>%s</task-id>\n<status>%s</status>\n"
                                        "</task-notification>" % (agent, status)}})


def make_subagent(folder, agent, meta, lines, mtime):
    path = os.path.join(folder, "agent-%s.jsonl" % agent)
    with open(path, "wb") as f:
        f.write(b"".join(lines))
    with open(os.path.join(folder, "agent-%s.meta.json" % agent), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.utime(path, (mtime, mtime))
    return path


class SubagentTests(unittest.TestCase):
    PROMPT = "Собери отчёт по бэкенду. " * 5

    def setUp(self):
        self.w = WebEnv(self)
        e = self.e = self.w.e
        t = lambda sec: iso(BASE - sec)
        self.proj = os.path.join(e.tmp.name, "proj")
        os.makedirs(os.path.join(self.proj, "sess1", "subagents"))
        self.folder = os.path.join(self.proj, "sess1", "subagents")
        parent = b"".join([
            parent_agent_call("toolu_A", t(120)), parent_result("toolu_A", t(50)),                 # A: обычный, завершён
            parent_agent_call("toolu_B", t(520)), parent_result("toolu_B", t(495)),                # B: фоновый, «запущен» не завершение
            parent_note("bbbb2222", "completed", t(480)), parent_note("bbbb2222", "completed", t(300 - 10)),
            parent_note("bbbb2222", "completed", t(289), "queue"),                                # дубль доставки (до 60 с)
            parent_agent_call("toolu_C", t(40)), parent_result("toolu_C", t(39)),                  # C: фоновый, без завершения
            parent_agent_call("toolu_D", t(500)), parent_note("dddd4444", "killed", t(400)),       # D: убит
            parent_agent_call("toolu_E", t(9000)),                                                 # E: оборван давно
            parent_note("zzzz9999", "completed", t(10)),                                           # чужой агент — не наш
        ])
        self.tr = os.path.join(self.proj, "sess1.jsonl")
        with open(self.tr, "wb") as f:
            f.write(parent)
        os.utime(self.tr, (BASE - 5, BASE - 5))
        make_subagent(self.folder, "aaaa1111",
                      {"agentType": "general-purpose", "description": "Построить бота", "toolUseId": "toolu_A",
                       "spawnDepth": 1, "requestShape": "foreground", "model": "sonnet"},
                      [sub_line("aaaa1111", "user", self.PROMPT, t(115), "u1"),
                       sub_assistant("aaaa1111", "Читаю файлы", t(110), "a1"),
                       sub_line("aaaa1111", "user", [{"type": "tool_result", "tool_use_id": "ta1", "content": "x"}],
                                t(100), "u2"),
                       sub_assistant("aaaa1111", "Готов отчёт по задаче " + "я" * 300, t(60), "a2", tools=2)], BASE - 30)
        make_subagent(self.folder, "bbbb2222",
                      {"agentType": "general-purpose", "description": "Второй ревьюер", "toolUseId": "toolu_B",
                       "requestShape": "background", "model": "opus"},
                      [sub_line("bbbb2222", "user", "Проверь код", t(515), "u1"),
                       sub_assistant("bbbb2222", "Первый проход", t(490), "a1"),
                       sub_assistant("bbbb2222", "Второй проход после возобновления", t(300), "a2")], BASE - 250)
        make_subagent(self.folder, "cccc3333",
                      {"agentType": "Explore", "description": "Поиск", "toolUseId": "toolu_C",
                       "requestShape": "background", "model": "haiku"},
                      [sub_line("cccc3333", "user", "Найди файлы", t(38), "u1"),
                       sub_assistant("cccc3333", "Ищу", t(12), "a1", tools=3)], BASE - 10)
        make_subagent(self.folder, "dddd4444",
                      {"agentType": "x", "description": "Упавший", "toolUseId": "toolu_D", "requestShape": "background"},
                      [sub_line("dddd4444", "user", "Задача", t(499), "u1"),
                       sub_assistant("dddd4444", "", t(420), "a1")], BASE - 410)
        make_subagent(self.folder, "eeee5555",
                      {"agentType": "x", "description": "Забытый", "toolUseId": "toolu_E"},
                      [sub_line("eeee5555", "user", "Старая задача", t(9000), "u1"),
                       sub_assistant("eeee5555", "Что-то делал", t(8900), "a1")], BASE - 9000)
        mk(e, "s1", "working", 1, linkScanPath=self.tr)
        e.bot.list_sessions(True)

    def get(self, path, **kw):
        return self.w.req("GET", path, **kw)

    def test_list_statuses_order_and_fields(self):
        st, obj, _ = self.get("/api/session/s1/subagents")
        self.assertEqual(st, 200)
        subs = {x["id"]: x for x in obj["subagents"]}
        self.assertEqual([x["id"] for x in obj["subagents"]], ["cccc3333", "aaaa1111", "bbbb2222", "dddd4444", "eeee5555"])
        self.assertEqual({k: v["status"] for k, v in subs.items()},
                         {"aaaa1111": "done", "bbbb2222": "done", "cccc3333": "working", "dddd4444": "failed",
                          "eeee5555": "done"})
        a = subs["aaaa1111"]
        self.assertEqual((a["type"], a["description"], a["model"], a["background"]),
                         ("general-purpose", "Построить бота", "sonnet", False))
        self.assertEqual(a["runs"], 1)
        self.assertEqual(a["tools"], 3)  # 1 + 2 вызова
        self.assertEqual(len(a["last"]), 160)
        self.assertTrue(a["last"].startswith("Готов отчёт по задаче"))
        self.assertEqual(a["started"], cc._to_epoch(iso(BASE - 115)))
        self.assertEqual(a["updated"], BASE - 30)
        b = subs["bbbb2222"]
        self.assertEqual((b["background"], b["runs"], b["last"]), (True, 2, "Второй проход после возобновления"))
        self.assertEqual(subs["cccc3333"]["tools"], 3)
        self.assertEqual((subs["cccc3333"]["runs"], subs["dddd4444"]["runs"], subs["eeee5555"]["runs"]), (0, 1, 0))
        self.assertNotIn("zzzz9999", subs)

    def test_resumed_agent_is_working_again(self):
        # после завершения агент возобновился: запись новее последнего уведомления и файл свежий
        path = os.path.join(self.folder, "agent-bbbb2222.jsonl")
        with open(path, "ab") as f:
            f.write(sub_assistant("bbbb2222", "Снова работаю", iso(BASE - 5), "a3"))
        os.utime(path, (BASE - 5, BASE - 5))
        subs = {x["id"]: x for x in self.get("/api/session/s1/subagents")[1]["subagents"]}
        self.assertEqual((subs["bbbb2222"]["status"], subs["bbbb2222"]["runs"]), ("working", 2))

    def test_detail_history_with_task_first_and_sidechain(self):
        st, d, _ = self.get("/api/session/s1/subagent/aaaa1111")
        self.assertEqual(st, 200)
        self.assertEqual((d["id"], d["type"], d["description"], d["model"], d["status"]),
                         ("aaaa1111", "general-purpose", "Построить бота", "sonnet", "done"))
        self.assertEqual(d["history"][0]["role"], "task")
        self.assertEqual(d["history"][0]["text"], self.PROMPT.strip())
        roles = [h["role"] for h in d["history"]]
        self.assertEqual(roles, ["task", "assistant", "assistant"])  # sidechain-записи не отброшены, tool_result скрыт
        self.assertIn("Читаю файлы", d["history"][1]["html"])
        self.assertEqual(len([h for h in d["history"] if h["role"] == "task"]), 1)

    def test_detail_limits_and_long_task(self):
        path = os.path.join(self.folder, "agent-aaaa1111.jsonl")
        with open(path, "wb") as f:
            f.write(sub_line("aaaa1111", "user", "З" * 6000, iso(BASE - 115), "u1")
                    + b"".join(sub_assistant("aaaa1111", "шаг %d" % i, iso(BASE - 100 + i), "a%d" % i, 0)
                               for i in range(60)))
        d = self.get("/api/session/s1/subagent/aaaa1111")[1]
        self.assertEqual(len(d["history"][0]["text"]), 4000)
        self.assertLessEqual(len(d["history"]), 41)  # задача + последние 40
        self.assertIn("шаг 59", d["history"][-1]["html"])

    def test_bad_ids_foreign_paths_and_symlinks(self):
        for bad in ("ab", "a" * 65, "a%20b%20c%20d", "..", "%2e%2e%2f%2e%2e", "ааааа"):
            st, obj, _ = self.get("/api/session/s1/subagent/" + urllib.parse.quote(bad, safe="%"))
            self.assertIn(st, (400, 404), bad)
            self.assertNotIn("history", obj)
        self.assertEqual(self.get("/api/session/s1/subagent/zzzz9999")[0], 404)
        self.assertEqual(self.get("/api/session/s1/subagent/aaaa1111/extra")[0], 404)
        # агент другой сессии недоступен через эту
        os.makedirs(os.path.join(self.proj, "sess2", "subagents"))
        make_subagent(os.path.join(self.proj, "sess2", "subagents"), "ffff6666", {"agentType": "x"},
                      [sub_line("ffff6666", "user", "чужая", iso(BASE - 5), "u1")], BASE - 5)
        self.assertEqual(self.get("/api/session/s1/subagent/ffff6666")[0], 404)
        # симлинк на файл вне папки сессии: ни в списке, ни в деталях
        secret = os.path.join(self.e.tmp.name, "secret.jsonl")
        with open(secret, "wb") as f:
            f.write(sub_assistant("evil0001", "СЕКРЕТ", iso(BASE - 5), "x"))
        os.symlink(secret, os.path.join(self.folder, "agent-evil0001.jsonl"))
        self.assertNotIn("evil0001", [x["id"] for x in self.get("/api/session/s1/subagents")[1]["subagents"]])
        self.assertEqual(self.get("/api/session/s1/subagent/evil0001")[0], 404)
        self.assertEqual(self.get("/api/session/нет/subagents".replace("нет", "nope"))[0], 404)

    def test_count_in_session_and_cache(self):
        d = self.get("/api/session/s1")[1]
        self.assertEqual(d["subagents_count"], {"working": 1, "total": 5})
        calls = []
        real = cc.scan_subagent_file
        with mock.patch.object(cc, "scan_subagent_file", lambda p: (calls.append(p), real(p))[1]):
            self.get("/api/session/s1/subagents")
            self.get("/api/session/s1")
        self.assertEqual(calls, [])  # папка и файлы не менялись — кэш
        path = os.path.join(self.folder, "agent-cccc3333.jsonl")
        with open(path, "ab") as f:
            f.write(sub_assistant("cccc3333", "ещё", iso(BASE - 3), "a9"))
        os.utime(path, (BASE - 3, BASE - 3))
        with mock.patch.object(cc, "scan_subagent_file", lambda p: (calls.append(p), real(p))[1]):
            subs = {x["id"]: x for x in self.get("/api/session/s1/subagents")[1]["subagents"]}
        self.assertEqual(len(calls), 5)
        self.assertEqual(subs["cccc3333"]["last"], "ещё")
        # со временем «свежий» перестаёт быть свежим без перечитывания файлов: статус считается на лету
        self.e.t["v"] += 600
        self.assertEqual(self.get("/api/session/s1")[1]["subagents_count"]["working"], 1)  # без завершения — всё ещё работает
        self.e.t["v"] += 4000
        self.assertEqual(self.get("/api/session/s1")[1]["subagents_count"], {"working": 0, "total": 5})

    def test_limits_and_empty(self):
        for i in range(60):
            aid = "mass%04d" % i
            make_subagent(self.folder, aid, {"agentType": "x"}, [sub_line(aid, "user", "z", iso(BASE - 5), "u")],
                          BASE - 100 - i)
        self.assertEqual(len(self.get("/api/session/s1/subagents")[1]["subagents"]), 50)
        mk(self.e, "s2", "done", 1)  # без папки subagents
        self.e.bot.list_sessions(True)
        self.assertEqual(self.get("/api/session/s2/subagents")[1], {"subagents": []})
        self.assertEqual(self.get("/api/session/s2")[1]["subagents_count"], {"working": 0, "total": 0})

    def test_auth_and_read_only(self):
        self.assertEqual(self.get("/api/session/s1/subagents", auth=False)[0], 401)
        self.assertEqual(self.get("/api/session/s1/subagent/aaaa1111", auth=False)[0], 401)
        dev = device_login(self.w, self.e)[1]["device_token"]
        self.assertEqual(self.get("/api/session/s1/subagents", headers={"X-Device-Token": dev}, auth=False)[0], 200)
        self.assertEqual(self.w.req("POST", "/api/session/s1/subagents", {})[0], 405)
        self.assertEqual(self.w.req("POST", "/api/session/s1/subagent/aaaa1111", {})[0], 405)
        self.assertEqual(self.e.sent, [])  # в сессию ничего не отправляется


# ---------------------------------------------------------------- аудит безопасности: исправления

import random as _random


class WhitelistTests(unittest.TestCase):
    """Белый список файлов: ~/tg-inbox, ~/code, ~/.claude/jobs/*/tmp, /var/tmp, /tmp — остальное запрещено."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.tmp.cleanup)
        self.home = os.path.join(self.tmp.name, "home")
        os.makedirs(self.home)
        p = mock.patch.object(cc, "ABS_ROOTS", ())  # домашние правила проверяем отдельно от /tmp, где лежит сам тест
        p.start()
        self.addCleanup(p.stop)

    def h(self, *parts):
        return os.path.join(self.home, *parts)

    def ok(self, path):
        return cc.file_ok(path, self.home) is not None

    def test_allowed_roots(self):
        for path in (write_file(self.h("tg-inbox", "2026-10-15", "IMG.png")), write_file(self.h("code", "x", "report.pdf")),
                     write_file(self.h(".claude", "jobs", "abcd1234", "tmp", "out.txt")),
                     write_file(self.h(".claude", "jobs", "abcd1234", "tmp", "sub", "a.md"))):
            self.assertTrue(self.ok(path), path)
        self.assertEqual(cc.file_ok("~/code/x/report.pdf", self.home)[0], self.h("code", "x", "report.pdf"))
        self.assertEqual(cc.file_ok("~/tg-inbox/2026-10-15/IMG.png", self.home)[0],
                         self.h("tg-inbox", "2026-10-15", "IMG.png"))
        self.assertTrue(self.ok(write_file(self.h("tg-inbox", "Альфа", "2026-10-15", "a.pdf"))))

    def test_absolute_roots_var_tmp_and_tmp(self):
        with mock.patch.object(cc, "ABS_ROOTS", ("/var/tmp", "/tmp")):
            with tempfile.TemporaryDirectory(dir="/var/tmp") as d:
                a = write_file(os.path.join(d, "a.md"), b"1")
                self.assertIsNotNone(cc.file_ok(a, self.home))
                self.assertIsNone(cc.file_ok(write_file(os.path.join(d, ".hid", "a.md")), self.home))
                self.assertIsNone(cc.file_ok(write_file(os.path.join(d, "secrets", "a.md")), self.home))
            self.assertIsNotNone(cc.file_ok(write_file(self.h("loose.md")), self.home))  # лежит под /tmp
            self.assertIsNone(cc.file_ok("/var/tmp", self.home))

    def test_everything_else_is_denied(self):
        for rel in (("note.md",), (".notes", "x.md"), ("srv", "x.md"), ("docs", "a.md"), (".codex", "auth.json"),
                    (".claude.json",), (".cloudflared", "x.json"), (".kube", "config"), (".bash_history",),
                    (".ssh", "id_ed25519"), (".config", "app", "x.txt"), (".claude", "settings.json"),
                    (".claude", "jobs", "abcd", "state.json"), (".claude", "jobs", "abcd", "tmp2", "a.md"),
                    ("tg-inbox-evil", "a.md"), ("codex", "a.md")):
            self.assertFalse(self.ok(write_file(self.h(*rel))), rel)
        homes = {self.home}
        for raw in ("/srv/db/secrets/postgres-password", "/srv/db/app.env", "/srv/anything.md",
                    "/etc/hostname", "/proc/self/environ", "/root/x"):
            self.assertFalse(cc._allowed_norm(raw, homes), raw)

    def test_audit_paths_inside_allowed_roots(self):
        with mock.patch.object(cc, "ABS_ROOTS", ("/var/tmp", "/tmp")):
            for raw in ("/tmp/app.env", "/tmp/backup_ed25519", "/tmp/x/secrets/postgres-password",
                        "/var/tmp/z/app-service.env", "/tmp/.codex/auth.json", "/tmp/a/.cloudflared/x.json"):
                self.assertFalse(cc._allowed_norm(raw, {self.home}), raw)
            for raw in ("/var/tmp/a.md", "/tmp/report.pdf"):
                self.assertTrue(cc._allowed_norm(raw, {self.home}), raw)

    def test_inside_roots_dot_components_and_forbidden_dirs(self):
        for rel in (("code", ".venv", "x.py"), ("code", "a", ".hidden", "b.txt"), ("code", "x", "secrets", "a.txt"),
                    ("code", "x", "Secret", "a.txt"), ("code", "x", "PRIVATE", "a.txt"),
                    ("code", "x", "credentials", "a.txt"), ("code", "x", ".git", "config"),
                    ("tg-inbox", ".hid", "a.txt")):
            self.assertFalse(self.ok(write_file(self.h(*rel))), rel)

    def test_every_name_mask(self):
        bad = ("app.env", "prod.ENV", ".env", "ENV", "env", ".env.local", "a.pem", "a.KEY", "a.p12", "a.pfx", "a.jks",
               "id_rsa", "id_foo", "host_rsa_old", "backup_ed25519", "k_ecdsa.pub", "my_Token.txt", "secrets.txt",
               "Credentials.json", "password.txt", "passwd", "auth.json", "x.kdbx", "bash_history", "History.txt",
               "a.sqlite", "a.db", "a.dump", "dump.sql", "backup.SQL")
        for name in bad:
            self.assertFalse(self.ok(write_file(self.h("code", "x", name))), name)
        good = ("report.pdf", "notes.md", "a.png", "envelope.txt", "environment.md", "identity.txt", "keyboard.md",
                "idle.log", "tokenizer-notes".replace("token", "tok_"), "README")
        for name in good:
            self.assertTrue(self.ok(write_file(self.h("code", "x", name))), name)

    def test_symlinks_are_checked_by_realpath(self):
        secret = write_file(self.h(".ssh", "id_ed25519"), b"KEY")
        os.makedirs(self.h("code"), exist_ok=True)
        os.symlink(secret, self.h("code", "innocent.txt"))
        self.assertFalse(self.ok(self.h("code", "innocent.txt")))
        outside = write_file(self.h("docs", "a.md"))  # корень ~ не в списке
        os.symlink(outside, self.h("tg-inbox-link.md"))
        os.makedirs(self.h("tg-inbox"), exist_ok=True)
        os.symlink(outside, self.h("tg-inbox", "l.md"))
        self.assertFalse(self.ok(self.h("tg-inbox", "l.md")))
        good = write_file(self.h("code", "real.md"))
        os.symlink(good, self.h("tg-inbox", "to-good.md"))
        self.assertTrue(self.ok(self.h("tg-inbox", "to-good.md")))
        # симлинк из /tmp на секрет в домашней папке
        with mock.patch.object(cc, "ABS_ROOTS", ("/tmp",)), tempfile.TemporaryDirectory(dir="/tmp") as d:
            os.symlink(secret, os.path.join(d, "link.txt"))
            self.assertIsNone(cc.file_ok(os.path.join(d, "link.txt"), self.home))
            os.symlink(os.path.join(os.sep, "etc", "hostname"), os.path.join(d, "h.txt"))
            self.assertIsNone(cc.file_ok(os.path.join(d, "h.txt"), self.home))

    def test_regular_file_and_size(self):
        write_file(self.h("code", "d", "x.txt"))
        self.assertFalse(self.ok(self.h("code", "d")))
        big = self.h("code", "big.bin")
        with open(big, "wb") as f:
            f.truncate(cc.FILE_MAX + 1)
        self.assertFalse(self.ok(big))
        with open(big, "wb") as f:
            f.truncate(cc.FILE_MAX)
        self.assertTrue(self.ok(big))

    def test_find_files_and_download_recheck(self):
        good = write_file(self.h("code", "out", "r.pdf"), b"%PDF")
        bad = write_file(self.h("code", "out", "secrets", "k.txt"), b"S")
        files = cc.find_files("Вот %s и %s и /srv/prod-pg/secrets/x" % (good, bad), self.home)
        self.assertEqual([f["path"] for f in files], [good])


class WhitelistWebTests(unittest.TestCase):
    def setUp(self):
        self.w = WebEnv(self)
        self.e = self.w.e
        self.home = self.e.cfg.home
        mk(self.e, "s1", "working", 1)
        self.e.bot.list_sessions(True)

    def test_download_rechecked_after_token_issue(self):
        path = write_file(os.path.join(self.home, "code", "out.txt"), b"data")
        url = self.w.req("GET", "/api/file?path=" + urllib.parse.quote(path))[1]["url"]
        self.assertEqual(self.w.req("GET", url, auth=False)[0], 200)
        os.remove(path)
        secret = write_file(os.path.join(self.home, ".ssh", "id_ed25519"), b"KEY")
        os.symlink(secret, path)  # после выдачи токена файл подменили симлинком
        self.assertEqual(self.w.req("GET", url, auth=False)[0], 403)
        for bad in (os.path.join(self.home, ".ssh", "id_ed25519"), os.path.join(self.home, "backup_ed25519"),
                    "/srv/db/secrets/x", os.path.join(self.home, "code", "app.env")):
            self.assertEqual(self.w.req("GET", "/api/file?path=" + urllib.parse.quote(bad))[0], 403, bad)
            self.assertEqual(self.w.req("POST", "/api/session/s1/sendfile", {"path": bad})[0], 403, bad)
        self.assertEqual(len(self.e.tg.of("sendDocument")), 0)


class SecurityHeadersTests(unittest.TestCase):
    def test_headers_csp_with_hash(self):
        w = WebEnv(self)
        script = "\n  window.x = 1;\n"
        html = ('<!doctype html><title>t</title><script src="https://telegram.org/js/telegram-web-app.js"></script>'
                "<script>%s</script>" % script)
        with open(os.path.join(w.static, "index.html"), "w", encoding="utf-8") as f:
            f.write(html)
        digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
        want = ("default-src 'self'; script-src 'self' https://telegram.org 'sha256-%s'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
                "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org; base-uri 'none'; "
                "form-action 'self'" % digest)
        for path in ("/app/", "/app/index.html"):
            st, body, r = w.req("GET", path, auth=False)
            self.assertEqual(st, 200)
            self.assertEqual(r.getheader("Content-Security-Policy"), want)
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
            self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        # хэш пересчитывается при изменении файла (кэш по mtime/размеру)
        with open(os.path.join(w.static, "index.html"), "w", encoding="utf-8") as f:
            f.write("<script>alert(2)</script>")
        os.utime(os.path.join(w.static, "index.html"), (BASE + 5, BASE + 5))
        new = hashlib.sha256(b"alert(2)").digest()
        csp = w.req("GET", "/app/", auth=False)[2].getheader("Content-Security-Policy")
        self.assertIn("'sha256-%s'" % base64.b64encode(new).decode(), csp)
        self.assertNotIn(digest, csp)
        # не-HTML без CSP, но с nosniff
        st, body, r = w.req("GET", "/app/app.js", auth=False)
        self.assertIsNone(r.getheader("Content-Security-Policy"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")

    def test_product_webapp_serves_clio_layers(self):
        """Реальная статика мини-аппа: слои Clio подключены, отдаются с верным типом, CSP их пускает, sw.js кэширует."""
        real = os.path.join(os.path.dirname(os.path.abspath(cc.__file__)), "assets", "webapp")
        w = WebEnv(self)
        w.srv.static_dir = real
        st, body, r = w.req("GET", "/app/clio.js", auth=False)
        self.assertEqual(st, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("application/javascript"))
        self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff")
        self.assertNotIn(b"lab-source", body)
        st, body, r = w.req("GET", "/app/clio.css", auth=False)
        self.assertEqual(st, 200)
        self.assertTrue(r.getheader("Content-Type").startswith("text/css"))
        st, body, r = w.req("GET", "/app/", auth=False)
        self.assertEqual(st, 200)
        html = body.decode("utf-8")
        self.assertRegex(html, r'<link rel="stylesheet" href="/app/clio\.css\?v=\d+">')  # versioned when served
        self.assertRegex(html, r'<script src="/app/clio\.js\?v=\d+" defer></script>')
        for key in ("filter", "open", "ask", "quota"):
            self.assertNotIn('data-lab-%s=' % key, html)  # ворот слоёв нет: clio.css их не читает (хотфикс ae3f691 снят 20.10)
        self.assertNotIn("data-lab-source", html)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("script-src 'self' https://telegram.org", csp)
        self.assertNotIn("'unsafe-inline'", csp.split("style-src")[0])  # скрипты по-прежнему только 'self' и хэши
        st, body, r = w.req("GET", "/app/sw.js", auth=False)
        self.assertEqual(st, 200)
        self.assertIn(b"/app/clio.js", body)
        self.assertIn(b"/app/clio.css", body)

    def test_api_and_download_headers(self):
        w = WebEnv(self)
        mk(w.e, "s1", "done", 1)
        w.e.bot.list_sessions(True)
        for path, auth in (("/api/sessions", True), ("/api/sessions", False), ("/nope", False)):
            st, body, r = w.req("GET", path, auth=auth)
            self.assertEqual(r.getheader("X-Content-Type-Options"), "nosniff", (path, auth))
            self.assertEqual(r.getheader("Referrer-Policy"), "no-referrer")
        self.assertEqual(w.req("GET", "/api/sessions")[2].getheader("Cache-Control"), "no-store")
        f = write_file(os.path.join(w.e.cfg.home, "code", "a.txt"), b"1")
        url = w.req("GET", "/api/file?path=" + urllib.parse.quote(f))[1]["url"]
        r = w.req("GET", url, auth=False)[2]
        self.assertEqual((r.getheader("X-Content-Type-Options"), r.getheader("Referrer-Policy")),
                         ("nosniff", "no-referrer"))


class ServerLimitsTests(unittest.TestCase):
    def test_defaults(self):
        w = WebEnv(self)
        self.assertEqual(w.srv.httpd.RequestHandlerClass.timeout, 30)
        self.assertEqual(w.srv.httpd.request_queue_size, 64)
        self.assertEqual(w.srv.httpd._slots._initial_value, 32)

    def test_idle_connection_is_closed_by_timeout(self):
        w = WebEnv(self)
        srv = cc.WebServer(w.e.bot, "127.0.0.1", 0, w.static, io_timeout=0.4)
        srv.start()
        self.addCleanup(srv.stop)
        s = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
        s.sendall(b"GET /app/ HTTP/1.1\r\nHost: x\r\n")  # заголовки не закончены
        t0 = time.time()
        self.assertEqual(s.recv(100), b"")  # сервер сам закрыл соединение
        self.assertLess(time.time() - t0, 3)
        s.close()

    def test_connection_cap_gives_503(self):
        w = WebEnv(self)
        srv = cc.WebServer(w.e.bot, "127.0.0.1", 0, w.static, io_timeout=5, max_handlers=2)
        srv.start()
        self.addCleanup(srv.stop)
        held = [socket.create_connection(("127.0.0.1", srv.port), timeout=5) for _ in range(2)]
        for s in held:
            s.sendall(b"GET /app/ HTTP/1.1\r\n")  # занимают обработчики
        time.sleep(0.3)
        extra = socket.create_connection(("127.0.0.1", srv.port), timeout=5)
        extra.sendall(b"GET /app/ HTTP/1.1\r\nHost: x\r\n\r\n")
        self.assertTrue(extra.recv(200).startswith(b"HTTP/1.1 503"))
        extra.close()
        for s in held:
            s.close()
        time.sleep(0.5)  # слоты освободились
        conn = http.client.HTTPConnection("127.0.0.1", srv.port, timeout=5)
        conn.request("GET", "/app/")
        self.assertEqual(conn.getresponse().status, 200)
        conn.close()

    def test_unread_body_closes_connection(self):
        w = WebEnv(self)
        smuggled = b"GET /api/sessions HTTP/1.1\r\nHost: x\r\nX-Dev: 1\r\n\r\n"
        for head, extra in ((b"POST /api/sessions HTTP/1.1\r\nHost: x\r\nContent-Length: %d\r\n\r\n" % len(smuggled), smuggled),
                            (b"POST /api/session/s1/send HTTP/1.1\r\nHost: x\r\nContent-Length: 999999\r\n\r\n", b""),
                            (b"POST /api/login HTTP/1.1\r\nHost: x\r\nContent-Length: 999999\r\n\r\n", b"")):
            s = socket.create_connection(("127.0.0.1", w.srv.port), timeout=5)
            s.sendall(head + extra)
            data = b""
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data += chunk
            s.close()
            self.assertEqual(data.count(b"HTTP/1.1 "), 1, data)  # один ответ, второго «вложенного» нет
            self.assertIn(b"Connection: close", data)


class SanitizeTests(unittest.TestCase):
    def test_md_to_html_survives_control_markers(self):
        self.assertEqual(cc.md_to_html("\x000\x00"), "0")
        self.assertNotIn("\x00", cc.md_to_html("a `b` \x001\x00 ```\nc\x00\n```"))
        rnd = _random.Random(7)
        alphabet = "`*#[]()\x00\x01\x1b0123456789 \nabc<>&:/"
        for _ in range(500):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 80)))
            out = cc.md_to_html(text)  # не падает
            self.assertNotIn("\x00", out)
        data = asst_ts("a1", "x \x005\x00 `код` \x000\x00", "t1")
        self.assertEqual(cc.parse_history(data)[0]["role"], "assistant")

    def test_links_only_safe_schemes(self):
        out = cc.md_to_html("[a](https://e.org/x?a=1&b=2) [m](mailto:a@b.org) [j](javascript:alert(1)) "
                            "[d](data:text/html,x) [r](/relative) [v](VBSCRIPT:x)")
        self.assertNotIn("<a", cc.md_to_html("[j](javascript:alert(1))"))
        self.assertIn('<a href="https://e.org/x?a=1&amp;b=2">a</a>', out)  # для Telegram — без лишних атрибутов
        self.assertNotIn("javascript", out.replace("[j]", ""))
        self.assertIn(">m</a>", out)
        for bad in ("javascript", "data:text", "relative", "VBSCRIPT"):
            self.assertNotIn('href="%s' % bad, out)
        web = cc.md_to_html("[a](https://e.org) [j](javascript:alert(1))", web=True)
        self.assertIn('<a href="https://e.org" rel="noopener noreferrer" target="_blank">a</a>', web)
        self.assertEqual(web.count("<a "), 1)
        self.assertIn(">a</a> j", web)  # недопустимая схема — просто текст ссылки
        hist = cc.parse_history(asst_ts("a1", "[x](https://e.org)", "t"))[0]["html"]
        self.assertIn('rel="noopener noreferrer"', hist)

    def test_control_characters_never_reach_the_terminal(self):
        pasted, screen = [], {"v": "%s\n❯\xa0\n%s\n" % (BORDER, BORDER)}

        def runner(args, **kw):
            if args[1] == "load-buffer":
                with open(args[-1], "rb") as f:
                    pasted.append(f.read())
            elif args[1] == "paste-buffer":
                screen["v"] = "%s\n❯ %s\n%s\n" % (BORDER, pasted[-1].decode().splitlines()[0], BORDER)
            elif args[1] == "capture-pane":
                return 0, screen["v"]
            return 0, ""
        clock = {"t": 0.0}
        tm = cc.Tmux("claude", runner, lambda s: clock.__setitem__("t", clock["t"] + s), lambda: clock["t"])
        ok, err = tm.send_text("abc12345", "hello\x1b[201~INJECTED\x1b]52;c;ZXZpbA==\x07world\x08\x9b\nвторая\tстрока")
        self.assertTrue(ok, err)
        data = pasted[0]
        for bad in (b"\x1b", b"\x07", b"\x08", "\x9b".encode()):
            self.assertNotIn(bad, data)
        self.assertEqual(data.decode(), "hello[201~INJECTED]52;c;ZXZpbA==world\nвторая\tстрока")
        self.assertEqual(tm.send_text("abc12345", "\x1b\x1b"), (False, "Пустое сообщение не отправляю."))
        # «свой ответ» на вопрос и имя
        pasted.clear()
        self.assertEqual(cc.clean_name("Имя\x1b[31m\x00х"), "Имя[31mх"[:28])
        self.assertNotIn("\x1b", cc.clean_name("a\x1bb"))

    def test_own_answer_and_rename_are_sanitized(self):
        w = WebEnv(self)
        e = w.e
        tr = os.path.join(e.tmp.name, "q.jsonl")
        with open(tr, "wb") as f:
            f.write(ask_line("toolu_1", [Q_SIZE]))
        mk(e, "s1", "working", 1, linkScanPath=tr)
        e.agents[-1]["pid"] = 5
        e.bot.list_sessions(True)
        fs = FakeScreen(MENU_S)
        pasted = []
        orig = fs.runner

        def runner(args, timeout=30, cwd=None, input_text=None):
            if args[1] == "load-buffer":
                with open(args[-1], "rb") as f:
                    pasted.append(f.read())
            return orig(args, timeout, cwd, input_text)
        e.bot.tmux = cc.Tmux("claude", runner, lambda s: None)
        fs.on_paste = lambda f: setattr(f, "screen", MENU_S.replace("4. Type something", "❯ 4. ровно[201~"))
        st, obj, _ = w.req("POST", "/api/session/s1/answer", {"action": "own", "text": "ровно\x1b[201~"})
        self.assertTrue(obj["ok"], obj)
        self.assertEqual(pasted[0], "ровно[201~".encode())

    def test_command_whitelist_on_server(self):
        w, e, fs = live_env(self)
        type_into(fs, "/model opus")
        for bad in ("/foo", "/model\x1bopus", "/exit", "/login", "/mcp add x", "/Model"):
            st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": bad})
            self.assertEqual((st, obj["code"]), (400, "bad"), bad)
        self.assertEqual(fs.keys, [])
        for ok_cmd in ("/model opus", "/compact что сохранить", "/status", "/rename Новое имя"):
            type_into(fs, ok_cmd)
            st, obj, _ = w.req("POST", "/api/session/s1/command", {"text": ok_cmd})
            self.assertEqual(st, 200, ok_cmd)
        # обычное сообщение, начинающееся с пути, не считается командой и идёт как текст
        e.bot.deliver = lambda chat, sid, text, origin="chat": (True, "")
        st, obj, _ = w.req("POST", "/api/session/s1/send", {"text": "/home/user/x.md посмотри"})
        self.assertEqual(obj, {"ok": True, "error": None, "code": None})


class PushHostTests(unittest.TestCase):
    def test_endpoint_allowlist(self):
        good = ("https://fcm.googleapis.com/fcm/send/x", "https://updates.push.services.mozilla.com/wpush/v2/x",
                "https://web.push.apple.com/x", "https://a.push.apple.com/x", "https://wns2-par02p.notify.windows.com/x",
                "https://b.push.services.mozilla.com/x")
        bad = ("https://127.0.0.1/x", "https://10.0.0.1/x", "https://evil.example/x", "http://fcm.googleapis.com/x",
               "https://fcm.googleapis.com.evil.example/x", "https://fcm.googleapis.com:8443/x",
               "https://fcm.googleapis.com@evil.example/x", "https://push.apple.com/x", "https://[::1]/x",
               "https://169.254.169.254/latest", "https://localhost/x", "ftp://fcm.googleapis.com/x", "")
        for ep in good:
            self.assertTrue(cc.push_endpoint_ok(ep), ep)
        for ep in bad:
            self.assertFalse(cc.push_endpoint_ok(ep), ep)

    def test_subscribe_rejects_internal_hosts(self):
        w = WebEnv(self)
        e = w.e
        e.cfg.vapid_file = os.path.join(e.tmp.name, "vapid", "vapid.pem")
        priv = _ec.generate_private_key(_ec.SECP256R1())
        keys = {"p256dh": cc.b64u(priv.public_key().public_bytes(_ser.Encoding.X962, _ser.PublicFormat.UncompressedPoint)),
                "auth": cc.b64u(os.urandom(16))}
        h = {"X-Device-Token": device_login(w, e)[1]["device_token"]}
        for ep in ("https://127.0.0.1/x", "https://10.0.0.1", "https://evil.example"):
            st, obj, _ = w.req("POST", "/api/push/subscribe", {"endpoint": ep, "keys": keys}, headers=h, auth=False)
            self.assertEqual(st, 400, ep)
        self.assertEqual(e.bot.store.data["push"], {})
        st, obj, _ = w.req("POST", "/api/push/subscribe", {"endpoint": "https://fcm.googleapis.com/fcm/send/z",
                                                          "keys": keys}, headers=h, auth=False)
        self.assertEqual(st, 200)

    def test_stored_foreign_endpoint_is_never_posted(self):
        w, e = push_env(self)
        ep = "https://127.0.0.1:9/x"
        e.bot.store.data["push"][ep] = dict(e.bot.store.data["push"][e.subs[0][0]])
        mk(e, "s1", "blocked", 1)
        e.bot.list_sessions(True)
        e.bot.push_notify(1, "s1", "x")
        self.assertNotIn(ep, [x[0] for x in e.sent_pushes])
        self.assertNotIn(ep, e.bot.store.data["push"])


class DeviceLifetimeTests(unittest.TestCase):
    def test_absolute_180_days(self):
        w = WebEnv(self)
        e = w.e
        e.cfg.webapp_url_env = "https://x"
        dev = device_login(w, e)[1]["device_token"]
        h = {"X-Device-Token": dev}
        for _ in range(2):  # скользящее окно продлевается...
            e.t["v"] += 80 * 86400
            self.assertEqual(w.req("GET", "/api/sessions", headers=h, auth=False)[0], 200)
        self.assertEqual(w.req("GET", "/api/sessions", headers=h, auth=False)[0], 200)  # 160 суток
        e.t["v"] += 30 * 86400  # ...но абсолютный срок 180 суток от создания истёк, хотя last_seen свежий
        self.assertEqual(w.req("GET", "/api/sessions", headers=h, auth=False)[0], 401)
        self.assertEqual(e.bot.store.data["devices"], {})


class LogRedactionTests(unittest.TestCase):
    def test_signed_links_not_logged(self):
        w = WebEnv(self)
        f = write_file(os.path.join(w.e.cfg.home, "code", "a.txt"), b"1")
        url = w.req("GET", "/api/file?path=" + urllib.parse.quote(f))[1]["url"]
        lines = []
        with mock.patch.object(cc, "log", lambda m: lines.append(m)):
            w.req("GET", url, auth=False)
            w.req("GET", "/api/sessions")
        joined = "\n".join(lines)
        self.assertIn("webapp GET /f/<hidden>", joined)
        self.assertNotIn(url[3:20], joined)
        self.assertIn("webapp GET /api/sessions", joined)


class InboxCleanupTests(unittest.TestCase):
    def test_nested_session_folder_cleaned(self):
        e = panel_env(self)
        inbox = os.path.join(e.cfg.home, "tg-inbox")
        nest = write_file(os.path.join(inbox, "Альфа", "2026-08-01", "n.pdf"))
        os.utime(nest, (BASE - 40 * 86400,) * 2)
        e.bot.inbox_tick()
        self.assertFalse(os.path.exists(os.path.join(inbox, "Альфа")))  # файл и пустые папки сессии убраны

    def test_old_files_removed_daily(self):
        e = panel_env(self)
        inbox = os.path.join(e.cfg.home, "tg-inbox")
        old = write_file(os.path.join(inbox, "2026-08-01", "old.pdf"))
        new = write_file(os.path.join(inbox, "2026-10-14", "new.pdf"))
        other = write_file(os.path.join(inbox, "2026-08-01", "old2.png"))
        os.makedirs(os.path.join(inbox, "empty-old"))
        outside = write_file(os.path.join(e.cfg.home, "keep.txt"))
        os.symlink(outside, os.path.join(inbox, "2026-08-01", "link"))
        dangling = os.path.join(inbox, "2026-10-14", "moved.png")  # left by moving files into session folders
        os.symlink(os.path.join(inbox, "gone", "moved.png"), dangling)
        for p_, days in ((old, 31), (other, 45), (new, 2), (outside, 400)):
            os.utime(p_, (BASE - days * 86400, BASE - days * 86400))
        lines = []
        with mock.patch.object(cc, "log", lambda m: lines.append(m)):
            e.bot.inbox_tick()
        self.assertFalse(os.path.exists(old) or os.path.exists(other))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(outside))  # за пределами ~/tg-inbox и по симлинку не трогаем
        self.assertTrue(os.path.islink(os.path.join(inbox, "2026-08-01", "link")))  # a live link stays
        self.assertFalse(os.path.lexists(dangling))
        self.assertFalse(os.path.exists(os.path.join(inbox, "empty-old")))
        self.assertEqual(lines, ["tg-inbox: files removed 2"])  # в логе только число
        write_file(os.path.join(inbox, "2026-08-01", "old3.txt"))
        os.utime(os.path.join(inbox, "2026-08-01", "old3.txt"), (BASE - 99 * 86400,) * 2)
        e.bot.inbox_tick()  # раз в сутки
        self.assertTrue(os.path.exists(os.path.join(inbox, "2026-08-01", "old3.txt")))
        e.t["v"] += 25 * 3600
        e.bot.inbox_tick()
        self.assertFalse(os.path.exists(os.path.join(inbox, "2026-08-01", "old3.txt")))


# ---------------------------------------------------------------- language switching (i18n)

import ast as _ast


def _spec_counts(text):
    return sorted(re.findall(r"%(?:\.\d+)?[sdf]|%%", text))


class I18nTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(cc.set_lang, "ru")

    @staticmethod
    def module_tree():
        with open(cc.__file__, encoding="utf-8") as f:
            return _ast.parse(f.read())

    def test_key_sets_are_complete(self):
        self.assertEqual(set(cc.STR["en"]), set(cc.STR["ru"]))
        self.assertGreater(len(cc.STR["ru"]), 250)
        for k in cc.STR["en"]:
            self.assertEqual(cc.STR["en"][k], k)  # the English text is the key itself

    def test_every_used_key_has_a_translation_and_no_dead_keys(self):
        tree = self.module_tree()
        table = [n for n in _ast.walk(tree) if isinstance(n, _ast.Dict) and n.keys and
                 any(isinstance(k, _ast.Constant) and k.value == "waiting for you" for k in n.keys)]
        self.assertEqual(len(table), 1, "_RU table")
        inside = {id(c) for c in _ast.walk(table[0])}
        used, called = set(), set()
        for n in _ast.walk(tree):
            if isinstance(n, _ast.Constant) and isinstance(n.value, str) and id(n) not in inside:
                used.add(n.value)
            if (isinstance(n, _ast.Call) and isinstance(n.func, _ast.Name) and n.func.id in ("_t", "t") and n.args
                    and isinstance(n.args[0], _ast.Constant) and isinstance(n.args[0].value, str)):
                called.add(n.args[0].value)
        self.assertGreater(len(called), 200)
        self.assertEqual(sorted(k for k in called if k not in cc._RU), [])  # untranslated source strings
        self.assertEqual(sorted(k for k in cc._RU if k not in used), [])  # translations nobody uses

    def test_placeholders_match_between_languages(self):
        for en, ru in cc._RU.items():
            self.assertEqual(_spec_counts(en), _spec_counts(ru), en)
            self.assertEqual(en.count("<i>"), ru.count("<i>"), en)

    def test_russian_keeps_the_original_wording(self):
        cc.set_lang("ru")
        self.assertEqual(cc.t("🗂 Archive %d") % 5, "🗂 Архив 5")
        self.assertEqual(cc.ago_short(0 + 1000, 1000 + 12 * 60), "12 мин")
        self.assertEqual(cc.humanize_detail("You've hit your session limit · resets 6:30am (Europe/Belgrade)"),
                         "лимит сессии · сброс в 6:30")
        self.assertEqual(cc.state_word("blocked"), "ждёт вас")
        self.assertEqual(cc.btn("list"), "📋 Сессии")
        self.assertTrue(cc.help_topics().startswith("Как пользоваться"))
        self.assertTrue(cc.name_prompt().endswith("Сессии:"))
        self.assertEqual(cc.panel_title(), "🎛 Агенты")

    def test_english_texts(self):
        cc.set_lang("en")
        self.assertEqual(cc.LANG, "en")
        self.assertEqual(cc.t("🗂 Archive %d") % 5, "🗂 Archive 5")
        self.assertEqual(cc.ago_short(1000, 1000 + 12 * 60), "12 min")
        self.assertEqual(cc.ago_short(1000, 1000 + 3 * 3600), "3 h")
        self.assertEqual(cc.ago_short(1000, 1005), "now")
        self.assertEqual(cc.ago(1000, 1005), "just now")
        self.assertEqual(cc.humanize_detail("You've hit your session limit · resets 4:10pm (Europe/Belgrade)"),
                         "session limit · resets at 16:10")
        self.assertEqual(cc.humanize_detail("You've reached your Fable limit"), "Fable limit")
        self.assertEqual(cc.humanize_detail("API Error: blocked by safeguards"), "API error: request rejected by safeguards")
        self.assertEqual([cc.state_word(x) for x in ("blocked", "working", "done", "failed")],
                         ["waiting for you", "running", "done", "error"])
        self.assertEqual([cc.btn(k) for k in ("list", "new", "quota", "clean")],
                         ["📋 Sessions", "➕ New", "📊 Quota", "🧹 Clean"])
        self.assertEqual([c for c, _ in cc.bot_commands()], ["s", "new", "quota", "screen", "stop", "pwa", "devices", "help"])
        self.assertEqual(dict(cc.bot_commands())["quota"], "Claude quota")
        by = {c["cmd"]: c for c in cc.slash_commands()}
        self.assertEqual((by["/model"]["title"], by["/model"]["hint"]), ("Model", "Also changes the default for new sessions"))
        self.assertIn("previous conversation stays on disk", by["/clear"]["confirm"])
        self.assertTrue(cc.help_default().startswith("Control Claude Code sessions from your phone."))
        self.assertTrue(cc.help_topics().startswith("How to use"))
        self.assertEqual(cc.no_session(), "First pick a session: press «📋 Sessions».")
        self.assertTrue(cc.name_prompt().startswith("You come up with short names"))
        self.assertTrue(cc.name_prompt().endswith("Sessions:"))
        self.assertIn("in English", cc.name_prompt())
        self.assertEqual(cc.notification_text({"name": "A", "state": "blocked", "detail": "", "result": None}),
                         "🟡 A is waiting: input")
        self.assertEqual(cc.human_size(3 * 1024 * 1024), "3.0 MB")
        self.assertEqual(cc.card_markup("a")["inline_keyboard"][0][0]["text"], "💬 Write here")

    def test_unknown_key_falls_back_to_itself_and_format_fields(self):
        cc.set_lang("en")
        self.assertEqual(cc.t("Not in the table"), "Not in the table")
        cc.set_lang("ru")
        self.assertEqual(cc.t("Not in the table"), "Not in the table")
        self.assertEqual(cc.t("Hello {name}", name="x"), "Hello x")
        self.assertEqual(cc.set_lang("de"), "en")

    def test_no_task_answer_in_both_languages(self):
        for name in ("Без задачи", "без задачи: мусор", "No task", "no task at all"):
            self.assertTrue(cc.is_no_task(name), name)
        self.assertFalse(cc.is_no_task("Проект: задача"))
        self.assertFalse(cc.is_no_task(None))

    def test_legacy_reply_buttons_recognized_in_any_language(self):
        for text, kind in (("📋 Сессии", "list"), ("📋 Sessions", "list"), ("➕ Новая", "new"), ("📊 Quota", "quota"),
                           ("🧹 Очистить", "clean"), ("📍 Текущая", "cur"), ("📺 Screen", "screen")):
            self.assertEqual(cc.button_kind(text), kind, text)
        self.assertIsNone(cc.button_kind("hello"))

    def test_panel_and_bot_messages_follow_language(self):
        w = WebEnv(self)
        e = w.e
        cc.set_lang("en")
        e.bot.handle_update(e.msg("/s", mid=5))
        panel = e.tg.of("sendMessage")[-1]
        self.assertIn("No sessions", panel["text"])
        self.assertIn("🗂 Archive 0", str(panel["reply_markup"]))
        self.assertIn("❓ Help", str(panel["reply_markup"]))
        e.bot.handle_update(e.msg("hello", mid=6))
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "First pick a session: press «📋 Sessions».")
        mk(e, "s1", "done", 1, name="Alpha")
        e.bot.list_sessions(True)
        self.assertIn("🔔 Follow", str(cc.card_markup("s1", watched=False)))
        self.assertIn("s1", e.bot.card_text("s1"))
        self.assertTrue(e.bot.card_text("s1").splitlines()[1].startswith("done"))
        cc.set_lang("ru")
        self.assertTrue(e.bot.card_text("s1").splitlines()[1].startswith("готово"))

    def test_api_reports_language_and_localized_commands(self):
        w = WebEnv(self)
        e = w.e
        mk(e, "s1", "done", 1)
        e.bot.list_sessions(True)
        cc.set_lang("ru")
        self.assertEqual(w.req("GET", "/api/sessions")[1]["lang"], "ru")
        self.assertEqual(w.req("GET", "/api/session/s1")[1]["lang"], "ru")
        by = {c["cmd"]: c for c in w.req("GET", "/api/commands")[1]["commands"]}
        self.assertEqual(by["/model"]["title"], "Модель")
        cc.set_lang("en")
        self.assertEqual(w.req("GET", "/api/sessions")[1]["lang"], "en")
        self.assertEqual(w.req("GET", "/api/session/s1")[1]["lang"], "en")
        by = {c["cmd"]: c for c in w.req("GET", "/api/commands")[1]["commands"]}
        self.assertEqual(by["/model"]["title"], "Model")
        st, obj, _ = w.req("POST", "/api/session/s1/send", {"text": "  "})
        self.assertEqual(obj["error"], "Empty message")
        cc.set_lang("ru")
        st, obj, _ = w.req("POST", "/api/session/s1/send", {"text": "  "})
        self.assertEqual(obj["error"], "Пустое сообщение")

    def test_naming_prompt_language_is_sent_to_the_model(self):
        e = names_env(self)
        mk(e, "a1", "done", 1)
        e.bot.list_sessions(True)
        cc.set_lang("en")
        e.bot.suggest_names(e.bot.list_sessions(True))
        self.assertTrue(e.name_inputs[-1].startswith(cc.name_prompt()))
        self.assertIn("in English", e.name_inputs[-1])
        cc.set_lang("ru")
        e.bot.suggest_names(e.bot.list_sessions(True))
        self.assertIn("по-русски", e.name_inputs[-1])


class PushNeedsContactTests(unittest.TestCase):
    def test_push_is_off_without_vapid_sub(self):
        w, e = push_env(self)
        mk(e, "s1", "blocked", 1)
        e.bot.list_sessions(True)
        self.assertTrue(e.bot.push_enabled())
        e.cfg.vapid_sub = ""
        self.assertFalse(e.bot.push_enabled())
        self.assertFalse(e.bot.push_notify(1, "s1", "x"))  # fall back to Telegram
        self.assertEqual(e.sent_pushes, [])
        st, obj, _ = w.req("GET", "/api/push/key")
        self.assertEqual((st, obj), (501, {"error": "push unavailable"}))
        e.cfg.vapid_sub = "mailto:admin@example.com"
        self.assertEqual(w.req("GET", "/api/push/key")[0], 200)


class RepositoryHygieneTests(unittest.TestCase):
    BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def test_units_use_home_placeholder(self):
        for name in ("zidos-pager.service", "zidos-pager-tunnel.service", "zidos-pager-quick-tunnel.service"):
            text = open(os.path.join(self.BASE, "deploy", name), encoding="utf-8").read()
            self.assertIn("%h", text, name)
            self.assertNotIn("/home/", text, name)
        example = open(os.path.join(self.BASE, "deploy", "zidos-pager.yml.example"), encoding="utf-8").read()
        self.assertIn("<TUNNEL_UUID>", example)
        self.assertIn("example.com", example)

    def test_source_has_no_personal_defaults(self):
        src = open(cc.__file__, encoding="utf-8").read()
        for bad in (w[::-1] for w in ("ivak/emoh/", "liamnotorp", "semreh.", "oivaoivak")):  # reversed on purpose  # the zidos brand is the product name now
            self.assertNotIn(bad, src, bad)


class FakeTelegramServer(BaseHTTPRequestHandler):
    log = []
    updates = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        FakeTelegramServer.log.append((method, body))
        if method == "getMe":
            res = {"has_topics_enabled": True}
        elif method == "createForumTopic":
            res = {"message_thread_id": 777 + sum(1 for m_, _ in FakeTelegramServer.log if m_ == "createForumTopic") - 1}
        elif method == "getUpdates":
            res = FakeTelegramServer.updates[:]
            FakeTelegramServer.updates.clear()
        else:
            res = {"message_id": len(FakeTelegramServer.log)}
        data = json.dumps({"ok": True, "result": res}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


class EndToEndTests(unittest.TestCase):
    def test_fake_telegram_server(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeTelegramServer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        FakeTelegramServer.log.clear()
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, "env")
            open(envf, "w").write("TG_BOT_TOKEN=T\nTG_ALLOWED_USERS=5\nTG_API_BASE=http://127.0.0.1:%d\n"
                                  "CC_JOBS_DIR=%s\nZIDOS_PAGER_STATE=%s\n"
                                  % (srv.server_address[1], d, os.path.join(d, "state.json")))
            cfg = cc.Config(env_path=envf, environ={"HOME": d})
            agents = [{"id": "aaaaaaaa", "name": "Сессия", "state": "blocked"}]
            runner = lambda args, **kw: (0, json.dumps(agents))
            injected, done = [], threading.Event()

            def inj(sid, text):
                injected.append((sid, text))
                done.set()
                return True, ""
            threads = []

            def spawn(fn, *a):
                t = threading.Thread(target=fn, args=a, daemon=True)
                threads.append(t)
                t.start()
            bot = cc.Bot(cfg, runner=runner, sleep=lambda s: None, spawn=spawn)
            bot.claude_missing = lambda: False
            bot.injector = inj

            def upd(uid, text, mid):
                return {"update_id": mid, "message": {"message_id": mid, "from": {"id": uid},
                                                      "chat": {"id": uid, "type": "private"}, "text": text}}
            FakeTelegramServer.updates[:] = [upd(5, "/s", 1)]
            self.assertEqual(bot.poll_once(), 1)
            sends = [b for m, b in FakeTelegramServer.log if m == "sendMessage"]
            self.assertEqual(len(sends), 1)
            kb = sends[0]["reply_markup"]["inline_keyboard"]
            self.assertTrue(kb[0][0]["callback_data"] == "s:aaaaaaaa")
            self.assertEqual(kb[0][0]["text"], "🟡 Сессия")
            FakeTelegramServer.updates[:] = [
                {"update_id": 2, "callback_query": {"id": "c", "from": {"id": 5}, "data": "s:aaaaaaaa",
                                                    "message": {"message_id": 3, "chat": {"id": 5, "type": "private"}}}},
                upd(9, "чужой", 3), upd(5, "привет, сессия", 4)]
            bot.poll_once()
            self.assertTrue(done.wait(5))
            self.assertEqual(injected, [("aaaaaaaa", "привет, сессия")])
            for t in threads:
                t.join(5)
            self.assertEqual(json.load(open(os.path.join(d, "state.json")))["offset"], 5)

    def test_fake_telegram_server_topics_mode(self):
        srv = HTTPServer(("127.0.0.1", 0), FakeTelegramServer)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        FakeTelegramServer.log.clear()
        with tempfile.TemporaryDirectory() as d:
            envf = os.path.join(d, "env")
            with open(envf, "w") as f:
                f.write("TG_BOT_TOKEN=T\nTG_ALLOWED_USERS=5\nTG_API_BASE=http://127.0.0.1:%d\n"
                        "CC_JOBS_DIR=%s\nZIDOS_PAGER_STATE=%s\n" % (srv.server_address[1], d, os.path.join(d, "s.json")))
            cfg = cc.Config(env_path=envf, environ={"HOME": d})
            agents = [{"id": "aaaaaaaa", "name": "Сессия", "state": "working", "cwd": "/tmp"}]
            os.makedirs(os.path.join(d, "aaaaaaaa"))
            with open(os.path.join(d, "aaaaaaaa", "state.json"), "w") as f:
                json.dump({"state": "working", "name": "Сессия", "cwd": "/tmp"}, f)
            injected = []
            bot = cc.Bot(cfg, runner=lambda args, **kw: (0, json.dumps(agents)), sleep=lambda s: None,
                         spawn=lambda fn, *a: fn(*a))
            bot.claude_missing = lambda: False
            bot.injector = lambda sid, text: (injected.append((sid, text)) or (True, ""))
            self.assertTrue(bot.detect_topics())

            def upd(uid, text, mid, thread=None):
                m = {"message_id": mid, "from": {"id": uid}, "chat": {"id": uid, "type": "private"}, "text": text}
                if thread:
                    m["message_thread_id"] = thread
                return {"update_id": mid, "message": m}
            FakeTelegramServer.updates[:] = [
                {"update_id": 1, "callback_query": {"id": "c", "from": {"id": 5}, "data": "s:aaaaaaaa",
                                                    "message": {"message_id": 3, "chat": {"id": 5, "type": "private"}}}},
                upd(5, "привет из топика", 4, thread=777)]
            bot.poll_once()
            log_ = FakeTelegramServer.log
            self.assertEqual([b["name"] for m, b in log_ if m == "createForumTopic"], ["Сессия", "🎛 Агенты"])
            cards = [b for m, b in log_ if m == "sendMessage" and b["text"].endswith("· aaaaaaaa")]
            self.assertEqual(cards[0]["message_thread_id"], 777)
            self.assertEqual(injected, [("aaaaaaaa", "привет из топика")])


class FakeHerdr:
    """A fake herdr server on a Unix socket: answers the API methods the bot uses and records the requests."""

    def __init__(self, tc):
        self.dir = tempfile.mkdtemp(prefix="hd")
        tc.addCleanup(self.close)
        self.path = os.path.join(self.dir, "herdr.sock")
        self.requests = []
        self.status = "working"
        self.screen = "line one\nDo you want to proceed?\n 1. Yes\n 2. No"
        self.events = []  # lines sent to a subscriber after subscription_started
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.alive = True
        threading.Thread(target=self.serve, daemon=True).start()

    def close(self):
        self.alive = False
        try:
            self.srv.close()
        except OSError:
            pass
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def agent(self):
        return {"agent": "codex", "agent_status": self.status, "pane_id": "w1:p2", "workspace_id": "w1",
                "cwd": "/home/user/code/proj", "revision": 5, "terminal_id": "t1", "tab_id": "w1:t1",
                "focused": False}

    def serve(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.split(b"\n")[0])
            self.requests.append((req["method"], req.get("params")))
            m, p = req["method"], req.get("params") or {}
            if m == "agent.list":
                res = {"type": "agent_list", "agents": [self.agent()]}
            elif m == "pane.list":
                res = {"type": "pane_list", "panes": [{"pane_id": "w1:p2", "workspace_id": "w1"}]}
            elif m == "pane.read":
                res = {"type": "pane_read", "read": {"text": self.screen, "truncated": False, "revision": 5}}
            elif m in ("pane.send_text", "pane.send_keys"):
                res = {"type": "ok"}
            elif m == "events.subscribe":
                c.sendall((json.dumps({"id": req["id"], "result": {"type": "subscription_started"}}) + "\n").encode())
                for ev in self.events:
                    c.sendall((json.dumps(ev) + "\n").encode())
                time.sleep(0.5)
                return
            else:
                c.sendall((json.dumps({"id": req["id"], "error": {"code": "bad_method", "message": m}}) + "\n").encode())
                return
            c.sendall((json.dumps({"id": req["id"], "result": res}) + "\n").encode())
        finally:
            c.close()

    def methods(self):
        return [m for m, _ in self.requests]


def herdr_env(tc, fake):
    e = Env(tc)
    e.bot.herdr = cc.HerdrClient(fake.path)
    e.bot.injector = e.bot.tmux.send_text  # the real Tmux path: herdr ids go to the herdr client
    return e


class HerdrTests(unittest.TestCase):
    def test_id_roundtrip_and_validation(self):
        self.assertEqual(cc.herdr_sid("w1:p2"), "h-w1.p2")
        self.assertTrue(cc.is_herdr("h-w1.p2"))
        self.assertEqual(cc.herdr_pane("h-w1.p2"), "w1:p2")
        for bad in ("abcd1234", "h-w1", "h-", "h-w1:p2", "x-w1.p2", "", None):
            self.assertFalse(cc.is_herdr(bad))
        self.assertNotIn(":", cc.herdr_sid("w12:p34"))

    def test_list_contains_herdr_agent(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        e.agents = [{"id": "a", "name": "Claude job", "state": "working"}]
        res = e.bot.list_sessions(show_all=True)
        h = [s for s in res if s["id"] == "h-w1.p2"][0]
        self.assertEqual((h["kind"], h["source"], h["state"]), ("codex", "herdr", "working"))
        self.assertEqual(h["name"], "Codex: proj")
        self.assertEqual(h["detail"], "2. No")
        self.assertIn("a", [s["id"] for s in res])
        self.assertIn("h-w1.p2", e.bot.last_full)
        fake.status = "idle"
        self.assertEqual([s for s in e.bot.list_sessions(True) if s["id"] == "h-w1.p2"][0]["state"], "blocked")
        fake.status = "done"
        self.assertEqual([s for s in e.bot.list_sessions(True) if s["id"] == "h-w1.p2"][0]["state"], "done")

    def test_detail_read_not_more_than_every_10s(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        t = {"v": 1000.0}
        e.bot.clock = lambda: t["v"]
        e.bot.list_sessions(True)
        e.bot.list_sessions(True)
        self.assertEqual(fake.methods().count("pane.read"), 1)

    def test_server_down_claude_list_is_fine(self):
        e = Env(self)
        e.bot.herdr = cc.HerdrClient(os.path.join(e.tmp.name, "none.sock"))
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        res = e.bot.list_sessions(True)
        self.assertEqual([s["id"] for s in res], ["a"])
        # the socket file exists but nobody listens
        dead = os.path.join(e.tmp.name, "dead.sock")
        s = socket.socket(socket.AF_UNIX)
        s.bind(dead)
        s.close()
        e.bot.herdr = cc.HerdrClient(dead)
        self.assertEqual([x["id"] for x in e.bot.list_sessions(True)], ["a"])
        e.bot.herdr = cc.HerdrClient(dead, mode="off")
        self.assertFalse(e.bot.herdr.enabled())

    def test_client_error_reply(self):
        fake = FakeHerdr(self)
        with self.assertRaises(cc.HerdrError) as cm:
            cc.HerdrClient(fake.path).call("nope")
        self.assertEqual(cm.exception.code, "bad_method")

    def test_send_text_sends_text_then_enter(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        ok, err = e.bot.injector("h-w1.p2", "hello")
        self.assertEqual((ok, err), (True, ""))
        self.assertEqual(fake.requests, [("pane.send_text", {"pane_id": "w1:p2", "text": "hello"}),
                                         ("pane.send_keys", {"pane_id": "w1:p2", "keys": ["enter"]})])
        fake.close()
        ok, err = cc.Tmux("claude", lambda *a, **k: (1, "")).send_text("h-w1.p2", "x")
        self.assertFalse(ok)  # no client at all -> a readable error, not a tmux call

    def test_screen_text_reads_visible(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        self.assertIn("Do you want to proceed?", e.bot.screen_text("h-w1.p2"))
        self.assertEqual(fake.requests[-1][1]["source"], "visible")
        fake.close()
        self.assertIn("herdr не отвечает", e.bot.screen_text("h-w1.p2"))

    def test_press_key_translates_names(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        e.bot.known["h-w1.p2"] = {"id": "h-w1.p2", "source": "herdr", "name": "x"}
        for k in ("up", "esc", "ent", "2"):
            e.bot.press_key(1, "h-w1.p2", k, None)
        keys = [p["keys"][0] for m, p in fake.requests if m == "pane.send_keys"]
        self.assertEqual(keys, ["up", "esc", "enter", "2"])
        code, res = e.bot.api_key("h-w1.p2", "down")
        self.assertEqual(code, 200)
        self.assertTrue(res["ok"])
        self.assertIn("Do you want", res["screen"])

    def test_card_and_notify_hide_unsupported_actions(self):
        m = cc.card_markup("h-w1.p2", current=True, watched=False, topic=True)
        acts = [b["callback_data"].split(":")[0] for r in m["inline_keyboard"] for b in r]
        for gone in ("last", "stp", "rn", "cm", "ch"):
            self.assertNotIn(gone, acts)
        self.assertIn("scr", acts)
        self.assertIn("watch", acts)
        n = cc.notify_markup({"id": "h-w1.p2", "state": "blocked"})
        self.assertEqual([b["callback_data"].split(":")[0] for r in n["inline_keyboard"] for b in r], ["go", "scr"])
        self.assertTrue(any(b["callback_data"].startswith("last:") for r in cc.card_markup("abcd1234")["inline_keyboard"]
                            for b in r))

    def test_api_rows_carry_kind_and_source(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.list_sessions(True)
        rows = {r["id"]: r for r in e.bot.api_sessions(1)["sessions"]}
        self.assertEqual((rows["h-w1.p2"]["kind"], rows["h-w1.p2"]["source"], rows["h-w1.p2"]["herdr"]),
                         ("codex", "herdr", True))
        self.assertEqual((rows["a"]["kind"], rows["a"]["source"], rows["a"]["herdr"]), ("claude", "claude", False))
        code, one = e.bot.api_dispatch(1, "GET", "/api/session/h-w1.p2", None)
        self.assertEqual((code, one["source"]), (200, "herdr"))
        code, _ = e.bot.api_dispatch(1, "POST", "/api/session/h-w1.p2/command", {"text": "/model"})
        self.assertEqual(code, 200)

    def test_blocked_after_working_notifies_once(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        e.bot.set_watch(1, "h-w1.p2", True)
        e.bot.notify_tick()  # primes: working
        fake.status = "blocked"
        ev = {"event": "pane.agent_status_changed", "data": {"pane_id": "w1:p2", "agent_status": "blocked"}}
        e.bot._hd_status["w1:p2"] = "working"
        self.assertTrue(e.bot.herdr_on_event(ev))
        e.bot.notify_tick()  # the poll loop sees the same state: nothing new
        self.assertFalse(e.bot.herdr_on_event(ev))
        sm = e.tg.of("sendMessage")
        self.assertEqual(len(sm), 1)
        self.assertIn("Codex: proj", sm[0]["text"])
        self.assertIn("Do you want to proceed?", sm[0]["text"])
        self.assertIn("<pre>", sm[0]["text"])

    def test_new_idle_pane_is_not_a_transition(self):
        fake = FakeHerdr(self)
        e = herdr_env(self, fake)
        fake.status = "idle"
        e.bot.set_watch(1, "h-w1.p2", True)
        e.bot.notify_tick()  # primes
        e.bot.store.data["states"].pop("h-w1.p2", None)  # as if the pane appeared after priming
        e.bot.notify_tick()
        self.assertEqual(e.tg.of("sendMessage"), [])

    def test_subscription_stream_and_resubscribe(self):
        fake = FakeHerdr(self)
        fake.events = [{"event": "pane_agent_status_changed",
                        "data": {"pane_id": "w1:p2", "agent_status": "blocked"}},
                       {"event": "pane_created", "data": {"pane_id": "w1:p3"}}]
        e = herdr_env(self, fake)
        got = []
        e.bot.herdr_on_event = lambda ev: got.append(ev["event"])
        e.bot.herdr_watch_once()  # returns on pane_created
        self.assertEqual(got, ["pane_agent_status_changed"])
        sub = [p for m, p in fake.requests if m == "events.subscribe"][0]["subscriptions"]
        self.assertIn({"type": "pane.agent_status_changed", "pane_id": "w1:p2"}, sub)
        self.assertIn({"type": "pane.created"}, sub)


if __name__ == "__main__":
    unittest.main()


class UnansweredAskTests(unittest.TestCase):
    def _write(self, lines):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for obj in lines:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    @staticmethod
    def ask(tid):
        return {"type": "assistant", "uuid": "u-" + tid, "message": {"content": [
            {"type": "tool_use", "id": tid, "name": "AskUserQuestion", "input": {"questions": [
                {"question": "Чай или кофе?", "header": "Напиток", "multiSelect": False,
                 "options": [{"label": "Чай"}, {"label": "Кофе"}]}]}}]}}

    @staticmethod
    def answer(tid):
        return {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": '"Чай или кофе?"="Чай"'}]}}

    def test_pending_found(self):
        path = self._write([self.ask("t1"), self.answer("t1"), self.ask("t2")])
        ev = cc.unanswered_ask(path)
        self.assertEqual(ev["tool_id"], "t2")

    def test_answered_none(self):
        path = self._write([self.ask("t1"), self.answer("t1")])
        self.assertIsNone(cc.unanswered_ask(path))

    def test_missing_file(self):
        self.assertIsNone(cc.unanswered_ask("/nonexistent/x.jsonl"))
        self.assertIsNone(cc.unanswered_ask(None))


class BusyStatusTests(unittest.TestCase):
    def test_stale_working_state_but_idle_process_is_not_busy(self):
        res = cc.merge_sessions([{"id": "a1", "pid": 1, "state": "working", "status": "idle", "name": "x"}], "/nonexistent")
        self.assertIs(res[0]["busy"], False)

    def test_missing_status_keeps_none(self):
        res = cc.merge_sessions([{"id": "a1", "pid": 1, "state": "working", "name": "x"}], "/nonexistent")
        self.assertIsNone(res[0]["busy"])


class StaleWorkingTests(unittest.TestCase):
    def test_idle_process_with_stale_working_counts_as_waiting(self):
        res = cc.merge_sessions([{"id": "a1", "pid": 1, "state": "working", "status": "idle", "name": "x"}], "/nonexistent")
        self.assertEqual(res[0]["state"], "blocked")

    def test_busy_process_stays_working(self):
        res = cc.merge_sessions([{"id": "a1", "pid": 1, "state": "working", "status": "busy", "name": "x"}], "/nonexistent")
        self.assertEqual(res[0]["state"], "working")


class LiveStateTests(unittest.TestCase):
    """state in state.json lags (rewritten with the summary); tempo/inFlight and the process status are live (owner 27.10)."""
    CASES = {
        # a new turn started: tempo active, state still from the previous turn
        "turn": ({"status": "busy", "state": "working"}, {"state": "blocked", "tempo": "active"}, "working", "work"),
        # the process is busy with its background shells, the last summary said done
        "bg": ({"status": "busy", "state": "working"}, {"state": "done", "tempo": "idle",
                "inFlight": {"tasks": 2, "kinds": ["local_bash"]}}, "working", "work"),
        # idle between its own scheduled checks — not waiting for the owner
        "cron": ({"status": "idle", "state": "working"}, {"state": "working", "tempo": "idle",
                  "inFlight": {"tasks": 3, "kinds": ["session_cron"]}}, "working", "work"),
        # turn aborted with Esc: working left behind, nothing in flight — waiting for the owner
        "esc": ({"status": "idle", "state": "working"}, {"state": "working", "tempo": "idle",
                 "inFlight": {"tasks": 0, "kinds": []}}, "blocked", "wait"),
        "asks": ({"status": "idle", "state": "blocked"}, {"state": "blocked", "tempo": "idle"}, "blocked", "wait"),
        # asked the owner while its background shell still runs: the question wins
        "asks_bg": ({"status": "busy", "state": "blocked"}, {"state": "blocked", "tempo": "blocked",
                     "inFlight": {"tasks": 1, "kinds": ["local_bash"]}}, "blocked", "wait"),
    }

    def env(self):
        e = Env(self)
        e.agents = []
        for sid, (ag, st, _, _) in self.CASES.items():
            e.job(sid, name=sid, cwd="/tmp", updatedAt="2026-10-27T01:00:00Z", **st)
            e.agents.append(dict(ag, id=sid, pid=1, name=sid))
        return e

    def test_list_uses_live_state(self):
        e = self.env()
        got = {x["id"]: x["state"] for x in cc.merge_sessions(e.agents, e.jobs)}
        self.assertEqual(got, {sid: c[2] for sid, c in self.CASES.items()})

    def test_mini_app_uses_live_state(self):
        e = self.env()
        e.bot.list_sessions(True)
        got = {r["id"]: (r["state"], r["group"]) for r in e.bot.api_sessions(1)["sessions"]}
        self.assertEqual(got, {sid: (c[2], c[3]) for sid, c in self.CASES.items()})

    def test_turn_end_reaches_the_mini_app_with_the_next_list(self):
        e = self.env()
        e.bot.list_sessions(True)
        self.assertEqual(e.bot.session_info("turn")["state"], "working")
        e.job("turn", name="turn", cwd="/tmp", updatedAt="2026-10-27T01:01:00Z", state="blocked", tempo="idle")
        e.agents[0].update({"status": "idle", "state": "blocked"})
        e.bot.list_sessions(True)  # notify_tick refreshes the list every 7 s
        self.assertEqual(e.bot.session_info("turn")["state"], "blocked")

    def test_agents_state_beats_a_stale_state_json(self):
        # the session reported done, then went on with its own checks: claude agents says working
        e = self.env()
        e.job("cron", name="cron", cwd="/tmp", updatedAt="2026-10-27T01:00:00Z", state="done", tempo="idle",
              inFlight={"tasks": 1, "kinds": ["session_cron"]})
        listed = {x["id"]: x["state"] for x in e.bot.list_sessions(True)}
        self.assertEqual((listed["cron"], e.bot.session_info("cron")["state"]), ("working", "working"))


class ConfirmDialogMenuTests(unittest.TestCase):
    def test_effort_confirm_dialog_is_menu(self):
        pane = ("● мини-апп\n▔▔▔▔\n   Change effort level?\n   Your next response will be slower\n"
                "   ❯ 1. Yes, switch to low\n     2. No, go back\n")
        self.assertTrue(cc.menu_active(pane))

    def test_plain_prompt_is_not_menu(self):
        self.assertFalse(cc.menu_active("● Готово\n────\n❯ \n────\n  ⏵⏵ bypass permissions on\n"))

    def test_history_numbered_reply_far_above_is_not_menu(self):
        pane = "❯ 1. старый ответ в истории\n" + "\n".join("строка %d" % i for i in range(20)) + "\n❯ \n"
        self.assertFalse(cc.menu_active(pane))

    def test_user_numbered_message_above_prompt_is_not_menu(self):
        pane = "❯ 1. сделай X\n● Сделал\n✻ done\n────\n❯ \n────\n  ⏵⏵ bypass\n"
        self.assertFalse(cc.menu_active(pane))


class QueuedHistoryTests(unittest.TestCase):
    @staticmethod
    def lines(*objs):
        return ("\n".join(json.dumps(o, ensure_ascii=False) for o in objs) + "\n").encode()

    def test_enqueue_then_absorbed_shows_once_not_queued(self):
        data = self.lines(
            {"type": "queue-operation", "operation": "enqueue", "timestamp": "t1", "content": "второе"},
            {"type": "queue-operation", "operation": "remove", "timestamp": "t2", "content": "второе", "reason": "absorbed_mid_turn"},
            {"type": "attachment", "uuid": "a1", "attachment": {"type": "queued_command", "prompt": "второе"}},
            {"type": "user", "uuid": "u1", "origin": {"kind": "human"}, "message": {"content": "второе"}})
        h = cc.parse_history(data)
        self.assertEqual([(m["role"], m["text"], m.get("queued")) for m in h], [("user", "второе", False)])

    def test_sdk_turn_without_origin_ends_the_queue(self):
        data = self.lines(
            {"type": "queue-operation", "operation": "enqueue", "timestamp": "t1", "content": "сделай X"},
            {"type": "queue-operation", "operation": "dequeue", "timestamp": "t2"},
            {"type": "user", "uuid": "u1", "message": {"role": "user", "content": "сделай X"}})
        h = cc.parse_history(data)
        self.assertEqual([(m["role"], m["text"], m.get("queued")) for m in h], [("user", "сделай X", False)])

    def test_pending_queue_is_marked(self):
        h = cc.parse_history(self.lines({"type": "queue-operation", "operation": "enqueue", "timestamp": "t", "content": "жду"}))
        self.assertTrue(h[0]["queued"])

    def test_queued_command_alone_is_user_message(self):
        h = cc.parse_history(self.lines({"type": "attachment", "uuid": "a", "attachment": {"type": "queued_command", "prompt": "посреди хода"}}))
        self.assertEqual(h[0]["text"], "посреди хода")

    def test_queue_hint_is_not_foreign_input(self):
        self.assertEqual(cc.input_text("● работаю\n────\n❯ Press up to edit queued messages\n────\n"), "")


class HistoryWindowTests(unittest.TestCase):
    def test_huge_image_tail_does_not_hide_conversation(self):
        d = tempfile.mkdtemp(); self.addCleanup(lambda: __import__("shutil").rmtree(d))
        path = os.path.join(d, "t.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for i in range(30):
                f.write(json.dumps({"type": "user", "uuid": "u%d" % i, "origin": {"kind": "human"}, "message": {"content": "вопрос %d" % i}}, ensure_ascii=False) + "\n")
                f.write(json.dumps({"type": "assistant", "uuid": "a%d" % i, "message": {"content": [{"type": "text", "text": "ответ %d" % i}]}}, ensure_ascii=False) + "\n")
            big = "x" * (700 * 1024)
            for i in range(3):  # картинки в хвосте: больше HISTORY_TAIL
                f.write(json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t", "content": [{"type": "image", "source": {"data": big}}]}]}}) + "\n")
        b = cc.Bot.__new__(cc.Bot)
        b._hist = {}
        b.cfg = type("C", (), {"home": d})()
        b.transcript_path = lambda sid: path
        h = cc.Bot.api_history(b, "s")
        if h is None:
            self.skipTest("имя метода истории другое")
        self.assertGreaterEqual(len(h), 10)
        self.assertEqual(h[-1]["text"] if h[-1]["role"] == "user" else h[-1]["role"], "assistant")


class CcSendHistoryTests(unittest.TestCase):
    def test_cc_send_bash_call_shows_file_chip(self):
        d = tempfile.mkdtemp(); self.addCleanup(lambda: __import__("shutil").rmtree(d))
        f = os.path.join(d, "shot.png"); open(f, "wb").write(b"\x89PNG")
        line = json.dumps({"type": "assistant", "uuid": "a", "message": {"content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "cc-send %s \"подпись\"" % f}}]}})
        h = cc.parse_history((line + "\n").encode(), home=d)
        self.assertEqual(h[0]["files"][0]["name"], "shot.png")
        self.assertEqual(h[0]["files"][0]["kind"], "image")


class LooksLikePromptTests(unittest.TestCase):
    def test_prompt_like_names(self):
        self.assertTrue(cc.looks_like_prompt("изучи и разбери, как создать аналогичный продукт - https://x", ""))
        self.assertTrue(cc.looks_like_prompt("design system lead setup (2)", ""))
        self.assertTrue(cc.looks_like_prompt("russian language testing", ""))
        self.assertTrue(cc.looks_like_prompt("NEW-SESSION-PROMPT.md", ""))
        self.assertTrue(cc.looks_like_prompt("Сделай отчёт по", "Сделай отчёт по продажам"))

    def test_good_names_kept(self):
        for n in ("Аналитика: доработка", "Проба: переименование", "Продукт: план", "bot"):
            self.assertFalse(cc.looks_like_prompt(n, "что-то другое"), n)


class ChainLabelInRealNameTests(unittest.TestCase):
    def test_strip_chain_label(self):
        self.assertEqual(cc.strip_chain_label("🗂 Аналитика: бэкенд №2"), "Аналитика: бэкенд")
        self.assertEqual(cc.strip_chain_label("Аналитика: доработка №3"), "Аналитика: доработка")
        self.assertEqual(cc.strip_chain_label("Обычное имя"), "Обычное имя")


class StopFailQueueTests(unittest.TestCase):
    def setUp(self):
        self.e = panel_env(self)
        self.q = os.path.join(self.e.tmp.name, "sf", "queue.jsonl")
        os.makedirs(os.path.dirname(self.q))
        self.e.cfg.stopfail_queue = self.q

    def add(self, text, mode="a"):
        with open(self.q, mode, encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def rec(**kw):
        d = {"time": "2026-10-15T10:00:00+00:00", "error": "rate_limit", "details": "limit reached",
             "session_id": "abcdef1234567890", "cwd": "/x", "message": "m"}
        d.update(kw)
        return json.dumps(d) + "\n"

    def stop_msgs(self):
        return [m for m in self.e.tg.of("sendMessage") if m["text"].startswith("⛔ Сессия")]

    def test_first_run_does_not_replay_old(self):
        self.add(self.rec())
        self.e.bot.stopfail_tick()
        self.assertEqual(self.stop_msgs(), [])
        self.add(self.rec(error="billing_error"))
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 1)

    def test_repeated_stop_of_one_session_is_one_message(self):
        now = [1000.0]
        self.e.bot.clock = lambda: now[0]
        self.e.bot.stopfail_tick()  # no file yet: everything later is new
        for _ in range(50):  # a session retrying against a limit writes a record per attempt
            self.add(self.rec())
        self.add(self.rec(session_id="other0000000000"))
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 2)
        now[0] += cc.STOPFAIL_REPEAT - 1
        self.add(self.rec())
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 2)
        now[0] += 2  # long after the last notice: a new stop is news again
        self.add(self.rec())
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 3)

    def test_queue_created_after_start_delivers_its_first_record(self):
        self.e.bot.stopfail_tick()  # no file yet
        self.add(self.rec())
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 1)

    def test_new_line_one_message_then_nothing(self):
        self.e.bot.stopfail_tick()  # no file yet
        self.add("")
        self.e.bot.stopfail_tick()
        self.add(self.rec(details="<b>x</b> " + "я" * 400))
        self.e.bot.stopfail_tick()
        m = self.stop_msgs()
        self.assertEqual(len(m), 1)
        self.assertIn("«abcdef12»", m[0]["text"])
        self.assertIn("лимит (rate_limit)", m[0]["text"])
        self.assertLess(len(m[0]["text"]), 450)
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 1)

    def test_rotation_reads_new_file_from_start(self):
        self.add(self.rec())
        self.e.bot.stopfail_tick()
        os.rename(self.q, self.q + ".1")
        self.add(self.rec(error="billing_error") + self.rec(error="billing_error", session_id="second0000000000"), "w")
        self.e.bot.stopfail_tick()
        m = self.stop_msgs()
        self.assertEqual(len(m), 2)
        self.assertIn("оплата (billing_error)", m[0]["text"])

    def test_broken_line_skipped(self):
        self.add("")
        self.e.bot.stopfail_tick()
        self.add("{не json\n[1]\n" + self.rec())
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 1)

    def test_incomplete_line_waits(self):
        self.add("")
        self.e.bot.stopfail_tick()
        line = self.rec()
        self.add(line[:-1])
        self.e.bot.stopfail_tick()
        self.assertEqual(self.stop_msgs(), [])
        self.add("\n")
        self.e.bot.stopfail_tick()
        self.assertEqual(len(self.stop_msgs()), 1)


class MultiUploadParseTests(unittest.TestCase):
    def test_parse_multiple_files(self):
        b = "XyZ"
        body = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.png\"\r\n\r\nAAA\r\n"
                "--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"b.txt\"\r\n\r\nBB\r\n"
                "--%s\r\nContent-Disposition: form-data; name=\"caption\"\r\n\r\nподпись\r\n--%s--\r\n" % (b, b, b, b)).encode()
        fields, first = cc.parse_multipart("multipart/form-data; boundary=" + b, body)
        self.assertEqual([n for n, _ in fields["_files"]], ["a.png", "b.txt"])
        self.assertEqual(first[0], "a.png")
        self.assertEqual(fields["caption"], "подпись")


class FakeSdk:
    """A fake SDK runner on a Unix socket: keeps sessions, records requests, answers like sdk-protocol.md."""

    def __init__(self, tc, key=True):
        self.dir = tempfile.mkdtemp(prefix="sd")
        tc.addCleanup(self.close)
        self.path = os.path.join(self.dir, "sdk.sock")
        self.requests = []
        self.key = key
        self.events = []
        self.errors = {}   # method -> error code
        self.already = False
        self.start_pending = []
        self.sessions = {"s-1a2b3c4d": self.session()}
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.alive = True
        threading.Thread(target=self.serve, daemon=True).start()

    @staticmethod
    def session(**kw):
        d = {"id": "s-1a2b3c4d", "session_id": "11111111-2222-3333-4444-555555555555", "cwd": "/work/proj",
             "name": "Fix the bug", "model": "sonnet", "state": "working", "detail": "", "budget_usd": 1.0,
             "cost_usd": 0.12, "created": 1000.0, "updated": 1042.0, "pending": [], "always": []}
        d.update(kw)
        return d

    def close(self):
        self.alive = False
        try:
            self.srv.close()
        except OSError:
            pass
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.split(b"\n")[0])
            m, p = req["method"], req.get("params") or {}
            self.requests.append((m, p))
            if m in self.errors:
                c.sendall(line({"id": req["id"], "error": {"code": self.errors[m], "message": m}}))
                return
            sess = self.sessions.get(p.get("id"))
            if m == "runner.info":
                res = {"version": "1", "sdk_version": "1", "key": self.key}
            elif m == "session.list":
                res = {"sessions": list(self.sessions.values())}
            elif m == "session.get":
                res = {"session": sess}
            elif m == "session.start":
                s = self.session(id="s-aabbccdd", cwd=p["cwd"], name=p["prompt"][:60], pending=self.start_pending)
                self.sessions[s["id"]] = s
                res = {"session": s}
            elif m in ("session.send", "session.budget"):
                res = {"session": sess}
            elif m == "session.answer":
                res = {"ok": True, "already": True} if self.already else {"ok": True}
            elif m in ("session.stop", "session.interrupt"):
                res = {"ok": True}
            elif m == "events.subscribe":
                c.sendall(line({"id": req["id"], "result": {"subscribed": True}}))
                for ev in self.events:
                    c.sendall(line(ev))
                time.sleep(0.3)
                return
            else:
                c.sendall(line({"id": req["id"], "error": {"code": "bad_request", "message": m}}))
                return
            c.sendall(line({"id": req["id"], "result": res}))
        finally:
            c.close()

    def methods(self):
        return [m for m, _ in self.requests]

    def calls(self, method):
        return [p for m, p in self.requests if m == method]


SID = "s-1a2b3c4d"


def perm_req(req="r-00000001", tool="Bash", inp=None, can_always=True):
    return {"req": req, "kind": "perm", "tool": tool, "tool_use_id": "toolu_" + req, "input": inp or {"command": "ls -la"},
            "title": None, "can_always": can_always, "created": 1.0}


def sdk_env(tc, fake, watch=True):
    e = Env(tc)
    e.bot.sdk = cc.SdkClient(fake.path)
    e.bot.injector = e.bot.tmux.send_text  # the real Tmux path: SDK ids go to the runner
    e.agents = []
    if watch:
        e.bot.set_watch(1, SID, True)
    e.bot.list_sessions(True)
    return e


def buttons(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


class StartingIsNotWaitingTests(unittest.TestCase):
    def test_booting_session_is_not_waiting_and_later_wait_still_notifies(self):
        boot = {"id": "a", "state": "blocked", "detail": "starting…"}
        out, prev = cc.find_transitions({}, [boot])
        self.assertEqual(out, [])
        self.assertEqual(prev["a"], "working")
        real = {"id": "a", "state": "blocked", "detail": "question: X?"}
        out, prev = cc.find_transitions(prev, [real])
        self.assertEqual([s["id"] for s in out], ["a"])


class StoreRaceTests(unittest.TestCase):
    def test_save_retries_when_another_thread_changes_the_data(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        st = cc.Store(os.path.join(tmp.name, "s", "state.json"))
        calls = []

        def flaky(real):
            def f(*a, **k):
                calls.append(1)
                if len(calls) == 1:
                    raise RuntimeError("dictionary changed size during iteration")
                return real(*a, **k)
            return f
        with mock.patch.object(cc.json, "dumps", flaky(json.dumps)), mock.patch.object(cc.json, "dump", flaky(json.dump)):
            st.data["x"] = 1
            st.save()  # must not raise
        with open(st.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["x"], 1)


class SdkTests(unittest.TestCase):
    def test_budget_detail_is_localized(self):
        d = cc.sdk_session_dict({"id": "s-0123abcd", "state": "budget", "detail": "budget of 0.00 USD exhausted",
                                 "cost_usd": 0.0031, "budget_usd": 0.003})
        self.assertNotIn("USD exhausted", d["detail"])
        self.assertIn("<0.01", d["detail"])

    def test_usd_cents(self):
        self.assertEqual([cc.usd(v) for v in (0, 0.0015, 0.006, 0.123, 1)], ["0.00", "<0.01", "0.01", "0.12", "1.00"])
        self.assertEqual(cc.sdk_cost_line({"cost_usd": 0.0015, "budget_usd": 0.1}), "💲 <0.01 / 0.10 $")

    def last_markup(self, e):
        return [p for p in e.tg.of("sendMessage") if p.get("reply_markup")][-1]["reply_markup"]

    def test_id_format(self):
        self.assertTrue(cc.is_sdk("s-1a2b3c4d"))
        for bad in ("s-1A2B3C4D", "s-1a2b3c4", "s-1a2b3c4d5", "h-w1.p2", "1a2b3c4d", "", None):
            self.assertFalse(cc.is_sdk(bad))

    def test_config(self):
        e = Env(self)
        self.assertEqual(e.cfg.sdk_mode, "auto")
        self.assertTrue(e.cfg.sdk_socket.endswith(".local/state/zidos-pager/sdk.sock"))
        cfg = cc.Config(env_path=os.path.join(e.tmp.name, "env"), environ={"HOME": e.tmp.name, "CC_SDK": "off",
                                                                          "CC_SDK_SOCKET": "/x/y.sock"})
        self.assertEqual((cfg.sdk_mode, cfg.sdk_socket), ("off", "/x/y.sock"))

    def test_list_maps_states_and_cost(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        want = {"working": "working", "blocked": "blocked", "idle": "done", "budget": "blocked", "error": "failed",
                "interrupted": "failed", "stopped": "done"}
        for st, exp in want.items():
            fake.sessions[SID] = fake.session(state=st)
            s = [x for x in e.bot.list_sessions(True) if x["id"] == SID][0]
            self.assertEqual((s["state"], s["source"], s["kind"]), (exp, "sdk", "sdk"), st)
        s = [x for x in e.bot.list_sessions(True) if x["id"] == SID][0]
        self.assertEqual((s["sessionId"], s["cwd"], s["name"], s["cost_usd"], s["budget_usd"], s["pending"]),
                         ("11111111-2222-3333-4444-555555555555", "/work/proj", "Fix the bug", 0.12, 1.0, []))
        fake.sessions[SID] = fake.session(state="budget")
        self.assertIn("1.00", [x for x in e.bot.list_sessions(True) if x["id"] == SID][0]["detail"])
        self.assertIn(SID, e.bot.last_full)
        self.assertEqual(e.bot.session_info(SID)["cost_usd"], 0.12)
        self.assertIn("💲 0.12 / 1.00 $", e.bot.card_text(SID))

    def test_runner_down_list_is_fine(self):
        e = Env(self)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.sdk = cc.SdkClient(os.path.join(e.tmp.name, "none.sock"))
        self.assertEqual([s["id"] for s in e.bot.list_sessions(True)], ["a"])
        dead = os.path.join(e.tmp.name, "dead.sock")
        s = socket.socket(socket.AF_UNIX)
        s.bind(dead)
        s.close()
        e.bot.sdk = cc.SdkClient(dead)
        self.assertEqual([x["id"] for x in e.bot.list_sessions(True)], ["a"])
        self.assertFalse(e.bot.sdk_ready(True))
        e.bot.sdk = cc.SdkClient(dead, mode="off")
        self.assertFalse(e.bot.sdk.enabled())

    def test_send_and_errors(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        self.assertEqual(e.bot.injector(SID, "hello"), (True, ""))
        self.assertEqual(fake.calls("session.send"), [{"id": SID, "text": "hello"}])
        fake.errors["session.send"] = "busy"
        ok, err = e.bot.injector(SID, "again")
        self.assertFalse(ok)
        self.assertIn("занята", err)
        fake.errors["session.send"] = "closed"
        ok, err = e.bot.injector(SID, "again")
        self.assertIn("остановлена", err)
        e.bot.do_send(1, SID, "x")  # the readable error reaches the owner, without a screen button
        sent = e.tg.of("sendMessage")[-1]
        self.assertIn("остановлена", sent["text"])
        self.assertNotIn("reply_markup", sent)

    def test_card_hides_unsupported_buttons(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        data = [b["callback_data"] for b in buttons(cc.card_markup(SID, True, True, True))]
        self.assertEqual({d.split(":")[0] for d in data}, {"watch", "last", "stp", "tpc", "arc"})
        card = buttons(e.bot.card_mk(SID))
        self.assertTrue(any(b["callback_data"] == "sdi:" + SID for b in card))  # working: interrupt
        n = cc.notify_markup({"id": SID, "state": "blocked", "sdk_state": "blocked"})
        self.assertFalse(any(b["callback_data"].split(":")[0] in ("qk", "scr") for b in buttons(n)))
        self.assertIn("Недоступно для SDK", e.bot.screen_text(SID))
        self.assertFalse(e.bot.tmux.send_key(SID, "Up")[0])

    def test_stop_calls_session_stop(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.handle_update(e.cb("stp:" + SID))
        yes = [b for b in buttons(self.last_markup(e)) if b["callback_data"].startswith("stpy")][0]
        e.bot.handle_update(e.cb(yes["callback_data"]))
        self.assertEqual(fake.calls("session.stop"), [{"id": SID}])
        self.assertFalse([r for r in e.runs if "stop" in r[0]])

    def open_perm(self, e, fake, **kw):
        ev = {"event": "request.opened", "data": {"id": SID, "request": perm_req(**kw)}}
        e.bot.sdk_on_event(ev)
        return [p for p in e.tg.of("sendMessage") if "🔐" in p["text"]]

    def test_perm_message_and_buttons(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        msgs = self.open_perm(e, fake)
        self.assertEqual(len(msgs), 1)
        self.assertIn("🔐 Fix the bug: нужно ваше разрешение — Bash", msgs[0]["text"])
        self.assertIn("ls -la", msgs[0]["text"])
        texts = [b["text"] for b in buttons(msgs[0]["reply_markup"])]
        self.assertEqual(texts, ["✅ Разрешить", "♾ Всегда Bash в этой сессии", "✖ Отклонить"])
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in buttons(msgs[0]["reply_markup"])))
        e2 = sdk_env(self, FakeSdk(self))
        m2 = self.open_perm(e2, None, req="r-00000002", can_always=False)
        self.assertEqual(len(buttons(m2[0]["reply_markup"])), 2)

    def test_perm_not_shown_for_ask(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": dict(perm_req(), kind="ask")}})
        self.assertEqual([p for p in e.tg.of("sendMessage") if "🔐" in p["text"]], [])

    def test_perm_input_descriptions(self):
        self.assertTrue(cc.sdk_input_text("Write", {"file_path": "/a/b.txt", "content": "x" * 999}).startswith("/a/b.txt\nx"))
        self.assertEqual(cc.sdk_input_text("Edit", {"file_path": "/a/c.txt"}), "/a/c.txt")
        d = cc.sdk_input_text("WebFetch", {"url": "u" * 900})
        self.assertLessEqual(len(d), cc.SDK_INPUT_LIMIT)
        self.assertLessEqual(len(cc.sdk_input_text("Bash", {"command": "c" * 900})), cc.SDK_INPUT_LIMIT)

    def press(self, e, msg_markup, idx):
        e.bot.handle_update(e.cb(buttons(msg_markup)[idx]["callback_data"], mid=77))

    def test_perm_press_answers_and_edits(self):
        for idx, decision, note in ((0, "allow", "✔ Разрешено"), (1, "always", "♾ Bash разрешён для сессии"),
                                    (2, "deny", "✖ Отклонено")):
            fake = FakeSdk(self)
            e = sdk_env(self, fake)
            msg = self.open_perm(e, fake)[0]
            self.press(e, msg["reply_markup"], idx)
            self.assertEqual(fake.calls("session.answer"), [{"id": SID, "req": "r-00000001", "decision": decision}])
            edit = e.tg.of("editMessageText")[-1]
            self.assertIn(note, edit["text"])
            self.assertNotIn("reply_markup", edit)
            e.bot.handle_update(e.cb(buttons(msg["reply_markup"])[0]["callback_data"], mid=77))  # second press
            self.assertEqual(len(fake.calls("session.answer")), 1)

    def test_perm_already(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        msg = self.open_perm(e, fake)[0]
        fake.already = True
        self.press(e, msg["reply_markup"], 0)
        self.assertIn("Уже решено", e.tg.of("editMessageText")[-1]["text"])

    def test_perm_closed_event_closes_message(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        self.open_perm(e, fake)
        e.bot.sdk_on_event({"event": "request.closed", "data": {"id": SID, "req": "r-00000001", "decision": "deny"}})
        self.assertIn("✖ Отклонено", e.tg.of("editMessageText")[-1]["text"])
        n = len(e.tg.of("editMessageText"))
        e.bot.sdk_on_event({"event": "request.closed", "data": {"id": SID, "req": "r-00000001", "decision": "deny"}})
        self.assertEqual(len(e.tg.of("editMessageText")), n)  # already closed

    def test_reconcile_after_restart_has_no_duplicates(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="blocked", pending=[perm_req()])
        e = sdk_env(self, fake)
        e.bot.sdk_reconcile()
        e.bot.sdk_reconcile()
        self.assertEqual(len([p for p in e.tg.of("sendMessage") if "🔐" in p["text"]]), 1)
        # the same request arrives as an event after the bot had shown it: no second message
        self.open_perm(e, fake)
        self.assertEqual(len([p for p in e.tg.of("sendMessage") if "🔐" in p["text"]]), 1)
        # the request disappeared while the bot was away: the message is closed
        fake.sessions[SID] = fake.session(state="idle", pending=[])
        e.bot.sdk_reconcile()
        self.assertIn("Уже решено", e.tg.of("editMessageText")[-1]["text"])

    def test_watch_once_reads_events(self):
        fake = FakeSdk(self)
        fake.events = [{"event": "request.opened", "data": {"id": SID, "request": perm_req()}}]
        e = sdk_env(self, fake)
        with self.assertRaises(cc.SdkError):
            e.bot.sdk_watch_once()  # ends when the runner closes the connection
        self.assertEqual(len([p for p in e.tg.of("sendMessage") if "🔐" in p["text"]]), 1)

    def ask_env(self, questions, fake=None):
        fake = fake or FakeSdk(self)
        e = sdk_env(self, fake)
        home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, home, True)
        p = mock.patch.dict(os.environ, {"HOME": home})
        p.start()
        self.addCleanup(p.stop)
        path = cc.fallback_transcript_path("/work/proj", "11111111-2222-3333-4444-555555555555")
        self.assertTrue(path.startswith(home))
        os.makedirs(os.path.dirname(path))
        with open(path, "wb") as f:
            f.write(ask_line("toolu_ask", questions))
        self.assertEqual(e.bot.transcript_path(SID), path)
        ev = cc.unanswered_ask(path)
        q, _ = e.bot.register_question(1, SID, ev)
        fake.sessions[SID] = fake.session(state="blocked", pending=[
            {"req": "r-0000000a", "kind": "ask", "tool": "AskUserQuestion", "tool_use_id": "toolu_ask",
             "input": {"questions": questions}, "can_always": False, "created": 1.0}])
        e.bot.post_question(1, q)
        return e, fake, q

    def qpress(self, e, q, action, mid=None):
        e.bot.question_press(1, mid or q["mid"], e.bot.qkey(1, SID, "toolu_ask", action))

    def test_question_single_choice_answers_at_once(self):
        e, fake, q = self.ask_env([Q_SIZE])
        self.qpress(e, q, "o:0:2")
        self.assertEqual(fake.calls("session.answer"), [{"id": SID, "req": "r-0000000a", "decision": "allow",
                                                         "answers": {"Размер?": "M"}}])
        self.assertTrue(q["waiting"])
        self.assertIn("Ответ отправлен", e.tg.of("editMessageText")[-1]["text"])
        self.assertEqual(e.runs and [r for r in e.runs if "send-keys" in r[0]], [])

    def test_question_multi_step_and_submit(self):
        e, fake, q = self.ask_env([Q_SIZE, Q_TOP])
        self.qpress(e, q, "o:0:1")
        self.assertEqual(q["step"], 1)
        self.qpress(e, q, "o:1:1")
        self.qpress(e, q, "o:1:2")
        self.qpress(e, q, "next:1")
        self.assertEqual(q["step"], 2)
        self.assertEqual(fake.calls("session.answer"), [])
        self.qpress(e, q, "sub:2")
        self.assertEqual(fake.calls("session.answer")[0]["answers"], {"Размер?": "S", "Добавки?": "Сыр, Лук"})

    def test_question_own_answer(self):
        e, fake, q = self.ask_env([Q_SIZE])
        ok, err, code = e.bot.question_own_do(q, 0, "  XXL  ")
        self.assertTrue(ok)
        self.assertEqual(fake.calls("session.answer")[0]["answers"], {"Размер?": "XXL"})
        e2, fake2, q2 = self.ask_env([Q_SIZE, Q_TOP])
        self.assertTrue(e2.bot.question_own_do(q2, 0, "XXL")[0])
        self.assertEqual((q2["step"], fake2.calls("session.answer")), (1, []))

    def test_question_cancel_denies(self):
        e, fake, q = self.ask_env([Q_SIZE, Q_TOP])
        self.qpress(e, q, "esc:0")
        c = fake.calls("session.answer")[0]
        self.assertEqual((c["decision"], c["req"]), ("deny", "r-0000000a"))
        self.assertNotIn("answers", c)

    def test_question_without_pending_is_closed(self):
        e, fake, q = self.ask_env([Q_SIZE])
        fake.sessions[SID] = fake.session(state="idle", pending=[])
        self.qpress(e, q, "o:0:1")
        self.assertEqual(fake.calls("session.answer"), [])
        self.assertTrue(q["closed"])
        self.assertIn("закрыт", e.tg.of("editMessageText")[-1]["text"])

    def test_question_via_miniapp_api(self):
        e, fake, q = self.ask_env([Q_SIZE])
        res = e.bot.api_answer(1, SID, {"action": "option", "index": 3})
        self.assertTrue(res["ok"])
        self.assertEqual(fake.calls("session.answer")[0]["answers"], {"Размер?": "L"})

    def test_transcript_text_is_forwarded(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        home = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, home, True)
        with mock.patch.dict(os.environ, {"HOME": home}):
            path = e.bot.transcript_path(SID)
            self.assertTrue(path.startswith(home) and path.endswith("11111111-2222-3333-4444-555555555555.jsonl"))
            os.makedirs(os.path.dirname(path))
            open(path, "wb").close()
            c = e.bot.store.chat(1)
            e.bot.init_fw(c, SID)
            e.bot.start_turn(c, SID)
            with open(path, "ab") as f:
                f.write(asst("u1", "готово, вот ответ"))
            e.bot.forward_tick()
        self.assertTrue([p for p in e.tg.of("sendMessage") if "готово, вот ответ" in p["text"]])

    def test_turn_result_cost_is_not_a_message(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        n = len(e.tg.of("sendMessage"))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": SID, "subtype": "success", "cost_usd": 0.5,
                                                              "is_error": False}})
        self.assertEqual(len(e.tg.of("sendMessage")), n)  # the cost goes to the card and the «done» notification
        self.assertIn("💲 0.50 / 1.00 $", e.bot.card_text(SID))

    def test_budget_button_and_continue(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="budget", cost_usd=1.02)
        e = sdk_env(self, fake)
        btn_ = [b for b in buttons(e.bot.card_mk(SID)) if b["callback_data"] == "sdb:" + SID]
        self.assertEqual(btn_[0]["text"], "+1 $ и продолжить")
        e.bot.handle_update(e.cb("sdb:" + SID))
        self.assertEqual(fake.calls("session.budget"), [{"id": SID, "budget_usd": 2.0}])
        self.assertEqual(fake.calls("session.send"), [{"id": SID, "text": "continue"}])
        self.assertLess(fake.methods().index("session.budget"), fake.methods().index("session.send"))
        fake.sessions[SID] = fake.session(state="interrupted")
        e.bot.list_sessions(True)
        self.assertTrue([b for b in buttons(e.bot.card_mk(SID)) if b["callback_data"] == "sdc:" + SID])
        e.bot.handle_update(e.cb("sdc:" + SID))
        self.assertEqual(len(fake.calls("session.send")), 2)
        self.assertEqual(len(fake.calls("session.budget")), 1)

    def test_always_list_in_card(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(always=["Write", "Bash"])
        e = sdk_env(self, fake)
        self.assertIn("♾ Write, Bash", e.bot.card_text(SID))

    def test_new_button_visibility(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.new_dialog(1)
        self.assertIn("nds", [b["callback_data"] for b in buttons(self.last_markup(e))])
        fake.key = False
        e.bot.new_dialog(1)
        self.assertNotIn("nds", [b["callback_data"] for b in buttons(self.last_markup(e))])
        fake.key = True
        e.bot.sdk = cc.SdkClient(os.path.join(e.tmp.name, "none.sock"))
        e.bot.new_dialog(1)
        self.assertNotIn("nds", [b["callback_data"] for b in buttons(self.last_markup(e))])

    def test_new_session_via_dialog(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake, watch=False)
        e.bot.new_dialog(1)
        e.bot.handle_update(e.cb("nds"))
        e.bot.handle_update(e.cb("nd:0"))
        self.assertEqual(e.bot.store.chat(1)["pending_new"]["sdk"], True)
        e.bot.handle_update(e.msg("сделай X", mid=5))
        self.assertEqual(fake.calls("session.start"), [{"cwd": e.cfg.default_cwd, "prompt": "сделай X"}])
        self.assertTrue(e.bot.is_watched("s-aabbccdd"))
        self.assertEqual(e.bot.store.chat(1)["session"], "s-aabbccdd")
        self.assertFalse([r for r in e.runs if "--bg" in r[0]])

    def test_new_session_creates_topic_and_watch(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake, watch=False)
        e.bot.topics_enabled = True
        sid = e.bot.do_new(1, "/work/proj", "task", True)
        self.assertEqual(sid, "s-aabbccdd")
        self.assertIn(sid, e.bot.store.chat(1)["topics"])
        self.assertTrue(e.bot.is_watched(sid))
        self.assertIn(sid, e.bot.store.chat(1)["fw"])

    def test_api_new_sdk_flag(self):
        fake = FakeSdk(self, key=False)
        e = sdk_env(self, fake, watch=False)
        code, res = e.bot.api_new(1, {"text": "t", "cwd": e.tmp.name, "sdk": True})
        self.assertEqual(code, 400)
        fake.key = True
        code, res = e.bot.api_new(1, {"text": "t", "cwd": e.tmp.name, "sdk": True})
        self.assertEqual((code, res["id"]), (200, "s-aabbccdd"))


class SdkReviewTests(unittest.TestCase):
    def perms(self, e):
        return [p for p in e.tg.of("sendMessage") if "нужно ваше разрешение" in p["text"]]

    def test_reconcile_runs_after_subscribe_ack(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        fake.requests.clear()
        with self.assertRaises(cc.SdkError):
            e.bot.sdk_watch_once()
        ms = fake.methods()
        self.assertLess(ms.index("events.subscribe"), ms.index("session.list"))

    def test_new_sdk_session_shows_pending_perm(self):
        fake = FakeSdk(self)
        fake.start_pending = [perm_req()]
        e = sdk_env(self, fake, watch=False)
        sid = e.bot.do_new(1, "/work/proj", "task", True)
        self.assertEqual(len(self.perms(e)), 1)
        self.assertIn("ls -la", self.perms(e)[0]["text"])

    def test_follow_shows_pending_perm(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="blocked", pending=[perm_req()])
        e = sdk_env(self, fake, watch=False)
        e.bot.handle_update(e.cb("watch:" + SID))
        self.assertEqual(len(self.perms(e)), 1)

    def test_stale_buttons_do_not_call_runner(self):
        for kind, state in (("sdb", "working"), ("sdc", "working"), ("sdi", "idle"), ("sdb", "idle")):
            fake = FakeSdk(self)
            fake.sessions[SID] = fake.session(state=state)
            e = sdk_env(self, fake)
            fake.requests.clear()
            e.bot.handle_update(e.cb("%s:%s" % (kind, SID)))
            self.assertEqual([m for m in fake.methods() if m not in ("session.get", "session.list")], [], kind + state)
            self.assertTrue(any("устарела" in t for t, _ in popups(e)))
            self.assertEqual(e.tg.of("sendMessage"), [])
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="stopped")
        e = sdk_env(self, fake)
        e.bot.handle_update(e.cb("sdc:" + SID))
        self.assertEqual(len(fake.calls("session.send")), 1)

    def test_stopped_card_has_continue(self):
        rows = cc.sdk_action_rows({"id": SID, "sdk_state": "stopped"})
        self.assertEqual(rows[0][0]["callback_data"], "sdc:" + SID)

    def test_notify_only_when_displayed_state_changes(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        with mock.patch.object(e.bot, "notify_tick") as nt:
            e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(cost_usd=0.5)}})
            self.assertEqual(nt.call_count, 0)
            e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(state="blocked")}})
            self.assertEqual(nt.call_count, 1)  # working -> blocked is a displayed change
            e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(state="budget")}})
            self.assertEqual(nt.call_count, 1)  # both show as blocked
            e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(state="idle")}})
            self.assertEqual(nt.call_count, 2)

    def test_eviction_drops_closed_first(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        self.open_n(e, 1)
        first = list(e.bot.sdk_perms().values())[0]
        for i in range(2, cc.SDK_PERM_KEEP + 1):
            self.open_n(e, i)
        e.bot.sdk_close_perm(SID, "r-%08d" % 5, "allow")
        self.open_n(e, 1000)
        keys = [r["req"] for r in e.bot.sdk_perms().values()]
        self.assertIn(first["req"], keys)
        self.assertNotIn("r-%08d" % 5, keys)
        self.assertEqual(len(keys), cc.SDK_PERM_KEEP)

    def open_n(self, e, i):
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req(req="r-%08d" % i)}})

    def test_descriptions_show_content(self):
        d = cc.sdk_input_text("Write", {"file_path": "/a/b.txt", "content": "line1\nSECRET=1"})
        self.assertIn("/a/b.txt", d)
        self.assertIn("SECRET=1", d)
        d = cc.sdk_input_text("Edit", {"file_path": "/a/b.txt", "old_string": "foo", "new_string": "bar"})
        self.assertIn("foo", d)
        self.assertIn("bar", d)
        d = cc.sdk_input_text("MultiEdit", {"file_path": "/a/b.txt", "edits": [
            {"old_string": "x1", "new_string": "y1"}, {"old_string": "x2", "new_string": "y2"}]})
        self.assertIn("2", d)
        self.assertIn("x1", d)
        self.assertLessEqual(len(cc.sdk_input_text("Write", {"file_path": "/p", "content": "c" * 5000})),
                             cc.SDK_INPUT_LIMIT)

    def test_unarchive_stopped_session_offers_continue(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        ok, stopped, err = e.bot.archive_session(SID)
        self.assertTrue(ok)
        fake.sessions[SID] = fake.session(state="stopped")
        e.bot.list_sessions(True)
        e.bot.unarchive_session(SID)
        self.assertTrue([b for b in buttons(e.bot.card_mk(SID, archived=False)) if b["callback_data"] == "sdc:" + SID])


class SdkOnlyTests(unittest.TestCase):
    """No Claude Code on the machine: only the SDK runner (and herdr) feed the bot."""

    def env(self, fake=None, mode="auto"):
        e = Env(self)
        if fake is not None:
            e.bot.sdk = cc.SdkClient(fake.path)
        e.bot.injector = e.bot.tmux.send_text
        e.cfg.claude_bin = os.path.join(e.tmp.name, "no-such-dir", "claude")
        e.cfg.claude_mode = mode
        del e.bot.claude_missing  # the real check
        self.claude_calls = 0
        base = e.runner

        def runner(args, timeout=30, cwd=None, input_text=None):
            if args and args[0] == e.cfg.claude_bin:
                self.claude_calls += 1
            return base(args, timeout, cwd, input_text)
        e.bot.run = e.bot._claude_guard(runner)
        e.bot.tmux.run = runner
        return e

    def test_missing_binary_is_detected_and_cached(self):
        e = self.env()
        self.assertTrue(e.bot.claude_missing())
        fake_bin = os.path.join(e.tmp.name, "claude")
        open(fake_bin, "w").close()
        os.chmod(fake_bin, 0o755)
        e.cfg.claude_bin = fake_bin
        self.assertTrue(e.bot.claude_missing())  # cached for CLAUDE_CHECK_TTL
        t = {"v": 5000.0}
        e.bot.clock = lambda: t["v"]
        e.bot._claude_chk = (0.0, True)
        self.assertFalse(e.bot.claude_missing())

    def test_missing_is_remembered_briefly_present_for_a_minute(self):
        # 20.10 05:07 an npm update replaced the binary for ~2 s; a cached «missing» then failed a stop for a minute
        e = self.env()
        t = {"v": 5000.0}
        e.bot.clock = lambda: t["v"]
        self.assertTrue(e.bot.claude_missing())
        fake_bin = os.path.join(e.tmp.name, "no-such-dir", "claude")
        os.makedirs(os.path.dirname(fake_bin))
        open(fake_bin, "w").close()
        os.chmod(fake_bin, 0o755)
        t["v"] += cc.CLAUDE_GONE_TTL
        self.assertFalse(e.bot.claude_missing())  # back after seconds, not a minute
        os.unlink(fake_bin)
        t["v"] += cc.CLAUDE_GONE_TTL
        self.assertFalse(e.bot.claude_missing())  # «present» is still cached

    def test_off_behaves_as_missing(self):
        e = self.env(mode="off")
        e.cfg.claude_bin = "/bin/sh"  # exists, but CC_CLAUDE=off wins
        self.assertTrue(e.bot.claude_missing())
        self.assertEqual(e.bot.run([e.cfg.claude_bin, "agents"])[0], 127)
        cfg = cc.Config(env_path=os.path.join(e.tmp.name, "env"), environ={"HOME": e.tmp.name, "CC_CLAUDE": "off"})
        self.assertEqual(cfg.claude_mode, "off")

    def test_panel_lists_sdk_session_without_quota_or_names(self):
        fake = FakeSdk(self)
        e = self.env(fake)
        html, plain, markup = e.bot.panel_render(1)
        self.assertIn("Fix the bug", plain)
        self.assertNotIn("Квота", plain)
        data = [b["callback_data"] for b in buttons(markup)]
        self.assertNotIn("pm:quota", data)
        self.assertNotIn("pm:names", data)
        self.assertIn("pm:help", data)
        self.assertIn("new", data)
        self.assertEqual(self.claude_calls, 0)

    def test_new_session_goes_through_sdk(self):
        fake = FakeSdk(self)
        e = self.env(fake)
        e.bot.new_dialog(1)
        self.assertNotIn("nds", [b["callback_data"] for b in buttons(e.tg.of("sendMessage")[-1]["reply_markup"])])
        e.bot.handle_update(e.cb("nd:0"))
        e.bot.handle_update(e.msg("сделай X", mid=5))
        self.assertEqual(fake.calls("session.start"), [{"cwd": e.cfg.default_cwd, "prompt": "сделай X"}])
        self.assertFalse([r for r in e.runs if "--bg" in r[0]])
        self.assertEqual(self.claude_calls, 0)
        self.assertEqual(e.bot.store.chat(1)["session"], "s-aabbccdd")

    def test_no_backend_shows_hint(self):
        e = self.env(FakeSdk(self, key=False))
        html, plain, markup = e.bot.panel_render(1)
        self.assertIn("Нет Claude Code и не отвечает SDK-раннер", plain)
        e2 = self.env()
        e2.bot.sdk = cc.SdkClient(os.path.join(e2.tmp.name, "none.sock"))
        _, plain, _ = e2.bot.panel_render(1)
        self.assertIn("Нет Claude Code", plain)
        e2.bot.panel_publish(1)
        self.assertEqual(self.claude_calls, 0)

    def test_help_without_tmux_mentions_permissions(self):
        e = self.env(FakeSdk(self))
        h = e.bot.help_text()
        self.assertIn("🔐", h)
        self.assertNotIn("tmux", h.lower())
        self.assertNotIn("Экран", h)

    def test_quota_command_is_declined(self):
        e = self.env(FakeSdk(self))
        e.bot.send_quota(1)
        self.assertIn("Квота подписки недоступна", e.tg.of("sendMessage")[-1]["text"])

    def test_ticks_never_call_claude(self):
        fake = FakeSdk(self)
        e = self.env(fake)
        e.bot.set_watch(1, SID, True)
        for fn in (e.bot.panel_tick, e.bot.notify_tick, e.bot.names_tick, e.bot.forward_tick, e.bot.status_tick,
                   e.bot.chains_scan, lambda: e.bot.stop_session("abcd1234"), lambda: e.bot.start_generation(True)):
            fn()
        self.assertEqual(self.claude_calls, 0)
        self.assertEqual([r for r in e.runs if r[0] and r[0][0] == e.cfg.claude_bin], [])

    def test_claude_present_failing_list_is_still_an_error(self):
        e = Env(self)
        e.bot.run = lambda *a, **k: (1, "")
        self.assertIsNone(e.bot.list_sessions(True))


def popups(e):
    return [(p.get("text"), p.get("show_alert")) for p in e.tg.of("answerCallbackQuery") if p.get("text")]


class NoClutterTests(unittest.TestCase):
    """The chat is not a log: one live message per entity, temporary replies are popups."""

    # ---- 1. popups instead of messages
    def test_outdated_perm_button_is_a_popup(self):
        e = sdk_env(self, FakeSdk(self))
        n = len(e.tg.of("sendMessage"))
        e.bot.handle_update(e.cb("pr:999"))
        self.assertEqual(len(e.tg.of("sendMessage")), n)
        self.assertTrue(any("устарела" in t for t, _ in popups(e)))
        self.assertEqual(len(e.tg.of("answerCallbackQuery")), 1)

    def test_stop_result_is_a_popup_and_dialog_is_deleted(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.handle_update(e.cb("stp:" + SID))
        n = len(e.tg.of("sendMessage"))
        dialog = e.tg.of("sendMessage")[-1]
        cancel = buttons(dialog["reply_markup"])[-1]
        self.assertEqual(cancel["callback_data"], "cl")  # cancel deletes the dialog
        e.bot.handle_update(e.cb("stpy:" + SID, mid=61))
        self.assertEqual(len(e.tg.of("sendMessage")), n)
        self.assertIn(61, [d["message_id"] for d in e.tg.of("deleteMessage")])
        self.assertTrue(any("Остановлено" in t and not a for t, a in popups(e)))
        fake.errors["session.stop"] = "closed"
        e.tg.calls.clear()
        e.bot.handle_update(e.cb("stpy:" + SID, mid=62))
        self.assertEqual(e.tg.of("sendMessage"), [])
        self.assertTrue(any("Не получилось остановить" in t and a for t, a in popups(e)))

    def test_no_replies_rn_sdk_write_here_are_popups(self):
        e = sdk_env(self, FakeSdk(self))
        for data, text in (("last:" + SID, "Ответов модели пока нет."), ("rn:" + SID, "Недоступно для SDK"),
                           ("go:" + SID, "Пишите сюда"), ("cur:" + SID, "Теперь пишите")):
            e.tg.calls.clear()
            e.bot.handle_update(e.cb(data))
            self.assertEqual(e.tg.of("sendMessage"), [], data)
            self.assertTrue(any(text in t for t, _ in popups(e)), data)

    def test_sdk_continue_and_interrupt_are_popups(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="stopped")
        e = sdk_env(self, fake)
        e.bot.handle_update(e.cb("sdc:" + SID))
        self.assertEqual(e.tg.of("sendMessage"), [])
        self.assertTrue(any("Продолжаю" in t for t, _ in popups(e)))
        fake.sessions[SID] = fake.session(state="working")
        e.bot.handle_update(e.cb("sdi:" + SID))
        self.assertEqual(e.tg.of("sendMessage"), [])
        self.assertTrue(any("Прервано" in t for t, _ in popups(e)))
        fake.sessions[SID] = fake.session(state="working")
        e.bot.handle_update(e.cb("sdc:" + SID))  # not a continue state any more: outdated
        self.assertEqual(e.tg.of("sendMessage"), [])
        self.assertTrue(any("устарела" in t for t, _ in popups(e)))

    def test_not_sent_error_is_temporary(self):
        e = sdk_env(self, FakeSdk(self))
        e.bot.report_send_error(1, SID, "busy")
        self.assertEqual(len(e.bot.temp), 1)
        e2 = Env(self)
        e2.bot.report_send_error(1, "a", "boom")  # with the Screen button: stays
        self.assertEqual(e2.bot.temp, [])

    # ---- 2. one card per session
    def test_card_is_replaced_not_duplicated(self):
        for topics in (False, True):
            e = topic_env(self, topics)
            e.bot.send_card(1, "a")
            first = e.bot.store.chat(1)["cards"]["main:a"]
            e.tg.calls.clear()
            e.bot.send_card(1, "a")
            second = e.bot.store.chat(1)["cards"]["main:a"]
            self.assertNotEqual(first, second)
            self.assertEqual([d["message_id"] for d in e.tg.of("deleteMessage")], [first])
            self.assertEqual(len(e.tg.of("sendMessage")), 1)

    # ---- 3. one notification per session
    def test_new_notification_deletes_the_previous_one(self):
        e = Env(self)
        e.bot.set_watch(1, "a", True)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.agents = [{"id": "a", "name": "A", "state": "blocked"}]
        e.job("a", state="blocked", detail="ждёт")
        e.bot.notify_tick()
        first = e.tg.of("sendMessage")
        self.assertEqual(len(first), 1)
        self.assertEqual(e.tg.of("deleteMessage"), [])
        first_mid = e.bot.store.chat(1)["notes"]["a"]["mid"]
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.job("a", state="working")
        e.bot.notify_tick()
        e.agents = [{"id": "a", "name": "A", "state": "done"}]
        e.job("a", state="done", output={"result": "итог"})
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), 2)
        self.assertEqual(len(e.tg.of("deleteMessage")), 1)
        self.assertEqual([d["message_id"] for d in e.tg.of("deleteMessage")], [first_mid])
        self.assertNotEqual(e.bot.store.chat(1)["notes"]["a"]["mid"], first_mid)

    def test_sdk_notification_carries_the_cost(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(cost_usd=0.5)
        e = sdk_env(self, fake)
        e.bot.notify_tick()
        fake.sessions[SID] = fake.session(state="idle", cost_usd=0.5)
        e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.sessions[SID]}})
        e.bot.notify_tick()
        sm = [p["text"] for p in e.tg.of("sendMessage")]
        self.assertTrue(sm and "💲 0.50 / 1.00 $" in sm[-1], sm)

    # ---- 4. starting a session: one message
    def test_do_new_sends_only_the_card(self):
        e = Env(self)
        e.bot.do_new(1, "/tmp", "сделай")
        texts = [p["text"] for p in e.tg.of("sendMessage")]
        self.assertFalse([t for t in texts if "Сессия создана" in t])
        self.assertEqual(len([p for p in e.tg.of("sendMessage") if "reply_markup" in p]), len(texts))

    def test_do_new_in_topics_only_card_in_the_topic(self):
        e = panel_env(self)
        e.bot.panel_publish(1)
        e.tg.calls.clear()
        sid = e.bot.do_new(1, "/tmp", "сделай")
        msgs = e.tg.of("sendMessage")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["message_thread_id"], e.bot.store.chat(1)["topics"][sid]["thread"])

    # ---- 5. cost is not a separate message
    def test_turn_result_sends_nothing_but_updates_cost(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        n = len(e.tg.of("sendMessage"))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": SID, "cost_usd": 0.7}})
        self.assertEqual(len(e.tg.of("sendMessage")), n)
        self.assertEqual(e.bot.known[SID]["cost_usd"], 0.7)

    # ---- 6. closed requests are compact; dialogs vanish
    def test_closed_permission_is_one_line(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        msg = [p for p in e.tg.of("sendMessage") if "🔐" in p["text"]][0]
        e.bot.handle_update(e.cb(buttons(msg["reply_markup"])[0]["callback_data"], mid=77))
        edit = e.tg.of("editMessageText")[-1]
        self.assertEqual(edit["text"], "🔐 Bash · ls -la · ✔ Разрешено")
        self.assertNotIn("<pre>", str(edit))

    def test_answered_question_is_compact(self):
        e = Env(self)
        q, _ = e.bot.register_question(1, "a", {"tool_id": "t1", "questions": [
            {"header": "Размер", "question": "Какой размер?", "multi": False,
             "options": [{"label": "S", "description": ""}, {"label": "L", "description": ""}]}]})
        q["mid"] = 40
        e.bot.on_answer({"tool_id": "t1", "content": "User has answered your questions: \"Какой размер?\"=\"L\"."})
        text = e.tg.of("editMessageText")[-1]["text"]
        self.assertTrue(text.startswith("❓ Размер — "), text)
        self.assertNotIn("Какой размер?", text)
        self.assertNotIn("\n", text)

    def test_confirmation_dialogs_are_deleted_after_answer(self):
        e = topic_env(self)
        e.bot.ensure_topic(1, "a")
        e.bot.handle_update(e.cb("tpd:a", mid=63))
        self.assertIn(63, [d["message_id"] for d in e.tg.of("deleteMessage")])
        e2 = Env(self)
        e2.bot.confirm_stop(1, "a")
        cancel = buttons(e2.tg.of("sendMessage")[-1]["reply_markup"])[-1]
        self.assertEqual(cancel["callback_data"], "cl")
        e2.bot.handle_update(e2.cb("stpy:a", mid=64))
        self.assertIn(64, [d["message_id"] for d in e2.tg.of("deleteMessage")])

    # ---- 7. choosing the engine in the panel
    def panel_buttons(self, e):
        return [(b["text"], b["callback_data"]) for r in e.tg.of("editMessageText")[-1]["reply_markup"]["inline_keyboard"]
                for b in r]

    def test_panel_has_new_session_button_and_engine_choice(self):
        e = panel_env(self)
        e.bot.sdk_ready = lambda fresh=False: True
        e.bot.panel_publish(1)
        mid = e.bot.panel_state(1)["mid"]
        sent = [p for p in e.tg.of("sendMessage") if p.get("message_thread_id") == pth(e)][-1]
        self.assertIn(("🆕 Новая сессия", "new"), [(b["text"], b["callback_data"]) for b in buttons(sent["reply_markup"])])
        e.bot.handle_update(e.cb("new", mid=mid))
        self.assertIn("Как запустить?", e.tg.of("editMessageText")[-1]["text"])
        self.assertEqual([t for t, _ in self.panel_buttons(e)],
                         ["▶ Подписка (Claude Code)", "⚡ SDK (ключ API)", "✖ Отмена"])
        e.bot.handle_update(e.cb("neg:sdk", mid=mid))
        self.assertEqual(e.bot.store.chat(1)["new_engine"], "sdk")
        self.assertIn("Напишите задачу в этот топик", e.tg.of("editMessageText")[-1]["text"])
        with mock.patch.object(e.bot, "do_new") as dn:
            e.bot.handle_update(e.msg("Сделай тесты", mid=9, thread=pth(e)))
            dn.assert_called_once_with(1, e.cfg.default_cwd, "Сделай тесты", True)
        self.assertIsNone(e.bot.store.chat(1)["new_engine"])
        self.assertIsNone(e.bot.store.chat(1)["pending_task"])

    def test_subscription_choice_starts_without_second_step(self):
        e = panel_env(self)
        e.bot.sdk_ready = lambda fresh=False: True
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("new"))
        e.bot.handle_update(e.cb("neg:sub"))
        e.bot.handle_update(e.msg("Сделай тесты", mid=9, thread=pth(e)))
        cmd, cwd = [r for r in e.runs if "--bg" in r[0]][-1]
        self.assertEqual((cmd[-1], cwd), ("Сделай тесты", e.cfg.default_cwd))

    def test_choice_cancel_and_no_choice_keeps_two_steps(self):
        e = panel_env(self)
        e.bot.sdk_ready = lambda fresh=False: True
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("new"))
        e.bot.handle_update(e.cb("ncx"))
        self.assertFalse(e.bot.store.chat(1).get("new_engine"))
        self.assertNotIn("Как запустить?", e.tg.of("editMessageText")[-1]["text"])
        e.bot.handle_update(e.msg("Сделай тесты", mid=9, thread=pth(e)))
        self.assertEqual([r for r in e.runs if "--bg" in r[0]], [])
        self.assertIn("▶ Запустить", str(e.tg.of("editMessageText")[-1]["reply_markup"]))

    def test_without_claude_goes_straight_to_sdk_and_without_sdk_to_old_path(self):
        e = panel_env(self)
        e.bot.claude_missing = lambda: True
        e.bot.sdk_ready = lambda fresh=False: True
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("new"))
        self.assertEqual(e.bot.store.chat(1)["new_engine"], "sdk")
        self.assertNotIn("Как запустить?", e.tg.of("editMessageText")[-1]["text"])
        e2 = panel_env(self)
        e2.bot.sdk_ready = lambda fresh=False: False
        e2.bot.panel_publish(1)
        e2.bot.handle_update(e2.cb("new"))
        self.assertFalse(e2.bot.store.chat(1).get("new_engine"))
        self.assertIn("напишите задачу в этот топик", e2.tg.of("editMessageText")[-1]["text"])


class NoClutterReviewTests(unittest.TestCase):
    def test_result_notification_is_not_deleted(self):
        e = Env(self)
        e.bot.set_watch(1, "a", True)
        e.agents = [{"id": "a", "name": "A", "state": "working"}]
        e.bot.notify_tick()
        e.job("a", state="done", output={"result": "итог"})
        e.agents = [{"id": "a", "name": "A", "state": "done"}]
        e.bot.notify_tick()
        self.assertTrue(e.bot.store.chat(1)["notes"]["a"]["final"])
        e.job("a", state="blocked", detail="ждёт")
        e.agents = [{"id": "a", "name": "A", "state": "blocked"}]
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), 2)
        self.assertEqual(e.tg.of("deleteMessage"), [])  # the answer stays

    def test_failed_answer_falls_back_to_message(self):
        e = sdk_env(self, FakeSdk(self))
        e.tg.errors["answerCallbackQuery"] = (lambda p: True, "query is too old")
        self.assertFalse(e.tg.answer("q", "x"))
        e.bot.handle_update(e.cb("go:" + SID))
        self.assertTrue([p for p in e.tg.of("sendMessage") if "Пишите сюда" in p["text"]])
        self.assertEqual(len(e.bot.temp), 1)  # not an alert: temporary
        e.bot.temp.clear()
        e.tg.calls.clear()
        e.bot._cbl.cur = {"id": "q", "done": False}
        e.bot.popup(1, "Ошибка", alert=True)
        e.bot._cbl.cur = None
        self.assertEqual(len(e.tg.of("sendMessage")), 1)
        self.assertEqual(e.bot.temp, [])  # an error is not removed by a timer

    def test_turn_result_refreshes_the_live_card(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.send_card(1, SID)
        mid = e.bot.store.chat(1)["cards"]["main:" + SID]
        n = len(e.tg.of("sendMessage"))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": SID, "cost_usd": 0.9}})
        self.assertEqual(len(e.tg.of("sendMessage")), n)
        ed = e.tg.of("editMessageText")[-1]
        self.assertEqual(ed["message_id"], mid)
        self.assertIn("💲 0.90 / 1.00 $", ed["text"])

    def test_state_change_refreshes_the_live_card(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.send_card(1, SID)
        mid = e.bot.store.chat(1)["cards"]["main:" + SID]
        x = dict(e.bot.sdk.get(SID), state="idle")
        e.bot.sdk_on_event({"event": "session.state", "data": {"session": x}})
        ed = e.tg.of("editMessageText")[-1]
        self.assertEqual(ed["message_id"], mid)
        self.assertNotIn("sdi:", str(ed.get("reply_markup")))  # no "Interrupt" on an idle session

    def test_failed_stop_keeps_the_dialog(self):
        fake = FakeSdk(self)
        fake.errors["session.stop"] = "closed"
        e = sdk_env(self, fake)
        e.bot.handle_update(e.cb("stpy:" + SID, mid=61))
        self.assertNotIn(61, [d["message_id"] for d in e.tg.of("deleteMessage")])
        self.assertTrue(any("Не получилось" in t and a for t, a in popups(e)))

    def test_armed_engine_is_visible_and_expires(self):
        e = panel_env(self)
        e.bot.sdk_ready = lambda fresh=False: True
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("new"))
        e.bot.handle_update(e.cb("neg:sdk"))
        e.bot.panel_flash(1, "x", ttl=1)
        e.t["v"] += 2
        e.bot.panel_tick()
        ed = e.tg.of("editMessageText")[-1]
        self.assertIn("Следующий текст в этом топике запустит сессию (⚡ SDK)", ed["text"])
        self.assertIn("ncx", str(ed["reply_markup"]))
        e.t["v"] += 601
        e.bot.panel_tick()
        self.assertNotIn("Следующий текст", e.tg.of("editMessageText")[-1]["text"])
        with mock.patch.object(e.bot, "do_new") as dn:
            e.bot.handle_update(e.msg("задача", mid=9, thread=pth(e)))
            dn.assert_not_called()  # expired: the two-step path

    def test_cards_of_topic_and_main_chat_are_separate(self):
        e = topic_env(self)
        e.bot.send_card(1, "a")  # no topic yet: main chat
        e.bot.ensure_topic(1, "a")
        e.tg.calls.clear()
        e.bot.send_card(1, "a")  # the card of the topic
        cards = e.bot.store.chat(1)["cards"]
        self.assertEqual(len(cards), 2)
        self.assertEqual(e.tg.of("deleteMessage"), [])
        e.bot.drop_topic(1, "a")
        self.assertEqual(list(e.bot.store.chat(1)["cards"]), ["main:a"])

    def test_permission_line_keeps_what_was_decided(self):
        e = sdk_env(self, FakeSdk(self))
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req(
            inp={"command": "rm -rf build\n" + "x" * 200})}})
        rec = list(e.bot.sdk_perms().values())[0]
        e.bot.sdk_end_perm(rec, "✔ Разрешено")
        t = e.tg.of("editMessageText")[-1]["text"]
        self.assertTrue(t.startswith("🔐 Bash · rm -rf build xxx"))
        self.assertEqual(t.count("\n"), 0)
        self.assertLessEqual(len(t), 120)

    def test_double_press_while_deciding_says_processing(self):
        e = sdk_env(self, FakeSdk(self))
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        msg = [p for p in e.tg.of("sendMessage") if "🔐" in p["text"]][0]
        list(e.bot.sdk_perms().values())[0]["deciding"] = True
        e.bot.handle_update(e.cb(buttons(msg["reply_markup"])[0]["callback_data"], mid=77))
        self.assertTrue(any("Обрабатывается" in t for t, _ in popups(e)))

    def test_new_without_claude_and_without_runner(self):
        e = panel_env(self)
        e.bot.claude_missing = lambda: True
        e.bot.sdk_ready = lambda fresh=False: False
        e.bot.panel_publish(1)
        e.bot.handle_update(e.cb("new"))
        self.assertFalse(e.bot.store.chat(1).get("new_engine"))
        shown = [p["text"] for p in e.tg.of("sendMessage") + e.tg.of("editMessageText")]
        self.assertIn(cc.no_backend_text(), shown[-1])
        self.assertNotIn("Следующий текст", shown[-1])

    def test_popup_text_is_cut_by_utf16_units(self):
        e = Env(self)
        e.tg.answer("q", "😀" * 150)
        text = e.tg.of("answerCallbackQuery")[-1]["text"]
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 200)
        self.assertTrue(text.startswith("😀😀"))


class SilentTests(unittest.TestCase):
    def _env(self, state="working"):
        e = topic_env(self)
        tr = os.path.join(e.tmp.name, "t.jsonl")
        open(tr, "wb").write(asst("old", "старое"))
        e.job("a", name="Альфа", state=state, cwd="/tmp", detail="читает", linkScanPath=tr)
        e.bot.set_watch(1, "a", True)
        e.bot.ensure_topic(1, "a")
        e.bot.handle_update(e.msg("go", mid=9, thread=thread_of(e)))
        return e, tr

    def test_tg_silent_param(self):
        e = Env(self)
        e.tg.send(1, "a", silent=True)
        e.tg.send_rich(1, "<b>a</b>", "a", silent=True)
        e.tg.send_md(1, "x" * (cc.LONG_LIMIT + 10), silent=True)
        e.tg.send(1, "b")
        sm = e.tg.of("sendMessage")
        self.assertTrue(all(m.get("disable_notification") for m in sm[:-1]))
        self.assertNotIn("disable_notification", sm[-1])
        self.assertIn(b"disable_notification", e.tg.of("sendDocument")[0]["raw"])

    def test_status_message_is_silent(self):
        e, _ = self._env()
        e.bot.status_tick()
        st = [m for m in e.tg.of("sendMessage") if "работает" in m["text"]]
        self.assertTrue(st and st[0].get("disable_notification"))

    def test_forward_silent_only_while_working(self):
        e, tr = self._env()
        with open(tr, "ab") as f:
            f.write(asst("n1", "промежуточный"))
        e.bot.forward_tick()
        self.assertTrue(e.tg.of("sendMessage")[-1].get("disable_notification"))
        e.job("a", name="Альфа", state="done", cwd="/tmp", output={"result": "ok"}, linkScanPath=tr)
        with open(tr, "ab") as f:
            f.write(asst("n2", "итог"))
        e.bot.forward_tick()
        m = e.tg.of("sendMessage")[-1]
        self.assertIn("итог", m["text"])
        self.assertNotIn("disable_notification", m)

    def test_question_no_duplicate_waiting_notice(self):
        e, tr = self._env()
        e.bot.notify_tick()
        with open(tr, "ab") as f:
            f.write(ask_line("tq", [Q_SIZE]))
        e.job("a", name="Альфа", state="blocked", cwd="/tmp", detail="question: Размер?", linkScanPath=tr)
        e.agents = [{"id": "a", "name": "Альфа", "state": "blocked", "cwd": "/tmp"}]
        e.bot.forward_tick()
        e.bot.notify_tick()
        texts = [m["text"] for m in e.tg.of("sendMessage")]
        self.assertFalse([t for t in texts if "ждёт" in t])
        self.assertTrue([t for t in texts if "Размер?" in t and "❓" in t])

    def test_notify_markup_question_has_no_yes_no(self):
        s = {"id": "a", "state": "blocked", "detail": "Question: X"}
        kb = [b["text"] for r in cc.notify_markup(s)["inline_keyboard"] for b in r]
        self.assertNotIn("✅ Да", kb)
        s["detail"] = "ждёт"
        kb = [b["text"] for r in cc.notify_markup(s)["inline_keyboard"] for b in r]
        self.assertIn("✅ Да", kb)

    def test_question_prefix_localized(self):
        self.assertEqual(cc.humanize_detail("question: Размер?"), "Вопрос: Размер?")
        s = {"name": "A", "state": "blocked", "detail": "question: Размер?", "result": ""}
        self.assertIn("Вопрос: Размер?", cc.notification_text(s))
        self.assertNotIn("question:", cc.notification_text(s))


LONG_PROMPT = "Please fix the failing import of mails in the billing service and add a regression test for it"


def sdk_names_env(tc, fake, watch=True):
    e = sdk_env(tc, fake, watch)
    e.name_inputs = []
    e.name_reply = ""
    orig = e.bot.run

    def runner(args, timeout=30, cwd=None, input_text=None):
        if "-p" in args:
            e.name_inputs.append(input_text)
            return 0, e.name_reply
        return orig(args, timeout, cwd, input_text)
    e.bot.run = runner
    return e


class SdkNamesTests(unittest.TestCase):
    def test_short_task_name(self):
        f = cc.short_task_name
        self.assertEqual(f("Fix the bug"), "Fix the bug")
        self.assertEqual(f("First line here\nsecond line"), "First line here")
        self.assertEqual(f("Do this. Then that and more"), "Do this.")
        out = f(LONG_PROMPT)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(len(out), 41)
        self.assertTrue(LONG_PROMPT.startswith(out[:-1].rstrip()))
        self.assertNotIn("  ", out)
        self.assertEqual(f(""), "")
        self.assertEqual(f("x" * 100), "x" * 39 + "…")

    def test_session_dict_name_is_short(self):
        d = cc.sdk_session_dict({"id": SID, "name": LONG_PROMPT})
        self.assertTrue(d["name"].endswith("…"))
        self.assertLessEqual(len(d["name"]), 41)
        self.assertEqual(d["task"], LONG_PROMPT)

    def test_sdk_session_is_proposed_and_applied_as_alias(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(name=LONG_PROMPT)
        e = sdk_names_env(self, fake)
        e.name_reply = reply_for(**{SID: "Почта: импорт"})
        e.bot.names_tick()
        rec = e.bot.store.data["names"][SID]
        self.assertEqual((rec["name"], rec["approved"]), ("Почта: импорт", False))
        self.assertEqual([p[0] for p in e.bot.name_proposals()], [SID])
        self.assertIn(LONG_PROMPT[:30], e.name_inputs[0])
        self.assertEqual(e.bot.apply_name(SID, "Почта: импорт"), "alias")
        self.assertEqual(e.bot.alias_for(SID), "Почта: импорт")
        self.assertEqual(fake.calls("session.send"), [])  # no /rename into the runner
        self.assertEqual(e.bot.topic_title(SID), "Почта: импорт")
        s = [x for x in e.bot.list_sessions(True) if x["id"] == SID][0]
        self.assertEqual(s["name"], "Почта: импорт")
        self.assertEqual(e.bot.session_info(SID)["name"], "Почта: импорт")
        e.bot.names_tick()  # nothing is sent on later ticks either
        self.assertEqual(fake.calls("session.send"), [])
        self.assertEqual(e.bot.alias_for(SID), "Почта: импорт")

    def test_new_sdk_session_is_named_automatically(self):
        fake = FakeSdk(self)
        e = sdk_names_env(self, fake, watch=False)
        e.name_reply = reply_for(**{"s-aabbccdd": "Почта: импорт"})
        sid = e.bot.do_new(1, "/work/proj", LONG_PROMPT, True)
        self.assertEqual(e.bot.alias_for(sid), "Почта: импорт")
        self.assertEqual(fake.calls("session.send"), [])

    def test_sdk_only_uses_short_name_and_no_claude(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(name=LONG_PROMPT)
        e = SdkOnlyTests.env(self, fake)
        s = [x for x in e.bot.list_sessions(True) if x["id"] == SID][0]
        self.assertTrue(s["name"].endswith("…"))
        self.assertLessEqual(len(s["name"]), 41)
        e.bot.names_tick()
        self.assertEqual(self.claude_calls, 0)
        self.assertEqual(e.bot.store.data.get("names", {}), {})


class SdkReplyButtonTests(unittest.TestCase):
    def test_reply_button_hidden_in_topics(self):
        def cds(m):
            return [b["callback_data"].split(":")[0] for b in (buttons(m) if m else [])]
        s = {"id": SID, "state": "blocked", "sdk_state": "working"}
        self.assertIn("go", cds(cc.notify_markup(s)))
        self.assertNotIn("go", cds(cc.notify_markup(s, topics=True)))
        self.assertIn("sdi", cds(cc.notify_markup(s, topics=True)))
        a = {"id": "abcd1234", "state": "done"}
        self.assertIn("go", cds(cc.notify_markup(a)))
        self.assertNotIn("go", cds(cc.notify_markup(a, topics=True)))
        self.assertIn("scr", cds(cc.notify_markup(a, topics=True)))

    def test_notification_in_topic_has_no_reply_button(self):
        for topics, want in ((True, False), (False, True)):
            fake = FakeSdk(self)
            e = sdk_env(self, fake)
            e.bot.topics_enabled = topics
            fake.sessions[SID] = fake.session(state="idle")
            e.bot.notify_tick()
            fake.sessions[SID] = fake.session(state="working")
            e.bot.notify_tick()
            fake.sessions[SID] = fake.session(state="idle", updated=2000.0)
            e.bot.notify_tick()
            marks = [p["reply_markup"] for p in e.tg.of("sendMessage") if p.get("reply_markup")]
            data = [b["callback_data"] for m in marks for b in buttons(m)]
            self.assertEqual(any(d.startswith("go:") for d in data), want, topics)


class SdkPermLayoutTests(unittest.TestCase):
    def shown(self, tool, inp):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req(tool=tool, inp=inp)}})
        return [p for p in e.tg.of("sendMessage") if "🔐" in p["text"]][0]

    def test_bash_stays_in_pre(self):
        m = self.shown("Bash", {"command": "ls <a>"})
        self.assertIn("<pre>ls &lt;a&gt;</pre>", m["text"])
        self.assertNotIn("blockquote", m["text"])

    def test_write_is_path_code_and_expandable_quote(self):
        m = self.shown("Write", {"file_path": "/a/b.txt", "content": "x<y\nline2"})
        self.assertNotIn("<pre>", m["text"])
        self.assertIn("<code>/a/b.txt</code>", m["text"])
        self.assertIn("<blockquote expandable>x&lt;y\nline2</blockquote>", m["text"])

    def test_edit_shows_old_and_new_in_quote(self):
        m = self.shown("Edit", {"file_path": "/a/c.py", "old_string": "a<b", "new_string": "c&d"})
        self.assertIn("<code>/a/c.py</code>", m["text"])
        self.assertIn("<blockquote expandable>a&lt;b\n→\nc&amp;d</blockquote>", m["text"])
        self.assertNotIn("<pre>", m["text"])

    def test_other_tool_json_in_quote(self):
        m = self.shown("WebFetch", {"url": "http://x/?a=1&b=2"})
        self.assertNotIn("<pre>", m["text"])
        self.assertIn("<blockquote expandable>", m["text"])
        self.assertIn("&amp;b=2", m["text"])


# ---------------------------------------------------------------- permission dialogs of tmux / SDK sessions

PERM_FIX = (
    '\n'
    '\x1b[38;5;174m ▐\x1b[48;5;16m▛███▛█\x1b[39m\x1b[49m   \x1b[1mClaude Code\x1b[0m \x1b[38;5;246mv2.1.300\x1b[39m\n'
    '\x1b[38;5;174m▝▜\x1b[48;5;16m█████\x1b[49m█▀\x1b[39m  \x1b[38;5;246mHaiku 5.5 · Claude Max\x1b[39m\n'
    '\x1b[38;5;174m ▝▝   ▝▝ \x1b[39m  \x1b[38;5;246m~/proj · \x1b[38;5;114m\x1b]8;id=iouolv;https://claude.ai/code/session_01URFwVQkUaTKakiAAaT6y5x?from=cli\x1b\\/rc\x1b[39m\x1b]8;;\x1b\\\n'
    '\n'
    '\n'
    '\x1b[38;5;246m\x1b[48;5;237m❯ \x1b[38;5;231mRun exactly this bash command and nothing else: echo hello-from-bash > out.txt\x1b[39m\n'
    '\n'
    '\x1b[38;5;246m\x1b[49m●\x1b[39m Writing hello-from-bash into out.txt\n'
    '\x1b[38;5;246m  ⎿  $ echo hello-from-bash > out.txt\x1b[39m\n'
    '\n'
    '\x1b[38;5;153m──────────────────────────────────────────────────────────────────────────────────────────────────────────────\n'
    '\x1b[39m \x1b[1m\x1b[38;5;153mBash command\x1b[0m\n'
    ' \x1b[1mTip: auto mode handles these prompts for you — choose "switch to auto mode" below\x1b[0m\n'
    ' \x1b[38;5;246mWrite hello-from-bash into out.txt\x1b[39m\n'
    '\x1b[38;5;239m╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n'
    '\x1b[39m echo hello-from-bash > out.txt\n'
    '\x1b[38;5;239m╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n'
    '\x1b[39m \x1b[2m│\x1b[0m Claude requested permissions to edit /home/user/proj/out.txt which is a\n'
    ' \x1b[2m│\x1b[0m sensitive file.\n'
    '\n'
    ' Do you want to proceed?\n'
    ' \x1b[38;5;153m❯\x1b[39m \x1b[38;5;246m1. \x1b[38;5;153mYes\x1b[39m\n'
    '   \x1b[38;5;246m2. \x1b[39mYes, and always allow access to \x1b[1m/home/user/proj\x1b[0m from this project\n'
    '   \x1b[38;5;246m3. \x1b[39mYes, and switch to auto mode\x1b[38;5;246m · auto mode handles these prompts for you\x1b[39m\n'
    '   \x1b[38;5;246m4. \x1b[39mNo\n'
    '\n'
    ' \x1b[38;5;246mEsc to cancel · Tab to amend\x1b[39m\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
    '\n'
)
PERM_EDIT = ("────────────────────────────────────────\n"
             " Edit file\n"
             " src/app.py\n"
             "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
             " 1 -old line\n"
             " 1 +new line\n"
             "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌\n"
             " Do you want to make this edit to app.py?\n"
             " ❯ 1. Yes\n"
             "   2. Yes, allow all edits during this session (shift+tab)\n"
             "   3. No\n"
             "\n"
             " Esc to cancel\n")
PERM_FETCH = ("────────────────────────────────────────\n"
              " Fetch\n"
              " https://example.com/page\n"
              "\n"
              " Do you want to allow Claude to fetch this content?\n"
              "   1. Yes\n"
              " ❯ 2. Yes, and don't ask again for example.com\n"
              "   3. No, and tell Claude what to do differently (esc)\n"
              "\n"
              " Esc to cancel\n")
PERM_BASH_OPTS = [{"n": 1, "label": "Yes"},
                  {"n": 2, "label": "Yes, and always allow access to /home/user/proj from this project"},
                  {"n": 3, "label": "Yes, and switch to auto mode · auto mode handles these prompts for you"},
                  {"n": 4, "label": "No"}]


class PermParseTests(unittest.TestCase):
    def test_bash_fixture(self):
        want = {"title": "Bash command", "summary": "Write hello-from-bash into out.txt",
                "body": "echo hello-from-bash > out.txt",
                "note": "Claude requested permissions to edit /home/user/proj/out.txt which is a sensitive file.",
                "question": "Do you want to proceed?", "options": PERM_BASH_OPTS, "selected": 1}
        self.assertEqual(cc.parse_perm_dialog(PERM_FIX), want)
        self.assertEqual(cc.parse_perm_dialog(cc.strip_ansi(PERM_FIX)), want)
        self.assertTrue(cc.menu_active(PERM_FIX))
        self.assertNotIn("Tip", json.dumps(cc.parse_perm_dialog(PERM_FIX)))

    def test_other_kinds(self):
        p = cc.parse_perm_dialog(PERM_EDIT)
        self.assertEqual((p["title"], p["summary"], p["question"], p["selected"]),
                         ("Edit file", "src/app.py", "Do you want to make this edit to app.py?", 1))
        self.assertEqual(p["body"], "1 -old line\n1 +new line")
        self.assertEqual([o["n"] for o in p["options"]], [1, 2, 3])
        f = cc.parse_perm_dialog(PERM_FETCH)
        self.assertEqual((f["title"], f["selected"], len(f["options"])), ("Fetch", 2, 3))
        self.assertIn("https://example.com/page", f["body"] + f["summary"])

    def test_not_permissions(self):
        for scr in (MENU_S, MENU_TABS, REVIEW, PANEL_S, EMPTY_PROMPT, "",
                    "Select model\n\n❯ 1. Default\n  2. Opus\n\nEnter to confirm · Esc to exit\n"):
            self.assertIsNone(cc.parse_perm_dialog(scr), scr[:30])


def perm_env(tc, state="blocked", screen=PERM_FIX, live=True, sub_count=0):
    """A web env with push; session s1 is primed as working, then moved to `state` with the given screen."""
    w, e = push_env(tc, sub_count=sub_count)
    mk(e, "s1", "working", 1, name="Альфа")
    if live:
        e.agents[-1]["pid"] = 4242
    e.bot.list_sessions(True)
    e.bot.set_watch(1, "s1", True)
    fs = FakeScreen(EMPTY_PROMPT)
    e.bot.tmux = fs.tmux()
    e.bot.notify_tick()
    set_state(e, state)
    fs.screen = screen
    return w, e, fs


def set_state(e, state, detail="needs permission"):
    e.job("s1", name="Альфа", state=state, detail=detail, cwd="/tmp", updatedAt=iso(BASE - 30))
    e.agents[0]["state"] = state
    e.bot.list_sessions(True)


class PermApiTests(unittest.TestCase):
    def test_session_has_perm_for_blocked_live_tmux(self):
        w, e, fs = perm_env(self)
        n = len(fs.calls)
        st, obj, _ = w.req("GET", "/api/session/s1")
        self.assertEqual(st, 200)
        p = obj["perm"]
        self.assertEqual((p["kind"], p["title"], p["body"], p["selected"]), ("tmux", "Bash command",
                                                                          "echo hello-from-bash > out.txt", 1))
        self.assertEqual(p["options"], PERM_BASH_OPTS)
        self.assertEqual([c[1] for c in fs.calls[n:]], ["capture-pane"])  # only a capture: no window reopening
        fs.screen = EMPTY_PROMPT
        self.assertIsNone(w.req("GET", "/api/session/s1")[1]["perm"])
        fs.screen = PERM_FIX
        set_state(e, "working")
        self.assertIsNone(w.req("GET", "/api/session/s1")[1]["perm"])  # not blocked: the screen is not even read

    def test_session_perm_none_when_not_live_or_no_window(self):
        w, e, fs = perm_env(self, live=False)
        self.assertIsNone(w.req("GET", "/api/session/s1")[1]["perm"])
        w, e, fs = perm_env(self)
        e.bot.tmux.run = lambda *a, **k: (1, "")  # no tmux window: capture is empty
        self.assertIsNone(w.req("GET", "/api/session/s1")[1]["perm"])

    def test_list_flag_comes_from_cache_not_from_screen(self):
        w, e, fs = perm_env(self)
        n = len(fs.calls)
        rows = {r["id"]: r for r in w.req("GET", "/api/sessions")[1]["sessions"]}
        self.assertIs(rows["s1"]["perm"], False)  # cache is filled by the background tick
        self.assertEqual(len(fs.calls), n)
        e.bot.notify_tick()
        n = len(fs.calls)
        rows = {r["id"]: r for r in w.req("GET", "/api/sessions")[1]["sessions"]}
        self.assertIs(rows["s1"]["perm"], True)
        self.assertEqual(len(fs.calls), n)  # the list never captures the screen
        self.assertEqual(rows["s1"]["perm_title"], "Bash command")
        self.assertEqual(rows["s1"]["perm_body"], "echo hello-from-bash > out.txt")
        fs.screen = EMPTY_PROMPT
        e.bot.notify_tick()
        rows = {r["id"]: r for r in w.req("GET", "/api/sessions")[1]["sessions"]}
        self.assertIs(rows["s1"]["perm"], False)
        self.assertEqual(rows["s1"]["perm_title"], "")

    def test_post_presses_the_option(self):
        w, e, fs = perm_env(self)
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 2, "label": PERM_BASH_OPTS[1]["label"]})
        self.assertEqual((st, obj), (200, {"ok": True, "perm": None}))
        self.assertEqual(fs.flat_keys, ["2"])

    def test_post_returns_next_dialog(self):
        w, e, fs = perm_env(self)
        fs.on_key = lambda ks, f: setattr(f, "screen", PERM_EDIT)
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 1, "label": "Yes"})
        self.assertTrue(obj["ok"])
        self.assertEqual(obj["perm"]["title"], "Edit file")

    def test_post_closed_and_stale_press_nothing(self):
        w, e, fs = perm_env(self, screen=EMPTY_PROMPT)
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 1, "label": "Yes"})
        self.assertEqual((st, obj), (200, {"ok": False, "code": "closed"}))
        fs.screen = PERM_FIX
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 2, "label": "Yes"})
        self.assertEqual((obj["ok"], obj["code"]), (False, "stale"))
        self.assertEqual(obj["perm"]["options"], PERM_BASH_OPTS)
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 7, "label": "x"})
        self.assertEqual((obj["ok"], obj["code"]), (False, "stale"))
        self.assertEqual(fs.flat_keys, [])

    def test_post_bad_requests_and_auth(self):
        w, e, fs = perm_env(self)
        for body in ({"label": "Yes"}, {"option": "x", "label": "Yes"}, {"option": 1}, {"option": 0, "label": "Yes"}):
            self.assertEqual(w.req("POST", "/api/session/s1/perm", body)[0], 400, body)
        self.assertEqual(w.req("POST", "/api/session/s1/perm", {"option": 1, "label": "Yes"}, auth=False)[0], 401)
        self.assertEqual(w.req("GET", "/api/session/s1/perm")[0], 405)
        self.assertEqual(fs.flat_keys, [])
        set_state(e, "done")
        e.agents[0].pop("pid")
        e.bot.list_sessions(True)
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 1, "label": "Yes"})
        self.assertEqual((obj["ok"], obj["code"]), (False, "stopped"))


class PermSdkApiTests(unittest.TestCase):
    def env(self, can_always=True, pending=True):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="blocked" if pending else "idle",
                                          pending=[perm_req(inp={"command": "ls -la"}, can_always=can_always)]
                                          if pending else [])
        return fake, sdk_env(self, fake)

    def test_session_perm_view(self):
        fake, e = self.env()
        row = e.bot.api_session(1, SID)
        self.assertEqual(row["perm"], {"kind": "sdk", "req": "r-00000001", "title": "Bash", "summary": "",
                                       "body": "ls -la", "path": "", "note": "", "question": "", "selected": 1,
                                       "options": [{"n": 1, "label": "Разрешить"},
                                                   {"n": 2, "label": "Всегда Bash в этой сессии"},
                                                   {"n": 3, "label": "Отклонить"}]})
        self.assertIs(e.bot.api_row(1, SID, 0)["perm"], True)
        self.assertEqual(e.bot.api_row(1, SID, 0)["perm_title"], "Bash")
        self.assertEqual(e.bot.api_row(1, SID, 0)["perm_body"], "ls -la")
        fake, e = self.env(can_always=False)
        self.assertEqual([o["n"] for o in e.bot.api_session(1, SID)["perm"]["options"]], [1, 3])
        fake, e = self.env(pending=False)
        self.assertIsNone(e.bot.api_session(1, SID)["perm"])
        self.assertIs(e.bot.api_row(1, SID, 0)["perm"], False)

    def test_session_perm_path_for_write(self):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="blocked", pending=[
            perm_req(tool="Write", inp={"file_path": "/a/b.txt", "content": "hi"})])
        e = sdk_env(self, fake)
        p = e.bot.api_session(1, SID)["perm"]
        self.assertEqual((p["title"], p["path"], p["body"]), ("Write", "/a/b.txt", "hi"))

    def test_post_maps_options_to_decisions(self):
        for opt, decision in ((1, "allow"), (2, "always"), (3, "deny")):
            fake, e = self.env()
            st, obj = e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, {"option": opt, "req": "r-00000001"})
            self.assertEqual((st, obj["ok"]), (200, True))
            self.assertNotIn("already", obj)
            self.assertEqual(fake.calls("session.answer"), [{"id": SID, "req": "r-00000001", "decision": decision}])

    def test_post_already_bad_and_closed(self):
        fake, e = self.env()
        fake.already = True
        st, obj = e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, {"option": 1, "req": "r-00000001"})
        self.assertEqual(obj, {"ok": True, "already": True})
        fake, e = self.env(can_always=False)
        st, obj = e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, {"option": 2, "req": "r-00000001"})
        self.assertEqual((st, obj["ok"]), (400, False))
        st, obj = e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, {"option": 9})
        self.assertEqual(st, 400)
        self.assertEqual(fake.calls("session.answer"), [])
        fake, e = self.env(pending=False)
        st, obj = e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, {"option": 1})
        self.assertEqual((st, obj), (200, {"ok": False, "code": "closed"}))

    def test_post_sdk_error(self):
        fake, e = self.env()
        fake.errors["session.answer"] = "bad_request"
        st, obj = e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, {"option": 1, "req": "r-00000001"})
        self.assertEqual((st, obj["ok"], obj["code"]), (200, False, "error"))
        self.assertTrue(obj["error"])


class PermNotifyTests(unittest.TestCase):
    def pushes(self, e):
        return [decode_push(e, i)[0] for i in range(len(e.sent_pushes))]

    def test_transition_pushes_the_permission_once(self):
        w, e, fs = perm_env(self, sub_count=1)
        e.bot.notify_tick()
        e.bot.notify_tick()
        bodies = [p["body"] for p in self.pushes(e)]
        self.assertEqual(len(bodies), 1)
        self.assertTrue(bodies[0].startswith("Bash command: echo hello-from-bash"), bodies)
        self.assertEqual(decode_push(e)[1]["Urgency"], "high")
        self.assertEqual(self.pushes(e)[0]["tag"], "s1")

    def test_permission_in_already_blocked_session_pushes_once(self):
        w, e, fs = perm_env(self, sub_count=1, screen=EMPTY_PROMPT)
        e.bot.notify_tick()
        n = len(e.sent_pushes)  # the plain "waiting" notification
        fs.screen = PERM_FIX
        e.bot.notify_tick()
        e.bot.notify_tick()
        new = [p["body"] for p in self.pushes(e)[n:]]
        self.assertEqual(len(new), 1)
        self.assertTrue(new[0].startswith("Bash command"))
        fs.screen = PERM_EDIT  # the next request is a new one
        e.bot.notify_tick()
        self.assertTrue([p["body"] for p in self.pushes(e)][-1].startswith("Edit file"))
        self.assertEqual(len(e.sent_pushes), n + 2)

    def test_quiet_while_owner_is_in_the_miniapp(self):
        w, e, fs = perm_env(self, sub_count=1, screen=EMPTY_PROMPT)
        e.bot.notify_tick()
        n = len(e.sent_pushes)
        e.bot.webapp_touch["s1"] = e.bot.clock()
        fs.screen = PERM_FIX
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n)
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]), 0)

    def test_no_push_on_restart_for_an_open_permission(self):
        w, e = push_env(self, sub_count=1)
        mk(e, "s1", "blocked", 1, name="Альфа")
        e.agents[-1]["pid"] = 4242
        fs = FakeScreen(PERM_FIX)
        e.bot.tmux = fs.tmux()
        e.bot.list_sessions(True)
        e.bot.set_watch(1, "s1", True)
        e.bot.notify_tick()  # the first tick only primes
        e.bot.notify_tick()
        self.assertEqual(e.sent_pushes, [])

    def test_sdk_request_pushes(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        sent = []
        e.bot.push_notify = lambda uid, sid, body, urgency="high", title=None: sent.append((uid, sid, body, urgency)) or True
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        self.assertEqual(sent, [(1, SID, "Bash: ls -la", "high")])
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        self.assertEqual(len(sent), 1)  # the same request is not pushed twice
        e.bot.webapp_touch[SID] = e.bot.clock()
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req(req="r-00000002")}})
        self.assertEqual(len(sent), 1)  # the owner is in the mini-app


class PermTelegramTests(unittest.TestCase):
    def notice(self, e):
        return [m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]

    def test_waiting_notice_shows_the_dialog_with_numbered_buttons(self):
        w, e, fs = perm_env(self)
        e.bot.notify_tick()
        msgs = self.notice(e)
        self.assertEqual(len(msgs), 1)
        self.assertIn("echo hello-from-bash &gt; out.txt", msgs[0]["text"])
        self.assertIn("<pre>", msgs[0]["text"])
        self.assertIn("Bash command", msgs[0]["text"])
        btns = [b for b in buttons(msgs[0]["reply_markup"]) if b["callback_data"].startswith("pk:")]
        texts = [b["text"] for b in btns]
        self.assertEqual(len(btns), 4)
        self.assertTrue(texts[0].startswith("1.") and texts[-1].endswith("Нет"), texts)
        self.assertTrue(all(len(t) <= 30 for t in texts), texts)
        self.assertTrue(all(len(b["callback_data"].encode()) <= 64 for b in btns))
        self.assertFalse([b for b in buttons(msgs[0]["reply_markup"]) if b["callback_data"].startswith("qk:")])

    def test_without_a_parsed_dialog_the_old_buttons_stay(self):
        w, e, fs = perm_env(self, screen=EMPTY_PROMPT)
        e.bot.notify_tick()
        m = [x for x in e.tg.of("sendMessage") if "Альфа" in x["text"]][-1]
        self.assertEqual([b["callback_data"] for b in buttons(m["reply_markup"])][:2], ["qk:s1:y", "qk:s1:n"])

    def press(self, e, msg, idx, mid=77):
        pk = [b for b in buttons(msg["reply_markup"]) if b["callback_data"].startswith("pk:")]
        e.bot.handle_update(e.cb(pk[idx]["callback_data"], mid=mid))

    def test_press_sends_the_number_key(self):
        w, e, fs = perm_env(self)
        e.bot.notify_tick()
        msg = self.notice(e)[0]
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        self.press(e, msg, 3)
        self.assertEqual(fs.flat_keys, ["4"])
        self.assertTrue(e.tg.of("editMessageReplyMarkup"))

    def test_stale_button_presses_nothing(self):
        w, e, fs = perm_env(self)
        e.bot.notify_tick()
        msg = self.notice(e)[0]
        fs.screen = PERM_EDIT  # a different dialog: option 2 has another label now
        self.press(e, msg, 1)
        self.assertEqual(fs.flat_keys, [])
        self.assertEqual(e.tg.of("answerCallbackQuery")[-1]["text"], "Эта кнопка устарела.")
        fs.screen = EMPTY_PROMPT  # closed
        self.press(e, msg, 0)
        self.assertEqual(fs.flat_keys, [])


# ---------------------------------------------------------------- permission dialogs: review fixes

def threaded_spawn(fn, *a):
    t = threading.Thread(target=fn, args=a)
    t.start()
    t.join(5)


def answered_texts(e):
    return [p["text"] for p in e.tg.of("answerCallbackQuery") if p.get("text")]


class PermReviewTests(unittest.TestCase):
    def pk_buttons(self, e):
        m = [x for x in e.tg.of("sendMessage") if "🔐" in x["text"]][-1]
        return [b for b in buttons(m["reply_markup"]) if b["callback_data"].startswith("pk:")]

    def test_pk_press_answers_with_a_popup_even_with_async_spawn(self):
        w, e, fs = perm_env(self)
        e.bot.notify_tick()
        pk = self.pk_buttons(e)
        e.bot.spawn = threaded_spawn
        n = len(e.tg.of("sendMessage"))
        fs.screen = PERM_EDIT  # the dialog changed: the button is stale
        e.bot.handle_update(e.cb(pk[1]["callback_data"], mid=77))
        self.assertIn("Эта кнопка устарела.", answered_texts(e))
        self.assertEqual(len(e.tg.of("sendMessage")), n)  # not a temporary chat message

    def test_sdk_push_delivered_means_no_telegram(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake)
        order = []
        e.bot.push_notify = lambda uid, sid, body, urgency="high", title=None: order.append("push") or True
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        self.assertEqual(order, ["push"])
        self.assertEqual([p for p in e.tg.of("sendMessage") if "🔐" in p["text"]], [])
        e.bot.push_notify = lambda *a, **k: False  # no working subscription: Telegram
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req(req="r-00000002")}})
        self.assertEqual(len([p for p in e.tg.of("sendMessage") if "🔐" in p["text"]]), 1)

    def test_dialog_without_transition_goes_to_telegram_without_push(self):
        w, e, fs = perm_env(self, screen=EMPTY_PROMPT)
        e.bot.notify_tick()
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]), 0)
        fs.screen = PERM_FIX
        e.bot.notify_tick()
        e.bot.notify_tick()
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]), 1)
        self.assertEqual(len(self.pk_buttons(e)), 4)
        self.assertTrue(e.tg.of("deleteMessage"))  # the old «waiting» notice with Yes/No is gone
        fs.screen = PERM_EDIT  # a second dialog in a row
        e.bot.notify_tick()
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]), 2)

    def test_dialog_without_transition_with_push_stays_out_of_telegram(self):
        w, e, fs = perm_env(self, screen=EMPTY_PROMPT, sub_count=1)
        e.bot.notify_tick()
        fs.screen = PERM_FIX
        e.bot.notify_tick()
        self.assertEqual(len([m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]), 0)

    def test_answered_dialog_still_drawn_is_not_cached_or_pushed(self):
        w, e, fs = perm_env(self, sub_count=1)
        e.bot.notify_tick()
        n = len(e.sent_pushes)
        st, obj, _ = w.req("POST", "/api/session/s1/perm", {"option": 1, "label": "Yes"})
        self.assertTrue(obj["ok"])
        e.bot.notify_tick()  # the screen has not redrawn yet
        self.assertEqual(len(e.sent_pushes), n)
        self.assertFalse([r for r in w.req("GET", "/api/sessions")[1]["sessions"] if r["id"] == "s1"][0]["perm"])
        e.t["v"] += 61  # a real new dialog of the same text after the window
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n + 1)

    def test_telegram_press_marks_answered_too(self):
        w, e, fs = perm_env(self)
        e.bot.notify_tick()
        e.bot.handle_update(e.cb(self.pk_buttons(e)[0]["callback_data"], mid=77))
        self.assertEqual(fs.flat_keys, ["1"])
        n = len(e.tg.of("sendMessage"))
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), n)

    def test_post_perm_does_not_wait_for_the_global_lock(self):
        w, e, fs = perm_env(self)
        held, release, out = threading.Event(), threading.Event(), []

        def hold():
            with e.bot.api_lock:
                held.set()
                release.wait(10)
        t = threading.Thread(target=hold)
        t.start()
        self.addCleanup(t.join)
        self.addCleanup(release.set)
        held.wait(5)
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        r = threading.Thread(target=lambda: out.append(
            e.bot.api_dispatch(1, "POST", "/api/session/s1/perm", {"option": 1, "label": "Yes"})))
        r.start()
        r.join(3)
        self.assertFalse(r.is_alive())
        self.assertEqual(out[0][1]["ok"], True)

    def test_push_is_only_held_while_the_owner_is_active(self):
        w, e, fs = perm_env(self, sub_count=1, screen=EMPTY_PROMPT)
        e.bot.notify_tick()
        n = len(e.sent_pushes)
        fs.screen = PERM_FIX
        e.bot.webapp_touch["s1"] = e.bot.clock()
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n)
        e.t["v"] += 61  # the owner left the mini-app
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n + 1)
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n + 1)

    def test_push_after_an_old_touch(self):
        w, e, fs = perm_env(self, sub_count=1, screen=EMPTY_PROMPT)
        e.bot.notify_tick()
        n = len(e.sent_pushes)
        e.bot.webapp_touch["s1"] = e.bot.clock() - 120  # inside WEBAPP_QUIET but not active
        fs.screen = PERM_FIX
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), n + 1)

    def test_fresh_cache_serves_the_view_without_reading_the_screen(self):
        w, e, fs = perm_env(self)
        e.bot.notify_tick()
        fs.screen = EMPTY_PROMPT
        n = len(fs.calls)
        self.assertEqual(w.req("GET", "/api/session/s1")[1]["perm"]["title"], "Bash command")
        self.assertEqual(len(fs.calls), n)
        e.t["v"] += 11
        self.assertIsNone(w.req("GET", "/api/session/s1")[1]["perm"])


class PermSdkReviewTests(unittest.TestCase):
    def env(self, **kw):
        fake = FakeSdk(self)
        fake.sessions[SID] = fake.session(state="blocked", pending=[perm_req(**kw)])
        return fake, sdk_env(self, fake)

    def post(self, e, body):
        return e.bot.api_dispatch(1, "POST", "/api/session/%s/perm" % SID, body)

    def test_already_drops_the_pending_request(self):
        fake, e = self.env()
        fake.already = True
        self.assertEqual(self.post(e, {"option": 1, "req": "r-00000001"})[1], {"ok": True, "already": True})
        self.assertEqual(e.bot.known[SID]["pending"], [])

    def test_unknown_req_is_refreshed_then_closed(self):
        fake, e = self.env()
        e.bot.known[SID]["pending"] = []  # the cache is behind the runner
        st, obj = self.post(e, {"option": 1, "req": "r-00000001"})
        self.assertTrue(obj["ok"])
        self.assertEqual(len(fake.calls("session.answer")), 1)
        fake, e = self.env()
        st, obj = self.post(e, {"option": 1, "req": "r-nope"})
        self.assertEqual((st, obj), (200, {"ok": False, "code": "closed"}))
        self.assertEqual(fake.calls("session.answer"), [])

    def test_always_needs_can_always_of_the_found_request(self):
        fake, e = self.env(can_always=False)
        e.bot.known[SID]["pending"] = []
        st, obj = self.post(e, {"option": 2, "req": "r-00000001"})
        self.assertEqual(st, 400)
        self.assertEqual(fake.calls("session.answer"), [])

    def test_runner_timeout_is_short(self):
        fake, e = self.env()
        seen = []
        real = e.bot.sdk.call
        e.bot.sdk.call = lambda m, p=None, timeout=None, **k: seen.append(timeout) or real(m, p, timeout=timeout, **k)
        self.post(e, {"option": 1, "req": "r-00000001"})
        self.assertTrue(seen and max(seen) <= 8.0, seen)

    def test_closed_event_matches_requests_by_string(self):
        fake, e = self.env()
        e.bot.known[SID]["pending"] = [dict(perm_req(), req=5)]
        e.bot.sdk_on_event({"event": "request.closed", "data": {"id": SID, "req": "5", "decision": "deny"}})
        self.assertEqual(e.bot.known[SID]["pending"], [])


class FakeStt:
    """A fake STT runner on a Unix socket (docs/stt-protocol.md)."""

    def __init__(self, tc, ready=True):
        self.dir = tempfile.mkdtemp(prefix="st")
        tc.addCleanup(self.close)
        self.path = os.path.join(self.dir, "stt.sock")
        self.requests = []
        self.engines = [{"name": "local", "ready": ready, "model": "small"}, {"name": "groq", "ready": False}]
        self.text = "привет мир"
        self.error = None
        self.seen = []        # (existed, size, ext) of the file at the call
        self.lock_free = []   # was the bot's api_lock free during the call
        self.bot = None
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(self.path)
        self.srv.listen(8)
        self.alive = True
        threading.Thread(target=self.serve, daemon=True).start()

    def close(self):
        self.alive = False
        try:
            self.srv.close()
        except OSError:
            pass
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def serve(self):
        while self.alive:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            threading.Thread(target=self.handle, args=(c,), daemon=True).start()

    def handle(self, c):
        buf = b""
        try:
            while b"\n" not in buf:
                chunk = c.recv(65536)
                if not chunk:
                    return
                buf += chunk
            req = json.loads(buf.split(b"\n")[0])
            m, p = req["method"], req.get("params") or {}
            self.requests.append((m, p))
            if m == "stt.info":
                c.sendall(line({"id": req["id"], "result": {"version": "1", "engines": self.engines, "loaded": False}}))
            elif m == "stt.transcribe":
                pth_ = p.get("path", "")
                ok = os.path.isfile(pth_)
                self.seen.append((ok, os.path.getsize(pth_) if ok else -1, os.path.splitext(pth_)[1]))
                if self.bot is not None:
                    got = []

                    def probe():
                        ok_ = self.bot.api_lock.acquire(blocking=False)
                        if ok_:
                            self.bot.api_lock.release()
                        got.append(ok_)
                    t = threading.Thread(target=probe)
                    t.start()
                    t.join()
                    self.lock_free.append(bool(got and got[0]))
                if self.error:
                    c.sendall(line({"id": req["id"], "error": {"code": self.error, "message": "x"}}))
                else:
                    c.sendall(line({"id": req["id"], "result": {"text": self.text, "lang": p.get("lang"), "seconds": 3.5,
                                                                 "took": 0.4, "engine": "local"}}))
            else:
                c.sendall(line({"id": req["id"], "error": {"code": "bad_request", "message": m}}))
        finally:
            c.close()

    def calls(self, method):
        return [p for m, p in self.requests if m == method]


def stt_attach(e, fake):
    e.bot.stt = cc.SttClient(fake.path)
    fake.bot = e.bot
    return e


def voice_msg(kind="voice", mid=60, thread=None, reply=None):
    return file_msg(kind, mid=mid, thread=thread, reply=reply)


def voice_cards(e):
    return [p for p in e.tg.of("sendMessage") if p.get("text", "").startswith("🎙")]


class SttConfigTests(unittest.TestCase):
    def test_config(self):
        e = Env(self)
        self.assertEqual(e.cfg.stt_mode, "auto")
        self.assertTrue(e.cfg.stt_socket.endswith(".local/state/zidos-pager/stt.sock"))
        self.assertEqual(e.cfg.stt_lang, "")
        cfg = cc.Config(env_path=os.path.join(e.tmp.name, "env"), environ={
            "HOME": e.tmp.name, "CC_STT": "off", "CC_STT_SOCKET": "/x/s.sock", "CC_STT_LANG": "EN"})
        self.assertEqual((cfg.stt_mode, cfg.stt_socket, cfg.stt_lang), ("off", "/x/s.sock", "en"))

    def test_ready_cache_and_states(self):
        fake = FakeStt(self)
        e = stt_attach(Env(self), fake)
        self.assertTrue(e.bot.stt_ready(True))
        n = len(fake.requests)
        self.assertTrue(e.bot.stt_ready())  # cached
        self.assertEqual(len(fake.requests), n)
        fake.engines[0]["ready"] = False
        self.assertTrue(e.bot.stt_ready())  # still the cached answer
        self.assertFalse(e.bot.stt_ready(True))  # no ready engine
        e.bot.stt = cc.SttClient(os.path.join(e.tmp.name, "none.sock"))
        self.assertFalse(e.bot.stt_ready(True))
        e.bot.stt = cc.SttClient(fake.path, mode="off")
        self.assertFalse(e.bot.stt_ready(True))


class SttTelegramTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeStt(self)
        e = panel_env(self)
        mk(e, "s1", "working", 1, name="Альфа")
        e.bot.list_sessions(True)
        e.bot.ensure_topic(1, "s1")
        stt_attach(e, self.fake)
        e.tg.files["X1"] = ("voice/v.oga", b"OGGDATA")
        self.e = e
        self.th = thread_of(e, "s1")

    def card(self):
        cards = voice_cards(self.e)
        self.assertEqual(len(cards), 1)
        return cards[0]

    def test_voice_in_topic_shows_text_with_buttons_and_does_not_send(self):
        e = self.e
        e.bot.handle_update(voice_msg(thread=self.th))
        c = self.card()
        self.assertEqual(c["text"], "🎙 привет мир")
        self.assertEqual(c["message_thread_id"], self.th)
        self.assertEqual(c["reply_parameters"]["message_id"], 60)
        kb = [b["text"] for b in buttons(c["reply_markup"])]
        self.assertEqual(kb, ["📨 Отправить", "✖"])
        self.assertEqual(e.sent, [])  # nothing reaches the session before the button
        self.assertEqual(self.fake.calls("stt.transcribe")[0]["lang"], "ru")
        self.assertEqual(self.fake.seen, [(True, 7, ".ogg")])
        self.assertTrue(e.tg.of("setMessageReaction"))
        p = self.fake.calls("stt.transcribe")[0]["path"]
        self.assertFalse(os.path.exists(p))  # the temp file is gone
        self.assertFalse(os.path.exists(os.path.dirname(p)))

    def test_lang_override_and_english(self):
        e = self.e
        e.cfg.stt_lang = "de"
        e.bot.handle_update(voice_msg(thread=self.th))
        self.assertEqual(self.fake.calls("stt.transcribe")[-1]["lang"], "de")
        e.cfg.stt_lang = ""
        cc.set_lang("en")
        self.addCleanup(cc.set_lang, "ru")
        e.bot.handle_update(voice_msg(mid=61, thread=self.th))
        self.assertEqual(self.fake.calls("stt.transcribe")[-1]["lang"], "en")

    def test_send_button_delivers_and_marks_the_card(self):
        e = self.e
        e.bot.handle_update(voice_msg(thread=self.th))
        c = self.card()
        data = [b["callback_data"] for b in buttons(c["reply_markup"])]
        self.assertTrue(all(len(d) <= 64 for d in data))
        e.bot.handle_update(e.cb(data[0], mid=e.tg.n, text=c["text"]))
        self.assertEqual(e.sent, [("s1", "привет мир")])
        ed = e.tg.of("editMessageText")[-1]
        self.assertEqual(ed["text"], "🎙 привет мир · ✔ отправлено")
        self.assertNotIn("reply_markup", ed)
        self.assertEqual(e.tg.of("answerCallbackQuery")[-1]["text"], "Отправлено")
        e.bot.handle_update(e.cb(data[0], mid=e.tg.n, text=c["text"]))  # a second press does not send again
        self.assertEqual(len(e.sent), 1)

    def test_cancel_button_deletes_the_card(self):
        e = self.e
        e.bot.handle_update(voice_msg(thread=self.th))
        c = self.card()
        data = [b["callback_data"] for b in buttons(c["reply_markup"])]
        mid = e.tg.n
        e.bot.handle_update(e.cb(data[1], mid=mid, text=c["text"]))
        self.assertIn(mid, [d["message_id"] for d in e.tg.of("deleteMessage")])
        self.assertEqual(e.sent, [])

    def test_audio_and_reply_routing_without_topics(self):
        e = panel_env(self, topics=False)
        mk(e, "s1", "working", 1)
        mk(e, "s2", "working", 1)
        e.bot.list_sessions(True)
        stt_attach(e, self.fake)
        e.tg.files["X1"] = ("a/a.mp3", b"MP3")
        e.bot.make_current(1, "s1")
        e.bot.say(1, "про s2", sid="s2")
        e.bot.handle_update(voice_msg("audio", mid=70, reply=e.tg.n))
        c = voice_cards(e)[0]
        self.assertEqual(self.fake.seen[-1][2], ".mp3")
        e.bot.handle_update(e.cb(buttons(c["reply_markup"])[0]["callback_data"], mid=e.tg.n))
        self.assertEqual(e.sent, [("s2", "привет мир")])

    def test_all_voice_card_in_all_send_routes_cancel_cleans(self):
        e = self.e
        stub_router(e, '{"sid": "s1", "cwd": null}')
        e.bot.handle_update(voice_msg(mid=81))
        c = self.card()
        self.assertNotIn("message_thread_id", c)  # the card stays in All, under the voice message
        self.assertEqual(c["reply_parameters"]["message_id"], 81)
        self.assertEqual(e.sent, [])
        e.bot.handle_update(e.cb(buttons(c["reply_markup"])[0]["callback_data"], mid=e.tg.n))
        self.assertEqual(e.sent, [("s1", "привет мир")])
        self.assertIn(81, [d["message_id"] for d in e.tg.of("deleteMessage")])
        e.bot.handle_update(voice_msg(mid=82))
        card = voice_cards(e)[-1]
        e.bot.handle_update(e.cb(buttons(card["reply_markup"])[1]["callback_data"], mid=e.tg.n))
        self.assertIn(82, [d["message_id"] for d in e.tg.of("deleteMessage")])  # ✖ takes the voice message too
        self.assertEqual(len(e.sent), 1)

    def test_panel_voice_becomes_a_new_task(self):
        e = self.e
        e.bot.panel_publish(1)
        e.bot.handle_update(voice_msg(mid=80, thread=pth(e)))
        self.assertIn(80, [d["message_id"] for d in e.tg.of("deleteMessage")])  # the voice message itself is cleaned
        self.assertEqual(self.fake.calls("stt.transcribe")[-1]["lang"], "ru")  # but it was processed
        c = self.card()
        self.assertEqual(c["message_thread_id"], pth(e))
        e.bot.handle_update(e.cb(buttons(c["reply_markup"])[0]["callback_data"], mid=e.tg.n))
        self.assertEqual(e.bot.store.chat(1)["pending_task"]["text"], "привет мир")
        self.assertEqual(e.sent, [])

    def test_unavailable_error_and_empty(self):
        e = self.e
        e.bot.stt = cc.SttClient(os.path.join(e.tmp.name, "none.sock"))
        e.bot.handle_update(voice_msg(thread=self.th))
        self.assertIn("Голосовой ввод не настроен", e.tg.of("sendMessage")[-1]["text"])
        self.assertIn("zidos-pager-stt", e.tg.of("sendMessage")[-1]["text"])
        stt_attach(e, self.fake)
        self.fake.error = "internal"
        e.bot.handle_update(voice_msg(mid=61, thread=self.th))
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "Не удалось распознать речь")
        self.fake.error = None
        self.fake.text = "  "
        e.bot.handle_update(voice_msg(mid=62, thread=self.th))
        self.assertEqual(e.tg.of("sendMessage")[-1]["text"], "Речь не распознана")
        self.assertEqual(voice_cards(e), [])

    def test_too_big_voice_is_not_downloaded(self):
        e = self.e
        upd = voice_msg(thread=self.th)
        upd["message"]["voice"]["file_size"] = 30 * 1024 * 1024
        e.bot.handle_update(upd)
        self.assertEqual(e.tg.of("getFile"), [])
        self.assertEqual(self.fake.calls("stt.transcribe"), [])
        self.assertIn("20 МБ", e.tg.of("sendMessage")[-1]["text"])

    def test_log_has_no_recognised_text(self):
        e = self.e
        with mock.patch.object(cc, "log") as lg:
            e.bot.handle_update(voice_msg(thread=self.th))
            c = self.card()
            e.bot.handle_update(e.cb(buttons(c["reply_markup"])[0]["callback_data"], mid=e.tg.n))
        logged = " ".join(str(a) for call in lg.call_args_list for a in call.args)
        self.assertNotIn("привет", logged)
        self.assertIn("local", logged)

    def test_long_text_is_capped_in_store(self):
        e = self.e
        self.fake.text = "я" * 20000
        e.bot.handle_update(voice_msg(thread=self.th))
        for v in e.bot.store.data["voices"].values():
            self.assertLessEqual(len(v["text"]), cc.VOICE_TEXT_MAX)
        for i in range(cc.VOICE_KEEP + 10):
            e.bot.handle_update(voice_msg(mid=100 + i, thread=self.th))
        self.assertLessEqual(len(e.bot.store.data["voices"]), cc.VOICE_KEEP)


class SttWebTests(unittest.TestCase):
    def setUp(self):
        self.w = WebEnv(self)
        self.e = self.w.e
        self.fake = FakeStt(self)
        stt_attach(self.e, self.fake)

    def post(self, body=b"OGGDATA", ctype="audio/ogg", **kw):
        return self.w.req("POST", "/api/voice", headers={"Content-Type": ctype}, raw_body=body, **kw)

    def test_ok_contract_and_lock_not_held(self):
        st, obj, _ = self.post()
        self.assertEqual((st, obj), (200, {"ok": True, "text": "привет мир", "seconds": 3.5, "engine": "local"}))
        self.assertEqual(self.fake.calls("stt.transcribe")[0]["lang"], "ru")
        self.assertEqual(self.fake.seen[0][:2], (True, 7))
        self.assertEqual(self.fake.lock_free, [True])
        p = self.fake.calls("stt.transcribe")[0]["path"]
        self.assertFalse(os.path.exists(p))

    def test_failures(self):
        self.fake.text = ""
        st, obj, _ = self.post()
        self.assertEqual((st, obj["ok"], obj["code"]), (200, False, "empty"))
        self.fake.error = "internal"
        st, obj, _ = self.post()
        self.assertEqual((st, obj["ok"], obj["code"]), (200, False, "error"))
        self.e.bot.stt = cc.SttClient(os.path.join(self.e.tmp.name, "none.sock"))
        st, obj, _ = self.post()
        self.assertEqual((st, obj["ok"], obj["code"]), (200, False, "unavailable"))
        self.assertTrue(obj["error"])

    def test_auth_type_size_and_method(self):
        self.assertEqual(self.post(auth=False)[0], 401)
        self.assertEqual(self.fake.requests, [])
        self.assertEqual(self.post(ctype="application/json")[0], 415)
        st, obj, _ = self.post(body=b"")
        self.assertEqual((st, obj["ok"]), (400, False))
        s = socket.create_connection(("127.0.0.1", self.w.srv.port), timeout=5)  # the limit is checked on the header
        s.sendall(("POST /api/voice HTTP/1.1\r\nHost: x\r\nContent-Type: audio/ogg\r\nX-Tg-Init-Data: %s\r\n"
                   "Content-Length: %d\r\n\r\n" % (make_init_data(), cc.VOICE_MAX + 1)).encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        self.assertTrue(data.startswith(b"HTTP/1.0 413") or data.startswith(b"HTTP/1.1 413"), data[:40])
        self.assertIn(b'"too_large"', data)
        self.assertEqual(self.fake.calls("stt.transcribe"), [])
        self.assertEqual(self.e.bot.api_dispatch(1, "DELETE", "/api/voice", {})[0], 405)

    def test_voice_flag_for_the_front(self):
        st, obj, _ = self.w.req("GET", "/api/voice")
        self.assertEqual((st, obj), (200, {"voice": True}))
        self.assertIs(self.w.req("GET", "/api/sessions")[1]["voice"], True)
        self.fake.engines[0]["ready"] = False
        self.e.bot._stt_ready = (0.0, False)
        self.assertEqual(self.w.req("GET", "/api/voice")[1], {"voice": False})
        self.assertIs(self.w.req("GET", "/api/sessions")[1]["voice"], False)


# ---------------------------------------------------------------- permission requests notify always (15.10)

def perm_env_unwatched(tc, sub_count=0, topics=False, screen=PERM_FIX):
    """Like perm_env, but the session is NOT subscribed to."""
    w, e = push_env(tc, sub_count=sub_count)
    e.bot.topics_enabled = topics
    mk(e, "s1", "working", 1, name="Альфа")
    e.agents[-1]["pid"] = 4242
    e.bot.list_sessions(True)
    fs = FakeScreen(EMPTY_PROMPT)
    e.bot.tmux = fs.tmux()
    e.bot.notify_tick()
    set_state(e, "blocked")
    fs.screen = screen
    return w, e, fs


class PermAlwaysTests(unittest.TestCase):
    def notice(self, e):
        return [m for m in e.tg.of("sendMessage") if "🔐" in m["text"]]

    def test_unwatched_tmux_permission_goes_to_telegram_with_buttons(self):
        w, e, fs = perm_env_unwatched(self)
        e.bot.notify_tick()
        e.bot.notify_tick()
        msgs = self.notice(e)
        self.assertEqual(len(msgs), 1)
        self.assertIn("🔐 Альфа: нужно ваше разрешение — Bash command", msgs[0]["text"])
        self.assertIn("echo hello-from-bash", msgs[0]["text"])
        self.assertEqual(len([b for b in buttons(msgs[0]["reply_markup"]) if b["callback_data"].startswith("pk:")]), 4)
        self.assertFalse(e.bot.is_watched("s1"))
        self.assertFalse(e.bot.tracked_c(e.bot.store.chat(1), "s1"))

    def test_unwatched_tmux_permission_pushes_in_the_app_wording(self):
        w, e, fs = perm_env_unwatched(self, sub_count=1)
        e.bot.notify_tick()
        e.bot.notify_tick()
        self.assertEqual(len(e.sent_pushes), 1)
        payload, h = decode_push(e)
        self.assertEqual(payload["title"], "Альфа: нужно разрешение")
        self.assertTrue(payload["body"].startswith("Bash command: echo hello-from-bash"), payload["body"])
        self.assertEqual(h["Urgency"], "high")
        self.assertEqual(self.notice(e), [])  # delivered as a push: Telegram is quiet

    def test_unwatched_session_gets_no_other_notifications(self):
        w, e, fs = perm_env_unwatched(self, screen=EMPTY_PROMPT)
        n = len(e.tg.of("sendMessage"))
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("sendMessage")), n)

    def test_topic_is_created_but_does_not_subscribe(self):
        w, e, fs = perm_env_unwatched(self, topics=True)
        e.bot.notify_tick()
        c = e.bot.store.chat(1)
        self.assertIn("s1", c["topics"])
        self.assertEqual(len(self.notice(e)), 1)
        self.assertFalse(e.bot.tracked_c(c, "s1"))
        e.bot.forward_tick()
        self.assertNotIn("s1", c["fw"])

    def test_notice_collapses_when_the_dialog_disappears(self):
        w, e, fs = perm_env_unwatched(self)
        e.bot.notify_tick()
        mid = self.notice(e)[0].get("message_id") or e.tg.of("sendMessage")[-1].get("message_id")
        fs.screen = EMPTY_PROMPT
        e.bot.notify_tick()
        ed = e.tg.of("editMessageText")
        self.assertEqual(len(ed), 1)
        self.assertTrue(ed[0]["text"].startswith("🔐 Bash command · "), ed[0]["text"])
        self.assertTrue(ed[0]["text"].endswith("· закрыто"), ed[0]["text"])
        self.assertNotIn("reply_markup", ed[0])
        e.bot.notify_tick()
        self.assertEqual(len(e.tg.of("editMessageText")), 1)

    def windowless(self, e, fs):
        """The tmux of a background session: no window until the bot opens one; the job asks for permission."""
        fs.window = False
        real = fs.runner

        def runner(args, timeout=30, cwd=None, input_text=None):
            if args[1] in ("capture-pane", "has-session") and not fs.window:
                return 1, ""
            if args[1] == "new-session":
                fs.window = True
            return real(args, timeout, cwd, input_text)
        e.bot.tmux.run = runner
        e.job("s1", name="Альфа", state="working", detail="d", cwd="/tmp", updatedAt=iso(BASE - 30),
              needs="approve Bash: echo hello-from-bash")
        e.agents[0]["status"] = "idle"
        e.bot.list_sessions(True)

    def test_window_closed_by_the_idle_cleanup_keeps_the_notice(self):
        w, e, fs = perm_env_unwatched(self)
        self.windowless(e, fs)
        for _ in range(3):
            e.bot.notify_tick()
        self.assertEqual(len(self.notice(e)), 1)
        fs.window = False  # the cleanup killed it
        e.bot._perm_opened.clear()
        for _ in range(3):
            e.bot.notify_tick()
        self.assertTrue(fs.window)
        self.assertEqual(e.tg.of("editMessageText"), [])  # not «closed»
        self.assertEqual(len(self.notice(e)), 1)  # and not again

    def test_restart_with_a_windowless_open_dialog_does_not_push(self):
        w, e, fs = perm_env_unwatched(self, sub_count=1)
        self.windowless(e, fs)
        e.bot.primed = False  # a fresh start: the dialog was open before it
        for _ in range(3):
            e.bot.notify_tick()
        self.assertTrue(fs.window)
        self.assertEqual((len(self.notice(e)), len(e.sent_pushes)), (0, 0))

    def test_blocked_session_without_a_window_gets_one_and_its_notice(self):
        w, e, fs = perm_env_unwatched(self)
        fs.window = False
        real = fs.runner

        def runner(args, timeout=30, cwd=None, input_text=None):
            if args[1] in ("capture-pane", "has-session") and not fs.window:
                return 1, ""  # a background session nobody attached to yet
            if args[1] == "new-session":
                fs.window = True
            return real(args, timeout, cwd, input_text)
        e.bot.tmux.run = runner
        e.bot.notify_tick()  # blocked only means "waits for the owner": no window for a finished turn
        self.assertFalse(fs.window)
        e.job("s1", name="Альфа", state="working", detail="d", cwd="/tmp", updatedAt=iso(BASE - 30),
              needs="approve Bash: echo hello-from-bash")
        e.agents[0]["status"] = "idle"
        e.bot.list_sessions(True)
        e.bot.notify_tick()
        self.assertTrue(fs.window)
        e.bot.notify_tick()
        self.assertEqual(len(self.notice(e)), 1)

    def test_notice_collapses_after_a_bot_restart(self):
        w, e, fs = perm_env_unwatched(self)
        e.bot.notify_tick()
        e.bot.store = cc.Store(e.bot.store.path)  # restart: only what was saved survives
        e.bot._perm_cache, e.bot._perm_msgs = {}, e.bot.perm_msgs_load()
        fs.screen = EMPTY_PROMPT
        e.bot.notify_tick()
        ed = e.tg.of("editMessageText")
        self.assertEqual(len(ed), 1)
        self.assertTrue(ed[0]["text"].endswith("· закрыто"), ed[0]["text"])

    def test_notice_collapses_as_decided_when_answered_via_button(self):
        w, e, fs = perm_env_unwatched(self)
        e.bot.notify_tick()
        msg = self.notice(e)[0]
        pk = [b for b in buttons(msg["reply_markup"]) if b["callback_data"].startswith("pk:")]
        fs.on_key = lambda ks, f: setattr(f, "screen", EMPTY_PROMPT)
        e.bot.handle_update(e.cb(pk[0]["callback_data"], mid=77))
        e.bot.notify_tick()
        ed = e.tg.of("editMessageText")
        self.assertEqual(len(ed), 1)
        self.assertTrue(ed[0]["text"].endswith("· ✔ решено"), ed[0]["text"])

    def test_sdk_untracked_session_shows_permission_and_collapses(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake, watch=False)
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        msgs = self.notice(e)
        self.assertEqual(len(msgs), 1)
        self.assertIn("нужно ваше разрешение — Bash", msgs[0]["text"])
        self.assertFalse(e.bot.is_watched(SID))
        self.assertFalse(e.bot.tracked_c(e.bot.store.chat(1), SID))
        e.bot.sdk_on_event({"event": "request.closed", "data": {"id": SID, "req": "r-00000001", "decision": "allow"}})
        self.assertTrue(e.tg.of("editMessageText")[-1]["text"].startswith("🔐 Bash · ls -la"))

    def test_sdk_untracked_pushes_in_the_app_wording(self):
        fake = FakeSdk(self)
        e = sdk_env(self, fake, watch=False)
        sent = []
        e.bot.push_notify = lambda uid, sid, body, urgency="high", title=None: sent.append((sid, body, title)) or True
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": SID, "request": perm_req()}})
        self.assertEqual(sent, [(SID, "Bash: ls -la", "Fix the bug: нужно разрешение")])
        self.assertEqual(self.notice(e), [])


class PermPanelTests(unittest.TestCase):
    def setUp(self):
        self.e = panel_env(self)
        e = self.e
        mk(e, "p1", "blocked", 2, name="Пэ")
        e.agents[-1]["pid"] = 4242
        mk(e, "w2", "blocked", 9)
        mk(e, "r1", "working", 10)
        e.bot.tmux = FakeScreen(PERM_FIX).tmux()
        e.bot.list_sessions(True)
        e.bot.perm_scan(e.bot.list_sessions(True), prime=True)

    def test_group_on_top_with_command_counter_and_button(self):
        html, plain, markup = self.e.bot.panel_render(1)
        lines = plain.split("\n")
        self.assertEqual(lines[0], "🔐 1 · 🟡 1 ждут · 🔵 1 в работе")
        self.assertLess(plain.index("🔐 НУЖНО РАЗРЕШЕНИЕ"), plain.index("ЖДУТ ВАС"))
        self.assertIn("Bash command: echo hello-from-bash", plain)
        self.assertEqual(plain.count("Пэ"), 1)
        self.assertEqual(markup["inline_keyboard"][0][0]["text"], "🔐 Пэ")
        self.assertEqual(markup["inline_keyboard"][0][0]["callback_data"], "s:p1")
        self.assertEqual(markup["inline_keyboard"][1][0]["text"], "🟡 w2")

    def test_no_perm_group_without_permissions(self):
        e = self.e
        e.bot._perm_cache.clear()
        html, plain, markup = e.bot.panel_render(1)
        self.assertNotIn("НУЖНО РАЗРЕШЕНИЕ", plain)
        self.assertNotIn("🔐", plain)
        self.assertEqual(plain.split("\n")[0], "🟡 2 ждут · 🔵 1 в работе")


GROUP = -1001234


def responder_env(tc, rate=30):
    fake = FakeSdk(tc)
    e = sdk_env(tc, fake, watch=False)
    e.bot.cfg.responder_chat = GROUP
    e.bot.cfg.responder_cwd = e.tmp.name
    e.bot.cfg.responder_rate = rate
    e.bot.cfg.responder_log = os.path.join(e.tmp.name, "responder.jsonl")
    e.bot.chat_username = "cctg_bot"
    return e, fake


def gmsg(text, mid=10, chat=GROUP, uid=777, name="Анна", **extra):
    m = {"message_id": mid, "from": {"id": uid, "first_name": name}, "chat": {"id": chat, "type": "supergroup"}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"update_id": mid, "message": m}


class ResponderTests(unittest.TestCase):
    def done(self, e, sid, text, subtype="success"):
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": sid, "subtype": subtype, "cost_usd": 0.01,
                                                              "is_error": subtype != "success", "result": text}})

    def to_group(self, e):
        return [m for m in e.tg.of("sendMessage") if m["chat_id"] == GROUP]

    def test_config_keys(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        t = td.name
        envf = os.path.join(t, "env")
        with open(envf, "w") as f:
            f.write("TG_BOT_TOKEN=T\nCC_RESPONDER_CHAT=-100777\nCC_RESPONDER_CWD=%s\n" % t)
        cfg = cc.Config(env_path=envf, environ={"HOME": t})
        self.assertEqual((cfg.responder_chat, cfg.responder_cwd, cfg.responder_rate), (-100777, t, 30))
        self.assertIsNone(cc.Config(env_path=os.path.join(t, "none"), environ={"HOME": t}).responder_chat)

    def test_question_starts_a_chat_session_wrapped_as_a_participant_question(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot какая точность за октябрь?"))
        start = fake.calls("session.start")
        self.assertEqual(len(start), 1)
        p = start[0]
        self.assertIs(p["chat"], True)
        self.assertEqual(p["cwd"], e.tmp.name)
        self.assertIn("какая точность за октябрь?", p["prompt"])
        self.assertIn("Анна", p["prompt"])
        self.assertIn("confirm", p["prompt"])
        self.assertNotIn("@cctg_bot", p["prompt"])
        self.assertEqual(e.bot.store.data["responder"]["sid"], "s-aabbccdd")

    def test_a_question_waits_for_a_runner_that_is_still_starting(self):
        e, fake = responder_env(self)
        real, calls = e.bot.sdk.call, []
        def call(method, params=None, timeout=2.0):
            calls.append(method)
            if method == "session.start" and calls.count("session.start") == 1:
                raise cc.HerdrError("unavailable", "no socket yet")
            return real(method, params, timeout)
        e.bot.sdk.call = call
        jobs = []
        e.bot._spawn = lambda fn, *a: jobs.append(fn)
        e.bot.sleep = lambda s: None
        e.bot.handle_update(gmsg("@cctg_bot вопрос", mid=10))
        self.assertFalse(self.to_group(e))  # no «could not answer» while the runner starts
        jobs.pop(0)()
        self.assertEqual(calls.count("session.start"), 2)
        e.bot.clock = lambda: 10 ** 10  # past the wait: a runner that never comes is a plain failure
        e.bot._resp_busy = None
        e.bot._resp_queue.insert(0, {"mid": 11, "q": "x", "who": "Гость", "ctx": None, "from": 7, "ts": 0, "first_ts": 0})
        calls.clear()
        def down(method, params=None, timeout=2.0):
            raise cc.HerdrError("unavailable", "down")
        e.bot.sdk.call = down
        e.bot.responder_next()
        self.assertTrue(self.to_group(e))

    def test_answer_can_attach_a_document_from_docs_only(self):
        e, fake = responder_env(self)
        os.makedirs(os.path.join(e.tmp.name, "docs"))
        with open(os.path.join(e.tmp.name, "docs", "x.md"), "w") as f:
            f.write("текст")
        with open(os.path.join(e.tmp.name, ".env"), "w") as f:
            f.write("SECRET=1")
        out, files = e.bot.responder_files("Вот файл.\n[[файл: docs/x.md]]\n[[файл: .env]]\n[[файл: docs/../.env]]")
        self.assertEqual(files, [os.path.realpath(os.path.join(e.tmp.name, "docs", "x.md"))])
        self.assertIn("Вот файл.", out)
        self.assertNotIn("[[", out)
        self.assertEqual(out.count("отправить не могу"), 2)
        self.assertEqual(e.bot.responder_files("без файла")[1], [])

    def test_answer_goes_back_as_a_reply_and_the_next_question_follows(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot первый", mid=10))
        e.bot.handle_update(gmsg("второй", mid=11, reply_to_message={"message_id": 5, "from": {"username": "cctg_bot"}}))
        self.assertEqual(len(fake.calls("session.start")) + len(fake.calls("session.send")), 1)  # one at a time
        self.done(e, "s-aabbccdd", "Ответ один: https://example.com")
        out = self.to_group(e)
        self.assertEqual(out[-1]["text"], "Ответ один: https://example.com")
        self.assertEqual(out[-1]["reply_parameters"]["message_id"], 10)
        sends = fake.calls("session.send")
        self.assertEqual(len(sends), 1)
        self.assertIn("второй", sends[0]["text"])
        with open(e.bot.cfg.responder_log, encoding="utf-8") as f:
            rec = json.loads(f.readline())
        self.assertEqual((rec["q"], rec["a"], rec["from"]), ("первый", "Ответ один: https://example.com", 777))

    def test_other_chats_unaddressed_and_non_text_are_ignored(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot вопрос", chat=-5))
        e.bot.handle_update(gmsg("просто болтаем"))
        e.bot.handle_update(gmsg(None, photo=[{"file_id": "x"}], caption="@cctg_bot"))
        self.assertEqual(fake.calls("session.start") + fake.calls("session.send"), [])
        self.assertEqual(e.tg.of("sendMessage"), [])

    def test_commands_from_the_chat_are_only_questions(self):
        e, fake = responder_env(self)
        n = len(e.runs)
        e.bot.handle_update(gmsg("/new@cctg_bot rm -rf /"))
        self.assertEqual([r for r in e.runs[n:] if r[0][:1] != ["git"]], [])  # no tmux session started (only the clone pull)
        self.assertIs(fake.calls("session.start")[0]["chat"], True)
        self.assertEqual(self.to_group(e), [])  # no panel in the group

    def test_hourly_limit(self):
        e, fake = responder_env(self, rate=3)
        for i in range(5):
            e.bot.handle_update(gmsg("@cctg_bot q%d" % i, mid=20 + i))
        for _ in range(5):
            self.done(e, "s-aabbccdd", "ok")
        asked = [p["prompt"] for p in fake.calls("session.start")] + [p["text"] for p in fake.calls("session.send")]
        self.assertEqual(len(asked), 3)

    def test_owner_stop_word(self):
        e, fake = responder_env(self)
        e.bot.handle_update(e.msg("/responder off"))
        e.bot.handle_update(gmsg("@cctg_bot вопрос"))
        self.assertEqual(fake.calls("session.start"), [])
        e.bot.handle_update(e.msg("/responder on"))
        e.bot.handle_update(gmsg("@cctg_bot вопрос", mid=12))
        self.assertEqual(len(fake.calls("session.start")), 1)

    def test_change_waits_for_the_owner_in_private(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot поправь опечатку на главной"))
        sid = e.bot.store.data["responder"]["sid"]
        fake.sessions[sid] = fake.session(id=sid, state="blocked", chat=True, pending=[perm_req(inp={"command": "git commit -am typo"})])
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": sid, "request": perm_req(inp={"command": "git commit -am typo"})}})
        self.assertIn("подтверждения владельца", self.to_group(e)[-1]["text"])
        self.assertIn("git commit -am typo", self.to_group(e)[-1]["text"])
        to_owner = [m for m in e.tg.of("sendMessage") if m["chat_id"] == 1 and "🔐" in m["text"]]
        self.assertEqual(len(to_owner), 1)  # the buttons are only in the owner's chat
        note = [m for m in e.tg.of("sendMessage") if m["chat_id"] == 1 and "поправь опечатку" in m["text"]]
        self.assertEqual(len(note), 1)  # whose request it is
        self.assertIn("Анна", note[0]["text"])
        self.assertTrue(all("reply_markup" not in m for m in self.to_group(e)))
        self.done(e, sid, "Готово: опечатка исправлена.")
        self.assertEqual(self.to_group(e)[-1]["text"], "Готово: опечатка исправлена.")

    def test_undecided_change_goes_to_the_backlog_and_runs_later_as_the_owners(self):
        e, fake = responder_env(self)
        t = {"v": 1000.0}
        e.bot.clock = lambda: t["v"]
        e.bot.handle_update(gmsg("@cctg_bot поправь опечатку на главной"))
        sid = e.bot.store.data["responder"]["sid"]
        req = perm_req(inp={"command": "touch x"})
        fake.sessions[sid] = fake.session(id=sid, state="blocked", chat=True, pending=[req])
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": sid, "request": req}})
        self.assertEqual([x["status"] for x in e.bot.backlog()], ["waiting"])  # stored at once: a restart keeps it
        e.bot.responder_typing_tick()
        self.assertEqual(fake.calls("session.answer"), [])  # not yet
        t["v"] += cc.RESPONDER_WAIT + 1
        e.bot.responder_typing_tick()
        self.assertEqual([c["decision"] for c in fake.calls("session.answer")], ["deny"])
        self.assertIn("бэклог", self.to_group(e)[-1]["text"])
        card = [m for m in e.tg.of("sendMessage") if m["chat_id"] == 1 and "📋" in m["text"]][-1]
        self.assertIn("поправь опечатку", card["text"])
        self.assertEqual(card["text"], "📋 " + e.bot.backlog_text(e.bot.backlog()[0]))  # the card is exactly what runs
        self.done(e, sid, "Не вышло: нет подтверждения.")
        self.assertEqual(e.bot.backlog()[0]["status"], "open")  # the denial did not close it
        bid = e.bot.backlog()[0]["id"]
        e.bot.handle_update(e.cb("bl:%s:run" % bid))
        q = fake.calls("session.start")[-1]  # the owner's own request: the session that may write in the project
        self.assertIs(q["write"], True)
        self.assertIs(fake.calls("session.start")[0]["write"], False)
        q["text"] = q["prompt"]
        sid = e.bot.store.data["responder"]["sid"]
        self.assertEqual(e.bot.store.data["responder"]["sid_owner"], sid)
        self.assertIn("Владелец подтвердил", q["text"])
        self.assertIn("поправь опечатку", q["text"])
        self.assertEqual(q["model"], "opus")
        auto = perm_req(req="r-00000009", inp={"command": "touch x"})
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": sid, "request": auto}})
        self.assertEqual(fake.calls("session.answer")[-1]["decision"], "allow")  # the owner's own request now
        self.done(e, sid, "Готово.")
        self.assertEqual(e.bot.backlog()[0]["status"], "done")
        n = len(fake.calls("session.start")) + len(fake.calls("session.send"))
        e.bot.handle_update(e.cb("bl:%s:run" % bid))
        self.assertEqual(len(fake.calls("session.start")) + len(fake.calls("session.send")), n)  # not run twice

    def test_backlog_survives_a_restart_and_is_listed(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot удали старый файл"))
        req = perm_req(inp={"command": "rm old"})
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": e.bot.store.data["responder"]["sid"], "request": req}})
        sid = e.bot.store.data["responder"]["sid"]
        fake.sessions[sid] = fake.session(id=sid, state="blocked", chat=True, pending=[req])
        e.bot.backlog_on_start()  # the bot started again while the request waited (the runner did not)
        self.assertEqual(e.bot.backlog()[0]["status"], "open")
        self.assertEqual([c["decision"] for c in fake.calls("session.answer")], ["deny"])  # only the card runs it
        n = len(e.tg.of("sendMessage"))
        e.bot.handle_update(e.msg("/backlog"))
        self.assertIn("удали старый файл", e.tg.of("sendMessage")[n]["text"])
        e.bot.handle_update(e.cb("bl:%s:drop" % e.bot.backlog()[0]["id"]))
        self.assertEqual(e.bot.backlog()[0]["status"], "dropped")
        e.bot.handle_update(e.msg("/backlog"))
        self.assertIn("пуст", e.tg.of("sendMessage")[-1]["text"])

    def test_the_owner_is_named_by_id_not_by_name(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot вопрос", uid=1, name="Alex"))
        self.assertIn("Alex (the owner himself", fake.calls("session.start")[0]["prompt"])
        self.done(e, "s-aabbccdd", "ok")
        e.bot.handle_update(gmsg("@cctg_bot вопрос", mid=11, name="Alex"))  # same name, another id
        self.assertNotIn("the owner himself", fake.calls("session.start")[-1]["prompt"])  # the guests' session

    def test_budget_out_mid_question_retries_it_once_in_a_fresh_session(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot + США и Азия"))
        self.done(e, "s-aabbccdd", None, subtype="error_max_budget_usd")
        self.assertEqual(len(fake.calls("session.start")), 2)  # the same question, a fresh session
        self.assertIn("США и Азия", fake.calls("session.start")[1]["prompt"])
        self.assertEqual(self.to_group(e), [])  # no «could not answer» yet
        self.done(e, "s-aabbccdd", None, subtype="error_max_budget_usd")
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])  # only once
        self.assertEqual(len(fake.calls("session.start")), 2)

    def fake_codex(self):
        """A signed-in Codex of its own in a temporary home: the tests need no codex on the machine, only bwrap."""
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        home = self.codex_home = td.name
        os.makedirs(os.path.join(home, ".codex"))
        open(os.path.join(home, ".codex", "auth.json"), "w").close()
        os.makedirs(os.path.join(home, "pkg", "bin"))
        codex = os.path.join(home, "pkg", "bin", "codex")
        with open(codex, "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(codex, 0o755)
        return {"HOME": home, "CC_CODEX_BIN": codex}

    def codex_case(self, env=None, rc=0, out="Ответ кодекса"):
        e, fake = responder_env(self)
        calls = []

        def run(cmd, **kw):
            calls.append((cmd, kw))
            return subprocess.CompletedProcess(cmd, rc, out, "")
        e.bot._spawn = lambda fn, *a: fn(*a)
        with mock.patch.dict(os.environ, {**self.fake_codex(), **(env or {})}), mock.patch.object(cc.subprocess, "run", run):
            e.bot.handle_update(gmsg("@cctg_bot вопрос про квоту"))
            e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(
                id="s-aabbccdd", state="error", chat=True, detail="rate_limit")}})
        return e, fake, calls

    def test_rate_limit_answers_through_codex(self):
        e, fake, calls = self.codex_case()
        cmd, kw = calls[0]
        self.assertTrue(cmd[0].endswith("bwrap"))  # Codex «read-only» still reads the home: confined to the clone
        self.assertIn(self.codex_home, cmd[cmd.index("--tmpfs", cmd.index("/tmp")) + 1:][:1])
        i = cmd.index("exec")
        self.assertEqual(cmd[i:i + 3], ["exec", "--sandbox", "read-only"])
        self.assertEqual(cmd[-2], "--")
        self.assertEqual(cmd[cmd.index("-C") + 1], e.bot.cfg.responder_cwd)
        self.assertIs(kw["stdin"], cc.subprocess.DEVNULL)
        self.assertIn("вопрос про квоту", cmd[-1])
        self.assertIn("Ответ кодекса", self.to_group(e)[-1]["text"])
        self.assertIn("ответил Codex", self.to_group(e)[-1]["text"])
        self.assertEqual(len(fake.calls("session.start")), 1)  # no retry in the same limit

    def test_empty_credit_balance_answers_through_codex(self):
        e, fake = responder_env(self)
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "Ответ кодекса", "")
        e.bot._spawn = lambda fn, *a: fn(*a)
        with mock.patch.dict(os.environ, self.fake_codex()), mock.patch.object(cc.subprocess, "run", run):
            e.bot.handle_update(gmsg("@cctg_bot как успехи?"))
            # verbatim from the 02.11 outage: the CLI's own result on an API key with no credit
            e.bot.sdk_on_event({"event": "turn.result", "data": {
                "id": "s-aabbccdd", "subtype": "success", "is_error": True, "cost_usd": 0.0,
                "result": "Credit balance is too low"}})
        self.assertEqual(len(calls), 1)
        self.assertIn("Ответ кодекса", self.to_group(e)[-1]["text"])

    def test_blocked_clone_pull_tells_the_owner_once(self):
        e, fake = responder_env(self)
        os.makedirs(os.path.join(e.tmp.name, ".git"))
        e.bot.cfg.allowed = {42}
        sent, results = [], [(1, ""), (0, " M docs/a.md"), (1, ""), (0, " M docs/a.md"), (0, ""), (1, ""), (0, "")]
        e.bot.run = lambda *a, **k: results.pop(0)
        e.bot.tg.send = lambda uid, text, **k: sent.append((uid, text))
        for _ in range(4):
            e.bot._resp_pulled = -10000
            e.bot.responder_pull()
        self.assertEqual(len(sent), 2)  # one per failure streak: not on every question, again after a recovery
        self.assertIn("docs/a.md", sent[0][1])
        self.assertIn("git pull failed", sent[1][1])

    def engine_case(self, rc=0, model="", n=1):
        e, fake = responder_env(self)
        e.bot.cfg.responder_engine, e.bot.cfg.codex_model = "codex", model
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, rc if len(calls) == 1 else 0, "Ответ кодекса", "")
        e.bot._spawn = lambda fn, *a: fn(*a)
        with mock.patch.object(cc.subprocess, "run", run), mock.patch.object(
                cc, "codex_boxed", lambda cwd, prompt, model="": ["codex", "exec"] + (["-m", model] if model else []) + ["--", prompt]):
            for i in range(n):
                e.bot.handle_update(gmsg("@cctg_bot вопрос %d" % i, mid=10 + i))
        return e, fake, calls

    def test_codex_engine_routes_without_sdk_calls(self):
        e, fake, calls = self.engine_case()
        self.assertEqual(fake.calls("session.start") + fake.calls("session.send"), [])
        self.assertNotIn("-m", calls[0])
        self.assertIn("Ответ кодекса", self.to_group(e)[-1]["text"])
        self.assertNotIn("ответил Codex", self.to_group(e)[-1]["text"])
        self.assertIn("only read and answer", calls[0][-1])
        self.assertNotIn("confirm", calls[0][-1])

    def test_codex_engine_passes_the_model(self):
        e, fake, calls = self.engine_case(model="gpt-x")
        self.assertEqual(calls[0][calls[0].index("-m") + 1], "gpt-x")

    def test_codex_engine_failure_is_neutral_and_the_queue_moves_on(self):
        e, fake, calls = self.engine_case(rc=1, n=2)
        self.assertEqual(len(calls), 2)
        texts = [m["text"] for m in self.to_group(e)]
        self.assertIn("Передам владельцу", texts[0])
        self.assertIn("Ответ кодекса", texts[1])

    def codex_quota_case(self, claude_out=False):
        e, fake = responder_env(self)
        e.bot.cfg.responder_engine = "codex"
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: You've hit your usage limit. Try again later.")
        e.bot._spawn = lambda fn, *a: fn(*a)
        with mock.patch.object(cc.subprocess, "run", run), mock.patch.object(
                cc, "codex_boxed", lambda cwd, prompt, model="": ["codex", "exec", "--", prompt]):
            e.bot.handle_update(gmsg("@cctg_bot вопрос"))
            if claude_out:
                e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(
                    id="s-aabbccdd", state="error", chat=True, detail="rate_limit")}})
        return e, fake, calls

    def test_codex_quota_hands_the_question_to_the_sdk_once(self):
        e, fake, calls = self.codex_quota_case()
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(fake.calls("session.start")), 1)  # the console's model takes it over
        self.done(e, "s-aabbccdd", "Ответ Claude")
        text = self.to_group(e)[-1]["text"]
        self.assertIn("Ответ Claude", text)
        self.assertIn("квота Codex исчерпана", text)

    def test_both_quotas_out_is_one_plain_failure_without_ping_pong(self):
        e, fake, calls = self.codex_quota_case(claude_out=True)
        self.assertEqual(len(calls), 1)  # Codex is not asked a second time
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])

    def test_codex_engine_off_keeps_the_sdk_and_fallback(self):
        e, fake, calls = self.codex_case()
        self.assertEqual(len(fake.calls("session.start")), 1)
        self.assertNotIn("-m", calls[0][0])
        self.assertNotIn("only read and answer", calls[0][0][-1])
        self.assertIn("ответил Codex", self.to_group(e)[-1]["text"])

    def test_codex_failure_is_a_plain_failure(self):
        e, fake, calls = self.codex_case(rc=1)
        self.assertEqual(len(calls), 1)
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])

    def test_codex_fallback_can_be_switched_off(self):
        e, fake, calls = self.codex_case(env={"CC_RESPONDER_CODEX_FALLBACK": "0"})
        self.assertEqual(calls, [])
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])

    def test_failed_turn_answers_and_next_question_starts_a_fresh_session(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot q1"))
        self.done(e, "s-aabbccdd", None, subtype="error_during_execution")  # not the budget: no retry
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])
        e.bot.handle_update(gmsg("@cctg_bot q2", mid=11))
        self.assertEqual(len(fake.calls("session.start")), 2)
        self.assertEqual(fake.calls("session.send"), [])

    def test_session_is_replaced_after_a_number_of_questions(self):
        e, fake = responder_env(self, rate=100)
        for i in range(cc.RESPONDER_TURNS + 1):
            e.bot.handle_update(gmsg("@cctg_bot q%d" % i, mid=30 + i))
            self.done(e, "s-aabbccdd", "ok")
        self.assertEqual(len(fake.calls("session.start")), 2)  # untrusted turns do not pile up in one context
        self.assertEqual(len(fake.calls("session.send")), cc.RESPONDER_TURNS - 1)

    def test_session_dying_without_a_result_frees_the_queue(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot q1"))
        e.bot.handle_update(gmsg("@cctg_bot q2", mid=11))
        e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(
            id="s-aabbccdd", state="interrupted", chat=True)}})
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])
        self.assertEqual(len(fake.calls("session.start")), 2)  # q2 goes to a fresh session

    def test_a_session_that_cannot_resume_gets_the_question_in_a_fresh_one(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot q1"))
        e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(
            id="s-aabbccdd", state="error", chat=True, detail="No conversation found")}})
        self.assertFalse(self.to_group(e))  # not «could not answer»: the same question went to a fresh session
        self.assertEqual(len(fake.calls("session.start")), 2)
        sid = e.bot.store.data["responder"]["sid"]
        e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(id=sid, state="error", chat=True)}})
        self.assertIn("Передам владельцу", self.to_group(e)[-1]["text"])  # the second failure is a plain one

    def test_stuck_question_is_released_after_a_timeout(self):
        e, fake = responder_env(self)
        now = [1000.0]
        e.bot.clock = lambda: now[0]
        e.bot.handle_update(gmsg("@cctg_bot q1"))
        now[0] += cc.RESPONDER_TIMEOUT + 1
        e.bot.handle_update(gmsg("@cctg_bot q2", mid=11))
        self.assertEqual(len(fake.calls("session.start")), 2)

    def test_chat_session_is_in_the_console_so_its_requests_reach_the_owner(self):
        e, fake = responder_env(self)
        fake.sessions["s-aabbccdd"] = fake.session(id="s-aabbccdd", chat=True)
        e.bot.sdk_on_event({"event": "session.created", "data": {"session": fake.sessions["s-aabbccdd"]}})
        self.assertIn("s-aabbccdd", e.bot.known)

    def test_question_waiting_for_the_owner_is_not_timed_out(self):
        e, fake = responder_env(self)
        now = [1000.0]
        e.bot.clock = lambda: now[0]
        e.bot.handle_update(gmsg("@cctg_bot q1"))
        e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(
            id="s-aabbccdd", state="blocked", chat=True)}})
        now[0] += cc.RESPONDER_TIMEOUT + 1
        e.bot.handle_update(gmsg("@cctg_bot q2", mid=11))
        self.assertEqual(len(fake.calls("session.start")), 1)
        self.assertEqual(self.to_group(e), [])

    def test_id_command_in_a_new_group(self):
        e, fake = responder_env(self)
        e.bot.cfg.responder_chat = None
        e.bot.handle_update(gmsg("@cctg_bot id", chat=-100999))
        msgs = e.tg.of("sendMessage")
        self.assertEqual([m["chat_id"] for m in msgs], [1])  # only the owner, nothing in the group
        self.assertIn("-100999", msgs[0]["text"])
        e.bot.handle_update(gmsg("@cctg_bot расскажи", chat=-100999))
        self.assertEqual(len(e.tg.of("sendMessage")), 1)  # nothing else in an unknown group
        self.assertEqual(fake.calls("session.start"), [])

    def test_mention_needs_the_whole_bot_name(self):
        e, fake = responder_env(self)
        e.bot.handle_update(gmsg("@cctg_bot_helper вопрос"))
        e.bot.handle_update(gmsg("вопрос", mid=11, reply_to_message={"message_id": 5, "from": {"username": "other_bot"}}))
        self.assertEqual(len(fake.calls("session.start")), 0)
        e.bot.handle_update(gmsg("@CCTG_BOT, вопрос", mid=12))
        self.assertEqual(len(fake.calls("session.start")), 1)

    def test_lost_session_is_retried_once_with_a_fresh_one(self):
        e, fake = responder_env(self)
        e.bot.store.data["responder"] = {"sid": "s-gone0000", "n": 1}
        fake.errors["session.send"] = "not_found"
        e.bot.handle_update(gmsg("@cctg_bot q1"))
        self.assertEqual(len(fake.calls("session.start")), 1)
        self.assertEqual(self.to_group(e), [])


class ChatBotTests(unittest.TestCase):
    """A separate bot for the shared chat: groups go through it, the owner's 🔐 stays with the console bot."""

    def env(self):
        e, fake = responder_env(self)
        e.bot.tg_chat = FakeTG()
        e.bot.chat_username = "sf_chat_bot"
        os.makedirs(os.path.join(e.tmp.name, ".git"))  # the responder folder is a clone
        return e, fake

    def test_group_goes_through_the_chat_bot_only(self):
        e, fake = self.env()
        e.bot.handle_update(gmsg("@sf_chat_bot вопрос"))  # the console bot ignores groups now
        self.assertEqual(fake.calls("session.start"), [])
        e.bot.chat_update(gmsg("@sf_chat_bot вопрос"))
        self.assertEqual(len(fake.calls("session.start")), 1)
        sid = e.bot.store.data["responder"]["sid"]
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": sid, "request": perm_req(inp={"command": "touch x"})}})
        self.assertIn("подтверждения владельца", e.bot.tg_chat.of("sendMessage")[-1]["text"])
        self.assertTrue(any("🔐" in m["text"] and m["chat_id"] == 1 for m in e.tg.of("sendMessage")))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": sid, "subtype": "success", "cost_usd": 0.01,
                                                              "is_error": False, "result": "Ответ"}})
        self.assertEqual(e.bot.tg_chat.of("sendMessage")[-1]["text"], "Ответ")
        self.assertFalse(any(m["chat_id"] == GROUP for m in e.tg.of("sendMessage")))

    def test_id_command_via_the_chat_bot(self):
        e, fake = self.env()
        e.bot.cfg.responder_chat = None
        e.bot.chat_update(gmsg("@sf_chat_bot id", chat=-100999))
        self.assertEqual(e.bot.tg_chat.of("sendMessage"), [])
        self.assertIn("-100999", e.tg.of("sendMessage")[0]["text"])  # the owner learns it from the console bot

    def test_added_to_a_group_reports_its_id(self):
        e, fake = self.env()
        e.bot.cfg.responder_chat = None
        e.bot.chat_update(gmsg(None, chat=-100777, new_chat_members=[{"id": 5, "is_bot": True, "username": "sf_chat_bot"}]))
        self.assertEqual(e.bot.tg_chat.of("sendMessage"), [])
        self.assertIn("-100777", e.tg.of("sendMessage")[0]["text"])
        e.bot.chat_update(gmsg(None, chat=-100778, new_chat_members=[{"id": 6, "username": "someone"}]))
        self.assertEqual(len(e.tg.of("sendMessage")), 1)  # only when the bot itself joins

    def test_question_gets_eyes_and_typing_while_it_is_answered(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot вопрос", mid=55))
        r = e.bot.tg_chat.of("setMessageReaction")
        self.assertEqual((r[0]["chat_id"], r[0]["message_id"], r[0]["reaction"][0]["emoji"]), (GROUP, 55, "👀"))
        e.bot.responder_typing_tick()
        self.assertEqual(e.bot.tg_chat.of("sendChatAction")[-1], {"chat_id": GROUP, "action": "typing"})
        sid = e.bot.store.data["responder"]["sid"]
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": sid, "subtype": "success", "cost_usd": 0.01,
                                                              "is_error": False, "result": "Ответ"}})
        n = len(e.bot.tg_chat.of("sendChatAction"))
        e.bot.responder_typing_tick()
        self.assertEqual(len(e.bot.tg_chat.of("sendChatAction")), n)  # answered: no more typing

    def test_reply_to_someone_else_brings_that_message_along(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot что скажешь?", reply_to_message={
            "message_id": 7, "from": {"first_name": "Анна", "username": "den"}, "text": "Прогноз на Махачева 70%"}))
        p = fake.calls("session.start")[0]["prompt"]
        self.assertIn("Прогноз на Махачева 70%", p)
        self.assertIn("что скажешь?", p)

    def test_a_failed_getme_at_start_is_retried_on_the_next_mention(self):
        e, fake = self.env()
        e.bot.chat_username = None  # 03.11: getMe failed after a reboot (no network yet) and the group went deaf
        with mock.patch.object(e.bot.tg_chat, "get_me", create=True, return_value={"username": "sf_chat_bot"}):
            e.bot.chat_update(gmsg("@sf_chat_bot живой?"))
        self.assertEqual(e.bot.chat_username, "sf_chat_bot")
        self.assertEqual(len(fake.calls("session.start")), 1)

    def test_bare_mention_is_still_a_question(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot", reply_to_message={
            "message_id": 7, "from": {"first_name": "Анна"}, "text": "такой рейтинг добавить"}))
        p = fake.calls("session.start")[0]["prompt"]
        self.assertIn("такой рейтинг добавить", p)
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "ok"}})
        e.bot.chat_update(gmsg("@sf_chat_bot", mid=12))
        self.assertEqual(len(fake.calls("session.send")), 0)  # no visible context: no model turn, an honest reply
        m = e.bot.tg_chat.of("sendMessage")[-1]
        self.assertEqual(m.get("reply_to_message_id") or (m.get("reply_parameters") or {}).get("message_id"), 12)
        self.assertIn("Перешлите", m["text"])

    def test_bare_mention_on_a_reply_from_another_chat_or_a_quote(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot", quote={"text": "такой рейтинг добавить"}, external_reply={
            "origin": {"type": "user", "sender_user": {"first_name": "Анна"}}}))
        p = fake.calls("session.start")[0]["prompt"]
        self.assertIn("«такой рейтинг добавить»", p)
        self.assertIn("Анна", p)
        self.assertNotIn("Hi", p)
        self.assertEqual(cc.Bot.reply_context(e.bot, {"reply_to_message": {"message_id": 7, "from": {"first_name": "Анна"},
                                                                          "photo": [{}]}})[0], "Анна")  # a photo alone is context too

    def test_report_word_sums_up_the_day_with_a_page_link(self):
        e, fake = self.env()
        log = ("\x1ea1|1792400000|Anna|feat(v2): плашка замены\nsrc/v2/Row.jsx\njournal/x.md\n\n"
               "\x1ea2|1792400100|Anna|fix(odds): гейт рынка\nscripts/odds/gate.mjs\ntests/unit/g.test.js\n").replace("|", "\x1f")
        e.bot.run = lambda args, timeout=30, cwd=None, input_text=None: (0, log if "log" in args else "")
        e.bot.cfg.webapp_static = e.tmp.name
        with open(os.path.join(e.tmp.name, "report.html"), "w") as f:
            f.write("<!doctype html>")
        e.bot.cfg.webapp_url_env = "https://cc.example"
        e.bot.chat_update(gmsg("Отчёт", mid=40))  # no mention needed
        p = fake.calls("session.start")[0]
        self.assertIn("сайт: feat(v2): плашка замены", p["prompt"])
        self.assertIn("прогнозы и данные: fix(odds): гейт рынка", p["prompt"])
        self.assertEqual(p["model"], "")  # commit words do not send a summary to opus
        self.assertIs(p["write"], False)
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "На сайте плашка замены."}})
        msg = e.bot.tg_chat.of("sendMessage")[-1]["text"]
        token = re.search(r"https://cc\.example/r/([A-Za-z0-9_-]+)", msg).group(1)
        data = json.loads(e.bot.report_json(token))
        self.assertEqual(data["counts"], {"site": 1, "data": 1, "server": 0, "other": 0})
        self.assertIsNone(e.bot.report_json("../../etc/passwd"))
        self.assertEqual([c["area"] for c in cc.parse_report_log(log)], ["site", "data"])

    def test_every_question_carries_the_recent_chat_and_the_bot_answer_it_replies_to(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("пресс-конференции есть?", mid=60))  # not to the bot: only remembered
        e.bot.chat_update(gmsg("@sf_chat_bot что есть по весогонке?", mid=61))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "Готового пайплайна нет."}})
        e.bot.cfg.allowed = {777}  # the owner replies to the bot's answer
        e.bot.chat_update(gmsg("я это хотел настроить", mid=63, reply_to_message={
            "message_id": 62, "from": {"username": "sf_chat_bot"}, "text": "Готового пайплайна нет."}))
        p = fake.calls("session.start")[-1]["prompt"] if len(fake.calls("session.start")) > 1 else fake.calls("session.send")[-1]["text"]
        self.assertIn("Анна: пресс-конференции есть?", p)
        self.assertIn("sf_chat_bot: Готового пайплайна нет.", p)  # CC_RESPONDER_NAME unset: the bot's @name
        self.assertIn("It replies to your earlier answer: «Готового пайплайна нет.»", p)
        self.assertNotIn("Анна: я это хотел настроить", p)  # the question itself is not repeated as history
        self.assertIs(fake.calls("session.start")[-1].get("write"), True)  # a reply to the bot is still the owner's own

    def test_no_git_folder_is_not_pulled_and_the_report_word_is_a_plain_message(self):
        e, fake = self.env()
        os.rmdir(os.path.join(e.tmp.name, ".git"))  # a plain folder of documents
        calls = []
        e.bot.run = lambda args, timeout=30, cwd=None, input_text=None: (calls.append(args) or (0, ""))
        e.bot.chat_update(gmsg("@sf_chat_bot отчёт", mid=40))
        self.assertEqual(calls, [])
        p = fake.calls("session.start")[0]["prompt"]
        self.assertNotIn("Сводка за последние сутки", p)  # not the report: the word goes to the model as a question
        self.assertIn("отчёт", p)

    def test_responder_name_is_used_for_the_bots_own_answers(self):
        e, fake = self.env()
        e.bot.cfg.responder_name = "Helper"
        e.bot.chat_update(gmsg("@sf_chat_bot вопрос", mid=61))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "Ответ"}})
        e.bot.chat_update(gmsg("@sf_chat_bot ещё", mid=62))
        p = (fake.calls("session.send") or fake.calls("session.start"))[-1]
        p = p.get("text") or p["prompt"]
        self.assertIn("Helper: Ответ", p)
        self.assertIn("«Helper»", p)

    def test_402_result_reaches_the_owner_and_never_the_group(self):
        e, fake = self.env()
        e.bot.cfg.allowed = {42}
        e.bot.chat_update(gmsg("@sf_chat_bot вопрос"))
        e.bot.sdk_on_event({"event": "turn.result", "data": {
            "id": "s-aabbccdd", "subtype": "success", "is_error": True, "cost_usd": 0.0,
            "result": "API Error: 402 Insufficient Balance"}})
        sent = e.tg.of("sendMessage") + e.bot.tg_chat.of("sendMessage")
        self.assertFalse([m for m in sent if m["chat_id"] == GROUP and "402" in m["text"]])
        self.assertTrue([m for m in sent if m["chat_id"] == 42 and "402 Insufficient Balance" in m["text"]])
        self.assertIn("Передам владельцу", e.bot.tg_chat.of("sendMessage")[-1]["text"])  # the group keeps the neutral text

    def test_codex_fallback_without_codex_or_bwrap_fails_quietly(self):
        e, fake = self.env()
        e.bot._spawn = lambda fn, *a: fn(*a)
        with mock.patch("shutil.which", return_value=None):
            e.bot.chat_update(gmsg("@sf_chat_bot вопрос"))
            e.bot.sdk_on_event({"event": "session.state", "data": {"session": fake.session(
                id="s-aabbccdd", state="error", chat=True, detail="rate_limit")}})
        self.assertIn("Передам владельцу", e.bot.tg_chat.of("sendMessage")[-1]["text"])

    def test_one_token_mode_learns_its_own_name_at_startup(self):
        e, fake = responder_env(self)
        self.assertIs(e.bot.tg_chat, e.bot.tg)  # no CC_RESPONDER_TOKEN: one bot for everything
        e.bot.chat_username = None  # as after a start: only getMe knows it
        e.bot.tg.get_me = lambda: {"username": "one_bot", "has_topics_enabled": False}
        e.bot.handle_update(gmsg("@one_bot вопрос"))
        self.assertEqual(len(fake.calls("session.start")), 1)  # the name is asked again on the mention itself
        e.bot.detect_topics()  # what run_forever calls
        self.assertEqual(e.bot.chat_username, "one_bot")
        e.bot.handle_update(gmsg("@one_bot вопрос", mid=11))
        self.assertEqual(len(fake.calls("session.start")), 1)
        e.bot.cfg.responder_chat = None
        e.bot.cfg.allowed = {42}
        e.bot.handle_update(gmsg("@one_bot id", chat=-100999, mid=12))
        self.assertTrue([m for m in e.tg.of("sendMessage") if m["chat_id"] == 42 and "-100999" in m["text"]])

    def test_clone_is_pulled_before_a_question_at_most_every_10_min(self):
        e, fake = self.env()
        calls = []
        e.bot.run = lambda args, timeout=30, cwd=None, input_text=None: (calls.append(args) or (0, ""))
        e.bot.chat_update(gmsg("@sf_chat_bot q1"))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "ok"}})
        e.bot.chat_update(gmsg("@sf_chat_bot q2", mid=12))
        pulls = [a for a in calls if a[:1] == ["git"]]
        self.assertEqual(len(pulls), 1)
        self.assertEqual(pulls[0][-2:], ["--ff-only", "--quiet"])

    def test_relay_answers_are_logged_and_not_answered(self):
        e, fake = self.env()
        d = os.path.dirname(e.bot.cfg.responder_log)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "relay.json"), "w") as f:
            json.dump({"mid": 500, "until": e.bot.clock() + 60}, f)
        e.bot.chat_update(gmsg("1 да, 2 убрать", mid=11, reply_to_message={"message_id": 500, "from": {"username": "sf_chat_bot"}}))
        self.assertEqual(fake.calls("session.start"), [])  # the answer to the relay is not a question to the bot
        e.bot.chat_update(gmsg("@sf_chat_bot кто фаворит?", mid=12))
        self.assertEqual(len(fake.calls("session.start")), 1)  # an ordinary question still goes on
        with open(os.path.join(d, "relay.jsonl")) as f:
            rows = [json.loads(x) for x in f]
        self.assertEqual([(r["text"], r["reply"]) for r in rows], [("1 да, 2 убрать", True), ("@sf_chat_bot кто фаворит?", False)])
        # 25.10: a question in a reply to the relay (a mention or «?») still reaches the responder — Анна lost 4 of them
        for i, q in enumerate(["разбор сделал? почему так много мимо?", "@sf_chat_bot посмотри протоколы"]):
            e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                                  "is_error": False, "result": "ok"}})
            e.bot.chat_update(gmsg(q, mid=20 + i, reply_to_message={"message_id": 500, "from": {"username": "sf_chat_bot"}}))
            self.assertEqual(len(fake.calls("session.start")) + len(fake.calls("session.send")), 2 + i, q)

    def test_change_requests_go_to_opus_questions_to_the_default(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot Добавь рейтинг в карточку"))
        self.assertEqual(fake.calls("session.start")[0]["model"], "opus")
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "ok"}})
        e.bot.chat_update(gmsg("@sf_chat_bot кто фаворит?", mid=12))
        self.assertEqual(fake.calls("session.send")[-1]["model"], "")
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "ok"}})
        e.bot.chat_update(gmsg("@sf_chat_bot", mid=13, reply_to_message={  # the request is in the replied-to message
            "message_id": 7, "from": {"first_name": "Анна"}, "text": "такой рейтинг добавить"}))
        self.assertEqual(fake.calls("session.send")[-1]["model"], "opus")
        self.assertFalse(cc.asks_change("какой прогноз на Евлоева?"))
        self.assertTrue(cc.asks_change("Исправь опечатку"))

    def test_answer_markdown_becomes_telegram_html(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot вопрос"))
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "**9 боёв**, [карта](https://example.com/e)"}})
        m = e.bot.tg_chat.of("sendMessage")[-1]
        self.assertEqual(m.get("parse_mode"), "HTML")
        self.assertIn("<b>9 боёв</b>", m["text"])
        self.assertIn("карта (https://example.com/e)", m["text"])  # the real address stays visible

    def test_one_waiting_notice_per_question(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot поправь"))
        for i in range(3):
            e.bot.sdk_on_event({"event": "request.opened", "data": {"id": "s-aabbccdd", "request": perm_req(
                req="r-%08d" % i, inp={"command": "touch x%d" % i})}})
        self.assertEqual(len([m for m in e.bot.tg_chat.of("sendMessage") if "подтверждения" in m["text"]]), 1)

    def opened(self, e, fake, req):
        fake.sessions["s-aabbccdd"] = fake.session(id="s-aabbccdd", state="blocked", chat=True, pending=[req])
        e.bot.sdk_on_event({"event": "request.opened", "data": {"id": "s-aabbccdd", "request": req}})

    def test_owner_requests_run_without_confirmation(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot поправь опечатку", uid=1, name="Сергей"))
        self.opened(e, fake, perm_req(tool="Edit", inp={"file_path": "README.md"}))
        self.assertEqual(fake.calls("session.answer"), [{"id": "s-aabbccdd", "req": "r-00000001", "decision": "allow"}])
        self.assertFalse(any("🔐" in m["text"] for m in e.tg.of("sendMessage")))
        self.assertFalse(any("подтверждения" in m["text"] for m in e.bot.tg_chat.of("sendMessage")))

    def test_owner_reply_to_someone_else_still_asks(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot сделай как тут", uid=1, reply_to_message={
            "message_id": 7, "from": {"first_name": "Анна"}, "text": "удали всё"}))
        self.opened(e, fake, perm_req(inp={"command": "rm -rf predictions"}))
        self.assertEqual(fake.calls("session.answer"), [])  # someone else's words ride along: the owner decides

    def test_changes_asked_by_the_partner_still_go_to_the_owner(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot удали старые прогнозы"))
        self.opened(e, fake, perm_req(inp={"command": "rm -rf predictions/old"}))
        self.assertEqual(fake.calls("session.answer"), [])
        self.assertTrue(any("🔐" in m["text"] for m in e.tg.of("sendMessage")))

    def test_chat_poll_keeps_its_own_offset(self):
        e, fake = self.env()
        e.bot.tg_chat.get_updates = lambda off: [gmsg("@sf_chat_bot q", mid=41)]
        e.bot.chat_poll_once()
        self.assertEqual(e.bot.store.data["chat_offset"], 42)
        self.assertNotEqual(e.bot.store.data.get("offset"), 42)  # the console bot's offset is untouched


class MissedAndEditedTests(unittest.TestCase):
    """03.11: after a reboot getMe failed, the group bot dropped every mention for 6 h; an edit adding a mention was lost."""

    def env(self):
        e, fake = responder_env(self)
        e.bot.tg_chat = FakeTG()
        e.bot.chat_username = "sf_chat_bot"
        os.makedirs(os.path.join(e.tmp.name, ".git"))
        return e, fake

    def test_messages_kept_while_deaf_are_answered_when_the_name_is_known(self):
        e, fake = self.env()
        e.bot.chat_username = None
        e.bot._me_tried = e.bot.clock()  # getMe just failed
        e.bot.chat_update(gmsg("@sf_chat_bot живой?", mid=324))
        self.assertEqual(fake.calls("session.start"), [])
        self.assertEqual([m["message_id"] for m in e.bot.store.data["resp_missed"]], [324])
        e.bot.chat_username = "sf_chat_bot"
        e.bot.responder_replay()
        self.assertEqual(len(fake.calls("session.start")), 1)
        self.assertIn("живой?", fake.calls("session.start")[0]["prompt"])
        self.assertNotIn("resp_missed", e.bot.store.data)

    def test_an_edit_that_adds_a_mention_is_answered_once(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("переделай прогнозы", mid=323))
        self.assertEqual(fake.calls("session.start"), [])
        edit = {"update_id": 99, "edited_message": gmsg("@sf_chat_bot переделай прогнозы", mid=323)["message"]}
        e.bot.chat_update(edit)
        self.assertEqual(len(fake.calls("session.start")), 1)
        e.bot.chat_update(dict(edit, update_id=100))  # a second edit of the same message: not a second question
        e.bot.sdk_on_event({"event": "turn.result", "data": {"id": "s-aabbccdd", "subtype": "success", "cost_usd": 0,
                                                              "is_error": False, "result": "ok"}})
        self.assertEqual(len(fake.calls("session.send")), 0)

    def test_an_edit_of_an_answered_message_is_not_asked_again(self):
        e, fake = self.env()
        e.bot.chat_update(gmsg("@sf_chat_bot вопрос", mid=40))
        e.bot.chat_update({"update_id": 5, "edited_message": gmsg("@sf_chat_bot вопрос, уточнил", mid=40)["message"]})
        self.assertEqual(len(fake.calls("session.start")), 1)
        self.assertEqual(len(e.bot.store.data["chat_tail"]), 1)  # the edit does not add a second line to the tail
