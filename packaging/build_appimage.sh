#!/usr/bin/env bash
# Build the GAMEMAX LCD tray app as an AppImage.
#
#   bash packaging/build_appimage.sh [output_dir]
#
# Needs: uv (for the throwaway build venv) and network on first run.
# Produces dist/GAMEMAX-LCD-x86_64.AppImage with PySide6, the sender script and the icon.
set -euo pipefail

PACKAGING_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$PACKAGING_DIR/.." && pwd)"
OUT_DIR="${1:-$ROOT_DIR/dist}"
BUILD_DIR="${BUILD_DIR:-/tmp/gamemax-appimage}"
VENV="${VENV:-/tmp/gamemax-appimage-venv}"
APPIMAGETOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

mkdir -p "$OUT_DIR" "$BUILD_DIR"
cd "$BUILD_DIR"
cp "$ROOT_DIR/src/gamemax_gui.py" "$ROOT_DIR/src/gamemax_report.py" "$ROOT_DIR/src/icon.png" .

# 1. throwaway venv with PySide6 + PyInstaller
if [ ! -x "$VENV/bin/pyinstaller" ]; then
  uv venv "$VENV"
  uv pip install --python "$VENV/bin/python" pyside6 pyinstaller
fi

# 2. bundle the app (PySide6 + the sender script + the icon)
"$VENV/bin/pyinstaller" --noconfirm --windowed --name gamemax-lcd \
  --add-data "gamemax_report.py:." --add-data "icon.png:." gamemax_gui.py

# 3. assemble the AppDir
APPDIR="$BUILD_DIR/AppDir"
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
cp -r "$BUILD_DIR/dist/gamemax-lcd/." "$APPDIR/usr/bin/"
install -m 755 /dev/stdin "$APPDIR/AppRun" <<'EOF'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
exec "$HERE/usr/bin/gamemax-lcd" "$@"
EOF
cp "$ROOT_DIR/src/icon.png" "$APPDIR/gamemax-lcd.png"
cat > "$APPDIR/gamemax-lcd.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=GAMEMAX IceBurg LCD
GenericName=AI LCD controller
Comment=Status, on/off, monitor, and settings for the GAMEMAX IceBurg AIO LCD
Exec=gamemax-lcd
Icon=gamemax-lcd
Terminal=false
Categories=System;Utility;
EOF

# 4. package (UPDATE_INFO_URL menyematkan info zsync agar appimageupdatetool bisa jalan)
UPDATE_INFO_ARG=""
if [ -n "${UPDATE_INFO_URL:-}" ]; then
  UPDATE_INFO_ARG="-u zsync|${UPDATE_INFO_URL}"
  echo "embedding update information: ${UPDATE_INFO_URL}"
fi
if [ ! -x "$BUILD_DIR/appimagetool" ]; then
  curl -fsSL "$APPIMAGETOOL_URL" -o "$BUILD_DIR/appimagetool"
  chmod +x "$BUILD_DIR/appimagetool"
fi
OUT="$OUT_DIR/GAMEMAX-LCD-x86_64.AppImage"
# shellcheck disable=SC2086
ARCH=x86_64 "$BUILD_DIR/appimagetool" $UPDATE_INFO_ARG "$APPDIR" "$OUT"
chmod +x "$OUT"
echo "built: $OUT"
echo "verify: $OUT --selftest   (or APPIMAGE_EXTRACT_AND_RUN=1 $OUT --selftest)"
