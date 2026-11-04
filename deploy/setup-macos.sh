#!/usr/bin/env bash
# macOS installer: LaunchAgents for the bot and the SDK runner. Bash 3.2 compatible (no arrays, mapfile or ${x,,}).
# Installs from this checkout; never downloads or executes an installation script.
set +x
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash deploy/setup-macos.sh [options]
macOS LaunchAgent installation (~/Library/LaunchAgents/dev.zidos.pager.{bot,sdk}.plist).

  --dry-run             Print changes as '+ ' commands; change nothing
  --non-interactive     Never prompt; requires --token-file and --users for a new install
  --token-file PATH     File (absolute, mode 600, yours) with the Telegram token: bare or TG_BOT_TOKEN=value
  --users IDS           Allowed numeric Telegram user IDs, comma-separated
  --env KEY=VALUE       Extra line for the bot env (repeatable); KEY is CC_[A-Z0-9_]+ or TG_API_BASE
  --with-sdk            Create .venv-sdk, install claude-agent-sdk, run the SDK runner as a LaunchAgent
  --sdk-key-file PATH   File containing ANTHROPIC_API_KEY=value, mode 600 (only its path is stored)
  --sdk-key-var NAME    Variable name inside the key file (default ANTHROPIC_API_KEY)
  --sdk-base-url URL    Anthropic-compatible endpoint instead of Anthropic
  --sdk-model NAME      Model name for chat sessions (default sonnet)
  --update              Existing install: reload plists, upgrade the SDK; env is rewritten only with --token-file
  --help                Show this help

Keep secrets out of command lines: put them into mode-600 files and pass the paths.
HELP
}

fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
command_line() { printf '+ '; printf '%q ' "$@"; printf '\n'; }
run() {
    command_line "$@"
    if [ "$dry_run" != 1 ]; then "$@"; fi
}

dry_run=0 non_interactive=0 with_sdk=0 update=0
token_file='' users='' sdk_key_file='' sdk_key_var='' sdk_base_url='' sdk_model=''
env_pairs=''   # newline-separated KEY=VALUE
env_re='^(CC_[A-Z0-9_]+|TG_API_BASE)=[^'$'\n\r'']*$'

