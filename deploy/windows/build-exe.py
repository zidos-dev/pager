"""Build dist/cc-tgbot-Setup-windows.exe (run on Windows, repo root): python deploy/windows/build-exe.py"""
import glob
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
FIXED = ["cc_tgbot.py", "sdk_runner.py", "stt_runner.py", "LICENSE", "README*", "env.example", "deploy", "assets", "docs"]  # keep in step with setup.py

cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--console", "--noconfirm", "--name", "cc-tgbot-Setup-windows",
       "--distpath", os.path.join(ROOT, "dist"), "--workpath", os.path.join(ROOT, "build"), "--specpath", os.path.join(ROOT, "build")]
for pat in FIXED:
    for p in glob.glob(os.path.join(ROOT, pat)):
        rel = os.path.relpath(p, ROOT)
        cmd += ["--add-data", f"{p}{os.pathsep}{os.path.dirname(rel) or '.'}" if os.path.isfile(p) else f"{p}{os.pathsep}{rel}"]
cmd.append(os.path.join(ROOT, "setup.py"))
sys.exit(subprocess.call(cmd))
