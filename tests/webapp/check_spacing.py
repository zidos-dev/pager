"""One edge for every block (owner 27.10 03:38, IMG_5288: «почему везде разные отступы»). At phone width every
top-level block — the «Sessions» title, the quota tiles, section labels, session cards, the bottom capsule, feed
bubbles, the composer, the question sheet's title, options and buttons — stands at the same distance from the
screen edge, and quota tiles and session cards share one inner padding. The first block under the quota tiles
stands 24 px below them on every tab (IMG_5289: tabs without a section label glued the first card to the tiles). Before: 12, 14, 16, 18, 20 and 22 px.
390 and 360 px, chromium and webkit.

Usage: PORT=19305 python3 check_spacing.py   (APP_DIR selects the checkout)
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
PORT = int(os.environ.get("PORT", "18985"))
URL = f"http://127.0.0.1:{PORT}"
GUT = 16
TOP_GAP = 24  # quota tiles -> first section label or card, on every tab
GAP = """()=>{const t=[...document.querySelectorAll('#list-screen .lqt')].pop(),f=[...document.querySelectorAll('#list > *')].find(e=>e.offsetHeight>0);
 return t&&f?Math.round(f.getBoundingClientRect().top-t.getBoundingClientRect().bottom):null}"""
ASK_SID = "a1c3e5f7"  # demo session with an open question
# [label, selector, which edges: l = left of the box, r = right, t = left of the first text line]
EDGES = """(spec)=>{const vw=innerWidth,out=[];
 for(const [lab,sel,sides] of spec){const els=[...document.querySelectorAll(sel)].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&r.bottom>0&&r.top<innerHeight});
  if(!els.length){out.push([lab,'missing',null]);continue}
  for(const e of els.slice(0,3)){const r=e.getBoundingClientRect();
   if(sides.includes('l'))out.push([lab,'left',Math.round(r.left)]);
   if(sides.includes('r'))out.push([lab,'right',Math.round(vw-r.right)]);
   if(sides.includes('t')){const g=document.createRange();g.selectNodeContents(e);const q=g.getClientRects()[0];out.push([lab,'text',Math.round(q?q.left:r.left)])}}}
 return out}"""
LIST = [["title", "#list-screen .top h1", "t"], ["quota tile", "#list-screen .lqt:first-child", "l"],
        ["quota tile", "#list-screen .lqt:last-child", "r"],
        ["section label", "#list-screen .lsec", "t"], ["card", "#list-screen .card", "lr"],
        ["capsule", "#lab-fcap", "lr"]]
SESSION = [["assistant bubble", "#s-body .msg:not(.me):not(.thinking)", "l"], ["own bubble", "#s-body .me", "r"],
           ["subtasks pill", "#s-subs", "l"], ["sheet title", ".lab-ask-q", "t"],
           ["sheet option", ".lab-ask-opt", "lr"], ["sheet button", ".lab-ask-actions .lab-ask-btn:first-child", "l"],
           ["sheet button", ".lab-ask-actions .lab-ask-btn:last-child", "r"]]
PADS = """()=>[...document.querySelectorAll('#list-screen .lqt, #list-screen .card')].slice(0,4).map(e=>{const s=getComputedStyle(e);return e.className.split(' ')[0]+':'+s.paddingLeft+'/'+s.paddingRight})"""


def run(pw, engine, width):
    browser = getattr(pw, engine).launch()
    page = browser.new_context(viewport={"width": width, "height": 844}).new_page()
    try:
        page.goto(f"{URL}/app/", wait_until="load")
        expect(page.locator("#list-screen .card").first).to_be_visible()
        page.wait_for_timeout(600)
        got = page.evaluate(EDGES, LIST)
        bad = []
        pads = sorted(set(p.split(":")[1] for p in page.evaluate(PADS)))
        # every tab (IMG_5289: «В работе» has no section label and its first card stuck 4 px under the tiles)
        n = page.locator("#lab-fcap .lf-item").count()
        for i in range(n):
            page.locator("#lab-fcap .lf-item").nth(i).click()
            page.wait_for_timeout(500)
            gap = page.evaluate(GAP)
            if gap is not None and gap != TOP_GAP:
                bad.append(f"tab {i + 1}: first block {gap} px under the quota tiles, not {TOP_GAP}")
        page.goto(f"{URL}/app/#s={ASK_SID}", wait_until="load")
        expect(page.locator(".lab-ask-opt").first).to_be_visible()
        page.wait_for_timeout(900)
        got += page.evaluate(EDGES, SESSION)
        pads += sorted(set(page.evaluate("()=>[...document.querySelectorAll('.lab-ask-opt')].map(e=>{const s=getComputedStyle(e);return s.paddingLeft+'/'+s.paddingRight})")))
        pads = sorted(set(pads))
        bad += [f"{lab} {side}={v}" for lab, side, v in got if v != GUT]
        if len(pads) != 1:
            bad.append(f"quota tile, card and sheet option inner padding differ: {pads}")
        if bad:
            raise AssertionError("not on the %d px edge: %s" % (GUT, "; ".join(dict.fromkeys(bad))))
    finally:
        browser.close()


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="cc-space-") as tmp:
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
                            print(f"FAIL {engine} {width}: {str(e)[:600]}")
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
