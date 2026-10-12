# GAMEMAX LCD — Linux bridge & tray app

Drive the LCD panel of a GAMEMAX **IceBurg Digital** AIO cooler from Linux: a small
sender plus a tray application (GUI) with live monitoring, settings and autostart.

Reverse engineered independently from the Windows vendor software
(`PcInfoMonitorPro.exe` / "IceBurg Digital Software"); the protocol notes are in
[`docs/reverse-engineering.md`](docs/reverse-engineering.md) (Indonesian).

Author: **oxpoe** · License: **MIT** · Please read [DISCLAIMER.md](DISCLAIMER.md).

![Tray app preview](assets/preview.png)

## Features

- Sends CPU temp/load/clock/fan/power, GPU temp/load/clock/fan/power, RAM and disk
  usage to the panel as a 65-byte HID report (the layout the firmware understands)
- Reads sensors from standard Linux interfaces: `hwmon`, `/proc/stat`, `cpufreq`,
  `/proc/meminfo`, `statvfs` — no extra dependencies (pure Python, stdlib only)
- **Tray app** (`--tray-only`) plus a window with an animated gauge, bars and details
- **Settings**: fan channel (pick the header your fans are on), CPU temperature source,
  send/sample interval — saved to `~/.config/gamemax-report/config.json`
- **Clear mode indicators**: green "showing sensor data" / orange "blank (zeros)" /
  grey "sending stopped", dimmed tray icon and a desktop notification per change
- **Autostart** via a systemd user unit (equivalent of the vendor's Task Scheduler trick)
- **Plug and play**: the service waits for the device, streams while present and keeps
  waiting through unplug/replug; a udev rule grants access to the logged-in user
- **Single instance**: launching the app again just raises the running window
- **AppImage build** script (`packaging/build_appimage.sh`, PySide6 bundled)

The GUI labels are in Indonesian.

## Requirements

- Linux with a working `hidraw` stack (tested on CachyOS / Arch, KDE Wayland)
- Python 3.10+ for the sender (stdlib only)
- For the tray app: PySide6 (`uv run --with pyside6 python src/gamemax_gui.py`)
  or use the prebuilt AppImage

## Install

```bash
# 1. device access for the logged-in user (once, needs root)
sudo cp packaging/70-gamemax.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=hidraw

# 2. try it without installing anything
cd src
python3 gamemax_report.py --dry-run      # build and print a report, no device writes
python3 gamemax_report.py --listen 5     # read panel -> host reports (none on this panel)

# 3. send for real
python3 gamemax_report.py --count 3      # a few reports
python3 gamemax_report.py                # keep streaming (Ctrl-C to stop)
```

## Run as a background service (autostart + plug and play)

```bash
mkdir -p ~/.local/share/gamemax-report ~/.config/systemd/user
cp src/gamemax_report.py ~/.local/share/gamemax-report/
cp packaging/gamemax-report.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now gamemax-report.service
journalctl --user -u gamemax-report -f        # shows "waiting for 0x5131:0x2007" / "opened ..."
```

The tray app can do all of this for you (buttons *Pasang / perbarui layanan*,
*Mulai otomatis saat login*).

## Tray app / AppImage

```bash
uv run --with pyside6 python src/gamemax_gui.py            # window + tray
uv run --with pyside6 python src/gamemax_gui.py --tray-only

bash packaging/build_appimage.sh                            # -> dist/GAMEMAX-LCD-x86_64.AppImage
```

## Protocol (verified on hardware)

VID `0x5131` / PID `0x2007`, vendor-defined HID page `0xFF00`, 64-byte IN + 64-byte OUT,
no report IDs. The host sends 64 data bytes:

| Offset | Field | Notes |
|---|---|---|
| 0 | report ID | always 0 |
| 1 | 64 | payload length |
| 2 | CPU temp | 0..99 °C, two digits only; values ≥ 128 are ignored by firmware |
| 3 | CPU load % | |
| 4..5 | RPM slot | big-endian u16 — the panel shows *this* field next to the fan icon |
| 6..7 | CPU clock MHz | big-endian u16 |
| 8..9 | CPU power W | big-endian u16 |
| 10 | GPU temp | 0..99 °C |
| 11 | GPU load % | |
| 12..13 | GPU clock MHz | big-endian u16 |
| 14..15 | GPU fan RPM | big-endian u16 |
| 16..17 | GPU power W | big-endian u16 |
| 18 | RAM usage % | |
| 19..20 | total RAM MiB | big-endian u16 |
| 21 | disk free % | |
| 22..23 | total disk GB | big-endian u16 |
| 24..64 | unused | zero |

Firmware behaviours worth knowing: the panel latches values it accepts, blinks the
value area when the data stops changing, cannot display three digits, has a fixed `°C`
label, and offers no command to power the display off.

## Repository layout

```
src/         gamemax_report.py (sender)  gamemax_gui.py (tray app)  icon.png
packaging/   build_appimage.sh  70-gamemax.rules  gamemax-report.service
tools/       ildump.py, string_refs.py — helpers used to read the vendor MSIL
docs/        reverse-engineering.md — full analysis (Indonesian)
assets/      previews and icon
```

## Credits

Reverse engineered and written by **oxpoe**, with the help of
[REA](https://github.com/morluto/rea) (managed-code analysis) and Ghidra.
Not affiliated with GAMEMAX/GameMax — see [DISCLAIMER.md](DISCLAIMER.md).
