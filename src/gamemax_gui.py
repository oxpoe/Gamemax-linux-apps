#!/usr/bin/env python3
"""IceBurg LCD — GUI tray: status, nyala/mati, pemantauan beranimasi, pengaturan.

Streaming tetap dijalankan oleh layanan systemd (gamemax-report.service); GUI ini
mengendalikannya dan memakai gamemax_report.py sebagai pengirim.

    gamemax_gui.py              # jendela + tray (default)
    gamemax_gui.py --tray-only  # tanpa jendela
    gamemax_gui.py --selftest   # uji muat tanpa tampilan (untuk build AppImage)
"""
import glob
import hashlib
import json
import math
import os
import re
import tempfile
import urllib.request
import shutil
import subprocess
import sys
import threading
import time
import traceback

from PySide6.QtCore import QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (QAction, QColor, QConicalGradient, QDesktopServices, QFont,
                           QIcon, QPainter, QPen, QPixmap)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSystemTrayIcon,
    QVBoxLayout, QWidget,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket

APP_VERSION = "1.3.3"
APP_NAME = "IceBurg LCD"
REPO = "oxpoe/Gamemax-linux-apps"
RELEASES_API = f"https://gitea.oxpoe.qzz.io/api/v1/repos/{REPO}/releases/latest"
UPDATE_ASSET = "GAMEMAX-LCD-x86_64.AppImage"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gamemax_report as gr  # noqa: E402

DONATE_URL = "https://donasi.oxpoe.qzz.io/"
REPO_URL = "https://gitea.oxpoe.qzz.io/oxpoe/Gamemax-linux-apps"

SERVICE = "gamemax-report.service"
CONFIG_PATH = gr.CONFIG_PATH
INSTALL_DIR = os.path.expanduser("~/.local/share/gamemax-report")
UNIT_PATH = os.path.expanduser(f"~/.config/systemd/user/{SERVICE}")
RULE_PATH = "/etc/udev/rules.d/70-gamemax.rules"
AUTOSTART_DIR = os.path.expanduser("~/.config/autostart")
AUTOSTART_FILE = os.path.join(AUTOSTART_DIR, "iceburg-lcd.desktop")

UNIT_TEMPLATE = """[Unit]
Description=IceBurg LCD — HID sensor display bridge (Linux)
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {script} --wait --interval {interval} --sample {sample}
Restart=always
RestartSec=3

[Install]
WantedBy=default.target
"""

RULE_TEMPLATE = ('SUBSYSTEM=="hidraw", ATTRS{idVendor}=="5131", ATTRS{idProduct}=="2007", '
                 'TAG+="uaccess"\n')

APP_QSS = """
QWidget { background: #14121a; color: #ece6ff; font-size: 13px; }
QLabel#hint { color: #a99fc4; }
QGroupBox { border: 1px solid #2b2740; border-radius: 10px; margin-top: 14px;
            padding: 12px 10px 8px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: #a99fc4; }
QPushButton { background: #242033; border: 1px solid #34304a; border-radius: 8px;
              padding: 7px 13px; }
QPushButton:hover { background: #2c2740; }
QPushButton:pressed { background: #201c2e; }
QPushButton:disabled { color: #6c6486; border-color: #2a2740; }
QPushButton#primary { background: #def750; color: #14121a; border: none; font-weight: 700; }
QPushButton#primary:hover { background: #e9ff6a; }
QPushButton#primary:disabled { background: #4a5230; color: #2a2e20; }
QComboBox, QDoubleSpinBox { background: #1c1926; border: 1px solid #34304a;
                            border-radius: 8px; padding: 6px 8px; }
QComboBox QAbstractItemView { background: #1c1926; border: 1px solid #2b2740;
                              selection-background-color: #34304a; }
QPlainTextEdit { background: #1c1926; border: 1px solid #2b2740; border-radius: 10px;
                 padding: 6px; }
QProgressBar { background: #1c1926; border: 1px solid #2b2740; border-radius: 6px; }
QProgressBar::chunk { background: #def750; border-radius: 5px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 15px; height: 15px; border-radius: 4px;
                       border: 1px solid #6c6486; background: #1c1926; }
QCheckBox::indicator:checked { background: #def750; border-color: #def750; }
QMessageBox { background: #1b1826; }
"""
CRASH_LOG = os.path.join(os.path.dirname(CONFIG_PATH), "gui-crash.log")
ACCENT = QColor(222, 247, 80)      # warna aksen GAMEMAX dari appConfig.ini
DIM = QColor(120, 128, 120)


def crash_log(text):
    """Catat galat ke berkas supaya bisa diperiksa setelah aplikasi berhenti."""
    try:
        os.makedirs(os.path.dirname(CRASH_LOG), exist_ok=True)
        with open(CRASH_LOG, "a") as fh:
            fh.write(time.strftime("[%Y-%m-%d %H:%M:%S] ") + text.rstrip() + "\n")
    except OSError:
        pass


def version_tuple(tag):
    parts = re.findall(r"\d+", tag or "")[:3]
    return tuple(int(p) for p in parts) or (0,)


def ensure_ca_bundle():
    """Bundel PyInstaller tidak menemukan CA sistem sendiri; arahkan OpenSSL ke sana."""
    if os.environ.get("SSL_CERT_FILE"):
        return
    for path in ("/etc/ssl/certs/ca-certificates.crt",      # Arch/Debian
                 "/etc/pki/tls/certs/ca-bundle.crt",        # Fedora/openSUSE
                 "/etc/ssl/cert.pem"):                      # Alpine/BSD
        if os.path.exists(path):
            os.environ["SSL_CERT_FILE"] = path
            return


def verify_sha256(path, sha_url):
    """True/False bila berkas checksum tersedia, None bila rilis tidak menyediakannya."""
    try:
        with urllib.request.urlopen(sha_url, timeout=10) as resp:
            expected = resp.read().decode().split()[0].strip().lower()
    except Exception:  # noqa: BLE001
        return None
    if len(expected) != 64:
        return None
    actual = hashlib.sha256(open(path, "rb").read()).hexdigest()
    return actual == expected


