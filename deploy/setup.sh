#!/usr/bin/env bash
# Install from this checkout; never download or execute an installation script.
set +x
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash deploy/setup.sh [options]
Linux systemd user installation. Default publisher: funnel (permanent HTTPS, no domain).

  --dry-run                 Print changes as '+ ' commands; change nothing
  --non-interactive         Never prompt; requires --token and --users for a new install
  --token TOKEN             Telegram bot token from @BotFather (hidden prompt by default)
  --token-file FILE         Read the token from a mode-600 file holding TOKEN or TG_BOT_TOKEN=TOKEN
  --users IDS               Allowed numeric Telegram user IDs, comma-separated
  --publish MODE            funnel | cloudflare | quick | none (also --publish=MODE)
  --reconfigure-publish     Existing install: run only publishing; preserve bot/runner config
  --cf-hostname HOST        Your own Cloudflare hostname (cloudflare mode)
  --cf-api-token-file FILE  Accepted for planning only; manual browser login needs no API token
  --wait-for-enable         Funnel: with --non-interactive, also wait for the tailnet to be enabled
  --ts-api-key-file FILE    Funnel: Tailscale API key file (mode 600) to enable Funnel in the tailnet
                            policy without clicks; only needed during installation
  --env KEY=VALUE           Extra line for the bot env file (repeatable); KEY is CC_* or TG_API_BASE
  --update                  Existing install: rewrite env/sdk.env/units from these flags, reuse venvs,
                            restart services (without it an existing install is refused)
  --with-sdk               Create .venv-sdk and install claude-agent-sdk
  --sdk-key-file FILE      File containing ANTHROPIC_API_KEY=value, mode 600
  --sdk-base-url URL       Anthropic-compatible endpoint (e.g. DeepSeek) instead of Anthropic; needs --with-sdk
  --sdk-model NAME         Model name for chat sessions (default sonnet; the endpoint's own name with --sdk-base-url)
  --sdk-login              No key: SDK sessions use Claude Code's own sign-in (run claude auth login first)
  --sdk-key-var NAME       Variable name inside the key file (default ANTHROPIC_API_KEY)
  --with-stt               Create .venv-stt and install faster-whisper (needs ffmpeg/ffprobe)
  --stt-groq-key-file FILE  Optional GROQ_API_KEY=value file, mode 600; use local,groq engines
  --help                   Show this help

Keep API key VALUES out of command lines. Put NAME=value in a mode-600 file first.
--token/--token-file are for automation (SSH wizards: prefer --token-file); the hidden prompt avoids shell history/process arguments.
Funnel, interactive: if Funnel or the Tailscale login is missing, the script shows the link (opens it, prints a
QR code when qrencode exists), waits up to CC_SETUP_FUNNEL_ENABLE_WAIT seconds (default 600) and continues.
This script never installs Tailscale; it prints the official command instead.
HELP
}

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
command_line() { printf '+ '; printf '%q ' "$@"; printf '\n'; }
run() {
    command_line "$@"
    if (( ! dry_run )); then "$@"; fi
}

