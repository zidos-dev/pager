"""Owner's files in the session feed show as previews (album for several photos), whatever the inbox folder is called:
session folders carry the session title, so paths have spaces and brackets — «~/tg-inbox/Фронт сайта (f17b45ca)/…»
(owner 21.10 01:09: «превью в чате нет, хотя должны быть»). The caption stays as text; the raw path line does not.

Usage: PORT=19305 python3 check_owner_files.py   (APP_DIR selects the checkout)
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18985"))
URL = f"http://127.0.0.1:{PORT}"
SID = "c3e5a7b9"
D = "/home/user/tg-inbox/Фронт сайта (f17b45ca)/2026-10-21/"
ALBUM = f"Файлы от владельца (2):\n• {D}IMG_5170.png (446 КБ, image/png)\n• {D}IMG_5171.png (434 КБ, image/png)\nВидишь косячок?"
ONE = "Файл от владельца: /home/user/tg-inbox/Поиск: перенос модели/2026-10-20/IMG_5139.jpeg (5.8 МБ, image/jpeg)"
PLAIN = "Файл от владельца: /home/user/tg-inbox/2026-10-19/IMG_5000.jpeg (1.2 МБ, image/jpeg)"


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def run(pw, engine):
    browser = getattr(pw, engine).launch()
    page = browser.new_context(viewport={"width": 390, "height": 844}).new_page()
    try:
        page.goto(f"{URL}/app/#s={SID}", wait_until="load")
        expect(page.locator("#session-screen")).to_be_visible()
        page.wait_for_timeout(1200)
        pv = page.locator("#s-hist [data-pv]")
        paths = pv.evaluate_all("l=>l.map(e=>e.dataset.pv)")
        want = [D + "IMG_5170.png", D + "IMG_5171.png", "/home/user/tg-inbox/Поиск: перенос модели/2026-10-20/IMG_5139.jpeg", "/home/user/tg-inbox/2026-10-19/IMG_5000.jpeg"]
        check(paths == want, f"previews {paths}, want {want}")
        check(page.locator("#s-hist .album [data-pv]").count() == 2, "two photos are not an album")
        hist = page.locator("#s-hist").inner_text()
        check("Видишь косячок?" in hist, "caption lost")
        check("tg-inbox" not in hist, "raw file path shown as text")
    finally:
        browser.close()


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cc-ownfiles-") as tmp:
        d = json.loads((ROOT.parent / "stand/demo2.json").read_text())
        h = d["session"][SID]["history"]
        h[:] = [{"role": "user", "uuid": "f1", "ts": "2026-10-21T22:54:00.000Z", "text": ALBUM},
                {"role": "user", "uuid": "f2", "ts": "2026-10-21T22:55:00.000Z", "text": ONE},
                {"role": "user", "uuid": "f3", "ts": "2026-10-21T22:56:00.000Z", "text": PLAIN}] + h
        data = Path(tmp) / "demo.json"
        data.write_text(json.dumps(d, ensure_ascii=False))
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, DATA_FILE=str(data), PORT=str(PORT)), stdout=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    urllib.request.urlopen(URL + "/api/sessions", timeout=.3).close()
                    break
                except OSError:
                    time.sleep(.1)
            else:
                raise RuntimeError("stand readiness timeout")
            with sync_playwright() as pw:
                for engine in ("chromium", "webkit"):
                    try:
                        run(pw, engine)
                        print(f"PASS {engine}")
                    except Exception as e:
                        failures += 1
                        print(f"FAIL {engine}: {str(e)[:400]}")
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
