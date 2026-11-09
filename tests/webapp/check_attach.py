"""Files attach to the message draft like in Telegram, they are not sent on pick (owner 20.10 10:10: «сообщение
автоматом улетает при загрузке фото»). Picking files only fills a preview row over the field (#s-attach, one .att per
file — <img> for images — with a button.att-x); «Send» is active with text OR files; one POST /upload carries every
file and the text as caption; success clears field and row, failure keeps both with a toast; text alone still goes
through /send; leaving the session keeps the draft files and text with that session (08.11).

Run externally with Playwright; APP_DIR selects checkout. Server receipts: UPLOAD_LOG / SEND_LOG of the stand.
"""
import base64
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
PORT = int(os.environ.get("PORT", "18981"))
URL = f"http://127.0.0.1:{PORT}"
SID = "c3e5a7b9"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def check(ok, message):
    if not ok:
        raise AssertionError(message)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def opened(page):
    page.goto(f"{URL}/app/#s={SID}", wait_until="load")
    expect(page.locator("#session-screen")).to_be_visible()
    page.wait_for_timeout(900)


def attach(page, files):
    page.locator("#s-file").set_input_files([str(f) for f in files])
    page.wait_for_timeout(700)


def case_send(page, ctx, logs, files):
    opened(page)
    attach(page, files)
    check(records(logs["upload"]) == [] and records(logs["send"]) == [], "picking files sent something")
    items = page.locator("#s-attach .att")
    expect(items).to_have_count(2)
    expect(items.nth(0).locator("img")).to_have_count(1)
    expect(page.locator("#s-send")).to_be_visible()
    expect(page.locator("#s-send")).to_be_enabled()
    page.locator("#s-text").fill("with photo")
    page.locator("#s-send").click()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not records(logs["upload"]):
        page.wait_for_timeout(50)
    page.wait_for_timeout(400)
    up = records(logs["upload"])
    check(len(up) == 1, f"want exactly one upload, got {up}")
    check(up[0]["caption"] == "with photo" and [f["name"] for f in up[0]["files"]] == [f.name for f in files], f"upload body {up[0]}")
    check(records(logs["send"]) == [], "text with files also went through /send")
    expect(page.locator("#s-text")).to_have_value("")
    expect(page.locator("#s-attach .att")).to_have_count(0)


def case_remove(page, ctx, logs, files):
    opened(page)
    attach(page, files)
    page.locator("#s-attach .att").nth(0).evaluate("el => { el.dataset.keep = '1' }")
    page.locator("#s-attach .att").nth(1).locator("button.att-x").click()
    expect(page.locator("#s-attach .att")).to_have_count(1)
    # the remaining tile is the same node: a rebuild replays its entrance animation, it blinks (owner 09.11)
    check(page.locator("#s-attach .att").nth(0).get_attribute("data-keep") == "1", "removing a tile rebuilt the others")
    page.locator("#s-send").click()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not records(logs["upload"]):
        page.wait_for_timeout(50)
    up = records(logs["upload"])
    check(len(up) == 1 and [f["name"] for f in up[0]["files"]] == [files[0].name] and not up[0]["caption"], f"after removing one: {up}")


def case_fail(page, ctx, logs, files):
    ctx.add_cookies([{"name": "upload_reply", "value": "fail", "url": URL}])
    opened(page)
    attach(page, files)
    page.locator("#s-text").fill("keep me")
    page.locator("#s-send").click()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not records(logs["upload"]):
        page.wait_for_timeout(50)
    page.wait_for_timeout(600)
    expect(page.locator("#s-text")).to_have_value("keep me")
    expect(page.locator("#s-attach .att")).to_have_count(2)
    expect(page.locator("#toast")).to_be_visible()


def case_leave(page, ctx, logs, files):
    # 08.11 owner: «после выхода из чата пропадают вложения и ломается верстка окна ввода» — the draft (files and text)
    # stays with its session; back in it the field fits the restored text again; another session does not get it
    def back():
        page.locator("#session-screen [data-back]:visible, #session-screen .back:visible").first.click()
        expect(page.locator("#session-screen")).to_be_hidden()
        page.wait_for_timeout(500)

    def enter(sid):
        page.locator(f'.card[data-id="{sid}"]').click()
        expect(page.locator("#session-screen")).to_be_visible()
        page.wait_for_timeout(700)

    opened(page)
    attach(page, files)
    page.locator("#s-text").fill("line one\nline two\nline three")
    page.wait_for_timeout(200)
    h0 = page.locator("#s-text").evaluate("t => t.clientHeight")
    back()
    enter("d4f6b8c0")
    check(page.locator("#s-attach .att").count() == 0, "another session got this session's files")
    check(page.locator("#s-text").input_value() == "", "another session got this session's text")
    back()
    enter(SID)
    expect(page.locator("#s-attach .att")).to_have_count(2)
    check(page.locator("#s-text").input_value() == "line one\nline two\nline three", "draft text lost")
    fit = page.locator("#s-text").evaluate("t => [t.clientHeight, t.scrollHeight]")
    check(fit[0] + 1 >= fit[1] and abs(fit[0] - h0) <= 2, f"restored draft is cut: height {fit[0]} < text {fit[1]} (typed {h0})")
    check(records(logs["upload"]) == [], "leaving sent the files")
    expect(page.locator("#s-send")).to_be_enabled()


def case_text(page, ctx, logs, files):
    opened(page)
    page.locator("#s-text").fill("text only")
    page.locator("#s-send").click()
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not records(logs["send"]):
        page.wait_for_timeout(50)
    check(len(records(logs["send"])) == 1 and records(logs["upload"]) == [], "text alone must go through /send only")


