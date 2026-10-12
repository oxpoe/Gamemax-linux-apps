# GAMEMAX PcInfoMonitorPro — Recon & HID Protocol

Target: `GAMEMAX_V1.0.0/app/` (installer `GAMEMAX_V1.0.0.04(1).exe`)
Analisis: 2026-10-13, REA 6.4.0 + Ghidra 12.1.2 (managed static provider), IL dumper lokal.

## 1. Isi target

| File | Peran | Teknologi |
|---|---|---|
| `PcInfoMonitorPro.exe` | Aplikasi utama (GUI) | PE32 mixed-mode C++/CLI + Qt5, **logika = MSIL** (457 method body) |
| `allComputerInfoGetPro.exe` | Helper console (dipanggil terpisah) | mixed-mode C++/CLI, sebagian besar CRT |
| `WitmodHardwareInfoLibrary.dll` | Model data sensor (CPU/GPU/mobo) | .NET Framework, CIL murni |
| `LibreHardwareMonitorLib.dll` | Sumber data sensor | OSS pihak ketiga (bukan kode vendor) |
| `HidSharp.dll` | HID library .NET | OSS pihak ketiga (tidak dipakai jalur utama) |
| `hidapi.dll` | I/O HID | OSS, **dipakai** untuk buka/tulis device |
| `Qt5*.dll`, `opengl32sw.dll`, `D3Dcompiler_47.dll` | Framework UI | Qt 5 native |
| `configDir/appConfig.ini` | Konfigurasi app | `[AppInfo] AppName=GAMEMAX, AppVersion=V_1.0.0.04, AppID=ABCDEF6543A1`, warna tema, font Microsoft YaHei |

Catatan: vendor internal tampil sebagai `MyCompanyWitmod` / `WITMOD`; GAMEMAX adalah merek OEM-nya.

## 2. Arsitektur logika

Semua logika aplikasi dikompilasi ke **MSIL** (mixed-mode `/clr`), sehingga
decompile native (Ghidra) pada metode aplikasi menghasilkan sampah. Metode
dideklarasikan sebagai fungsi global bernama bertitik, mis.
`MainHidInterFace.hidApi_Write`, `DMainWidget.onGetPcInfoOk`, di bawah tipe `<Module>`.
Native hanya: Qt DLL, hidapi, CRT.

Alur HID:
```
DMainWidget.onGetPcInfoOk   (bangun report 65 byte dari sensor)
  -> MainHidInterFace.hidApi_Write   (mutex + copy 65 B + QList enqueue)
      -> MainHidInterFace.run        (thread: QList.takeFirst -> hid_write, poll 500 us)
```

## 3. Device target & pembukaan

`MainHidInterFace.getuUsbHidDeviceList`:
1. `hid_enumerate(0, 0)` — semua HID
2. filter `vendor_id == 20785 (0x5131)` **dan** `product_id == 8199 (0x2007)`
3. ambil `path` (offset 0, string ASCII) → `hid_open_path`
4. daftar device aktif dimuat via `RegisterDeviceNotificationW` (plug/unplug, sinyal
   `sigHidDeviceConnectChanging_Into/Remove`, `sigOpenHidSuccessful`)

Tulis **satu arah**: tidak ada `hid_read*` yang diimpor — app hanya mengirim.

## 4. Protokol tulis (terverifikasi dari IL)

- Transfer `hid_write(handle, buf, 65)`: 65 byte = 1 byte report ID + 64 byte payload
- `buf[0] = 0` (report ID 0)
- `buf[1] = 64` (0x40, panjang payload)
- Nilai 16-bit **big-endian** (high byte dulu)
- Suhu: jika checkbox unit tercentang → nilai dipakai apa adanya; jika tidak →
  `round((v - 32) / 1.8) | 0x80`. **Bit 0x80 menandakan nilai Fahrenheit.**
- Pengiriman berulang tiap 500 us selama device terbuka

### Layout report (dari `DMainWidget.onGetPcInfoOk`)

| Offset | Isi | Sumber |
|---|---|---|
| 0 | 0 | report ID |
| 1 | 64 | panjang payload |
| 2 | CPU temp | `GetCpu_Temperature` (+flag 0x80 bila °F) |
| 3 | CPU load | `GetCpu_Load` |
| 4..5 | CPU clock (MHz, BE16) | `GetCpu_Clock` |
| 6..7 | CPU fan (RPM, BE16) | `GetCpu_Fan` |
| 8..9 | BE16 dari struct lokal | belum dipetakan |
| 10 | GPU temp | `GetGpu_Temperature` (+flag 0x80) |
| 11 | GPU load | `GetGpu_CoreLoad` |
| 12..13 | GPU clock (BE16) | `GetGpu_CoreClock` |
| 14..15 | GPU fan (BE16) | `GetGpu_Fan` |
| 16..17 | GPU power (BE16) | `GetGpu_Power` |
| 18 | u8 dari struct lokal | belum dipetakan |
| 19..20 | BE16 dari struct lokal | belum dipetakan |
| 21 | u8 dari struct lokal | belum dipetakan |
| 22..23 | BE16 dari struct lokal | belum dipetakan |
| 24..64 | 0 | tidak ditulis jalur ini |

