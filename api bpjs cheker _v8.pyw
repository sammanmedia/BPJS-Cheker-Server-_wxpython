# -*- coding: utf-8 -*-
"""
BPJS SERVER CHECKER V8
SAMMAN MEDIA
External CFG + Category Separator + Auto Cek + Export CSV
+ TAMBAHKAN SERVER (popup mudah)
+ LIST SERVER (cari, pilih banyak, hapus, aktif/nonaktif)
+ EDIT SERVER (tombol EDIT di dalam LIST SERVER, popup form edit)
+ Pemisah kategori satu baris bersih (tanpa ==== dan ----)
+ Semua error tampil di kotak pesan dan dicatat ke error.log
Windows 10 | wxPython
"""

import wx
import os
import re
import sys
import csv
import ssl
import time
import shutil
import socket
import datetime
import functools
import traceback
import webbrowser
import configparser
import urllib.request
import urllib.error
import threading

from concurrent.futures import ThreadPoolExecutor, as_completed


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(APP_DIR, "api-link-bpjs.cfg")
ERROR_LOG = os.path.join(APP_DIR, "error.log")

APP_VERSION = "V8"
MAX_WORKERS = 6

DEFAULT_CATEGORY = "SERVER TANPA KATEGORI"
NEW_SERVER_CATEGORY = "SERVER TAMBAHAN"

SECTION_RE = re.compile(r"^\s*\[([^\]]+)\]\s*$")

# Warna baris hasil
COLOR_WAIT = wx.Colour(255, 255, 255)
COLOR_OK = wx.Colour(210, 245, 215)      # hijau  : online
COLOR_AUTH = wx.Colour(215, 235, 250)    # biru   : online, butuh autentikasi
COLOR_WARN = wx.Colour(255, 245, 190)    # kuning : online, endpoint 4xx
COLOR_ERR5 = wx.Colour(255, 225, 180)    # oranye : server error 5xx
COLOR_DOWN = wx.Colour(255, 205, 205)    # merah  : gagal / down
COLOR_CANCEL = wx.Colour(225, 225, 225)  # abu    : dibatalkan


DEFAULT_HEADER = """# BPJS SERVER CHECKER CONFIGURATION
# SAMMAN MEDIA
# enabled = true / false
# Baris yang diawali // dipakai sebagai pemisah kategori di tabel.
# File ini ditulis ulang otomatis oleh aplikasi saat server
# ditambah atau dihapus (backup disimpan sebagai api-link-bpjs.cfg.bak)."""


DEFAULT_CFG = DEFAULT_HEADER + """

// CEK WEB BPJS PRODUCTION

[SERVER_1]
name = VClaim Production
url = https://apijkn.bpjs-kesehatan.go.id/vclaim-rest
enabled = true
description = VClaim (Virtual Claim) merupakan layanan integrasi BPJS Kesehatan untuk fasilitas kesehatan. Digunakan untuk SEP, pencarian peserta, referensi diagnosa, poli, dokter, dan proses klaim.

[SERVER_2]
name = Antrean RS Production
url = https://apijkn.bpjs-kesehatan.go.id/antreanrs
enabled = true
description = Layanan Antrean Online BPJS Kesehatan untuk integrasi antrean pasien, pendaftaran kunjungan, jadwal praktik dokter, dan informasi antrean rumah sakit.

[SERVER_3]
name = PCare Production
url = https://apijkn.bpjs-kesehatan.go.id/pcare-rest
enabled = true
description = Primary Care BPJS Kesehatan. Digunakan FKTP untuk integrasi data peserta, kunjungan, pelayanan, pemeriksaan, dan rujukan.

[SERVER_4]
name = Aplicares Production
url = https://new-api.bpjs-kesehatan.go.id:8080/aplicaresws/rest
enabled = true
description = Layanan informasi ketersediaan tempat tidur rumah sakit berdasarkan kelas dan ruang perawatan.

// CEK WEB BPJS DEVELOPMENT DAN PORTAL

[SERVER_5]
name = VClaim Development
url = https://apijkn-dev.bpjs-kesehatan.go.id/vclaim-rest-dev
enabled = true
description = Lingkungan pengembangan dan pengujian VClaim untuk menguji integrasi aplikasi sebelum digunakan di lingkungan production.

[SERVER_6]
name = Portal Developer BPJS
url = https://dvlp.bpjs-kesehatan.go.id/
enabled = true
description = Portal developer BPJS Kesehatan untuk referensi pengembangan integrasi, dokumentasi, dan informasi layanan API.

[SERVER_7]
name = VClaim BPJS
url = https://vclaim.bpjs-kesehatan.go.id/vclaim
enabled = true
description = Situs VClaim BPJS Kesehatan.

// CEK KONEKSI INTERNET

[SERVER_8]
name = Google
url = https://google.com
enabled = true
description = Situs Google (pembanding untuk memastikan koneksi internet normal).
"""


def bold_font(size=10):
    return wx.Font(
        size,
        wx.FONTFAMILY_DEFAULT,
        wx.FONTSTYLE_NORMAL,
        wx.FONTWEIGHT_BOLD
    )


# =====================================================
# PENANGANAN ERROR (supaya tidak diam-diam di file .pyw)
# =====================================================

def log_error(text):

    try:
        with open(ERROR_LOG, "a", encoding="utf-8") as f:
            f.write(
                "[" + datetime.datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ) + "]\n" + text + "\n\n"
            )
    except Exception:
        pass


def show_error(text, title="TERJADI ERROR"):

    log_error(text)

    try:
        wx.MessageBox(
            "Terjadi error:\n\n" + text[-1500:]
            + "\n\nDetail dicatat di:\n" + ERROR_LOG,
            title,
            wx.OK | wx.ICON_ERROR
        )
    except Exception:
        pass


