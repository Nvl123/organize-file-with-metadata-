import os
import sys
import shutil
import struct
import re
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Coba import Pillow dan pillow_heif untuk gambar
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
# KONFIGURASI DEFAULT
# ==============================================================================
DAYS_INDONESIAN = {
    0: "Senin",
    1: "Selasa",
    2: "Rabu",
    3: "Kamis",
    4: "Jumat",
    5: "Sabtu",
    6: "Minggu"
}
DATE_FOLDER_FORMAT = "%Y-%m-%d"
MISC_FOLDER = "miscellaneous"

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".heic", ".png", ".webp", ".tiff", ".bmp", ".cr2", ".nef", ".arw"}
VIDEO_EXTENSIONS = {".mov", ".mp4", ".m4v", ".avi", ".mkv", ".3gp"}


# ==============================================================================
# FUNGSI EKSTRAKSI METADATA
# ==============================================================================
def parse_date_string(date_str: str) -> datetime | None:
    """Mengubah string tanggal EXIF atau ISO menjadi objek datetime."""
    clean_str = date_str.strip().replace("\x00", "")
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
            if hasattr(ExifTags, "IFD") and hasattr(ExifTags.IFD, "Exif"):
                try:
                    ifd = exif.get_ifd(ExifTags.IFD.Exif)
                    for tag in (36867, 36868):
                        if tag in ifd and ifd[tag]:
                            date_str = str(ifd[tag])
                            break
                except Exception:
                    pass

            if not date_str and 306 in exif and exif[306]:
                date_str = str(exif[306])

            if date_str:
                return parse_date_string(date_str)
    except Exception:
        pass
    return None


