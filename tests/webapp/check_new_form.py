"""«New session» form (09.11 owner, IMG_5597/5598): the task text does not survive a launch or a reopen;
folder is «default | other» with hints only while typing a path; files attach right in the task and go with the launch.
RU/EN x light/dark at 390x844, targets >= 44, no horizontal overflow.
Run: python3 tests/webapp/check_new_form.py   (own stand on PORT, default 18987; BROWSER=chromium|webkit)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("PORT", "18987"))
URL = f"http://127.0.0.1:{PORT}"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360f8cf00000301010018dd8db00000000049454e44ae426082")


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def fits(page, sel):
    for b in page.locator(sel).all():
        if b.is_visible():
            r = b.bounding_box(); check(r["height"] >= 44 and r["x"] >= 0 and r["x"] + r["width"] <= 391, f"target {sel} {r}")
    check(page.evaluate("document.documentElement.scrollWidth") <= 390, "horizontal overflow")


def back_to_list(page):
    page.locator("#new-screen [data-back]").click()
    expect(page.locator("#new-screen")).to_be_hidden()


def form(page, lang):
    page.goto(f"{URL}/app/", wait_until="load")
    page.locator("#new-btn").click()
    chips = page.locator("#n-dirs .chip")
    expect(chips).to_have_count(2)  # default and «other», no list of suggested folders
    expect(chips.nth(0)).to_have_attribute("aria-pressed", "true")
    expect(chips.nth(1)).to_have_text("Другая" if lang == "ru" else "Other")
    expect(page.locator("#n-other")).to_be_hidden()
    # text does not survive a reopen
    page.locator("#n-text").fill("left behind")
    back_to_list(page); page.locator("#new-btn").click()
    expect(page.locator("#n-text")).to_have_value("")
    # other folder: hints only while typing
    chips.nth(1).click()
    expect(page.locator("#n-path")).to_be_focused()
    expect(page.locator("#n-hints .hint")).to_have_count(0)
    page.locator("#n-path").fill("~/CO")
    hints = page.locator("#n-hints .hint")
    expect(hints.first).to_be_visible()
    texts = hints.all_inner_texts()
    check(all(h.lower().startswith("~/co") for h in texts), f"hints off the prefix {texts}")
    expect(page.locator("#n-nodir")).to_be_visible()  # «~/CO» itself is not a folder
    fits(page, "#n-dirs .chip, #n-hints .hint, #n-clip")
    first = texts[0]; hints.first.click()
    expect(page.locator("#n-path")).to_have_value(first + "/")
    expect(page.locator("#n-nodir")).to_be_hidden()
    page.locator("#n-path").fill("~/no-such-place-xyz")
    expect(page.locator("#n-nodir")).to_be_visible(); expect(page.locator("#n-hints .hint")).to_have_count(0)
    page.locator("#n-path").fill(first)
    expect(page.locator("#n-nodir")).to_be_hidden()
    # files right in the task: one tap opens one chooser
    with page.expect_file_chooser() as fc:
        page.locator("#n-clip").click()
    d = tempfile.mkdtemp()  # kept until the end: Chromium reads a chosen file only when it uploads it
    a, b, c = Path(d, "shot.png"), Path(d, "notes.txt"), Path(d, "extra.txt")
    a.write_bytes(PNG); b.write_text("hello"); c.write_text("x")
    fc.value.set_files([str(a), str(b), str(c)])
    expect(page.locator("#n-attach .att")).to_have_count(3)
    page.locator("#n-attach .att").nth(2).locator(".att-x").click()
    expect(page.locator("#n-attach .att")).to_have_count(2)
    page.locator("#n-text").fill("look at these")
    with page.expect_request(lambda r: r.url.endswith("/api/new") and r.method == "POST") as rq:
        page.locator("#fab").click()
    ct = rq.value.headers.get("content-type", "")
    check(ct.startswith("multipart/form-data"), f"launch with files is not multipart: {ct}")
    expect(page.locator("#new-screen")).to_be_hidden()  # the stand has answered by now
    new = [json.loads(x)["new"] for x in Path(os.environ["SEND_LOG"]).read_text().splitlines() if '"new"' in x][-1]
    check(new == {"text": "look at these", "cwd": first, "files": ["shot.png", "notes.txt"]}, f"launch as the stand saw it: {new}")
    # the form is clean when opened after a launch
    expect(page.locator("#new-screen")).to_be_hidden()
    page.locator("#new-btn").click()
    expect(page.locator("#n-text")).to_have_value("")
    expect(page.locator("#n-attach")).to_be_hidden()
    expect(page.locator("#n-path")).to_have_value("")
    expect(page.locator("#n-other")).to_be_hidden()
    shutil.rmtree(d)


def main():
    os.environ["SEND_LOG"] = tempfile.mktemp(suffix=".log")
    data = tempfile.NamedTemporaryFile(suffix=".json", delete=False); data.write((ROOT.parent / "stand" / "demo2.json").read_bytes()); data.close()
    server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=dict(os.environ, PORT=str(PORT), DATA_FILE=data.name, SEND_LOG=os.environ["SEND_LOG"]), stdout=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try: urllib.request.urlopen(URL + "/api/dirs").read(); break
            except Exception: time.sleep(.1)
        with sync_playwright() as pw:
            browser = getattr(pw, os.environ.get("BROWSER", "chromium")).launch()
            for lang in ("en", "ru"):
                for theme in ("light", "dark"):
                    ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, color_scheme=theme,
                                              locale="ru-RU" if lang == "ru" else "en-US", service_workers="block")
                    page = ctx.new_page(); page.set_default_timeout(5000)
                    form(page, lang)
                    ctx.close()
            browser.close()
    finally:
        server.terminate(); os.unlink(data.name)
    print("check_new_form: OK")


if __name__ == "__main__":
    main()
