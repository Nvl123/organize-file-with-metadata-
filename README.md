# Media Metadata Organizer (Pengelompok Media Otomatis)

Skrip Python otomatis untuk membaca metadata tanggal dari file foto dan video, kemudian mengorganisirnya ke dalam struktur folder berdasarkan **Hari** (`Senin` - `Sabtu`/`Minggu`), sub-folder **Tanggal** (`YYYY-MM-DD`), serta folder khusus **`miscellaneous`** untuk file tanpa metadata.

---

## 📋 Fitur Utama

- **Dukungan Format Gambar Luas**: Mendukung format umum seperti `.jpg`, `.jpeg`, `.png`, `.webp`, `.tiff`, serta format foto Apple iPhone terbaru (`.heic`) dan format RAW kamera (`.cr2`, `.nef`, `.arw`).
- **Ekstraksi Metadata Video Native**: Mampu membaca metadata atom QuickTime/MP4 (`moov` -> `creationdate` / `mvhd`) secara langsung tanpa perlu menginstal aplikasi pihak ketiga seperti *FFmpeg* atau *ffprobe*.
- **Struktur Folder Teratur**:
  - Tingkat 1: Nama hari dalam Bahasa Indonesia (`Senin`, `Selasa`, `Rabu`, `Kamis`, `Jumat`, `Sabtu`, `Minggu`).
  - Tingkat 2: Tanggal pengambilan media (format standar `YYYY-MM-DD`, dapat disesuaikan).
- **Folder Khusus File Tanpa Metadata**: Gambar atau video yang tidak memiliki metadata EXIF/Creation Date (misalnya hasil unduhan dari WhatsApp/media sosial) otomatis dipisahkan ke folder `miscellaneous`.
- **Aman dari Penimpaan File (Collision-Safe)**: Jika ada file dengan nama yang sama di folder tujuan, skrip otomatis menambahkan akhiran numerik (`_1`, `_2`, dst.) agar tidak ada data yang hilang atau tertimpa.
- **Mode Simulasi (Dry Run)**: Memungkinkan peninjauan rencana pemindahan sebelum file benar-benar dipindahkan.
- **Pilihan Move atau Copy**: Mendukung pemindahan file asli (*move*) maupun penyalinan (*copy*).

---

## 📂 Contoh Struktur Folder yang Dihasilkan

```text
Codero/
├── organize_media.py
├── README.md
├── miscellaneous/                  <-- File tanpa metadata tanggal
│   ├── 0d17b5cd-....jpg
│   └── 74bc3902-....jpg
├── Senin/
│   ├── 2026-08-24/
│   │   ├── IMG_2040.MOV
│   │   └── IMG_2041.HEIC
│   ├── 2026-08-31/
│   └── 2026-09-07/
├── Selasa/
│   ├── 2026-09-15/
│   └── 2026-09-22/
├── Rabu/
│   ├── 2026-09-02/
│   └── 2026-09-09/
├── Kamis/
│   ├── 2026-08-20/
│   └── 2026-08-27/
├── Jumat/
│   ├── 2026-09-18/
│   └── 2026-10-02/
└── Sabtu/
    ├── 2026-08-22/
    └── 2026-09-05/
```

---

## 🛠️ Kebutuhan Sistem & Instalasi

Pastikan telah menginstal **Python 3.10** ke atas. Instal pustaka pendukung berikut:

```bash
pip install -r requirements.txt
```

*(Atau instal secara manual: `pip install pillow pillow-heif`)*

> **Catatan**: 
> - `pillow`: Digunakan untuk membaca metadata EXIF gambar standar (JPG, PNG, TIFF).
> - `pillow-heif`: Memungkinkan Python membaca metadata foto resolusi tinggi Apple iPhone (`.HEIC`).

---

## 🚀 Cara Penggunaan

Simpan file skrip `organize_media.py` di dalam folder yang berisi media Anda, lalu buka terminal/PowerShell di direktori tersebut:

### 1. Simulasi Terlebih Dahulu (Dry Run)
Direkomendasikan menjalankan mode simulasi terlebih dahulu untuk melihat kemana file akan diarahkan tanpa mengubah apa pun:

```bash
python organize_media.py --dry-run
```

### 2. Memindahkan File (Mode Default)
Untuk langsung mengelompokkan dan memindahkan file ke dalam folder hari dan tanggal:

```bash
python organize_media.py
```
*(Atau dengan opsi eksplisit: `python organize_media.py --move`)*

### 3. Menyalin File (Mode Copy)
Jika ingin menyalin file dan tetap mempertahankan file asli di lokasi semula:

```bash
python organize_media.py --copy
```

---

## ⚙️ Konfigurasi Tambahan

Anda dapat mengedit beberapa konstanta di bagian atas skrip [`organize_media.py`](organize_media.py) sesuai kebutuhan:

| Variabel | Default | Keterangan |
|---|---|---|
| `SOURCE_DIR` | `Path(".")` | Lokasi folder media yang ingin dipindai. |
| `OPERATION_MODE` | `"move"` | Mode aksi bawaan (`"move"` atau `"copy"`). |
| `MISC_FOLDER` | `"miscellaneous"` | Nama folder untuk file tanpa metadata. |
| `DATE_FOLDER_FORMAT` | `"%Y-%m-%d"` | Format penamaan subfolder tanggal (misal: `"%d-%m-%Y"` untuk `07-09-2026`). |

---

## 🛡️ Penanganan Error & Keamanan Data

1. **File Khusus Dilewati**: Skrip `organize_media.py` dan `README.md` tidak akan tersentuh atau dipindahkan.
2. **Folder Terorganisir Diabaikan**: Jika skrip dijalankan ulang, folder hari (`Senin`, `Selasa`, dst.) tidak akan dipindahkan secara rekursif ke dalam dirinya sendiri.
3. **Penyelamatan File Tanpa EXIF**: File dari media sosial atau tangkapan layar yang bersih dari metadata akan dengan aman dipisahkan ke `miscellaneous` tanpa menimbulkan error.
