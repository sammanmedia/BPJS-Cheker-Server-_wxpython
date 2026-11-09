# Obrolan v9
# - Tombol "Chatroom Online" diperbaiki: kontrol klik sendiri (bukan StaticText), klik cepat/dobel
#   tetap terbaca, popup selalu terbuka (ada pesan "memuat"/error kalau data belum ada), cadangan menu.
# - Jendela dibuka setinggi layar (area kerja, di atas taskbar), nempel kanan/kiri/tengah (obrolan.cfg).
# - Centang ala WhatsApp pada pesan sendiri: 1 centang = sampai server, 2 centang hijau = sudah dibaca
#   (butuh presence.php versi v9).
# - (dari v8) background gambar/GIF, daftar online, reply, notif reply hanya ke yang dibalas.
import base64
import filecmp
import getpass
import json
import math
import re
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
import uuid
import wave
from datetime import datetime, date, timedelta
from pathlib import Path
from urllib.parse import urlparse

import requests
import wx
import wx.adv
from wx.lib.buttons import GenBitmapTextButton

CFG_PATH = Path.home() / ".obrolan_config.json"   # nama, popup, suara (per user)

# ====================== obrolan.cfg (pengaturan + data dropdown) ======================
if getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
APP_CFG_PATH = APP_DIR / "obrolan.cfg"
BG_DIR = APP_DIR / "background"   # hasil salinan gambar dari tombol [Pilih Gambar]

DEFAULT_APP_CFG = {
    "Pengaturan": {
        "poll_ms": 2000,
        "lock_port": 47653,   # port lokal untuk deteksi "aplikasi sudah terbuka"
        "aktif": 0,           # index chatroom yang sedang dipakai
        "full_tinggi": True,  # jendela dibuka setinggi layar
        "dock": "kanan",      # kanan | kiri | tengah | bebas (posisi horizontal saat dibuka)
    },
    "DropDown": [
        {"chatroom": "Semua Unit", "api": "http://192.168.60.228/api.php"},
        {"chatroom": "Lantai 1", "api": "http://192.168.60.228/api-lantai1.php"},
    ],
}


# kunci opsional per chatroom (background)
ROOM_EXTRA_KEYS = ("background", "bg_mode", "bg_warna", "presence")


