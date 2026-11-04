import asyncio
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import time
import unittest
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stt_runner  # noqa: E402

GROQ_KEY = "gsk_TESTMARKER-77aa"
OPENAI_KEY = "sk-TESTMARKER-55bb"
SPEECH = "SECRETSPEECH-marker-31337"


class Seg:
    def __init__(self, text):
        self.text = text


class FakeInfo:
    language = "ru"


class FakeModel:
    def __init__(self, text=SPEECH):
        self.text = text
        self.calls = []

    def transcribe(self, audio, language=None, beam_size=None, vad_filter=None):
        self.calls.append((audio, language, beam_size, vad_filter))
        return iter([Seg(" " + self.text), Seg("")]), FakeInfo()


class Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = self.tmp.name
        self.sock = os.path.join(t, "run", "stt.sock")
        self.audio = os.path.join(t, "voice.ogg")
        with open(self.audio, "wb") as f:
            f.write(b"not really audio")
        self.scratch = os.path.join(t, "scratch")      # tempfile directory: must stay empty
        os.mkdir(self.scratch)
        self._tmpdir = tempfile.tempdir
        tempfile.tempdir = self.scratch
        self.addCleanup(setattr, tempfile, "tempdir", self._tmpdir)
        self.gkey = os.path.join(t, "groq.env")
        with open(self.gkey, "w") as f:
            f.write("GROQ_API_KEY=%s\n" % GROQ_KEY)
        self.okey = os.path.join(t, "openai.key")
        with open(self.okey, "w") as f:
            f.write(OPENAI_KEY + "\n")
        self._env = dict(os.environ)
        for k in list(os.environ):
            if k.startswith("CC_STT_"):
                del os.environ[k]
        os.environ["CC_STT_GROQ_KEY_FILE"] = self.gkey
        os.environ["CC_STT_OPENAI_KEY_FILE"] = self.okey
        self.addCleanup(self._restore_env)
        # fakes for everything outside the process
        self.model = FakeModel()
        self.loads = []
        self.posts = []
        self.duration = 8.0
        self.cloud_text = "cloud text"
        self.cloud_exc = None
        self._patched = {}
        self.patch("local_available", lambda: True)
        self.patch("load_model", self._load_model)
        self.patch("probe_duration", lambda p: self.duration if os.path.exists(p) and self.duration is not None else None)
        self.patch("decode_audio", lambda p, m: [0.0, 0.1])
        self.patch("encode_opus", self._encode)
        self.patch("http_post", self._post)
        self.clock = [1000.0]
        self.patch("now", lambda: self.clock[0])
        self.stderr = io.StringIO()
        self._redir = contextlib.redirect_stderr(self.stderr)
        self._redir.__enter__()
        self.addCleanup(self._redir.__exit__, None, None, None)

    def patch(self, name, fn):
        self._patched.setdefault(name, getattr(stt_runner, name))
        setattr(stt_runner, name, fn)
        self.addCleanup(setattr, stt_runner, name, self._patched[name])

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)

    def _load_model(self, name, threads):
        self.loads.append((name, threads))
        return self.model

    def _encode(self, src, dst, max_seconds):
        with open(dst, "wb") as f:
            f.write(b"OggS-fake")

    def _post(self, url, headers, body, timeout):
        self.posts.append((url, headers, body))
        if self.cloud_exc:
            raise self.cloud_exc
        return json.dumps({"text": self.cloud_text}).encode()

    async def start(self, **env):
        os.environ.update(env)
        self.runner = stt_runner.Runner(self.sock)
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

    async def err(self, code, method="stt.transcribe", **params):
        res = await self.call(method, **params)
        self.assertEqual(res.get("error", {}).get("code"), code, res)
        return res["error"]


