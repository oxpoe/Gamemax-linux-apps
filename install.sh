#!/usr/bin/env bash
# GAMEMAX LCD — pemasangan / pelepasan satu perintah.
#
#   curl -fsSL https://raw.githubusercontent.com/oxpoe/Gamemax-linux-apps/main/install.sh | bash
#
# Opsi:
#   --uninstall      lepaskan pemasangan (layanan, aplikasi, config, ikon, entri menu)
#   --no-service     pasang aplikasi saja, jangan aktifkan layanan/autostart
#   --version vX.Y.Z pasang versi rilis tertentu (default: rilis terbaru)
#   --remove-data    saat --uninstall: hapus juga ~/.config/gamemax-report
#
# UdeV: aturan izin device dipasang dengan sudo. Kalau skrip dijalankan lewat pipa
# (curl | bash) dan sudo butuh kata sandi, perintahnya dicetak untuk dijalankan manual.
set -euo pipefail

REPO="oxpoe/Gamemax-linux-apps"
ASSET="GAMEMAX-LCD-x86_64.AppImage"
RAW="https://raw.githubusercontent.com/$REPO/main"

OPT_DIR="$HOME/.local/opt/gamemax-lcd"
APP="$OPT_DIR/GAMEMAX-LCD.AppImage"
DATA_DIR="$HOME/.local/share/gamemax-report"
UNIT="$HOME/.config/systemd/user/gamemax-report.service"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/128x128/apps"
RULE=/etc/udev/rules.d/70-gamemax.rules
SERVICE="gamemax-report.service"

UNINSTALL=0 WITH_SERVICE=1 REMOVE_DATA=0 VERSION="latest"

say() { printf '%s\n' "$*"; }
die() { printf 'galat: %s\n' "$*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --uninstall)    UNINSTALL=1 ;;
    --no-service)   WITH_SERVICE=0 ;;
    --remove-data)  REMOVE_DATA=1 ;;
    --version)      shift; VERSION="${1:?--version butuh nilai seperti v1.3.0}" ;;
    -h|--help)      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)              die "opsi tidak dikenal: $1" ;;
  esac
  shift
done

need() { command -v "$1" >/dev/null 2>&1 || die "perintah '$1' tidak tersedia"; }

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  say "== melepaskan GAMEMAX LCD =="
  command -v systemctl >/dev/null 2>&1 && systemctl --user disable --now "$SERVICE" 2>/dev/null || true
  rm -f "$UNIT"
  command -v systemctl >/dev/null 2>&1 && { systemctl --user daemon-reload 2>/dev/null || true; \
    systemctl --user reset-failed "$SERVICE" 2>/dev/null || true; }
  rm -rf "$OPT_DIR" "$DATA_DIR"
  rm -f "$DESKTOP_DIR/gamemax-lcd.desktop" "$ICON_DIR/gamemax-lcd.png"
  [ "$REMOVE_DATA" = 1 ] && rm -rf "$HOME/.config/gamemax-report"
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  if [ -f "$RULE" ]; then
    if sudo -n rm -f "$RULE" 2>/dev/null; then
      sudo -n udevadm control --reload 2>/dev/null || true
      say "  aturan udev dihapus"
    else
      say "  aturan udev masih ada (butuh sudo): sudo rm -f $RULE && sudo udevadm control --reload"
    fi
  fi
  say "selesai. Aplikasi, layanan, dan berkas pendukung sudah dilepas."
  say "catatan: konfigurasi ~/.config/gamemax-report hanya dihapus dengan --remove-data."
  exit 0
fi

# ---------------------------------------------------------------- install
need curl
need sha256sum
command -v systemctl >/dev/null 2>&1 || die "systemd (systemctl) dibutuhkan untuk layanan"

if [ "$VERSION" = "latest" ]; then
  BASE="https://github.com/$REPO/releases/latest/download"
else
  BASE="https://github.com/$REPO/releases/download/$VERSION"
fi

say "== GAMEMAX LCD: pemasangan ($VERSION) =="
mkdir -p "$OPT_DIR" "$DATA_DIR" "$DESKTOP_DIR" "$ICON_DIR" "$HOME/.config/systemd/user"

