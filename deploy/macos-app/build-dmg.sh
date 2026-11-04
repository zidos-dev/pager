#!/bin/bash
# Build dist/zidos-pager-Setup-macos-ARCH.dmg (run on macOS, from the repo): bash deploy/macos-app/build-dmg.sh arm64|x86_64
set -euo pipefail
ARCH="${1:-}"
PBS_TAG=20261026
PBS_PY=3.12.16
case "$ARCH" in
  arm64)  PBS_ARCH=aarch64; PBS_SHA=fbf4afef53c2a6b72bb741bc06d5671a9dfaf87bd84e5aa73ac9b3d0d589f14e ;;
  x86_64) PBS_ARCH=x86_64;  PBS_SHA=a962e47df522889eaa7ff0d6a56203f1079f68aabb65ffdbe8455f362bc769f3 ;;
  *) echo "usage: $0 arm64|x86_64" >&2; exit 2 ;;
esac
NAME="cpython-${PBS_PY}%2B${PBS_TAG}-${PBS_ARCH}-apple-darwin-install_only.tar.gz"
URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}/${NAME}"

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$REPO/dist"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
mkdir -p "$OUT"

curl -fL --retry 3 -o "$WORK/python.tar.gz" "$URL"
GOT="$(shasum -a 256 "$WORK/python.tar.gz" | cut -d' ' -f1)"
if [ "$GOT" != "$PBS_SHA" ]; then
  echo "python-build-standalone sha256 mismatch: got $GOT, want $PBS_SHA" >&2
  exit 1
fi

APP="$WORK/dmg/Zidos Pager Setup.app"
PAY="$APP/Contents/Resources/payload"
mkdir -p "$APP/Contents/MacOS" "$PAY/zidos-pager"
tar -xzf "$WORK/python.tar.gz" -C "$PAY"   # -> payload/python
git -C "$REPO" archive HEAD | tar -x -C "$PAY/zidos-pager" --exclude='tests' --exclude='.github'
echo "$(git -C "$REPO" rev-parse HEAD)-${PBS_PY}+${PBS_TAG}" > "$PAY/VERSION"
cp "$REPO/deploy/macos-app/launcher.sh" "$APP/Contents/MacOS/launcher"
chmod +x "$APP/Contents/MacOS/launcher"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleExecutable</key><string>launcher</string>
<key>CFBundleIdentifier</key><string>io.github.zidosdev.pager.setup</string>
<key>CFBundleName</key><string>Zidos Pager Setup</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>11.0</string>
</dict></plist>
PLIST
cat > "$WORK/dmg/README.txt" <<'TXT'
Double-click "Zidos Pager Setup". If macOS says it cannot check the developer:
System Settings > Privacy & Security > Open Anyway, or in Terminal:
  xattr -dr com.apple.quarantine "/Volumes/Zidos Pager Setup/Zidos Pager Setup.app"
The app copies its own Python to ~/.local/share/zidos-pager (keep it: the bot runs on it).
TXT

codesign --force --deep -s - "$APP"
rm -f "$OUT/zidos-pager-Setup-macos-$ARCH.dmg"
hdiutil create -volname "Zidos Pager Setup" -srcfolder "$WORK/dmg" -ov -format UDZO "$OUT/zidos-pager-Setup-macos-$ARCH.dmg"
echo "built $OUT/zidos-pager-Setup-macos-$ARCH.dmg"
