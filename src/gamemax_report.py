#!/usr/bin/env python3
"""Push hardware sensor data to a GAMEMAX AIO LCD (HID VID 0x5131, PID 0x2007).

Reverse-engineered from PcInfoMonitorPro.exe (GAMEMAX V1.0.0.04) — see ../01-recon.md.
Report layout, derived from DMainWidget.onGetPcInfoOk:

    byte 0      report ID, always 0
    byte 1      64 (0x40), payload length
    byte 2      CPU temp (0..127). The original app ORs 0x80 for Celsius, but this
                firmware ignores every value >= 128, so we send plain numbers
    byte 3      CPU load %
    byte 4..5   RPM slot  (big-endian u16) -- panel renders this field next to the fan icon
    byte 6..7   CPU clock MHz (big-endian u16) -- original app's byte order
    byte 8..9   CPU power W (big-endian u16)
    byte 10     GPU temp (0..127), same rule as CPU temp
    byte 11     GPU load %
    byte 12..13 GPU clock MHz (big-endian u16)
    byte 14..15 GPU fan RPM (big-endian u16)
    byte 16..17 GPU power W (big-endian u16)
    byte 18     RAM usage %
    byte 19..20 total RAM MiB (big-endian u16)
    byte 21     disk free %
    byte 22..23 total disk GB (big-endian u16)
    byte 24..64 zero

The panel reads byte 4..5 for the "RPM" field while the original app puts the CPU
clock there, so by default the two values are swapped so the panel shows a real fan
speed; --vendor-layout restores the original placement.

Observed ceiling: the interrupt OUT endpoint (bInterval 160 ms) accepts about
8 reports/s and each hidraw write blocks until its URB completes.

Plug and play: with --wait the process stays alive, waits for the device to appear
and keeps streaming through unplug/replug, which is what the systemd unit uses.

    python gamemax_report.py --dry-run --cpu-temp 55
    python gamemax_report.py --wait                 # service mode
    python gamemax_report.py --list-sensors         # JSON for the GUI
"""
import argparse
import glob
import json
import os
import select
import signal
import sys
import threading
import time

VID, PID = 0x5131, 0x2007
REPORT_SIZE = 65
CONFIG_PATH = os.path.expanduser("~/.config/gamemax-report/config.json")
VALUE_KEYS = ("cpu_temp", "gpu_temp", "cpu_load", "cpu_clock", "cpu_fan", "cpu_power",
              "gpu_load", "gpu_clock", "gpu_fan", "gpu_power",
              "ram_load", "ram_mb", "disk_free", "disk_gb")
CONFIG_KEYS = ("fan_channel", "temp_channel", "unit", "interval", "sample",
               "flag_celsius", "vendor_layout", "disk_path", "blank")


def load_config(path=CONFIG_PATH):
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def u16(value):
    v = max(0, min(0xFFFF, int(value or 0)))
    return bytes((v >> 8, v & 0xFF))


def temp_byte(value, unit, flag_celsius=False):
    """Wire values are Celsius 0..127.

    The original app ORs 0x80 when the UI unit is Celsius; the IceBurg panel rejects
    values >= 128 (it keeps showing the last accepted one), so we only do that when
    --flag-celsius is requested. --unit f converts the supplied Fahrenheit values.
    """
    if value is None:
        return 0
    # The panel shows at most two digits and its unit label is fixed to Celsius, so
    # values above 99 are clamped (verified on hardware); --unit f sends the number
    # as-is for fidelity with the original app, but the panel still labels it °C.
    v = max(0, min(99, int(round(value))))
    return v | 0x80 if flag_celsius else v


def build_report(v, unit, flag_celsius=False, vendor_layout=False):
    r = bytearray(REPORT_SIZE)
    clock, fan = v.get("cpu_clock"), v.get("cpu_fan")
    if not vendor_layout:  # this panel renders byte 4..5 in the slot labelled RPM
        clock, fan = fan, clock
    r[0] = 0
    r[1] = 64
    r[2] = temp_byte(v.get("cpu_temp"), unit, flag_celsius)
    r[3] = int(v.get("cpu_load") or 0) & 0xFF
    r[4:6] = u16(clock)
    r[6:8] = u16(fan)
    r[8:10] = u16(v.get("cpu_power"))
    r[10] = temp_byte(v.get("gpu_temp"), unit, flag_celsius)
    r[11] = int(v.get("gpu_load") or 0) & 0xFF
    r[12:14] = u16(v.get("gpu_clock"))
    r[14:16] = u16(v.get("gpu_fan"))
    r[16:18] = u16(v.get("gpu_power"))
    r[18] = int(v.get("ram_load") or 0) & 0xFF
    r[19:21] = u16(v.get("ram_mb"))
    r[21] = int(v.get("disk_free") or 0) & 0xFF
    r[22:24] = u16(v.get("disk_gb"))
    return bytes(r)