parse_args() {
    while (( $# )); do
        case "$1" in
            --*=*) set -- "${1%%=*}" "${1#*=}" "${@:2}" ;;
        esac
        case "$1" in
            --help|-h) usage; exit 0 ;;
            --dry-run) dry_run=1 ;;
            --non-interactive) non_interactive=1 ;;
            --reconfigure-publish) reconfigure=1 ;;
            --wait-for-enable) wait_enable=1 ;;
            --with-sdk) with_sdk=1 ;;
            --sdk-login) sdk_login=1 ;;
            --with-stt) with_stt=1 ;;
            --update) update=1 ;;
            --token|--token-file|--env|--users|--publish|--cf-hostname|--cf-api-token-file|--sdk-key-file|--sdk-base-url|--sdk-model|--sdk-key-var|--stt-groq-key-file|--ts-api-key-file)
                (( $# >= 2 )) && [[ -n $2 && $2 != --* ]] || fail 'An option is missing its value; see --help.'
                case "$1" in
                    --token) token=$2 ;;
                    --token-file) token_file=$2 ;;
                    --env)
                        [[ $2 =~ ^(CC_[A-Z0-9_]+|TG_API_BASE)= ]] || fail 'Invalid --env key (expected CC_NAME=value or TG_API_BASE=value).'
                        [[ $2 != *$'\n'* ]] || fail 'Invalid --env value (no newlines).'
                        env_pairs+=("$2") ;;
                    --users) users=$2 ;;
                    --publish) publish=$2 ;;
                    --cf-hostname) cf_hostname=$2 ;;
                    --cf-api-token-file) cf_api_token_file=$2 ;;
                    --sdk-key-file) sdk_key_file=$2 ;;
                    --sdk-base-url) sdk_base_url=$2 ;;
                    --sdk-model) sdk_model=$2 ;;
                    --sdk-key-var) sdk_key_var=$2 ;;
                    --ts-api-key-file) ts_api_key_file=$2 ;;
                    --stt-groq-key-file) stt_key_file=$2 ;;
                esac
                shift ;;
            *) fail 'Unknown option; see --help.' ;;
        esac
        shift
    done
    case "$publish" in funnel|cloudflare|quick|none) ;; *) fail 'Invalid --publish mode.' ;; esac
    [[ -z $token || -z $token_file ]] || fail 'Use either --token or --token-file, not both.'
    (( ! update || ! reconfigure )) || fail '--update and --reconfigure-publish cannot be combined.'
    if (( reconfigure && (with_sdk || with_stt) )) || { (( reconfigure )) && [[ -n $token$token_file$users ]]; } || { (( reconfigure && ${#env_pairs[@]} )); }; then
        fail '--reconfigure-publish cannot change bot credentials or runners.'
    fi
    if [[ $publish != cloudflare && ( -n $cf_hostname || -n $cf_api_token_file ) ]]; then
        fail 'Cloudflare options require --publish=cloudflare.'
    fi
    [[ -z $ts_api_key_file || $publish == funnel ]] || fail '--ts-api-key-file requires --publish=funnel.'
    [[ -z $sdk_key_file ]] || (( with_sdk )) || fail '--sdk-key-file requires --with-sdk.'
    [[ -z $sdk_base_url$sdk_model$sdk_key_var ]] || (( with_sdk )) || fail '--sdk-base-url, --sdk-model and --sdk-key-var require --with-sdk.'
    (( ! sdk_login )) || (( with_sdk )) || fail '--sdk-login requires --with-sdk.'
    [[ -z $sdk_key_file$sdk_base_url$sdk_key_var ]] || (( ! sdk_login )) || fail '--sdk-login takes no key file, base URL or key variable.'
    [[ -z $sdk_base_url || $sdk_base_url =~ ^https?://[^[:space:]\"\\]+$ ]] || fail 'Invalid --sdk-base-url (expected http(s)://host/path).'
    [[ -z $sdk_model || $sdk_model =~ ^[A-Za-z0-9._:/-]+$ ]] || fail 'Invalid --sdk-model.'
    [[ -z $sdk_key_var || $sdk_key_var =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail 'Invalid --sdk-key-var.'
    [[ -z $stt_key_file ]] || (( with_stt )) || fail '--stt-groq-key-file requires --with-stt.'
}

check_existing() {
    if [[ ! -e $config/env && -e $HOME/.config/cc-tgbot/env ]]; then  # legacy install: migrate only on --update, never overwrite it
        (( update )) || fail "Existing legacy cc-tgbot install found: $HOME/.config/cc-tgbot/env. Nothing changed. Run with --update to migrate it to Zidos Pager."
    elif [[ -e $config/env || -L $config/env ]]; then
        (( reconfigure || update )) || fail "Existing install found: $config/env. Nothing changed. A new install would write bot/runner configuration, create selected venvs, copy and enable user services, and configure publishing. Use --reconfigure-publish to change publishing only, or --update to update it."
        [[ -f $config/env && ! -L $config/env ]] || fail 'Existing env must be a regular file.'
    elif (( reconfigure )); then
        fail '--reconfigure-publish requires an existing bot env file.'
    fi
}

check_platform() {
    command -v python3 >/dev/null || fail 'Python 3.10 or newer is required.'
    python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || fail 'Python 3.10 or newer is required.'
    python_bin=$(command -v python3)
    # env treats even an absolute executable path containing '=' as an assignment.
    [[ $repo != *=* && $python_bin != *=* ]] || fail "Repository and Python executable paths must not contain '='; move the checkout or choose another Python path."
    command -v systemctl >/dev/null || fail 'Linux with systemd user services is required.'
    # Non-interactive SSH has no user-session variables; point systemctl --user at the running manager.
    if [[ -z ${XDG_RUNTIME_DIR:-} && -d /run/user/$(id -u) ]]; then
        export XDG_RUNTIME_DIR=/run/user/$(id -u)
        [[ -S $XDG_RUNTIME_DIR/bus ]] && export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
    fi
    systemctl --user show-environment >/dev/null 2>&1 || fail 'systemctl --user is unavailable. Log into a Linux user session with a running user manager.'
    if (( with_sdk && ! reconfigure && ! dry_run )); then  # chat sessions refuse to run unsandboxed
        command -v bwrap >/dev/null && command -v socat >/dev/null || fail 'Install bubblewrap and socat before --with-sdk (e.g. apt install bubblewrap socat).'
    fi
    # Debian/Ubuntu ship python3 without ensurepip: venv creation would fail half way through the install
    if (( ! reconfigure && ! dry_run )) && { { (( with_sdk )) && [[ ! -d $repo/.venv-sdk ]]; } || { (( with_stt )) && [[ ! -d $repo/.venv-stt ]]; }; }; then
        python3 -c 'import ensurepip' 2>/dev/null || fail 'Python cannot create virtual environments: install python3-venv (sudo apt-get install -y python3-venv) and run the installer again.'
    fi
    # Only publish is allowed on reconfiguration: never prompt for or change linger.
    if (( ! reconfigure )); then
        command -v loginctl >/dev/null || fail 'loginctl is required to check linger.'
        linger=$(loginctl show-user "$login_user" --property=Linger --value 2>/dev/null || true)
        if [[ $linger != yes ]]; then
            if (( non_interactive && ! dry_run )) && command -v sudo >/dev/null && sudo -n loginctl enable-linger "$login_user" >/dev/null 2>&1; then
                linger=yes
            else
                printf 'To keep services running after logout, enable linger:\n'
                command_line loginctl enable-linger "$login_user"
            fi
        fi
    fi
}

check_publish_config() {
    if (( reconfigure )); then
        if [[ $publish != cloudflare && -e $unit_dir/zidos-pager-tunnel.service ]]; then
            fail 'A manual named Cloudflare tunnel unit exists. Disable it and remove the unit before switching publishers.'
        fi
        # Parse as data, never source a file holding credentials or shell-like text.
        python3 - "$config/env" "$tunnel_file" <<'PY' || fail 'Resolve the conflicting setting in the bot env and restart the bot before reconfiguring publishing.'
import sys
values = {}
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if line.startswith("export "):
        line = line[7:].lstrip()
    if line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    else:
        value = value.split(" #", 1)[0].rstrip()
    values[key.strip()] = value
for key, default in (("CC_WEBAPP_URL", ""), ("CC_TUNNEL_FILE", sys.argv[2]), ("CC_WEBAPP_PORT", "18795")):
    if values.get(key, default) != default:
        print("Publishing conflict: " + key, file=sys.stderr)
        sys.exit(1)
PY
    fi
    if [[ -f $config/publish-mode ]]; then
        IFS= read -r previous < "$config/publish-mode" || true
        case "$previous" in funnel|quick|none) ;; *) fail 'Unknown previous publish-mode; inspect it before continuing.' ;; esac
    elif (( reconfigure )) && [[ -e $unit_dir/zidos-pager-quick-tunnel.service ]]; then
        previous=quick
    elif (( reconfigure )) && [[ -e $tunnel_file || -e $unit_dir/zidos-pager-tunnel.service ]]; then
        fail 'Unmanaged publisher found. Stop it and remove its stale tunnel-url/unit before retrying; no changes made.'
    fi
    if [[ $publish == funnel || ( $previous == funnel && $publish != cloudflare ) ]]; then
        if ! command -v tailscale >/dev/null; then
            printf 'Install Tailscale using the official command, then rerun setup:\ncurl -fsSL https://tailscale.com/install.sh | sh\n' >&2
            exit 1
        fi
    fi
    if [[ $publish == funnel ]]; then
        command -v timeout >/dev/null || fail 'timeout (GNU coreutils) is required for Tailscale readiness polling.'
        ts_status=$(tailscale status --json 2>/dev/null) || fail 'Tailscale is not ready. Run sudo tailscale up, then rerun setup.'
        if ! ts_running <<< "$ts_status" 2>/dev/null; then
            if (( non_interactive || dry_run )); then fail 'Tailscale is not logged in. Run sudo tailscale up, then rerun setup.'; fi
            ts_login_flow
        fi
        current_funnel=$(tailscale funnel status --json 2>/dev/null) || fail 'Cannot inspect existing Funnel configuration; run tailscale funnel status before retrying.'
        python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        sys.exit(1)
    tcp = data.get("TCP", {}).get("443", {})
    if tcp and tcp != {"HTTPS": True}:
        sys.exit(1)
    for host, web in data.get("Web", {}).items():
        if host.endswith(":443"):
            for path, handler in web.get("Handlers", {}).items():
                if path != "/" or handler.get("Proxy") not in ("http://127.0.0.1:18795", "http://localhost:18795"):
                    sys.exit(1)
except (ValueError, TypeError, AttributeError):
    sys.exit(1)
' <<< "$current_funnel" || fail 'Existing Serve/Funnel configuration uses HTTPS port 443 for another service. Resolve it manually; nothing has been changed.'
    elif [[ $publish == quick ]]; then
        printf 'WARNING: the quick tunnel address changes on every restart. An installed PWA and push notifications will break; reinstall/re-subscribe after an address change.\n'
        command -v cloudflared >/dev/null || fail 'Install cloudflared first, then rerun setup with --publish=quick.'
    elif [[ $publish == cloudflare ]]; then
        if [[ -z $cf_hostname ]]; then
            if (( dry_run )); then cf_hostname=app.example.com
            elif (( non_interactive )); then fail '--publish=cloudflare requires --cf-hostname.'
            else read -r -p 'Hostname in your Cloudflare domain: ' cf_hostname || fail 'No hostname supplied.'; fi
        fi
        python3 - "$cf_hostname" <<'PY' || fail 'Use a DNS hostname, without a scheme, port or path.'
import re, sys
host = sys.argv[1]
sys.exit(not (len(host) <= 253 and "." in host and all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in host.split("."))))
PY
    fi
}

# Tailscale state probes read `tailscale status --json` on stdin.
ts_running() {
    python3 -c 'import json,sys; sys.exit(json.load(sys.stdin).get("BackendState") != "Running")'
}
ts_funnel_ready() {
    python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    me = d.get("Self") or {}
    caps = me.get("CapMap") or {}
    legacy_caps = me.get("Capabilities") or []
    certs = d.get("CertDomains") or me.get("CertDomains") or []
    allowed = any(cap in caps or cap in legacy_caps for cap in ("https://tailscale.com/cap/funnel", "funnel"))
    sys.exit(0 if allowed and certs else 1)
except (ValueError, TypeError, AttributeError):
    sys.exit(1)
'
}

open_link() {
    if [[ $(uname -s 2>/dev/null) == Darwin ]] && command -v open >/dev/null; then
        open "$1" >/dev/null 2>&1 || true
    elif [[ -n ${DISPLAY:-}${WAYLAND_DISPLAY:-} ]] && command -v xdg-open >/dev/null; then
        xdg-open "$1" >/dev/null 2>&1 || true
    fi
}

show_link() {
    printf '\n==============================================================\n\n    %s\n\n==============================================================\n' "$1"
    open_link "$1" </dev/null >/dev/null 2>&1 &
    if command -v qrencode >/dev/null; then
        printf 'Scan to open on your phone:\n'
        qrencode -t ANSIUTF8 "$1" || true
    else
        printf 'Open this link in a browser (on your phone too; install qrencode to get a QR code here).\n'
    fi
}

# await_ts PROBE [OUTFILE LINK-PATTERN PID]: poll tailscale status until PROBE accepts it. With OUTFILE
# the first link matching the pattern is shown as soon as it appears. PID is the login timeout
# supervisor. Returns 1 on timeout, 2 if PID failed.
await_ts() {
    local probe=$1 out=${2:-} pat=${3:-} pid=${4:-}
    local limit=${CC_SETUP_FUNNEL_ENABLE_WAIT:-600} step=${CC_SETUP_POLL_STEP:-3}
    local deadline=$(( SECONDS + limit )) remaining delay shown=0 s link rc ret=1 interrupted=0
    local status_file status_pid='' sleep_pid='' old_int
    status_file=$(mktemp)
    old_int=$(trap -p INT)
    trap 'interrupted=1' INT
    printf 'Waiting for it (up to %s s, Ctrl+C to stop)...\n' "$limit"
    while (( SECONDS < deadline && ! interrupted )); do
        if [[ -n $out ]] && (( ! shown )); then
            link=$(grep -o "$pat" "$out" 2>/dev/null | head -n 1 || true)
            if [[ -n $link ]]; then show_link "$link"; shown=1; fi
        fi
        remaining=$(( deadline - SECONDS ))
        (( remaining > 0 && ! interrupted )) || break
        # An asynchronous wait lets SIGINT interrupt a stuck daemon immediately.
        # KILL bounds even a daemon client that ignores TERM, without extra grace time.
        timeout --signal=KILL "$remaining" tailscale status --json >"$status_file" 2>/dev/null &
        status_pid=$!
        wait "$status_pid" 2>/dev/null && rc=0 || rc=$?
        (( ! interrupted )) || break
        status_pid=''
        (( SECONDS < deadline )) || break
        s=$(cat "$status_file")
        if (( rc == 0 )) && "$probe" <<< "$s" 2>/dev/null; then
            (( ! interrupted && SECONDS < deadline )) || break
            ts_status=$s; ret=0; break
        fi
        if [[ -n $pid ]] && ! kill -0 "$pid" 2>/dev/null; then
            wait "$pid" && rc=0 || rc=$?
            pid=
            if (( rc != 0 )); then ret=2; break; fi
        fi
        remaining=$(( deadline - SECONDS ))
        (( remaining > 0 && ! interrupted )) || break
        delay=$step
        (( delay <= remaining )) || delay=$remaining
        sleep "$delay" &
        sleep_pid=$!
        wait "$sleep_pid" 2>/dev/null || true
        (( ! interrupted )) || break
        sleep_pid=''
    done
    # timeout owns a process group; stop its client too on interruption.
    if [[ -n $status_pid ]]; then
        kill -KILL -- "-$status_pid" 2>/dev/null || kill -KILL "$status_pid" 2>/dev/null || true
        wait "$status_pid" 2>/dev/null || true
    fi
    if [[ -n $sleep_pid ]]; then
        kill -KILL "$sleep_pid" 2>/dev/null || true
        wait "$sleep_pid" 2>/dev/null || true
    fi
    if [[ -n $pid ]]; then
        # Let timeout/sudo forward TERM and reap the login client; timeout escalates
        # after one second if it refuses to stop.
        kill -TERM "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
    fi
    rm -f "$status_file"
    trap - INT
    [[ -z $old_int ]] || eval "$old_int"
    if (( interrupted )); then
        [[ -z $out ]] || rm -f "$out"
        printf '\nInterrupted. %s\n' "$abort_hint" >&2
        exit 130
    fi
    return "$ret"
}

ts_login_flow() {
    local answer out pid sudo_cmd=() rc
    printf 'Tailscale is not logged in. Run "sudo tailscale up" now? [y/N] '
    read -r answer || answer=n
    case "$answer" in y|Y|yes|YES) ;; *) fail 'Tailscale is not logged in. Run sudo tailscale up, then rerun setup.' ;; esac
    if (( EUID != 0 )); then sudo_cmd=(sudo); sudo -v || fail 'sudo is required for tailscale up.'; fi
    out=$(mktemp)
    command_line "${sudo_cmd[@]}" tailscale up
    timeout --kill-after=1 "${CC_SETUP_FUNNEL_ENABLE_WAIT:-600}" "${sudo_cmd[@]}" tailscale up >"$out" 2>&1 &
    pid=$!
    abort_hint='Log in with sudo tailscale up, then rerun setup.'
    await_ts ts_running "$out" 'https://login\.tailscale\.com/[^[:space:]]*' "$pid" && rc=0 || rc=$?
    if (( rc != 0 )); then
        [[ -s $out ]] && cat "$out" >&2
        rm -f "$out"
        fail 'Tailscale login did not complete. Run sudo tailscale up, then rerun setup.'
    fi
    rm -f "$out"
    printf 'Tailscale login complete.\n'
}

# Optional automation through the official API (https://tailscale.com/api): policy file nodeAttrs
# "funnel" and the HTTPS certificates setting. The key is read by python only, never printed or put in argv.
ts_api_enable() {
    python3 - "$ts_api_key_file" "${CC_SETUP_TS_API:-https://api.tailscale.com}" <<'PY'
import json, sys, urllib.error, urllib.request

key_file, base = sys.argv[1], sys.argv[2].rstrip("/")
ELEM = '{"target": ["autogroup:member"], "attr": ["funnel"]}'


def read_key(path):
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" in line:
            line = line.split("=", 1)[1].strip()
        if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
            line = line[1:-1]
        return line
    return ""


def scan(t):
    """Per-char kind: c code, q opening quote, s string rest, m comment; plus bracket depth."""
    kinds, depth, d, i, n = [], [], 0, 0, len(t)
    while i < n:
        c = t[i]
        if c == '"':
            j = i + 1
            while j < n and t[j] != '"':
                j += 2 if t[j] == "\\" else 1
            j = min(j, n - 1)
            for k in range(i, j + 1):
                kinds.append("q" if k == i else "s"); depth.append(d)
            i = j + 1
        elif t.startswith("//", i) or t.startswith("/*", i):
            j = t.find("\n", i) if t[i + 1] == "/" else t.find("*/", i + 2)
            j = n if j < 0 else (j if t[i + 1] == "/" else j + 2)
            for k in range(i, j):
                kinds.append("m"); depth.append(d)
            i = j
        else:
            if c in "}]":
                d -= 1
            kinds.append("c"); depth.append(d)
            if c in "{[":
                d += 1
            i += 1
    return kinds, depth


def to_json(t, kinds):
    out, n = [], len(t)
    for i, c in enumerate(t):
        if kinds[i] == "m":
            out.append("\n" if c == "\n" else " ")
        elif kinds[i] == "c" and c == ",":
            j = i + 1
            while j < n and (kinds[j] == "m" or t[j].isspace()):
                j += 1
            out.append(" " if j < n and kinds[j] == "c" and t[j] in "}]" else c)
        else:
            out.append(c)
    return json.loads("".join(out))


def has_funnel(doc):
    for e in doc.get("nodeAttrs") or []:
        attrs = [str(a) for a in e.get("attr") or []]
        if any(a == "funnel" or a.endswith("/cap/funnel") for a in attrs) and {"autogroup:member", "*"} & set(e.get("target") or []):
            return True
    return False


def add_funnel(t):
    kinds, depth = scan(t)
    m = None
    pos = t.find('"nodeAttrs"')
    while pos >= 0:
        if kinds[pos] == "q" and depth[pos] == 1:
            m = pos
            break
        pos = t.find('"nodeAttrs"', pos + 1)
    if m is not None:
        j = m + len('"nodeAttrs"')
        while j < len(t) and not (kinds[j] == "c" and t[j] == "["):
            j += 1
        if j >= len(t):
            raise ValueError("nodeAttrs is not an array")
        return t[: j + 1] + "\n\t" + ELEM + "," + t[j + 1:]
    j = next(i for i, c in enumerate(t) if kinds[i] == "c" and c == "{")
    return t[: j + 1] + '\n\t"nodeAttrs": [' + ELEM + "],"  + t[j + 1:]


key = read_key(key_file)
if not key:
    sys.exit("API key file is empty.")


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(base + path, data=body, method=method)
    req.add_header("Authorization", "Bearer " + key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as e:
        sys.exit("Tailscale API is unreachable: %s" % type(e).__name__)


status, headers, text = call("GET", "/api/v2/tailnet/-/acl", headers={"Accept": "application/hujson"})
if status != 200:
    sys.exit("Policy file read failed (HTTP %d); the key needs the policy file scope." % status)
etag = headers.get("ETag")
kinds, _ = scan(text)
if has_funnel(to_json(text, kinds)):
    print("Policy already allows Funnel for autogroup:member.")
else:
    new = add_funnel(text)
    if not has_funnel(to_json(new, scan(new)[0])):
        sys.exit("Could not edit the policy safely; add nodeAttrs by hand.")
    hdr = {"Content-Type": "application/hujson", "Accept": "application/json"}
    if etag:
        hdr["If-Match"] = etag
    status, _, resp = call("POST", "/api/v2/tailnet/-/acl", new.encode("utf-8"), hdr)
    if status != 200:
        sys.exit("Policy update rejected (HTTP %d): %s" % (status, resp[:300].replace("\n", " ")))
    print("Policy updated: nodeAttrs funnel added for autogroup:member.")

status, _, resp = call("GET", "/api/v2/tailnet/-/settings", headers={"Accept": "application/json"})
try:
    https_on = status == 200 and json.loads(resp).get("httpsEnabled") is True
except ValueError:
    https_on = False
if https_on:
    print("HTTPS certificates already enabled.")
else:
    status, _, resp = call("PATCH", "/api/v2/tailnet/-/settings", json.dumps({"httpsEnabled": True}).encode(),
                           {"Content-Type": "application/json"})
    if status == 200:
        print("HTTPS certificates enabled.")
    else:
        print("HTTPS certificates could not be enabled through the API (HTTP %d); use the link below." % status)
PY
}

key_file_check() {
    (( dry_run )) && return 0
    python3 - "$1" <<'PY' || fail 'Key file must be an absolute path to an existing, readable, user-owned regular file with mode 600. Put NAME=value in that file; do not pass the key itself.'
import os, stat, sys
path = sys.argv[1]
try:
    info = os.lstat(path)
    ok = os.path.isabs(path) and stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid() and os.access(path, os.R_OK)
except OSError:
    ok = False
sys.exit(not ok)
PY
}

collect_config() {
    (( reconfigure )) && return 0
    if [[ -n $token_file ]]; then
        key_file_check "$token_file"
        if (( ! dry_run )); then
            token=$(python3 - "$token_file" <<'PY'
import sys
lines = [l.strip() for l in open(sys.argv[1], encoding="utf-8").read().splitlines() if l.strip() and not l.lstrip().startswith("#")]
keyed = [l[len("TG_BOT_TOKEN="):] for l in lines if l.startswith("TG_BOT_TOKEN=")]
print(keyed[-1] if keyed else lines[0] if len(lines) == 1 else "")
PY
) || fail 'Cannot read --token-file.'
        fi
    fi
    if (( dry_run )); then
        token=${token:-REDACTED}
        users=${users:-123456789}
    else
        if [[ -z $token ]]; then
            (( ! non_interactive )) || fail '--non-interactive requires --token or --token-file.'
            read -r -s -p 'Telegram bot token from @BotFather: ' token || fail 'No token supplied.'
            printf '\n'
        fi
        if [[ -z $users ]]; then
            (( ! non_interactive )) || fail '--non-interactive requires --users.'
            read -r -p 'Allowed Telegram user IDs (comma-separated): ' users || fail 'No user IDs supplied.'
        fi
    fi
    [[ $token =~ ^[A-Za-z0-9:_-]+$ ]] || fail 'Invalid token format (expected a single token, no spaces or newlines).'
    users=${users// /}
    [[ $users =~ ^[0-9]+(,[0-9]+)*$ ]] || fail 'Use comma-separated numeric Telegram user IDs.'
    if (( with_sdk && sdk_login )); then
        (( dry_run )) || PATH="$HOME/.local/bin:$PATH" claude auth status </dev/null 2>/dev/null | grep -q '"loggedIn": *true' \
            || fail 'Claude Code is not signed in on this machine. Run claude auth login, then run the installer again.'
    elif (( with_sdk )); then
        printf 'SDK: put %s=value in a mode-600 file; only its path is stored in sdk.env.\n' "${sdk_key_var:-ANTHROPIC_API_KEY}"
        sdk_key_file=${sdk_key_file:-$config/anthropic-api.env}
        if (( ! non_interactive && ! dry_run )); then
            read -r -p "API key file path [$sdk_key_file]: " answer || fail 'No key file path supplied.'
            sdk_key_file=${answer:-$sdk_key_file}
        fi
        key_file_check "$sdk_key_file"
    fi
    if (( with_stt )); then
        printf 'STT uses local recognition by default; ffmpeg and ffprobe are required. Models download on first use.\n'
        if (( ! dry_run )); then
            command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || fail 'Install ffmpeg (including ffprobe) before --with-stt.'
        fi
        if (( ! non_interactive && ! dry_run )) && [[ -z $stt_key_file ]]; then
            printf 'Optional Groq fallback: put GROQ_API_KEY=value in a mode-600 file. Audio is uploaded when Groq is used.\n'
            read -r -p 'Groq key file path (empty for local only): ' stt_key_file || fail 'No STT selection supplied.'
        fi
        [[ -z $stt_key_file ]] || key_file_check "$stt_key_file"
    fi
    (( update )) && return 0  # units, venvs and runner env files are rewritten/reused by --update
    for name in "${services[@]}"; do
        [[ ! -e $unit_dir/$name.service && ! -L $unit_dir/$name.service ]] || fail 'A selected service unit already exists; inspect the partial/manual installation first.'
    done
    if [[ $publish == quick && ( -e $unit_dir/zidos-pager-quick-tunnel.service || -L $unit_dir/zidos-pager-quick-tunnel.service ) ]]; then
        fail 'A quick tunnel unit already exists; inspect the partial/manual installation first.'
    fi
    for name in sdk stt; do
        if { [[ $name == sdk ]] && (( with_sdk )); } || { [[ $name == stt ]] && (( with_stt )); }; then
            [[ ! -e $repo/.venv-$name && ! -L $repo/.venv-$name && ! -e $config/$name.env && ! -L $config/$name.env ]] || fail 'A selected runner configuration or venv already exists; nothing overwritten.'
        fi
    done
}

# Contents are always passed on stdin, never as process arguments or printed.
write_private() {
    local target=$1
    if (( dry_run )); then
        cat >/dev/null
        printf '+ write configuration (contents redacted) to %q\n' "$target"
        command_line chmod 600 "$target"
        return
    fi
    [[ ! -L $target ]] || fail 'Refusing to write through a symlink.'
    # umask protects new files before the first byte is written; chmod protects existing files.
    if [[ -e $target ]]; then chmod 600 "$target"; fi
    cat > "$target"
    chmod 600 "$target"
}

install_unit() {
    local name=$1
    run mkdir -p "$unit_dir"
    if (( dry_run )); then
        printf '+ render %q with repository path %q and Python %q into %q\n' "$repo/deploy/$name.service" "$repo" "$python_bin" "$unit_dir/$name.service"
        command_line chmod 644 "$unit_dir/$name.service"
        return
    fi
    python3 - "$repo" "$name" "$unit_dir" "$python_bin" <<'PY'
import os, pathlib, sys
repo, name, destination, python = sys.argv[1:]
# Paths are arguments to a fixed /usr/bin/env executable. systemd prohibits quote
# and backslash characters in its executable field even when they are escaped.
def escape(value):
    value = value.replace("%", "%%").replace("$", "$$")
    return "".join("\\x%02x" % ord(c) if c.isspace() or c in "\\\"'" else c for c in value)
source = (pathlib.Path(repo) / "deploy" / (name + ".service")).read_text()
lines = []
for line in source.splitlines():
    if line.startswith("ExecStart="):
        executable, separator, arguments = line[len("ExecStart="):].partition(" ")
        executable = executable.replace("%h/zidos-pager", escape(repo))
        arguments = arguments.replace("%h/zidos-pager", escape(repo))
        if name in ("zidos-pager", "zidos-pager-bot@"):
            executable = executable.replace("/usr/bin/python3", escape(python), 1)
        line = "ExecStart=/usr/bin/env " + executable + separator + arguments
    lines.append(line)
    if line == "[Service]" and name == "zidos-pager-quick-tunnel":
        lines.append("UMask=0077")
path = pathlib.Path(destination) / (name + ".service")
if path.is_symlink():
    sys.exit("Refusing to overwrite a symlink unit")
path.write_text("\n".join(lines) + "\n")
os.chmod(path, 0o644)
PY
}

# Venvs made by this run are all removed if one fails: otherwise the next install refuses to overwrite them
new_venvs=()
make_venv() {
    local dir=$repo/.venv-$1 pkg=$2
    if (( update )) && [[ -d $dir ]]; then run "$dir/bin/pip" install -U "$pkg"; return; fi
    new_venvs+=("$dir")
    run python3 -m venv "$dir" && run "$dir/bin/pip" install "$pkg" && return
    rm -rf -- "${new_venvs[@]}"
    fail "Could not install $pkg into $dir (see above). Nothing was kept; run the installer again."
}

install_bot() {
    # venvs first: a network failure in pip then leaves no config behind and a retry is a fresh install
    (( ! with_sdk )) || make_venv sdk claude-agent-sdk
    (( ! with_stt )) || make_venv stt faster-whisper
    run mkdir -p "$config"
    { printf 'TG_BOT_TOKEN=%s\nTG_ALLOWED_USERS=%s\n' "$token" "$users"
      for pair in ${env_pairs[@]+"${env_pairs[@]}"}; do printf '%s\n' "$pair"; done; } | write_private "$config/env"
    if (( with_sdk )); then
        # systemd EnvironmentFile double quotes protect whitespace and literal backslashes/quotes.
        if (( sdk_login )); then printf 'CC_SDK_AUTH=login\nCC_SDK_MODEL=%s\n' "${sdk_model:-sonnet}" | write_private "$config/sdk.env"
        else
        python3 - "$sdk_key_file" "$sdk_base_url" "$sdk_model" "$sdk_key_var" <<'PY' | write_private "$config/sdk.env"
import sys
value = sys.argv[1].replace("\\", "\\\\").replace('"', '\\"')
base, model, var = sys.argv[2:5]
print('CC_SDK_KEY_FILE="' + value + '"\nCC_SDK_KEY_VAR=' + (var or "ANTHROPIC_API_KEY") + "\nCC_SDK_MODEL=" + (model or "sonnet"))
if base:  # a third-party endpoint: no dollar budget (the CLI prices unknown models like Claude)
    print('CC_SDK_BASE_URL="' + base + '"')
else:
    print("CC_SDK_BUDGET=1.00")
PY
        fi
    fi
    if (( with_stt )); then
        python3 - "$stt_key_file" <<'PY' | write_private "$config/stt.env"
import sys
print("CC_STT_ENGINES=" + ("local,groq" if sys.argv[1] else "local"))
if sys.argv[1]:
    value = sys.argv[1].replace("\\", "\\\\").replace('"', '\\"')
    print('CC_STT_GROQ_KEY_FILE="' + value + '"')
PY
    fi
    for name in "${services[@]}"; do install_unit "$name"; done
    # templates of the extra group-assistant bots (`zidos_pager.py bots`): installed, never started here
    install_unit 'zidos-pager-bot@'
    install_unit 'zidos-pager-bot-sdk@'
    if [[ $linger != yes ]] && (( ! non_interactive && ! dry_run )); then
        read -r -p 'Enable linger now? [y/N] ' answer || answer=n
        case "$answer" in y|Y|yes|YES) run loginctl enable-linger "$login_user" ;; esac
    fi
    (( ! update )) || retire_legacy_units
    run systemctl --user daemon-reload
    if (( update )); then
        run systemctl --user enable "${services[@]}"
        run systemctl --user restart "${services[@]}"
    else
        run systemctl --user enable --now "${services[@]}"
    fi
}

# Funnel is switched off with `tailscale funnel reset`, which clears ALL serve/funnel config of the
# node, so it runs only when every handler proxies to this bot; otherwise the user does it by hand.
funnel_off() {
    if (( dry_run )); then command_line tailscale funnel reset; return; fi
    local status
    status=$(tailscale funnel status --json 2>/dev/null || true)
    if python3 -c 'import json,sys
d = json.load(sys.stdin) or {}
proxies = [h.get("Proxy", "") for site in (d.get("Web") or {}).values() for h in (site.get("Handlers") or {}).values()]
sys.exit(0 if proxies and all(p.rstrip("/").endswith(":18795") for p in proxies) else 1)' <<< "$status" 2>/dev/null; then
        run tailscale funnel reset
    else
        printf 'Funnel/serve on this node has other handlers; switch off only the zidos-pager one by hand (tailscale funnel status).\n' >&2
    fi
}

stop_previous() {
    if [[ $previous == quick ]]; then
        run systemctl --user disable --now zidos-pager-quick-tunnel
    elif [[ $previous == funnel ]]; then
        funnel_off
    fi
    run rm -f "$tunnel_file"
}

funnel_url() {
    python3 -c '
import json, re, sys
try:
    data = json.load(sys.stdin)
except (ValueError, TypeError):
    sys.exit(1)
if not isinstance(data, dict):
    sys.exit(1)
# Current ServeConfig: Web and AllowFunnel keyed by DNS name and HTTPS port.
for host, enabled in data.get("AllowFunnel", {}).items():
    if enabled and host.endswith(":443") and re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net:443", host):
        web = data.get("Web", {}).get(host, {})
        proxy = web.get("Handlers", {}).get("/", {}).get("Proxy", "")
        if proxy in ("http://127.0.0.1:18795", "http://localhost:18795"):
            print("https://" + host[:-4]); sys.exit(0)
sys.exit(1)
'
}

cloudflare_steps() {
    printf 'Cloudflare publishing is MANUAL: these commands are printed only, not executed.\n'
    printf 'You need a domain managed by Cloudflare and cloudflared installed. Use browser login; no API calls are made.\n'
    if [[ -n $cf_api_token_file ]]; then
        printf '%s\n' '--cf-api-token-file is not read or used by this browser-login recipe; keep that file private.'
    fi
    if [[ $previous != none ]]; then
        printf 'Before switching, stop the current publisher:\n'
        if [[ $previous == quick ]]; then command_line systemctl --user disable --now zidos-pager-quick-tunnel
        else command_line tailscale funnel reset; fi
    fi
    cat <<'STEPS'
cloudflared tunnel login
cloudflared tunnel create zidos-pager
# Copy the UUID printed above (the credentials JSON is created by cloudflared):
read -r -p 'Tunnel UUID: ' TUNNEL_UUID
mkdir -p "$HOME/.cloudflared" "$HOME/.config/systemd/user" "$HOME/.local/state/zidos-pager"
umask 077
STEPS
    printf 'cloudflared tunnel route dns zidos-pager %q\n' "$cf_hostname"
    cat <<STEPS
cat > "\$HOME/.cloudflared/zidos-pager.yml" <<YAML
tunnel: \$TUNNEL_UUID
credentials-file: '\$HOME/.cloudflared/\$TUNNEL_UUID.json'
ingress:
  - hostname: $cf_hostname
    service: http://127.0.0.1:18795
  - service: http_status:404
YAML
STEPS
    printf 'cp %q "$HOME/.config/systemd/user/zidos-pager-tunnel.service"\n' "$repo/deploy/zidos-pager-tunnel.service"
    cat <<STEPS
systemctl --user daemon-reload
systemctl --user enable --now zidos-pager-tunnel
# The unit runs: cloudflared tunnel --no-autoupdate --config ~/.cloudflared/zidos-pager.yml run
printf '%s\\n' 'https://$cf_hostname' > "\$HOME/.local/state/zidos-pager/tunnel-url"
chmod 600 "\$HOME/.local/state/zidos-pager/tunnel-url"
# Bot polls this file; no bot restart needed. CC_WEBAPP_URL must be unset.
STEPS
    printf 'PWA URL after completing these steps: https://%s/app/\n' "$cf_hostname"
    pwa_url='not configured automatically (complete the Cloudflare steps above)'
}

# Runs `tailscale funnel --bg`; when Funnel is not enabled the CLI prints a link and waits, so it is
# stopped once a link appears or CC_SETUP_FUNNEL_WAIT seconds pass. Sets funnel_out and funnel_rc.
run_funnel() {
    local tmp pid ticks=0 limit=$(( ${CC_SETUP_FUNNEL_WAIT:-20} * 5 ))
    tmp=$(mktemp)
    tailscale funnel --bg 18795 >"$tmp" 2>&1 &
    pid=$!
    while kill -0 "$pid" 2>/dev/null; do
        if (( ticks >= limit )) || grep -q 'https://login\.tailscale\.com/' "$tmp"; then
            kill "$pid" 2>/dev/null || true
            break
        fi
        sleep 0.2
        ticks=$(( ticks + 1 ))
    done
    wait "$pid" && funnel_rc=0 || funnel_rc=$?
    funnel_out=$(cat "$tmp")
    rm -f "$tmp"
}

publish_app() {
    if [[ $publish == cloudflare ]]; then cloudflare_steps; return; fi
    # Stop before writing: the quick tunnel removes the shared URL file on exit.
    if [[ $previous != none && $previous != "$publish" ]]; then stop_previous; fi
    case "$publish" in
        funnel)
            if (( dry_run )); then
                [[ -z $ts_api_key_file ]] || printf '+ (Tailscale API) add nodeAttrs funnel to the policy and enable HTTPS certificates; key file %q\n' "$ts_api_key_file"
                command_line tailscale funnel --bg 18795
                pwa_url='https://<your-machine>.<your-tailnet>.ts.net'
            else
                if [[ -n $ts_api_key_file ]] && ! ts_funnel_ready <<< "$(tailscale status --json 2>/dev/null || true)"; then
                    printf 'Enabling Funnel through the Tailscale API (key file %s).\n' "$ts_api_key_file"
                    ts_api_enable || printf 'API step failed; falling back to the enablement link.\n' >&2
                fi
                command_line tailscale funnel --bg 18795
                run_funnel
                if (( funnel_rc != 0 )); then
                    printf '%s\n' "$funnel_out" >&2
                    link=$(grep -o 'https://login\.tailscale\.com/[^[:space:]]*' <<< "$funnel_out" | head -n 1 || true)
                    if [[ -n $link ]] && (( ! non_interactive || wait_enable )); then
                        printf '\nFunnel must be enabled for this tailnet (one click: HTTPS certificates and the funnel attribute).\n'
                        show_link "$link"
                        abort_hint='Retry with --reconfigure-publish --publish=funnel.'
                        await_ts ts_funnel_ready || fail 'Funnel was not enabled in time. Retry with --reconfigure-publish --publish=funnel.'
                        printf 'Funnel is enabled; continuing.\n'
                        command_line tailscale funnel --bg 18795
                        run_funnel
                        if (( funnel_rc != 0 )); then
                            printf '%s\n' "$funnel_out" >&2
                            fail 'Funnel still failed to start; see the output above, then retry with --reconfigure-publish --publish=funnel.'
                        fi
                    else
                        [[ -n $link ]] && printf '\nOpen this link to enable Funnel for the tailnet: %s\n' "$link"
                        fail 'Enable Funnel and HTTPS for this tailnet using the admin-console link above, then retry with --reconfigure-publish --publish=funnel (interactively, or with --wait-for-enable). Also check Tailscale operator permissions.'
                    fi
                fi
                status=$(tailscale funnel status --json 2>/dev/null || true)
                pwa_url=$(funnel_url <<< "$status" || true)
                if [[ -z $pwa_url ]]; then
                    pwa_url=$(python3 -c 'import json,re,sys; name=json.load(sys.stdin).get("Self",{}).get("DNSName", "").rstrip("."); sys.exit(1) if not re.fullmatch(r"[A-Za-z0-9.-]+\.ts\.net",name) else print("https://"+name)' <<< "$ts_status") || fail 'Funnel started but its URL could not be determined; inspect tailscale funnel status.'
                fi
            fi
            run mkdir -p "${tunnel_file%/*}"
            printf '%s\n' "$pwa_url" | write_private "$tunnel_file"
            ;;
        quick)
            # Make the shared directory private before the existing tunnel script writes its file.
            run mkdir -p "${tunnel_file%/*}"
            install_unit zidos-pager-quick-tunnel
            run systemctl --user daemon-reload
            run systemctl --user enable --now zidos-pager-quick-tunnel
            pwa_url="pending: the quick tunnel writes $tunnel_file; check /start once ready"
            ;;
        none)
            run rm -f "$tunnel_file"
            pwa_url='none (Telegram only)'
            ;;
    esac
    run mkdir -p "$config"
    printf '%s\n' "$publish" | write_private "$config/publish-mode"
}

