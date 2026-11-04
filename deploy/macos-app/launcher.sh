#!/bin/bash
# Double-click entry of "Zidos Pager Setup.app": opens Terminal, copies the bundled Python and code to a stable path, starts the wizard.
set -euo pipefail
if [ -z "${TERM:-}" ] || [ ! -t 0 ]; then
  open -a Terminal "$0"
  exit 0
fi
HERE="$(cd "$(dirname "$0")" && pwd)"
PAYLOAD="$HERE/../Resources/payload"
DEST="$HOME/.local/share/zidos-pager"
if [ ! -f "$DEST/.version" ] || ! cmp -s "$PAYLOAD/VERSION" "$DEST/.version"; then
  echo "Copying Zidos Pager to $DEST ..."
  mkdir -p "$DEST"
  rm -rf "$DEST/python" "$DEST/src" "$DEST/.version"
  cp -R "$PAYLOAD/python" "$DEST/python"
  cp -R "$PAYLOAD/zidos-pager" "$DEST/src"
  xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true
  cp "$PAYLOAD/VERSION" "$DEST/.version"  # written last: an interrupted copy is redone
fi
export PATH="$DEST/python/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
exec "$DEST/python/bin/python3" "$DEST/src/setup.py"