class LocalTests(Base):
    async def test_info_and_socket_modes(self):
        await self.start(CC_STT_MODEL="base")
        info = await self.ok("stt.info")
        self.assertEqual(info, {"version": "1", "engines": [{"name": "local", "ready": True, "model": "base"}],
                                "loaded": False})
        self.assertEqual(stat.S_IMODE(os.stat(self.sock).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.dirname(self.sock)).st_mode), 0o700)

    async def test_transcribe_local(self):
        await self.start(CC_STT_MODEL="small", CC_STT_THREADS="3")
        res = await self.ok("stt.transcribe", path=self.audio, lang="ru")
        self.assertEqual(res["text"], SPEECH)
        self.assertEqual((res["lang"], res["engine"], res["seconds"]), ("ru", "local", 8.0))
        self.assertIn("took", res)
        self.assertEqual(self.loads, [("small", 3)])
        self.assertEqual(self.model.calls[0][1:], ("ru", 5, True))
        self.assertTrue(os.path.exists(self.audio), "the runner must not delete the input")

    async def test_no_hint_means_auto(self):
        await self.start()
        res = await self.ok("stt.transcribe", path=self.audio)
        self.assertIsNone(self.model.calls[0][1])
        self.assertEqual(res["lang"], "ru")      # taken from the model's detection

    async def test_lazy_load_and_idle_unload(self):
        await self.start(CC_STT_IDLE="10")
        self.assertEqual(self.loads, [])
        self.assertFalse((await self.ok("stt.info"))["loaded"])
        await self.ok("stt.transcribe", path=self.audio)
        await self.ok("stt.transcribe", path=self.audio)
        self.assertEqual(len(self.loads), 1)
        self.assertTrue((await self.ok("stt.info"))["loaded"])
        self.clock[0] += 9 * 60
        self.assertFalse(self.runner.reap())
        self.assertTrue((await self.ok("stt.info"))["loaded"])
        self.clock[0] += 2 * 60
        self.assertTrue(self.runner.reap())
        self.assertFalse((await self.ok("stt.info"))["loaded"])
        await self.ok("stt.transcribe", path=self.audio)
        self.assertEqual(len(self.loads), 2)

    async def test_auto_model(self):
        self.patch("ram_gb", lambda: 16.0)
        old = os.cpu_count
        os.cpu_count = lambda: 16
        self.addCleanup(setattr, os, "cpu_count", old)
        await self.start(CC_STT_MODEL="auto")
        self.assertEqual(self.runner.cfg.model, "large-v3-turbo")
        self.patch("ram_gb", lambda: 4.0)
        self.assertEqual(stt_runner.resolve_model("auto"), "small")


class ChainTests(Base):
    async def test_local_fails_then_groq(self):
        def boom(name, threads):
            raise RuntimeError("path /secret/file and %s" % SPEECH)
        self.patch("load_model", boom)
        await self.start(CC_STT_ENGINES="local,groq")
        res = await self.ok("stt.transcribe", path=self.audio, lang="en")
        self.assertEqual((res["engine"], res["text"]), ("groq", "cloud text"))
        url, headers, body = self.posts[0]
        self.assertEqual(url, "https://api.groq.com/openai/v1/audio/transcriptions")
        self.assertEqual(headers["Authorization"], "Bearer " + GROQ_KEY)
        self.assertIn(b"whisper-large-v3-turbo", body)
        self.assertIn(b"OggS-fake", body)
        self.assertIn(b'name="language"', body)
        out = self.stderr.getvalue()
        self.assertIn("engine local failed: RuntimeError", out)
        self.assertNotIn("/secret/file", out)
        self.assertNotIn(SPEECH, out)

    async def test_local_timeout_then_openai(self):
        def slow(name, threads):
            time.sleep(1.5)
            return self.model
        self.patch("load_model", slow)
        await self.start(CC_STT_ENGINES="local,openai", CC_STT_TIMEOUT="1")
        t0 = time.monotonic()
        res = await self.ok("stt.transcribe", path=self.audio)
        self.assertLess(time.monotonic() - t0, 1.4)
        self.assertEqual(res["engine"], "openai")
        self.assertEqual(self.posts[0][0], "https://api.openai.com/v1/audio/transcriptions")
        self.assertIn(b"gpt-4o-mini-transcribe", self.posts[0][2])
        self.assertIn("engine local failed: timeout", self.stderr.getvalue())

    async def test_all_fail_is_unavailable(self):
        self.cloud_exc = urllib.error.HTTPError("https://x", 401, "Unauthorized " + GROQ_KEY, {}, None)
        self.patch("load_model", lambda n, t: (_ for _ in ()).throw(RuntimeError("x")))
        await self.start(CC_STT_ENGINES="local,groq")
        e = await self.err("unavailable", path=self.audio)
        self.assertIn("local:RuntimeError", e["message"])
        self.assertIn("groq:http_401", e["message"])
        self.assertNotIn(GROQ_KEY, json.dumps(e))
        self.assertNotIn(GROQ_KEY, self.stderr.getvalue())

    async def test_missing_key_skips_engine(self):
        os.unlink(self.gkey)
        await self.start(CC_STT_ENGINES="groq")
        info = await self.ok("stt.info")
        self.assertEqual(info["engines"], [{"name": "groq", "ready": False}])
        e = await self.err("unavailable", path=self.audio)
        self.assertIn("groq:no_key", e["message"])
        self.assertEqual(self.posts, [])

    async def test_key_read_at_call_time(self):
        os.unlink(self.gkey)
        await self.start(CC_STT_ENGINES="groq")
        with open(self.gkey, "w") as f:
            f.write(GROQ_KEY)             # a bare value without NAME=
        res = await self.ok("stt.transcribe", path=self.audio)
        self.assertEqual(res["engine"], "groq")
        self.assertEqual(self.posts[0][1]["Authorization"], "Bearer " + GROQ_KEY)