def _clamp(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except Exception:
        return default


def load_app_cfg():
    cfg = json.loads(json.dumps(DEFAULT_APP_CFG))  # deep copy
    try:
        raw = json.loads(APP_CFG_PATH.read_text(encoding="utf-8"))
        if isinstance(raw.get("Pengaturan"), dict):
            cfg["Pengaturan"].update(raw["Pengaturan"])
        rooms = []
        for r in raw.get("DropDown", []):
            if isinstance(r, dict) and r.get("chatroom") and r.get("api"):
                room = {"chatroom": str(r["chatroom"]), "api": str(r["api"])}
                for k in ROOM_EXTRA_KEYS:
                    if r.get(k):
                        room[k] = str(r[k])
                rooms.append(room)
        if rooms:
            cfg["DropDown"] = rooms
    except Exception:
        pass
    p = cfg["Pengaturan"]
    p["poll_ms"] = _clamp(p.get("poll_ms"), 500, 60000, 2000)
    p["lock_port"] = _clamp(p.get("lock_port"), 1024, 65535, 47653)
    p["aktif"] = _clamp(p.get("aktif"), 0, len(cfg["DropDown"]) - 1, 0)
    if not APP_CFG_PATH.exists():
        save_app_cfg(cfg)  # buat file default saat pertama kali jalan
    return cfg


def save_app_cfg(cfg):
    try:
        APP_CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ====================== config user (cache nama, popup, suara) ======================
def load_config():
    cfg = {"name": getpass.getuser() or "python", "popup": True, "sound": True}
    try:
        cfg.update(json.loads(CFG_PATH.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_cid():
    """cid disimpan di file lokal supaya setelah aplikasi ditutup,
    pesan lama tetap dikenali sebagai 'punya saya' (bubble kanan, biru)."""
    p = Path.home() / ".obrolan_cid"
    try:
        c = p.read_text().strip()
        if c:
            return c
    except Exception:
        pass
    c = uuid.uuid4().hex[:24]
    try:
        p.write_text(c)
    except Exception:
        pass
    return c


CID = load_cid()


# ====================== background: salin file ke folder /background ======================
def import_background(src):
    """Salin gambar pilihan user ke APP_DIR/background. Return path relatif ('background/x.gif')."""
    src = Path(src)
    BG_DIR.mkdir(parents=True, exist_ok=True)
    dst = BG_DIR / src.name
    if dst.exists():
        try:
            if src.resolve() == dst.resolve() or filecmp.cmp(src, dst, shallow=False):
                return "background/" + dst.name   # file yang sama -> tidak perlu disalin lagi
        except OSError:
            pass
        n = 1
        while (BG_DIR / f"{src.stem}_{n}{src.suffix}").exists():   # nama bentrok, isi beda
            n += 1
        dst = BG_DIR / f"{src.stem}_{n}{src.suffix}"
    shutil.copy2(src, dst)
    return "background/" + dst.name


# ====================== presence (siapa yang sedang membuka chatroom) ======================
def presence_endpoint(room):
    """-> (url presence.php, kunci room). Default: presence.php di folder yang sama dengan api room.
    Bisa diganti lewat kunci "presence" pada item DropDown di obrolan.cfg."""
    api = room["api"]
    url = room.get("presence") or (api.rsplit("/", 1)[0] + "/presence.php")
    key = re.sub(r"[^A-Za-z0-9_\-]", "", Path(urlparse(api).path).stem) or "default"
    return url, key


# ====================== format pesan reply ======================
# Reply dikirim sebagai teks biasa berawalan tag  [[R:<base64 json>]]\n<isi pesan>
# sehingga server PHP tidak perlu diubah sama sekali.
REPLY_RE = re.compile(r"^\[\[R:([A-Za-z0-9_\-=]+)\]\]\s*")


def encode_reply(rep, body):
    data = {"c": rep["c"], "n": rep["n"][:24], "t": rep["t"], "s": rep["s"][:50]}
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return "[[R:" + base64.urlsafe_b64encode(raw).decode("ascii") + "]]\n" + body


def parse_message(text):
    """-> (reply|None, isi_pesan). reply = {c: cid, n: nama, t: waktu, s: cuplikan}"""
    m = REPLY_RE.match(text)
    if not m:
        return None, text
    try:
        d = json.loads(base64.urlsafe_b64decode(m.group(1).encode("ascii")).decode("utf-8"))
        rep = {"c": str(d["c"]), "n": str(d.get("n", "?")),
               "t": int(d.get("t", 0)), "s": str(d.get("s", ""))}
    except Exception:
        return None, text
    return rep, text[m.end():]


# ====================== ikon logo (digambar lewat kode) ======================
BLUE = (24, 119, 242)
WHITE = (255, 255, 255)
RED = (230, 50, 50)


def _in_rrect(u, v, x0, y0, x1, y1, r):
    cx = min(max(u, x0 + r), x1 - r)
    cy = min(max(v, y0 + r), y1 - r)
    return (u - cx) ** 2 + (v - cy) ** 2 <= r * r


def _in_tri(u, v, a, b, c):
    def s(p, q, r):
        return (p[0] - r[0]) * (q[1] - r[1]) - (q[0] - r[0]) * (p[1] - r[1])
    p = (u, v)
    d1, d2, d3 = s(p, a, b), s(p, b, c), s(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


def _sample(u, v, alert):
    if alert:  # titik merah penanda pesan baru
        d = (u - .76) ** 2 + (v - .24) ** 2
        if d <= .21 ** 2:
            return RED
        if d <= .27 ** 2:
            return WHITE
    if (u - .5) ** 2 + (v - .5) ** 2 > .25:
        return None
    if _in_rrect(u, v, .17, .24, .83, .62, .13) or _in_tri(u, v, (.28, .58), (.28, .82), (.50, .60)):
        for cx in (.34, .5, .66):
            if (u - cx) ** 2 + (v - .43) ** 2 <= .055 ** 2:
                return BLUE
        return WHITE
    return BLUE


def make_icon_rgba(size, alert=False):
    n = 4  # supersampling biar halus
    rgb, alpha = bytearray(), bytearray()
    for py in range(size):
        for px in range(size):
            r = g = bl = cnt = 0
            for sy in range(n):
                for sx in range(n):
                    c = _sample((px + (sx + .5) / n) / size, (py + (sy + .5) / n) / size, alert)
                    if c:
                        r += c[0]; g += c[1]; bl += c[2]; cnt += 1
            if cnt:
                rgb += bytes((r // cnt, g // cnt, bl // cnt))
                alpha.append(cnt * 255 // (n * n))
            else:
                rgb += b"\x00\x00\x00"
                alpha.append(0)
    return bytes(rgb), bytes(alpha)


def make_icon(size, alert=False):
    rgb, alpha = make_icon_rgba(size, alert)
    img = wx.Image(size, size)
    img.SetData(rgb)
    img.SetAlpha(alpha)
    icon = wx.Icon()
    icon.CopyFromBitmap(wx.Bitmap(img))
    return icon


# ====================== ikon lonceng (putih) untuk tombol popup ======================
def _dist_seg(u, v, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    t = ((u - x0) * dx + (v - y0) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(u - (x0 + t * dx), v - (y0 + t * dy))


def _bell_sample(u, v, muted):
    if muted:  # garis coret + celah transparan di sekitarnya
        d = _dist_seg(u, v, .14, .12, .86, .88)
        if d <= .05:
            return True
        if d <= .11:
            return False
    if (u - .5) ** 2 + (v - .13) ** 2 <= .06 ** 2:                       # kenop atas
        return True
    if v <= .40 and (u - .5) ** 2 + (v - .40) ** 2 <= .24 ** 2:          # kubah
        return True
    if .40 < v <= .72 and abs(u - .5) <= .24 + (v - .40) / .32 * .10:    # badan melebar
        return True
    if _in_rrect(u, v, .12, .70, .88, .78, .04):                         # bibir bawah
        return True
    if (u - .5) ** 2 + (v - .87) ** 2 <= .08 ** 2:                       # pemukul
        return True
    return False


def make_bell_bitmap(size=18, muted=False):
    n = 4
    rgb, alpha = bytearray(), bytearray()
    for py in range(size):
        for px in range(size):
            cnt = 0
            for sy in range(n):
                for sx in range(n):
                    if _bell_sample((px + (sx + .5) / n) / size, (py + (sy + .5) / n) / size, muted):
                        cnt += 1
            rgb += b"\xff\xff\xff"
            alpha.append(cnt * 255 // (n * n))
    img = wx.Image(size, size)
    img.SetData(bytes(rgb))
    img.SetAlpha(bytes(alpha))
    return wx.Bitmap(img)


# ====================== suara "tring" ======================
def make_tring_wav(path):
    rate = 44100
    total = int(rate * 0.8)
    buf = [0.0] * total

    def add(freq, start, dur, vol):
        s0 = int(start * rate)
        for i in range(int(dur * rate)):
            if s0 + i >= total:
                break
            t = i / rate
            env = math.exp(-t * 6.5) * min(1.0, t * 300)
            v = (math.sin(2 * math.pi * freq * t)
                 + .35 * math.sin(2 * math.pi * freq * 2.01 * t)
                 + .15 * math.sin(2 * math.pi * freq * 3.02 * t))
            buf[s0 + i] += env * v * vol

    add(1318.5, 0.00, .50, 1.0)
    add(1760.0, 0.13, .65, 1.0)
    peak = max(abs(x) for x in buf) or 1
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"".join(struct.pack("<h", int(x / peak * 0.7 * 32767)) for x in buf))


def ensure_sound():
    p = Path(tempfile.gettempdir()) / "obrolan_tring.wav"
    if not p.exists():
        try:
            make_tring_wav(p)
        except Exception:
            pass
    return p


_snd_ref = None


def play_sound(path):
    global _snd_ref
    try:
        if sys.platform.startswith("win"):
            import winsound
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            _snd_ref = wx.adv.Sound(str(path))
            _snd_ref.Play(wx.adv.SOUND_ASYNC)
    except Exception:
        pass


# ====================== satu instance saja ======================
def acquire_lock(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        s.listen(5)
        return s
    except OSError:
        s.close()
        return None


def signal_existing(port):
    """Minta instance yang sudah jalan untuk tampil. True kalau berhasil."""
    try:
        if sys.platform.startswith("win"):
            import ctypes
            ctypes.windll.user32.AllowSetForegroundWindow(-1)  # izinkan jendela lama maju ke depan
    except Exception:
        pass
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as c:
            c.sendall(b"SHOW")
        return True
    except OSError:
        return False


def listen_for_show(srv, callback):
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            break
        try:
            conn.settimeout(1)
            if conn.recv(16).startswith(b"SHOW"):
                wx.CallAfter(callback)
        except Exception:
            pass
        finally:
            conn.close()


# ====================== dropdown chatroom (2 baris: nama + URL) ======================
class RoomCombo(wx.adv.OwnerDrawnComboBox):
    C_NAME = wx.Colour(24, 119, 242)
    C_URL = wx.Colour(130, 136, 145)
    CREATE_LABEL = "[Buat Chatroom]"

    def __init__(self, parent):
        super().__init__(parent, style=wx.CB_READONLY, size=(-1, 32))
        self.rooms = []
        self.f_name = wx.Font(wx.FontInfo(10))
        self.f_url = wx.Font(wx.FontInfo(8).Italic())

    def set_rooms(self, rooms, active):
        self.rooms = rooms
        self.Clear()
        for r in rooms:
            self.Append(r["chatroom"])
        self.Append(self.CREATE_LABEL)  # item terakhir = tombol [Buat Chatroom]
        if rooms:
            self.SetSelection(max(0, min(active, len(rooms) - 1)))

    def OnMeasureItem(self, item):
        return 32 if item == len(self.rooms) else 40

    def OnMeasureItemWidth(self, item):
        return -1

    def OnDrawItem(self, dc, rect, item, flags):
        if item == wx.NOT_FOUND or item > len(self.rooms):
            return
        in_control = bool(flags & wx.adv.ODCB_PAINTING_CONTROL)
        selected = bool(flags & wx.adv.ODCB_PAINTING_SELECTED)

        if not in_control and item > 0:  # garis pemisah antar item
            dc.SetPen(wx.Pen(wx.Colour(215, 219, 225), 1))
            dc.DrawLine(rect.x + 4, rect.y, rect.x + rect.width - 4, rect.y)

        if item == len(self.rooms):  # tombol [Buat Chatroom]
            dc.SetFont(self.f_name)
            dc.SetTextForeground(wx.WHITE if selected else self.C_NAME)
            _tw, th = dc.GetTextExtent(self.CREATE_LABEL)
            dc.DrawText(self.CREATE_LABEL, rect.x + 6, rect.y + (rect.height - th) // 2)
            return

        r = self.rooms[item]
        dc.SetFont(self.f_name)
        dc.SetTextForeground(wx.WHITE if selected else self.C_NAME)
        line = "Chatroom : " + r["chatroom"]
        tw, th = dc.GetTextExtent(line)
        if in_control:  # kondisi tertutup: satu baris, rata tengah vertikal
            dc.DrawText(line, rect.x + 4, rect.y + (rect.height - th) // 2)
            return
        dc.DrawText(line, rect.x + 6, rect.y + 4)
        dc.SetFont(self.f_url)
        dc.SetTextForeground(wx.Colour(225, 232, 245) if selected else self.C_URL)
        dc.DrawText(r["api"], rect.x + 6, rect.y + 4 + th + 2)


# ====================== dialog pengaturan ======================
class SettingsDialog(wx.Dialog):
    def __init__(self, parent, rooms, active, poll_ms, lock_port):
        super().__init__(parent, title="Pengaturan", style=wx.DEFAULT_DIALOG_STYLE)
        self.rooms = [dict(r) for r in rooms]
        self.active = active
        self.result = None

        names = [r["chatroom"] for r in self.rooms]
        cur = self.rooms[active]

        grid = wx.FlexGridSizer(5, 2, 8, 10)
        grid.AddGrowableCol(1)

        def lbl(t):
            return wx.StaticText(self, label=t)

        self.cb_room = wx.ComboBox(self, choices=names, style=wx.CB_DROPDOWN, value=cur["chatroom"])
        self.t_api = wx.TextCtrl(self, value=cur["api"], size=(340, -1))
        self.t_poll = wx.SpinCtrl(self, min=500, max=60000, initial=poll_ms, size=(120, -1))
        self.t_port = wx.SpinCtrl(self, min=1024, max=65535, initial=lock_port, size=(120, -1))

        self._loaded = active
        self.bg_value = cur.get("background", "")
        self.btn_bg = wx.Button(self, label="Pilih Gambar")
        self.lbl_bg = wx.StaticText(self, label="", style=wx.ST_ELLIPSIZE_MIDDLE)
        self.lbl_bg.SetMinSize((120, -1))
        self.btn_bg_clear = wx.Button(self, label="Hapus", size=(60, -1))
        bgrow = wx.BoxSizer(wx.HORIZONTAL)
        bgrow.Add(self.btn_bg, 0, wx.RIGHT, 8)
        bgrow.Add(self.lbl_bg, 1, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 8)
        bgrow.Add(self.btn_bg_clear, 0)
        self._show_bg()

        grid.Add(lbl("CHATROOM   :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.cb_room, 1, wx.EXPAND)
        grid.Add(lbl("API SERVER :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_api, 1, wx.EXPAND)
        grid.Add(lbl("BACKGROUND :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(bgrow, 1, wx.EXPAND)
        grid.Add(lbl("POLL_MS    :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_poll, 0)
        grid.Add(lbl("LOCK_PORT  :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_port, 0)

        hint = wx.StaticText(
            self, label="Ketik nama baru di CHATROOM lalu OK untuk menambah chatroom.\n"
                        "LOCK_PORT berlaku setelah aplikasi dibuka ulang.")
        hint.SetForegroundColour(wx.Colour(130, 136, 145))

        btn_ok = wx.Button(self, wx.ID_OK, "OK")
        btn_cancel = wx.Button(self, wx.ID_CANCEL, "BATAL")
        btn_del = wx.Button(self, wx.ID_ANY, "Hapus Chatroom")
        btn_ok.SetDefault()
        btns = wx.BoxSizer(wx.HORIZONTAL)
        btns.Add(btn_del, 0)
        btns.AddStretchSpacer(1)
        btns.Add(btn_ok, 0, wx.RIGHT, 6)
        btns.Add(btn_cancel, 0)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(grid, 0, wx.EXPAND | wx.ALL, 14)
        box.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        box.Add(btns, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizerAndFit(box)
        self.CentreOnParent()

        self.cb_room.Bind(wx.EVT_COMBOBOX, self.on_pick)
        btn_ok.Bind(wx.EVT_BUTTON, self.on_ok)
        btn_del.Bind(wx.EVT_BUTTON, self.on_delete)
        self.btn_bg.Bind(wx.EVT_BUTTON, self.on_pick_bg)
        self.btn_bg_clear.Bind(wx.EVT_BUTTON, self.on_clear_bg)
        self.cb_room.Bind(wx.EVT_TEXT, self.on_room_text)

    def _find(self, name):
        for i, r in enumerate(self.rooms):
            if r["chatroom"].strip().lower() == name.strip().lower():
                return i
        return -1

    def _show_bg(self):
        self.lbl_bg.SetLabel(Path(self.bg_value).name if self.bg_value else "(tidak ada)")
        self.btn_bg_clear.Enable(bool(self.bg_value))

    def _load_room(self, i):
        """Tampilkan API + background milik chatroom ke-i di form."""
        if i < 0 or i >= len(self.rooms) or i == self._loaded:
            return
        self._loaded = i
        self.t_api.SetValue(self.rooms[i]["api"])
        self.bg_value = self.rooms[i].get("background", "")
        self._show_bg()

    def on_pick(self, evt):
        self._load_room(evt.GetSelection())

    def on_room_text(self, _evt):
        self._load_room(self._find(self.cb_room.GetValue()))

    def on_pick_bg(self, _evt):
        wildcard = "Gambar (*.jpg;*.jpeg;*.png;*.gif)|*.jpg;*.jpeg;*.png;*.gif"
        dlg = wx.FileDialog(self, "Pilih gambar background", wildcard=wildcard,
                            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST)
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        finally:
            dlg.Destroy()
        try:
            self.bg_value = import_background(path)   # otomatis disalin ke folder /background
        except Exception as e:
            wx.MessageBox(f"Gagal menyalin gambar:\n{e}", "Pengaturan", wx.OK | wx.ICON_ERROR, self)
            return
        self._show_bg()

    def on_clear_bg(self, _evt):
        self.bg_value = ""
        self._show_bg()

    def on_delete(self, _evt):
        i = self._find(self.cb_room.GetValue())
        if i < 0:
            wx.MessageBox("Chatroom tidak ditemukan di daftar.", "Pengaturan", wx.OK | wx.ICON_INFORMATION, self)
            return
        if len(self.rooms) <= 1:
            wx.MessageBox("Minimal harus ada satu chatroom.", "Pengaturan", wx.OK | wx.ICON_WARNING, self)
            return
        if wx.MessageBox(f"Hapus chatroom '{self.rooms[i]['chatroom']}'?", "Konfirmasi",
                         wx.YES_NO | wx.ICON_QUESTION, self) != wx.YES:
            return
        del self.rooms[i]
        self.cb_room.Set([r["chatroom"] for r in self.rooms])
        self.cb_room.SetSelection(0)
        self._loaded = -1
        self._load_room(0)

    def on_ok(self, _evt):
        name = self.cb_room.GetValue().strip()
        api = self.t_api.GetValue().strip()
        if not name:
            wx.MessageBox("Nama chatroom tidak boleh kosong.", "Pengaturan", wx.OK | wx.ICON_WARNING, self)
            return
        if not api.lower().startswith(("http://", "https://")):
            wx.MessageBox("API SERVER harus diawali http:// atau https://", "Pengaturan",
                          wx.OK | wx.ICON_WARNING, self)
            return
        i = self._find(name)
        if i >= 0:
            self.rooms[i]["chatroom"] = name
            self.rooms[i]["api"] = api
        else:
            self.rooms.append({"chatroom": name, "api": api})
            i = len(self.rooms) - 1
        if self.bg_value:
            self.rooms[i]["background"] = self.bg_value
        else:
            self.rooms[i].pop("background", None)
        self.result = dict(rooms=self.rooms, active=i,
                           poll_ms=self.t_poll.GetValue(), lock_port=self.t_port.GetValue())
        self.EndModal(wx.ID_OK)


class CreateRoomDialog(wx.Dialog):
    """Form 'Buat Chatroom' - tampilan sama dengan form Pengaturan."""

    def __init__(self, parent, existing_names, poll_ms, lock_port):
        super().__init__(parent, title="Buat Chatroom", style=wx.DEFAULT_DIALOG_STYLE)
        self.existing = [n.strip().lower() for n in existing_names]
        self.result = None

        grid = wx.FlexGridSizer(4, 2, 8, 10)
        grid.AddGrowableCol(1)

        def lbl(t):
            return wx.StaticText(self, label=t)

        self.t_room = wx.TextCtrl(self)
        self.t_api = wx.TextCtrl(self, value="http://", size=(340, -1))
        self.t_poll = wx.SpinCtrl(self, min=500, max=60000, initial=poll_ms, size=(120, -1))
        self.t_port = wx.SpinCtrl(self, min=1024, max=65535, initial=lock_port, size=(120, -1))

        grid.Add(lbl("CHATROOM   :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_room, 1, wx.EXPAND)
        grid.Add(lbl("API SERVER :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_api, 1, wx.EXPAND)
        grid.Add(lbl("POLL_MS    :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_poll, 0)
        grid.Add(lbl("LOCK_PORT  :"), 0, wx.ALIGN_CENTER_VERTICAL)
        grid.Add(self.t_port, 0)

        hint = wx.StaticText(
            self, label="Folder dan api.php untuk chatroom ini harus sudah ada di server.\n"
                        "LOCK_PORT berlaku setelah aplikasi dibuka ulang.")
        hint.SetForegroundColour(wx.Colour(130, 136, 145))

        btn_ok = wx.Button(self, wx.ID_OK, "OK")
        btn_cancel = wx.Button(self, wx.ID_CANCEL, "BATAL")
        btn_ok.SetDefault()
        btns = wx.BoxSizer(wx.HORIZONTAL)
        btns.AddStretchSpacer(1)
        btns.Add(btn_ok, 0, wx.RIGHT, 6)
        btns.Add(btn_cancel, 0)

        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(grid, 0, wx.EXPAND | wx.ALL, 14)
        box.Add(hint, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        box.Add(btns, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 14)
        self.SetSizerAndFit(box)
        self.CentreOnParent()
        self.t_room.SetFocus()

        btn_ok.Bind(wx.EVT_BUTTON, self.on_ok)

    def on_ok(self, _evt):
        name = self.t_room.GetValue().strip()
        api = self.t_api.GetValue().strip()
        if not name:
            wx.MessageBox("Nama chatroom tidak boleh kosong.", "Buat Chatroom", wx.OK | wx.ICON_WARNING, self)
            return
        if name.lower() in self.existing:
            wx.MessageBox("Nama chatroom sudah ada. Pakai nama lain.", "Buat Chatroom",
                          wx.OK | wx.ICON_WARNING, self)
            return
        if not api.lower().startswith(("http://", "https://")) or len(api) < 9:
            wx.MessageBox("API SERVER harus diawali http:// atau https:// dan diisi lengkap.",
                          "Buat Chatroom", wx.OK | wx.ICON_WARNING, self)
            return
        self.result = dict(name=name, api=api,
                           poll_ms=self.t_poll.GetValue(), lock_port=self.t_port.GetValue())
        self.EndModal(wx.ID_OK)


class ChatView(wx.ScrolledWindow):
    PAD = 12          # jarak ke tepi jendela
    BPX = 12          # padding horizontal dalam bubble
    BPY = 7           # padding vertikal dalam bubble
    GAP = 3           # jarak antar pesan dari orang yang sama
    GROUP_GAP = 10    # jarak antar pesan beda pengirim
    RADIUS = 14
    QBAR = 3          # lebar garis kiri kutipan reply
    QPAD = 6          # padding dalam kotak kutipan
    QPY = 4
    ARROW = 28        # diameter tombol panah reply
    ARROW_GAP = 6     # jarak panah ke bubble
    TICKW = 15        # lebar area centang di pojok kanan-bawah bubble sendiri
    C_TICK = wx.Colour(205, 226, 255)     # 1 centang: sudah sampai
    C_TICK_READ = wx.Colour(120, 240, 165)  # 2 centang: sudah dibaca

    C_BG = wx.Colour(236, 239, 243)
    C_ME = wx.Colour(24, 119, 242)
    C_OTHER = wx.Colour(255, 255, 255)
    C_BORDER = wx.Colour(221, 225, 230)
    NAME_COLORS = [
        wx.Colour(200, 60, 60), wx.Colour(40, 150, 90), wx.Colour(180, 110, 20),
        wx.Colour(130, 70, 180), wx.Colour(20, 140, 160), wx.Colour(190, 70, 130),
    ]

    def __init__(self, parent, on_reply=None):
        super().__init__(parent, style=wx.VSCROLL)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self.SetBackgroundColour(self.C_BG)
        self.SetScrollRate(0, 8)
        if wx.Platform == "__WXMSW__":
            self.SetDoubleBuffered(True)

        self.f_msg = wx.Font(wx.FontInfo(10))
        self.f_name = wx.Font(wx.FontInfo(9).Bold())
        self.f_small = wx.Font(wx.FontInfo(8))
        self.f_quote = wx.Font(wx.FontInfo(9))

        self.messages = []
        self.my_cid = CID
        self.items = []
        self.total_h = 0
        self.on_reply = on_reply
        self.hover = -1       # index item yang sedang di-hover (panah reply tampil)
        self.flash = -1       # index item yang disorot setelah klik kutipan
        self.read_mark = 0    # waktu pesan terakhir yang sudah dibaca orang lain
        self.on_scrolled = None

        # background per chatroom
        self.bg_key = None
        self.bg_mode = "cover"
        self.bg_color = self.C_BG
        self.bg_img = None        # gambar statis
        self.bg_anim = None       # wx.adv.Animation untuk GIF bergerak
        self.bg_idx = 0
        self._bg_cache = None
        self.bg_timer = wx.Timer(self)

        self.Bind(wx.EVT_TIMER, self.on_bg_timer, self.bg_timer)
        self.Bind(wx.EVT_PAINT, self.on_paint)
        self.Bind(wx.EVT_SIZE, self.on_size)
        self.Bind(wx.EVT_MOTION, self.on_motion)
        self.Bind(wx.EVT_LEAVE_WINDOW, self.on_leave)
        self.Bind(wx.EVT_LEFT_UP, self.on_left_up)
        self.Bind(wx.EVT_MOUSEWHEEL, self.on_wheel)
        self.Bind(wx.EVT_SCROLLWIN, self.on_wheel)

    # ---------- API publik ----------
    def set_messages(self, msgs, my_cid, force_bottom=False):
        at_bottom = self._at_bottom()
        self.messages = msgs
        self.my_cid = my_cid
        self._layout()
        if self.flash >= len(self.items):
            self.flash = -1
        if at_bottom or force_bottom:
            self._scroll_bottom()
        self.Refresh()
        wx.CallAfter(self._update_hover)

    def set_background(self, path, mode="cover", color=""):
        """Atur background chatroom. path boleh relatif terhadap folder aplikasi. GIF boleh bergerak."""
        mode = (mode or "cover").strip().lower()
        if mode not in ("cover", "stretch", "tile", "center"):
            mode = "cover"
        p = None
        mt = 0
        if path:
            p = Path(path)
            if not p.is_absolute():
                p = APP_DIR / p
            try:
                mt = p.stat().st_mtime
            except OSError:
                mt = 0
        key = (str(p or ""), mt, mode, color or "")
        if key == self.bg_key:
            return
        self.bg_key = key
        self.bg_mode = mode
        if self.bg_timer.IsRunning():
            self.bg_timer.Stop()
        self.bg_img = self.bg_anim = self._bg_cache = None
        self.bg_idx = 0

        col = self.C_BG
        if color:
            try:
                c = wx.Colour(color)
                if c.IsOk():
                    col = c
            except Exception:
                pass
        self.bg_color = col

        if p is not None and mt:
            nolog = wx.LogNull()  # jangan munculkan dialog error kalau gambar rusak
            try:
                if p.suffix.lower() == ".gif":
                    anim = wx.adv.Animation()
                    if anim.LoadFile(str(p), wx.adv.ANIMATION_TYPE_GIF) and anim.IsOk() \
                            and anim.GetFrameCount() > 1:
                        self.bg_anim = anim
                if self.bg_anim is None:
                    img = wx.Image(str(p))
                    if img.IsOk():
                        m = max(img.GetWidth(), img.GetHeight())
                        if m > 1920:  # batasi ukuran supaya resize tetap ringan
                            k = 1920.0 / m
                            img = img.Scale(max(1, int(img.GetWidth() * k)),
                                            max(1, int(img.GetHeight() * k)),
                                            wx.IMAGE_QUALITY_HIGH)
                        self.bg_img = img
            except Exception:
                self.bg_img = self.bg_anim = None
            del nolog
        if self.bg_anim is not None:
            self._bg_arm()
        self.Refresh()

    def _bg_arm(self):
        try:
            d = self.bg_anim.GetDelay(self.bg_idx)
        except Exception:
            d = 100
        self.bg_timer.StartOnce(max(40, d or 100))

    def on_bg_timer(self, _evt):
        if self.bg_anim is None:
            return
        top = self.GetTopLevelParent()
        if not self.IsShownOnScreen() or top.IsIconized():  # disembunyikan/minimize -> jangan boros CPU
            self.bg_timer.StartOnce(500)
            return
        self.bg_idx = (self.bg_idx + 1) % self.bg_anim.GetFrameCount()
        self.Refresh()
        self._bg_arm()

    def set_read_mark(self, t):
        if t != self.read_mark:
            self.read_mark = t
            self.Refresh()

    def at_bottom(self):
        return self._at_bottom()

    # ---------- util ----------
    def _at_bottom(self):
        ch = self.GetClientSize().height
        oy = self.GetViewStart()[1] * self.GetScrollPixelsPerUnit()[1]
        return oy + ch >= self.total_h - 20

    def _scroll_bottom(self):
        ch = self.GetClientSize().height
        uy = self.GetScrollPixelsPerUnit()[1] or 1
        self.Scroll(0, max(0, (self.total_h - ch + uy - 1) // uy))

    @staticmethod
    def _day_label(d):
        today = date.today()
        if d == today:
            return "Hari ini"
        if d == today - timedelta(days=1):
            return "Kemarin"
        return d.strftime("%d/%m/%Y")

    def _name_color(self, name):
        return self.NAME_COLORS[sum(map(ord, name)) % len(self.NAME_COLORS)]

    @staticmethod
    def _ext(gc, s):
        return gc.GetTextExtent(s)[:2]

    def _ellipsize(self, gc, s, maxw):
        s = " ".join(str(s).split())
        if self._ext(gc, s)[0] <= maxw:
            return s
        lo, hi = 0, len(s)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._ext(gc, s[:mid] + "…")[0] <= maxw:
                lo = mid
            else:
                hi = mid - 1
        return s[:lo] + "…"

    def _wrap(self, gc, text, maxw):
        lines = []
        for para in text.split("\n"):
            cur = ""
            for word in para.split(" "):
                cand = word if not cur else cur + " " + word
                if self._ext(gc, cand)[0] <= maxw:
                    cur = cand
                    continue
                if cur:
                    lines.append(cur)
                    cur = ""
                while self._ext(gc, word)[0] > maxw and len(word) > 1:
                    n = len(word)
                    while n > 1 and self._ext(gc, word[:n])[0] > maxw:
                        n -= 1
                    lines.append(word[:n])
                    word = word[n:]
                cur = word
            lines.append(cur)
        return lines

    # ---------- layout ----------
    def _layout(self):
        cw = max(self.GetClientSize().width, 120)
        gc = wx.GraphicsContext.Create(self)
        self.items = []
        y = self.PAD
        max_bw = int(cw * 0.72)
        prev_sender = None
        prev_day = None

        for m in self.messages:
            try:
                mtime = int(m.get("time", 0))
                dt = datetime.fromtimestamp(mtime)
            except Exception:
                mtime = 0
                dt = datetime.fromtimestamp(0)
            name = str(m.get("name", "?"))
            cid = str(m.get("cid", ""))
            rep, body = parse_message(str(m.get("message", "")))
            mine = bool(cid) and cid == self.my_cid
            sender = cid or name

            # pemisah tanggal
            if dt.date() != prev_day:
                gc.SetFont(self.f_small, wx.BLACK)
                label = self._day_label(dt.date())
                tw, th = self._ext(gc, label)
                pw, ph = int(tw) + 18, int(th) + 8
                self.items.append(dict(kind="date", label=label, x=(cw - pw) // 2,
                                       y=y, w=pw, h=ph, th=th, tw=tw))
                y += ph + self.GROUP_GAP
                prev_day = dt.date()
                prev_sender = None
            elif prev_sender is not None:
                y += self.GAP if sender == prev_sender else self.GROUP_GAP

            show_name = (not mine) and sender != prev_sender

            gc.SetFont(self.f_msg, wx.BLACK)
            lines = self._wrap(gc, body, max_bw - 2 * self.BPX)
            lh = self._ext(gc, "Ag")[1]
            tw = max(self._ext(gc, ln)[0] for ln in lines)

            nh = nw = 0
            if show_name:
                gc.SetFont(self.f_name, wx.BLACK)
                nw, nh = self._ext(gc, name)

            tstr = dt.strftime("%H:%M")
            gc.SetFont(self.f_small, wx.BLACK)
            timew, timeh = self._ext(gc, tstr)
            if mine:
                timew += self.TICKW + 3   # sisakan tempat untuk centang

            # kutipan reply (kalau pesan ini balasan)
            q = None
            qw = 0
            qh = 0
            if rep:
                qmax = max_bw - 2 * self.BPX - self.QBAR - 2 * self.QPAD
                gc.SetFont(self.f_name, wx.BLACK)
                qname = self._ellipsize(gc, rep["n"], qmax)
                qnw, qnh = self._ext(gc, qname)
                gc.SetFont(self.f_quote, wx.BLACK)
                qsnip = self._ellipsize(gc, rep["s"], qmax)
                qsw, qlh = self._ext(gc, qsnip)
                qw = int(max(qnw, qsw)) + self.QBAR + 2 * self.QPAD
                qh = int(2 * self.QPY + qnh + qlh)
                q = dict(name=qname, snip=qsnip, c=rep["c"], t=rep["t"],
                         rawname=rep["n"], nh=qnh, h=qh)

            bw = int(max(tw, nw, timew, qw)) + 2 * self.BPX
            bh = int(2 * self.BPY + (nh + 2 if show_name else 0)
                     + (qh + 4 if q else 0)
                     + len(lines) * lh + 2 + timeh)
            if q:
                q["y"] = self.BPY + (nh + 2 if show_name else 0)
            x = cw - self.PAD - bw if mine else self.PAD

            self.items.append(dict(
                kind="msg", mine=mine, name=name, show_name=show_name,
                cid=cid, time=mtime, body=body, q=q,
                lines=lines, lh=lh, nh=nh, tstr=tstr, timew=timew, timeh=timeh,
                x=x, y=y, w=bw, h=bh))
            y += bh
            prev_sender = sender

        self.total_h = y + self.PAD
        self.SetVirtualSize((cw, self.total_h))

    # ---------- hit test / mouse ----------
    def _arrow_rect(self, it):
        return (it["x"] + it["w"] + self.ARROW_GAP,
                it["y"] + (it["h"] - self.ARROW) // 2,
                self.ARROW, self.ARROW)

    def _hit(self, px, py):
        """-> (index, zona); zona: 'arrow' | 'quote' | 'bubble' | None"""
        for i, it in enumerate(self.items):
            if it["kind"] != "msg":
                continue
            top, bot = it["y"], it["y"] + it["h"]
            x = it["x"]
            if not it["mine"]:
                ax, ay, aw, ah = self._arrow_rect(it)
                if x + it["w"] < px <= ax + aw and ay <= py <= ay + ah:
                    return i, "arrow"
            if py < top or py > bot:
                continue
            q = it["q"]
            if q:
                qx = x + self.BPX
                qy = top + q["y"]
                if qx <= px <= x + it["w"] - self.BPX and qy <= py <= qy + q["h"]:
                    return i, "quote"
            if x <= px <= x + it["w"]:
                return i, "bubble"
        return -1, None

    def _client_to_virtual(self, pt):
        oy = self.GetViewStart()[1] * self.GetScrollPixelsPerUnit()[1]
        return pt.x, pt.y + oy

    def _update_hover(self, pt=None):
        try:
            if pt is None:
                pt = self.ScreenToClient(wx.GetMousePosition())
                if not self.GetClientRect().Contains(pt):
                    self._set_hover(-1, None)
                    return
            px, py = self._client_to_virtual(pt)
            idx, zone = self._hit(px, py)
            self._set_hover(idx, zone)
        except RuntimeError:  # window sudah dihancurkan
            pass

    def _set_hover(self, idx, zone):
        hover = idx if (idx >= 0 and not self.items[idx]["mine"]) else -1
        if hover != self.hover:
            self.hover = hover
            self.Refresh()
        cur = wx.CURSOR_HAND if zone in ("arrow", "quote") else wx.CURSOR_ARROW
        self.SetCursor(wx.Cursor(cur))

    def on_motion(self, evt):
        self._update_hover(evt.GetPosition())
        evt.Skip()

    def on_leave(self, evt):
        self._set_hover(-1, None)
        evt.Skip()

    def on_wheel(self, evt):
        wx.CallAfter(self._update_hover)
        if self.on_scrolled:
            wx.CallAfter(self.on_scrolled)
        evt.Skip()

    def on_left_up(self, evt):
        px, py = self._client_to_virtual(evt.GetPosition())
        idx, zone = self._hit(px, py)
        if idx >= 0:
            it = self.items[idx]
            if zone == "arrow" and self.on_reply:
                self.on_reply(it)
            elif zone == "quote" and it["q"]:
                self.jump_to(it["q"]["c"], it["q"]["t"])
        evt.Skip()

    def jump_to(self, cid, t):
        """Scroll ke pesan asli yang dikutip, lalu sorot sebentar."""
        for i, it in enumerate(self.items):
            if it["kind"] == "msg" and it["cid"] == cid and it["time"] == t:
                ch = self.GetClientSize().height
                uy = self.GetScrollPixelsPerUnit()[1] or 1
                self.Scroll(0, max(0, (it["y"] - ch // 3) // uy))
                self.flash = i
                self.Refresh()
                wx.CallLater(1400, self._clear_flash)
                return

    def _clear_flash(self):
        try:
            self.flash = -1
            self.Refresh()
        except RuntimeError:
            pass

    # ---------- event ----------
    def on_size(self, evt):
        at_bottom = self._at_bottom()
        self._layout()
        if at_bottom:
            self._scroll_bottom()
        self.Refresh()
        evt.Skip()

    def _bg_source(self):
        if self.bg_anim is not None:
            try:
                return self.bg_anim.GetFrame(self.bg_idx)
            except Exception:
                return None
        return self.bg_img

    def _draw_bg(self, dc, cw, ch):
        dc.SetPen(wx.TRANSPARENT_PEN)
        dc.SetBrush(wx.Brush(self.bg_color))
        dc.DrawRectangle(0, 0, cw, ch)
        if cw < 2 or ch < 2 or (self.bg_img is None and self.bg_anim is None):
            return
        mode = self.bg_mode
        key = (cw, ch, mode)
        if self._bg_cache is None or self._bg_cache[0] != key:
            self._bg_cache = (key, {})
        cache = self._bg_cache[1]
        bmp = cache.get(self.bg_idx)
        if bmp is None:
            img = self._bg_source()
            if img is None or not img.IsOk():
                return
            q = wx.IMAGE_QUALITY_HIGH if self.bg_anim is None else wx.IMAGE_QUALITY_NORMAL
            if mode in ("tile", "center"):
                bmp = wx.Bitmap(img)
            elif mode == "stretch":
                bmp = wx.Bitmap(img.Scale(cw, ch, q))
            else:  # cover: penuhi area, proporsi tetap, potong tengah
                iw, ih = img.GetSize()
                sc = max(cw / iw, ch / ih)
                nw = max(cw, int(math.ceil(iw * sc)))
                nh = max(ch, int(math.ceil(ih * sc)))
                big = img.Scale(nw, nh, q)
                bmp = wx.Bitmap(big.GetSubImage(wx.Rect((nw - cw) // 2, (nh - ch) // 2, cw, ch)))
            if len(cache) < max(1, int(120e6 // (cw * ch * 4))):  # batasi memori cache frame GIF
                cache[self.bg_idx] = bmp
        if mode == "tile":
            bw, bh = bmp.GetSize()
            for ty in range(0, ch, bh):
                for tx in range(0, cw, bw):
                    dc.DrawBitmap(bmp, tx, ty, True)
        elif mode == "center":
            bw, bh = bmp.GetSize()
            dc.DrawBitmap(bmp, (cw - bw) // 2, (ch - bh) // 2, True)
        else:
            dc.DrawBitmap(bmp, 0, 0, True)

    def _draw_ticks(self, gc, x, y, read):
        """Centang kecil ala WhatsApp: 1 = sudah sampai, 2 = sudah dibaca."""
        try:  # centang tidak boleh sampai membuat seluruh chat gagal tergambar
            pen = wx.Pen(self.C_TICK_READ if read else self.C_TICK, 2)  # lebar pen harus integer
            pen.SetCap(wx.CAP_ROUND)
            pen.SetJoin(wx.JOIN_ROUND)
            gc.SetPen(pen)
            gc.SetBrush(wx.TRANSPARENT_BRUSH)

            def check(ox):
                p = gc.CreatePath()
                p.MoveToPoint(ox, y + 5)
                p.AddLineToPoint(ox + 3.2, y + 8.2)
                p.AddLineToPoint(ox + 9, y + 1.2)
                gc.StrokePath(p)
            if read:
                check(x)
                check(x + 4.5)
            else:
                check(x + 4.5)
        except Exception:
            pass

    def _draw_reply_arrow(self, gc, ax, ay):
        r = self.ARROW
        gc.SetPen(wx.Pen(self.C_BORDER, 1))
        gc.SetBrush(wx.Brush(wx.Colour(255, 255, 255, 235)))
        gc.DrawEllipse(ax, ay, r, r)
        cx, cy = ax + r / 2, ay + r / 2
        pen = wx.Pen(wx.Colour(90, 100, 115), 2)
        pen.SetCap(wx.CAP_ROUND)
        pen.SetJoin(wx.JOIN_ROUND)
        gc.SetPen(pen)
        gc.SetBrush(wx.TRANSPARENT_BRUSH)
        body = gc.CreatePath()  # badan panah melengkung (ala Telegram)
        body.MoveToPoint(cx + 7, cy + 6)
        body.AddCurveToPoint(cx + 7, cy + 1, cx + 4, cy - 2, cx - 3, cy - 2)
        gc.StrokePath(body)
        head = gc.CreatePath()  # kepala panah ke kiri
        head.MoveToPoint(cx, cy - 7)
        head.AddLineToPoint(cx - 6, cy - 2)
        head.AddLineToPoint(cx, cy + 3)
        gc.StrokePath(head)

    def on_paint(self, _evt):
        dc = wx.PaintDC(self)
        cw, ch = self.GetClientSize()
        oy = self.GetViewStart()[1] * self.GetScrollPixelsPerUnit()[1]

        self._draw_bg(dc, cw, ch)
        gc = wx.GraphicsContext.Create(dc)

        for idx, it in enumerate(self.items):
            if it["y"] + it["h"] + self.ARROW < oy or it["y"] - self.ARROW > oy + ch:
                continue
            y = it["y"] - oy
            x = it["x"]

            if it["kind"] == "date":
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(wx.Colour(214, 219, 226)))
                gc.DrawRoundedRectangle(x, y, it["w"], it["h"], it["h"] / 2)
                gc.SetFont(self.f_small, wx.Colour(80, 86, 95))
                gc.DrawText(it["label"], x + (it["w"] - it["tw"]) / 2, y + 4)
                continue

            # bubble
            if it["mine"]:
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(self.C_ME))
                fg = wx.WHITE
                tcol = wx.Colour(205, 226, 255)
            else:
                gc.SetPen(wx.Pen(self.C_BORDER, 1))
                gc.SetBrush(wx.Brush(self.C_OTHER))
                fg = wx.Colour(30, 33, 38)
                tcol = wx.Colour(140, 146, 155)
            if idx == self.flash:  # sorot pesan asli setelah klik kutipan
                gc.SetPen(wx.Pen(wx.Colour(255, 170, 0), 3))
            gc.DrawRoundedRectangle(x, y, it["w"], it["h"], self.RADIUS)

            ty = y + self.BPY
            if it["show_name"]:
                gc.SetFont(self.f_name, self._name_color(it["name"]))
                gc.DrawText(it["name"], x + self.BPX, ty)
                ty += it["nh"] + 2

            q = it["q"]
            if q:  # kotak kutipan balasan
                qx, qy, qw = x + self.BPX, ty, it["w"] - 2 * self.BPX
                ncol = self._name_color(q["rawname"])
                if it["mine"]:
                    bgc, barc = wx.Colour(255, 255, 255, 55), wx.WHITE
                    qn_c, qs_c = wx.WHITE, wx.Colour(225, 236, 255)
                else:
                    bgc = wx.Colour(ncol.Red(), ncol.Green(), ncol.Blue(), 30)
                    barc, qn_c, qs_c = ncol, ncol, wx.Colour(90, 96, 105)
                gc.SetPen(wx.TRANSPARENT_PEN)
                gc.SetBrush(wx.Brush(bgc))
                gc.DrawRoundedRectangle(qx, qy, qw, q["h"], 6)
                gc.SetBrush(wx.Brush(barc))
                gc.DrawRoundedRectangle(qx, qy, self.QBAR + 1, q["h"], 2)
                tx = qx + self.QBAR + self.QPAD
                gc.SetFont(self.f_name, qn_c)
                gc.DrawText(q["name"], tx, qy + self.QPY)
                gc.SetFont(self.f_quote, qs_c)
                gc.DrawText(q["snip"], tx, qy + self.QPY + q["nh"])
                ty += q["h"] + 4

            gc.SetFont(self.f_msg, fg)
            for ln in it["lines"]:
                gc.DrawText(ln, x + self.BPX, ty)
                ty += it["lh"]

            gc.SetFont(self.f_small, tcol)
            gc.DrawText(it["tstr"], x + it["w"] - self.BPX - it["timew"], ty + 2)
            if it["mine"]:
                read = self.read_mark > 0 and it["time"] <= self.read_mark
                self._draw_ticks(gc, x + it["w"] - self.BPX - self.TICKW,
                                 ty + 2 + (it["timeh"] - 9) / 2.0, read)

            # panah reply (hanya muncul saat mouse di atas pesan orang lain)
            if idx == self.hover and not it["mine"]:
                ax, ay, _aw, _ah = self._arrow_rect(it)
                self._draw_reply_arrow(gc, ax, ay - oy)


# ====================== teks yang bisa diklik ======================
class LinkLabel(wx.Window):
    """Teks biru bergaris bawah yang bisa diklik. Lebih andal daripada wx.StaticText
    (StaticText di Windows sering tidak menerima klik, dan klik cepat dibaca sebagai klik-dobel)."""
    def __init__(self, parent, text, on_click):
        super().__init__(parent, style=wx.BORDER_NONE | wx.WANTS_CHARS)
        self.SetBackgroundStyle(wx.BG_STYLE_PAINT)
        self._text = text
        self._on_click = on_click
        self._hot = False
        f = wx.Font(wx.FontInfo(9))
        f.SetUnderlined(True)
        self._font = f
        self.SetCursor(wx.Cursor(wx.CURSOR_HAND))
        self.Bind(wx.EVT_PAINT, self._on_paint)
        self.Bind(wx.EVT_LEFT_DOWN, self._on_down)
        self.Bind(wx.EVT_LEFT_DCLICK, self._on_down)   # klik cepat = DCLICK di Windows
        self.Bind(wx.EVT_ENTER_WINDOW, lambda e: self._set_hot(True))
        self.Bind(wx.EVT_LEAVE_WINDOW, lambda e: self._set_hot(False))
        self._fit()

    def _fit(self):
        dc = wx.ClientDC(self)
        dc.SetFont(self._font)
        w, h = dc.GetTextExtent(self._text)
        size = wx.Size(int(w) + 8, int(h) + 8)
        changed = size != self.GetMinSize()
        self.SetMinSize(size)
        self.SetInitialSize(size)
        if changed and self.GetParent():
            self.GetParent().Layout()

    def set_text(self, text):
        if text != self._text:
            self._text = text
            self._fit()
            self.Refresh()

    def _set_hot(self, v):
        self._hot = v
        self.Refresh()

    def _on_down(self, _evt):
        if self._on_click:
            self._on_click()

    def _on_paint(self, _evt):
        dc = wx.AutoBufferedPaintDC(self)
        dc.SetBackground(wx.Brush(self.GetParent().GetBackgroundColour()))
        dc.Clear()
        dc.SetFont(self._font)
        dc.SetTextForeground(wx.Colour(10, 80, 190) if self._hot else wx.Colour(24, 119, 242))
        dc.DrawText(self._text, 4, 4)


# ====================== popup daftar user online ======================
class OnlinePopup(wx.PopupTransientWindow):
    def __init__(self, parent, on_pick, on_closed):
        super().__init__(parent, wx.BORDER_SIMPLE)
        self.on_pick = on_pick
        self.on_closed = on_closed
        self.users = []
        self.SetBackgroundColour(wx.WHITE)
        self.title = wx.StaticText(self, label="Chatroom Online")
        self.title.SetFont(wx.Font(wx.FontInfo(9).Bold()))
        self.title.SetForegroundColour(wx.Colour(24, 119, 242))
        self.lst = wx.ListCtrl(self, style=wx.LC_REPORT | wx.LC_SINGLE_SEL | wx.BORDER_NONE)
        self.lst.InsertColumn(0, "NO", width=44)
        self.lst.InsertColumn(1, "Nama", width=210)
        box = wx.BoxSizer(wx.VERTICAL)
        box.Add(self.title, 0, wx.ALL, 6)
        box.Add(self.lst, 1, wx.EXPAND)
        self.SetSizer(box)
        self.lst.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_sel)

    def set_message(self, text):
        """Dipakai saat daftar belum ada (memuat / presence.php bermasalah)."""
        self.users = []
        self.lst.DeleteAllItems()
        idx = self.lst.InsertItem(0, "")
        self.lst.SetItem(idx, 1, text)
        self.SetSize((290, 34 + 26 + 24))
        self.Layout()

    def set_users(self, users, my_cid):
        self.users = list(users)
        self.lst.DeleteAllItems()
        for n, u in enumerate(self.users):
            idx = self.lst.InsertItem(n, str(n + 1))
            self.lst.SetItem(idx, 1, u["name"] + ("  (kamu)" if u["cid"] == my_cid else ""))
        rows = min(max(len(self.users), 1), 8)
        self.SetSize((290, 34 + 26 + rows * 24))
        self.Layout()

    def on_sel(self, evt):
        i = evt.GetIndex()
        if 0 <= i < len(self.users):
            u = self.users[i]
            wx.CallAfter(self._dismiss_safe)
            wx.CallAfter(self.on_pick, u)

    def _dismiss_safe(self):
        try:
            self.Dismiss()
        except Exception:
            pass

    def OnDismiss(self):
        self.on_closed(self)


# ====================== tray icon ======================
class TrayIcon(wx.adv.TaskBarIcon):
    def __init__(self, frame):
        super().__init__()
        self.frame = frame
        size = wx.SystemSettings.GetMetric(wx.SYS_SMALLICON_X) or 16
        self.ic_normal = make_icon(size)
        self.ic_alert = make_icon(size, alert=True)
        self.blink_on = False
        self.tip = "Obrolan"
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_blink, self.timer)
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_UP, lambda e: self.frame.restore_from_tray())
        self.SetIcon(self.ic_normal, "Obrolan")

    def set_alert(self, on, tip="Obrolan"):
        self.tip = tip
        if on:
            if not self.timer.IsRunning():
                self.blink_on = True
                self.timer.Start(500)
            self.SetIcon(self.ic_alert, tip)
        else:
            self.timer.Stop()
            self.SetIcon(self.ic_normal, "Obrolan")

    def on_blink(self, _evt):
        self.blink_on = not self.blink_on
        self.SetIcon(self.ic_alert if self.blink_on else self.ic_normal, self.tip)

    def CreatePopupMenu(self):
        menu = wx.Menu()
        i_open = menu.Append(wx.ID_ANY, "Buka Obrolan")
        i_pop = menu.AppendCheckItem(wx.ID_ANY, "Popup pesan masuk")
        i_pop.Check(self.frame.cfg["popup"])
        i_snd = menu.AppendCheckItem(wx.ID_ANY, "Suara notifikasi")
        i_snd.Check(self.frame.cfg["sound"])
        menu.AppendSeparator()
        i_exit = menu.Append(wx.ID_ANY, "Keluar")
        menu.Bind(wx.EVT_MENU, lambda e: self.frame.restore_from_tray(), i_open)
        menu.Bind(wx.EVT_MENU, lambda e: self.frame.set_popup(i_pop.IsChecked()), i_pop)
        menu.Bind(wx.EVT_MENU, lambda e: self.frame.set_sound(i_snd.IsChecked()), i_snd)
        menu.Bind(wx.EVT_MENU, lambda e: self.frame.exit_app(), i_exit)
        return menu


# ====================== jendela utama ======================
class ChatFrame(wx.Frame):
    GREEN = wx.Colour(40, 167, 69)
    RED = wx.Colour(215, 50, 50)

    def __init__(self, cfg, app_cfg):
        super().__init__(None, title="Obrolan", size=(580, 660))
        self.cfg = cfg
        self.app_cfg = app_cfg
        self.rooms = app_cfg["DropDown"]
        self.active = app_cfg["Pengaturan"]["aktif"]
        self.server = self.rooms[self.active]["api"]
        self.last_count = -1
        self.force_bottom = True
        self.unread = 0
        self._exiting = False
        self.sound_path = ensure_sound()

        bundle = wx.IconBundle()
        for s in (16, 32, 48):
            bundle.AddIcon(make_icon(s))
        self.SetIcons(bundle)

        self.bmp_bell_on = make_bell_bitmap(18, muted=False)
        self.bmp_bell_off = make_bell_bitmap(18, muted=True)

        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)

        # baris 1: [Pengaturan] [Popup ON/OFF]   baris 2: [Dropdown chatroom] lebar 100%
        bar = wx.BoxSizer(wx.HORIZONTAL)
        self.btn_set = wx.Button(panel, label="Pengaturan", size=(-1, 32))
        self.btn_popup = GenBitmapTextButton(panel, wx.ID_ANY, self.bmp_bell_on, "", size=(200, 32))
        self.btn_popup.SetFont(wx.Font(wx.FontInfo(9).Bold()))
        self.btn_popup.SetForegroundColour(wx.WHITE)
        self.combo = RoomCombo(panel)
        self.combo.set_rooms(self.rooms, self.active)
        bar.Add(self.btn_set, 0, wx.RIGHT, 6)
        bar.Add(self.btn_popup, 1, wx.EXPAND)
        vbox.Add(bar, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        vbox.Add(self.combo, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 6)
        self._update_popup_button()

        top = wx.BoxSizer(wx.HORIZONTAL)
        top.Add(wx.StaticText(panel, label="Nama:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.nama = wx.TextCtrl(panel, value=cfg["name"])
        top.Add(self.nama, 1)
        vbox.Add(top, 0, wx.EXPAND | wx.ALL, 8)

        self.view = ChatView(panel, on_reply=self.begin_reply)
        vbox.Add(self.view, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 8)

        # bar "Membalas ..." di atas kolom ketik
        self.reply_to = None
        self.reply_panel = wx.Panel(panel)
        self.reply_panel.SetBackgroundColour(wx.Colour(232, 240, 254))
        stripe = wx.Panel(self.reply_panel, size=(4, -1))
        stripe.SetBackgroundColour(wx.Colour(24, 119, 242))
        self.rp_name = wx.StaticText(self.reply_panel, style=wx.ST_ELLIPSIZE_END)
        self.rp_name.SetFont(wx.Font(wx.FontInfo(9).Bold()))
        self.rp_name.SetForegroundColour(wx.Colour(24, 119, 242))
        self.rp_text = wx.StaticText(self.reply_panel, style=wx.ST_ELLIPSIZE_END)
        self.rp_text.SetForegroundColour(wx.Colour(90, 96, 105))
        self.rp_name.SetMinSize((10, -1))
        self.rp_text.SetMinSize((10, -1))
        self.rp_close = wx.Button(self.reply_panel, label="X", size=(28, 28))
        rtxt = wx.BoxSizer(wx.VERTICAL)
        rtxt.Add(self.rp_name, 0, wx.EXPAND)
        rtxt.Add(self.rp_text, 0, wx.EXPAND)
        rps = wx.BoxSizer(wx.HORIZONTAL)
        rps.Add(stripe, 0, wx.EXPAND)
        rps.Add(rtxt, 1, wx.EXPAND | wx.ALL, 6)
        rps.Add(self.rp_close, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self.reply_panel.SetSizer(rps)
        vbox.Add(self.reply_panel, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        vbox.Hide(self.reply_panel)
        self.vbox = vbox

        bottom = wx.BoxSizer(wx.HORIZONTAL)
        self.input = wx.TextCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.btn = wx.Button(panel, label="Kirim")
        bottom.Add(self.input, 1, wx.RIGHT, 6)
        bottom.Add(self.btn, 0)
        vbox.Add(bottom, 0, wx.EXPAND | wx.ALL, 8)

        # footer: baris 1 = status koneksi, baris 2 = "Chatroom Online : N orang" (bisa diklik)
        self.foot = wx.Panel(panel)
        fs = wx.BoxSizer(wx.VERTICAL)
        self.lbl_status = wx.StaticText(self.foot, label="", style=wx.ST_ELLIPSIZE_END)
        self.lbl_status.SetMinSize((10, -1))
        self.lbl_status.SetForegroundColour(wx.Colour(90, 96, 105))
        self.lbl_online = LinkLabel(self.foot, "Chatroom Online : ...", self.on_online_click)
        fs.Add(self.lbl_status, 0, wx.EXPAND)
        fs.Add(self.lbl_online, 0, wx.TOP, 2)
        self.foot.SetSizer(fs)
        vbox.Add(self.foot, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.online = None          # None = belum tahu / gagal; list = [{cid, name}]
        self._online_popup = None
        self._popup_closed_at = 0.0
        self.online_err = ""
        self.my_read = 0            # waktu pesan terakhir yang sudah SAYA baca (dikirim ke server)
        self.others_read = 0        # waktu pesan terakhir yang sudah dibaca ORANG LAIN
        self._pres_last = None      # (url, key) presence terakhir -> untuk kirim "leave"
        panel.SetSizer(vbox)
        self._update_title()
        self._apply_background()
        self._cfg_mtime = self._cfg_stat()

        self.tray = TrayIcon(self)

        self.nama.Bind(wx.EVT_TEXT, self.on_name_changed)
        self.btn_set.Bind(wx.EVT_BUTTON, self.on_settings)
        self.btn_popup.Bind(wx.EVT_BUTTON, lambda e: self.set_popup(not self.cfg["popup"]))
        self.combo.Bind(wx.EVT_COMBOBOX, self.on_room_selected)
        self.input.Bind(wx.EVT_TEXT_ENTER, self.on_send)
        self.input.Bind(wx.EVT_KEY_DOWN, self.on_input_key)
        self.rp_close.Bind(wx.EVT_BUTTON, lambda e: self.cancel_reply())
        self.btn.Bind(wx.EVT_BUTTON, self.on_send)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.Bind(wx.EVT_SHOW, self.on_show_evt)
        self.Bind(wx.EVT_ICONIZE, self.on_iconize_evt)
        self.view.on_scrolled = self._update_my_read

        self._place_window()

        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.on_tick(), self.timer)
        self.timer.Start(app_cfg["Pengaturan"]["poll_ms"])
        self.refresh()
        self.ping_presence()
        self.input.SetFocus()

    def set_status(self, text):
        self.lbl_status.SetLabel(text)

    def _place_window(self):
        """Buka jendela setinggi layar (area kerja, di atas taskbar). Lebar tetap.
        obrolan.cfg > Pengaturan: "full_tinggi" (true/false), "dock" (kanan|kiri|tengah|bebas)."""
        p = self.app_cfg["Pengaturan"]
        if not p.get("full_tinggi", True):
            return
        try:
            i = wx.Display.GetFromPoint(wx.GetMousePosition())
            area = wx.Display(i if i != wx.NOT_FOUND else 0).GetClientArea()
            w = min(self.GetSize().width, area.width)
            dock = str(p.get("dock", "kanan")).lower()
            if dock == "kiri":
                x = area.x
            elif dock == "tengah":
                x = area.x + (area.width - w) // 2
            elif dock == "bebas":
                x = self.GetPosition().x
            else:
                x = area.x + area.width - w
            self.SetSize(x, area.y, w, area.height)
        except Exception:
            pass

    # ---------- pengaturan ----------
    def _update_popup_button(self):
        on = bool(self.cfg["popup"])
        self.btn_popup.SetLabel("Popup pesan masuk: " + ("ON" if on else "OFF"))
        self.btn_popup.SetBitmapLabel(self.bmp_bell_on if on else self.bmp_bell_off)
        self.btn_popup.SetBackgroundColour(self.GREEN if on else self.RED)
        self.btn_popup.Refresh()

    def set_popup(self, value):
        self.cfg["popup"] = bool(value)
        save_config(self.cfg)
        self._update_popup_button()

    def set_sound(self, value):
        self.cfg["sound"] = bool(value)
        save_config(self.cfg)

    def on_name_changed(self, _evt):
        self.cfg["name"] = self.nama.GetValue()
        save_config(self.cfg)

    def _update_title(self):
        self.SetTitle("Obrolan - " + self.rooms[self.active]["chatroom"])

    def on_settings(self, _evt):
        p = self.app_cfg["Pengaturan"]
        dlg = SettingsDialog(self, self.rooms, self.active, p["poll_ms"], p["lock_port"])
        if dlg.ShowModal() == wx.ID_OK and dlg.result:
            self.apply_settings(dlg.result)
        dlg.Destroy()

    def apply_settings(self, res):
        p = self.app_cfg["Pengaturan"]
        old_port = p["lock_port"]
        old_server = self.server
        old_active = self.active

        self.rooms = res["rooms"]
        self.active = res["active"]
        self.app_cfg["DropDown"] = self.rooms
        p["aktif"] = self.active
        p["poll_ms"] = res["poll_ms"]
        p["lock_port"] = res["lock_port"]
        save_app_cfg(self.app_cfg)

        self.combo.set_rooms(self.rooms, self.active)
        self.timer.Start(p["poll_ms"])
        self._update_title()
        self._apply_background()

        if self.active != old_active or self.rooms[self.active]["api"] != old_server:
            self.switch_room()
        if p["lock_port"] != old_port:
            wx.MessageBox("LOCK_PORT baru berlaku setelah aplikasi ditutup dan dibuka lagi.",
                          "Pengaturan", wx.OK | wx.ICON_INFORMATION, self)

    def on_create(self, _evt=None):
        p = self.app_cfg["Pengaturan"]
        dlg = CreateRoomDialog(self, [r["chatroom"] for r in self.rooms], p["poll_ms"], p["lock_port"])
        if dlg.ShowModal() == wx.ID_OK and dlg.result:
            r = dlg.result
            rooms = [dict(x) for x in self.rooms] + [{"chatroom": r["name"], "api": r["api"]}]
            self.apply_settings(dict(rooms=rooms, active=len(rooms) - 1,
                                     poll_ms=r["poll_ms"], lock_port=r["lock_port"]))
        dlg.Destroy()

    def on_room_selected(self, _evt):
        i = self.combo.GetSelection()
        if i == len(self.rooms):  # item [Buat Chatroom]
            self.combo.SetSelection(self.active)  # kembalikan pilihan semula
            wx.CallAfter(self.on_create)
            return
        if i == wx.NOT_FOUND or i == self.active:
            return
        self.active = i
        self.app_cfg["Pengaturan"]["aktif"] = i
        save_app_cfg(self.app_cfg)
        self._update_title()
        self.switch_room()

    def switch_room(self):
        room = self.rooms[self.active]
        self.server = room["api"]
        self.last_count = -1
        self.force_bottom = True
        self.unread = 0
        self.tray.set_alert(False)
        self.cancel_reply()
        self._apply_background()
        if self._pres_last and self._pres_last != presence_endpoint(room) :
            self._leave_ep(self._pres_last)
        self.online = None
        self.online_err = ""
        self.my_read = 0
        self.others_read = 0
        self.view.set_read_mark(0)
        self._update_online_label(loading=True)
        self.view.set_messages([], CID)
        self.set_status(f"Menghubungkan ke {room['chatroom']}...")
        self.refresh()
        self.ping_presence()

    # ---------- background per chatroom ----------
    def _apply_background(self):
        r = self.rooms[self.active]
        self.view.set_background(r.get("background", ""), r.get("bg_mode", "cover"),
                                 r.get("bg_warna", ""))

    @staticmethod
    def _cfg_stat():
        try:
            return APP_CFG_PATH.stat().st_mtime
        except OSError:
            return 0

    def _reload_room_backgrounds(self):
        """obrolan.cfg diedit tangan -> background langsung berubah tanpa restart."""
        try:
            raw = json.loads(APP_CFG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        src = {str(r.get("chatroom")): r for r in raw.get("DropDown", []) if isinstance(r, dict)}
        for room in self.rooms:
            r = src.get(room["chatroom"])
            if r is None:
                continue
            for k in ROOM_EXTRA_KEYS:
                if r.get(k):
                    room[k] = str(r[k])
                else:
                    room.pop(k, None)
        self._apply_background()

    def on_tick(self):
        mt = self._cfg_stat()
        if mt != self._cfg_mtime:
            self._cfg_mtime = mt
            self._reload_room_backgrounds()
        self.refresh()
        self._update_my_read()
        self.ping_presence()

    # ---------- online (presence) ----------
    def ping_presence(self):
        """Heartbeat + baca daftar online. Dipanggil tiap POLL_MS (sama dengan baca pesan)."""
        url, key = presence_endpoint(self.rooms[self.active])
        self._pres_last = (url, key)
        poll = self.app_cfg["Pengaturan"]["poll_ms"]
        payload = {"cid": CID, "name": self.nama.GetValue().strip() or "python",
                   "ttl": max(15, int(poll * 2.5 / 1000) + 5), "read": self.my_read}
        threading.Thread(target=self._presence_post, args=(self.server, url, key, payload),
                         daemon=True).start()

    def _presence_post(self, server, url, key, payload):
        try:
            r = requests.post(url, params={"room": key}, json=payload, timeout=4)
            r.raise_for_status()
            users = r.json().get("users")
            if not isinstance(users, list):
                raise ValueError("balasan presence.php tidak valid")
            users = [{"cid": str(u.get("cid", "")), "name": str(u.get("name", "?"))}
                     for u in users if isinstance(u, dict)]
            others = 0   # waktu pesan terbaru yang sudah dibaca orang LAIN (bukan saya)
            reads = r.json().get("reads")
            if isinstance(reads, dict):
                for c, t in reads.items():
                    if str(c) != CID:
                        try:
                            others = max(others, int(t))
                        except (TypeError, ValueError):
                            pass
            wx.CallAfter(self._set_online, server, users, "", others)
        except Exception as e:
            wx.CallAfter(self._set_online, server, None, str(e), None)

    def _leave_ep(self, ep, wait=False):
        url, key = ep

        def go():
            try:
                requests.post(url, params={"room": key}, json={"cid": CID, "leave": True}, timeout=2)
            except Exception:
                pass
        if wait:
            go()
        else:
            threading.Thread(target=go, daemon=True).start()

    def _set_online(self, server, users, err, others_read=None):
        if server != self.server:  # hasil dari chatroom lama -> abaikan
            return
        if users is not None:
            self.online = users
            self.online_err = ""
        else:
            self.online_err = err   # gagal sementara: daftar terakhir tetap dipakai
        if others_read is not None and others_read > self.others_read:
            self.others_read = others_read
            self.view.set_read_mark(others_read)   # centang pesan saya jadi 2
        self._update_online_label(err=self.online_err)
        if self._online_popup is not None:
            try:
                self._fill_popup(self._online_popup)
            except RuntimeError:
                self._online_popup = None

    def _update_online_label(self, loading=False, err=""):
        if loading or (self.online is None and not err):
            text, tip = "Chatroom Online : ...", ""
        elif self.online is None:
            text = "Chatroom Online : -"
            tip = "presence.php belum terpasang / tidak bisa dihubungi: " + err
        else:
            text = f"Chatroom Online : {len(self.online)} orang"
            tip = "Klik untuk melihat daftar" + (f"\n(update terakhir gagal: {err})" if err else "")
        self.lbl_online.set_text(text)
        self.lbl_online.SetToolTip(tip)
        self.foot.Layout()

    def _fill_popup(self, pop):
        if self.online is not None:
            pop.set_users(self.online, CID)
        elif self.online_err:
            pop.set_message("presence.php tidak bisa dihubungi")
        else:
            pop.set_message("Memuat...")

    def _popup_alive(self):
        """True hanya kalau popup benar-benar masih ada DAN tampil di layar."""
        pop = self._online_popup
        if pop is None:
            return False
        try:
            return bool(pop.IsShown())
        except RuntimeError:       # objek sudah dihancurkan
            return False

    def _close_popup(self):
        """Tutup + bersihkan popup apa pun kondisinya (aman dipanggil kapan saja)."""
        pop = self._online_popup
        self._online_popup = None
        if pop is None:
            return
        try:
            pop.Dismiss()
        except Exception:
            pass
        wx.CallAfter(self._destroy_popup, pop)

    @staticmethod
    def _destroy_popup(pop):
        try:
            pop.Destroy()
        except Exception:
            pass

    def on_online_click(self, _evt=None):
        # klik yang tadinya hanya menutup popup (popup menutup sendiri saat mouse ditekan) -> jangan buka lagi
        if time.time() - self._popup_closed_at < 0.35:
            return
        if self._online_popup is not None:
            if self._popup_alive():
                self._close_popup()      # klik saat popup terbuka = tutup
                return
            self._close_popup()          # sisa popup mati -> bersihkan, lalu lanjut buka yang baru
        self.ping_presence()   # segarkan datanya sekarang juga
        try:
            pop = OnlinePopup(self, self.on_pick_online, self._popup_closed)
            self._fill_popup(pop)
            self._online_popup = pop
            pw, ph = pop.GetSize()
            foot_top = self.foot.ClientToScreen(wx.Point(0, 0))
            right = self.ClientToScreen(wx.Point(self.GetClientSize().width, 0))
            # pojok kanan-bawah jendela, tepat di atas footer
            pop.Position(wx.Point(right.x - pw - 10, foot_top.y - ph - 4), wx.Size(0, 0))
            pop.Popup()
            wx.CallLater(250, self._verify_popup, pop)
        except Exception:
            self._close_popup()
            self._online_menu()   # cadangan: menu biasa

    def _verify_popup(self, pop):
        """Kalau popup ternyata tidak tampil, jangan biarkan macet: ganti menu biasa."""
        try:
            if self._online_popup is pop and not pop.IsShown():
                self._close_popup()
                self._online_menu()
        except RuntimeError:
            if self._online_popup is pop:
                self._online_popup = None

    def _online_menu(self):
        menu = wx.Menu()
        if not self.online:
            item = menu.Append(wx.ID_ANY, "(belum ada data)")
            item.Enable(False)
        else:
            for n, u in enumerate(self.online, 1):
                label = f"{n}.  {u['name']}" + ("  (kamu)" if u["cid"] == CID else "")
                item = menu.Append(wx.ID_ANY, label)
                self.Bind(wx.EVT_MENU, lambda e, u=u: self.on_pick_online(u), item)
        self.lbl_online.PopupMenu(menu)
        menu.Destroy()

    def _popup_closed(self, pop):
        self._popup_closed_at = time.time()
        if self._online_popup is pop:
            self._online_popup = None
        wx.CallAfter(self._destroy_popup, pop)

    def on_pick_online(self, user):
        """Klik nama user online -> reply pesan terbawah dari dia."""
        if user["cid"] == CID:
            return
        for it in reversed(self.view.items):
            if it["kind"] == "msg" and it["cid"] == user["cid"]:
                self.begin_reply(it)
                self.view.jump_to(it["cid"], it["time"])
                return
        self.set_status(f"Belum ada pesan dari {user['name']} di chatroom ini.")

    # ---------- status baca ----------
    def _update_my_read(self):
        """Pesan dianggap sudah dibaca bila jendela terlihat (tidak di tray/minimize)
        dan pesan terbawah ada di layar. Langsung dikabarkan ke server."""
        msgs = self.view.messages
        if not msgs or not (self.IsShown() and not self.IsIconized() and self.view.IsShownOnScreen()):
            return
        if not self.view.at_bottom():
            return
        t = 0
        for m in msgs:
            try:
                t = max(t, int(m.get("time", 0)))
            except (TypeError, ValueError):
                pass
        if t > self.my_read:
            self.my_read = t
            self.ping_presence()

    # ---------- reply ----------
    def begin_reply(self, item):
        body = " ".join(item["body"].split())
        self.reply_to = {"c": item["cid"], "n": item["name"], "t": item["time"], "s": body[:50]}
        self.rp_name.SetLabel("Membalas " + item["name"])
        self.rp_text.SetLabel(body)
        self.vbox.Show(self.reply_panel, True)
        self.vbox.Layout()
        self.input.SetFocus()

    def cancel_reply(self):
        if self.reply_to is None:
            return
        self.reply_to = None
        self.vbox.Hide(self.reply_panel)
        self.vbox.Layout()

    def on_input_key(self, evt):
        if evt.GetKeyCode() == wx.WXK_ESCAPE and self.reply_to is not None:
            self.cancel_reply()
            return
        evt.Skip()

    # ---------- tray / jendela ----------
    def restore_from_tray(self):
        self.Iconize(False)
        self.Show()
        self.Raise()
        self.unread = 0
        self.tray.set_alert(False)
        self.input.SetFocus()
        wx.CallAfter(self._update_my_read)

    def on_close(self, evt):
        if self._exiting or not evt.CanVeto():
            evt.Skip()
            return
        self._close_popup()
        self.Hide()  # tombol X = sembunyi ke tray, keluar lewat menu tray

    def on_show_evt(self, evt):
        if not evt.IsShown():
            self._close_popup()
        evt.Skip()
        wx.CallAfter(self._update_my_read)

    def on_iconize_evt(self, evt):
        if evt.IsIconized():
            self._close_popup()
        evt.Skip()
        wx.CallAfter(self._update_my_read)

    def on_activate(self, evt):
        if evt.GetActive():
            self.unread = 0
            self.tray.set_alert(False)
            wx.CallAfter(self._update_my_read)
        evt.Skip()

    def exit_app(self):
        self._exiting = True
        self._close_popup()
        if self._pres_last:
            self._leave_ep(self._pres_last, wait=True)
        save_config(self.cfg)
        save_app_cfg(self.app_cfg)
        self.timer.Stop()
        self.tray.set_alert(False)
        self.tray.RemoveIcon()
        self.tray.Destroy()
        self.Destroy()

    # ---------- notifikasi ----------
    def notify(self, n):
        was_active = self.IsActive() and self.IsShown() and not self.IsIconized()
        if self.cfg["sound"]:
            play_sound(self.sound_path)
        if was_active:
            return

        self.unread += n
        self.tray.set_alert(True, f"Obrolan - {self.unread} pesan baru")

        if self.cfg["popup"]:
            self.Iconize(False)
            self.Show()
            self.Raise()
        elif not self.IsShown():
            # popup OFF & lagi di tray: munculkan minimize di taskbar supaya bisa berkedip
            self.Iconize(True)
            self.ShowWithoutActivating()
        self.RequestUserAttention(wx.USER_ATTENTION_ERROR)  # taskbar berkedip

    # ---------- baca ----------
    def refresh(self):
        threading.Thread(target=self._fetch, args=(self.server,), daemon=True).start()

    def _fetch(self, url):
        try:
            r = requests.get(url, timeout=4)
            r.raise_for_status()
            wx.CallAfter(self.render, r.json(), url)
        except Exception as e:
            wx.CallAfter(self._status_if_current, url, f"Gagal konek: {e}")

    def _status_if_current(self, url, text):
        if url == self.server:
            self.set_status(text)

    def render(self, data, url):
        if url != self.server:  # hasil dari chatroom lama (sudah pindah) -> abaikan
            return
        if not isinstance(data, list) or len(data) == self.last_count:
            return
        prev = self.last_count
        self.last_count = len(data)
        self.view.set_messages(data, CID, force_bottom=self.force_bottom)
        self.force_bottom = False
        self.set_status(f"Terhubung - {self.rooms[self.active]['chatroom']} - {len(data)} pesan")
        self._update_my_read()

        if prev >= 0 and len(data) > prev:
            n = 0
            for m in data[prev:]:
                if str(m.get("cid", "")) == CID:
                    continue  # pesan sendiri
                rep, _body = parse_message(str(m.get("message", "")))
                if rep is not None and rep["c"] != CID:
                    continue  # balasan untuk orang lain -> tampil tapi tanpa notifikasi
                n += 1
            if n:
                self.notify(n)

    # ---------- kirim ----------
    def on_send(self, _evt):
        message = self.input.GetValue().strip()
        name = self.nama.GetValue().strip() or "python"
        if not message:
            return
        if self.reply_to is not None:
            message = encode_reply(self.reply_to, message)
            self.cancel_reply()
        self.input.Clear()
        self.force_bottom = True
        threading.Thread(target=self._post, args=(self.server, name, message), daemon=True).start()

    def _post(self, url, name, message):
        try:
            r = requests.post(url, json={"name": name, "message": message, "cid": CID}, timeout=4)
            r.raise_for_status()
            wx.CallAfter(self.refresh)
        except Exception as e:
            wx.CallAfter(self._status_if_current, url, f"Gagal kirim: {e}")


def main():
    app_cfg = load_app_cfg()
    port = app_cfg["Pengaturan"]["lock_port"]

    srv = acquire_lock(port)
    if srv is None and signal_existing(port):
        return  # sudah ada yang jalan -> dia yang dimunculkan, yang ini keluar

    app = wx.App()
    try:
        wx.InitAllImageHandlers()  # PNG, JPG, BMP, GIF untuk background
    except Exception:
        pass
    frame = ChatFrame(load_config(), app_cfg)
    if srv:
        threading.Thread(target=listen_for_show, args=(srv, frame.restore_from_tray),
                         daemon=True).start()
    frame.Show()
    app.MainLoop()
    if srv:
        srv.close()


if __name__ == "__main__":
    main()
