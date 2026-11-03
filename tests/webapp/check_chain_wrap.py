"""Session header pill «Continuation #k · previous “name” ›»: the arrow never sits alone on a line of its own
(owner 21.10 00:54, IMG_5170 — the pill wrapped and «›» took an empty line). The text still wraps, it is never cut
(owner 19.10, see check_header_menu). Names of every length 20–90 chars, 390 and 360 px, chromium and webkit.

Usage: PORT=19304 python3 check_chain_wrap.py   (APP_DIR selects the checkout)
"""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import urllib.request

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18984"))
URL = f"http://127.0.0.1:{PORT}"
SID = "c3e5a7b9"
# the arrow (::after) is on the last line together with the last text character
PROBE = """n=>{const b=document.getElementById('s-chain');b.hidden=false;b.closest('#s-links').hidden=false;
 const name='Вести фронт-трек и ещё немного слов для длины названия'.repeat(3).slice(0,n);
 b.textContent='Продолжение №5 · предыдущая «'+name+'»';
 const t=b.firstChild,r=document.createRange();r.setStart(t,t.length-1);r.setEnd(t,t.length);
 const last=r.getClientRects();const lr=last[last.length-1];
 const cs=getComputedStyle(b),pb=parseFloat(cs.paddingBottom),box=b.getBoundingClientRect();
 const lh=parseFloat(cs.lineHeight)||parseFloat(cs.fontSize)*1.3;
 return {gap:Math.round(box.bottom-pb-lr.bottom),lh:Math.round(lh)}}"""


def run(pw, engine, width):
    browser = getattr(pw, engine).launch()
    page = browser.new_context(viewport={"width": width, "height": 844}).new_page()
    try:
        page.goto(f"{URL}/app/#s={SID}", wait_until="load")
        expect(page.locator("#session-screen")).to_be_visible()
        page.wait_for_timeout(800)
        bad = []
        for n in range(20, 91):
            r = page.evaluate(PROBE, n)
            if r["gap"] > r["lh"] / 2:  # below the last text character there is a whole line: the arrow alone
                bad.append(n)
        if bad:
            raise AssertionError(f"«›» alone on its own line for name lengths {bad[:12]}")
    finally:
        browser.close()


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cc-chain-") as tmp:
        data = Path(tmp) / "demo.json"
        data.write_text((ROOT.parent / "stand/demo2.json").read_text())
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
                    for width in (390, 360):
                        try:
                            run(pw, engine, width)
                            print(f"PASS {engine} {width}")
                        except Exception as e:
                            failures += 1
                            print(f"FAIL {engine} {width}: {str(e)[:300]}")
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