summary() {
    if (( dry_run )); then printf '\nDry run complete; no changes made.\n'
    elif (( reconfigure )); then printf '\nPublishing step complete; bot and runner configuration preserved.\n'
    else printf '\nInstalled services: %s\n' "${services[*]}"; fi
    printf 'Publish mode: %s\nPWA URL: %s\n' "$publish" "$pwa_url"
    if [[ $publish == funnel ]]; then
        printf 'The first time Funnel is enabled, its public DNS record can take a few minutes to appear.\n'
    fi
    printf 'Open the bot and send /start for the mini-app button; /pwa creates a PWA sign-in link.\n'
    printf 'Web Push additionally needs cryptography and CC_VAPID_SUB; see README.\n'
    printf '\nUndo commands (review before running):\n'
    if [[ $publish == funnel ]]; then command_line tailscale funnel reset; fi
    local undo_services=("${services[@]}")
    [[ $publish != quick ]] || undo_services+=(zidos-pager-quick-tunnel)
    [[ $publish != cloudflare ]] || undo_services+=(zidos-pager-tunnel)
    command_line systemctl --user disable --now "${undo_services[@]}"
    for name in "${undo_services[@]}"; do command_line rm -f "$unit_dir/$name.service"; done
    command_line systemctl --user daemon-reload
    command_line rm -f "$config/env" "$config/publish-mode" "$tunnel_file"
    if (( with_sdk )); then command_line rm -f "$config/sdk.env"; command_line rm -rf "$repo/.venv-sdk"; fi
    if (( with_stt )); then command_line rm -f "$config/stt.env"; command_line rm -rf "$repo/.venv-stt"; fi
    printf 'Session data and API key files are retained. Cloudflare tunnel deletion is manual (cloudflared tunnel delete zidos-pager); remove its DNS record too.\n'
}

