"""setup-macos.sh contract tests: launchctl, python and pip are local fakes; HOME is a temporary directory."""
import os
from pathlib import Path
import plistlib
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:TEST_SECRET_TOKEN"
KEY_VALUE = "sk-sdk-INSTALLER_SECRET"

# A python stand-in: version probe (-c) answers by VERSION, `-m venv DEST` builds a fake venv, the rest runs the real python.
PY = """#!/bin/sh
echo "$0 $*" >> "$SETUP_TEST_LOG"
if [ "$1" = "-c" ]; then [ "$FAKE_PY_OK" = 1 ]; exit $?; fi
if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
    mkdir -p "$3/bin"
    cp "$0" "$3/bin/python"
    printf '#!/bin/sh\\necho "pip $*" >> "$SETUP_TEST_LOG"\\n' > "$3/bin/pip"
    chmod +x "$3/bin/pip"
    exit 0
fi
exec "$REAL_PY" "$@"
"""


class MacSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "repo"
        (self.repo / "deploy").mkdir(parents=True)
        shutil.copy(ROOT / "deploy/setup-macos.sh", self.repo / "deploy/setup-macos.sh")
        (self.repo / "zidos_pager.py").write_text("")
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.log = self.tmp / "log"
        self.log.write_text("")
        launchctl = self.bin / "launchctl"
        launchctl.write_text('#!/bin/sh\necho "launchctl $*" >> "$SETUP_TEST_LOG"\n')
        launchctl.chmod(0o755)
        self.py312 = self.fake_python("py312")
        self.config = self.home / ".config/zidos-pager"
        self.agents = self.home / "Library/LaunchAgents"

    def fake_python(self, name):
        path = self.bin / name
        path.write_text(PY)
        path.chmod(0o755)
        return path

    def secret(self, name, text):
        path = self.tmp / name
        path.write_text(text)
        path.chmod(0o600)
        return str(path)

    def run_setup(self, *args, pythons=None, check=True):
        env = {
            "PATH": "%s:/usr/bin:/bin" % self.bin, "HOME": str(self.home), "SETUP_TEST_LOG": str(self.log),
            "REAL_PY": sys.executable,
            "CC_SETUP_PYTHONS": " ".join(map(str, pythons or [self.py312])),
        }
        env["FAKE_PY_OK"] = "1"  # the fake answers the 3.10 probe with yes
        proc = subprocess.run(["bash", str(self.repo / "deploy/setup-macos.sh"), *args], env=env,
                              capture_output=True, text=True, timeout=60)
        if check:
            self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        return proc

    def install(self, *extra, **kw):
        tok = self.secret("token", TOKEN + "\n")
        return self.run_setup("--non-interactive", "--token-file", tok, "--users", "42,43", *extra, **kw)

    def sdk_args(self):
        return ["--with-sdk", "--sdk-key-file", self.secret("key.env", "ANTHROPIC_API_KEY=%s\n" % KEY_VALUE),
                "--sdk-base-url", "https://api.example.com/anthropic", "--sdk-model", "m-1"]

    def plist(self, kind):
        path = self.agents / ("dev.zidos.pager.%s.plist" % kind)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        with open(path, "rb") as f:
            return plistlib.load(f)

    def test_plist_bot_and_sdk(self):
        self.install(*self.sdk_args())
        bot, sdk = self.plist("bot"), self.plist("sdk")
        for doc, kind in ((bot, "bot"), (sdk, "sdk")):
            self.assertIs(doc["KeepAlive"], True)
            self.assertIs(doc["RunAtLoad"], True)
            self.assertEqual(doc["ProcessType"], "Background")
            self.assertEqual(doc["StandardOutPath"], str(self.home / "Library/Logs/zidos-pager" / (kind + ".log")))
        self.assertEqual(bot["ProgramArguments"], [str(self.py312), "-u", str(self.repo / "zidos_pager.py")])
        self.assertEqual(bot["EnvironmentVariables"]["ZIDOS_PAGER_ENV"], str(self.config / "env"))
        self.assertEqual(sdk["ProgramArguments"][1:], ["-u", str(self.repo / "sdk_runner.py"), "serve"])
        self.assertTrue(sdk["ProgramArguments"][0].endswith(".venv-sdk/bin/python"))
        extra = {k for k in sdk["EnvironmentVariables"] if k not in ("HOME", "PATH")}
        self.assertTrue(extra and all(k.startswith("CC_SDK_") for k in extra), extra)
        self.assertEqual(sdk["EnvironmentVariables"]["CC_SDK_BASE_URL"], "https://api.example.com/anthropic")
        for path in self.agents.iterdir():
            text = path.read_text()
            self.assertNotIn(TOKEN, text)
            self.assertNotIn(KEY_VALUE, text)
            self.assertNotIn("TG_BOT_TOKEN", text)
        sdk_env = self.config / "sdk.env"
        self.assertEqual(stat.S_IMODE(sdk_env.stat().st_mode), 0o600)
        self.assertNotIn(KEY_VALUE, sdk_env.read_text())
        calls = self.log.read_text()
        self.assertIn("launchctl bootout gui/%d/dev.zidos.pager.bot" % os.getuid(), calls)
        self.assertIn("launchctl bootstrap gui/%d %s" % (os.getuid(), self.agents / "dev.zidos.pager.sdk.plist"), calls)

    def test_plist_without_sdk_has_only_bot(self):
        out = self.install().stdout
        self.assertEqual([p.name for p in self.agents.iterdir()], ["dev.zidos.pager.bot.plist"])
        self.assertIn("caffeinate", out)
        self.assertIn("launchctl bootout", out)

    def test_python_pick_rejects_old_and_prefers_first_good(self):
        old = self.bin / "py39"  # python 3.9: the version probe fails
        old.write_text("#!/bin/sh\nexit 1\n")
        old.chmod(0o755)
        proc = self.run_setup("--non-interactive", "--token-file", self.secret("t2", TOKEN), "--users", "1",
                              pythons=[old], check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("brew install python@3.12", proc.stderr)
        self.assertFalse(self.agents.exists())
        brew = self.fake_python("brew")  # stands for /opt/homebrew/bin/python3
        self.run_setup("--non-interactive", "--token-file", self.secret("t3", TOKEN), "--users", "1",
                       pythons=[old, brew, self.py312])
        self.assertEqual(self.plist("bot")["ProgramArguments"][0], str(brew))

    def test_update_reloads_and_upgrades(self):
        self.install(*self.sdk_args())
        again = self.run_setup("--non-interactive", "--token-file", self.secret("t", TOKEN), "--users", "42",
                               check=False)
        self.assertNotEqual(again.returncode, 0)  # no --update: refuse
        self.assertIn("--update", again.stderr)
        env_before = (self.config / "env").read_text()
        self.log.write_text("")
        self.run_setup("--update", "--with-sdk", "--non-interactive")
        self.assertEqual((self.config / "env").read_text(), env_before)  # no --token-file: untouched
        calls = self.log.read_text()
        self.assertIn("pip install -U claude-agent-sdk", calls)
        self.assertEqual(calls.count("launchctl bootstrap"), 2)
        bad = self.run_setup("--update", "--users", "5", check=False)
        self.assertNotEqual(bad.returncode, 0)

    def test_env_pairs(self):
        self.install("--env", "CC_FOO_1=a b", "--env=TG_API_BASE=https://tg.example.com")
        lines = (self.config / "env").read_text().splitlines()
        self.assertEqual(lines, ["TG_BOT_TOKEN=" + TOKEN, "TG_ALLOWED_USERS=42,43", "CC_FOO_1=a b",
                                 "TG_API_BASE=https://tg.example.com"])
        self.assertEqual(stat.S_IMODE((self.config / "env").stat().st_mode), 0o600)
        for bad in ("PATH=/x", "cc_low=1", "CC_X"):
            proc = self.run_setup("--non-interactive", "--env", bad, "--token-file", self.secret("t", TOKEN),
                                  "--users", "1", check=False)
            self.assertNotEqual(proc.returncode, 0, bad)

    def test_token_file(self):
        self.run_setup("--non-interactive", "--users", "7", "--token-file",
                       self.secret("tok", "TG_BOT_TOKEN=%s\n" % TOKEN))
        self.assertEqual((self.config / "env").read_text().splitlines()[0], "TG_BOT_TOKEN=" + TOKEN)
        shutil.rmtree(self.home)
        self.home.mkdir()
        loose = Path(self.secret("loose", TOKEN))
        loose.chmod(0o644)
        proc = self.run_setup("--non-interactive", "--users", "7", "--token-file", str(loose), check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((self.config / "env").exists())
        proc = self.run_setup("--non-interactive", "--users", "7", "--token", TOKEN, check=False)
        self.assertNotEqual(proc.returncode, 0)  # a token on the command line is not an option

    def test_dry_run_changes_nothing(self):
        out = self.install(*self.sdk_args(), "--dry-run").stdout
        self.assertIn("launchctl bootstrap", out)
        self.assertNotIn(TOKEN, out)
        self.assertFalse(self.agents.exists() and any(self.agents.iterdir()))
        self.assertFalse((self.config / "env").exists())
        self.assertNotIn("launchctl", self.log.read_text())


if __name__ == "__main__":
    unittest.main()
