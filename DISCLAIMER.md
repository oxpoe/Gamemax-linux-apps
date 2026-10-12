# Disclaimer

**Bahasa Indonesia**

Aplikasi ini dibuat oleh **oxpoe** semata-mata untuk **kompatibilitas Linux dengan
perangkat keras** (khususnya layar LCD pada AIO cooler). Tujuannya agar pengguna
Linux bisa memakai perangkat yang secara resmi hanya didukung aplikasi Windows.

1. **Tidak berafiliasi** dengan GAMEMAX, GameMax, atau vendor mana pun. Proyek ini
   tidak disponsori, tidak didukung, dan tidak berhubungan dengan pembuat perangkat.
2. **Dibuat untuk interoperabilitas.** Protokolnya dipelajari secara independen dari
   perilaku perangkat dan dari aplikasi vendor yang sudah terpasang di komputer
   penulis (rekayasa balik untuk kompatibilitas). Tidak ada kode, berkas, atau aset
   milik vendor yang disertakan dalam repositori ini.
3. **Merek dagang** seperti GAMEMAX, GameMax, IceBurg, serta nama produk lain
   adalah milik pemegangnya masing-masing. Ikon aplikasi di repositori ini adalah
   karya orisinal, bukan logo vendor.
4. **Risiko ditanggung pengguna.** Perangkat lunak ini menulis data ke perangkat USB
   HID. Penulis telah mengujinya pada perangkat miliknya sendiri, tetapi tidak ada
   jaminan apa pun — termasuk jaminan kelayakan untuk tujuan tertentu. Gunakan dengan
   risiko Anda sendiri.
5. **Batasan perangkat.** Perangkat lunak ini hanya mengirim data sensor. Panel
   **tidak bisa dimatikan** dan **logo pada panel tidak bisa diganti** lewat USB —
   keduanya urusan firmware vendor, bukan sesuatu yang bisa dilakukan proyek ini.
   Nilai yang ditampilkan panel terbatas dua digit dan berlabel °C.
6. **Diuji pada** panel GAMEMAX IceBurg 240 Digital (HID `5131:2007`) di CachyOS
   (Arch), kernel 7.2.9, KDE Plasma Wayland, Python 3.14. Revisi firmware lain bisa
   berbeda perilakunya; uji dulu dengan `--dry-run` (tidak menulis apa pun) sebelum
   dipakai rutin.
7. **Tanpa jaminan.** Perangkat lunak disediakan "sebagaimana adanya" di bawah
   lisensi MIT; lihat `LICENSE`.
6. **Perkakas pihak ketiga** yang dipakai saat analisis (mis. REA, Ghidra, PySide6)
   tetap tunduk pada lisensinya masing-masing dan tidak didistribusikan ulang di sini.

**English**

This project is made by **oxpoe** purely for **Linux hardware compatibility** —
so that Linux users can drive the LCD panel of a device that is officially supported
only by a Windows application.

- Not affiliated with, sponsored by, or endorsed by GAMEMAX/GameMax or any vendor.
- Independently reverse engineered for interoperability. No vendor code, binaries,
  or assets are redistributed; the application icon is original artwork.
- Trademarks belong to their respective owners.
- The panel cannot be powered off and its logo cannot be changed over USB; those are
  firmware features of the vendor. Displayed values are limited to two digits and are
  labelled °C by the firmware.
- The software writes to a USB HID device. Tested with a GAMEMAX IceBurg 240 Digital
  panel (HID `5131:2007`) on CachyOS/Arch, kernel 7.2.9, KDE Wayland, Python 3.14.
  Other firmware revisions may behave differently — inspect with `--dry-run` (writes
  nothing) before relying on it. Use at your own risk, provided "as is", without
  warranty of any kind (MIT).
- Third-party analysis tools keep their own licenses.
