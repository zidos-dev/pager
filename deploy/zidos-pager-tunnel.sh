#!/usr/bin/env bash
# Quick Cloudflare tunnel for the zidos-pager mini-app: catches the https://*.trycloudflare.com address and writes it to a file.
set -u
set -o pipefail
PORT="${CC_WEBAPP_PORT:-18795}"
OUT="${CC_TUNNEL_FILE:-$HOME/.local/state/zidos-pager/tunnel-url}"
BIN="${CLOUDFLARED_BIN:-$(command -v cloudflared || echo "$HOME/.local/bin/cloudflared")}"
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
"$BIN" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" 2>&1 | while IFS= read -r line; do
  printf '%s\n' "$line" >&2
  if [[ "$line" =~ (https://[A-Za-z0-9-]+\.trycloudflare\.com) ]]; then
    tmp="$OUT.tmp.$$"
    printf '%s\n' "${BASH_REMATCH[1]}" > "$tmp" && mv -f "$tmp" "$OUT"
  fi
done
rc=${PIPESTATUS[0]}
rm -f "$OUT"  # the address is no longer valid
exit "$rc"