def big_png(w=1500, h=2600):
    import struct, zlib
    row = b"\x00" + b"\xd2\xfb\x4a" * w
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * h, 1)) + chunk(b"IEND", b"")


def case_clip(page, ctx, logs, files):
    # one tap on the clip opens the picker once: iOS Safari drops the pick when the file input gets click() twice
    # (owner 20.10 11:17, «просто ничего»; a doubled listener — Chromium and desktop WebKit ignore the second click)
    opened(page)
    page.evaluate("""()=>{window.__fc=0;const i=document.getElementById('s-file');i.click=function(){window.__fc++}}""")
    page.locator("#s-clip").click()
    page.wait_for_timeout(200)
    n = page.evaluate("window.__fc")
    check(n == 1, f"clip tap clicks the file input {n}×, want 1")


def case_kb(page, ctx, logs, files):
    # field focused → clip → pick: the picker closes the iPhone keyboard, so the footer must leave the keyboard layout
    # (owner 21.10 00:54, IMG_5170: quota line gone and the field down on the home bar while a photo sat in the draft)
    opened(page)
    page.locator("#s-text").click()
    page.wait_for_timeout(400)
    with page.expect_file_chooser() as fc:
        page.locator("#s-clip").click()
    fc.value.set_files([str(f) for f in files])
    page.wait_for_timeout(800)
    expect(page.locator("#s-attach .att")).to_have_count(2)
    check(not page.evaluate("document.activeElement===document.getElementById('s-text')"), "field keeps focus after the picker: iOS shows no keyboard, the footer stays in keyboard layout")
    check(not page.evaluate("document.documentElement.classList.contains('kb')"), "html.kb stuck after picking files")
    expect(page.locator("#session-screen .lab-sq")).to_be_visible()  # строка квоты под полем (слой layers)


def case_thumb(page, ctx, logs, files):
    # a camera-size photo is previewed by a small re-encoded frame, never by the original in <img> (iOS web view ran out of memory)
    opened(page)
    page.locator("#s-file").set_input_files([{"name": "camera.png", "mimeType": "image/png", "buffer": big_png()}])
    img = page.locator("#s-attach .att img")
    expect(img).to_have_count(1)
    page.wait_for_function("(()=>{const i=document.querySelector('#s-attach .att img');return i&&i.complete&&i.naturalWidth>0})()")
    nw, nh = img.evaluate("i=>[i.naturalWidth,i.naturalHeight]")
    check(max(nw, nh) <= 112, f"preview decodes {nw}×{nh}, want ≤112 px")


def case_lost(page, ctx, logs, files):
    # the web view restarting drops the in-memory draft: say so instead of «nothing»
    opened(page)
    attach(page, files)
    expect(page.locator("#s-attach .att")).to_have_count(2)
    page.reload(wait_until="load")
    expect(page.locator("#toast").filter(has_text="attachments were dropped")).to_be_visible(timeout=5000)
    page.reload(wait_until="load")
    page.wait_for_timeout(800)
    expect(page.locator("#toast").filter(has_text="attachments were dropped")).to_have_count(0)


def main():
    failures = 0
    engines = [n.strip() for n in os.environ.get("BROWSERS", "chromium,webkit").split(",") if n.strip()]
    with tempfile.TemporaryDirectory(prefix="cc-attach-") as tmp:
        tmp = Path(tmp)
        files = [tmp / "photo.png", tmp / "notes.txt"]
        files[0].write_bytes(PNG)
        files[1].write_text("route test")
        logs = {"upload": tmp / "upload.jsonl", "send": tmp / "send.jsonl"}
        data = tmp / "demo.json"
        data.write_text((ROOT.parent / "stand/demo2.json").read_text())
        for p in logs.values():
            p.write_text("")
        env = dict(os.environ, DATA_FILE=str(data), PORT=str(PORT), UPLOAD_LOG=str(logs["upload"]), SEND_LOG=str(logs["send"]))
        server = subprocess.Popen([sys.executable, str(ROOT / "stand_server.py")], env=env, stdout=subprocess.DEVNULL)
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
                for engine in engines:
                    for name, case in [("send", case_send), ("remove", case_remove), ("fail", case_fail), ("leave", case_leave), ("text", case_text), ("clip", case_clip), ("kb", case_kb), ("thumb", case_thumb), ("lost", case_lost)]:
                        for p in logs.values():
                            p.write_text("")
                        data.write_text((ROOT.parent / "stand/demo2.json").read_text())
                        browser = getattr(pw, engine).launch()
                        ctx = browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, locale="en-US")
                        ctx.add_init_script("localStorage.setItem('cc-lang','en');localStorage.setItem('cc-filter','all')")
                        ctx.route("https://telegram.org/**", lambda route: route.fulfill(body=""))
                        page = ctx.new_page()
                        errors = []
                        page.on("pageerror", lambda e: errors.append(str(e)))
                        try:
                            case(page, ctx, logs, files)
                            check(not errors, "page errors: " + "; ".join(errors))
                            print(f"PASS {engine} {name}")
                        except Exception as e:
                            failures += 1
                            print(f"FAIL {engine} {name}: {str(e)[:300]}")
                        finally:
                            browser.close()
        finally:
            server.terminate()
            server.wait(timeout=5)
    print(f"TOTAL {'PASS' if not failures else 'FAIL'} ({failures} failed)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