def self_check():
    """Lock the protocol shape the original enforces."""
    r = build_report({"cpu_temp": 60, "gpu_temp": 70, "cpu_clock": 5000, "cpu_fan": 1200,
                      "cpu_power": 88, "gpu_power": 250, "ram_load": 42, "ram_mb": 32768,
                      "disk_free": 30, "disk_gb": 1951}, "c")
    assert len(r) == REPORT_SIZE, len(r)
    assert r[0] == 0 and r[1] == 64, r[:2].hex()
    assert r[2] == 60 and r[10] == 70, (r[2], r[10])                 # plain Celsius, no flag
    flagged = build_report({"cpu_temp": 60}, "c", flag_celsius=True)
    assert flagged[2] == 0xBC, hex(flagged[2])                       # original app's byte
    assert r[4:6] == b"\x04\xb0" and r[6:8] == b"\x13\x88"           # fan in RPM slot, BE
    vendor = build_report({"cpu_clock": 5000, "cpu_fan": 1200}, "c", vendor_layout=True)
    assert vendor[4:6] == b"\x13\x88" and vendor[6:8] == b"\x04\xb0"  # original placement
    assert r[8:10] == b"\x00\x58" and r[16:18] == b"\x00\xfa"
    assert r[18] == 42 and r[19:21] == b"\x80\x00"                   # 32768 MiB
    assert r[21] == 30 and r[22:24] == b"\x07\x9f"                   # 1951 GB
    assert r[24:] == bytes(41)
    assert build_report({"cpu_temp": 100}, "f")[2] == 99             # clamped to two digits
    assert build_report({"cpu_temp": 77}, "f")[2] == 77              # Fahrenheit sent as-is
    return r


# ---------------------------------------------------------------- hwmon access

def hwmon_read(path, scale=1.0):
    try:
        with open(path) as fh:
            return float(fh.read().strip()) / scale
    except (OSError, ValueError):
        return None


def hwmon_channels(pattern="fan*_input"):
    """List every hwmon channel as {"chip", "file", "label", "value"}."""
    out = []
    for path in sorted(glob.glob(f"/sys/class/hwmon/hwmon*/{pattern}")):
        base, fname = os.path.dirname(path), os.path.basename(path)
        try:
            chip = open(os.path.join(base, "name")).read().strip()
        except OSError:
            continue
        label = None
        try:
            label = open(os.path.join(base, fname.replace("_input", "_label"))).read().strip()
        except OSError:
            pass
        out.append({"chip": chip, "file": fname, "label": label,
                    "value": hwmon_read(path)})
    return out


def channel_value(spec, scale=1.0):
    """Read an explicit channel like "nct6798/fan2_input"."""
    if not spec or "/" not in spec:
        return None
    chip, fname = spec.split("/", 1)
    for path in glob.glob(f"/sys/class/hwmon/hwmon*/{fname}"):
        try:
            if open(os.path.join(os.path.dirname(path), "name")).read().strip() != chip:
                continue
        except OSError:
            continue
        return hwmon_read(path, scale)
    return None


def hwmon_max(chips, pattern="temp*_input", scale=1.0):
    """Highest matching reading; a CPU exposes Tctl plus per-CCD Tccd sensors."""
    best = None
    for ch in hwmon_channels(pattern):
        if not any(c == "*" or c in ch["chip"] for c in chips):
            continue
        if ch["value"] is None:
            continue
        value = ch["value"] / scale
        if best is None or value > best:
            best = value
    return best


CPU_CHIPS = ["k10temp", "coretemp", "zenpower", "cpu_thermal"]
GPU_CHIPS = ["amdgpu", "nouveau", "i915"]


def cpu_temp(cfg):
    scale = 1000.0
    explicit = channel_value(cfg.get("temp_channel"), scale)
    if explicit is not None:
        return explicit
    return (hwmon_max(CPU_CHIPS, "temp*_input", scale)
            or hwmon_max(CPU_CHIPS, "temp1_input", scale))


def cpu_fan(cfg):
    explicit = channel_value(cfg.get("fan_channel"))
    if explicit is not None:
        return explicit
    fans = [c["value"] for c in hwmon_channels("fan*_input")
            if c["value"] and c["chip"] not in GPU_CHIPS]
    return max(fans) if fans else 0


