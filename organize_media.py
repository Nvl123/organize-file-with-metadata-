import os
import sys
import shutil
import struct
import re
from datetime import datetime, timezone, date
from pathlib import Path

# Coba import Pillow dan pillow_heif untuk gambar (mendukung JPG, PNG, HEIC, WEBP, dll.)
try:
    from PIL import Image, ExifTags
except ImportError:
    Image = None
    ExifTags = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass


# ==============================================================================
# KONFIGURASI PENGATURAN
# ==============================================================================
# Direktori target (default: direktori tempat script ini berada)
SOURCE_DIR = Path(".")

# Mode operasi: "move" untuk memindahkan file, atau "copy" untuk menduplikasi file
OPERATION_MODE = "move"  # Pilihan: "move" atau "copy"

# Nama folder untuk file yang tidak memiliki metadata
MISC_FOLDER = "miscellaneous"

# Format nama sub-folder tanggal di dalam folder hari:
# "%Y-%m-%d" -> contoh: "2026-09-07"
# "%d-%m-%Y" -> contoh: "07-09-2026"
DATE_FOLDER_FORMAT = "%Y-%m-%d"

# Pemetaan hari dalam bahasa Indonesia (Python weekday: 0 = Senin, ..., 6 = Minggu)
DAYS_INDONESIAN = {
    0: "Senin",
    1: "Selasa",
    2: "Rabu",
    3: "Kamis",
    4: "Jumat",
    5: "Sabtu",
    6: "Minggu"
}

# Ekstensi file yang didukung
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".png", ".webp", ".tiff", ".bmp", ".cr2", ".nef", ".arw"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp"}


# ==============================================================================
# FUNGSI EKSTRAKSI METADATA
# ==============================================================================
def get_image_metadata_date(filepath: Path) -> datetime | None:
    """Mengambil tanggal pembuatan dari metadata EXIF gambar."""
    if Image is None:
        return None
    try:
        with Image.open(filepath) as img:
            exif = img.getexif()
            if not exif:
                return None

            date_str = None

            # 1. Cek IFD Exif (DateTimeOriginal / DateTimeDigitized)
            if hasattr(ExifTags, "IFD") and hasattr(ExifTags.IFD, "Exif"):
                try:
                    ifd = exif.get_ifd(ExifTags.IFD.Exif)
                    # 36867 = DateTimeOriginal, 36868 = DateTimeDigitized
                    for tag in (36867, 36868):
                        if tag in ifd and ifd[tag]:
                            date_str = str(ifd[tag])
                            break
                except Exception:
                    pass

            # 2. Cek tag utama EXIF (306 = DateTime) jika belum dapat
            if not date_str and 306 in exif and exif[306]:
                date_str = str(exif[306])

            if date_str:
                return parse_date_string(date_str)
    except Exception:
        pass
    return None


def get_mov_mp4_metadata_date(filepath: Path) -> datetime | None:
    """
    Membaca metadata atom QuickTime/MP4 (moov -> mvhd & creationdate)
    secara native tanpa memerlukan dependensi eksternal (ffmpeg/ffprobe).
    """
    try:
        filesize = filepath.stat().st_size
        with open(filepath, "rb") as f:
            offset = 0
            while offset < filesize:
                f.seek(offset)
                header = f.read(8)
                if len(header) < 8:
                    break
                atom_size, atom_type = struct.unpack(">I4s", header)
                atom_type = atom_type.decode("latin1", errors="ignore")

                if atom_size == 1:
                    atom_size = struct.unpack(">Q", f.read(8))[0]
                elif atom_size == 0:
                    atom_size = filesize - offset

                if atom_type == "moov":
                    # Baca isi box moov (dibatasi 15MB untuk efisiensi RAM)
                    moov_data = f.read(min(atom_size - 8, 15 * 1024 * 1024))

                    # 1. Cek string tanggal format Apple QuickTime (misal: 2026-09-07T17:11:08+0700)
                    m = re.search(rb"(20\d{2}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", moov_data)
                    if m:
                        ds = m.group(1).decode("ascii")
                        ts = m.group(2).decode("ascii")
                        return datetime.strptime(f"{ds} {ts}", "%Y-%m-%d %H:%M:%S")

                    # 2. Cek atom mvhd (Movie Header) standar MP4/MOV
                    idx = moov_data.find(b"mvhd")
                    if idx != -1:
                        version = moov_data[idx + 4]
                        if version == 1:
                            ctime = struct.unpack(">Q", moov_data[idx + 8 : idx + 16])[0]
                        else:
                            ctime = struct.unpack(">I", moov_data[idx + 8 : idx + 12])[0]

                        if ctime > 0:
                            # QuickTime epoch dimulai dari 1 Januari 1904 UTC
                            epoch = datetime(1904, 1, 1, tzinfo=timezone.utc)
                            dt_utc = epoch + (datetime.fromtimestamp(0, tz=timezone.utc) - datetime(1970, 1, 1, tz=timezone.utc))
                            # Hitung waktu datetime lokal
                            dt = epoch.timestamp() + ctime
                            return datetime.fromtimestamp(dt)
                    break
                offset += atom_size
    except Exception:
        pass
    return None