Nilai sensor berasal dari `WitmodHardwareInfoLibrary.Monitor` (wrapper tipis di atas
LibreHardwareMonitor): CPU/GPU Temperature, Load, CoreLoad, Clock, Clock_Max, Fan,
Fan_Max, Power, Power_Max.

## 5. Bukti & artefak

- `analysis/evidence/hid-write-protocol.json` — Evidence bundle REA (12 records, 5.5 MB)
- `analysis/snapshots/pcinfomonitorpro.json` — snapshot Ghidra (bisa dibuka ulang tanpa import)
- `analysis/tools/ildump.py` — disassembler IL (dnfile+dncil) untuk membaca MSIL dengan
  operand terselesaikan; venv: `uv venv ilenv && uv pip install --python ilenv/bin/python dnfile dncil`

## 6. Hasil lanjutan

### 6.1 Hanya ada satu jenis perintah HID
Seluruh IL (457 body) dipindai: setiap fungsi hidapi dipanggil tepat sekali
(`hid_init`, `hid_enumerate`, `hid_free_enumeration`, `hid_open_path`,
`hid_set_nonblocking`, `hid_write`, `hid_close`, `hid_exit`) dan `hidApi_Write`
hanya punya **satu pemanggil** (`DMainWidget.onGetPcInfoOk`). Tidak ada
`hid_read*`/`hid_get_feature_report` — tidak ada perintah lain (brightness,
mode, dll.) di jalur ini.

### 6.2 Auto-start + self-elevation (Task Scheduler)
`DMainWidget.initThemeData` (fungsi startup besar, offset IL ~4380-4520):
1. Menulis penanda ke QSettings: `StartupTaskOKver---`
2. `ShellExecuteW(verb="runas", file="schtasks", params=`<br>
   `/create /tn "%1" /tr "\"%2\" %3" /sc onlogon /rl highest`
   - `%2` = `QCoreApplication.applicationFilePath()` (path exe sendiri)
   - `%3` = `---StartupByTaskMinWindow` (penanda argumen)
   - verb `runas` → UAC sekali di awal, task berjalan dengan token tertinggi tiap logon
3. `main` memindai `argv` untuk `---StartupByTaskMinWindow`, lalu mencetak
   "Program was started by Task Scheduler." / "Program was not started by Task Scheduler."
   dan memulai aplikasi terminimalkan
4. Manifest exe: `requestedExecutionLevel level='requireAdministrator'`

Efek: aplikasi otomatis berjalan sebagai admin tanpa prompt UAC di setiap logon.

### 6.3 `allComputerInfoGetPro.exe` = stub test
`main`: `new Monitor(); GetComputerInfo(); Sleep(500); printf("Over ... ..."); getchar();`
— harness uji developer untuk `WitmodHardwareInfoLibrary`, bukan bagian alur runtime.
Nama file ini **tidak dirujuk** oleh PcInfoMonitorPro.exe (ASCII maupun UTF-16) dan
tidak ada di installer.

### 6.4 Layout report — LENGKAP (terverifikasi di device)

| Offset | Isi | Sumber di aplikasi | Terverifikasi |
|---|---|---|---|
| 0 | 0 | report ID | ✅ |
| 1 | 64 | panjang payload | ✅ |
| 2 | CPU temp (0..127) | `GetCpu_Temperature` | ✅ tampil di layar |
| 3 | CPU load % | `GetCpu_Load` | ✅ |
| 4..5 | CPU clock MHz (BE16) | `GetCpu_Clock` | ✅ — **device merender byte ini di slot "RPM"** |
| 6..7 | CPU fan RPM (BE16) | `GetCpu_Fan` | ✅ (device tidak menampilkannya di halaman ini) |
| 8..9 | **CPU power W** (BE16) | `GetCpu_Power` | ✅ |
| 10 | GPU temp (0..127) | `GetGpu_Temperature` | ✅ |
| 11 | GPU load % | `GetGpu_CoreLoad` | ✅ |
| 12..13 | GPU clock MHz (BE16) | `GetGpu_CoreClock` | ✅ |
| 14..15 | GPU fan RPM (BE16) | `GetGpu_Fan` | ✅ |
| 16..17 | GPU power W (BE16) | `GetGpu_Power` | ✅ |
| 18 | RAM usage % | `GlobalMemoryStatusEx` → `dwMemoryLoad` | ✅ |
| 19..20 | total RAM MiB (BE16) | `ullTotalPhys` × 2⁻²⁰ | ✅ |
| 21 | disk free % | `GetDiskFreeSpaceExW` (free/total × 100) | ✅ |
| 22..23 | total disk GB (BE16) | total bytes >> 30 | ✅ |
| 24..64 | nol | tidak dipakai | ✅ |