class RequestTests(Base):
    async def test_bad_requests(self):
        await self.start()
        await self.err("bad_request", path=os.path.join(self.tmp.name, "nope.ogg"))
        await self.err("bad_request")
        await self.err("bad_request", path=self.audio, lang="Rus!")
        self.duration = None
        e = await self.err("bad_request", path=self.audio)
        self.assertIn("not an audio", e["message"])
        await self.err("bad_request", method="stt.nothing")
        self.assertEqual(self.loads, [])

    async def test_too_long(self):
        await self.start(CC_STT_MAX_SECONDS="60")
        self.duration = 61.0
        await self.err("bad_request", path=self.audio)
        self.duration = 59.0
        await self.ok("stt.transcribe", path=self.audio)

    async def test_busy(self):
        orig = stt_runner.LocalEngine.run

        def slow(eng, job):
            time.sleep(0.5)
            return orig(eng, job)
        self.patch("probe_duration", lambda p: 5.0)
        stt_runner.LocalEngine.run = slow
        self.addCleanup(setattr, stt_runner.LocalEngine, "run", orig)
        await self.start()
        calls = [asyncio.create_task(self.call("stt.transcribe", path=self.audio))
                 for _ in range(stt_runner.MAX_QUEUE)]
        await asyncio.sleep(0.2)
        await self.err("busy", path=self.audio)
        results = await asyncio.gather(*calls)
        self.assertTrue(all("result" in r for r in results), results)

    async def test_invalid_json(self):
        await self.start()
        r, w = await asyncio.open_unix_connection(self.sock)
        w.write(b"{oops\n")
        await w.drain()
        self.assertEqual(json.loads(await r.readline())["error"]["code"], "bad_request")
        w.close()


class PrivacyTests(Base):
    async def test_no_secrets_or_text_in_log_and_no_key_in_reply(self):
        await self.start(CC_STT_ENGINES="local,groq,openai")
        res = await self.call("stt.transcribe", path=self.audio, lang="ru")
        info = await self.call("stt.info")
        blob = json.dumps(res) + json.dumps(info)
        for secret in (GROQ_KEY, OPENAI_KEY):
            self.assertNotIn(secret, blob)
            self.assertNotIn(secret, self.stderr.getvalue())
        out = self.stderr.getvalue()
        self.assertIn("transcribe ok engine=local", out)
        self.assertNotIn(SPEECH, out)
        self.assertNotIn(self.audio, out)
        self.assertNotIn(os.path.basename(self.audio), out)

    async def test_cloud_text_not_in_log(self):
        self.cloud_text = "CLOUDSPEECH-marker-9"
        self.patch("load_model", lambda n, t: (_ for _ in ()).throw(RuntimeError("x")))
        await self.start(CC_STT_ENGINES="local,groq")
        res = await self.ok("stt.transcribe", path=self.audio)
        self.assertEqual(res["text"], "CLOUDSPEECH-marker-9")
        self.assertNotIn("CLOUDSPEECH", self.stderr.getvalue())

    async def test_temp_files_removed(self):
        seen = []
        orig = self._encode

        def spy(src, dst, m):
            seen.append(dst)
            orig(src, dst, m)
        self.patch("encode_opus", spy)
        await self.start(CC_STT_ENGINES="groq,openai")
        await self.ok("stt.transcribe", path=self.audio)
        self.assertEqual(len(seen), 1, "one re-encoding is shared by the engines")
        self.assertFalse(os.path.exists(seen[0]))
        self.assertEqual(os.listdir(self.scratch), [])
        # also when every engine fails
        self.cloud_exc = urllib.error.URLError("down")
        await self.err("unavailable", path=self.audio)
        self.assertEqual(os.listdir(self.scratch), [])
        self.assertTrue(os.path.exists(self.audio))


class HelperTests(unittest.TestCase):
    def test_read_key(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "k")
            for text, want in (("A=\"v1\"\n", "v1"), ("# c\nexport K = v2\n", "v2"), ("bare-value\n", "bare-value"),
                               ("\n", None)):
                with open(p, "w") as f:
                    f.write(text)
                self.assertEqual(stt_runner.read_key(p), want, text)
            self.assertIsNone(stt_runner.read_key(os.path.join(d, "missing")))
            self.assertIsNone(stt_runner.read_key(""))

    def test_multipart(self):
        ctype, body = stt_runner.multipart({"model": "m"}, "a.ogg", b"DATA")
        b = ctype.split("boundary=")[1].encode()
        self.assertTrue(body.startswith(b"--" + b))
        self.assertTrue(body.endswith(b"--" + b + b"--\r\n"))
        self.assertIn(b'name="model"\r\n\r\nm', body)
        self.assertIn(b"\r\n\r\nDATA\r\n", body)

    def test_config_defaults(self):
        old = dict(os.environ)
        for k in list(os.environ):
            if k.startswith("CC_STT_"):
                del os.environ[k]
        try:
            c = stt_runner.Config()
            self.assertEqual((c.engines, c.model, c.idle, c.timeout, c.max_seconds),
                             (["local"], "small", 10, 60, 300))
            self.assertTrue(c.socket.endswith("/.local/state/zidos-pager/stt.sock"))
            os.environ["CC_STT_ENGINES"] = "groq, bogus,groq,local"
            self.assertEqual(stt_runner.Config().engines, ["groq", "local"])
        finally:
            os.environ.clear()
            os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