def parse_date_string(date_str: str) -> datetime | None:
    """Mengubah string tanggal EXIF atau ISO menjadi objek datetime."""
    clean_str = date_str.strip().replace("\x00", "")
    # Format umum EXIF: "YYYY:MM:DD HH:MM:SS" atau "YYYY-MM-DD HH:MM:SS"
    m = re.search(r"(\d{4})[:\-](\d{2})[:\-](\d{2})(?:[ T](\d{2}):(\d{2}):(\d{2}))?", clean_str)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        h = int(m.group(4)) if m.group(4) else 0
        mi = int(m.group(5)) if m.group(5) else 0
        s = int(m.group(6)) if m.group(6) else 0
        try:
            return datetime(y, mo, d, h, mi, s)
        except ValueError:
            pass
    return None


def get_unique_destination_path(target_path: Path) -> Path:
    """Mencegah file tertimpa jika nama file sama sudah ada di folder tujuan."""
    if not target_path.exists():
        return target_path

    parent = target_path.parent
    stem = target_path.stem
    suffix = target_path.suffix
    counter = 1

    while True:
        new_path = parent / f"{stem}_{counter}{suffix}"
        if not new_path.exists():
            return new_path
        counter += 1


# ==============================================================================
# FUNGSI UTAMA PENGORGANISIR
# ==============================================================================
def organize_files(source_dir: Path, mode: str = "move", dry_run: bool = False):
    """
    Memindai direktori, membaca metadata tanggal setiap gambar & video,
    dan mengelompokkannya ke dalam folder Hari/Tanggal atau folder Miscellaneous.
    """
    source_dir = source_dir.resolve()
    print("=" * 65)
    print(f" Memulai Pengelompokan Media ")
    print(f" Direktori Target : {source_dir}")
    print(f" Mode Operasi     : {mode.upper()} {'(DRY RUN - SIMULASI)' if dry_run else ''}")
    print("=" * 65)

    # Folder-folder yang harus dilewati agar tidak terjadi pemindahan rekursif
    reserved_folders = set(DAYS_INDONESIAN.values()) | {MISC_FOLDER}

    processed_count = 0
    classified_count = 0
    misc_count = 0
    skipped_count = 0

    all_files = [f for f in source_dir.iterdir() if f.is_file()]

    for file_path in all_files:
        # Lewati script ini sendiri
        if file_path.name == Path(__file__).name:
            continue

        ext = file_path.suffix.lower()
        if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
            skipped_count += 1
            continue

        processed_count += 1
        created_dt = None

        if ext in IMAGE_EXTENSIONS:
            created_dt = get_image_metadata_date(file_path)
        elif ext in VIDEO_EXTENSIONS:
            created_dt = get_mov_mp4_metadata_date(file_path)

        if created_dt:
            # Dapatkan nama hari (0 = Senin, dst.)
            day_name = DAYS_INDONESIAN.get(created_dt.weekday(), "Lainnya")
            date_folder_name = created_dt.strftime(DATE_FOLDER_FORMAT)
            target_dir = source_dir / day_name / date_folder_name
            classified_count += 1
            info_tag = f"[{day_name} -> {date_folder_name}]"
        else:
            # Jika tidak ada metadata tanggal, masukkan ke folder miscellaneous
            target_dir = source_dir / MISC_FOLDER
            misc_count += 1
            info_tag = f"[{MISC_FOLDER}] (Tanpa Metadata)"

        dest_file_path = get_unique_destination_path(target_dir / file_path.name)

        action_label = "AKAN PINDAH" if mode == "move" else "AKAN SALIN"
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
            if mode == "move":
                shutil.move(str(file_path), str(dest_file_path))
                action_label = "DIPINDAHKAN"
            else:
                shutil.copy2(str(file_path), str(dest_file_path))
                action_label = "DISALIN"

        print(f" {action_label:<12} : {file_path.name} -> {target_dir.relative_to(source_dir)}")

    print("\n" + "=" * 65)
    print(" RINGKASAN PROSES ")
    print(f" Total File Media Diproses : {processed_count}")
    print(f" Berhasil Dikelompokkan    : {classified_count}")
    print(f" Masuk ke Miscellaneous    : {misc_count}")
    print(f" Dilewati (bukan media)    : {skipped_count}")
    print("=" * 65)


if __name__ == "__main__":
    # Dukungan argumen command line jika diperlukan:
    # python organize_media.py --dry-run
    # python organize_media.py --copy
    # python organize_media.py --move
    args = sys.argv[1:]
    is_dry_run = "--dry-run" in args
    selected_mode = OPERATION_MODE

    if "--copy" in args:
        selected_mode = "copy"
    elif "--move" in args:
        selected_mode = "move"

    organize_files(SOURCE_DIR, mode=selected_mode, dry_run=is_dry_run)