def safe(func):
    """Bungkus handler agar error selalu tampil, bukan diam-diam."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):

        try:
            return func(*args, **kwargs)

        except Exception:
            show_error(traceback.format_exc())
            return None

    return wrapper


def global_excepthook(exc_type, exc_value, exc_tb):

    text = "".join(
        traceback.format_exception(exc_type, exc_value, exc_tb)
    )

    log_error(text)

    try:
        wx.CallAfter(
            wx.MessageBox,
            "Terjadi error:\n\n" + text[-1500:]
            + "\n\nDetail dicatat di:\n" + ERROR_LOG,
            "TERJADI ERROR",
            wx.OK | wx.ICON_ERROR
        )
    except Exception:
        pass


# =====================================================
# FUNGSI CFG (BACA / TULIS / VALIDASI)
# =====================================================

def read_cfg_raw(path=CFG_FILE):
    """
    Membaca CFG apa adanya (termasuk server enabled = false).
    Mengembalikan (header_lines, entries).
    Baris // menjadi kategori untuk section di bawahnya.
    """

    config = configparser.ConfigParser(
        interpolation=None,
        strict=True
    )

    config.optionxform = str.lower

    clean_lines = []
    header = []
    section_categories = {}

    current_category = DEFAULT_CATEGORY
    seen_first = False

    with open(path, "r", encoding="utf-8-sig") as f:

        for line in f:

            stripped = line.strip()

            if stripped.startswith("//"):

                seen_first = True

                category = stripped[2:].strip()

                if category:
                    current_category = category

                continue

            match = SECTION_RE.match(line)

            if match:

                seen_first = True

                section_categories[
                    match.group(1).strip()
                ] = current_category

            elif not seen_first:

                header.append(line.rstrip("\r\n"))

            clean_lines.append(line)

    while header and not header[-1].strip():
        header.pop()

    try:
        config.read_string("".join(clean_lines))

    except configparser.Error as e:
        raise ValueError(
            "Format CFG salah (periksa nama section, "
            "tanda '=' dan duplikat):\n" + str(e)
        )

    entries = []

    for section in config.sections():

        if not section.upper().startswith("SERVER_"):
            continue

        data = config[section]

        enabled_raw = data.get(
            "enabled", data.get("enable", "true")
        ).strip().lower()

        entries.append({
            "section": section,
            "name": data.get("name", "").strip(),
            "url": data.get("url", "").strip(),
            "enabled_raw": enabled_raw,
            "enabled": enabled_raw in ("true", "1", "yes"),
            "description": data.get(
                "description", "Tidak ada deskripsi."
            ).strip(),
            "category": section_categories.get(
                section, DEFAULT_CATEGORY
            )
        })

    return header, entries


def validate_enabled(entries):
    """Validasi lalu kembalikan daftar server yang aktif saja."""

    servers = []

    for e in entries:

        section = e["section"]

        if e["enabled_raw"] not in (
            "true", "false", "1", "0", "yes", "no"
        ):
            raise ValueError(
                f"{section}: nilai enabled tidak valid "
                f"(gunakan true / false)"
            )

        if not e["enabled"]:
            continue

        if not e["name"]:
            raise ValueError(f"{section}: nama server kosong")

        if not e["url"].lower().startswith(
            ("http://", "https://")
        ):
            raise ValueError(
                f"{section}: URL tidak valid "
                f"(harus diawali http:// atau https://)"
            )

        servers.append({
            "section": section,
            "name": e["name"],
            "url": e["url"],
            "description": e["description"] or "Tidak ada deskripsi.",
            "category": e["category"]
        })

    return servers


def one_line(text):
    """Rapikan teks menjadi satu baris (CFG tidak mendukung multiline)."""
    return " ".join(str(text).split())


def entry_key(e):
    """Kunci unik server (nama + url) untuk mencari entri."""
    return (e["name"].lower(), e["url"].rstrip("/").lower())


def write_cfg(header, entries, path=CFG_FILE):
    """
    Menulis ulang seluruh CFG dari daftar entries.
    Section dinomori ulang SERVER_1, SERVER_2, ...
    Backup lama disimpan ke <file>.bak
    """

    lines = []

    if header:
        lines.extend(header)
    else:
        lines.extend(DEFAULT_HEADER.splitlines())

    lines.append("")

    previous_category = None

    for number, e in enumerate(entries, start=1):

        category = one_line(e["category"]) or DEFAULT_CATEGORY

        if category != previous_category:

            lines.append("// " + category)
            lines.append("")

            previous_category = category

        lines.append(f"[SERVER_{number}]")
        lines.append("name = " + one_line(e["name"]))
        lines.append("url = " + one_line(e["url"]))
        lines.append(
            "enabled = "
            + ("true" if e["enabled"] else "false")
        )
        lines.append(
            "description = "
            + (one_line(e["description"]) or "Tidak ada deskripsi.")
        )
        lines.append("")

    text = "\n".join(lines).rstrip("\n") + "\n"

    if os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except Exception:
            pass

    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)

    os.replace(tmp, path)


def insert_entry(entries, new_entry):
    """Sisipkan server baru di akhir kelompok kategorinya."""

    last = None

    for i, e in enumerate(entries):
        if e["category"] == new_entry["category"]:
            last = i

    if last is None:
        entries.append(new_entry)
    else:
        entries.insert(last + 1, new_entry)


# =====================================================
# POPUP: TAMBAHKAN SERVER
# =====================================================

class AddServerDialog(wx.Dialog):

    def __init__(self, parent, categories, entries, edit_entry=None):

        wx.Dialog.__init__(
            self,
            parent,
            title="EDIT SERVER" if edit_entry else "TAMBAHKAN SERVER"
        )

        self.entries = entries

        # Kunci server yang sedang diedit (dilewati saat cek duplikat)
        self.edit_key = entry_key(edit_entry) if edit_entry else None

        categories = list(categories)

        if NEW_SERVER_CATEGORY not in categories:
            categories.append(NEW_SERVER_CATEGORY)

        main = wx.BoxSizer(wx.VERTICAL)

        info = wx.StaticText(
            self,
            label=(
                (
                    "Ubah data server lalu klik SIMPAN.\n"
                    "Perubahan langsung ditulis ke file api-link-bpjs.cfg."
                    if edit_entry else
                    "Isi data server lalu klik SIMPAN.\n"
                    "Data langsung masuk ke file api-link-bpjs.cfg."
                )
            )
        )

        main.Add(info, 0, wx.ALL, 15)

        grid = wx.FlexGridSizer(5, 2, 10, 10)
        grid.AddGrowableCol(1)

        v_center = wx.ALIGN_CENTER_VERTICAL

        # nama server
        grid.Add(
            wx.StaticText(self, label="nama server :"),
            0, v_center
        )

        self.txt_name = wx.TextCtrl(self, size=(430, -1))

        grid.Add(self.txt_name, 1, wx.EXPAND)

        # url
        grid.Add(
            wx.StaticText(self, label="url :"),
            0, v_center
        )

        self.txt_url = wx.TextCtrl(self, value="https://")

        grid.Add(self.txt_url, 1, wx.EXPAND)

        # enabled
        grid.Add(
            wx.StaticText(self, label="enabled :"),
            0, v_center
        )

        self.cho_enabled = wx.Choice(
            self, choices=["true", "false"]
        )

        self.cho_enabled.SetSelection(0)

        grid.Add(self.cho_enabled, 0)

        # kategori
        grid.Add(
            wx.StaticText(self, label="kategori :"),
            0, v_center
        )

        self.cmb_category = wx.ComboBox(
            self,
            value=NEW_SERVER_CATEGORY,
            choices=categories
        )

        grid.Add(self.cmb_category, 1, wx.EXPAND)

        # description
        grid.Add(
            wx.StaticText(self, label="description :"),
            0, wx.ALIGN_TOP
        )

        self.txt_desc = wx.TextCtrl(
            self,
            style=wx.TE_MULTILINE,
            size=(430, 95)
        )

        grid.Add(self.txt_desc, 1, wx.EXPAND)

        main.Add(
            grid, 1,
            wx.EXPAND | wx.LEFT | wx.RIGHT, 15
        )

        # tombol
        self.btn_save = wx.Button(
            self, wx.ID_ANY, "SIMPAN", size=(130, 38)
        )

        self.btn_cancel = wx.Button(
            self, wx.ID_CANCEL, "BATAL", size=(100, 38)
        )

        btns = wx.BoxSizer(wx.HORIZONTAL)

        btns.Add(self.btn_save, 0, wx.RIGHT, 10)
        btns.Add(self.btn_cancel, 0)

        main.Add(btns, 0, wx.ALIGN_RIGHT | wx.ALL, 15)

        self.SetSizer(main)
        main.Fit(self)

        self.btn_save.Bind(wx.EVT_BUTTON, self.on_save)

        if edit_entry:

            self.txt_name.SetValue(edit_entry["name"])
            self.txt_url.SetValue(edit_entry["url"])

            self.cho_enabled.SetSelection(
                0 if edit_entry["enabled"] else 1
            )

            self.cmb_category.SetValue(edit_entry["category"])

            desc = edit_entry["description"]

            if desc == "Tidak ada deskripsi.":
                desc = ""

            self.txt_desc.SetValue(desc)

        self.CentreOnParent()
        self.txt_name.SetFocus()

    @safe
    def on_save(self, event):

        name = one_line(self.txt_name.GetValue())
        url = self.txt_url.GetValue().strip()

        if not name:
            wx.MessageBox(
                "Nama server tidak boleh kosong.",
                "DATA BELUM LENGKAP",
                wx.OK | wx.ICON_WARNING
            )
            self.txt_name.SetFocus()
            return

        # Ramah pengguna: otomatis tambah https:// bila lupa.
        if url and not url.lower().startswith(
            ("http://", "https://")
        ):
            url = "https://" + url
            self.txt_url.SetValue(url)

        if len(url) <= len("https://"):
            wx.MessageBox(
                "URL belum diisi.",
                "DATA BELUM LENGKAP",
                wx.OK | wx.ICON_WARNING
            )
            self.txt_url.SetFocus()
            return

        for e in self.entries:

            # Saat edit, server itu sendiri tidak dianggap duplikat.
            if self.edit_key and entry_key(e) == self.edit_key:
                continue

            if e["name"].lower() == name.lower():
                wx.MessageBox(
                    f"Nama server '{name}' sudah ada di CFG.",
                    "DUPLIKAT",
                    wx.OK | wx.ICON_WARNING
                )
                self.txt_name.SetFocus()
                return

            if e["url"].rstrip("/").lower() == url.rstrip("/").lower():
                wx.MessageBox(
                    f"URL tersebut sudah dipakai oleh server "
                    f"'{e['name']}'.",
                    "DUPLIKAT",
                    wx.OK | wx.ICON_WARNING
                )
                self.txt_url.SetFocus()
                return

        self.EndModal(wx.ID_OK)

    def get_values(self):

        description = one_line(self.txt_desc.GetValue())

        if not description:
            description = "Tidak ada deskripsi."

        enabled = self.cho_enabled.GetStringSelection() == "true"

        return {
            "section": "",
            "name": one_line(self.txt_name.GetValue()),
            "url": self.txt_url.GetValue().strip(),
            "enabled_raw": "true" if enabled else "false",
            "enabled": enabled,
            "description": description,
            "category": (
                one_line(self.cmb_category.GetValue())
                or NEW_SERVER_CATEGORY
            )
        }


# =====================================================
# POPUP: EDIT SERVER
# =====================================================

class EditServerDialog(AddServerDialog):
    """Form edit: sama dengan form tambah, tetapi terisi data lama."""

    def __init__(self, parent, categories, entries, entry):

        AddServerDialog.__init__(
            self, parent, categories, entries, edit_entry=entry
        )


# =====================================================
# POPUP: LIST SERVER
# =====================================================

class ListServerDialog(wx.Dialog):

    def __init__(self, parent):

        wx.Dialog.__init__(
            self,
            parent,
            title="LIST SERVER TERSIMPAN",
            size=(1050, 620),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )

        self.frame = parent
        self.entries = []     # semua server di CFG
        self.view = []        # server yang sedang tampil (hasil cari)

        main = wx.BoxSizer(wx.VERTICAL)

        # ---- baris cari ----

        top = wx.BoxSizer(wx.HORIZONTAL)

        top.Add(
            wx.StaticText(self, label="Cari server:"),
            0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8
        )

        self.txt_search = wx.TextCtrl(self, size=(300, -1))

        top.Add(self.txt_search, 0, wx.RIGHT, 15)

        self.lbl_count = wx.StaticText(self, label="")
        self.lbl_count.SetFont(bold_font(10))

        top.Add(self.lbl_count, 1, wx.ALIGN_CENTER_VERTICAL)

        main.Add(top, 0, wx.EXPAND | wx.ALL, 12)

        # ---- petunjuk ----

        hint = wx.StaticText(
            self,
            label=(
                "Pilih satu atau beberapa server "
                "(tahan Ctrl atau Shift lalu klik), "
                "kemudian klik HAPUS TERPILIH. "
                "Bisa juga tekan tombol Delete di keyboard "
                "atau klik kanan. Untuk mengubah data, pilih satu "
                "server lalu klik EDIT (atau klik dua kali barisnya)."
            )
        )

        main.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 12)

        # ---- tabel ----

        self.list = wx.ListCtrl(
            self,
            style=wx.LC_REPORT | wx.BORDER_SUNKEN
        )

        columns = [
            ("NO", 50),
            ("KATEGORI", 230),
            ("NAMA SERVER", 220),
            ("URL", 400),
            ("ENABLED", 80)
        ]

        for i, (name, width) in enumerate(columns):
            self.list.InsertColumn(i, name, width=width)

        main.Add(
            self.list, 1,
            wx.EXPAND | wx.LEFT | wx.RIGHT, 12
        )

        # ---- tombol ----

        btns = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_add = wx.Button(
            self, wx.ID_ANY, "TAMBAH SERVER", size=(150, 40)
        )

        self.btn_edit = wx.Button(
            self, wx.ID_ANY, "EDIT", size=(110, 40)
        )

        self.btn_toggle = wx.Button(
            self, wx.ID_ANY, "AKTIF / NONAKTIF", size=(160, 40)
        )

        self.btn_delete = wx.Button(
            self, wx.ID_ANY, "HAPUS TERPILIH", size=(150, 40)
        )

        self.btn_close = wx.Button(
            self, wx.ID_CLOSE, "TUTUP", size=(110, 40)
        )

        btns.Add(self.btn_add, 0, wx.RIGHT, 10)
        btns.Add(self.btn_edit, 0, wx.RIGHT, 10)
        btns.Add(self.btn_toggle, 0, wx.RIGHT, 10)
        btns.Add(self.btn_delete, 0)
        btns.AddStretchSpacer(1)
        btns.Add(self.btn_close, 0)

        main.Add(btns, 0, wx.EXPAND | wx.ALL, 12)

        self.SetSizer(main)

        # ---- event ----

        self.txt_search.Bind(wx.EVT_TEXT, self.on_search)
        self.btn_add.Bind(wx.EVT_BUTTON, self.on_add)
        self.btn_edit.Bind(wx.EVT_BUTTON, self.on_edit)
        self.btn_toggle.Bind(wx.EVT_BUTTON, self.on_toggle)
        self.btn_delete.Bind(wx.EVT_BUTTON, self.on_delete)
        self.btn_close.Bind(
            wx.EVT_BUTTON, lambda e: self.EndModal(wx.ID_CLOSE)
        )

        self.list.Bind(
            wx.EVT_LIST_ITEM_SELECTED, self.on_select_change
        )
        self.list.Bind(
            wx.EVT_LIST_ITEM_DESELECTED, self.on_select_change
        )
        self.list.Bind(wx.EVT_LIST_KEY_DOWN, self.on_key)
        self.list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.on_edit)
        self.list.Bind(
            wx.EVT_LIST_ITEM_RIGHT_CLICK, self.on_right_click
        )

        self.SetEscapeId(wx.ID_CLOSE)

        self.refresh()

        self.CentreOnParent()

    # ---------- data ----------

    def refresh(self):
        """Baca ulang CFG lalu tampilkan sesuai kata pencarian."""

        try:
            header, entries = read_cfg_raw()

        except Exception as e:

            wx.MessageBox(
                f"Gagal membaca CFG:\n\n{e}",
                "CONFIG ERROR",
                wx.OK | wx.ICON_ERROR
            )

            entries = []

        self.entries = entries

        self.fill()

    def fill(self):

        keyword = self.txt_search.GetValue().strip().lower()

        self.view = []

        self.list.DeleteAllItems()

        for e in self.entries:

            haystack = (
                e["name"] + " " + e["url"] + " " + e["category"]
            ).lower()

            if keyword and keyword not in haystack:
                continue

            self.view.append(e)

        for i, e in enumerate(self.view):

            self.list.InsertItem(i, str(i + 1))
            self.list.SetItem(i, 1, e["category"])
            self.list.SetItem(i, 2, e["name"])
            self.list.SetItem(i, 3, e["url"])
            self.list.SetItem(
                i, 4, "true" if e["enabled"] else "false"
            )

            if not e["enabled"]:
                self.list.SetItemTextColour(
                    i, wx.Colour(140, 140, 140)
                )

        self.update_state()

    def selected_entries(self):

        result = []

        index = self.list.GetFirstSelected()

        while index != -1:

            if 0 <= index < len(self.view):
                result.append(self.view[index])

            index = self.list.GetNextSelected(index)

        return result

    def update_state(self):

        selected = len(self.selected_entries())

        self.lbl_count.SetLabel(
            f"Total: {len(self.entries)} server   |   "
            f"Tampil: {len(self.view)}   |   "
            f"Terpilih: {selected}"
        )

        self.btn_delete.Enable(selected > 0)
        self.btn_toggle.Enable(selected > 0)
        self.btn_edit.Enable(selected == 1)

    # ---------- event ----------

    def on_select_change(self, event):

        event.Skip()
        wx.CallAfter(self.update_state_safe)

    def update_state_safe(self):

        try:
            self.update_state()
        except RuntimeError:
            pass

    def on_search(self, event):

        self.fill()

    def on_key(self, event):

        if event.GetKeyCode() == wx.WXK_DELETE:
            self.on_delete(None)
        else:
            event.Skip()

    def on_right_click(self, event):

        menu = wx.Menu()

        item_edit = menu.Append(wx.ID_ANY, "Edit server")
        item_delete = menu.Append(wx.ID_ANY, "Hapus terpilih")
        item_toggle = menu.Append(wx.ID_ANY, "Aktif / nonaktif")

        self.Bind(wx.EVT_MENU, self.on_edit, item_edit)
        self.Bind(wx.EVT_MENU, self.on_delete, item_delete)
        self.Bind(wx.EVT_MENU, self.on_toggle, item_toggle)

        self.PopupMenu(menu)

        menu.Destroy()

    # ---------- aksi ----------

    def apply_change(self, change_func, status_text):
        """
        Baca CFG terbaru, ubah lewat change_func, tulis,
        muat ulang tabel utama, lalu segarkan daftar ini.
        """

        header, entries = read_cfg_raw()

        entries = change_func(entries)

        write_cfg(header, entries)

        try:
            self.frame.load_config()
            self.frame.status.SetLabel("STATUS: " + status_text)

        except Exception as e:

            wx.MessageBox(
                f"Perubahan tersimpan di CFG, tetapi tabel utama "
                f"gagal dimuat:\n\n{e}",
                "PERINGATAN",
                wx.OK | wx.ICON_WARNING
            )

        self.refresh()

    @safe
    def on_add(self, event):

        if self.frame.show_add_dialog(self):
            self.refresh()

    @safe
    def on_edit(self, event):

        selected = self.selected_entries()

        if len(selected) != 1:
            wx.MessageBox(
                "Pilih satu server saja yang ingin diedit.",
                "INFORMASI",
                wx.OK | wx.ICON_INFORMATION
            )
            return

        if self.frame.show_edit_dialog(selected[0], self):
            self.refresh()

    @safe
    def on_delete(self, event):

        selected = self.selected_entries()

        if not selected:
            wx.MessageBox(
                "Pilih dulu server yang ingin dihapus.",
                "INFORMASI",
                wx.OK | wx.ICON_INFORMATION
            )
            return

        names = [e["name"] for e in selected]

        preview = "\n".join("- " + n for n in names[:8])

        if len(names) > 8:
            preview += f"\n... dan {len(names) - 8} lainnya"

        answer = wx.MessageBox(
            f"Hapus {len(selected)} server berikut dari CFG?\n\n"
            f"{preview}",
            "KONFIRMASI HAPUS",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION
        )

        if answer != wx.YES:
            return

        keys = set(entry_key(e) for e in selected)

        self.apply_change(
            lambda entries: [
                e for e in entries if entry_key(e) not in keys
            ],
            f"{len(selected)} SERVER DIHAPUS"
        )

    @safe
    def on_toggle(self, event):

        selected = self.selected_entries()

        if not selected:
            wx.MessageBox(
                "Pilih dulu server yang ingin diubah.",
                "INFORMASI",
                wx.OK | wx.ICON_INFORMATION
            )
            return

        keys = set(entry_key(e) for e in selected)

        # Jika semua terpilih aktif -> nonaktifkan, selain itu aktifkan.
        new_state = not all(e["enabled"] for e in selected)

        def change(entries):

            for e in entries:

                if entry_key(e) in keys:
                    e["enabled"] = new_state
                    e["enabled_raw"] = "true" if new_state else "false"

            return entries

        self.apply_change(
            change,
            f"{len(selected)} SERVER "
            + ("DIAKTIFKAN" if new_state else "DINONAKTIFKAN")
        )


# =====================================================
# APLIKASI UTAMA
# =====================================================

class BPJSChecker(wx.Frame):

    def __init__(self):

        super().__init__(
            None,
            title=f"BPJS SERVER CHECKER {APP_VERSION} - SAMMAN MEDIA",
            size=(1350, 750)
        )

        self.servers = []
        self.rows = []              # isi tabel: category / server
        self.row_of_server = {}     # server_index -> nomor baris tabel
        self.results = {}           # server_index -> hasil pemeriksaan
        self.running = False
        self.closing = False
        self.dialog_open = False
        self.stop_event = threading.Event()
        self.run_settings = {"timeout": 8, "verify_ssl": True}

        self.create_cfg()

        panel = wx.Panel(self)
        panel.SetBackgroundColour("#EEEEEE")

        main = wx.BoxSizer(wx.VERTICAL)

        # =========================
        # HEADER
        # =========================

        header = wx.Panel(panel)
        header.SetBackgroundColour("#202020")

        hs = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(
            header,
            label=f"BPJS SERVER CHECKER {APP_VERSION}"
        )

        title.SetForegroundColour("white")
        title.SetFont(bold_font(20))

        subtitle = wx.StaticText(
            header,
            label="SAMMAN MEDIA | HTTP / HTTPS MONITOR"
        )

        subtitle.SetForegroundColour("#BBBBBB")

        hs.Add(title, 0, wx.ALL, 12)
        hs.Add(subtitle, 0, wx.LEFT | wx.BOTTOM, 12)

        header.SetSizer(hs)

        main.Add(header, 0, wx.EXPAND)

        # =========================
        # BUTTON
        # =========================

        buttons = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_check = wx.Button(
            panel, label="CEK SEMUA SERVER", size=(170, 42)
        )

        self.btn_add = wx.Button(
            panel, label="TAMBAHKAN SERVER", size=(180, 42)
        )

        self.btn_stop = wx.Button(
            panel, label="STOP", size=(90, 42)
        )

        self.btn_reload = wx.Button(
            panel, label="MUAT ULANG CFG", size=(150, 42)
        )

        self.btn_clear = wx.Button(
            panel, label="BERSIHKAN", size=(115, 42)
        )

        self.btn_open = wx.Button(
            panel, label="BUKA FILE CFG", size=(135, 42)
        )

        self.btn_export = wx.Button(
            panel, label="EXPORT CSV", size=(125, 42)
        )

        self.btn_list = wx.Button(
            panel, label="LIST SERVER", size=(125, 42)
        )

        self.btn_stop.Disable()

        for btn in [
            self.btn_check,
            self.btn_add,
            self.btn_stop,
            self.btn_reload,
            self.btn_clear,
            self.btn_open,
            self.btn_export,
            self.btn_list
        ]:
            buttons.Add(btn, 0, wx.ALL, 5)

        main.Add(buttons, 0, wx.ALL, 5)

        # =========================
        # OPSI
        # =========================

        options = wx.BoxSizer(wx.HORIZONTAL)

        center = wx.ALIGN_CENTER_VERTICAL | wx.ALL

        options.Add(
            wx.StaticText(panel, label="Timeout (detik):"),
            0, center, 6
        )

        self.spin_timeout = wx.SpinCtrl(
            panel, min=2, max=60, initial=8, size=(70, -1)
        )

        options.Add(self.spin_timeout, 0, center, 6)

        self.chk_ssl = wx.CheckBox(
            panel, label="Abaikan verifikasi sertifikat SSL"
        )

        options.Add(self.chk_ssl, 0, center, 12)

        self.chk_auto = wx.CheckBox(
            panel, label="Cek otomatis tiap"
        )

        options.Add(self.chk_auto, 0, center, 12)

        self.spin_auto = wx.SpinCtrl(
            panel, min=15, max=3600, initial=60, size=(80, -1)
        )

        options.Add(self.spin_auto, 0, center, 6)

        options.Add(
            wx.StaticText(panel, label="detik"),
            0, center, 6
        )

        main.Add(options, 0, wx.LEFT, 5)

        # =========================
        # TABLE
        # =========================

        self.table = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_SUNKEN
        )

        columns = [
            ("NO", 45),
            ("NAMA SERVER", 190),
            ("URL", 350),
            ("HTTP", 65),
            ("RESPON", 85),
            ("STATUS", 170),
            ("DESKRIPSI", 650)
        ]

        for i, (name, width) in enumerate(columns):
            self.table.InsertColumn(i, name, width=width)

        main.Add(
            self.table,
            1,
            wx.EXPAND | wx.ALL,
            10
        )

        # =========================
        # PROGRESS
        # =========================

        self.progress = wx.Gauge(
            panel, range=100, size=(-1, 20)
        )

        main.Add(
            self.progress,
            0,
            wx.EXPAND | wx.LEFT | wx.RIGHT,
            10
        )

        # =========================
        # STATUS
        # =========================

        self.status = wx.StaticText(
            panel,
            label="STATUS: MEMUAT KONFIGURASI..."
        )

        self.status.SetFont(bold_font(10))

        main.Add(self.status, 0, wx.ALL, 10)

        panel.SetSizer(main)

        # =========================
        # TIMER AUTO CEK
        # =========================

        self.timer = wx.Timer(self)

        # =========================
        # EVENTS
        # =========================

        self.btn_check.Bind(wx.EVT_BUTTON, self.start_check)
        self.btn_add.Bind(wx.EVT_BUTTON, self.add_server)
        self.btn_stop.Bind(wx.EVT_BUTTON, self.stop_check)
        self.btn_reload.Bind(wx.EVT_BUTTON, self.reload_config)
        self.btn_clear.Bind(wx.EVT_BUTTON, self.clear_table)
        self.btn_open.Bind(wx.EVT_BUTTON, self.open_cfg)
        self.btn_export.Bind(wx.EVT_BUTTON, self.export_csv)
        self.btn_list.Bind(wx.EVT_BUTTON, self.list_server)

        self.chk_auto.Bind(wx.EVT_CHECKBOX, self.on_auto_toggle)
        self.spin_auto.Bind(wx.EVT_SPINCTRL, self.on_auto_toggle)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)

        self.table.Bind(
            wx.EVT_LIST_ITEM_ACTIVATED, self.on_row_activated
        )
        self.table.Bind(wx.EVT_SIZE, self.on_table_size)

        self.Bind(wx.EVT_CLOSE, self.on_close)

        try:
            self.load_config()
        except Exception as e:
            self.status.SetLabel("STATUS: CFG ERROR")
            wx.MessageBox(
                f"Gagal membaca CFG:\n\n{e}\n\nFile: {CFG_FILE}",
                "CONFIG ERROR",
                wx.OK | wx.ICON_ERROR
            )

        self.Centre()
        self.Maximize(True)

        wx.CallAfter(self.fit_last_column)

    # =================================
    # UTIL
    # =================================

    def post(self, func, *args):
        """Panggil fungsi GUI dari thread lain dengan aman."""

        if not self.closing:
            wx.CallAfter(self._safe_call, func, *args)

    def _safe_call(self, func, *args):

        if self.closing:
            return

        try:
            func(*args)
        except RuntimeError:
            # Widget sudah dihancurkan saat aplikasi ditutup.
            pass

    def fit_last_column(self):

        if self.closing:
            return

        try:
            total = self.table.GetClientSize().width
            used = sum(
                self.table.GetColumnWidth(i) for i in range(6)
            )

            self.table.SetColumnWidth(
                6, max(300, total - used - 4)
            )

        except RuntimeError:
            pass

    def fit_category_column(self):
        """
        Lebarkan kolom NAMA SERVER secukupnya agar teks kategori
        (misal "CEK WEB BPJS DEVELOPMENT DAN PORTAL") tidak terpotong.
        """

        try:
            dc = wx.ClientDC(self.table)
            dc.SetFont(bold_font(10))

            widest = 0

            for server in self.servers:
                width = dc.GetTextExtent(server["category"]).GetWidth()
                widest = max(widest, width)

            self.table.SetColumnWidth(1, max(190, widest + 30))

        except RuntimeError:
            pass

    def on_table_size(self, event):

        event.Skip()
        wx.CallAfter(self.fit_last_column)

    def set_busy(self, busy):
        """Aktif/nonaktifkan tombol saat pemeriksaan berjalan."""

        for ctrl in [
            self.btn_check,
            self.btn_add,
            self.btn_reload,
            self.btn_open,
            self.btn_clear,
            self.btn_export,
            self.btn_list,
            self.spin_timeout,
            self.chk_ssl
        ]:
            ctrl.Enable(not busy)

        self.btn_stop.Enable(busy)

    # =================================
    # CREATE CFG
    # =================================

    def create_cfg(self):

        if not os.path.exists(CFG_FILE):

            with open(
                CFG_FILE, "w", encoding="utf-8"
            ) as f:
                f.write(DEFAULT_CFG)

    # =================================
    # OPEN CFG
    # =================================

    @safe
    def open_cfg(self, event):

        try:

            if os.name == "nt":
                os.startfile(CFG_FILE)
            else:
                import subprocess

                if sys.platform == "darwin":
                    subprocess.Popen(["open", CFG_FILE])
                else:
                    subprocess.Popen(["xdg-open", CFG_FILE])

            self.status.SetLabel(
                "STATUS: FILE CFG DIBUKA"
            )

        except Exception as e:

            wx.MessageBox(
                str(e),
                "GAGAL MEMBUKA CFG",
                wx.OK | wx.ICON_ERROR
            )

    # =================================
    # PARSE CFG
    # =================================

    def parse_config(self):

        header, entries = read_cfg_raw()

        return validate_enabled(entries)

    # =================================
    # LOAD CONFIG
    # =================================

    def load_config(self):

        servers = self.parse_config()

        self.servers = servers
        self.rows = []
        self.row_of_server = {}
        self.results = {}

        self.table.DeleteAllItems()

        self.fit_category_column()

        previous_category = None
        number = 0

        for index, server in enumerate(self.servers):

            category = server["category"]

            # BARIS PEMISAH KATEGORI

            if category != previous_category:

                row = self.table.InsertItem(
                    self.table.GetItemCount(), ""
                )

                # Teks kategori polos, kolom lain dibiarkan kosong.
                self.table.SetItem(row, 1, category)

                self.table.SetItemBackgroundColour(
                    row, wx.Colour(45, 65, 85)
                )

                self.table.SetItemTextColour(
                    row, wx.WHITE
                )

                self.table.SetItemFont(row, bold_font(10))

                self.rows.append({
                    "type": "category"
                })

                previous_category = category

            # BARIS DATA SERVER

            number += 1

            row = self.table.InsertItem(
                self.table.GetItemCount(),
                str(number)
            )

            self.table.SetItem(row, 1, server["name"])
            self.table.SetItem(row, 2, server["url"])
            self.table.SetItem(row, 3, "-")
            self.table.SetItem(row, 4, "-")
            self.table.SetItem(row, 5, "MENUNGGU")
            self.table.SetItem(
                row, 6, server["description"]
            )

            self.rows.append({
                "type": "server",
                "server_index": index
            })

            self.row_of_server[index] = row

        self.progress.SetRange(
            max(1, len(self.servers))
        )

        self.progress.SetValue(0)

        self.status.SetLabel(
            f"STATUS: {len(self.servers)} SERVER AKTIF | CFG BERHASIL DIMUAT"
        )

        self.fit_last_column()

    # =================================
    # RELOAD
    # =================================

    @safe
    def reload_config(self, event):

        if self.running:
            return

        try:

            self.load_config()

            wx.MessageBox(
                f"Konfigurasi berhasil dimuat!\n\n"
                f"Server aktif: {len(self.servers)}\n\n"
                f"File: {CFG_FILE}",
                "CONFIG BERHASIL",
                wx.OK | wx.ICON_INFORMATION
            )

        except Exception as e:

            wx.MessageBox(
                f"Gagal membaca CFG:\n\n{e}",
                "CONFIG ERROR",
                wx.OK | wx.ICON_ERROR
            )

    # =================================
    # TAMBAHKAN SERVER
    # =================================

    def show_add_dialog(self, parent_window=None):
        """
        Tampilkan popup tambah server. Mengembalikan True bila
        server berhasil disimpan ke CFG.
        """

        header, entries = read_cfg_raw()

        categories = []

        for e in entries:
            if e["category"] not in categories:
                categories.append(e["category"])

        dlg = AddServerDialog(
            parent_window or self, categories, entries
        )

        try:
            result = dlg.ShowModal()
            values = dlg.get_values()

        finally:
            dlg.Destroy()

        if result != wx.ID_OK:
            return False

        insert_entry(entries, values)

        write_cfg(header, entries)

        self.load_config()

        if values["enabled"]:

            self.status.SetLabel(
                f"STATUS: SERVER '{values['name']}' DISIMPAN | "
                f"{len(self.servers)} SERVER AKTIF"
            )

        else:

            self.status.SetLabel(
                f"STATUS: SERVER '{values['name']}' DISIMPAN "
                f"(enabled = false, tidak ditampilkan di tabel)"
            )

        return True

    def show_edit_dialog(self, entry, parent_window=None):
        """
        Tampilkan popup edit server. Mengembalikan True bila CFG
        berubah (atau perlu dimuat ulang), False bila dibatalkan.
        """

        header, entries = read_cfg_raw()

        old_key = entry_key(entry)

        position = None

        for i, e in enumerate(entries):
            if entry_key(e) == old_key:
                position = i
                break

        if position is None:

            wx.MessageBox(
                "Server tidak ditemukan di CFG (mungkin file CFG "
                "sudah diubah). Daftar akan dimuat ulang.",
                "SERVER TIDAK DITEMUKAN",
                wx.OK | wx.ICON_WARNING
            )

            return True

        original = entries[position]

        categories = []

        for e in entries:
            if e["category"] not in categories:
                categories.append(e["category"])

        dlg = EditServerDialog(
            parent_window or self, categories, entries, original
        )

        try:
            result = dlg.ShowModal()
            values = dlg.get_values()

        finally:
            dlg.Destroy()

        if result != wx.ID_OK:
            return False

        if values["category"] == original["category"]:
            # Kategori sama: posisi di CFG tetap.
            entries[position] = values

        else:
            # Pindah kategori: taruh di akhir kategori barunya.
            del entries[position]
            insert_entry(entries, values)

        write_cfg(header, entries)

        self.load_config()

        self.status.SetLabel(
            f"STATUS: SERVER '{values['name']}' DIPERBARUI | "
            f"{len(self.servers)} SERVER AKTIF"
        )

        return True

    @safe
    def add_server(self, event):

        if self.running or self.dialog_open:
            return

        self.dialog_open = True

        try:
            self.show_add_dialog()

        finally:
            self.dialog_open = False

    # =================================
    # LIST SERVER
    # =================================

    @safe
    def list_server(self, event):

        if self.running or self.dialog_open:
            return

        self.dialog_open = True

        try:
            dlg = ListServerDialog(self)

            try:
                dlg.ShowModal()

            finally:
                dlg.Destroy()

        finally:
            self.dialog_open = False

    # =================================
    # HTTP CHECKER
    # =================================

    def make_ssl_context(self, verify):

        context = ssl.create_default_context()

        if not verify:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        return context

    def check_server(self, server, timeout, verify_ssl):

        start = time.perf_counter()

        try:

            request = urllib.request.Request(
                server["url"],
                headers={
                    "User-Agent": f"SammanMedia-BPJS-Checker/{APP_VERSION}",
                    "Accept": "*/*"
                },
                method="GET"
            )

            with urllib.request.urlopen(
                request,
                timeout=timeout,
                context=self.make_ssl_context(verify_ssl)
            ) as response:

                code = response.status

        except urllib.error.HTTPError as e:

            # Server merespons dengan kode HTTP (4xx / 5xx).
            code = e.code

            try:
                e.close()
            except Exception:
                pass

        except urllib.error.URLError as e:

            reason = e.reason

            if isinstance(reason, ssl.SSLError):
                return self.error_result(
                    start, "SSL ERROR", str(reason)
                )

            if isinstance(reason, (socket.timeout, TimeoutError)):
                return self.error_result(
                    start, "TIMEOUT",
                    f"Koneksi melebihi {timeout} detik."
                )

            if isinstance(reason, socket.gaierror):
                return self.error_result(
                    start, "DNS GAGAL",
                    "Nama domain tidak dapat ditemukan: "
                    + str(reason)
                )

            if isinstance(reason, ConnectionRefusedError):
                return self.error_result(
                    start, "KONEKSI DITOLAK",
                    "Server menolak koneksi: " + str(reason)
                )

            return self.error_result(
                start, "GAGAL KONEKSI", str(reason)
            )

        except ssl.SSLError as e:

            return self.error_result(
                start, "SSL ERROR", str(e)
            )

        except (socket.timeout, TimeoutError):

            return self.error_result(
                start, "TIMEOUT",
                f"Koneksi melebihi {timeout} detik."
            )

        except Exception as e:

            return self.error_result(
                start, "ERROR", str(e)
            )

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        if code < 400:

            status = "ONLINE"
            detail = "Server memberikan respons HTTP normal."
            level = "ok"

        elif code in (401, 403):

            status = "ONLINE - BUTUH AUTH"
            detail = (
                "Server aktif. Endpoint memerlukan autentikasi "
                "(consumer ID / signature), ini normal untuk API BPJS."
            )
            level = "auth"

        elif code < 500:

            status = "ONLINE - HTTP " + str(code)
            detail = (
                "Server merespons, tetapi alamat endpoint "
                "tidak menyediakan halaman (HTTP " + str(code) + ")."
            )
            level = "warn"

        elif code in (502, 503, 504):

            status = "SERVER DOWN"
            detail = (
                "Gateway/server tidak tersedia (HTTP "
                + str(code) + ")."
            )
            level = "down"

        else:

            status = "SERVER ERROR"
            detail = (
                "Server memberikan respons kesalahan HTTP "
                + str(code) + "."
            )
            level = "err5"

        return {
            "code": str(code),
            "elapsed": elapsed,
            "status": status,
            "detail": detail,
            "level": level
        }

    def error_result(self, start, status, detail):

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        return {
            "code": "-",
            "elapsed": elapsed,
            "status": status,
            "detail": detail,
            "level": "down"
        }

    # =================================
    # START
    # =================================

    @safe
    def start_check(self, event=None):

        if self.running or self.dialog_open:
            return

        if not self.servers:
            if event is not None:
                wx.MessageBox(
                    "Tidak ada server aktif di CFG.",
                    "INFORMASI",
                    wx.OK | wx.ICON_INFORMATION
                )
            return

        # Ambil pengaturan di thread utama, lalu kirim ke thread kerja.
        self.run_settings = {
            "timeout": self.spin_timeout.GetValue(),
            "verify_ssl": not self.chk_ssl.GetValue()
        }

        self.running = True
        self.stop_event.clear()
        self.results = {}

        self.set_busy(True)

        self.progress.SetRange(len(self.servers))
        self.progress.SetValue(0)

        for index, server in enumerate(self.servers):

            row = self.row_of_server[index]

            self.table.SetItem(row, 3, "-")
            self.table.SetItem(row, 4, "-")
            self.table.SetItem(row, 5, "MEMERIKSA")
            self.table.SetItem(row, 6, server["description"])

            self.table.SetItemBackgroundColour(
                row, COLOR_WAIT
            )

        self.status.SetLabel(
            "STATUS: MEMERIKSA SERVER..."
        )

        threading.Thread(
            target=self.run_checks,
            args=(list(self.servers), dict(self.run_settings)),
            daemon=True
        ).start()

    # =================================
    # BACKGROUND
    # =================================

    def run_checks(self, servers, settings):

        completed = 0
        total = len(servers)

        executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
        futures = {}

        try:

            futures = {
                executor.submit(
                    self.check_server,
                    server,
                    settings["timeout"],
                    settings["verify_ssl"]
                ): index

                for index, server in enumerate(servers)
            }

            for future in as_completed(futures):

                if self.stop_event.is_set():
                    break

                index = futures[future]

                try:
                    result = future.result()

                except Exception as e:

                    result = {
                        "code": "-",
                        "elapsed": 0,
                        "status": "ERROR",
                        "detail": str(e),
                        "level": "down"
                    }

                completed += 1

                self.post(
                    self.update_result,
                    index,
                    result,
                    completed,
                    total
                )

        finally:

            for f in list(futures):
                f.cancel()

            executor.shutdown(wait=False)

        self.post(self.finish_check)

    # =================================
    # UPDATE RESULT
    # =================================

    def update_result(self, server_index, result, completed, total):

        row = self.row_of_server.get(server_index)

        if row is None:
            return

        result["time"] = datetime.datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )

        self.results[server_index] = result

        self.table.SetItem(row, 3, result["code"])

        self.table.SetItem(
            row, 4,
            f'{result["elapsed"]:.0f} ms'
        )

        self.table.SetItem(
            row, 5, result["status"]
        )

        original = self.servers[
            server_index
        ]["description"]

        self.table.SetItem(
            row, 6,
            original + " | HASIL: " + result["detail"]
        )

        colors = {
            "ok": COLOR_OK,
            "auth": COLOR_AUTH,
            "warn": COLOR_WARN,
            "err5": COLOR_ERR5,
            "down": COLOR_DOWN
        }

        self.table.SetItemBackgroundColour(
            row, colors.get(result["level"], COLOR_DOWN)
        )

        self.progress.SetValue(completed)

        self.status.SetLabel(
            f"STATUS: MEMERIKSA {completed}/{total} SERVER"
        )

    # =================================
    # STOP
    # =================================

    def stop_check(self, event):

        self.stop_event.set()

        self.status.SetLabel(
            "STATUS: MENGHENTIKAN PEMERIKSAAN..."
        )

        self.btn_stop.Disable()

    # =================================
    # FINISH
    # =================================

    def finish_check(self):

        self.running = False

        self.set_busy(False)

        stopped = self.stop_event.is_set()

        # Server yang belum sempat diperiksa ditandai dibatalkan.
        if stopped:

            for index, row in self.row_of_server.items():

                if index not in self.results:

                    self.table.SetItem(row, 3, "-")
                    self.table.SetItem(row, 4, "-")
                    self.table.SetItem(row, 5, "DIBATALKAN")
                    self.table.SetItemBackgroundColour(
                        row, COLOR_CANCEL
                    )

        online = sum(
            1 for r in self.results.values()
            if r["status"].startswith("ONLINE")
        )

        problem = len(self.results) - online

        if stopped:

            self.status.SetLabel(
                f"STATUS: PEMERIKSAAN DIHENTIKAN | "
                f"ONLINE {online} | BERMASALAH {problem}"
            )

        else:

            self.status.SetLabel(
                f"STATUS: PEMERIKSAAN SELESAI | "
                f"ONLINE {online} | BERMASALAH {problem} | "
                f"{datetime.datetime.now().strftime('%H:%M:%S')}"
            )

    # =================================
    # CLEAR
    # =================================

    @safe
    def clear_table(self, event):

        if self.running:
            return

        self.results = {}

        for index, row in self.row_of_server.items():

            self.table.SetItem(row, 3, "-")
            self.table.SetItem(row, 4, "-")
            self.table.SetItem(row, 5, "MENUNGGU")

            self.table.SetItem(
                row, 6,
                self.servers[index]["description"]
            )

            self.table.SetItemBackgroundColour(
                row, COLOR_WAIT
            )

        self.progress.SetValue(0)

        self.status.SetLabel(
            "STATUS: SIAP MELAKUKAN PEMERIKSAAN"
        )

    # =================================
    # EXPORT CSV
    # =================================

    @safe
    def export_csv(self, event):

        if self.running:
            return

        if not self.results:
            wx.MessageBox(
                "Belum ada hasil pemeriksaan untuk diexport.\n"
                "Jalankan CEK SEMUA SERVER terlebih dahulu.",
                "INFORMASI",
                wx.OK | wx.ICON_INFORMATION
            )
            return

        default_name = (
            "hasil-cek-bpjs-"
            + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            + ".csv"
        )

        dlg = wx.FileDialog(
            self,
            "Simpan hasil pemeriksaan",
            defaultDir=APP_DIR,
            defaultFile=default_name,
            wildcard="File CSV (*.csv)|*.csv",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        )

        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return

        path = dlg.GetPath()
        dlg.Destroy()

        try:

            with open(
                path, "w", newline="", encoding="utf-8-sig"
            ) as f:

                writer = csv.writer(f, delimiter=";")

                writer.writerow([
                    "NO", "KATEGORI", "NAMA SERVER", "URL",
                    "HTTP", "RESPON (ms)", "STATUS", "HASIL",
                    "WAKTU CEK"
                ])

                for index, server in enumerate(self.servers):

                    r = self.results.get(index)

                    if r is None:
                        continue

                    writer.writerow([
                        index + 1,
                        server["category"],
                        server["name"],
                        server["url"],
                        r["code"],
                        f'{r["elapsed"]:.0f}',
                        r["status"],
                        r["detail"],
                        r.get("time", "")
                    ])

            self.status.SetLabel(
                "STATUS: HASIL DISIMPAN KE " + path
            )

        except Exception as e:

            wx.MessageBox(
                f"Gagal menyimpan file:\n\n{e}",
                "EXPORT GAGAL",
                wx.OK | wx.ICON_ERROR
            )

    # =================================
    # DOUBLE CLICK = BUKA URL
    # =================================

    @safe
    def on_row_activated(self, event):

        row = event.GetIndex()

        if row < 0 or row >= len(self.rows):
            return

        item = self.rows[row]

        if item["type"] != "server":
            return

        url = self.servers[item["server_index"]]["url"]

        try:
            webbrowser.open(url)
        except Exception as e:
            wx.MessageBox(
                str(e),
                "GAGAL MEMBUKA BROWSER",
                wx.OK | wx.ICON_ERROR
            )

    # =================================
    # AUTO CEK
    # =================================

    @safe
    def on_auto_toggle(self, event):

        self.timer.Stop()

        if self.chk_auto.GetValue():

            seconds = self.spin_auto.GetValue()

            self.timer.Start(seconds * 1000)

            self.status.SetLabel(
                f"STATUS: CEK OTOMATIS AKTIF TIAP {seconds} DETIK"
            )

            if not self.running:
                self.start_check(None)

        else:

            if not self.running:
                self.status.SetLabel(
                    "STATUS: CEK OTOMATIS DIMATIKAN"
                )

    def on_timer(self, event):

        if (
            not self.running
            and not self.closing
            and not self.dialog_open
        ):
            self.start_check(None)

    # =================================
    # CLOSE
    # =================================

    def on_close(self, event):

        self.closing = True

        self.timer.Stop()
        self.stop_event.set()

        self.Destroy()


if __name__ == "__main__":

    sys.excepthook = global_excepthook

    app = wx.App(False)

    frame = BPJSChecker()
    frame.Show()

    app.MainLoop()