Catatan pembalikan dari dugaan awal: byte 8..9 **bukan** counter waktu melainkan
CPU power; 19..20 dan 22..23 adalah RAM total dan ukuran disk, bukan waktu.

### 6.5 Perilaku firmware yang ditemukan saat pengujian

1. **Nilai ≥ 128 ditolak.** Aplikasi asli menulis suhu Celsius dengan bit `0x80`
   aktif. Firmware IceBurg **mengabaikan field itu** dan mempertahankan nilai terakhir
   yang valid (pengujian: 99 °C mentok sampai nilai tanpa flag dikirim). Jadi kirim
   suhu 0..127 tanpa flag.
2. **Kedip = indikator "nilai tidak berubah".** Saat semua field statis, angka tengah
   berkedip ~1×/detik; saat nilai berubah (suhu naik 40→90) kedip hilang. Bukan efek
   laju kirim — kedip tetap ada walau kita berhenti mengirim.
3. **Plafon laju ~8 report/detik.** Endpoint interrupt OUT `bInterval 160 ms`; setiap
   `write()` hidraw menunggu URB selesai (~125 ms). Aplikasi asli menumpuk antrean dari
   loop 500 µs (`QTimer.setInterval(10)` + `QThread.usleep(500)`), efektifnya sama.
4. **Selalu "streaming"**, tidak ada handshake/init: buka lalu tulis 64 byte.
5. **Slot RPM salah byte.** Diuji silang di hardware: kirim clock=4000 dan fan=1500 →
   layar menampilkan 4000 di slot RPM. Jadi firmware memakai byte 4..5 untuk slot itu,
   padahal aplikasi asli menaruh clock CPU di sana (bug pasangan app/firmware).
   Implementasi pengganti menukar clock/fan agar layar menampilkan RPM fan sungguhan.
6. **Akurasi dibandingkan system monitor**: suhu cocok (selisih hanya pembulatan ke
   integer), load cocok; keduanya tampil dengan delay beberapa detik akibat jendela
   sampling dan laju device.

## 7. Implementasi pengganti (jalan di host ini)

`analysis/tools/gamemax_report.py` — Python murni, tanpa dependensi:
- auto-detect node hidraw dari VID:PID (`0x5131:0x2007`)
- baca sensor Linux: hwmon (CPU = maksimum Tctl/Tccd*, GPU, fan, power), `/proc/stat`,
  cpufreq, `/proc/meminfo`, `statvfs("/")`
- bangun report 65 byte sesuai tabel di 6.4, tulis 64 byte payload ke hidraw
- `--dry-run` mencetak report + nilai; `--listen N` membaca pesan device→host
  (device ini tidak pernah mengirim apa pun); `--count`, `--interval`, `--unit c|f`,
  `--flag-celsius` (fidelitas ke aplikasi asli), override tiap field via `--cpu-temp` dll.
- `self_check()` mengunci header, urutan big-endian, dan semantik satuan

`analysis/tools/70-gamemax.rules` — udev rule `TAG+="uaccess"` untuk node hidraw.
`analysis/tools/gamemax-report.service` — unit systemd **user** (padanan
`schtasks /sc onlogon` aplikasi asli), sudah aktif di mesin ini:

```bash
systemctl --user status gamemax-report.service
journalctl --user -u gamemax-report -f
systemctl --user disable --now gamemax-report.service   # matikan
```

Uji yang sudah lolos: dry-run + assert, kirim nyata, device menampilkan load/RPM/suhu
sesuai kiriman, dan tampilan mengikuti perubahan nilai.

## 8. Device: GameMax IceBurg 240 Digital BK

Ditemukan di mesin ini sebagai `Bus 001 Device 005: ID 5131:2007` (usb.ids salah
menamainya "MSR-101U card reader" — descriptor-nya vendor-defined 0xFF00, 64 byte IN
+ 64 byte OUT, tanpa report ID). Produknya: **GameMax IceBurg 240 Digital BK**, AIO
240 mm dengan blok LCD; "IceBurg 240 Digital Software" dari halaman produk
(gamemaxpc.com/cpu-liquid-cooler/1550.html) adalah `PcInfoMonitorPro.exe` yang kita
bongkar. Layar menampilkan halaman CPU (ikon CPU + load %, suhu besar, RPM, 6 titik
indikator halaman) dan menganimasikan busur kiri/kanan.

Verifikasi: setelah `gamemax-report.service` aktif, device menampilkan suhu/load/RPM
dari sensor Linux, mengikuti perubahan nilai, dan berkedip hanya saat nilai statis
(perilaku firmware, disepakati dibiarkan apa adanya).
