"""Setup wizard, step «Model» and a bot's model: Grok and Gemini are sign-in engines like Codex — no key and model fields, the note
names their own login command. Serves assets/wizard/ on PORT (default 18991) with /api stubbed.
Run: python3 tests/webapp/check_wizard_engines.py
"""
import functools
import http.server
import os
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

PORT = int(os.environ.get("PORT", "18991"))
server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), functools.partial(
    http.server.SimpleHTTPRequestHandler, directory=str(Path(__file__).resolve().parents[2] / "assets" / "wizard")))
threading.Thread(target=server.serve_forever, daemon=True).start()

with sync_playwright() as pw:
    browser = getattr(pw, os.environ.get("BROWSER", "chromium")).launch()
    page = browser.new_page(locale="ru-RU")
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/**", lambda r: r.fulfill(status=200, content_type="application/json", body="{}"))
    page.goto(f"http://127.0.0.1:{PORT}/index.html", wait_until="load")
    got = page.evaluate("""() => { S.presets = [{id: 'codex', title: 'Codex', model: ''}, {id: 'grok', title: 'Grok', model: ''},
        {id: 'gemini', title: 'Gemini', model: ''}, {id: 'kimi', title: 'Kimi', model: 'k'}];
      const r = {}, $ = id => document.getElementById(id);
      for (const id of ['codex', 'grok', 'gemini', 'kimi']) { S.provider = id; paintPresets(); r[id] = [$('model-note').textContent, $('key-f').hidden, $('model-f').hidden]; }
      M.provider = 'gemini'; mPaint(); r.bot = [$('m-note').textContent, $('m-key-f').hidden, $('m-model-f').hidden]; return r; }""")
    browser.close()
server.shutdown()
assert not errors, errors
for eng, cmd in (("codex", "codex login"), ("grok", "grok login"), ("gemini", "agy")):
    note, key_hidden, model_hidden = got[eng]
    assert cmd in note and key_hidden and model_hidden, (eng, got[eng])
assert "agy" in got["bot"][0] and got["bot"][1] and got["bot"][2], got["bot"]
assert not got["kimi"][1], got["kimi"]  # a key provider keeps its key field
print("check_wizard_engines: OK")
