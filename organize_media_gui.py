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

# Set Windows DPI Awareness agar antarmuka tajam dan tidak pecah / terpotong di layar laptop
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

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
# GUI UTAMA (TKINTER MODERN & RESPONSIVE)
# ==============================================================================
class MediaOrganizerGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Media Metadata Organizer - GUI")
        # Ukuran proporsional & nyaman untuk berbagai resolusi layar laptop/desktop
        self.root.geometry("880x660")
        self.root.minsize(760, 520)

        # Queue untuk komunikasi thread-safe antara worker thread & GUI
        self.msg_queue = queue.Queue()
        self.cancel_requested = threading.Event()
        self.worker_thread = None

        self._apply_style()
        self._build_ui()
        self._bind_shortcuts()
        self._process_queue()

    def _apply_style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")

        # Palet Warna Modern (Clean Slate & Indigo)
        self.bg_color = "#f8fafc"         # Slate-50
        self.card_bg = "#ffffff"          # Pure White
        self.border_color = "#e2e8f0"     # Slate-200
        self.primary_color = "#2563eb"    # Blue-600
        self.text_dark = "#0f172a"        # Slate-900
        self.text_muted = "#64748b"       # Slate-500

        self.root.configure(bg=self.bg_color)

        style.configure(".", background=self.bg_color, foreground=self.text_dark, font=("Segoe UI", 9))
        style.configure("TFrame", background=self.bg_color)
        style.configure("Card.TFrame", background=self.card_bg)
        style.configure("TLabelframe", background=self.card_bg)
        style.configure(
            "TLabelframe.Label",
            font=("Segoe UI", 9, "bold"),
            background=self.card_bg,
            foreground="#1e293b"
        )

        style.configure("Header.TLabel", font=("Segoe UI", 13, "bold"), foreground="#0f172a", background=self.card_bg)
        style.configure("SubHeader.TLabel", font=("Segoe UI", 9), foreground=self.text_muted, background=self.card_bg)
        style.configure("Card.TLabel", background=self.card_bg, foreground=self.text_dark)
        style.configure("Muted.TLabel", background=self.card_bg, foreground=self.text_muted, font=("Segoe UI", 8))
        style.configure("Value.TLabel", background=self.card_bg, font=("Segoe UI", 10, "bold"), foreground="#1e293b")
        style.configure("Path.TLabel", background=self.card_bg, font=("Consolas", 8), foreground="#0284c7")

        # Style Progressbar
        style.configure("Horizontal.TProgressbar", troughcolor="#e2e8f0", background="#2563eb", thickness=14)

    def _build_ui(self):
        # 1. FIXED BOTTOM STATUS BAR (Packed FIRST with side=BOTTOM agar TIDAK PERNAH terpotong)
        self._build_bottom_bar()

        # 2. MAIN SCROLLABLE/EXPANDING CONTAINER
        main_container = ttk.Frame(self.root, padding=(14, 12, 14, 8))
        main_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # 3. HEADER BANNER
        self._build_header(main_container)

        # 4. KARTU PENGATURAN & TOMBOL MULAI (LANGSUNG TERLIHAT DI ATAS)
        self._build_config_and_actions(main_container)

        # 5. KARTU DASHBOARD STATUS & STATISTIK
        self._build_dashboard(main_container)

        # 6. LOG AKTIVITAS (FLEXIBLE EXPAND DI SISA RUANG)
        self._build_log_section(main_container)

    def _build_bottom_bar(self):
        """Status bar tetap di paling bawah, dijamin selalu muncul di layar."""
        bottom_frame = tk.Frame(self.root, bg="#f1f5f9", height=30, highlightthickness=1, highlightbackground=self.border_color)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        self.lbl_footer_status = tk.Label(
            bottom_frame,
            text="🟢 Siap digunakan. Pilih folder dan klik Mulai.",
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#334155"
        )
        self.lbl_footer_status.pack(side=tk.LEFT, padx=12, pady=5)

        lbl_tips = tk.Label(
            bottom_frame,
            text="Pintas Keyboard: [F5] Mulai | [Esc] Hentikan",
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#64748b"
        )
        lbl_tips.pack(side=tk.RIGHT, padx=12, pady=5)

    def _build_header(self, parent):
        header_card = tk.Frame(parent, bg=self.card_bg, padx=12, pady=8, highlightthickness=1, highlightbackground=self.border_color)
        header_card.pack(fill=tk.X, pady=(0, 8))

        top_row = tk.Frame(header_card, bg=self.card_bg)
        top_row.pack(fill=tk.X)

        tk.Label(
            top_row,
            text="📸 Media Metadata Organizer",
            font=("Segoe UI", 12, "bold"),
            bg=self.card_bg,
            fg="#1e3a8a"
        ).pack(side=tk.LEFT)

        tk.Label(
            top_row,
            text="v1.2 GUI Edition",
            font=("Segoe UI", 8, "bold"),
            bg="#e0e7ff",
            fg="#3730a3",
            padx=6,
            pady=2
        ).pack(side=tk.RIGHT)

        tk.Label(
            header_card,
            text="Otomatis sortir foto & video ke folder Hari (Senin-Minggu) & Tanggal (YYYY-MM-DD) berdasarkan EXIF/QuickTime.",
            font=("Segoe UI", 8),
            bg=self.card_bg,
            fg=self.text_muted
        ).pack(anchor=tk.W, pady=(2, 0))

    def _build_config_and_actions(self, parent):
        """Bagian Pengaturan Folder, Mode Tindakan, dan Tombol Start Utama yang sangat jelas."""
        card = tk.Frame(parent, bg=self.card_bg, padx=12, pady=10, highlightthickness=1, highlightbackground=self.border_color)
        card.pack(fill=tk.X, pady=(0, 8))

        # Baris 1: Folder Sumber
        row1 = tk.Frame(card, bg=self.card_bg)
        row1.pack(fill=tk.X, pady=(0, 6))

        tk.Label(row1, text="📁 Folder Sumber:", font=("Segoe UI", 9, "bold"), bg=self.card_bg, width=14, anchor=tk.W).pack(side=tk.LEFT)
        self.source_dir_var = tk.StringVar(value=str(Path(".").resolve()))
        entry_source = ttk.Entry(row1, textvariable=self.source_dir_var, font=("Segoe UI", 9))
        entry_source.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6))

        # Tombol Pilih Folder
        btn_browse = tk.Button(
            row1,
            text="📂 Cari Folder...",
            command=self._browse_source,
            font=("Segoe UI", 9),
            bg="#e2e8f0",
            fg="#1e293b",
            activebackground="#cbd5e1",
            relief="flat",
            padx=10,
            pady=3,
            cursor="hand2"
        )
        btn_browse.pack(side=tk.LEFT, padx=(0, 4))

        # Tombol Buka di File Explorer
        btn_open = tk.Button(
            row1,
            text="↗️ Buka Folder",
            command=self._open_in_explorer,
            font=("Segoe UI", 9),
            bg="#f1f5f9",
            fg="#475569",
            activebackground="#e2e8f0",
            relief="flat",
            padx=8,
            pady=3,
            cursor="hand2"
        )
        btn_open.pack(side=tk.LEFT)

        # Baris 2: Mode Operasi
        row2 = tk.Frame(card, bg=self.card_bg)
        row2.pack(fill=tk.X, pady=(2, 8))

        tk.Label(row2, text="⚙️ Mode Tindakan:", font=("Segoe UI", 9, "bold"), bg=self.card_bg, width=14, anchor=tk.W).pack(side=tk.LEFT)

        self.mode_var = tk.StringVar(value="move")
        r_move = tk.Radiobutton(
            row2, text="📦 Pindahkan File (Move)", variable=self.mode_var, value="move",
            bg=self.card_bg, activebackground=self.card_bg, font=("Segoe UI", 9)
        )
        r_copy = tk.Radiobutton(
            row2, text="📋 Salin File (Copy)", variable=self.mode_var, value="copy",
            bg=self.card_bg, activebackground=self.card_bg, font=("Segoe UI", 9)
        )
        r_dry = tk.Radiobutton(
            row2, text="🧪 Simulasi Saja (Dry Run)", variable=self.mode_var, value="dry_run",
            bg=self.card_bg, activebackground=self.card_bg, font=("Segoe UI", 9), fg="#0369a1"
        )
        r_move.pack(side=tk.LEFT, padx=(0, 10))
        r_copy.pack(side=tk.LEFT, padx=(0, 10))
        r_dry.pack(side=tk.LEFT)

        # Baris 3: BARIS TOMBOL AKSI UTAMA (SANGAT MENCOLOK, TIDAK AKAN PERNAH HILANG / TERGESER)
        sep = tk.Frame(card, bg="#f1f5f9", height=1)
        sep.pack(fill=tk.X, pady=(2, 8))

        action_row = tk.Frame(card, bg=self.card_bg)
        action_row.pack(fill=tk.X)

        # TOMBOL START BESAR (HIJAU VIBRANT)
        self.btn_start = tk.Button(
            action_row,
            text="▶  MULAI PENGELOMPOKAN (START)",
            command=self._start_processing,
            font=("Segoe UI", 10, "bold"),
            bg="#16a34a",
            fg="#ffffff",
            activebackground="#15803d",
            activeforeground="#ffffff",
            relief="flat",
            padx=18,
            pady=7,
            cursor="hand2"
        )
        self.btn_start.pack(side=tk.LEFT, padx=(0, 8))

        # TOMBOL STOP (MERAH)
        self.btn_stop = tk.Button(
            action_row,
            text="⏹  Hentikan (Stop)",
            command=self._stop_processing,
            font=("Segoe UI", 9, "bold"),
            bg="#94a3b8",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            relief="flat",
            padx=12,
            pady=7,
            state=tk.DISABLED,
            cursor="arrow"
        )
        self.btn_stop.pack(side=tk.LEFT, padx=(0, 10))

        # STATUS PILL / INDIKATOR CEPAT
        self.lbl_badge_status = tk.Label(
            action_row,
            text="Ready",
            font=("Segoe UI", 8, "bold"),
            bg="#dcfce7",
            fg="#15803d",
            padx=8,
            pady=4
        )
        self.lbl_badge_status.pack(side=tk.LEFT)

        # TOMBOL BERSIHKAN LOG DI SEBELAH KANAN
        btn_clear = tk.Button(
            action_row,
            text="🧹 Bersihkan Log",
            command=self._clear_log,
            font=("Segoe UI", 8),
            bg="#f1f5f9",
            fg="#64748b",
            activebackground="#e2e8f0",
            relief="flat",
            padx=8,
            pady=5,
            cursor="hand2"
        )
        btn_clear.pack(side=tk.RIGHT)

    def _build_dashboard(self, parent):
        """Dashboard pemantauan langsung: Progress bar, File aktif, Statistik kartu."""
        card = tk.Frame(parent, bg=self.card_bg, padx=12, pady=8, highlightthickness=1, highlightbackground=self.border_color)
        card.pack(fill=tk.X, pady=(0, 8))

        # Progress Bar & Persentase
        p_row = tk.Frame(card, bg=self.card_bg)
        p_row.pack(fill=tk.X, pady=(0, 6))

        self.progress_bar = ttk.Progressbar(p_row, orient=tk.HORIZONTAL, mode="determinate", style="Horizontal.TProgressbar")
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.lbl_progress_percent = tk.Label(
            p_row,
            text="0 / 0 (0%)",
            font=("Segoe UI", 9, "bold"),
            bg=self.card_bg,
            fg="#1e293b",
            width=14,
            anchor=tk.E
        )
        self.lbl_progress_percent.pack(side=tk.RIGHT)

        # File Sedang Diproses
        loc_row = tk.Frame(card, bg=self.card_bg)
        loc_row.pack(fill=tk.X, pady=(0, 6))

        tk.Label(loc_row, text="File Aktif :", font=("Segoe UI", 8, "bold"), bg=self.card_bg, fg=self.text_muted, width=10, anchor=tk.W).pack(side=tk.LEFT)
        self.lbl_current_source = tk.Label(loc_row, text="Menunggu dimulai...", font=("Consolas", 8), bg=self.card_bg, fg="#0369a1", anchor=tk.W)
        self.lbl_current_source.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Kartu Statistik 4 Kolom
        stats_frame = tk.Frame(card, bg="#f8fafc", padx=8, pady=6, highlightthickness=1, highlightbackground=self.border_color)
        stats_frame.pack(fill=tk.X)
        for c in range(4):
            stats_frame.columnconfigure(c, weight=1)

        # Kolom 1: Total Diproses
        c1 = tk.Frame(stats_frame, bg="#f8fafc")
        c1.grid(row=0, column=0, sticky="ew")
        tk.Label(c1, text="Total Media", font=("Segoe UI", 8), bg="#f8fafc", fg=self.text_muted).pack()
        self.lbl_stat_processed = tk.Label(c1, text="0", font=("Segoe UI", 11, "bold"), bg="#f8fafc", fg="#0f172a")
        self.lbl_stat_processed.pack()

        # Kolom 2: Berhasil
        c2 = tk.Frame(stats_frame, bg="#f8fafc")
        c2.grid(row=0, column=1, sticky="ew")
        tk.Label(c2, text="Terorganisir", font=("Segoe UI", 8), bg="#f8fafc", fg=self.text_muted).pack()
        self.lbl_stat_classified = tk.Label(c2, text="0", font=("Segoe UI", 11, "bold"), bg="#f8fafc", fg="#16a34a")
        self.lbl_stat_classified.pack()

        # Kolom 3: Miscellaneous
        c3 = tk.Frame(stats_frame, bg="#f8fafc")
        c3.grid(row=0, column=2, sticky="ew")
        tk.Label(c3, text="Miscellaneous", font=("Segoe UI", 8), bg="#f8fafc", fg=self.text_muted).pack()
        self.lbl_stat_misc = tk.Label(c3, text="0", font=("Segoe UI", 11, "bold"), bg="#f8fafc", fg="#d97706")
        self.lbl_stat_misc.pack()

        # Kolom 4: Dilewati
        c4 = tk.Frame(stats_frame, bg="#f8fafc")
        c4.grid(row=0, column=3, sticky="ew")
        tk.Label(c4, text="Dilewati", font=("Segoe UI", 8), bg="#f8fafc", fg=self.text_muted).pack()
        self.lbl_stat_skipped = tk.Label(c4, text="0", font=("Segoe UI", 11, "bold"), bg="#f8fafc", fg="#64748b")
        self.lbl_stat_skipped.pack()

        # Rentang Tanggal Badge
        date_row = tk.Frame(card, bg=self.card_bg)
        date_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(date_row, text="📅 Rentang Tanggal Metadata :", font=("Segoe UI", 8, "bold"), bg=self.card_bg, fg="#334155").pack(side=tk.LEFT)
        self.lbl_stat_date_range = tk.Label(date_row, text="-", font=("Segoe UI", 8, "bold"), bg=self.card_bg, fg="#2563eb")
        self.lbl_stat_date_range.pack(side=tk.LEFT, padx=6)

    def _build_log_section(self, parent):
        """Kotak log aktivitas bergaya modern console yang fleksibel menyesuaikan tinggi jendela."""
        log_frame = tk.Frame(parent, bg=self.card_bg, padx=10, pady=8, highlightthickness=1, highlightbackground=self.border_color)
        log_frame.pack(fill=tk.BOTH, expand=True)

        header_row = tk.Frame(log_frame, bg=self.card_bg)
        header_row.pack(fill=tk.X, pady=(0, 4))

        tk.Label(header_row, text="📋 Log Aktivitas Real-Time", font=("Segoe UI", 8, "bold"), bg=self.card_bg, fg="#334155").pack(side=tk.LEFT)
        tk.Label(header_row, text="Otomatis bergulir ke bawah", font=("Segoe UI", 7), bg=self.card_bg, fg=self.text_muted).pack(side=tk.RIGHT)

        text_container = tk.Frame(log_frame, bg="#0f172a")
        text_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            text_container,
            wrap=tk.WORD,
            font=("Consolas", 8),
            bg="#0f172a",
            fg="#e2e8f0",
            insertbackground="#ffffff",
            relief="flat",
            height=7,
            padx=8,
            pady=6
        )
        log_scroll = ttk.Scrollbar(text_container, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Tag Pewarnaan Log
        self.log_text.tag_config("SUCCESS", foreground="#4ade80")
        self.log_text.tag_config("INFO", foreground="#60a5fa")
        self.log_text.tag_config("WARN", foreground="#fbbf24")
        self.log_text.tag_config("ERROR", foreground="#f87171", font=("Consolas", 8, "bold"))
        self.log_text.tag_config("MUTED", foreground="#94a3b8")
        self.log_text.tag_config("HEADER", foreground="#c084fc", font=("Consolas", 8, "bold"))

        self._log("👋 Selamat datang di Media Metadata Organizer!", "INFO")
        self._log("Pilih folder sumber di atas, tentukan mode (Move/Copy/Dry Run), lalu tekan tombol 'MULAI PENGELOMPOKAN' atau tekan [F5].", "MUTED")

    def _bind_shortcuts(self):
        """Shortcut keyboard untuk kemudahan navigasi."""
        self.root.bind("<F5>", lambda e: self._start_processing())
        self.root.bind("<Control-Return>", lambda e: self._start_processing())
        self.root.bind("<Escape>", lambda e: self._stop_processing())

    def _browse_source(self):
        selected = filedialog.askdirectory(initialdir=self.source_dir_var.get(), title="Pilih Folder Sumber Media")
        if selected:
            self.source_dir_var.set(str(Path(selected).resolve()))

    def _open_in_explorer(self):
        folder = Path(self.source_dir_var.get().strip())
        if folder.exists() and folder.is_dir():
            if sys.platform == "win32":
                os.startfile(str(folder))
            else:
                import subprocess
                subprocess.run(["xdg-open", str(folder)])
        else:
            messagebox.showwarning("Peringatan", "Folder tidak ditemukan atau belum dipilih.")

    def _clear_log(self):
        self.log_text.delete("1.0", tk.END)

    def _log(self, text: str, tag: str = None):
        if tag:
            self.log_text.insert(tk.END, text + "\n", tag)
        else:
            self.log_text.insert(tk.END, text + "\n")
        self.log_text.see(tk.END)

    def _start_processing(self):
        # Mencegah eksekusi ganda jika sedang berjalan
        if self.worker_thread and self.worker_thread.is_alive():
            return

        source_path = Path(self.source_dir_var.get().strip())
        if not source_path.exists() or not source_path.is_dir():
            messagebox.showerror("Error", "Folder sumber tidak valid atau tidak ditemukan!")
            return

        # Update Tampilan Tombol
        self.btn_start.configure(state=tk.DISABLED, bg="#94a3b8", cursor="arrow")
        self.btn_stop.configure(state=tk.NORMAL, bg="#dc2626", cursor="hand2")
        self.lbl_badge_status.configure(text="Sedang Berjalan", bg="#fef3c7", fg="#b45309")
        self.lbl_footer_status.configure(text="⏳ Sedang memproses media...")
        self.cancel_requested.clear()

        # Reset Statistik
        self.progress_bar["value"] = 0
        self.lbl_progress_percent.configure(text="0 / 0 (0%)")
        self.lbl_current_source.configure(text="Memulai pemindaian file...")
        self.lbl_stat_processed.configure(text="0")
        self.lbl_stat_classified.configure(text="0")
        self.lbl_stat_misc.configure(text="0")
        self.lbl_stat_skipped.configure(text="0")
        self.lbl_stat_date_range.configure(text="-")

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
            self._log("⚠️ Permintaan pembatalan diterima, menghentikan proses...", "WARN")
            self.lbl_footer_status.configure(text="⏹ Menghentikan...")
            self.btn_stop.configure(state=tk.DISABLED, bg="#94a3b8", cursor="arrow")

    def _worker_run(self, source_dir: Path, mode: str):
        """Worker thread untuk memproses file di latar belakang tanpa membekukan antarmuka."""
        dry_run = (mode == "dry_run")
        effective_mode = "copy" if mode == "copy" else "move"

        all_entries = [f for f in source_dir.iterdir() if f.is_file()]
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
                self.msg_queue.put(("LOG", ("⏹️ Proses dihentikan oleh pengguna.", "WARN")))
                break

            ext = file_path.suffix.lower()
            if ext not in IMAGE_EXTENSIONS and ext not in VIDEO_EXTENSIONS:
                skipped_count += 1
                self.msg_queue.put(("UPDATE_PROGRESS", (idx, total_files, file_path.name, "Dilewati (Bukan Media)")))
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
                if earliest_dt is None or created_dt < earliest_dt:
                    earliest_dt = created_dt
                if latest_dt is None or created_dt > latest_dt:
                    latest_dt = created_dt

                day_name = DAYS_INDONESIAN.get(created_dt.weekday(), "Lainnya")
                date_folder_name = created_dt.strftime(DATE_FOLDER_FORMAT)
                target_dir = source_dir / day_name / date_folder_name
                log_tag = f"[{day_name} -> {date_folder_name}]"
                tag_style = "SUCCESS"
            else:
                misc_count += 1
                target_dir = source_dir / MISC_FOLDER
                log_tag = f"[{MISC_FOLDER}]"
                tag_style = "WARN"

            dest_file_path = get_unique_destination_path(target_dir / file_path.name)

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
                tag_style = "INFO"

            rel_target = target_dir.relative_to(source_dir)
            log_line = f"[{action_desc}] {file_path.name}  ➔  {rel_target} {log_tag}"

            self.msg_queue.put(("UPDATE_PROGRESS", (idx, total_files, file_path.name, str(rel_target))))
            self.msg_queue.put(("LOG", (log_line, tag_style)))
            self.msg_queue.put(("STATS", (processed_count, classified_count, misc_count, skipped_count)))

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
                    self._log(f"📋 Memulai pemrosesan {total} file ditemukan...", "HEADER")

                elif msg_type == "UPDATE_PROGRESS":
                    idx, total, src_name, dst_info = payload
                    pct = int((idx / total) * 100) if total > 0 else 0
                    self.progress_bar["value"] = idx
                    self.lbl_progress_percent.configure(text=f"{idx} / {total} ({pct}%)")
                    self.lbl_current_source.configure(text=f"{src_name}  ➔  {dst_info}")

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
                    if isinstance(payload, tuple):
                        text, tag = payload
                        self._log(text, tag)
                    else:
                        self._log(payload)

                elif msg_type == "DONE":
                    proc, clas, misc, skip, earliest, latest, dry_run = payload

                    # Kembalikan state tombol
                    self.btn_start.configure(state=tk.NORMAL, bg="#16a34a", cursor="hand2")
                    self.btn_stop.configure(state=tk.DISABLED, bg="#94a3b8", cursor="arrow")
                    self.lbl_badge_status.configure(text="Selesai", bg="#dbeafe", fg="#1d4ed8")
                    self.lbl_footer_status.configure(text="✅ Pemrosesan selesai.")

                    range_text = "Tidak ada metadata tanggal yang ditemukan."
                    if earliest and latest:
                        range_text = f"{earliest.strftime('%d %B %Y')} s/d {latest.strftime('%d %B %Y')} ({earliest.strftime('%Y-%m-%d')} ~ {latest.strftime('%Y-%m-%d')})"
                        self.lbl_stat_date_range.configure(text=f"{earliest.strftime('%d-%m-%Y')} s/d {latest.strftime('%d-%m-%Y')}")

                    mode_title = "SIMULASI (DRY RUN)" if dry_run else "EKSEKUSI NYATA"
                    self._log("\n" + "=" * 55, "HEADER")
                    self._log(f"🎉 PENGELOMPOKAN SELESAI ({mode_title})", "HEADER")
                    self._log(f" • Total Media Diproses : {proc} file", "SUCCESS")
                    self._log(f" • Berhasil Dikelompokkan: {clas} file", "SUCCESS")
                    self._log(f" • Masuk Miscellaneous   : {misc} file", "WARN" if misc > 0 else "MUTED")
                    self._log(f" • File Dilewati         : {skip} file", "MUTED")
                    self._log(f" • Rentang Tanggal      : {range_text}", "INFO")
                    self._log("=" * 55 + "\n", "HEADER")

                    messagebox.showinfo(
                        "Pengelompokan Selesai",
                        f"Proses {mode_title} telah selesai!\n\n"
                        f"• Total Media Diproses: {proc}\n"
                        f"• Berhasil Terorganisir: {clas}\n"
                        f"• Masuk Miscellaneous: {misc}\n"
                        f"• Dilewati: {skip}\n\n"
                        f"📅 Rentang Tanggal:\n{range_text}"
                    )
        except queue.Empty:
            pass

        self.root.after(50, self._process_queue)


def main():
    root = tk.Tk()
    app = MediaOrganizerGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
