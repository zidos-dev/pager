# Manual installation

Everything here is optional. The [Quick start](../README.md#quick-start) wizard (`python3 setup.py`) covers the common case; this page describes the same steps by hand, the `deploy/setup.sh` flags, publishing the mini-app / PWA, SDK-only mode and the group assistant on another model.

Russian version: [manual-install.ru.md](manual-install.ru.md). Back to the [README](../README.md).

## Quick install (no domain needed)

On Linux with a systemd user session, run this from your cloned checkout:

```bash
bash deploy/setup.sh
```

The script asks for your @BotFather token and allowed Telegram user IDs, installs the user service and defaults
to **Tailscale Funnel**: a free, permanent HTTPS address under `*.ts.net`, without buying a domain. Install and
log into Tailscale first (the script prints the official install command and never runs it). If you are not
logged in, the script offers to run `sudo tailscale up`. If Funnel is not yet enabled for your tailnet, the script
shows the enablement link large, opens it in your browser when a desktop session is available, prints a QR code when
`qrencode` is installed (so you can open it from your phone), and **waits** while you click the single
"Enable" page; then it continues by itself. The wait is limited by `CC_SETUP_FUNNEL_ENABLE_WAIT` seconds
(default 600); press Ctrl+C and rerun with `--reconfigure-publish --publish=funnel` any time. With
`--non-interactive` the script only prints the link and stops, unless you add `--wait-for-enable`. The address stays stable across restarts while the machine's Tailscale
DNS name stays the same. A stable address preserves PWA installs, push subscriptions and the Telegram WebApp
button. The machine must stay online. See the [Funnel documentation](https://tailscale.com/docs/reference/tailscale-cli/funnel).

- `--dry-run` previews changes; `--help` lists all options. Existing installs are left untouched unless you use
  `--reconfigure-publish --publish=MODE`, which changes publishing only.
- `--non-interactive --token "$BOT_TOKEN" --users 123456789 --publish=funnel` runs unattended. Prefer the hidden
  token prompt when possible: command-line arguments can appear in process listings and shell history.
- Fully click-free Funnel: `--ts-api-key-file /absolute/path/tailscale.env` (a **mode-600 file** with
  `TS_API_KEY=tskey-api-...` or just the key). Create the key in the Tailscale admin console under
  Settings -> Keys -> "Generate access token" (API access token) and delete it after installation; it is only needed
  while installing. The script reads it in-process (never in a command line or output) and uses the official
  [Tailscale API](https://tailscale.com/api): it reads the policy file (`GET /api/v2/tailnet/-/acl`, HuJSON), adds
  `"nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]` unless already present (comments are kept; saved
  with `If-Match` from the `ETag`), and turns on HTTPS certificates (`PATCH /api/v2/tailnet/-/settings`,
  `{"httpsEnabled": true}`). If the API refuses a step, the script falls back to the one-click link. The tailnet is
  changed only by those two settings; to roll back, delete the added `nodeAttrs` entry in the Access controls editor
  and switch off HTTPS certificates in the DNS page. `tailscale funnel reset` stops publishing on the machine.
  The public DNS name can take a few minutes to resolve after the first enablement.
- `--with-sdk --sdk-key-file /absolute/path/anthropic.env` adds SDK sessions; `--with-stt` adds local speech
  recognition (requires `ffmpeg`/`ffprobe`). `--stt-groq-key-file /absolute/path/groq.env` adds Groq fallback.
  Put `ANTHROPIC_API_KEY=value` or `GROQ_API_KEY=value` in a **mode-600 file** first; pass only its path.
- `--publish=quick` uses installed `cloudflared` without an account, but **the address changes on every restart**:
  installed PWAs and push subscriptions break. Use it for temporary access.
- `--publish=cloudflare --cf-hostname app.example.com` prints the manual named-tunnel commands for your own
  Cloudflare domain. `--cf-api-token-file` is accepted for planning but is not read or used; this recipe uses
  browser login. `--publish=none` keeps the bot Telegram-only.

Send `/start` to open the mini-app, then `/pwa` to install and sign in. Funnel writes the URL to the file the bot
polls, so no bot restart is needed. Reconfiguration requires `CC_WEBAPP_URL` unset and the default tunnel file
and port. Push additionally needs `cryptography` and `CC_VAPID_SUB` (see below). The installer prints undo commands.
## Installation

1. **Create the bot.** In @BotFather: `/newbot`, copy the token. Enable topics for private chats ("Threaded Mode" in
   the bot settings) if you want per-session topics; without it the bot works with a single "current session".
2. **Get the code.** `git clone https://github.com/zidos-dev/pager ~/zidos-pager` (any path works; adjust the unit file).
3. **Configure.**
   ```bash
   mkdir -p ~/.config/zidos-pager
   cp ~/zidos-pager/env.example ~/.config/zidos-pager/env
   chmod 600 ~/.config/zidos-pager/env
   $EDITOR ~/.config/zidos-pager/env     # set TG_BOT_TOKEN, TG_ALLOWED_USERS (your Telegram user id), CC_LANG
   ```
   Find your numeric user id with any "get my id" bot. The bot **refuses to start without `TG_ALLOWED_USERS`**.
4. **Run it as a systemd user service.**
   ```bash
   mkdir -p ~/.config/systemd/user
   cp ~/zidos-pager/deploy/zidos-pager.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now zidos-pager
   journalctl --user -u zidos-pager -f            # logs never contain message texts
   loginctl enable-linger "$USER"              # keep it running when you are logged out
   ```
   Open the bot in Telegram and send `/start`: the panel appears (in a "🎛 Agents" topic in threaded mode).
5. **Mini-app / PWA (optional).** The bot serves the mini-app on `127.0.0.1:18795`; Telegram and phones need a public
   https address in front of it. Pick one:
   - **Named Cloudflare tunnel** (stable address): follow `deploy/zidos-pager.yml.example`, install
     `deploy/zidos-pager-tunnel.service`, set `CC_WEBAPP_URL=https://cc.example.com` in the env file.
   - **Quick tunnel** (random `*.trycloudflare.com` address, changes on restart): install
     `deploy/zidos-pager-quick-tunnel.service`; the bot reads the address from a file and updates the Telegram menu button.
   - Any reverse proxy with TLS works too — just point `CC_WEBAPP_URL` at it.
6. **Install the PWA (optional).** Send `/pwa` to the bot, open the address in Safari/Chrome, "Add to Home Screen",
   launch the app from the home screen and paste the one-time code (iPhone PWAs have separate storage, so the link
   from Telegram does not carry over). `/devices` lists and revokes signed-in devices.
7. **Push notifications (optional).** `pip install cryptography`, set `CC_VAPID_SUB=mailto:you@example.com`, restart.
8. **Files from sessions (optional).** `ln -s ~/zidos-pager/deploy/cc-send ~/.local/bin/cc-send`; a session can then run
   `cc-send report.pdf "caption"`.

Run the tests with `python3 -m unittest discover -s tests` (no network, no Claude, no tmux needed).
## Without Claude Code (SDK only)

The bot can run on a machine that has no `claude` CLI at all. Then only sessions of the SDK runner are used: the
panel lists them, 🆕 New session starts one in the runner, and the subscription quota, tmux screen and automatic
names are hidden. If neither `claude` nor a runner with a key is available, the panel says so. `CC_CLAUDE=off`
forces this mode even when `claude` is installed.

**You need:** Python 3.10+ (3.12+ for the bot), an Anthropic API key and a Telegram bot token. The
`claude-agent-sdk` wheel brings its own CLI, so Claude Code is not installed separately.

1. Install the runner into its own virtualenv:
   ```
   cd ~/zidos-pager
   python3 -m venv .venv-sdk && .venv-sdk/bin/pip install claude-agent-sdk
   ```
2. Put the key in a file with a `NAME=value` line and lock it down: `chmod 600 ~/.config/zidos-pager/anthropic.env`
   (for example `ANTHROPIC_API_KEY=sk-ant-...`).
3. Create `~/.config/zidos-pager/sdk.env` (`chmod 600`) with only the runner's settings — never the bot's env file, it
   holds the Telegram token:
   ```
   CC_SDK_KEY_FILE=/home/you/.config/zidos-pager/anthropic.env
   CC_SDK_KEY_VAR=ANTHROPIC_API_KEY
   CC_SDK_MODEL=sonnet
   CC_SDK_BUDGET=1.00
   ```
4. Install both units (fix the paths inside), then start them:
   ```
   mkdir -p ~/.config/systemd/user
   cp deploy/zidos-pager.service deploy/zidos-pager-sdk.service ~/.config/systemd/user/
   systemctl --user daemon-reload && systemctl --user enable --now zidos-pager-sdk zidos-pager
   ```

**Works:** sessions started from the bot, writing to them, 🔐 permission requests, question buttons, the 💲 cost
and budget, topics, notifications and the mini-app (history, files). **Does not exist** in this mode: sessions of
your Claude subscription, the tmux screen and keys, the subscription quota, automatic names.

## Your own bot on another model (DeepSeek, OpenRouter, …)

The SDK runner works with any provider that offers an Anthropic-compatible address. You bring your own Telegram bot
and your own key; three settings choose the model.

**You need:** Linux with `python3`, `python3-venv`, `bubblewrap` and `socat` (`sudo apt install python3-venv bubblewrap socat`): chat sessions
refuse to start without the OS sandbox.

1. Create a bot in @BotFather and find your numeric Telegram id with any "get my id" bot. Get the code:
   `git clone https://github.com/zidos-dev/pager ~/zidos-pager && cd ~/zidos-pager`, or from an archive:
   `tar -xzf zidos-pager-own-bot.tar.gz -C ~ && cd ~/zidos-pager`
2. Put the key in a file and lock it down:
   ```
   mkdir -p ~/.config/zidos-pager
   echo 'DEEPSEEK_API_KEY=sk-...' > ~/.config/zidos-pager/deepseek.env
   chmod 600 ~/.config/zidos-pager/deepseek.env
   ```
3. Install:
   ```
   deploy/setup.sh --token 'BOT_TOKEN' --users YOUR_ID --publish none --with-sdk \
     --sdk-key-file ~/.config/zidos-pager/deepseek.env --sdk-key-var DEEPSEEK_API_KEY \
     --sdk-base-url https://api.deepseek.com/anthropic --sdk-model deepseek-flash
   ```
   Now you can talk to the model in a private chat with the bot.
4. **To answer in a group:** add the bot, write `@your_bot id` there (the id comes to you privately), then add to
   `~/.config/zidos-pager/env` and run `systemctl --user restart zidos-pager`:
   ```
   CC_RESPONDER_CHAT=-100...                 # the group id
   CC_RESPONDER_CWD=/home/you/knowledge      # the folder the bot answers from; a plain folder or a git clone
   CC_RESPONDER_NAME=Helper                  # how the bot calls itself
   CC_RESPONDER_BRIEF=/home/you/brief.md     # optional: role, tone, rules
   ```
   With the default privacy mode the bot sees only mentions and replies to it. To let it follow the whole conversation,
   send `/setprivacy` to @BotFather, choose Disable, and add the bot to the group again.
   The bot answers mentions and replies to its messages. Changing existing files, and anything outside the sandbox, waits for your 🔐; a new file in the folder is possible without it.

| Provider | `--sdk-base-url` | `--sdk-model` | Key variable |
| --- | --- | --- | --- |
| DeepSeek | `https://api.deepseek.com/anthropic` | `deepseek-flash` or `deepseek-v4-pro` | `DEEPSEEK_API_KEY` |
| OpenRouter | `https://openrouter.ai/api` | e.g. `deepseek/deepseek-v4.1-flash` | `OPENROUTER_API_KEY` |
| GLM (z.ai) | `https://api.z.ai/api/anthropic` | `glm-5.3` | `GLM_API_KEY` |
| Kimi (not tested: needs a Kimi Code subscription) | `https://api.kimi.com/coding` | `kimi-for-coding` | `KIMI_API_KEY` |

The runner's `sdk.env` also takes `CC_SDK_STRONG_MODEL` (model for change requests, default: the same model),
`CC_SDK_MAX_TURNS` (steps per answer, default 30) and `CC_CHAT_SITE` (a site the chat sandbox may reach).

**Codex** is not Anthropic-compatible, so it is a separate engine for the group: run `codex login`, then set `CC_RESPONDER_ENGINE=codex` (and optionally `CC_CODEX_MODEL`) in `~/.config/zidos-pager/env`. It reads the folder and answers but changes nothing; no SDK runner is needed for the group. Private-chat sessions stay on the runner. Use it only in a group of people you trust: the model can read the Codex login file, so a guest could ask for it.

**In the mini-app** the sliders button next to «Sessions» opens the same settings: pick a provider, paste a key, press «Check and connect». The key is saved only after the provider accepts it, and it never comes back to the screen. The mini-app needs a public address: install with `--publish quick` instead of `--publish none`. Connecting restarts the runner and stops its running sessions.

**Good to know.** The 💲 budget does not apply: the SDK knows only Claude prices, so spending is limited by your
balance at the provider. When the balance runs out, the group gets a neutral message and the error comes to you.
Web search and opening pages depend on the provider: DeepSeek documents `web_search`; `web_fetch` is not listed there. Keep the folder free of secrets: anyone in the group can ask about what is in it. `.env` files are blocked, a secret under another name is not.

## More bots from the command line

Every extra group bot is its own process pair (`zidos-pager-bot@<bot>`, `zidos-pager-bot-sdk@<bot>`; on a Mac
`dev.zidos.pager.bot.<bot>`, `dev.zidos.pager.sdk.<bot>`) with settings in `~/.config/zidos-pager/bots/<bot>/`. The wizard and the
mini-app's Bots sheet call the same commands; each reads JSON on stdin and prints JSON (`{"ok": false, "error": ...}`
on a refusal). Keep keys out of the shell history: pipe them from a file.

```
cd ~/zidos-pager
echo '{"token": "123456:ABC..."}' | python3 zidos_pager.py bots add            # -> {"ok": true, "slug": "my_bot", ...}
python3 - <<'PY' | python3 zidos_pager.py bots save                              # model and key, checked before saving
import json; print(json.dumps({"slug": "my_bot", "provider": "deepseek", "key": open("deepseek.key").read().strip()}))
PY
echo '{"slug": "my_bot", "about": "A dance school: schedule, prices, sign-up."}' | python3 zidos_pager.py bots persona
echo '{"slug": "my_bot", "cwd": "~/dance", "site": "https://dance.example", "chat": -100123}' | python3 zidos_pager.py bots save
python3 zidos_pager.py bots list
echo '{"slug": "my_bot"}' | python3 zidos_pager.py bots remove
```

`bots persona` returns a generated `brief` and `persona` (`name`, `short`, `description`) without saving them; pass them to
`bots save` as they are or edited. Groups the bot has seen appear in `bots list` (`groups`). Providers: `deepseek`,
`glm`, `kimi`, `openrouter`, `anthropic`, `custom` (with `base`), `codex`.
