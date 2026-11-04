"""Legacy cc-tgbot -> zidos-pager shim: directory migration and env-var fallback."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import zidos_pager as zp  # noqa: E402


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


class LegacyMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.files = {".config/cc-tgbot/env": "TG_BOT_TOKEN=x\n", ".config/cc-tgbot/state.json": "{}",
                      ".config/cc-tgbot/bots/x/env": "CC_ROLE=responder\n", ".local/share/cc-tgbot/a.txt": "a"}
        for rel, text in self.files.items():
            write(os.path.join(self.home, rel), text)

    def moved(self, rel):
        return os.path.join(self.home, rel.replace("cc-tgbot", "zidos-pager"))

    def test_moves_and_is_idempotent(self):
        for _ in range(2):
            zp.migrate_legacy_paths(self.home)
            for rel, text in self.files.items():
                self.assertEqual(read(self.moved(rel)), text)
        for rel in self.files:  # legacy absolute paths (old env files, units) still resolve, via a symlink
            self.assertTrue(os.path.islink(os.path.join(self.home, rel.split("cc-tgbot")[0], "cc-tgbot")))
            self.assertEqual(read(os.path.join(self.home, rel)), self.files[rel])

    def test_legacy_entry_point_runs_zidos_pager(self):
        import subprocess
        root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
        out = subprocess.run([sys.executable, os.path.join(root, "cc_tgbot.py"), "--help"], capture_output=True, text=True,
                             env={"HOME": self.home, "PATH": os.environ.get("PATH", "")}, timeout=60)  # legacy entry
        self.assertTrue(os.path.islink(os.path.join(self.home, ".config/cc-tgbot")), out.stderr)  # legacy dir migrated by main()

    def test_existing_new_dir_untouched(self):
        write(os.path.join(self.home, ".config/zidos-pager/env"), "NEW\n")
        zp.migrate_legacy_paths(self.home)
        self.assertEqual(read(os.path.join(self.home, ".config/zidos-pager/env")), "NEW\n")
        self.assertEqual(read(os.path.join(self.home, ".config/cc-tgbot/env")), "TG_BOT_TOKEN=x\n")
        self.assertEqual(read(self.moved(".local/share/cc-tgbot/a.txt")), "a")

    def test_env_var_fallback(self):
        self.assertEqual(zp.Config(environ={"HOME": self.home, "CC_TGBOT_ENV": "/legacy/env"}).env_path, "/legacy/env")
        env = {"HOME": self.home, "CC_TGBOT_ENV": "/legacy/env", "ZIDOS_PAGER_ENV": "/new/env"}
        self.assertEqual(zp.Config(environ=env).env_path, "/new/env")
        self.assertTrue(zp.Config(environ={"HOME": self.home}).env_path.endswith(".config/zidos-pager/env"))


if __name__ == "__main__":
    unittest.main()