parse_args() {
    local opt val
    while [ $# -gt 0 ]; do
        opt=$1
        case "$opt" in
            --*=*) val=${opt#*=}; opt=${opt%%=*}; shift; set -- "$opt" "$val" "$@" ;;
        esac
        case "$1" in
            --help|-h) usage; exit 0 ;;
            --dry-run) dry_run=1 ;;
            --non-interactive) non_interactive=1 ;;
            --with-sdk) with_sdk=1 ;;
            --update) update=1 ;;
            --token-file|--users|--env|--sdk-key-file|--sdk-key-var|--sdk-base-url|--sdk-model)
                [ $# -ge 2 ] && [ -n "$2" ] && [[ $2 != --* ]] || fail 'An option is missing its value; see --help.'
                case "$1" in
                    --token-file) token_file=$2 ;;
                    --users) users=$2 ;;
                    --env)
                        [[ $2 =~ $env_re ]] || fail 'Invalid --env (expected KEY=VALUE, KEY is CC_[A-Z0-9_]+ or TG_API_BASE, no newline).'
                        env_pairs="$env_pairs$2"$'\n' ;;
                    --sdk-key-file) sdk_key_file=$2 ;;
                    --sdk-key-var) sdk_key_var=$2 ;;
                    --sdk-base-url) sdk_base_url=$2 ;;
                    --sdk-model) sdk_model=$2 ;;
                esac
                shift ;;
            *) fail 'Unknown option; see --help.' ;;
        esac
        shift
    done
    [ -z "$sdk_key_file" ] || [ "$with_sdk" = 1 ] || fail '--sdk-key-file requires --with-sdk.'
    [ -z "$sdk_base_url$sdk_model$sdk_key_var" ] || [ "$with_sdk" = 1 ] || fail '--sdk-base-url, --sdk-model and --sdk-key-var require --with-sdk.'
    [ -z "$sdk_base_url" ] || [[ $sdk_base_url =~ ^https?://[^[:space:]\"\\]+$ ]] || fail 'Invalid --sdk-base-url (expected http(s)://host/path).'
    [ -z "$sdk_model" ] || [[ $sdk_model =~ ^[A-Za-z0-9._:/-]+$ ]] || fail 'Invalid --sdk-model.'
    [ -z "$sdk_key_var" ] || [[ $sdk_key_var =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || fail 'Invalid --sdk-key-var.'
    [ -z "$users" ] || [[ $users =~ ^[0-9]+(,[0-9]+)*$ ]] || fail 'Use comma-separated numeric Telegram user IDs.'
}

# First python >= 3.10: Homebrew locations, then PATH (CC_SETUP_PYTHONS overrides the list, for tests).
pick_python() {
    local cand found
    for cand in ${CC_SETUP_PYTHONS:-/opt/homebrew/bin/python3 /usr/local/bin/python3 python3}; do
        found=$(command -v "$cand" 2>/dev/null) || continue
        if "$found" -c 'import sys; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
            python_bin=$found
            return 0
        fi
    done
    fail 'Python 3.10 or newer is required (the system python3 is 3.9). Run: brew install python@3.12'
}

# Contents always arrive on stdin, never in argv.
write_private() {
    local target=$1
    if [ "$dry_run" = 1 ]; then
        cat >/dev/null
        printf '+ write configuration (contents redacted) to %q\n' "$target"
        command_line chmod 600 "$target"
        return
    fi
    [ ! -L "$target" ] || fail 'Refusing to write through a symlink.'
    if [ -e "$target" ]; then chmod 600 "$target"; fi
    (umask 077; cat > "$target")
    chmod 600 "$target"
}

# Read a secret file (absolute, regular, mode 600, user-owned) and print its first value line (KEY= prefix removed).
read_secret_file() {
    "$python_bin" - "$1" "${2:-}" <<'PY' || fail "File must be an absolute path to an existing, readable, user-owned regular file with mode 600: $1"
import os, stat, sys
path, want = sys.argv[1:3]
info = os.lstat(path)
if not (os.path.isabs(path) and stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o600 and info.st_uid == os.getuid()):
    sys.exit(1)
if want:  # key file: only the check, the value stays in the file
    sys.exit(0)
for line in open(path, encoding="utf-8"):
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if line.startswith("export "):
        line = line[7:].lstrip()
    if "=" in line:
        name, value = line.split("=", 1)
        if name.strip() != "TG_BOT_TOKEN":
            continue
        line = value.strip()
    if len(line) >= 2 and line[0] == line[-1] and line[0] in "\"'":
        line = line[1:-1]
    print(line)
    break
PY
}

key_file_check() {
    [ "$dry_run" = 1 ] && return 0
    read_secret_file "$1" check >/dev/null
}

# plist KIND: bot|sdk. The bot plist never gets the sdk.env values and the sdk plist never gets the bot env.
render_plist() {
    local kind=$1 label=$2 target=$3
    if [ "$dry_run" = 1 ]; then
        printf '+ render %s LaunchAgent into %q (mode 600)\n' "$kind" "$target"
        return
    fi
    [ ! -L "$target" ] || fail 'Refusing to overwrite a symlink plist.'
    "$python_bin" - "$kind" "$label" "$target" "$HOME" "$repo" "$config" "$python_bin" <<'PY'
import os, plistlib, sys
kind, label, target, home, repo, config, python = sys.argv[1:8]
path_env = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
if kind == "bot":
    program = [python, "-u", repo + "/zidos_pager.py"]
    env = {"HOME": home, "PATH": path_env, "ZIDOS_PAGER_ENV": config + "/env"}
else:
    program = [repo + "/.venv-sdk/bin/python", "-u", repo + "/sdk_runner.py", "serve"]
    env = {"HOME": home, "PATH": path_env}
    try:
        lines = open(config + "/sdk.env", encoding="utf-8").read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        name, sep, value = line.partition("=")
        value = value.strip()
        if sep and name.startswith("CC_SDK_"):
            if len(value) >= 2 and value[0] == value[-1] == '"':
                value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
            env[name] = value
doc = {
    "Label": label,
    "ProgramArguments": program,
    "EnvironmentVariables": env,
    "RunAtLoad": True,
    "KeepAlive": True,
    "ProcessType": "Background",
    "StandardOutPath": home + "/Library/Logs/zidos-pager/" + kind + ".log",
    "StandardErrorPath": home + "/Library/Logs/zidos-pager/" + kind + ".log",
}
fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "wb") as f:
    plistlib.dump(doc, f)
os.chmod(target, 0o600)
PY
}

# Legacy cc-tgbot dirs: move to the zidos-pager names and leave a symlink, so the old bot keeps running if setup stops later.
retire_legacy_dirs() {
    local legacy_base legacy_old legacy_new
    for legacy_base in .config .local/share .local/state; do
        legacy_old=$HOME/$legacy_base/cc-tgbot  # legacy dir
        legacy_new=$HOME/$legacy_base/zidos-pager
        if [ -d "$legacy_old" ] && [ ! -L "$legacy_old" ] && [ ! -e "$legacy_new" ]; then run mv "$legacy_old" "$legacy_new"; run ln -s "$legacy_new" "$legacy_old"; fi  # legacy paths keep resolving
    done
}

# Legacy cc-tgbot launchd jobs: booted out and removed only right before the new jobs start.
retire_legacy_units() {
    local legacy_plist legacy_label
    for legacy_plist in "$agents"/com.cctgbot.*.plist; do  # legacy plists
        [ -e "$legacy_plist" ] || continue
        legacy_label=$(basename "$legacy_plist" .plist)
        command_line launchctl bootout "gui/$uid/$legacy_label"  # legacy label
        if [ "$dry_run" != 1 ]; then launchctl bootout "gui/$uid/$legacy_label" >/dev/null 2>&1 || true; fi  # legacy label
        run rm -f "$legacy_plist"
    done
}

main() {
    parse_args "$@"
    repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
    config=$HOME/.config/zidos-pager
    agents=$HOME/Library/LaunchAgents
    logs=$HOME/Library/Logs/zidos-pager
    uid=$(id -u)
    if [ "$update" = 1 ]; then retire_legacy_dirs; fi  # legacy install is migrated only on --update
    pick_python
    [[ $repo$python_bin$HOME != *=* ]] || fail "Repository, Python and home paths must not contain '='."

    if [ "$update" != 1 ]; then
        if [ -e "$config/env" ] || [ -L "$config/env" ] || [ -e "$HOME/.config/cc-tgbot/env" ] || ls "$agents"/com.cctgbot.*.plist >/dev/null 2>&1 || [ -e "$agents/dev.zidos.pager.bot.plist" ] || [ -e "$agents/dev.zidos.pager.sdk.plist" ]; then  # legacy cc-tgbot counts as an install
            fail "Existing install found (env or LaunchAgents). Nothing changed. Use --update to reload and upgrade."
        fi
        [ -n "$token_file" ] && [ -n "$users" ] || fail 'A new install requires --token-file and --users.'
    else
        [ -f "$config/env" ] || fail '--update requires an existing install ('"$config"'/env).'
        { [ -z "$users" ] && [ -z "$env_pairs" ]; } || [ -n "$token_file" ] || fail '--update with --users or --env also needs --token-file (the bot env is rewritten as a whole).'
    fi
    [ -z "$token_file" ] || [ -n "$users" ] || fail '--token-file requires --users.'
    if [ "$with_sdk" = 1 ] && [ -z "$sdk_key_file" ] && [ ! -f "$config/sdk.env" ]; then
        fail '--with-sdk requires --sdk-key-file (a mode-600 file with NAME=value).'
    fi
    [ -z "$sdk_key_file" ] || key_file_check "$sdk_key_file"

    token=''
    if [ -n "$token_file" ]; then
        if [ "$dry_run" = 1 ]; then token=REDACTED; else token=$(read_secret_file "$token_file"); fi
        [[ $token =~ ^[A-Za-z0-9:_-]+$ ]] || [ "$dry_run" = 1 ] || fail 'Invalid token format (expected a single token, no spaces or newlines).'
    fi

    run mkdir -p "$config" "$agents" "$logs"
    if [ -n "$token_file" ]; then
        { printf 'TG_BOT_TOKEN=%s\nTG_ALLOWED_USERS=%s\n' "$token" "$users"; printf '%s' "$env_pairs"; } | write_private "$config/env"
    fi
    if [ "$with_sdk" = 1 ]; then
        if [ -d "$repo/.venv-sdk" ] && [ "$update" = 1 ]; then
            run "$repo/.venv-sdk/bin/pip" install -U claude-agent-sdk
        else
            [ -d "$repo/.venv-sdk" ] || run "$python_bin" -m venv "$repo/.venv-sdk"
            run "$repo/.venv-sdk/bin/pip" install claude-agent-sdk
        fi
        if [ -n "$sdk_key_file" ]; then
            "$python_bin" - "$sdk_key_file" "$sdk_base_url" "$sdk_model" "$sdk_key_var" <<'PY' | write_private "$config/sdk.env"
import sys
value = sys.argv[1].replace("\\", "\\\\").replace('"', '\\"')
base, model, var = sys.argv[2:5]
print('CC_SDK_KEY_FILE="' + value + '"\nCC_SDK_KEY_VAR=' + (var or "ANTHROPIC_API_KEY") + "\nCC_SDK_MODEL=" + (model or "sonnet"))
if base:  # third-party endpoint: no dollar budget
    print('CC_SDK_BASE_URL="' + base + '"')
else:
    print("CC_SDK_BUDGET=1.00")
PY
        fi
    fi

    labels='bot'
    [ "$with_sdk" != 1 ] || labels='bot sdk'
    if [ "$update" = 1 ]; then retire_legacy_units; fi  # legacy jobs stop only now
    for kind in $labels; do
        plist=$agents/dev.zidos.pager.$kind.plist
        render_plist "$kind" "dev.zidos.pager.$kind" "$plist"
        command_line launchctl bootout "gui/$uid/dev.zidos.pager.$kind" || true
        if [ "$dry_run" != 1 ]; then
            launchctl bootout "gui/$uid/dev.zidos.pager.$kind" >/dev/null 2>&1 || true
            # bootout returns before the job is gone; bootstrap right away fails with «5: Input/output error»
            n=0
            while launchctl print "gui/$uid/dev.zidos.pager.$kind" >/dev/null 2>&1 && [ $n -lt 50 ]; do sleep 0.2; n=$((n + 1)); done
        fi
        run launchctl bootstrap "gui/$uid" "$plist"
    done

    printf '\nUndo:\n'
    for kind in $labels; do
        printf '  launchctl bootout gui/%s/dev.zidos.pager.%s; rm -f %q\n' "$uid" "$kind" "$agents/dev.zidos.pager.$kind.plist"
    done
    printf '  rm -rf %q   # config and secrets\n' "$config"
    printf 'The Mac must stay awake for the bot to answer: System Settings > Battery > Options (prevent sleep on power adapter) or run caffeinate -s.\n'
    printf 'Logs: %s/{bot,sdk}.log\n' "$logs"
}

main "$@"
