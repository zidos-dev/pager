#!/usr/bin/env python3
"""STT runner: speech recognition as a separate process for the bot (see docs/stt-protocol.md).

Newline-delimited JSON over a Unix socket, the same framing as sdk_runner. Engines are tried in the
order of CC_STT_ENGINES: the local faster-whisper model and, if configured, the Groq / OpenAI cloud APIs.
Cloud API keys are read from files at call time; keys, recognised text and file paths are never logged.

    python stt_runner.py serve [--socket PATH]
"""
import argparse
import asyncio
import gc
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

VERSION = "1"
MAX_LINE = 1 << 20
MAX_QUEUE = 4            # requests waiting or running; the next one gets `busy`
ENGINE_NAMES = ("local", "groq", "openai")
ERR_CODES = ("bad_request", "unavailable", "busy", "internal", "too_big")
CLOUD = {
    "groq": ("https://api.groq.com/openai/v1/audio/transcriptions", "whisper-large-v3-turbo"),
    "openai": ("https://api.openai.com/v1/audio/transcriptions", "gpt-4o-mini-transcribe"),
}
FFMPEG_TIMEOUT = 120
now = time.monotonic     # replaced in tests


def log(*parts):
    """Log to stderr. Only engine names, durations, times and error codes - never text, paths or keys."""
    try:
        print("stt_runner:", *parts, file=sys.stderr, flush=True)
    except Exception:
        pass


class RunnerError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


# ---- config -------------------------------------------------------------------------------------------

def _int_env(name, default, lo=1):
    try:
        return max(lo, int(os.environ.get(name, "")))
    except ValueError:
        return default


def ram_gb():
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1048576.0
    except (OSError, ValueError, IndexError):
        pass
    try:  # macOS has no /proc/meminfo
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True,
                                  timeout=5).stdout) / 1073741824.0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0.0


def resolve_model(name):
    name = (name or "small").strip() or "small"
    if name == "auto":
        return "large-v3-turbo" if (os.cpu_count() or 1) >= 8 and ram_gb() >= 8 else "small"
    return name


class Config:
    """Environment snapshot. Key files are not read here: they are read at each call."""

    def __init__(self, socket_path=None):
        self.socket = os.path.expanduser(socket_path or os.environ.get("CC_STT_SOCKET")
                                         or "~/.local/state/zidos-pager/stt.sock")
        engines = [e.strip().lower() for e in (os.environ.get("CC_STT_ENGINES") or "local").split(",")]
        self.engines = []
        for e in engines:
            if e in ENGINE_NAMES and e not in self.engines:
                self.engines.append(e)
            elif e:
                log("ignoring unknown engine name")
        self.engines = self.engines or ["local"]
        self.model = resolve_model(os.environ.get("CC_STT_MODEL"))
        self.threads = _int_env("CC_STT_THREADS", min(16, os.cpu_count() or 1))
        self.idle = _int_env("CC_STT_IDLE", 10, lo=0)          # minutes; 0 = unload right after use
        self.timeout = _int_env("CC_STT_TIMEOUT", 60)
        self.max_seconds = _int_env("CC_STT_MAX_SECONDS", 300)
        self.key_files = {"groq": os.environ.get("CC_STT_GROQ_KEY_FILE", ""),
                          "openai": os.environ.get("CC_STT_OPENAI_KEY_FILE", "")}


def read_key(path):
    """Value from a key file: `NAME=value` (quotes allowed) or just the value. None when missing."""
    if not path:
        return None
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].lstrip()
                m = re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=(.*)$", line)
                val = (m.group(1) if m else line).strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                    val = val[1:-1]
                return val or None
    except OSError:
        return None
    return None


# ---- external pieces (replaced in tests) ----------------------------------------------------------------

def local_available():
    return importlib.util.find_spec("faster_whisper") is not None


def load_model(name, threads):
    """Lazy import: the module (and the tests) work without faster-whisper installed."""
    from faster_whisper import WhisperModel
    return WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)


def probe_duration(path):
    """Audio duration in seconds via ffprobe, or None when the file is not decodable audio."""
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", path],
                           capture_output=True, timeout=30)
        return float(p.stdout.decode().strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def decode_audio(path, max_seconds):
    """Mono 16 kHz float32 numpy array. The system ffmpeg is used (PyAV in faster-whisper is not)."""
    import numpy as np
    p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-t", str(max_seconds + 1), "-i", path,
                        "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                       capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def encode_opus(src, dst, max_seconds):
    """Compact ogg/opus, mono 16 kHz, for the cloud upload."""
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-t", str(max_seconds + 1), "-i", src,
                    "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "24k", "-f", "ogg", dst],
                   capture_output=True, timeout=FFMPEG_TIMEOUT, check=True)