def get_mov_mp4_metadata_date(filepath: Path) -> datetime | None:
    """Membaca metadata atom QuickTime/MP4 secara native."""
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
                    moov_data = f.read(min(atom_size - 8, 15 * 1024 * 1024))
                    m = re.search(rb"(20\d{2}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})", moov_data)
                    if m:
                        ds = m.group(1).decode("ascii")
                        ts = m.group(2).decode("ascii")
                        return datetime.strptime(f"{ds} {ts}", "%Y-%m-%d %H:%M:%S")

                    idx = moov_data.find(b"mvhd")
                    if idx != -1:
                        version = moov_data[idx + 4]
                        if version == 1:
                            ctime = struct.unpack(">Q", moov_data[idx + 8 : idx + 16])[0]
                        else:
                            ctime = struct.unpack(">I", moov_data[idx + 8 : idx + 12])[0]

                        if ctime > 0:
                            epoch = datetime(1904, 1, 1, tzinfo=timezone.utc)
                            dt = epoch.timestamp() + ctime
                            return datetime.fromtimestamp(dt)
                    break
                offset += atom_size
    except Exception:
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
# GUI UTAMA (TKINTER)
# ==============================================================================
class MediaOrganizerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Media Metadata Organizer")
        self.root.geometry("820x720")
        self.root.minsize(750, 650)

        # Queue untuk komunikasi thread-safe antara worker thread & GUI
        self.msg_queue = queue.Queue()
        self.cancel_requested = threading.Event()
        self.worker_thread = None

        self._apply_style()
        self._build_ui()
        self._process_queue()

    def _apply_style(self):
        style = ttk.Style()
        # Gunakan tema native yang bersih
        if "clam" in style.theme_names():
            style.theme_use("clam")

        # Warna kustom
        self.bg_color = "#f4f5f7"
        self.card_bg = "#ffffff"
        self.primary_color = "#2563eb"
        self.text_color = "#1e293b"

        self.root.configure(bg=self.bg_color)

        style.configure(".", background=self.bg_color, foreground=self.text_color, font=("Segoe UI", 9))
        style.configure("TFrame", background=self.bg_color)
        style.configure("Card.TFrame", background=self.card_bg, relief="groove")
        style.configure("TLabelframe", background=self.card_bg)
        style.configure("TLabelframe.Label", font=("Segoe UI", 10, "bold"), background=self.card_bg, foreground="#0f172a")

        style.configure("Header.TLabel", font=("Segoe UI", 14, "bold"), foreground="#1e3a8a", background=self.bg_color)
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground="#64748b", background=self.bg_color)
        style.configure("Card.TLabel", background=self.card_bg, foreground=self.text_color)
        style.configure("Value.TLabel", background=self.card_bg, font=("Segoe UI", 9, "bold"), foreground="#0f172a")
        style.configure("Path.TLabel", background=self.card_bg, font=("Consolas", 8), foreground="#0369a1")

        style.configure("Action.TButton", font=("Segoe UI", 10, "bold"), foreground="#ffffff", background=self.primary_color)
        style.map("Action.TButton", background=[("active", "#1d4ed8"), ("disabled", "#94a3b8")])

        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), foreground="#ffffff", background="#dc2626")
        style.map("Danger.TButton", background=[("active", "#b91c1c"), ("disabled", "#94a3b8")])

    def _build_ui(self):
        main_container = ttk.Frame(self.root, padding=16)
        main_container.pack(fill=tk.BOTH, expand=True)

        # 1. HEADER
        header_frame = ttk.Frame(main_container)
        header_frame.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(header_frame, text="📸 Media Metadata Organizer", style="Header.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header_frame,
            text="Organisir foto & video otomatis berdasarkan Hari/Tanggal metadata EXIF dan MP4 QuickTime.",
            style="SubHeader.TLabel"
        ).pack(anchor=tk.W, pady=(2, 0))

        # 2. PENGATURAN DIREKTORI & MODE
        config_frame = ttk.LabelFrame(main_container, text=" Konfigurasi Direktori & Mode ", padding=12)
        config_frame.pack(fill=tk.X, pady=(0, 10))

        # Folder Asli (Sumber)
        row1 = ttk.Frame(config_frame, style="Card.TFrame")
        row1.pack(fill=tk.X, pady=3)
        ttk.Label(row1, text="Folder Sumber:", width=15, style="Card.TLabel").pack(side=tk.LEFT)
        self.source_dir_var = tk.StringVar(value=str(Path(".").resolve()))
        source_entry = ttk.Entry(row1, textvariable=self.source_dir_var, font=("Segoe UI", 9))
        source_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(row1, text="Pilih Folder...", command=self._browse_source).pack(side=tk.RIGHT)

        # Mode Operasi (Move / Copy / Dry Run)
        row2 = ttk.Frame(config_frame, style="Card.TFrame")
        row2.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(row2, text="Mode Tindakan:", width=15, style="Card.TLabel").pack(side=tk.LEFT)

        self.mode_var = tk.StringVar(value="move")
        r_move = ttk.Radiobutton(row2, text="Pindahkan File (Move)", variable=self.mode_var, value="move")
        r_copy = ttk.Radiobutton(row2, text="Salin File (Copy)", variable=self.mode_var, value="copy")
        r_dry = ttk.Radiobutton(row2, text="Simulasi Saja (Dry Run)", variable=self.mode_var, value="dry_run")
        r_move.pack(side=tk.LEFT, padx=(0, 12))
        r_copy.pack(side=tk.LEFT, padx=(0, 12))
        r_dry.pack(side=tk.LEFT)

        # 3. STATUS PEMROSESAN LANGSUNG (LIVE TRACKING)
        status_frame = ttk.LabelFrame(main_container, text=" Status Pemrosesan Berjalan ", padding=12)
        status_frame.pack(fill=tk.X, pady=(0, 10))

        # Progress Bar & Persentase
        p_row = ttk.Frame(status_frame, style="Card.TFrame")
        p_row.pack(fill=tk.X, pady=(0, 6))
        self.progress_bar = ttk.Progressbar(p_row, orient=tk.HORIZONTAL, mode="determinate")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        self.lbl_progress_percent = ttk.Label(p_row, text="0 / 0 (0%)", style="Value.TLabel", width=16, anchor=tk.E)
        self.lbl_progress_percent.pack(side=tk.RIGHT)

        # Lokasi File Asli & Tujuan
        f_row = ttk.Frame(status_frame, style="Card.TFrame")
        f_row.pack(fill=tk.X, pady=2)
        ttk.Label(f_row, text="Lokasi Asli   :", width=14, style="Card.TLabel").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.lbl_current_source = ttk.Label(f_row, text="-", style="Path.TLabel", anchor=tk.W)
        self.lbl_current_source.grid(row=0, column=1, sticky=tk.W, padx=6)

        ttk.Label(f_row, text="Lokasi Tujuan :", width=14, style="Card.TLabel").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.lbl_current_dest = ttk.Label(f_row, text="-", style="Path.TLabel", anchor=tk.W)
        self.lbl_current_dest.grid(row=1, column=1, sticky=tk.W, padx=6)
        f_row.columnconfigure(1, weight=1)

        # 4. LAPORAN HASIL & RENTANG TANGGAL
        report_frame = ttk.LabelFrame(main_container, text=" Laporan & Statistik ", padding=12)
        report_frame.pack(fill=tk.X, pady=(0, 10))

        rep_grid = ttk.Frame(report_frame, style="Card.TFrame")
        rep_grid.pack(fill=tk.X)

        ttk.Label(rep_grid, text="Total File Diproses :", style="Card.TLabel").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.lbl_stat_processed = ttk.Label(rep_grid, text="0", style="Value.TLabel")
        self.lbl_stat_processed.grid(row=0, column=1, sticky=tk.W, padx=(4, 25))

        ttk.Label(rep_grid, text="Berhasil Dikelompokkan :", style="Card.TLabel").grid(row=0, column=2, sticky=tk.W, pady=2)
        self.lbl_stat_classified = ttk.Label(rep_grid, text="0", style="Value.TLabel")
        self.lbl_stat_classified.grid(row=0, column=3, sticky=tk.W, padx=(4, 25))

        ttk.Label(rep_grid, text="Tanpa Metadata (Misc) :", style="Card.TLabel").grid(row=1, column=0, sticky=tk.W, pady=2)
        self.lbl_stat_misc = ttk.Label(rep_grid, text="0", style="Value.TLabel")
        self.lbl_stat_misc.grid(row=1, column=1, sticky=tk.W, padx=(4, 25))

        ttk.Label(rep_grid, text="Bukan Media (Dilewati):", style="Card.TLabel").grid(row=1, column=2, sticky=tk.W, pady=2)
        self.lbl_stat_skipped = ttk.Label(rep_grid, text="0", style="Value.TLabel")
        self.lbl_stat_skipped.grid(row=1, column=3, sticky=tk.W, padx=(4, 25))

        # Rentang Tanggal
        ttk.Label(rep_grid, text="Rentang Tanggal     :", style="Card.TLabel").grid(row=2, column=0, sticky=tk.W, pady=(6, 2))
        self.lbl_stat_date_range = ttk.Label(
            rep_grid,
            text="-",
            style="Value.TLabel",
            foreground="#1d4ed8"
        )
        self.lbl_stat_date_range.grid(row=2, column=1, columnspan=3, sticky=tk.W, padx=4, pady=(6, 2))

        # 5. LOG AKTIVITAS (SCROLLABLE TEXT)
        log_frame = ttk.LabelFrame(main_container, text=" Log Aktivitas ", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            font=("Consolas", 8),
            bg="#ffffff",
            fg="#1e293b",
            relief="flat",
            highlightthickness=1,
            highlightbackground="#cbd5e1"
        )
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 6. TOMBOL AKSI UTAMA
        btn_container = ttk.Frame(main_container)
        btn_container.pack(fill=tk.X)

        self.btn_start = ttk.Button(
            btn_container,
            text="🚀 Mulai Pengelompokan",
            style="Action.TButton",
            command=self._start_processing
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 10), ipady=4)

        self.btn_stop = ttk.Button(
            btn_container,
            text="⏹️ Hentikan",
            style="Danger.TButton",
            command=self._stop_processing,
            state=tk.DISABLED
        )
        self.btn_stop.pack(side=tk.LEFT, ipady=4)

        self.lbl_footer_status = ttk.Label(btn_container, text="Siap digunakan.", foreground="#64748b")
        self.lbl_footer_status.pack(side=tk.RIGHT, pady=6)

    def _browse_source(self):
        selected = filedialog.askdirectory(initialdir=self.source_dir_var.get(), title="Pilih Folder Media")
        if selected:
            self.source_dir_var.set(str(Path(selected).resolve()))

    def _log(self, text: str):
        self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _start_processing(self):
        source_path = Path(self.source_dir_var.get().strip())
        if not source_path.exists() or not source_path.is_dir():
            messagebox.showerror("Error", "Folder sumber tidak valid atau tidak ditemukan!")
            return

        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.lbl_footer_status.configure(text="Sedang memproses...")
        self.cancel_requested.clear()

        # Reset UI
        self.progress_bar["value"] = 0
        self.lbl_progress_percent.configure(text="0 / 0 (0%)")
        self.lbl_current_source.configure(text="-")
        self.lbl_current_dest.configure(text="-")
        self.lbl_stat_processed.configure(text="0")
        self.lbl_stat_classified.configure(text="0")
        self.lbl_stat_misc.configure(text="0")
        self.lbl_stat_skipped.configure(text="0")
        self.lbl_stat_date_range.configure(text="-")
        self.log_text.delete("1.0", tk.END)

        mode = self.mode_var.get()
        self.worker_thread = threading.Thread(
            target=self._worker_run,
            args=(source_path, mode),
            daemon=True
        )
        self.worker_thread.start()

    def _stop_processing(self):
        if self.worker_thread and self.worker_thread.is_alive():
            self.cancel_requested.set()
            self._log("⚠️ Permintaan pembatalan diterima, menunggu proses saat ini selesai...")
            self.lbl_footer_status.configure(text="Membatalkan...")
            self.btn_stop.configure(state=tk.DISABLED)

    def _worker_run(self, source_dir: Path, mode: str):
        """Dijalankan di background thread agar UI tetap responsif."""
        dry_run = (mode == "dry_run")
        effective_mode = "copy" if mode == "copy" else "move"

        all_entries = [f for f in source_dir.iterdir() if f.is_file()]
        # Filter file skrip sendiri jika ada di direktori tersebut
        own_names = {"organize_media.py", "organize_media_gui.py", Path(__file__).name}
        files_to_check = [f for f in all_entries if f.name not in own_names]

        total_files = len(files_to_check)
        self.msg_queue.put(("INIT", total_files))

        processed_count = 0
        classified_count = 0
        misc_count = 0
        skipped_count = 0

        earliest_dt = None
        latest_dt = None

        for idx, file_path in enumerate(files_to_check, start=1):
            if self.cancel_requested.is_set():
                self.msg_queue.put(("LOG", "⏹️ Proses dihentikan oleh pengguna."))
                break

            ext = file_path.suffix.lower()
            if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
                skipped_count += 1
                self.msg_queue.put(("UPDATE_PROGRESS", (idx, total_files, str(file_path), "Dilewati (Bukan Media)")))
                self.msg_queue.put(("STATS", (processed_count, classified_count, misc_count, skipped_count)))
                continue

            processed_count += 1
            created_dt = None

            if ext in IMAGE_EXTENSIONS:
                created_dt = get_image_metadata_date(file_path)
            elif ext in VIDEO_EXTENSIONS:
                created_dt = get_mov_mp4_metadata_date(file_path)

            if created_dt:
                classified_count += 1
                # Update rentang tanggal
                if earliest_dt is None or created_dt < earliest_dt:
                    earliest_dt = created_dt
                if latest_dt is None or created_dt > latest_dt:
                    latest_dt = created_dt

                day_name = DAYS_INDONESIAN.get(created_dt.weekday(), "Lainnya")
                date_folder_name = created_dt.strftime(DATE_FOLDER_FORMAT)
                target_dir = source_dir / day_name / date_folder_name
                log_tag = f"[{day_name} -> {date_folder_name}]"
            else:
                misc_count += 1
                target_dir = source_dir / MISC_FOLDER
                log_tag = f"[{MISC_FOLDER}]"

            dest_file_path = get_unique_destination_path(target_dir / file_path.name)

            # Eksekusi aksi jika bukan dry run
            action_desc = "SIMULASI"
            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)
                if effective_mode == "move":
                    shutil.move(str(file_path), str(dest_file_path))
                    action_desc = "PINDAH"
                else:
                    shutil.copy2(str(file_path), str(dest_file_path))
                    action_desc = "SALIN"
            else:
                action_desc = "SIMULASI"

            self.msg_queue.put(("UPDATE_PROGRESS", (idx, total_files, str(file_path), str(dest_file_path))))
            self.msg_queue.put(("LOG", f"[{action_desc}] {file_path.name} -> {target_dir.relative_to(source_dir)} {log_tag}"))
            self.msg_queue.put(("STATS", (processed_count, classified_count, misc_count, skipped_count)))

            # Update rentang tanggal jika ada
            if earliest_dt and latest_dt:
                self.msg_queue.put(("DATE_RANGE", (earliest_dt, latest_dt)))

        self.msg_queue.put(("DONE", (processed_count, classified_count, misc_count, skipped_count, earliest_dt, latest_dt, dry_run)))

    def _process_queue(self):
        """Memeriksa antrean pesan dari worker thread secara berkala."""
        try:
            while True:
                msg_type, payload = self.msg_queue.get_nowait()
                if msg_type == "INIT":
                    total = payload
                    self.progress_bar["maximum"] = max(total, 1)
                    self.lbl_progress_percent.configure(text=f"0 / {total} (0%)")
                    self._log(f"📋 Memulai pemrosesan {total} file...")

                elif msg_type == "UPDATE_PROGRESS":
                    idx, total, src, dst = payload
                    pct = int((idx / total) * 100) if total > 0 else 0
                    self.progress_bar["value"] = idx
                    self.lbl_progress_percent.configure(text=f"{idx} / {total} ({pct}%)")
                    self.lbl_current_source.configure(text=src)
                    self.lbl_current_dest.configure(text=dst)

                elif msg_type == "STATS":
                    proc, clas, misc, skip = payload
                    self.lbl_stat_processed.configure(text=str(proc))
                    self.lbl_stat_classified.configure(text=str(clas))
                    self.lbl_stat_misc.configure(text=str(misc))
                    self.lbl_stat_skipped.configure(text=str(skip))

                elif msg_type == "DATE_RANGE":
                    earliest, latest = payload
                    date_range_str = f"{earliest.strftime('%d-%m-%Y')} s/d {latest.strftime('%d-%m-%Y')}"
                    self.lbl_stat_date_range.configure(text=date_range_str)

                elif msg_type == "LOG":
                    self._log(payload)

                elif msg_type == "DONE":
                    proc, clas, misc, skip, earliest, latest, dry_run = payload
                    self.btn_start.configure(state=tk.NORMAL)
                    self.btn_stop.configure(state=tk.DISABLED)
                    self.lbl_footer_status.configure(text="Selesai.")

                    range_text = "Tidak ada metadata tanggal yang ditemukan."
                    if earliest and latest:
                        range_text = f"{earliest.strftime('%d %B %Y')} s/d {latest.strftime('%d %B %Y')} ({earliest.strftime('%Y-%m-%d')} ~ {latest.strftime('%Y-%m-%d')})"
                        self.lbl_stat_date_range.configure(text=f"{earliest.strftime('%d-%m-%Y')} s/d {latest.strftime('%d-%m-%Y')}")

                    self._log("\n" + "=" * 60)
                    self._log("🎉 PENGELOMPOKAN SELESAI")
                    self._log(f" Mode              : {'SIMULASI (DRY RUN)' if dry_run else 'EKSEKUSI NYATA'}")
                    self._log(f" Total Diproses    : {proc} file media")
                    self._log(f" Dikelompokkan     : {clas} file")
                    self._log(f" Masuk Misc        : {misc} file")
                    self._log(f" Dilewati          : {skip} file")
                    self._log(f" Rentang Tanggal   : {range_text}")
                    self._log("=" * 60)

                    messagebox.showinfo(
                        "Proses Selesai",
                        f"Pengelompokan selesai!\n\n"
                        f"• Total Media Diproses: {proc}\n"
                        f"• Berhasil Terorganisir: {clas}\n"
                        f"• Masuk Miscellaneous: {misc}\n"
                        f"• File Dilewati: {skip}\n\n"
                        f"📅 Rentang Tanggal:\n{range_text}"
                    )
        except queue.Empty:
            pass

        # Jadwalkan pengecekan berikutnya setiap 50 ms
        self.root.after(50, self._process_queue)


def main():
    root = tk.Tk()
    app = MediaOrganizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