def meminfo(key):
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith(key):
                    return float(line.split()[1])  # kB
    except OSError:
        pass
    return None


def stat_snapshot():
    with open("/proc/stat") as fh:
        p = [int(x) for x in fh.readline().split()[1:]]
    return p[3] + p[4], sum(p)


def load_pct(prev, now):
    idle, total = prev
    idle2, total2 = now
    return None if total2 == total else round(100 * (1 - (idle2 - idle) / (total2 - total)))


def read_sensors(prev=None, cfg=None):
    cfg = cfg or {}
    total_kb, avail_kb = meminfo("MemTotal"), meminfo("MemAvailable")
    try:
        st = os.statvfs(cfg.get("disk_path") or "/")
        disk_free = round(100 * st.f_bavail / st.f_blocks) if st.f_blocks else None
        disk_gb = st.f_blocks * st.f_frsize / 2**30
    except OSError:
        disk_free = disk_gb = None
    return {
        "cpu_temp": cpu_temp(cfg),                  # Celsius; temp_byte handles the unit
        "gpu_temp": hwmon_max(GPU_CHIPS, "temp*_input", 1000.0),
        "cpu_load": None if prev is None else load_pct(prev, stat_snapshot()),
        "cpu_clock": hwmon_read("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", 1000.0),
        "cpu_fan": cpu_fan(cfg),
        "cpu_power": hwmon_max(CPU_CHIPS, "power*_input", 1e6),
        "gpu_clock": (hwmon_max(GPU_CHIPS, "freq*_input", 1e6)
                      or hwmon_read("/sys/class/drm/card0/device/pp_dpm_sclk")),
        "gpu_fan": hwmon_max(GPU_CHIPS, "fan*_input") or 0,
        "gpu_load": hwmon_max(GPU_CHIPS, "gpu_busy_percent"),
        "gpu_power": hwmon_max(GPU_CHIPS, "power*_input", 1e6),
        "ram_load": None if not (total_kb and avail_kb) else round(100 * (1 - avail_kb / total_kb)),
        "ram_mb": None if not total_kb else total_kb / 1024,
        "disk_free": disk_free,
        "disk_gb": disk_gb,
    }


class Sampler(threading.Thread):
    """Refresh sensor values in the background so the send cadence stays independent."""

    def __init__(self, interval, cfg=None):
        super().__init__(daemon=True)
        self.interval = interval
        self.cfg = cfg or {}
        self.values = {}
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.prev = stat_snapshot()

    def run(self):
        while not self.stop.is_set():
            now = stat_snapshot()
            v = read_sensors(self.prev, self.cfg)
            self.prev = now
            with self.lock:
                self.values = v
            self.stop.wait(self.interval)

    def snapshot(self):
        with self.lock:
            return dict(self.values)


def find_device():
    """Return the hidraw node whose HID_ID reports VID:PID 5131:2007."""
    want = f"{VID:08X}:{PID:08X}"
    for uevent in sorted(glob.glob("/sys/class/hidraw/hidraw*/device/uevent")):
        try:
            text = open(uevent).read()
        except OSError:
            continue
        if want in text.upper():
            return "/dev/" + uevent.split("/")[4]
    return None