def http_post(url, headers, body, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(1 << 20)


def multipart(fields, filename, data):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, k, v)).encode())
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                  'Content-Type: audio/ogg\r\n\r\n' % (boundary, filename)).encode())
    parts.append(data)
    parts.append(("\r\n--%s--\r\n" % boundary).encode())
    return "multipart/form-data; boundary=" + boundary, b"".join(parts)


# ---- engines --------------------------------------------------------------------------------------------

class EngineError(Exception):
    """Carries only a short code, safe for the log."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class Job:
    """One request: the source file and, lazily, its compact re-encoding (deleted in cleanup)."""

    def __init__(self, path, lang, seconds, cfg):
        self.path, self.lang, self.seconds, self.cfg = path, lang, seconds, cfg
        self._opus = None

    def opus_path(self):
        if self._opus is None:
            fd, tmp = tempfile.mkstemp(prefix="stt-", suffix=".ogg")
            os.close(fd)
            self._opus = tmp
            encode_opus(self.path, tmp, self.cfg.max_seconds)
        return self._opus

    def cleanup(self):
        if self._opus:
            try:
                os.unlink(self._opus)
            except OSError:
                pass
            self._opus = None


class LocalEngine:
    name = "local"

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = None
        self.last_used = now()
        self._lock = threading.Lock()   # one model, one transcription at a time (also after a timeout)
        self._busy = 0

    @property
    def loaded(self):
        return self.model is not None

    def ready(self):
        return local_available()

    def run(self, job):
        with self._lock:
            self._busy += 1
            try:
                if self.model is None:
                    t = now()
                    self.model = load_model(self.cfg.model, self.cfg.threads)
                    log("model loaded in %.1fs" % (now() - t))
                audio = decode_audio(job.path, self.cfg.max_seconds)
                segments, info = self.model.transcribe(audio, language=job.lang, beam_size=5, vad_filter=True)
                text = " ".join(s.text.strip() for s in segments).strip()   # the generator runs here
                lang = job.lang or getattr(info, "language", None)
                return text, lang
            finally:
                self.last_used = now()
                self._busy -= 1

    def unload_if_idle(self):
        """Drop the model after the idle period. Returns True when it was unloaded."""
        if self.model is None or self._busy:
            return False
        if now() - self.last_used < self.cfg.idle * 60:
            return False
        if not self._lock.acquire(blocking=False):
            return False
        try:
            self.model = None
        finally:
            self._lock.release()
        gc.collect()
        log("model unloaded after idle")
        return True


class CloudEngine:
    def __init__(self, name, cfg):
        self.name, self.cfg = name, cfg
        self.url, self.model = CLOUD[name]

    def key(self):
        return read_key(self.cfg.key_files.get(self.name, ""))

    def ready(self):
        return bool(self.key())

    def run(self, job):
        key = self.key()
        if not key:
            raise EngineError("no_key")
        try:
            with open(job.opus_path(), "rb") as f:
                data = f.read()
        except (OSError, subprocess.SubprocessError):
            raise EngineError("encode")
        fields = {"model": self.model, "response_format": "json"}
        if job.lang:
            fields["language"] = job.lang
        ctype, body = multipart(fields, "audio.ogg", data)
        headers = {"Authorization": "Bearer " + key, "Content-Type": ctype,
                   "Accept": "application/json", "User-Agent": "zidos-pager-stt/" + VERSION}
        try:
            raw = http_post(self.url, headers, body, self.cfg.timeout)
        except urllib.error.HTTPError as e:
            raise EngineError("http_%d" % e.code)
        except (urllib.error.URLError, OSError):
            raise EngineError("network")
        try:
            text = json.loads(raw)["text"]
            if not isinstance(text, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise EngineError("bad_response")
        return text.strip(), job.lang


# ---- runner ---------------------------------------------------------------------------------------------

class Runner:
    def __init__(self, socket_path=None):
        self.cfg = Config(socket_path)
        self.local = LocalEngine(self.cfg)
        self.engines = {"local": self.local, "groq": CloudEngine("groq", self.cfg),
                        "openai": CloudEngine("openai", self.cfg)}
        self.lock = asyncio.Lock()
        self.pending = 0
        self.server = None
        self._reaper = None

    # -- methods
    def info(self):
        out = []
        for n in self.cfg.engines:
            e = {"name": n, "ready": bool(self.engines[n].ready())}
            if n == "local":
                e["model"] = self.cfg.model
            out.append(e)
        return {"version": VERSION, "engines": out, "loaded": self.local.loaded}

    async def transcribe(self, params):
        path, lang = params.get("path"), params.get("lang")
        if not isinstance(path, str) or not path or not os.path.isfile(path):
            raise RunnerError("bad_request", "no such file")
        if lang in (None, "", "auto"):
            lang = None
        elif not (isinstance(lang, str) and re.fullmatch(r"[a-z]{2,3}", lang)):
            raise RunnerError("bad_request", "lang must be a two or three letter code")
        if self.pending >= MAX_QUEUE:
            raise RunnerError("busy", "queue is full")
        self.pending += 1
        try:
            async with self.lock:
                return await self._transcribe(path, lang)
        finally:
            self.pending -= 1

    async def _transcribe(self, path, lang):
        seconds = await asyncio.to_thread(probe_duration, path)
        if seconds is None:
            raise RunnerError("bad_request", "not an audio file")
        if seconds > self.cfg.max_seconds:
            raise RunnerError("bad_request", "audio is longer than %d s" % self.cfg.max_seconds)
        job = Job(path, lang, seconds, self.cfg)
        fails = []
        t0 = now()
        try:
            for name in self.cfg.engines:
                eng = self.engines[name]
                t1 = now()
                try:
                    text, rlang = await asyncio.wait_for(asyncio.to_thread(eng.run, job), self.cfg.timeout)
                except asyncio.TimeoutError:
                    code = "timeout"
                except EngineError as e:
                    code = e.code
                except Exception as e:   # never log str(e): it may carry paths or text
                    code = type(e).__name__
                else:
                    took = now() - t0
                    log("transcribe ok engine=%s audio=%.1fs took=%.1fs" % (name, seconds, took))
                    return {"text": text, "lang": rlang, "seconds": round(seconds, 2),
                            "took": round(took, 2), "engine": name}
                log("engine %s failed: %s after %.1fs" % (name, code, now() - t1))
                fails.append("%s:%s" % (name, code))
        finally:
            await asyncio.to_thread(job.cleanup)
        raise RunnerError("unavailable", "no engine could transcribe (%s)" % ", ".join(fails))

    def reap(self):
        return self.local.unload_if_idle()

    async def _reap_loop(self):
        while True:
            await asyncio.sleep(max(1.0, min(30.0, self.cfg.idle * 60 / 4.0)))
            try:
                self.reap()
            except Exception as e:
                log("reaper error", type(e).__name__)

    async def dispatch(self, method, params):
        if not isinstance(params, dict):
            raise RunnerError("bad_request", "params must be an object")
        if method == "stt.info":
            return self.info()
        if method == "stt.transcribe":
            return await self.transcribe(params)
        raise RunnerError("bad_request", "unknown method")

    # -- server
    async def _handle(self, reader, writer):
        try:
            try:
                line = await reader.readline()
            except (ValueError, asyncio.LimitOverrunError):
                await self._send(writer, {"id": None, "error": {"code": "too_big", "message": "line too long"}})
                return
            if not line:
                return
            rid = None
            try:
                req = json.loads(line)
                if not isinstance(req, dict):
                    raise ValueError
                rid = req.get("id")
            except ValueError:
                await self._send(writer, {"id": None, "error": {"code": "bad_request", "message": "invalid JSON"}})
                return
            method = req.get("method")
            try:
                out = {"id": rid, "result": await self.dispatch(method, req.get("params") or {})}
            except RunnerError as e:
                out = {"id": rid, "error": {"code": e.code, "message": e.message}}
            except Exception as e:
                log("internal error", str(method), type(e).__name__)
                out = {"id": rid, "error": {"code": "internal", "message": type(e).__name__}}
            await self._send(writer, out)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    async def _send(writer, obj):
        writer.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        await writer.drain()

    async def start(self):
        d = os.path.dirname(self.cfg.socket) or "."
        os.makedirs(d, mode=0o700, exist_ok=True)
        try:
            os.unlink(self.cfg.socket)
        except FileNotFoundError:
            pass
        old = os.umask(0o177)
        try:
            self.server = await asyncio.start_unix_server(self._handle, path=self.cfg.socket, limit=MAX_LINE)
        finally:
            os.umask(old)
        os.chmod(self.cfg.socket, 0o600)
        self._reaper = asyncio.create_task(self._reap_loop())

    async def close(self):
        if self._reaper:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
        if self.server is not None:
            self.server.close()
        try:
            os.unlink(self.cfg.socket)
        except OSError:
            pass


async def serve(socket_path=None):
    runner = Runner(socket_path)
    await runner.start()
    c = runner.cfg
    log("listening; engines=%s model=%s threads=%d" % (",".join(c.engines), c.model, c.threads))
    if "local" in c.engines and c.model.startswith("large-v3-turbo") and \
            not os.environ.get("HF_HUB_OFFLINE"):
        log("note: the first local request downloads the model (about 1.6 GB) if it is not cached yet")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    await runner.close()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stt_runner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("serve")
    sp.add_argument("--socket")
    a = ap.parse_args(argv)
    asyncio.run(serve(a.socket))


if __name__ == "__main__":
    main()