def latest_release():
    """(tag, url aset) dari rilis terbaru Gitea — dipanggil dari thread pekerja."""
    ensure_ca_bundle()
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/json", "User-Agent": "gamemax-lcd"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.load(resp)
    asset = next((a for a in data.get("assets", []) if a["name"] == UPDATE_ASSET), None)
    return data.get("tag_name", ""), (asset or {}).get("browser_download_url", "")


def installed_appimage():
    """Path AppImage yang sedang berjalan (kosong kalau dijalankan dari sumber)."""
    return os.environ.get("APPIMAGE", "")


def open_link(url, parent=None):
    """Buka tautan di peramban; kalau gagal, tampilkan pesannya di log."""
    if not QDesktopServices.openUrl(QUrl(url)):
        if parent is not None:
            parent.log(f"tidak bisa membuka tautan: {url}")


def other_autostart_entries(appimage):
    """Entri autostart lain yang menjalankan AppImage yang sama (mis. buatan tool integrasi)."""
    found = []
    for path in glob.glob(os.path.join(AUTOSTART_DIR, "*.desktop")):
        if path == AUTOSTART_FILE:
            continue
        try:
            text = open(path).read()
        except OSError:
            continue
        if appimage and appimage in text:
            found.append(path)
    return found


def set_tray_autostart(enable, appimage):
    """Autostart ikon tray saat login, tersembunyi (tanpa jendela)."""
    if not appimage:
        return "tidak berjalan dari AppImage — autostart tray dilewati"
    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    notes = []
    if enable:
        with open(AUTOSTART_FILE, "w") as fh:
            fh.write(f"""[Desktop Entry]
Type=Application
Name={APP_NAME} (tray)
Comment=Layar LCD AIO — berjalan di tray saat login (tanpa jendela)
Exec="{appimage}" --tray-only
Icon=iceburg-lcd
Terminal=false
X-GNOME-Autostart-enabled=true
""")
        notes.append("autostart tray dibuat (--tray-only, tanpa jendela)")
        for path in other_autostart_entries(appimage):
            try:
                text = open(path).read()
                if "Hidden=true" not in text:
                    with open(path, "a") as fh:
                        fh.write("\nHidden=true\n")
                    notes.append(f"entri autostart lain dinonaktifkan: {os.path.basename(path)}")
            except OSError:
                pass
    else:
        if os.path.exists(AUTOSTART_FILE):
            os.unlink(AUTOSTART_FILE)
            notes.append("autostart tray dihapus")
        for path in other_autostart_entries(appimage):
            try:
                lines = [l for l in open(path).read().splitlines() if l.strip() != "Hidden=true"]
                with open(path, "w") as fh:
                    fh.write("\n".join(lines) + "\n")
                notes.append(f"entri autostart lain diaktifkan kembali: {os.path.basename(path)}")
            except OSError:
                pass
    return "; ".join(notes) or "tidak ada perubahan autostart tray"


def systemctl(*args):
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def service_active():
    return systemctl("is-active", SERVICE).stdout.strip() == "active"


def service_enabled():
    return systemctl("is-enabled", SERVICE).stdout.strip() == "enabled"


def dimmed_icon(path, opacity=0.35):
    """Ikon tray versi redup untuk menandai layar tidak menampilkan sensor."""
    icon = QIcon(path)
    pixmap = icon.pixmap(64, 64)
    if pixmap.isNull():
        return icon
    dim = QPixmap(pixmap.size())
    dim.fill(Qt.transparent)
    painter = QPainter(dim)
    painter.setOpacity(opacity)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return QIcon(dim)


def icon_path():
    for name in ("icon.png", "icon.ico", "gamemax.png"):
        path = os.path.join(HERE, name)
        if os.path.exists(path):
            return path
    return ""


class Task(QThread):
    """Menjalankan aksi lambat (systemctl/pkexec/salin berkas) di luar thread UI.

    PENTING: method yang dijalankan di sini tidak boleh menyentuh widget Qt
    (membaca combo/checkbox atau memanggil refresh()); ambil nilainya di thread UI
    dan kirim sebagai argumen.
    """

    done = Signal(str, bool, str)

    def __init__(self, label, fn):
        super().__init__()
        self.label, self.fn = label, fn
        self.result = None

    def run(self):
        try:
            self.result = self.fn()
            self.done.emit(self.label, True, self.result if isinstance(self.result, str) else "")
        except Exception as exc:  # noqa: BLE001
            self.done.emit(self.label, False, str(exc))


class Animated:
    """Nilai yang bergerak halus menuju target (digerakkan QTimer 33 ms)."""

    def __init__(self, value=0.0):
        self.shown = value
        self.target = value

    def tick(self, ease=0.18):
        if abs(self.target - self.shown) < 0.5:
            self.shown = self.target
        else:
            self.shown += (self.target - self.shown) * ease
        return self.shown