say "1/6 mengunduh aplikasi …"
curl -fsSL -o "$APP.part" "$BASE/$ASSET"
curl -fsSL -o "$APP.sha256" "$BASE/$ASSET.sha256"

say "2/6 memverifikasi checksum …"
EXPECTED="$(awk '{print $1}' "$APP.sha256")"
ACTUAL="$(sha256sum "$APP.part" | awk '{print $1}')"
[ "$EXPECTED" = "$ACTUAL" ] || { rm -f "$APP.part"; die "checksum tidak cocok — unduhan dibatalkan"; }
say "   ok: ${ACTUAL:0:16}…"

mv -f "$APP.part" "$APP"
chmod +x "$APP"
rm -f "$APP.sha256"

say "3/6 mengambil sender + ikon dari bundel …"
TMP="$(mktemp -d)"
( cd "$TMP" && "$APP" --appimage-extract >/dev/null 2>&1 ) || true
INTERNAL="$TMP/squashfs-root/usr/bin/_internal"
if [ -f "$INTERNAL/gamemax_report.py" ]; then
  cp "$INTERNAL/gamemax_report.py" "$DATA_DIR/gamemax_report.py"
else
  say "   ! sender tidak ditemukan di bundel — unduh dari repo"
  curl -fsSL -o "$DATA_DIR/gamemax_report.py" "$RAW/src/gamemax_report.py"
fi
[ -f "$INTERNAL/icon.png" ] && cp "$INTERNAL/icon.png" "$ICON_DIR/gamemax-lcd.png" \
  || curl -fsSL -o "$ICON_DIR/gamemax-lcd.png" "$RAW/src/icon.png"
rm -rf "$TMP"

say "4/6 memasang layanan systemd (autostart + plug and play) …"
cat > "$UNIT" <<EOF
[Unit]
Description=IceBurg LCD — HID sensor display bridge (Linux)
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $DATA_DIR/gamemax_report.py --wait --interval 0.5 --sample 0.5
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
if [ "$WITH_SERVICE" = 1 ]; then
  systemctl --user enable --now "$SERVICE"
  sleep 2
  say "   layanan: $(systemctl --user is-active "$SERVICE") / $(systemctl --user is-enabled "$SERVICE")"
else
  say "   dilewati (--no-service)"
fi

say "5/6 memasang entri menu + ikon …"
cat > "$DESKTOP_DIR/gamemax-lcd.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=IceBurg LCD
GenericName=Kontrol LCD AIO
Comment=Status, nyala/mati, pemantauan, dan pengaturan LCD AIO IceBurg Digital
Exec="$APP"
Icon=gamemax-lcd
Terminal=false
Categories=System;Utility;
EOF
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && \
  gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

say "6/6 aturan udev (izin device) …"
RULE_TMP="$(mktemp)"
curl -fsSL -o "$RULE_TMP" "$RAW/packaging/70-gamemax.rules"
if [ -f "$RULE" ] && cmp -s "$RULE" "$RULE_TMP"; then
  say "   sudah terpasang"
elif sudo -n install -m 644 "$RULE_TMP" "$RULE" 2>/dev/null; then
  sudo -n udevadm control --reload 2>/dev/null || true
  sudo -n udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
  say "   terpasang"
elif [ -t 0 ]; then
  sudo install -m 644 "$RULE_TMP" "$RULE" && sudo udevadm control --reload && \
    sudo udevadm trigger --subsystem-match=hidraw && say "   terpasang"
else
  say "   ! butuh sudo — jalankan sekali:"
  say "     sudo install -m 644 $RULE_TMP $RULE && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw"
fi
rm -f "$RULE_TMP"

say
say "== selesai =="
say "  aplikasi : $APP"
say "  sender   : $DATA_DIR/gamemax_report.py"
say "  menu     : GAMEMAX IceBurg LCD"
say "  layanan  : systemctl --user status $SERVICE"
say "  log      : journalctl --user -u $SERVICE -f"
say
say "buka aplikasinya untuk mengatur slot kipas/sumber suhu, atau:"
say "  $APP --window"
