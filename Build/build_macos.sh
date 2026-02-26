#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_NAME="${APP_NAME:-Kajovo}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

"$PYTHON_BIN" -m pip install --upgrade pip
"$PYTHON_BIN" -m pip install -r requirements.txt pyinstaller
"$PYTHON_BIN" Build/generate_icons.py

ICON_ICNS="Build/assets/app_icon.icns"
ICONSET_DIR="Build/assets/app.iconset"
mkdir -p "$ICONSET_DIR"
sips -z 16 16 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_16x16.png" >/dev/null
sips -z 32 32 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null
sips -z 32 32 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_32x32.png" >/dev/null
sips -z 64 64 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null
sips -z 128 128 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_128x128.png" >/dev/null
sips -z 256 256 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null
sips -z 256 256 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_256x256.png" >/dev/null
sips -z 512 512 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null
sips -z 512 512 Build/assets/app_icon.png --out "$ICONSET_DIR/icon_512x512.png" >/dev/null
cp Build/assets/app_icon.png "$ICONSET_DIR/icon_512x512@2x.png"
iconutil -c icns "$ICONSET_DIR" -o "$ICON_ICNS"

"$PYTHON_BIN" -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "$APP_NAME" \
  --icon "$ICON_ICNS" \
  --add-data "resources/app_icon.png:resources" \
  --add-data "resources/montserrat_regular.ttf:resources" \
  --add-data "resources/montserrat_bold.ttf:resources" \
  kajovo/app/main.py

echo "Build complete: dist/$APP_NAME.app"