def stream(a, overrides):
    """Open the device and stream reports. Returns (sent, seconds)."""
    node = a.device or find_device()
    if not node:
        return None
    fd = os.open(node, os.O_RDWR)
    print(f"terbuka {node} ({VID:#06x}:{PID:#06x})", flush=True)
    sampler = Sampler(a.sample, {"fan_channel": a.fan_channel, "temp_channel": a.temp_channel})
    sampler.start()
    time.sleep(min(a.sample, 0.5))  # wait for the first sample
    sent, t0 = 0, time.time()
    try:
        while a.count == 0 or sent < a.count:
            v = {} if a.blank else sampler.snapshot()
            v.update(overrides)
            os.write(fd, build_report(v, a.unit, a.flag_celsius, a.vendor_layout)[1:])
            sent += 1
            time.sleep(a.interval)
    finally:
        sampler.stop.set()
        os.close(fd)
    return sent, time.time() - t0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the report instead of sending it")
    ap.add_argument("--listen", type=float, metavar="SECONDS",
                    help="passively print device->host reports for this long")
    ap.add_argument("--device", help="hidraw node; default: auto-detect by VID:PID")
    ap.add_argument("--unit", choices=["c", "f"], help="unit of the values you supply")
    ap.add_argument("--interval", type=float, help="seconds between reports (device caps at ~8/s)")
    ap.add_argument("--sample", type=float, help="sensor refresh interval")
    ap.add_argument("--count", type=int, default=0, help="stop after N reports (0 = run until Ctrl-C)")
    ap.add_argument("--flag-celsius", action="store_true", default=None,
                    help="emit the original app's 0x80 Celsius flag (this firmware ignores it)")
    ap.add_argument("--vendor-layout", action="store_true", default=None,
                    help="original app's byte order (clock lands in the panel's RPM slot)")
    ap.add_argument("--fan-channel", help='explicit fan channel, e.g. "nct6798/fan2_input"')
    ap.add_argument("--temp-channel", help='explicit temp channel, e.g. "k10temp/temp3_input"')
    ap.add_argument("--blank", action="store_true", default=None,
                    help="kirim report bernilai nol (mode 'layar kosong')")
    ap.add_argument("--wait", action="store_true",
                    help="stay alive and wait for the device (plug and play, service mode)")
    ap.add_argument("--list-sensors", action="store_true",
                    help="print available fan/temp channels as JSON and exit")
    ap.add_argument("--config", default=CONFIG_PATH, help="JSON config path")
    for key in VALUE_KEYS:
        ap.add_argument(f"--{key.replace('_', '-')}", type=float, default=None)
    a = ap.parse_args()

    if a.list_sensors:
        print(json.dumps({
            "fans": hwmon_channels("fan*_input"),
            "temps": hwmon_channels("temp*_input"),
            "cpu_chips": CPU_CHIPS,
            "gpu_chips": GPU_CHIPS,
            "device": find_device(),
            "config": load_config(a.config),
        }, indent=2))
        return 0

    print("pemeriksaan format:", self_check().hex(" "), flush=True)

    # config provides defaults; explicit flags win
    cfg = load_config(a.config)
    for key in CONFIG_KEYS:
        if getattr(a, key, None) is None and cfg.get(key) is not None:
            setattr(a, key, cfg[key])
    a.unit = a.unit or "c"
    a.interval = 0.5 if a.interval is None else a.interval
    a.sample = 0.5 if a.sample is None else a.sample
    a.flag_celsius = bool(a.flag_celsius)
    a.vendor_layout = bool(a.vendor_layout)
    a.blank = bool(a.blank)

    overrides = {k: getattr(a, k) for k in VALUE_KEYS if getattr(a, k) is not None}

    if a.dry_run:
        v = {} if a.blank else read_sensors(stat_snapshot(), {"fan_channel": a.fan_channel,
                                                              "temp_channel": a.temp_channel})
        v.update(overrides)
        print("nilai:", {k: (round(x, 1) if isinstance(x, float) else x)
                          for k, x in sorted(v.items())})
        print("isi report:", build_report(v, a.unit, a.flag_celsius, a.vendor_layout).hex(" "))
        return 0

    if a.listen:
        node = a.device or find_device()
        if not node:
            print(f"node hidraw untuk {VID:#06x}:{PID:#06x} tidak ditemukan", file=sys.stderr)
            return 1
        fd = os.open(node, os.O_RDONLY | os.O_NONBLOCK)
        print(f"mendengarkan di {node} selama {a.listen} detik")
        deadline, seen = time.time() + a.listen, 0
        try:
            while time.time() < deadline:
                if select.select([fd], [], [], 0.3)[0]:
                    seen += 1
                    print(f"  report {seen}: {os.read(fd, 64).hex(' ')}")
        finally:
            os.close(fd)
        print(f"report diterima: {seen}")
        return 0

    if not a.wait:
        result = stream(a, overrides)
        if result is None:
            print(f"node hidraw untuk {VID:#06x}:{PID:#06x} tidak ditemukan", file=sys.stderr)
            return 1
        sent, dt = result
        print(f"terkirim {sent} report ({sent / dt:.1f}/detik)")
        return 0

    # plug and play: wait for the device, stream, and go back to waiting on unplug
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(f"menunggu {VID:#06x}:{PID:#06x} (Ctrl-C atau SIGTERM untuk berhenti)", flush=True)
    while not stop.is_set():
        if not find_device():
            stop.wait(2)
            continue
        try:
            result = stream(a, overrides)
            if result:
                sent, dt = result
                print(f"perangkat terlepas setelah {sent} report ({sent / dt:.1f}/detik), menunggu lagi", flush=True)
        except OSError as exc:
            print(f"galat perangkat ({exc}), menunggu lagi", flush=True)
        stop.wait(2)
    print("berhenti")
    return 0


if __name__ == "__main__":
    sys.exit(main())
