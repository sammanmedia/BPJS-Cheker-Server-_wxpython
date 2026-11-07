
# -*- coding: utf-8 -*-
"""
BPJS SERVER CHECKER V3
SAMMAN MEDIA
External Configuration: api-link-bpjs.cfg
Windows 10 | wxPython
"""

import wx
import urllib.request
import urllib.error
import configparser
import socket
import ssl
import threading
import time
import os

from concurrent.futures import ThreadPoolExecutor, as_completed


APP_DIR = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(APP_DIR, "api-link-bpjs.cfg")

DEFAULT_CFG = """# BPJS SERVER CHECKER CONFIGURATION
# SAMMAN MEDIA

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
"""


class BPJSChecker(wx.Frame):

    def __init__(self):

        super().__init__(
            None,
            title="BPJS SERVER CHECKER V3 - SAMMAN MEDIA",
            size=(1300, 720)
        )

        self.servers = []
        self.running = False
        self.stop_event = threading.Event()

        self.create_default_cfg()

        panel = wx.Panel(self)
        panel.SetBackgroundColour("#EEEEEE")

        main = wx.BoxSizer(wx.VERTICAL)

        # HEADER
        header = wx.Panel(panel)
        header.SetBackgroundColour("#202020")

        hs = wx.BoxSizer(wx.VERTICAL)

        title = wx.StaticText(
            header,
            label="BPJS SERVER CHECKER V3"
        )

        title.SetForegroundColour("white")
        title.SetFont(wx.Font(
            20,
            wx.FONTFAMILY_DEFAULT,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_BOLD
        ))

        subtitle = wx.StaticText(
            header,
            label="SAMMAN MEDIA | HTTP / HTTPS MONITOR"
        )

        subtitle.SetForegroundColour("#BBBBBB")

        hs.Add(title, 0, wx.ALL, 12)
        hs.Add(subtitle, 0, wx.LEFT | wx.BOTTOM, 12)

        header.SetSizer(hs)

        main.Add(header, 0, wx.EXPAND)

        # BUTTONS
        buttons = wx.BoxSizer(wx.HORIZONTAL)

        self.btn_check = wx.Button(
            panel, label="CEK SEMUA SERVER", size=(180, 42)
        )

        self.btn_stop = wx.Button(
            panel, label="STOP", size=(90, 42)
        )

        self.btn_reload = wx.Button(
            panel, label="MUAT ULANG CFG", size=(150, 42)
        )

        self.btn_clear = wx.Button(
            panel, label="BERSIHKAN", size=(120, 42)
        )

        self.btn_stop.Disable()

        for btn in [
            self.btn_check,
            self.btn_stop,
            self.btn_reload,
            self.btn_clear
        ]:
            buttons.Add(btn, 0, wx.ALL, 5)

        main.Add(buttons, 0, wx.ALL, 5)

        # TABLE
        self.table = wx.ListCtrl(
            panel,
            style=wx.LC_REPORT | wx.BORDER_SUNKEN
        )

        columns = [
            ("NO", 45),
            ("NAMA SERVER", 180),
            ("URL", 340),
            ("HTTP", 65),
            ("RESPON", 85),
            ("STATUS", 130),
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

        # PROGRESS
        self.progress = wx.Gauge(
            panel, range=100, size=(-1, 20)
        )

        main.Add(
            self.progress,
            0,
            wx.EXPAND | wx.LEFT | wx.RIGHT,
            10
        )

        # STATUS
        self.status = wx.StaticText(
            panel,
            label="STATUS: MEMUAT KONFIGURASI..."
        )

        self.status.SetFont(wx.Font(
            10,
            wx.FONTFAMILY_DEFAULT,
            wx.FONTSTYLE_NORMAL,
            wx.FONTWEIGHT_BOLD
        ))

        main.Add(self.status, 0, wx.ALL, 10)

        panel.SetSizer(main)

        # EVENTS
        self.btn_check.Bind(
            wx.EVT_BUTTON, self.start_check
        )

        self.btn_stop.Bind(
            wx.EVT_BUTTON, self.stop_check
        )

        self.btn_reload.Bind(
            wx.EVT_BUTTON, self.reload_config
        )

        self.btn_clear.Bind(
            wx.EVT_BUTTON, self.clear_table
        )

        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.load_config()

        self.Centre()
        self.Maximize(True)

    # =================================
    # CREATE DEFAULT CONFIG
    # =================================

    def create_default_cfg(self):

        if not os.path.exists(CFG_FILE):

            with open(
                CFG_FILE, "w", encoding="utf-8"
            ) as f:

                f.write(DEFAULT_CFG)

    # =================================
    # LOAD CONFIG
    # =================================

    def load_config(self):

        config = configparser.ConfigParser(
            interpolation=None,
            strict=True
        )

        config.optionxform = str.lower

        config.read(CFG_FILE, encoding="utf-8-sig")

        servers = []

        for section in config.sections():

            if not section.upper().startswith("SERVER_"):
                continue

            data = config[section]

            enabled = data.get(
                "enabled", "true"
            ).strip().lower()

            if enabled not in ("true", "false", "1", "0", "yes", "no"):
                raise ValueError(
                    f"{section}: nilai enabled tidak valid"
                )

            if enabled not in ("true", "1", "yes"):
                continue

            name = data.get("name", "").strip()
            url = data.get("url", "").strip()
            description = data.get(
                "description", "Tidak ada deskripsi."
            ).strip()

            if not name:
                raise ValueError(
                    f"{section}: nama server kosong"
                )

            if not url.startswith(("http://", "https://")):
                raise ValueError(
                    f"{section}: URL harus menggunakan HTTP atau HTTPS"
                )

            if not description:
                description = "Tidak ada deskripsi."

            servers.append({
                "name": name,
                "url": url,
                "description": description,
                "section": section
            })

        self.servers = servers

        self.table.DeleteAllItems()

        for i, server in enumerate(self.servers):

            row = self.table.InsertItem(i, str(i + 1))

            self.table.SetItem(row, 1, server["name"])
            self.table.SetItem(row, 2, server["url"])
            self.table.SetItem(row, 3, "-")
            self.table.SetItem(row, 4, "-")
            self.table.SetItem(row, 5, "MENUNGGU")
            self.table.SetItem(
                row, 6, server["description"]
            )

        self.progress.SetRange(
            max(1, len(self.servers))
        )

        self.progress.SetValue(0)

        self.status.SetLabel(
            f"STATUS: {len(self.servers)} SERVER DIMUAT DARI CFG"
        )

    # =================================
    # RELOAD CFG
    # =================================

    def reload_config(self, event):

        if self.running:
            return

        try:

            self.load_config()

            wx.MessageBox(
                f"Konfigurasi berhasil dimuat!\n\n"
                f"Jumlah server aktif: {len(self.servers)}\n\n"
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
    # HTTP CHECKER
    # =================================

    def check_server(self, server):

        start = time.perf_counter()

        try:

            request = urllib.request.Request(
                server["url"],
                headers={
                    "User-Agent": "SammanMedia-BPJS-Checker/3.0",
                    "Accept": "*/*"
                },
                method="GET"
            )

            with urllib.request.urlopen(
                request,
                timeout=8
            ) as response:

                code = response.status

        except urllib.error.HTTPError as e:

            code = e.code

        except urllib.error.URLError as e:

            return self.error_result(
                start, "GAGAL KONEKSI", str(e.reason)
            )

        except (socket.timeout, TimeoutError):

            return self.error_result(
                start, "TIMEOUT",
                "Koneksi melebihi 8 detik."
            )

        except ssl.SSLError as e:

            return self.error_result(
                start, "SSL ERROR", str(e)
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
            detail = "Server memberikan respons HTTP."

        elif code < 500:

            status = "AKSES DITOLAK"
            detail = (
                "Server merespons, tetapi akses ditolak "
                "atau alamat endpoint tidak tersedia."
            )

        else:

            status = "SERVER ERROR"
            detail = (
                "Server memberikan respons kesalahan "
                "dengan status HTTP 5xx."
            )

        return {
            "code": str(code),
            "elapsed": elapsed,
            "status": status,
            "detail": detail
        }

    def error_result(self, start, status, detail):

        elapsed = (
            time.perf_counter() - start
        ) * 1000

        return {
            "code": "-",
            "elapsed": elapsed,
            "status": status,
            "detail": detail
        }

    # =================================
    # START CHECK
    # =================================

    def start_check(self, event):

        if self.running:
            return

        if not self.servers:

            wx.MessageBox(
                "Tidak ada server aktif di CFG.",
                "INFORMASI",
                wx.OK | wx.ICON_INFORMATION
            )

            return

        self.running = True
        self.stop_event.clear()

        self.btn_check.Disable()
        self.btn_reload.Disable()
        self.btn_stop.Enable()

        self.progress.SetRange(len(self.servers))
        self.progress.SetValue(0)

        for i, server in enumerate(self.servers):

            self.table.SetItem(i, 3, "-")
            self.table.SetItem(i, 4, "-")
            self.table.SetItem(i, 5, "MEMERIKSA")
            self.table.SetItem(i, 6, server["description"])
            self.table.SetItemBackgroundColour(
                i, wx.NullColour
            )

        self.status.SetLabel(
            "STATUS: MEMERIKSA SERVER..."
        )

        threading.Thread(
            target=self.run_checks,
            daemon=True
        ).start()

    # =================================
    # BACKGROUND CHECK
    # =================================

    def run_checks(self):

        completed = 0
        total = len(self.servers)

        with ThreadPoolExecutor(max_workers=4) as executor:

            futures = {
                executor.submit(
                    self.check_server, server
                ): index

                for index, server in enumerate(self.servers)
            }

            for future in as_completed(futures):

                if self.stop_event.is_set():

                    for f in futures:
                        f.cancel()

                    break

                index = futures[future]

                try:
                    result = future.result()

                except Exception as e:

                    result = {
                        "code": "-",
                        "elapsed": 0,
                        "status": "ERROR",
                        "detail": str(e)
                    }

                wx.CallAfter(
                    self.update_result,
                    index,
                    result
                )

                completed += 1

                wx.CallAfter(
                    self.progress.SetValue,
                    completed
                )

                wx.CallAfter(
                    self.status.SetLabel,
                    f"STATUS: MEMERIKSA {completed}/{total} SERVER"
                )

        wx.CallAfter(self.finish_check)

    # =================================
    # UPDATE TABLE
    # =================================

    def update_result(self, index, result):

        self.table.SetItem(
            index, 3, result["code"]
        )

        self.table.SetItem(
            index, 4,
            f'{result["elapsed"]:.0f} ms'
        )

        self.table.SetItem(
            index, 5, result["status"]
        )

        original = self.servers[index]["description"]

        description = (
            original + " | HASIL: " + result["detail"]
        )

        self.table.SetItem(
            index, 6, description
        )

        if result["status"] == "ONLINE":
            color = wx.Colour(210, 245, 215)

        elif result["status"] == "SERVER ERROR":
            color = wx.Colour(255, 225, 180)

        else:
            color = wx.Colour(255, 205, 205)

        self.table.SetItemBackgroundColour(
            index, color
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

        self.btn_check.Enable()
        self.btn_reload.Enable()
        self.btn_stop.Disable()

        if self.stop_event.is_set():

            self.status.SetLabel(
                "STATUS: PEMERIKSAAN DIHENTIKAN"
            )

        else:

            self.status.SetLabel(
                "STATUS: PEMERIKSAAN SELESAI"
            )

    # =================================
    # CLEAR
    # =================================

    def clear_table(self, event):

        if self.running:
            return

        for i, server in enumerate(self.servers):

            self.table.SetItem(i, 3, "-")
            self.table.SetItem(i, 4, "-")
            self.table.SetItem(i, 5, "MENUNGGU")
            self.table.SetItem(
                i, 6, server["description"]
            )

            self.table.SetItemBackgroundColour(
                i, wx.NullColour
            )

        self.progress.SetValue(0)

        self.status.SetLabel(
            "STATUS: SIAP MELAKUKAN PEMERIKSAAN"
        )

    # =================================
    # CLOSE
    # =================================

    def on_close(self, event):

        if self.running:

            self.stop_event.set()

        self.Destroy()


if __name__ == "__main__":

    app = wx.App(False)

    frame = BPJSChecker()
    frame.Show()

    app.MainLoop()