# Legacy cc-tgbot dirs: move to the zidos-pager names and leave a symlink, so the old bot keeps running if setup stops later.
retire_legacy_dirs() {
    local legacy_base legacy_old legacy_new
    for legacy_base in .config .local/share .local/state; do
        legacy_old=$HOME/$legacy_base/cc-tgbot  # legacy dir
        legacy_new=$HOME/$legacy_base/zidos-pager
        if [[ -d $legacy_old && ! -L $legacy_old && ! -e $legacy_new ]]; then run mv "$legacy_old" "$legacy_new"; run ln -s "$legacy_new" "$legacy_old"; fi  # legacy paths keep resolving
    done
}

# Legacy cc-tgbot units: stopped and removed only right before the new units start.
retire_legacy_units() {
    local legacy_unit
    for legacy_unit in "$unit_dir"/cc-tgbot*.service; do  # legacy unit files
        [[ -e $legacy_unit ]] || continue
        run systemctl --user disable --now "${legacy_unit##*/}" || true
        run rm -f "$legacy_unit"
    done
}

main() {
    dry_run=0 non_interactive=0 reconfigure=0 with_sdk=0 with_stt=0 update=0 token_file='' env_pairs=()
    token='' users='' publish=funnel cf_hostname='' cf_api_token_file=''
    sdk_login=0 sdk_key_file='' sdk_base_url='' sdk_model='' sdk_key_var='' stt_key_file='' previous=none linger=yes pwa_url=''
    ts_api_key_file='' wait_enable=0 abort_hint='' funnel_out='' funnel_rc=0 ts_status=''
    parse_args "$@"
    [[ -z $ts_api_key_file ]] || wait_enable=1
    repo=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
    config=$HOME/.config/zidos-pager
    unit_dir=$HOME/.config/systemd/user
    tunnel_file=$HOME/.local/state/zidos-pager/tunnel-url
    login_user=${USER:-$(id -un)}
    services=(zidos-pager)
    (( ! with_sdk )) || services+=(zidos-pager-sdk)
    (( ! with_stt )) || services+=(zidos-pager-stt)
    check_existing
    check_platform
    (( ! update )) || retire_legacy_dirs
    [[ -z $ts_api_key_file ]] || key_file_check "$ts_api_key_file"
    check_publish_config
    collect_config
    # Set only this process's default creation mode, after all read-only preflight checks.
    umask 077
    if (( ! reconfigure )); then install_bot; fi
    publish_app
    summary
}

main "$@"