class Gauge(QWidget):
    """Busur 240 derajat untuk suhu (0..99 °C), dengan jarum dan angka besar."""

    def __init__(self, title, vmax=99, unit="°C"):
        super().__init__()
        self.title, self.vmax, self.unit = title, vmax, unit
        self.value = Animated()
        self.setMinimumSize(220, 220)

    def set(self, value):
        self.value.target = 0.0 if value is None else min(self.vmax, value)

    def tick(self):
        self.value.tick(0.25)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        side = min(w, h) - 12
        rect = QRectF((w - side) / 2, (h - side) / 2 + 6, side, side)
        start, span = 210, -240                       # derajat: kiri-bawah -> kanan-bawah
        frac = max(0.0, min(1.0, self.value.shown / self.vmax))

        p.setPen(QPen(QColor(58, 62, 58), 14, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(rect, start * 16, span * 16)
        if frac > 0:
            grad = QConicalGradient(rect.center(), -60)
            grad.setColorAt(0.0, ACCENT)
            grad.setColorAt(0.5, QColor(255, 170, 60))
            grad.setColorAt(1.0, QColor(255, 90, 60))
            p.setPen(QPen(grad, 14, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(rect, start * 16, int(span * frac * 16))

        angle = math.radians(start + span * frac)
        r = side / 2 - 8
        cx, cy = rect.center().x(), rect.center().y()
        p.setPen(QPen(ACCENT, 5, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(cx, cy), QPointF(cx + r * math.cos(angle), cy - r * math.sin(angle)))

        p.setPen(ACCENT)
        f = QFont(); f.setPointSize(max(22, side // 5)); f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(rect.x(), cy - side / 6, side, side / 3), Qt.AlignCenter,
                   f"{int(round(self.value.shown))}")
        p.setPen(QColor(210, 214, 210))
        f.setPointSize(max(9, side // 14)); f.setBold(False)
        p.setFont(f)
        p.drawText(QRectF(rect.x(), cy + side / 8, side, side / 5), Qt.AlignCenter,
                   f"{self.unit} · {self.title}")


class Bar(QWidget):
    """Bar horizontal beranimasi dengan label dan nilai."""

    def __init__(self, title, vmax, unit=""):
        super().__init__()
        self.title, self.vmax, self.unit = title, vmax, unit
        self.value = Animated()
        self.setMinimumHeight(46)

    def set(self, value):
        self.value.target = 0.0 if value is None else max(0.0, min(self.vmax, value))

    def tick(self):
        self.value.tick()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        f = QFont(); f.setPointSize(10)
        p.setFont(f)
        p.setPen(QColor(210, 214, 210))
        frac = self.value.shown / self.vmax
        shown = int(round(self.value.shown))
        p.drawText(QRectF(0, 0, w, 20), Qt.AlignLeft, self.title)
        p.setPen(ACCENT)
        p.drawText(QRectF(0, 0, w, 20), Qt.AlignRight, f"{shown}{self.unit}")
        track = QRectF(0, h - 18, w, 12)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(58, 62, 58))
        p.drawRoundedRect(track, 6, 6)
        if frac > 0.001:
            fill = QRectF(0, h - 18, max(6.0, w * frac), 12)
            p.setBrush(ACCENT if frac < 0.8 else QColor(255, 120, 70))
            p.drawRoundedRect(fill, 6, 6)


class Window(QWidget):
    def __init__(self, tray):
        super().__init__()
        self.tray = tray
        self.prev_stat = gr.stat_snapshot()
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION}")
        self.setWindowIcon(QIcon(icon_path()))
        self.setMinimumWidth(640)
        root = QVBoxLayout(self)
        self._extra = {k: v for k, v in gr.load_config(CONFIG_PATH).items()
                       if k in ("close_action", "geometry")}
        geom = self._extra.get("geometry")
        if isinstance(geom, list) and len(geom) == 4:
            self.setGeometry(*[int(g) for g in geom])

        hint = QLabel("Kontrol layar LCD AIO IceBurg Digital. Pilih slot kipas/sumber suhu di "
                      "<b>Pengaturan</b> lalu klik <b>Simpan &amp; terapkan</b>.")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        # ---- banner mode layar (jelas: normal / kosong / berhenti)
        self.mode_banner = QLabel()
        self.mode_banner.setAlignment(Qt.AlignCenter)
        self.mode_banner.setMinimumHeight(34)
        root.addWidget(self.mode_banner)

        # ---- header status
        self.status = QLabel()
        self.status.setStyleSheet("font-size: 13px;")
        root.addWidget(self.status)

        # ---- kontrol utama
        controls = QHBoxLayout()
        self.toggle = QPushButton("Aktifkan layar")
        self.toggle.setMinimumHeight(38)
        self.toggle.setObjectName("primary")
        self.toggle.clicked.connect(lambda: self.start_task("mengubah status layanan", self.on_toggle))
        self.toggle.setToolTip("Mulai/hentikan pengiriman data sensor ke layar LCD")
        controls.addWidget(self.toggle, 2)
        self.blank = QCheckBox("Layar kosong")
        self.blank.toggled.connect(self.on_blank_clicked)
        self.blank.setToolTip("Kirim nilai nol ke layar (panel tetap beranimasi)")
        controls.addWidget(self.blank)
        test = QPushButton("Kirim uji")
        test.setToolTip("Kirim satu report contoh: 55 °C, beban 50 %, kipas 1500 RPM")
        test.clicked.connect(self.send_test)
        controls.addWidget(test)
        root.addLayout(controls)

        # indikator proses berjalan (aksi dijalankan di thread terpisah)
        self.busy = QProgressBar()
        self.busy.setRange(0, 0)          # mode tak tentu -> animasi berjalan
        self.busy.setTextVisible(False)
        self.busy.setFixedHeight(10)
        self.busy.hide()
        root.addWidget(self.busy)
        self.busy_label = QLabel()
        self.busy_label.setStyleSheet("color:#deF750;")
        self.busy_label.hide()
        root.addWidget(self.busy_label)
        self.buttons = [self.toggle, test]

        # ---- papan monitor beranimasi
        board = QGroupBox("Pemantauan langsung")
        self.board = board
        bl = QHBoxLayout(board)
        self.gauge = Gauge("CPU", 99, "°C")
        bl.addWidget(self.gauge, 1)
        bars = QVBoxLayout()
        self.bar_load = Bar("Beban CPU", 100, " %")
        self.bar_fan = Bar("Kipas CPU (slot RPM)", 2000, " RPM")
        self.bar_clock = Bar("Frekuensi CPU", 6000, " MHz")
        for b in (self.bar_load, self.bar_fan, self.bar_clock):
            bars.addWidget(b)
        self.info = QLabel()
        self.info.setStyleSheet("color:#c8ccc8;")
        bars.addWidget(self.info)
        bars.addStretch(1)
        bl.addLayout(bars, 2)
        root.addWidget(board)

        # ---- pengaturan
        settings = QGroupBox("Pengaturan")
        form = QFormLayout(settings)
        self.fan = QComboBox()
        self.fan.setToolTip("Kanal kipas yang dikirim ke layar; pilih kanal yang berputar "
                            "kalau kipas dipindah ke header lain")
        self.temp = QComboBox()
        form.addRow("Slot kipas (RPM)", self.fan)
        form.addRow("Sumber suhu CPU", self.temp)
        self.autostart = QCheckBox("Mulai otomatis saat login")
        self.autostart.toggled.connect(
            lambda checked: self.start_task("mulai otomatis saat login",
                                            lambda: self.on_autostart(checked)))
        form.addRow("", self.autostart)
        row = QHBoxLayout()
        save = QPushButton("Simpan && terapkan")
        save.clicked.connect(self.on_save_clicked)
        row.addWidget(save)
        install = QPushButton("Pasang / perbarui layanan")
        install.clicked.connect(lambda: self.start_task("memasang layanan", self.install_service))
        row.addWidget(install)
        rule = QPushButton("Pasang aturan udev")
        rule.clicked.connect(lambda: self.start_task("memasang aturan udev", self.install_rule))
        row.addWidget(rule)
        self.buttons += [save, install, rule]
        form.addRow("", row)
        root.addWidget(settings)

        # ---- tautan
        links = QHBoxLayout()
        donate = QPushButton("❤  Donasi")
        donate.setToolTip(DONATE_URL)
        donate.clicked.connect(lambda: open_link(DONATE_URL, self))
        repo_btn = QPushButton("Gitea")
        repo_btn.setToolTip(REPO_URL)
        repo_btn.clicked.connect(lambda: open_link(REPO_URL, self))
        links.addWidget(donate)
        links.addWidget(repo_btn)
        links.addStretch(1)
        copy_button = QPushButton("Salin report")
        copy_button.setToolTip("Salin isi report 65 byte ke papan klip (untuk laporan bug)")
        copy_button.clicked.connect(self.copy_report)
        links.addWidget(copy_button)
        self.check_button = QPushButton("Periksa pembaruan")
        self.check_button.setToolTip("Bandingkan dengan rilis terbaru di Gitea")
        self.check_button.clicked.connect(lambda: self.start_task("memeriksa pembaruan", self.do_check_update))
        links.addWidget(self.check_button)
        self.update_button = QPushButton("Perbarui")
        self.update_button.setStyleSheet("font-weight:bold;")
        self.update_button.hide()
        self.update_button.clicked.connect(self.on_update_clicked)
        links.addWidget(self.update_button)
        links.addStretch(1)
        self.version_label = QLabel(f"v{APP_VERSION}")
        self.version_label.setStyleSheet("color:#8a8f8a;")
        links.addWidget(self.version_label)
        root.addLayout(links)

        # ---- detail + log
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setFixedHeight(110)
        root.addWidget(self.detail)
        self.logview = QPlainTextEdit()
        self.logview.setReadOnly(True)
        self.logview.setFixedHeight(64)
        root.addWidget(self.logview)

        self.reload_choices()
        self.reload_config()

        # 33 ms -> animasi; 1000 ms -> baca sensor
        self.anim = QTimer(self, timeout=self.tick, interval=33)
        self.anim.start()
        self.poll = QTimer(self, timeout=self.refresh, interval=1000)
        self.poll.start()
        self.refresh()
        testing = any(f in sys.argv for f in ("--selftest", "--selftest-actions"))
        if installed_appimage() and not testing:     # hanya pada versi terpasang
            QTimer.singleShot(2500, lambda: self.start_task("memeriksa pembaruan",
                                                            self.do_check_update))
        if gr.load_config(CONFIG_PATH).get("blank"):
            self.log("PERHATIAN: mode layar kosong sedang aktif dari konfigurasi "
                     "(layar menampilkan nilai nol). Hilangkan centang 'Layar kosong' "
                     "untuk kembali ke nilai sensor.")

    # -------------------------------------------------------------- pengaturan
    def reload_choices(self):
        self.fan.clear()
        self.fan.addItem("Otomatis (kipas tercepat yang berputar)", "")
        for c in gr.hwmon_channels("fan*_input"):
            rpm = "" if c["value"] is None else f" — {int(c['value'])} RPM"
            self.fan.addItem(f"{c['chip']} {c['file']}{rpm}", f"{c['chip']}/{c['file']}")
        self.temp.clear()
        self.temp.addItem("Otomatis (sensor CPU tertinggi)", "")
        channels = [c for c in gr.hwmon_channels("temp*_input") if c["value"] is not None]
        channels.sort(key=lambda c: (c["chip"] not in gr.CPU_CHIPS, c["chip"], c["file"]))
        for c in channels:
            name = c["label"] or c["file"]
            tag = "" if c["chip"] in gr.CPU_CHIPS else " (bukan CPU) "
            self.temp.addItem(f"{c['chip']}{tag} {name} — {c['value'] / 1000:.1f} °C",
                              f"{c['chip']}/{c['file']}")

    def reload_config(self):
        cfg = gr.load_config(CONFIG_PATH)
        for combo, key in ((self.fan, "fan_channel"), (self.temp, "temp_channel")):
            idx = combo.findData(cfg.get(key) or "")
            combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.autostart.blockSignals(True)
        self.autostart.setChecked(service_enabled())
        self.autostart.blockSignals(False)

    def config(self):
        return {
            **self._extra,
            "fan_channel": self.fan.currentData() or None,
            "temp_channel": self.temp.currentData() or None,
            "unit": "c",                      # panel hanya punya label °C
            "interval": 0.5,
            "sample": 0.5,
            "vendor_layout": False,
            "blank": bool(self.blank.isChecked()),
        }

    def copy_report(self):
        QApplication.clipboard().setText(getattr(self, "_report_hex", ""))
        self.log("isi report disalin ke papan klip")

    def save_geometry(self):
        g = self.geometry()
        self._extra["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        try:
            self.write_config(self.config())
        except OSError:
            pass

    def closeEvent(self, event):                      # noqa: N802 (Qt)
        if getattr(self, "_quitting", False):
            self.save_geometry()
            event.accept()
            return
        action = self._extra.get("close_action", "ask")
        if action == "tray" and self.tray.available():
            self.save_geometry()
            self.hide()
            event.ignore()
            return
        if action == "quit":
            self.save_geometry()
            self._quitting = True
            event.accept()
            QApplication.quit()
            return

        box = QMessageBox(self)
        box.setWindowTitle(f"Tutup {APP_NAME}")
        box.setText("Sembunyikan ke tray atau keluar sepenuhnya?")
        box.setInformativeText("Selama tersembunyi di tray, data sensor tetap dikirim ke layar.")
        remember = QCheckBox("Ingat pilihan ini")
        box.setCheckBox(remember)
        hide_btn = box.addButton("Sembunyikan ke tray", QMessageBox.AcceptRole)
        quit_btn = box.addButton("Keluar", QMessageBox.DestructiveRole)
        box.addButton("Batal", QMessageBox.RejectRole)
        if not self.tray.available():
            hide_btn.setEnabled(False)
            box.setInformativeText("Tray tidak tersedia di desktop ini — menutup jendela akan "
                                   "menghentikan aplikasi.")
        box.exec()
        clicked = box.clickedButton()
        if clicked is hide_btn:
            if remember.isChecked():
                self._extra["close_action"] = "tray"
            self.save_geometry()
            self.hide()
            event.ignore()
        elif clicked is quit_btn:
            if remember.isChecked():
                self._extra["close_action"] = "quit"
            self.save_geometry()
            self._quitting = True
            event.accept()
            QApplication.quit()
        else:
            event.ignore()

    # -------------------------------------------------------------- pembaruan
    def do_check_update(self):
        tag, url = latest_release()
        self._pending_update = (tag, url) if url and version_tuple(tag) > version_tuple(APP_VERSION) else None
        if not tag:
            return "tidak bisa membaca daftar rilis Gitea"
        if self._pending_update:
            return f"versi baru tersedia: {tag} (terpasang v{APP_VERSION})"
        return f"sudah versi terbaru (v{APP_VERSION})"

    def on_update_clicked(self):
        pending = getattr(self, "_pending_update", None)
        target = installed_appimage()
        if not pending:
            return
        if not target:
            self.log("tidak berjalan dari AppImage — perbarui manual dari halaman Releases")
            return
        tag, url = pending
        self.start_task(f"mengunduh {tag}", lambda: self.do_update(tag, url, target))

    def do_update(self, tag, url, target):
        ensure_ca_bundle()
        tmp = target + ".new"
        urllib.request.urlretrieve(url, tmp)
        checked = verify_sha256(tmp, url + ".sha256")
        if checked is False:
            os.unlink(tmp)
            raise RuntimeError("checksum SHA-256 tidak cocok — unduhan dibatalkan")
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)                      # ganti berkas secara atomik
        # sender di layanan juga diambil dari bundel baru
        with tempfile.TemporaryDirectory() as td:
            subprocess.run([target, "--appimage-extract"], cwd=td,
                           capture_output=True, timeout=180)
            sender = os.path.join(td, "squashfs-root/usr/bin/_internal/gamemax_report.py")
            if os.path.exists(sender):
                os.makedirs(INSTALL_DIR, exist_ok=True)
                shutil.copyfile(sender, os.path.join(INSTALL_DIR, "gamemax_report.py"))
                systemctl("restart", SERVICE)
        note = "" if checked is None else " (checksum SHA-256 terverifikasi)"
        if checked is None:
            note = " (rilis ini tidak menyertakan checksum)"
        return (f"diperbarui ke {tag}{note} — tutup jendela ini lalu buka lagi "
                "untuk memakai versi baru")

    # -------------------------------------------------------------- tugas latar
    def start_task(self, label, fn):
        if getattr(self, "_task", None) is not None and self._task.isRunning():
            self.log("masih ada proses berjalan — tunggu sampai indikator selesai")
            return
        self.busy_label.setText(f"Sedang bekerja: {label}…")
        self.busy.show()
        self.busy_label.show()
        for button in self.buttons:
            button.setEnabled(False)
        task = Task(label, fn)
        task.done.connect(self.on_task_done)
        self._task = task
        task.start()

    def on_task_done(self, label, ok, message):
        self.busy.hide()
        self.busy_label.hide()
        for button in self.buttons:
            button.setEnabled(True)
        self.log(("selesai: " if ok else "GAGAL: ") + label + (f" — {message}" if message else ""))
        pending = getattr(self, "_pending_update", None)
        if label == "memeriksa pembaruan" and pending:
            self.update_button.setText(f"Perbarui ke {pending[0]}")
            self.update_button.show()
        self.refresh()

    # -------------------------------------------------------------- aksi
    def on_toggle(self):
        try:
            self.ensure_unit()
            want_active = not service_active()
            for attempt in (1, 2):
                systemctl("start" if want_active else "stop", SERVICE)
                if service_active() == want_active:
                    return (f"layanan {'AKTIF' if want_active else 'nonaktif'}"
                            + ("" if attempt == 1 else f" (percobaan ke-{attempt})"))
            else:
                return "status layanan tidak berubah seperti yang diminta"
        except Exception as exc:  # noqa: BLE001
            return f"galat: {exc}"

    def on_autostart(self, checked):
        try:
            self.ensure_unit()
            systemctl("daemon-reload")
            res = systemctl("enable" if checked else "disable", SERVICE)
            state = service_enabled()
            note = set_tray_autostart(checked, installed_appimage())
            return (f"mulai otomatis saat login: {'AKTIF' if state else 'nonaktif'}"
                    + ("" if res.returncode == 0 else f" ({res.stderr.strip()})")
                    + f" | {note}")
        except Exception as exc:  # noqa: BLE001
            return f"galat autostart: {exc}"

    def on_blank_clicked(self, checked):
        """Dijalankan di thread UI: baca widget di sini, kerjakan sisanya di latar."""
        cfg = self.config()
        cfg["blank"] = bool(checked)
        self.start_task("mode layar kosong", lambda: self.on_blank(checked, cfg))

    def on_blank(self, checked, cfg):
        """Mode layar kosong ditangani pengirim (config "blank")."""
        try:
            self.write_config(cfg)
            self.ensure_unit(cfg)
            if checked and not service_active():
                systemctl("start", SERVICE)
            elif service_active():
                systemctl("restart", SERVICE)
            return ("layar kosong: AKTIF (report bernilai nol)" if checked
                    else "layar kosong: nonaktif (kembali ke nilai sensor)")
        except Exception as exc:  # noqa: BLE001
            return f"galat mode layar kosong: {exc}"

    def write_config(self, cfg):
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as fh:
            json.dump(cfg, fh, indent=2)

    def ensure_unit(self, cfg=None):
        if not os.path.exists(UNIT_PATH):
            self.install_service(cfg)

    def send_test(self):
        node = gr.find_device()
        if not node:
            self.log("perangkat tidak terdeteksi")
            return
        v = gr.read_sensors(self.prev_stat, {"fan_channel": self.fan.currentData(),
                                             "temp_channel": self.temp.currentData()})
        v.update({"cpu_temp": 55, "gpu_temp": 60, "cpu_load": 50, "cpu_fan": 1500,
                  "cpu_clock": 4000, "cpu_power": 66})
        try:
            with open(node, "wb", buffering=0) as fh:
                fh.write(gr.build_report(v, "c")[1:])
            self.log(f"report uji terkirim ke {node} (55 °C / 50 % / 1500 RPM)")
        except OSError as exc:
            self.log(f"gagal mengirim uji: {exc}")

    def install_service(self, cfg=None):
        try:
            os.makedirs(INSTALL_DIR, exist_ok=True)
            script = os.path.join(INSTALL_DIR, "gamemax_report.py")
            shutil.copyfile(os.path.join(HERE, "gamemax_report.py"), script)
            os.makedirs(os.path.dirname(UNIT_PATH), exist_ok=True)
            cfg = cfg or {}
            with open(UNIT_PATH, "w") as fh:
                fh.write(UNIT_TEMPLATE.format(script=script,
                                              interval=cfg.get("interval", 0.5),
                                              sample=cfg.get("sample", 0.5)))
            systemctl("daemon-reload")
            return f"layanan dipasang: {UNIT_PATH}"
        except Exception as exc:  # noqa: BLE001
            return f"gagal memasang layanan: {exc}"

    def install_rule(self):
        script = "/tmp/gamemax-udev-install.sh"
        try:
            with open(script, "w") as fh:
                fh.write("#!/bin/sh\n"
                         f"cat > {RULE_PATH} <<'EOF'\n{RULE_TEMPLATE}EOF\n"
                         "udevadm control --reload\n"
                         "udevadm trigger --subsystem-match=hidraw\n")
            os.chmod(script, 0o755)
            res = subprocess.run(["pkexec", "sh", script], capture_output=True, text=True)
            return ("aturan udev dipasang" if res.returncode == 0
                    else f"gagal memasang udev: {res.stderr.strip() or res.returncode}")
        except FileNotFoundError:
            return f"pkexec tidak tersedia; jalankan manual: sudo sh {script}"
        except Exception as exc:  # noqa: BLE001
            return f"galat udev: {exc}"

    def fix_fan_choice(self):
        """Slot kipas terpilih yang diam sementara ada kipas lain berputar akan dialihkan."""
        if not self.fan.currentData():
            return ""
        spinning = [(c["chip"], c["file"], int(c["value"])) for c in gr.hwmon_channels("fan*_input")
                    if c["value"] and c["chip"] not in gr.GPU_CHIPS]
        if gr.channel_value(self.fan.currentData()) or not spinning:
            return ""
        chip, fname, rpm = max(spinning, key=lambda t: t[2])
        idx = self.fan.findData(f"{chip}/{fname}")
        if idx < 0:
            return ""
        old = self.fan.currentText()
        self.fan.setCurrentIndex(idx)
        return f"slot lama membaca 0 RPM ({old.split(' —')[0]}) -> dialihkan ke {chip} {fname} ({rpm} RPM)"

    def on_save_clicked(self):
        """Thread UI: koreksi slot kipas lalu baca pengaturan sebelum dikerjakan."""
        note = self.fix_fan_choice()
        self.start_task("menyimpan pengaturan",
                        lambda: self.save_settings(self.config(), note))

    def save_settings(self, cfg, note=""):
        try:
            self.write_config(cfg)
            self.ensure_unit(cfg)
            if service_active():
                systemctl("restart", SERVICE)
                tail = " (layanan dijalankan ulang)"
            else:
                systemctl("start", SERVICE)
                tail = " (layanan dinyalakan)"
            return "pengaturan disimpan & diterapkan" + tail + (f" | {note}" if note else "")
        except Exception as exc:  # noqa: BLE001
            return f"gagal menyimpan: {exc}"

    def log(self, text):
        self.logview.appendPlainText(text)
        if os.environ.get("GAMEMAX_DEBUG"):
            print(text, flush=True)

    def warn_once(self, text):
        if getattr(self, "_warned", None) != text:
            self._warned = text
            self.log(text)

    # -------------------------------------------------------------- refresh
    def refresh(self):
        try:
            active = service_active()
            node = gr.find_device()
            enabled = service_enabled()
            blank_on = bool(gr.load_config(CONFIG_PATH).get("blank"))
            mode = "normal" if active and not blank_on else ("kosong" if blank_on else "berhenti")
            banner = {
                "normal": ("Layar menampilkan data sensor", "#1d3b1d", "#b8f5b8"),
                "kosong": ("LAYAR KOSONG — panel menampilkan nilai nol (animasi firmware tetap jalan)",
                           "#4a3a10", "#ffd479"),
                "berhenti": ("PENGIRIMAN DIHENTIKAN — panel menampilkan data terakhir yang diterima",
                             "#3a3a3a", "#d0d0d0"),
            }[mode]
            self.mode_banner.setText(banner[0])
            self.board.setTitle({
                "normal": "Pemantauan langsung — nilai ini yang dikirim ke layar",
                "kosong": "Pemantauan langsung — nilai sensor (yang DIKIRIM ke layar: nol)",
                "berhenti": "Pemantauan langsung — nilai sensor (TIDAK dikirim, layanan mati)",
            }[mode])
            self.mode_banner.setStyleSheet(
                f"background:{banner[1]}; color:{banner[2]}; border-radius:4px; "
                "font-weight:bold; padding:4px;")
            if getattr(self, "_mode", None) != mode:
                self._mode = mode
                labels = {"normal": "Layar kembali menampilkan data sensor",
                          "kosong": "Layar dikosongkan (nilai nol)",
                          "berhenti": "Pengiriman data dihentikan"}
                if mode != "normal" or getattr(self, "_mode_seen", False):
                    subprocess.run(["notify-send", "-a", APP_NAME, "Status layar",
                                    labels[mode]], capture_output=True)
                self._mode_seen = True
            dot = "🟢" if active else "⚪"
            self.status.setText(
                f"{dot} Perangkat: <b>{node or 'tidak terdeteksi'}</b> &nbsp;·&nbsp; "
                f"Layanan: <b>{'AKTIF' if active else 'nonaktif'}</b> &nbsp;·&nbsp; "
                f"Mulai otomatis: <b>{'ya' if enabled else 'tidak'}</b>"
                + (" &nbsp;·&nbsp; Layar: <b style='color:#ff9060'>KOSONG (nilai nol)</b>"
                   if blank_on else ""))
            self.toggle.setText("Matikan layar" if active else "Aktifkan layar")
            self.autostart.blockSignals(True)
            self.autostart.setChecked(enabled)
            self.autostart.blockSignals(False)
            self.blank.blockSignals(True)
            self.blank.setChecked(bool(gr.load_config(CONFIG_PATH).get("blank")))
            self.blank.blockSignals(False)
            self.tray.sync(active)

            cfg = {"fan_channel": self.fan.currentData(),
                   "temp_channel": self.temp.currentData()}
            v = gr.read_sensors(self.prev_stat, cfg)
            self.prev_stat = gr.stat_snapshot()
            self.gauge.set(v["cpu_temp"])
            self.bar_load.set(v["cpu_load"])
            self.bar_fan.set(v["cpu_fan"])
            spinning = [(c["chip"], c["file"], int(c["value"])) for c in gr.hwmon_channels("fan*_input")
                        if c["value"] and c["chip"] not in gr.GPU_CHIPS]
            if self.fan.currentData() and not (v["cpu_fan"] or 0) and spinning:
                best = max(spinning, key=lambda t: t[2])
                self.warn_once(f"slot kipas terpilih membaca 0 RPM; kipas berputar ada di "
                               f"{best[0]} {best[1]} ({best[2]} RPM) — pilih itu lalu Simpan")
            self.bar_clock.set(v["cpu_clock"])
            self.detail.setPlainText(
                (f"Daya CPU        : {v['cpu_power']:.0f} W\n" if v["cpu_power"] else "Daya CPU        : —\n") +
                (f"GPU             : {v['gpu_temp']:.0f} °C · beban {v['gpu_load'] or '—'} % · "
                 f"{int(v['gpu_clock'] or 0)} MHz\n" if v["gpu_temp"] else "GPU             : —\n") +
                (f"Memori          : {v['ram_load']} % dari {int(v['ram_mb'])} MiB\n"
                 if v["ram_load"] and v["ram_mb"] else "Memori          : —\n") +
                (f"Penyimpanan     : {v['disk_free']} % kosong dari {v['disk_gb']:.0f} GB\n"
                 if v["disk_free"] and v["disk_gb"] else "Penyimpanan     : —\n") +
                "Isi report      : " + gr.build_report(v, "c").hex(" "))
        except Exception as exc:  # noqa: BLE001
            self.log(f"galat pembaruan: {exc}")
            if os.environ.get("GAMEMAX_DEBUG"):
                self.log(traceback.format_exc())

    def tick(self):
        for widget in (self.gauge, self.bar_load, self.bar_fan, self.bar_clock):
            widget.tick()


class Tray:
    def __init__(self, app):
        self.app = app
        self.icon = QSystemTrayIcon(QIcon(icon_path()), app)
        self.blank_action = QAction("Layar kosong (nilai nol)", app)
        self.blank_action.setCheckable(True)
        menu = QMenu()
        show = QAction("Buka jendela", menu)
        show.triggered.connect(self.show_window)
        toggle = QAction("Aktifkan / matikan", menu)
        toggle.triggered.connect(lambda: self.window.on_toggle())
        self.blank_action.triggered.connect(lambda checked: self.window.blank.setChecked(checked))
        check_action = QAction("Periksa pembaruan", menu)
        check_action.triggered.connect(lambda: self.window.start_task(
            "memeriksa pembaruan", self.window.do_check_update))
        donate_action = QAction("❤  Donasi", menu)
        donate_action.triggered.connect(lambda: open_link(DONATE_URL, self.window))
        repo_action = QAction("Gitea", menu)
        repo_action.triggered.connect(lambda: open_link(REPO_URL, self.window))
        quit_action = QAction("Keluar", menu)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(show)
        menu.addAction(toggle)
        menu.addAction(self.blank_action)
        menu.addSeparator()
        menu.addAction(check_action)
        menu.addAction(donate_action)
        menu.addAction(repo_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.icon.setContextMenu(menu)
        self.icon.activated.connect(
            lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)
        self.window = Window(self)
        self.icon.show()

    def available(self):
        return QSystemTrayIcon.isSystemTrayAvailable()

    def quit_app(self):
        self.window._quitting = True
        self.app.quit()

    def sync(self, active):
        blank = bool(gr.load_config(CONFIG_PATH).get("blank"))
        state = ("layar kosong (nilai nol)" if blank and active
                 else ("pengiriman dihentikan" if not active else "normal"))
        self.icon.setToolTip(f"{APP_NAME}: {state}")
        base = icon_path()
        self.icon.setIcon(dimmed_icon(base) if state != "normal" else QIcon(base))
        window = getattr(self, "window", None)
        if window is not None:
            self.blank_action.setChecked(window.blank.isChecked())

    def show_window(self):
        self.window.showNormal()          # keluar dari minimize
        self.window.setWindowState((self.window.windowState() & ~Qt.WindowMinimized)
                                   | Qt.WindowActive)
        self.window.raise_()
        self.window.activateWindow()
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.icon.showMessage(APP_NAME, "Jendela dibuka dari permintaan kedua.",
                                  QSystemTrayIcon.Information, 3000)


SOCKET_NAME = "gamemax-lcd-" + os.environ.get("USER", "user")


def main():
    try:
        return _main()
    except Exception:  # noqa: BLE001
        crash_log("FATAL\n" + traceback.format_exc())
        raise


def _main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyleSheet(APP_QSS)
    app.setQuitOnLastWindowClosed(False)

    # hanya satu instance: yang kedua memunculkan jendela yang sudah ada lalu keluar
    probe = QLocalSocket()
    probe.connectToServer(SOCKET_NAME)
    if probe.waitForConnected(400):
        probe.write(b"raise")
        probe.waitForBytesWritten(400)
        print(f"{APP_NAME} sudah berjalan — instance pertama diminta membuka jendelanya.")
        # Beri tahu lewat notifikasi desktop juga, karena keluarnya proses kedua bisa
        # terlihat seperti crash saat diluncurkan dari menu.
        subprocess.run(["notify-send", "-a", APP_NAME, "Sudah berjalan",
                        "Aplikasi sudah aktif — jendelanya dibuka, cek taskbar/tray."],
                       capture_output=True)
        return 0
    QLocalServer.removeServer(SOCKET_NAME)
    server = QLocalServer()
    server.listen(SOCKET_NAME)

    tray = Tray(app)
    server.newConnection.connect(lambda: tray.show_window())
    def on_quit():
        task = getattr(tray.window, "_task", None)
        if task is not None and task.isRunning():
            task.wait(3000)
        server.close()
        QLocalServer.removeServer(SOCKET_NAME)

    app.aboutToQuit.connect(on_quit)
    # Jendela selalu dibuka saat diluncurkan; tray tetap ada kalau didukung desktop.
    tray.show_window()
    if "--tray-only" in sys.argv:
        tray.window.hide()
    if not QSystemTrayIcon.isSystemTrayAvailable():
        tray.window.log("Tray tidak tersedia di desktop ini — pakai jendela ini untuk kontrol.")
    def report_exception(*tb):
        text = "".join(traceback.format_exception(*tb)).strip()
        crash_log(text)
        try:
            tray.window.log("GALAT: " + text.splitlines()[-1])
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = report_exception
    threading.excepthook = lambda args: report_exception(args.exc_type, args.exc_value, args.exc_traceback)
    if "--selftest-actions" in sys.argv:      # uji aksi GUI pada bundel beku
        w = tray.window
        steps = (("simpan & terapkan", lambda: w.save_settings(w.config())),
                 ("layar kosong ON", lambda: w.on_blank(True, {**w.config(), "blank": True})),
                 ("layar kosong OFF", lambda: w.on_blank(False, {**w.config(), "blank": False})),
                 ("matikan layar", w.on_toggle),
                 ("nyalakan layar", w.on_toggle),
                 ("autostart ON", lambda: w.on_autostart(True)),
                 ("kirim uji", w.send_test),
                 ("pasang layanan", lambda: w.install_service(w.config())),
                 ("periksa pembaruan", w.do_check_update))
        for name, fn in steps:
            try:
                out = fn()
                print(f"[ OK ] {name}" + (f" — {out}" if isinstance(out, str) and out else ""))
            except Exception as exc:  # noqa: BLE001
                print(f"[GAGAL] {name}: {exc}")
        # jalur asinkron yang dipakai tombol: centang layar kosong + simpan
        def wait_task(seconds=10):
            t0 = time.time()
            while time.time() - t0 < seconds:
                task = getattr(w, "_task", None)
                if task is not None and not task.isRunning():
                    break
                app.processEvents()
                time.sleep(0.05)
            for _ in range(10):
                app.processEvents()
                time.sleep(0.02)

        for name, trigger in (("centang layar kosong", lambda: w.blank.setChecked(True)),
                              ("lepas centang", lambda: w.blank.setChecked(False)),
                              ("simpan lewat tombol", w.on_save_clicked)):
            try:
                trigger()
                wait_task()
                print(f"[ OK ] {name} (asinkron)")
            except Exception as exc:  # noqa: BLE001
                print(f"[GAGAL] {name} (asinkron): {exc}")
        w.refresh()
        print("log:", " | ".join(w.logview.toPlainText().splitlines()[-6:]))
        return 0
    if "--selftest" in sys.argv:
        tray.window._quitting = True                 # keluar otomatis: lewati dialog tutup
        QTimer.singleShot(1200, app.quit)
    rc = app.exec()
    if "--selftest" in sys.argv:
        print(f"selftest OK (kipas={tray.window.fan.count()} suhu={tray.window.temp.count()})")
    return rc


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.exit(main())
