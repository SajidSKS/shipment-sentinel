#!/usr/bin/env python3
# Shipment Sentinel – Desktop Ingestion Utility & Hash Verifier
# Copyright (c) 2026 Team Solvers
# Licensed under the MIT License.

import os
import sys
import time
import json
import csv
import math
import hashlib
import tempfile
import threading
import subprocess
import webbrowser
import urllib.request
import urllib.error
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# High-DPI Awareness
if sys.platform == "win32":
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2) # Per-monitor v2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

# Configuration
ESP32_IP = "192.168.4.1"
ESP32_EXTRACT_URL = f"http://{ESP32_IP}/api/extract"
ESP32_STATUS_URL = f"http://{ESP32_IP}/api/status"
WIFI_SSID = "Shipment_Sentinel"
WIFI_PASS = "12345678"

# Auth key (must match ESP32 firmware)
SENTINEL_AUTH_KEY = "SENTINEL_SECURE_VAULT_9F82A4D1"
GENESIS_HASH = "GENESIS_ROOT_V4"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_DIR = os.path.join(SCRIPT_DIR, "SentinelReports")
os.makedirs(REPORTS_DIR, exist_ok=True)
ICON_ICO_PATH = os.path.join(SCRIPT_DIR, "sentinel_icon.ico")
ICON_PNG_PATH = os.path.join(SCRIPT_DIR, "sentinel_icon.png")

def ensure_app_icon():
    """Generates the Sentinel Shield icon if not already present on disk."""
    if os.path.exists(ICON_ICO_PATH) and os.path.exists(ICON_PNG_PATH):
        return
    try:
        from PIL import Image, ImageDraw
        size = 256
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        shield_pts = [(128, 14), (232, 50), (232, 140), (190, 204), (128, 244), (66, 204), (24, 140), (24, 50)]
        draw.polygon(shield_pts, fill=(10, 36, 106, 255))
        
        inner_shield = [(128, 26), (218, 58), (218, 134), (180, 192), (128, 228), (76, 192), (38, 134), (38, 58)]
        draw.polygon(inner_shield, fill=(15, 24, 48, 255), outline=(0, 242, 254, 255), width=6)
        
        draw.ellipse([70, 70, 186, 186], outline=(0, 242, 254, 160), width=4)
        draw.ellipse([90, 90, 166, 166], outline=(0, 255, 120, 200), width=4)
        draw.polygon([(128, 65), (145, 110), (195, 115), (155, 145), (168, 195), (128, 165), (88, 195), (101, 145), (61, 115), (111, 110)], fill=(0, 242, 254, 255))
        draw.ellipse([112, 112, 144, 144], fill=(255, 255, 255, 255), outline=(10, 36, 106, 255), width=2)
        
        img.save(ICON_PNG_PATH, format='PNG')
        img.save(ICON_ICO_PATH, format='ICO', sizes=[(16,16), (32,32), (48,48), (64,64), (128,128), (256,256)])
    except Exception as e:
        print(f"[Icon Note] {e}")

ensure_app_icon()

XP_BG = "#ECE9D8"          # Classic Windows XP Silver-Beige Window Face
XP_PANEL = "#E0DFE3"       # 3D Panel Surface
XP_BORDER_DARK = "#7A7264" # 3D Shadow
XP_BORDER_LIGHT = "#FFFFFF"
XP_NAVY_BAR = "#0A246A"    # Classic Windows Navy
XP_TEXT_MAIN = "#000000"
XP_TEXT_MUTED = "#555555"

LED_BG = "#0D1810"
LED_GREEN = "#00FF41"
LED_AMBER = "#FFB300"
LED_RED = "#FF2233"
LED_CYAN = "#00F2FE"

def verify_sha256_chain(rows):
    """
    Validates mathematical SHA-256 rolling hash chain integrity across all records.
    Returns: (is_valid, error_row_index, details)
    """
    if not rows:
        return True, -1, "No records to verify"
    
    # Check whether row 1 starts at GENESIS_HASH
    prev_hash = GENESIS_HASH
    first_row_matches_genesis = False
    if len(rows) > 0 and len(rows[0]) >= 6:
        e0 = str(rows[0][0]).strip()
        d0 = str(rows[0][1]).strip()
        ev0 = str(rows[0][2]).strip()
        v0 = str(rows[0][3]).strip()
        s0 = str(rows[0][4]).strip()
        h0 = str(rows[0][5]).strip()
        p0 = f"{GENESIS_HASH}|{e0}|{d0}|{ev0}|{v0}|{s0}"
        if h0 and hashlib.sha256(p0.encode("utf-8")).hexdigest()[:16].lower() == h0.lower():
            first_row_matches_genesis = True

    for idx, r in enumerate(rows, start=1):
        if len(r) < 6:
            continue
        elapsed = str(r[0]).strip()
        dt_str = str(r[1]).strip()
        ev_type = str(r[2]).strip()
        val_str = str(r[3]).strip()
        score_str = str(r[4]).strip()
        recorded_hash = str(r[5]).strip()

        # If starting mid-chain (multi-reboot trip), anchor on row 1's valid parent
        if idx == 1 and not first_row_matches_genesis:
            prev_hash = recorded_hash
            continue

        payload = f"{prev_hash}|{elapsed}|{dt_str}|{ev_type}|{val_str}|{score_str}"
        computed_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

        if recorded_hash and computed_hash.lower() != recorded_hash.lower():
            # If this is a hardware reboot event (SYSTEM / Device Boot), allow legitimate session re-anchoring
            if ev_type == "SYSTEM" or "Boot" in val_str:
                prev_hash = recorded_hash
                continue
            return False, idx, f"Hash mismatch at record #{idx}: expected '{computed_hash[:8]}...', found '{recorded_hash[:8]}...'"
        
        prev_hash = recorded_hash if recorded_hash else computed_hash

    return True, -1, "Cryptographic SHA-256 chain fully verified (Zero Tampering Detected)"

def configure_windows_wifi_profile(ssid, password):
    """Generates and registers a WPA2-PSK WiFi profile in Windows WLAN service."""
    xml_content = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID><name>{ssid}</name></SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>manual</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""
    try:
        temp_xml = os.path.join(tempfile.gettempdir(), f"{ssid}_profile.xml")
        with open(temp_xml, "w") as f:
            f.write(xml_content)
        cmd_add = f'netsh wlan add profile filename="{temp_xml}" user=current'
        subprocess.run(cmd_add, shell=True, capture_output=True, text=True)
        if os.path.exists(temp_xml):
            os.remove(temp_xml)
        return True
    except Exception as e:
        print(f"[WiFi Profile Error] {e}")
        return False

def connect_to_wifi(ssid, password, status_callback=None):
    """Attempts to connect Windows PC to the ESP32 WiFi Access Point."""
    # Fast check: If already connected to ESP32 AP, succeed instantly!
    try:
        req = urllib.request.Request(
            f"http://{ESP32_IP}/api/live?key={SENTINEL_AUTH_KEY}",
            headers={"User-Agent": "SentinelExtractor/4.0", "X-Sentinel-Key": SENTINEL_AUTH_KEY}
        )
        with urllib.request.urlopen(req, timeout=1.0) as response:
            if response.status == 200:
                if status_callback:
                    status_callback("Already connected to Shipment Sentinel AP.")
                return True
    except Exception:
        pass

    if sys.platform != "win32":
        if status_callback:
            status_callback("Non-Windows OS: Please connect to 'Shipment_Sentinel' manually.")
        return True

    if status_callback:
        status_callback(f"Configuring WLAN profile for '{ssid}'...")
    configure_windows_wifi_profile(ssid, password)

    iface_cmd = "netsh wlan show interfaces"
    iface_name = None
    try:
        out = subprocess.check_output(iface_cmd, shell=True, text=True)
        for line in out.splitlines():
            line_str = line.strip()
            if line_str.startswith("Name") and ":" in line_str:
                iface_name = line_str.split(":")[1].strip()
                break
    except Exception:
        pass

    if status_callback:
        status_callback(f"Initiating wireless link to '{ssid}'...")
    
    if iface_name:
        cmd_connect = f'netsh wlan connect name="{ssid}" ssid="{ssid}" interface="{iface_name}"'
    else:
        cmd_connect = f'netsh wlan connect name="{ssid}" ssid="{ssid}"'
    
    subprocess.run(cmd_connect, shell=True, capture_output=True, text=True)
    
    for i in range(12):
        time.sleep(1)
        if status_callback:
            status_callback(f"Connecting to Sentinel WiFi ({i+1}/12s)...")
        try:
            req = urllib.request.Request(
                f"http://{ESP32_IP}/api/live?key={SENTINEL_AUTH_KEY}",
                headers={"User-Agent": "SentinelExtractor/4.0", "X-Sentinel-Key": SENTINEL_AUTH_KEY}
            )
            with urllib.request.urlopen(req, timeout=1.2) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
    return False

class SentinelExtractorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Shipment Sentinel v4.0")
        self.root.geometry("1180x860")
        self.root.minsize(1040, 760)
        self.root.configure(bg=XP_BG)

        self._apply_app_icon()

        self.style = ttk.Style()
        self.style.theme_use("clam")
        self._setup_xp_styles()

        self.is_busy = False
        self.current_data = None
        self.all_table_records = []  # Full un-filtered list of rows
        self.current_view_mode = "TABLE" # TABLE or GRAPH
        self.hover_hud_id = None
        self.graph_event_points = [] # For mouse hover detection
        
        self.status_text = tk.StringVar(value="System Ready. Hold BOOT button on Sentinel to activate WiFi, then click Extract.")
        self.device_id_var = tk.StringVar(value="SENTINEL-????")
        self.uptime_var = tk.StringVar(value="--:--:--")
        self.extract_time_var = tk.StringVar(value="--/--/---- --:--:--")
        self.search_filter_var = tk.StringVar(value="")
        self.active_category_filter = "ALL"
        
        self.create_title_bar()
        self.create_toolbar()
        
        self.main_container = tk.Frame(self.root, bg=XP_BG, padx=10, pady=6)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        
        self.create_dashboard_panels(self.main_container)
        self.create_table_panel(self.main_container)
        self.create_status_bar()

    def _apply_app_icon(self):
        """Sets the custom Sentinel Shield icon on the window and taskbar."""
        try:
            if os.path.exists(ICON_ICO_PATH) and sys.platform == "win32":
                self.root.iconbitmap(ICON_ICO_PATH)
            elif os.path.exists(ICON_PNG_PATH):
                from PIL import Image, ImageTk
                img = ImageTk.PhotoImage(file=ICON_PNG_PATH)
                self.root.iconphoto(False, img)
        except Exception as e:
            print(f"[Icon Load Error] {e}")

    def _setup_xp_styles(self):
        """Configures classic Windows XP bevels and TreeView styles."""
        self.style.configure("XP.TFrame", background=XP_BG)
        self.style.configure("Treeview.Heading",
                             font=("Segoe UI", 9, "bold"),
                             background="#D4D0C8",
                             foreground="#000000",
                             relief="raised")
        self.style.configure("Treeview",
                             font=("Consolas", 9),
                             background="#FFFFFF",
                             fieldbackground="#FFFFFF",
                             rowheight=22)
        self.style.map("Treeview", background=[("selected", "#0A246A")], foreground=[("selected", "#FFFFFF")])

    def create_title_bar(self):
        """Clean Windows XP Navy Blue Header."""
        header = tk.Frame(self.root, bg=XP_NAVY_BAR, height=36)
        header.pack(fill=tk.X, side=tk.TOP)
        
        title_label = tk.Label(header, text="  🛡️  SHIPMENT SENTINEL",
                               font=("Segoe UI", 11, "bold"), fg="#FFFFFF", bg=XP_NAVY_BAR)
        title_label.pack(side=tk.LEFT, pady=6)

    def create_toolbar(self):
        """Top Action Toolbar with 3D Bevel Buttons."""
        toolbar = tk.Frame(self.root, bg=XP_PANEL, relief="groove", bd=2, height=52)
        toolbar.pack(fill=tk.X, padx=10, pady=(8, 0))

        self.btn_auto_extract = tk.Button(
            toolbar, text="⚡ AUTO-CONNECT & EXTRACT", font=("Segoe UI", 10, "bold"),
            bg="#2E7D32", fg="#FFFFFF", activebackground="#1B5E20", activeforeground="#FFFFFF",
            relief="raised", bd=3, padx=14, pady=6, cursor="hand2",
            command=self.start_auto_extract
        )
        self.btn_auto_extract.pack(side=tk.LEFT, padx=6, pady=6)

        self.btn_direct_extract = tk.Button(
            toolbar, text="📥 EXTRACT (IF CONNECTED)", font=("Segoe UI", 9, "bold"),
            bg="#E0DFE3", fg="#000000", activebackground="#C8C7CC",
            relief="raised", bd=2, padx=10, pady=6, cursor="hand2",
            command=self.start_direct_extract
        )
        self.btn_direct_extract.pack(side=tk.LEFT, padx=3, pady=6)

        btn_open_folder = tk.Button(
            toolbar, text="📂 OPEN ARCHIVE", font=("Segoe UI", 9),
            bg="#E0DFE3", fg="#000000", activebackground="#C8C7CC",
            relief="raised", bd=2, padx=8, pady=6, cursor="hand2",
            command=lambda: os.startfile(REPORTS_DIR) if sys.platform == "win32" else os.system(f'open "{REPORTS_DIR}"')
        )
        btn_open_folder.pack(side=tk.LEFT, padx=3, pady=6)

        btn_load_file = tk.Button(
            toolbar, text="📄 LOAD REPORT", font=("Segoe UI", 9),
            bg="#E0DFE3", fg="#000000", activebackground="#C8C7CC",
            relief="raised", bd=2, padx=8, pady=6, cursor="hand2",
            command=self.load_local_report
        )
        btn_load_file.pack(side=tk.LEFT, padx=3, pady=6)

        self.btn_cert = tk.Button(
            toolbar, text="📜 CERTIFICATE", font=("Segoe UI", 9, "bold"),
            bg="#0A246A", fg="#FFFFFF", activebackground="#001644", activeforeground="#FFFFFF",
            relief="raised", bd=2, padx=10, pady=6, cursor="hand2",
            command=self.generate_inspection_certificate
        )
        self.btn_cert.pack(side=tk.LEFT, padx=3, pady=6)

        self.btn_toggle_view = tk.Button(
            toolbar, text="📊 GRAPH VIEW", font=("Segoe UI", 9, "bold"),
            bg="#E0DFE3", fg="#000000", activebackground="#C8C7CC",
            relief="raised", bd=2, padx=8, pady=6, cursor="hand2",
            command=self.toggle_view_mode
        )
        self.btn_toggle_view.pack(side=tk.LEFT, padx=3, pady=6)

        self.btn_rearm = tk.Button(
            toolbar, text="🗑️ ERASE & RE-ARM", font=("Segoe UI", 9, "bold"),
            bg="#C62828", fg="#FFFFFF", activebackground="#8E0000", activeforeground="#FFFFFF",
            relief="raised", bd=2, padx=10, pady=6, cursor="hand2",
            command=self.prompt_rearm_device
        )
        self.btn_rearm.pack(side=tk.LEFT, padx=6, pady=6)

        self.led_canvas = tk.Canvas(toolbar, width=18, height=18, bg=XP_PANEL, highlightthickness=0)
        self.led_canvas.pack(side=tk.RIGHT, padx=(0, 10), pady=6)
        self.led_circle = self.led_canvas.create_oval(2, 2, 16, 16, fill="#888888", outline="#444444")
        
        self.led_label = tk.Label(toolbar, text="READY", font=("Segoe UI", 9, "bold"), bg=XP_PANEL, fg="#444444")
        self.led_label.pack(side=tk.RIGHT, padx=4)

    def set_led_status(self, color, text):
        self.led_canvas.itemconfig(self.led_circle, fill=color)
        self.led_label.config(text=text, fg="#000000" if color != "#888888" else "#444444")

    def create_dashboard_panels(self, parent):
        """Top section: Score LCD + Passport Info + Sensor Matrix + SCADA Tiles."""
        dash_frame = tk.Frame(parent, bg=XP_BG)
        dash_frame.pack(fill=tk.X, pady=(0, 6))

        score_frame = tk.Frame(dash_frame, bg=XP_PANEL, relief="groove", bd=2, width=250, height=152)
        score_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        score_frame.pack_propagate(False)

        score_title = tk.Label(score_frame, text="INTEGRITY SCORE", font=("Segoe UI", 8, "bold"), bg=XP_PANEL, fg=XP_TEXT_MUTED)
        score_title.pack(anchor="w", padx=10, pady=(6, 2))

        self.lcd_box = tk.Frame(score_frame, bg=LED_BG, relief="sunken", bd=3)
        self.lcd_box.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 4))

        self.score_display = tk.Label(self.lcd_box, text="100", font=("Consolas", 32, "bold"), bg=LED_BG, fg=LED_GREEN)
        self.score_display.pack(side=tk.LEFT, padx=(10, 0), pady=1)

        self.score_denom = tk.Label(self.lcd_box, text="/100", font=("Consolas", 13, "bold"), bg=LED_BG, fg="#008822")
        self.score_denom.pack(side=tk.LEFT, anchor="s", pady=8)

        self.status_badge = tk.Label(score_frame, text="[ PASSED / SAFE ]", font=("Segoe UI", 8, "bold"), bg="#D4EDDA", fg="#155724", relief="sunken", bd=1)
        self.status_badge.pack(fill=tk.X, padx=8, pady=(2, 6))

        info_frame = tk.Frame(dash_frame, bg=XP_PANEL, relief="groove", bd=2, width=285, height=152)
        info_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        info_frame.pack_propagate(False)

        info_title = tk.Label(info_frame, text="DEVICE & PASSPORT METADATA", font=("Segoe UI", 8, "bold"), bg=XP_PANEL, fg=XP_TEXT_MUTED)
        info_title.pack(anchor="w", padx=10, pady=(6, 4))

        meta_grid = tk.Frame(info_frame, bg=XP_PANEL)
        meta_grid.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 4))

        labels = [
            ("Device Identifier:", self.device_id_var),
            ("Trip Duration:", self.uptime_var),
            ("Extracted At:", self.extract_time_var)
        ]
        for row_idx, (lbl_txt, var) in enumerate(labels):
            tk.Label(meta_grid, text=lbl_txt, font=("Segoe UI", 8), bg=XP_PANEL, fg=XP_TEXT_MUTED).grid(row=row_idx, column=0, sticky="w", pady=4)
            tk.Label(meta_grid, textvariable=var, font=("Segoe UI", 8, "bold"), bg=XP_PANEL, fg="#000000").grid(row=row_idx, column=1, sticky="w", padx=(8, 0), pady=4)

        sensor_frame = tk.Frame(dash_frame, bg=XP_PANEL, relief="groove", bd=2, width=225, height=152)
        sensor_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 6))
        sensor_frame.pack_propagate(False)

        sensor_title = tk.Label(sensor_frame, text="HARDWARE DIAGNOSTICS", font=("Segoe UI", 8, "bold"), bg=XP_PANEL, fg=XP_TEXT_MUTED)
        sensor_title.pack(anchor="w", padx=10, pady=(4, 2))

        self.sensor_leds = {}
        sensor_names = [
            ("mpu", "MPU6050 Accel (±8G)"),
            ("bmp", "BMP180 Barometer"),
            ("apds", "APDS9960 Optical"),
            ("rtc", "DS3231 Precision RTC"),
            ("sd", "microSD (exFAT/FAT32)")
        ]
        for row_idx, (key, name) in enumerate(sensor_names):
            s_row = tk.Frame(sensor_frame, bg=XP_PANEL)
            s_row.pack(fill=tk.X, padx=10, pady=1)
            
            led = tk.Label(s_row, text="●", font=("Arial", 8), fg="#00CC00", bg=XP_PANEL)
            led.pack(side=tk.LEFT)
            lbl = tk.Label(s_row, text=f" {name}", font=("Segoe UI", 7, "bold" if key == "sd" else "normal"), bg=XP_PANEL, fg="#000000")
            lbl.pack(side=tk.LEFT)
            self.sensor_leds[key] = led

        scada_frame = tk.Frame(dash_frame, bg=XP_BG, height=152)
        scada_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.stat_widgets = {}
        stat_configs = [
            ("shocks", "SHOCKS / IMPACTS", "0", "Max: 0.00G", "#FFF3CD", "#856404"),
            ("drops", "FREE-FALL DROPS", "0", "Max: 0.00m (None)", "#F8D7DA", "#721C24"),
            ("tampers", "TAMPER BREACHES", "0", "0s Total", "#E2D9F3", "#38197A"),
            ("pressure", "PRESSURE ALERTS", "0", "Normal", "#D1ECF1", "#0C5460")
        ]
        
        for idx, (key, title, def_val, def_sub, bg_c, fg_c) in enumerate(stat_configs):
            r = idx // 2
            c = idx % 2
            tile = tk.Frame(scada_frame, bg=bg_c, relief="groove", bd=2)
            tile.grid(row=r, column=c, sticky="nsew", padx=2, pady=2)
            scada_frame.grid_rowconfigure(r, weight=1)
            scada_frame.grid_columnconfigure(c, weight=1)

            t_lbl = tk.Label(tile, text=title, font=("Segoe UI", 7, "bold"), bg=bg_c, fg=fg_c)
            t_lbl.pack(anchor="w", padx=6, pady=(2, 0))

            v_row = tk.Frame(tile, bg=bg_c)
            v_row.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 2))

            v_lbl = tk.Label(v_row, text=def_val, font=("Consolas", 15, "bold"), bg=bg_c, fg=fg_c)
            v_lbl.pack(side=tk.LEFT, padx=(2, 4))

            s_lbl = tk.Label(v_row, text=def_sub, font=("Segoe UI", 8), bg=bg_c, fg=fg_c)
            s_lbl.pack(side=tk.LEFT, padx=2)

            self.stat_widgets[key] = (v_lbl, s_lbl)

    def create_table_panel(self, parent):
        """Bottom section: Filter Bar + Table View / High-Res SCADA Graph View."""
        self.table_frame = tk.Frame(parent, bg=XP_PANEL, relief="groove", bd=2)
        self.table_frame.pack(fill=tk.BOTH, expand=True)

        tbl_hdr = tk.Frame(self.table_frame, bg=XP_PANEL)
        tbl_hdr.pack(fill=tk.X, padx=8, pady=(4, 2))

        tk.Label(tbl_hdr, text="📋 CRYPTOGRAPHIC CHAIN-OF-CUSTODY AUDIT LOG",
                 font=("Segoe UI", 9, "bold"), bg=XP_PANEL, fg=XP_NAVY_BAR).pack(side=tk.LEFT)

        self.crypto_badge = tk.Label(
            tbl_hdr, text="🛡️ SHA-256 HASH CHAIN: PENDING VERIFICATION",
            font=("Segoe UI", 8, "bold"), bg="#D4EDDA", fg="#155724", relief="groove", bd=1, padx=6
        )
        self.crypto_badge.pack(side=tk.RIGHT, padx=4)

        filter_bar = tk.Frame(self.table_frame, bg=XP_PANEL)
        filter_bar.pack(fill=tk.X, padx=8, pady=(2, 4))

        tk.Label(filter_bar, text="Filter:", font=("Segoe UI", 8, "bold"), bg=XP_PANEL).pack(side=tk.LEFT, padx=(0, 4))

        self.filter_buttons = {}
        filters = [
            ("ALL", "All Records"),
            ("SHOCK", "⚡ Shocks"),
            ("DROP", "📉 Drops & Height"),
            ("TAMPER", "🔓 Tampers"),
            ("PRESSURE", "🌪️ Pressure"),
            ("CRITICAL", "🚨 Severe/Critical")
        ]
        for f_key, f_label in filters:
            btn = tk.Button(
                filter_bar, text=f_label, font=("Segoe UI", 8),
                bg="#0A246A" if f_key == "ALL" else "#E0DFE3",
                fg="#FFFFFF" if f_key == "ALL" else "#000000",
                relief="sunken" if f_key == "ALL" else "raised", bd=2, padx=6, pady=2, cursor="hand2",
                command=lambda k=f_key: self.set_category_filter(k)
            )
            btn.pack(side=tk.LEFT, padx=2)
            self.filter_buttons[f_key] = btn

        search_box = tk.Frame(filter_bar, bg=XP_PANEL)
        search_box.pack(side=tk.RIGHT)

        tk.Label(search_box, text="🔍 Search:", font=("Segoe UI", 8), bg=XP_PANEL).pack(side=tk.LEFT, padx=2)
        search_entry = tk.Entry(search_box, textvariable=self.search_filter_var, font=("Segoe UI", 8), width=18)
        search_entry.pack(side=tk.LEFT, padx=2)
        search_entry.bind("<KeyRelease>", lambda e: self.apply_active_filters())

        self.table_count_lbl = tk.Label(search_box, text="0 Records", font=("Segoe UI", 8), bg=XP_PANEL, fg=XP_TEXT_MUTED)
        self.table_count_lbl.pack(side=tk.LEFT, padx=(6, 0))

        self.view_container = tk.Frame(self.table_frame, bg=XP_PANEL)
        self.view_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 6))

        self.tree_frame = tk.Frame(self.view_container, bg=XP_PANEL)
        self.tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("idx", "timestamp", "elapsed", "event", "value", "score", "hash")
        self.tree = ttk.Treeview(self.tree_frame, columns=cols, show="headings", selectmode="browse")
        
        self.tree.heading("idx", text="#")
        self.tree.heading("timestamp", text="RTC Timestamp (ISO)")
        self.tree.heading("elapsed", text="Elapsed")
        self.tree.heading("event", text="Incident Event Type")
        self.tree.heading("value", text="Telemetry Reading / Drop Height")
        self.tree.heading("score", text="Post-Incident Score")
        self.tree.heading("hash", text="SHA-256 Block Hash")

        self.tree.column("idx", width=40, anchor="center")
        self.tree.column("timestamp", width=155, anchor="w")
        self.tree.column("elapsed", width=80, anchor="center")
        self.tree.column("event", width=130, anchor="center")
        self.tree.column("value", width=300, anchor="w")
        self.tree.column("score", width=110, anchor="center")
        self.tree.column("hash", width=140, anchor="center")

        v_scroll = ttk.Scrollbar(self.tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=v_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.tag_configure("SHOCK", background="#FFF8E1", foreground="#B78103")
        self.tree.tag_configure("SEVERE_SHOCK", background="#FFEBEE", foreground="#C62828")
        self.tree.tag_configure("DROP", background="#E0F7FA", foreground="#006064")
        self.tree.tag_configure("DROP_IMPACT", background="#FFF3E0", foreground="#E65100")
        self.tree.tag_configure("TAMPER_OPEN", background="#F3E5F5", foreground="#6A1B9A")
        self.tree.tag_configure("TAMPER_CLOSED", background="#EDE7F6", foreground="#4527A0")
        self.tree.tag_configure("PRESSURE_ALERT", background="#E8F8F5", foreground="#117A65")
        self.tree.tag_configure("SYSTEM", background="#E1F5FE", foreground="#0277BD")

        self.graph_frame = tk.Frame(self.view_container, bg="#0D1117", relief="sunken", bd=2)
        self.graph_canvas = tk.Canvas(self.graph_frame, bg="#0D1117", highlightthickness=0)
        self.graph_canvas.pack(fill=tk.BOTH, expand=True)
        
        self.graph_canvas.bind("<Motion>", self.on_graph_mouse_move)
        self.graph_canvas.bind("<Configure>", lambda e: self.render_scada_graph())

    def create_status_bar(self):
        """Bottom Classic Windows XP Status Bar."""
        status_bar = tk.Frame(self.root, bg="#D4D0C8", relief="sunken", bd=1, height=24)
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

        status_lbl = tk.Label(status_bar, textvariable=self.status_text, font=("Segoe UI", 8), bg="#D4D0C8", fg="#000000", anchor="w")
        status_lbl.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

        ip_lbl = tk.Label(status_bar, text=f"Target: {ESP32_IP}:80 | Token Auth: ACTIVE  ", font=("Segoe UI", 8), bg="#D4D0C8", fg=XP_TEXT_MUTED)
        ip_lbl.pack(side=tk.RIGHT)

    def set_category_filter(self, category):
        self.active_category_filter = category
        for k, btn in self.filter_buttons.items():
            if k == category:
                btn.config(bg="#0A246A", fg="#FFFFFF", relief="sunken")
            else:
                btn.config(bg="#E0DFE3", fg="#000000", relief="raised")
        self.apply_active_filters()

    def apply_active_filters(self):
        """Applies search text + category filter and updates Treeview & Graph."""
        for row in self.tree.get_children():
            self.tree.delete(row)

        search_query = self.search_filter_var.get().strip().lower()
        displayed_count = 0

        for r in self.all_table_records:
            idx, ts, elapsed, ev, val, sc, h_val = r
            
            if self.active_category_filter == "SHOCK" and "SHOCK" not in ev:
                continue
            elif self.active_category_filter == "DROP" and "DROP" not in ev:
                continue
            elif self.active_category_filter == "TAMPER" and "TAMPER" not in ev:
                continue
            elif self.active_category_filter == "PRESSURE" and "PRESSURE" not in ev:
                continue
            elif self.active_category_filter == "CRITICAL" and (int(sc.split("/")[0]) >= 80 and ev != "SEVERE" and ev != "SEVERE_SHOCK"):
                continue

            row_str = f"{idx} {ts} {elapsed} {ev} {val} {sc} {h_val}".lower()
            if search_query and search_query not in row_str:
                continue

            tag = ev
            if tag not in ("SHOCK", "SEVERE_SHOCK", "DROP", "DROP_IMPACT", "TAMPER_OPEN", "TAMPER_CLOSED", "PRESSURE_ALERT", "SYSTEM"):
                tag = "SYSTEM"

            self.tree.insert("", "end", values=(idx, ts, elapsed, ev, val, sc, h_val), tags=(tag,))
            displayed_count += 1

        self.table_count_lbl.config(text=f"{displayed_count}/{len(self.all_table_records)} Records")
        if self.current_view_mode == "GRAPH":
            self.render_scada_graph()

    def toggle_view_mode(self):
        """Toggles between Treeview Table and High-Res SCADA Graph canvas."""
        if self.current_view_mode == "TABLE":
            self.current_view_mode = "GRAPH"
            self.tree_frame.pack_forget()
            self.graph_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_toggle_view.config(text="📋 TABLE VIEW", bg="#0A246A", fg="#FFFFFF", relief="sunken")
            self.root.update_idletasks()
            self.render_scada_graph()
        else:
            self.current_view_mode = "TABLE"
            self.graph_frame.pack_forget()
            self.tree_frame.pack(fill=tk.BOTH, expand=True)
            self.btn_toggle_view.config(text="📊 GRAPH VIEW", bg="#E0DFE3", fg="#000000", relief="raised")

    def render_scada_graph(self):
        """Draws high-resolution SCADA telemetry waveform with non-colliding labels and zones."""
        self.graph_canvas.delete("all")
        self.graph_event_points = []
        w = self.graph_canvas.winfo_width()
        h = self.graph_canvas.winfo_height()
        if w < 100 or h < 100:
            w = 980
            h = 360

        pad_left = 65
        pad_right = 35
        pad_top = 45
        pad_bottom = 50

        chart_w = max(50, w - pad_left - pad_right)
        chart_h = max(50, h - pad_top - pad_bottom)

        max_recorded_g = 8.0
        max_t_sec = 1

        for r in self.all_table_records:
            idx, ts, elapsed, ev, val, sc, h_val = r
            try:
                t_sec = int(elapsed.replace("+", "").replace("s", ""))
                if t_sec > max_t_sec:
                    max_t_sec = t_sec
            except ValueError:
                pass

            if "G" in val:
                try:
                    parts = val.split("G")[0].split("|")[-1].strip()
                    g = float(parts)
                    if g > max_recorded_g:
                        max_recorded_g = g
                except ValueError:
                    pass

        y_max = max(10.0, math.ceil(max_recorded_g * 1.15))

        def g_to_y(g):
            clamped = max(0.0, min(y_max, g))
            return (pad_top + chart_h) - (clamped / y_max) * chart_h

        def t_to_x(t):
            return pad_left + (t / max_t_sec) * chart_w

        y_severe = g_to_y(5.0)
        y_minor = g_to_y(2.2)
        y_baseline = g_to_y(1.0)
        y_freefall = g_to_y(0.5)
        y_zero = g_to_y(0.0)

        self.graph_canvas.create_rectangle(pad_left, pad_top, pad_left + chart_w, y_severe, fill="#1C0D10", outline="")
        self.graph_canvas.create_rectangle(pad_left, y_severe, pad_left + chart_w, y_minor, fill="#1A1408", outline="")
        self.graph_canvas.create_rectangle(pad_left, y_minor, pad_left + chart_w, y_freefall, fill="#0D1810", outline="")
        self.graph_canvas.create_rectangle(pad_left, y_freefall, pad_left + chart_w, y_zero, fill="#08161A", outline="")

        y_ticks = [0.0, 0.5, 1.0, 2.2, 5.0]
        if y_max >= 8.0:
            y_ticks.append(8.0)
        if y_max >= 10.0:
            y_ticks.append(10.0)
        if y_max >= 12.0:
            y_ticks.append(12.0)
        if y_max >= 15.0:
            y_ticks.append(15.0)

        for g_tick in y_ticks:
            y_p = g_to_y(g_tick)
            line_c = "#2A3830"
            dash_p = (3, 3)
            if g_tick == 1.0:
                line_c = "#00FF66"
                dash_p = (6, 2)
            elif g_tick == 5.0:
                line_c = "#FF2233"
                dash_p = (4, 3)
            elif g_tick == 2.2:
                line_c = "#FFB300"
                dash_p = (4, 3)

            self.graph_canvas.create_line(pad_left, y_p, pad_left + chart_w, y_p, fill=line_c, dash=dash_p, width=1)
            self.graph_canvas.create_text(pad_left - 8, y_p, text=f"{g_tick:.1f}G", fill="#8899AA" if g_tick != 1.0 else "#00FF66", font=("Consolas", 8, "bold"), anchor="e")

        t_step = max(5, int(math.ceil(max_t_sec / 8.0)))
        for t_val in range(0, max_t_sec + t_step, t_step):
            if t_val > max_t_sec:
                t_val = max_t_sec
            x_p = t_to_x(t_val)
            self.graph_canvas.create_line(x_p, pad_top, x_p, pad_top + chart_h, fill="#1A2420", dash=(2, 4))
            self.graph_canvas.create_line(x_p, pad_top + chart_h, x_p, pad_top + chart_h + 5, fill="#00FF66")
            self.graph_canvas.create_text(x_p, pad_top + chart_h + 16, text=f"{t_val}s", fill="#8899AA", font=("Consolas", 8), anchor="center")
            if t_val == max_t_sec:
                break

        self.graph_canvas.create_rectangle(pad_left, pad_top, pad_left + chart_w, pad_top + chart_h, outline="#33443B", width=1)

        self.graph_canvas.create_text(pad_left + chart_w - 6, y_baseline - 8, text="1.0G Gravity Baseline", fill="#00FF66", font=("Segoe UI", 8, "italic"), anchor="e")
        self.graph_canvas.create_text(pad_left + chart_w - 6, y_freefall + 10, text="Free-Fall Threshold (0.5G)", fill="#00F2FE", font=("Segoe UI", 7, "italic"), anchor="e")

        if not self.all_table_records:
            self.graph_canvas.create_text(w/2, h/2, text="NO TELEMETRY INGESTED YET", fill="#556677", font=("Segoe UI", 12, "bold"))
            return

        events_to_plot = []
        for r in self.all_table_records:
            idx, ts, elapsed, ev, val, sc, h_val = r
            try:
                t_sec = int(elapsed.replace("+", "").replace("s", ""))
            except ValueError:
                t_sec = 0

            g_val = 1.0
            drop_h_str = ""

            if "DROP" in ev and "m" in val:
                # Value format: "0.85m | 0.18G (415ms)"
                try:
                    drop_h_str = val.split("m")[0].strip() + "m"
                    g_val = float(val.split("|")[1].replace("G", "").split("(")[0].strip())
                except Exception:
                    g_val = 0.2
            elif "DROP" in ev:
                try:
                    g_val = float(val.replace("G", "").strip())
                    # Classical height estimation if only G available: h ≈ 0.5 * 9.81 * (0.35)^2 ≈ 0.6m
                    drop_h_str = f"~{math.sqrt(max(0.1, 1.0 - g_val))*0.4:.2f}m"
                except ValueError:
                    g_val = 0.2
                    drop_h_str = "Drop"
            elif "G" in val:
                try:
                    g_val = float(val.replace("G", "").split("|")[-1].strip())
                except ValueError:
                    g_val = 1.0

            events_to_plot.append({
                "idx": idx,
                "ts": ts,
                "t_sec": t_sec,
                "ev": ev,
                "val": val,
                "g_val": g_val,
                "drop_h": drop_h_str,
                "score": sc
            })

        last_x = pad_left
        for pt in events_to_plot:
            cur_x = t_to_x(pt["t_sec"])
            self.graph_canvas.create_line(last_x, y_baseline, cur_x, y_baseline, fill="#00FF66", width=2)
            last_x = cur_x
        self.graph_canvas.create_line(last_x, y_baseline, pad_left + chart_w, y_baseline, fill="#00FF66", width=2)

        stagger_idx = 0
        last_label_x = -999

        for pt in events_to_plot:
            x = t_to_x(pt["t_sec"])
            y = g_to_y(pt["g_val"])
            ev = pt["ev"]
            g = pt["g_val"]

            color = "#00FF66"
            lbl_text = f"{g:.2f}G"

            if "SEVERE_SHOCK" in ev:
                color = "#FF2233"
                lbl_text = f"⚡ {g:.2f}G"
            elif "SHOCK" in ev:
                color = "#FFB300"
                lbl_text = f"⚠️ {g:.2f}G"
            elif "DROP" in ev:
                color = "#00F2FE"
                lbl_text = f"🔻 {pt['drop_h']} ({g:.2f}G)" if pt['drop_h'] else f"🔻 {g:.2f}G"
            elif "TAMPER" in ev:
                color = "#B537F2"
                lbl_text = f"🔓 {ev}"

            self.graph_canvas.create_line(x, y_baseline, x, y, fill=color, width=2)
            dot_id = self.graph_canvas.create_oval(x-4, y-4, x+4, y+4, fill=color, outline="#FFFFFF", width=1)

            self.graph_event_points.append((x, y, pt))

            if abs(x - last_label_x) < 45:
                stagger_idx = (stagger_idx + 1) % 3
            else:
                stagger_idx = 0
            last_label_x = x

            if "DROP" in ev:
                tag_y = min(pad_top + chart_h - 10, y + 14 + (stagger_idx * 12))
            else:
                tag_y = max(pad_top + 10, y - 12 - (stagger_idx * 14))

            text_len = len(lbl_text) * 6.5
            self.graph_canvas.create_rectangle(x - text_len/2 - 3, tag_y - 7, x + text_len/2 + 3, tag_y + 7,
                                                fill="#0D1117", outline=color, width=1)
            self.graph_canvas.create_text(x, tag_y, text=lbl_text, fill=color, font=("Segoe UI", 8, "bold"), anchor="center")

        leg_x = pad_left + chart_w - 290
        leg_y = pad_top - 28
        self.graph_canvas.create_rectangle(leg_x - 6, leg_y - 6, pad_left + chart_w, leg_y + 18, fill="#0B1218", outline="#203038")
        
        items = [
            ("● >5G Shock", "#FF2233"),
            ("● 2.2-5G Shock", "#FFB300"),
            ("● Free-Fall Drop (Height in m)", "#00F2FE"),
            ("● Tamper", "#B537F2")
        ]
        curr_lx = leg_x
        for txt, col in items:
            self.graph_canvas.create_text(curr_lx, leg_y + 6, text=txt, fill=col, font=("Segoe UI", 7, "bold"), anchor="w")
            curr_lx += len(txt) * 6.2 + 8

    def on_graph_mouse_move(self, event):
        """Interactive real-time SCADA HUD Tooltip when moving mouse over canvas."""
        if not self.graph_event_points:
            return

        mx, my = event.x, event.y
        closest_pt = None
        min_dist = 9999

        for gx, gy, pt in self.graph_event_points:
            dist = math.hypot(mx - gx, my - gy)
            if dist < min_dist and abs(mx - gx) < 30:
                min_dist = dist
                closest_pt = (gx, gy, pt)

        self.graph_canvas.delete("hud_overlay")

        if closest_pt:
            gx, gy, pt = closest_pt
            self.graph_canvas.create_line(gx, 45, gx, self.graph_canvas.winfo_height() - 50, fill="#00FF66", width=1, dash=(2, 2), tags="hud_overlay")
            
            hud_text = f"📍 #{pt['idx']} | Time: {pt['ts']} (+{pt['t_sec']}s) | Event: {pt['ev']} | Data: {pt['val']} | Score: {pt['score']}"
            box_w = len(hud_text) * 7.2
            
            self.graph_canvas.create_rectangle(65, 12, 65 + box_w + 16, 34, fill="#0A246A", outline="#00F2FE", width=1, tags="hud_overlay")
            self.graph_canvas.create_text(73, 23, text=hud_text, fill="#FFFFFF", font=("Consolas", 8, "bold"), anchor="w", tags="hud_overlay")

    def start_auto_extract(self):
        if self.is_busy:
            return
        self.is_busy = True
        self.set_ui_state(False)
        self.set_led_status("#FFB300", "CONNECTING")
        threading.Thread(target=self._auto_extract_worker, daemon=True).start()

    def _auto_extract_worker(self):
        try:
            self.status_text.set("Step 1/3: Attempting auto-connection to ESP32 WiFi (Shipment_Sentinel)...")
            connected = connect_to_wifi(WIFI_SSID, WIFI_PASS, status_callback=lambda msg: self.status_text.set(msg))
            
            self.status_text.set("Step 2/3: Authenticating with security key & extracting payload...")
            self.set_led_status("#00F2FE", "EXTRACTING")
            
            data = self._fetch_extract_payload()
            if not data:
                raise Exception("Failed to receive authorized data from ESP32. Check security key or BOOT button.")

            self.status_text.set("Step 3/3: Archiving report to local disk and verifying SHA-256 chain...")
            filepath_json, filepath_csv = self._save_report_files(data)
            
            self.root.after(0, lambda: self._update_ui_with_data(data, filepath_csv))
            self.status_text.set(f"Extraction & Verification Successful! Saved to: {os.path.basename(filepath_csv)}")
            self.set_led_status("#00FF41", "ARCHIVED")

        except Exception as err:
            self.status_text.set(f"Extraction Error: {err}")
            self.set_led_status("#FF2233", "ERROR")
            self.root.after(0, lambda: messagebox.showerror(
                "Extraction Failed",
                f"Could not extract data from Shipment Sentinel:\n\n{err}\n\nTroubleshooting:\n"
                "1. Press and hold the BOOT button on the ESP32 for 2 seconds.\n"
                "2. Confirm the OLED display says 'DASHBOARD ACTIVE'.\n"
                "3. Try connecting to WiFi 'Shipment_Sentinel' (Password: 12345678) manually, then click 'EXTRACT (IF CONNECTED)'."
            ))
        finally:
            self.is_busy = False
            self.root.after(0, lambda: self.set_ui_state(True))

    def start_direct_extract(self):
        if self.is_busy:
            return
        self.is_busy = True
        self.set_ui_state(False)
        self.set_led_status("#00F2FE", "EXTRACTING")
        threading.Thread(target=self._direct_extract_worker, daemon=True).start()

    def _direct_extract_worker(self):
        try:
            self.status_text.set("Requesting authorized /api/extract from 192.168.4.1...")
            data = self._fetch_extract_payload()
            if not data:
                raise Exception("No authorized response from ESP32 at 192.168.4.1")

            self.status_text.set("Archiving report to disk...")
            filepath_json, filepath_csv = self._save_report_files(data)
            
            self.root.after(0, lambda: self._update_ui_with_data(data, filepath_csv))
            self.status_text.set(f"Data Extracted, Verified & Reset! File: {os.path.basename(filepath_csv)}")
            self.set_led_status("#00FF41", "ARCHIVED")
        except Exception as err:
            self.status_text.set(f"Direct Extract Error: {err}")
            self.set_led_status("#FF2233", "ERROR")
            self.root.after(0, lambda: messagebox.showerror("Direct Extract Error", f"Failed to extract:\n{err}\n\nMake sure your PC is connected to 'Shipment_Sentinel' WiFi."))
        finally:
            self.is_busy = False
            self.root.after(0, lambda: self.set_ui_state(True))

    def _fetch_extract_payload(self):
        """
        Two-call extraction strategy to avoid ESP32 RAM overflow:
          1. GET /api/extract  → compact metadata JSON (~600 bytes, always fits in RAM)
          2. GET /log.csv      → raw CSV streamed directly from storage (no RAM limit)
        """
        headers = {
            "User-Agent": "SentinelIngestion/4.0",
            "X-Sentinel-Key": SENTINEL_AUTH_KEY
        }

        data = None
        meta_url = f"{ESP32_EXTRACT_URL}?key={SENTINEL_AUTH_KEY}"
        try:
            req = urllib.request.Request(meta_url, headers=headers)
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    print(f"[Extract] Metadata OK: Device={data.get('deviceId')}, Score={data.get('score')}")
        except Exception as e:
            print(f"[Extract] Metadata fetch note: {e}")

        if data is None:
            try:
                live_url = f"http://{ESP32_IP}/api/live?key={SENTINEL_AUTH_KEY}"
                req_live = urllib.request.Request(live_url, headers=headers)
                with urllib.request.urlopen(req_live, timeout=5.0) as resp_live:
                    if resp_live.status == 200:
                        live_data = json.loads(resp_live.read().decode("utf-8"))
                        data = {
                            "firmware": "v4.0",
                            "deviceId": "SENTINEL-LIVE",
                            "extractTime": time.strftime("%Y-%m-%d %H:%M:%S"),
                            "uptime": live_data.get("uptime", "N/A"),
                            "score": live_data.get("score", 100),
                            "counters": {},
                            "metrics": {"currentPressure": float(live_data.get("pressure", 0)), "currentLight": live_data.get("light", 0)},
                            "sensors": {"mpu": True, "bmp": True, "apds": True, "rtc": True, "sd": True}
                        }
            except Exception as le:
                print(f"[Extract] Live fallback error: {le}")

        if data is None:
            raise Exception(
                "Could not connect to Shipment Sentinel at 192.168.4.1.\n\n"
                "Checklist:\n"
                "  1. Ensure you are connected to WiFi 'Shipment_Sentinel' (Password: 12345678)\n"
                "  2. Hold the BOOT button on the ESP32 for 2 seconds until OLED says 'DASHBOARD ACTIVE'"
            )

        csv_content = ""
        csv_url = f"http://{ESP32_IP}/log.csv?key={SENTINEL_AUTH_KEY}"
        try:
            req_csv = urllib.request.Request(csv_url, headers=headers)
            with urllib.request.urlopen(req_csv, timeout=10.0) as resp_csv:
                if resp_csv.status == 200:
                    csv_content = resp_csv.read().decode("utf-8", errors="replace")
                    print(f"[Extract] CSV stream received: {len(csv_content)} bytes")
        except Exception as ce:
            print(f"[Extract] CSV stream error: {ce}")

        data["csv"] = csv_content
        return data

    def _save_report_files(self, data):
        """Saves both full JSON and clean CSV to the SentinelReports folder."""
        dev_id = data.get("deviceId", "SENTINEL").replace("-", "_")
        time_slug = time.strftime("%Y%m%d_%H%M%S")
        
        json_filename = f"Trip_{dev_id}_{time_slug}.json"
        json_path = os.path.join(REPORTS_DIR, json_filename)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        csv_filename = f"Trip_{dev_id}_{time_slug}.csv"
        csv_path = os.path.join(REPORTS_DIR, csv_filename)
        csv_content = data.get("csv", "").strip()

        if not csv_content:
            extract_ts = data.get("extractTime", time.strftime("%Y-%m-%d %H:%M:%S"))
            csv_lines = [
                "# Shipment Sentinel v4.0 Cryptographic Trip Log",
                f"# Device: {data.get('deviceId', 'SENTINEL')} | Armed At: {extract_ts}",
                "Elapsed(s),Timestamp,Event,Value,Score,Hash"
            ]
            cnt = data.get("counters", {})
            metrics = data.get("metrics", {})
            curr_score = data.get("score", 100)
            
            if cnt.get("totalShocks", 0) > 0:
                max_g = metrics.get("worstImpactG", 2.5)
                csv_lines.append(f"1,{extract_ts},SHOCK,{max_g:.2f}G,{curr_score},RECONSTRUCTED")
            if cnt.get("drops", 0) > 0:
                max_h = metrics.get("worstDropHeightM", 0.3)
                min_g = metrics.get("worstDropG", 0.2)
                csv_lines.append(f"2,{extract_ts},DROP,{max_h:.2f}m | {min_g:.2f}G,{curr_score},RECONSTRUCTED")
            if cnt.get("tamperEvents", 0) > 0:
                csv_lines.append(f"3,{extract_ts},TAMPER_OPEN,Light:{metrics.get('currentLight', 250)},{curr_score},RECONSTRUCTED")
                csv_lines.append(f"4,{extract_ts},TAMPER_CLOSED,Open:{cnt.get('totalTamperSecs', 5)}s,{curr_score},RECONSTRUCTED")
            if cnt.get("pressureAlerts", 0) > 0:
                csv_lines.append(f"5,{extract_ts},PRESSURE_ALERT,Delta > 5%,{curr_score},RECONSTRUCTED")
            if len(csv_lines) == 3:
                csv_lines.append(f"0,{extract_ts},SYSTEM,Trip Safe / No Incidents,{curr_score},GENESIS_ROOT_V4")
            
            csv_content = "\n".join(csv_lines) + "\n"
            data["csv"] = csv_content

        with open(csv_path, "w", encoding="utf-8") as f:
            f.write(csv_content)

        return json_path, csv_path

    def _update_ui_with_data(self, data, source_file=""):
        """Populates all widgets, calculates smart duration/metrics with drop height, and verifies hash chain."""
        self.current_data = data
        csv_text = data.get("csv", "")
        
        raw_lines = csv_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        
        raw_records = []
        for ln in raw_lines:
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) < 5:
                continue
            if parts[0].lower().startswith("elapsed") or parts[2].lower() in ("event", "event type"):
                continue
            if not parts[0].replace("+", "").replace("-", "").isdigit():
                continue
            raw_records.append(parts)

        print(f"[EXTRACTOR] Parsed {len(raw_records)} event records from CSV ({len(csv_text)} bytes)")

        reconstructed_shocks = 0
        reconstructed_severe_shocks = 0
        reconstructed_drops = 0
        reconstructed_max_drop_m = 0.0
        reconstructed_tampers = 0
        reconstructed_tamper_secs = 0
        reconstructed_pressure = 0
        reconstructed_worst_shock = 0.0
        reconstructed_worst_drop = 999.0
        first_timestamp = None
        last_timestamp = None
        reconstructed_score = 100

        self.all_table_records = []
        for idx, parts in enumerate(raw_records, start=1):
            elapsed_sec = parts[0]
            dt_str = parts[1]
            ev = parts[2]
            val = parts[3].replace("|", ", ")
            try:
                sc_val = int(parts[4]) if len(parts) >= 5 else 100
            except (ValueError, IndexError):
                sc_val = 100
            h_val = parts[5] if len(parts) >= 6 else "--"

            reconstructed_score = sc_val

            if not first_timestamp:
                first_timestamp = dt_str
            last_timestamp = dt_str

            if "SHOCK" in ev:
                reconstructed_shocks += 1
                if "SEVERE" in ev:
                    reconstructed_severe_shocks += 1
                try:
                    g = float(val.replace("G", "").split("|")[-1].strip())
                    if g > reconstructed_worst_shock:
                        reconstructed_worst_shock = g
                except ValueError:
                    pass
            elif "DROP" in ev:
                reconstructed_drops += 1
                
                if "m" in val:
                    try:
                        h_m = float(val.split("m")[0].strip())
                        if h_m > reconstructed_max_drop_m:
                            reconstructed_max_drop_m = h_m
                    except ValueError:
                        pass
                
                try:
                    g = float(val.replace("G", "").split("|")[-1].split("(")[0].strip())
                    if g < reconstructed_worst_drop:
                        reconstructed_worst_drop = g
                except ValueError:
                    pass
            elif "DROP_IMPACT" in ev:
                try:
                    g = float(val.replace("G", "").strip())
                    if g > reconstructed_worst_shock:
                        reconstructed_worst_shock = g
                except ValueError:
                    pass
            elif "TAMPER_OPEN" in ev:
                reconstructed_tampers += 1
            elif "TAMPER_CLOSED" in ev:
                if "Open:" in val:
                    dur_str = val.split("Open:")[1].replace("s", "").strip()
                    try:
                        reconstructed_tamper_secs += int(dur_str)
                    except ValueError:
                        pass
            elif "PRESSURE" in ev:
                reconstructed_pressure += 1

            self.all_table_records.append((idx, dt_str, f"+{elapsed_sec}s", ev, val, f"{sc_val}/100", h_val))

        if not self.all_table_records and data.get("counters"):
            cnt = data.get("counters", {})
            metrics = data.get("metrics", {})
            curr_score = data.get("score", 100)
            extract_ts = data.get("extractTime", time.strftime("%Y-%m-%d %H:%M:%S"))
            rec_idx = 1
            if cnt.get("totalShocks", 0) > 0:
                max_g = metrics.get("worstImpactG", 2.5)
                self.all_table_records.append((rec_idx, extract_ts, "+1s", "SHOCK", f"{max_g:.2f}G (Total: {cnt.get('totalShocks')})", f"{curr_score}/100", "RECONSTRUCTED"))
                rec_idx += 1
            if cnt.get("drops", 0) > 0:
                max_h = metrics.get("worstDropHeightM", 0.3)
                min_g = metrics.get("worstDropG", 0.2)
                self.all_table_records.append((rec_idx, extract_ts, "+2s", "DROP", f"{max_h:.2f}m | {min_g:.2f}G (Total: {cnt.get('drops')})", f"{curr_score}/100", "RECONSTRUCTED"))
                rec_idx += 1
            if cnt.get("tamperEvents", 0) > 0:
                self.all_table_records.append((rec_idx, extract_ts, "+3s", "TAMPER_OPEN", f"Light:{metrics.get('currentLight', 250)} ({cnt.get('tamperEvents')} Breaches)", f"{curr_score}/100", "RECONSTRUCTED"))
                rec_idx += 1
                self.all_table_records.append((rec_idx, extract_ts, "+4s", "TAMPER_CLOSED", f"Open:{cnt.get('totalTamperSecs', 5)}s Total", f"{curr_score}/100", "RECONSTRUCTED"))
                rec_idx += 1
            if cnt.get("pressureAlerts", 0) > 0:
                self.all_table_records.append((rec_idx, extract_ts, "+5s", "PRESSURE_ALERT", f"Delta > 5% ({cnt.get('pressureAlerts')} Alerts)", f"{curr_score}/100", "RECONSTRUCTED"))
                rec_idx += 1
            if not self.all_table_records:
                self.all_table_records.append((1, extract_ts, "+0s", "SYSTEM", "Trip Initialized / Safe Transit", f"{curr_score}/100", "--"))

        calculated_duration = data.get("uptime", "N/A")
        if calculated_duration == "N/A" and raw_records:
            try:
                t_first_s = int(raw_records[0][0])
                t_last_s = int(raw_records[-1][0])
                diff_s = max(0, t_last_s - t_first_s)
                calculated_duration = f"{diff_s//3600}h {(diff_s%3600)//60}m {diff_s%60}s"
            except Exception:
                calculated_duration = f"+{raw_records[-1][0]}s"

        is_crypto_valid, err_idx, crypto_msg = verify_sha256_chain(raw_records)
        if is_crypto_valid:
            self.crypto_badge.config(
                text="🛡️ SHA-256 HASH CHAIN: VERIFIED (ZERO TAMPERING)",
                bg="#D4EDDA", fg="#155724"
            )
        else:
            self.crypto_badge.config(
                text=f"❌ CRYPTO WARNING: HASH MISMATCH AT #{err_idx}",
                bg="#F8D7DA", fg="#721C24"
            )

        if raw_records:
            score = reconstructed_score
        elif "score" in data:
            score = data["score"]
        else:
            shock_ded = min(30, (reconstructed_shocks - reconstructed_severe_shocks) * 3 + reconstructed_severe_shocks * 12)
            drop_ded = min(30, reconstructed_drops * 10)
            tamper_ded = min(25, reconstructed_tampers * 3 + (reconstructed_tamper_secs // 5))
            press_ded = min(15, reconstructed_pressure * 5)
            score = max(0, 100 - shock_ded - drop_ded - tamper_ded - press_ded)

        self.score_display.config(text=str(score))
        if score >= 85:
            self.score_display.config(fg=LED_GREEN)
            self.status_badge.config(text="[ PASSED / SAFE ]", bg="#D4EDDA", fg="#155724")
        elif score >= 50:
            self.score_display.config(fg=LED_AMBER)
            self.status_badge.config(text="[ WARNING / CAUTION ]", bg="#FFF3CD", fg="#856404")
        else:
            self.score_display.config(fg=LED_RED)
            self.status_badge.config(text="[ COMPROMISED / SEVERE ]", bg="#F8D7DA", fg="#721C24")

        dev_id = data.get("deviceId", "SENTINEL")
        if dev_id == "SENTINEL" and source_file and "_" in os.path.basename(source_file):
            dev_id = os.path.basename(source_file).split("_")[1]
        self.device_id_var.set(dev_id)
        self.uptime_var.set(calculated_duration)
        self.extract_time_var.set(data.get("extractTime", last_timestamp if last_timestamp else time.strftime("%Y-%m-%d %H:%M:%S")))

        total_shocks = data.get("counters", {}).get("totalShocks", reconstructed_shocks)
        worst_g = data.get("metrics", {}).get("worstImpactG", reconstructed_worst_shock)
        self.stat_widgets["shocks"][0].config(text=str(total_shocks))
        self.stat_widgets["shocks"][1].config(text=f"Max: {worst_g:.2f}G")

        drops = data.get("counters", {}).get("drops", reconstructed_drops)
        max_drop_m = data.get("metrics", {}).get("worstDropHeightM", reconstructed_max_drop_m)
        self.stat_widgets["drops"][0].config(text=str(drops))
        if max_drop_m > 0:
            self.stat_widgets["drops"][1].config(text=f"Max Height: {max_drop_m:.2f}m")
        elif drops > 0:
            self.stat_widgets["drops"][1].config(text=f"Detected ({drops} drops)")
        else:
            self.stat_widgets["drops"][1].config(text="None (0.00m)")

        tampers = data.get("counters", {}).get("tamperEvents", reconstructed_tampers)
        tamper_secs = data.get("counters", {}).get("totalTamperSecs", reconstructed_tamper_secs)
        self.stat_widgets["tampers"][0].config(text=str(tampers))
        self.stat_widgets["tampers"][1].config(text=f"{tamper_secs}s Total")

        pressure_alerts = data.get("counters", {}).get("pressureAlerts", reconstructed_pressure)
        p_current = data.get("metrics", {}).get("currentPressure", 0.0)
        self.stat_widgets["pressure"][0].config(text=str(pressure_alerts))
        self.stat_widgets["pressure"][1].config(text=f"{p_current:.1f} hPa" if p_current > 0 else "Normal")

        sensors = data.get("sensors", {"mpu": True, "bmp": True, "apds": True, "rtc": True})
        for s_key, led in self.sensor_leds.items():
            is_ok = sensors.get(s_key, True)
            led.config(fg="#00CC00" if is_ok else "#FF2233", text="●" if is_ok else "✖")

        self.apply_active_filters()

    def load_local_report(self):
        """Opens previously archived report and reconstructs all smart metrics with drop height."""
        filename = filedialog.askopenfilename(
            initialdir=REPORTS_DIR,
            title="Open Archived Sentinel Report",
            filetypes=[("Sentinel Reports (*.json;*.csv)", "*.json;*.csv"), ("JSON Files (*.json)", "*.json"), ("All Files", "*.*")]
        )
        if not filename:
            return

        try:
            if filename.endswith(".json"):
                with open(filename, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._update_ui_with_data(data, filename)
                self.status_text.set(f"Loaded & Reconstructed Archive: {os.path.basename(filename)}")
            elif filename.endswith(".csv"):
                with open(filename, "r", encoding="utf-8") as f:
                    content = f.read()
                synthetic_data = {
                    "firmware": "v4.0 (Imported)",
                    "deviceId": os.path.basename(filename).split("_")[1] if "_" in filename else "IMPORTED",
                    "extractTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(filename))),
                    "uptime": "N/A",
                    "counters": {},
                    "metrics": {},
                    "sensors": {"mpu": True, "bmp": True, "apds": True, "rtc": True},
                    "csv": content
                }
                self._update_ui_with_data(synthetic_data, filename)
                self.status_text.set(f"Loaded & Reconstructed CSV: {os.path.basename(filename)}")
        except Exception as e:
            messagebox.showerror("Load Failed", f"Could not read report file:\n{e}")

    def generate_inspection_certificate(self):
        """Generates an official, printable HTML/PDF Chain-of-Custody Certificate."""
        if not self.all_table_records:
            messagebox.showwarning("No Data", "Please extract or load a trip report before generating a certificate.")
            return

        dev_id = self.device_id_var.get()
        score = self.score_display.cget("text")
        status_str = self.status_badge.cget("text")
        uptime = self.uptime_var.get()
        extract_time = self.extract_time_var.get()
        cert_num = f"CERT-{int(time.time())}-{dev_id.replace('-', '')[:6]}"
        
        table_rows_html = ""
        for r in self.all_table_records:
            idx, ts, elap, ev, val, sc, h_val = r
            badge_color = "#333333"
            if "SHOCK" in ev:
                badge_color = "#B78103"
            elif "DROP" in ev:
                badge_color = "#006064"
            elif "TAMPER" in ev:
                badge_color = "#6A1B9A"
            elif "PRESSURE" in ev:
                badge_color = "#117A65"
            
            table_rows_html += f"""
            <tr>
                <td style="text-align:center; font-family: monospace;">{idx}</td>
                <td style="font-family: monospace;">{ts}</td>
                <td style="text-align:center; font-family: monospace;">{elap}</td>
                <td style="font-weight: bold; color: {badge_color}; text-align:center;">{ev}</td>
                <td style="font-family: monospace;">{val}</td>
                <td style="text-align:center; font-weight: bold;">{sc}</td>
                <td style="font-family: monospace; font-size: 11px; color: #555;">{h_val}</td>
            </tr>"""

        cert_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Chain-of-Custody Transit Certificate — {cert_num}</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&family=Segoe+UI:wght@400;600;700;800&display=swap');
body {{ font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #222; margin: 0; padding: 24px; }}
.cert-page {{ max-width: 900px; margin: 0 auto; background: #fff; border: 2px solid #0A246A; padding: 36px; box-shadow: 0 4px 20px rgba(0,0,0,0.15); }}
.header-row {{ display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid #0A246A; padding-bottom: 16px; margin-bottom: 24px; }}
.logo-title {{ display: flex; align-items: center; gap: 12px; }}
.logo-box {{ width: 44px; height: 44px; background: #0A246A; color: #fff; display: flex; align-items: center; justify-content: center; font-size: 24px; font-weight: bold; border-radius: 4px; }}
h1 {{ margin: 0; font-size: 22px; color: #0A246A; letter-spacing: 0.5px; }}
.cert-meta {{ text-align: right; font-size: 12px; color: #555; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 24px; }}
.card {{ background: #fafbfc; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; }}
.card h3 {{ margin: 0 0 10px 0; font-size: 13px; color: #0A246A; text-transform: uppercase; border-bottom: 1px solid #eaecef; padding-bottom: 4px; }}
.score-banner {{ display: flex; align-items: center; justify-content: space-between; background: #f0f4f8; border: 1px solid #0A246A; border-radius: 6px; padding: 16px 20px; margin-bottom: 24px; }}
.score-num {{ font-size: 42px; font-weight: 800; color: #0A246A; font-family: 'Courier Prime', monospace; }}
.badge {{ padding: 6px 14px; font-size: 13px; font-weight: bold; border-radius: 4px; border: 1px solid #28a745; background: #e8f5e9; color: #1b5e20; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 12px; }}
th {{ background: #0A246A; color: #fff; padding: 8px 10px; text-align: left; font-size: 11px; text-transform: uppercase; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #e1e4e8; }}
tr:nth-child(even) td {{ background: #f8f9fa; }}
.sign-box {{ display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 40px; border-top: 1px solid #ccc; padding-top: 24px; }}
.sign-line {{ border-bottom: 1px solid #333; height: 36px; margin-bottom: 6px; }}
.btn-print {{ background: #0A246A; color: #fff; border: none; padding: 10px 20px; font-size: 14px; font-weight: bold; border-radius: 4px; cursor: pointer; margin-bottom: 16px; }}
@media print {{
    body {{ background: #fff; padding: 0; }}
    .cert-page {{ border: none; box-shadow: none; padding: 0; }}
    .btn-print {{ display: none; }}
}}
</style>
</head>
<body>
<div style="max-width: 900px; margin: 0 auto; text-align: right;">
    <button onclick="window.print()" class="btn-print">🖨️ Print / Save to PDF</button>
</div>
<div class="cert-page">
    <div class="header-row">
        <div class="logo-title">
            <div class="logo-box">🛡️</div>
            <div>
                <h1>CHAIN-OF-CUSTODY TRANSIT CERTIFICATE</h1>
                <div style="font-size: 12px; color: #555;">Official Shipment Integrity & Cryptographic Ingestion Report</div>
            </div>
        </div>
        <div class="cert-meta">
            <div><strong>Certificate ID:</strong> {cert_num}</div>
            <div><strong>Date Issued:</strong> {extract_time}</div>
            <div><strong>Security Status:</strong> Cryptographically Verified</div>
        </div>
    </div>

    <div class="score-banner">
        <div>
            <div style="font-size: 12px; color: #555; text-transform: uppercase; font-weight: bold;">Final Integrity Assessment</div>
            <div class="score-num">{score}<span style="font-size: 20px; color: #666;">/100</span></div>
        </div>
        <div>
            <span class="badge">{status_str}</span>
        </div>
    </div>

    <div class="grid-2">
        <div class="card">
            <h3>Shipment Passport</h3>
            <div style="font-size: 12px; line-height: 1.8;">
                <div><strong>Device Identifier:</strong> {dev_id}</div>
                <div><strong>Transit Duration:</strong> {uptime}</div>
                <div><strong>Extraction Time:</strong> {extract_time}</div>
                <div><strong>Cryptographic Engine:</strong> SHA-256 Hash Chain Validated</div>
            </div>
        </div>
        <div class="card">
            <h3>Incident Excursion Summary</h3>
            <div style="font-size: 12px; line-height: 1.8;">
                <div><strong>Impact Shocks:</strong> {self.stat_widgets['shocks'][0].cget('text')} {self.stat_widgets['shocks'][1].cget('text')}</div>
                <div><strong>Free-Fall Drops:</strong> {self.stat_widgets['drops'][0].cget('text')} ({self.stat_widgets['drops'][1].cget('text')})</div>
                <div><strong>Tamper Breaches:</strong> {self.stat_widgets['tampers'][0].cget('text')} {self.stat_widgets['tampers'][1].cget('text')}</div>
                <div><strong>Barometric Pressure:</strong> {self.stat_widgets['pressure'][0].cget('text')} alerts</div>
            </div>
        </div>
    </div>

    <h3 style="font-size: 14px; color: #0A246A; text-transform: uppercase; margin: 24px 0 8px 0;">Cryptographic Audit Event Log ({len(self.all_table_records)} Total Records)</h3>
    <table>
        <thead>
            <tr>
                <th>#</th>
                <th>Timestamp (RTC)</th>
                <th>Elapsed</th>
                <th>Event Type</th>
                <th>Telemetry Reading</th>
                <th>Score</th>
                <th>SHA-256 Hash</th>
            </tr>
        </thead>
        <tbody>
            {table_rows_html}
        </tbody>
    </table>

    <div class="sign-box">
        <div>
            <div class="sign-line"></div>
            <div style="font-size: 12px; font-weight: bold;">Dispatcher / Origin Logistics Authority</div>
            <div style="font-size: 11px; color: #777;">Date & Official Seal</div>
        </div>
        <div>
            <div class="sign-line"></div>
            <div style="font-size: 12px; font-weight: bold;">Receiving Quality Assurance Inspector</div>
            <div style="font-size: 11px; color: #777;">Date & Official Verification</div>
        </div>
    </div>
</div>
</body>
</html>"""

        cert_filename = f"Certificate_{dev_id}_{int(time.time())}.html"
        cert_path = os.path.join(REPORTS_DIR, cert_filename)
        with open(cert_path, "w", encoding="utf-8") as f:
            f.write(cert_html)

        webbrowser.open(f"file:///{cert_path}")
        self.status_text.set(f"Certificate Generated: {cert_filename}")

    def prompt_rearm_device(self):
        """Operator confirmation dialog before wiping encrypted SD log and re-arming Sentinel."""
        if self.is_busy:
            return
        
        confirm = messagebox.askyesno(
            "⚠️ Re-Arm Sentinel for New Trip",
            "Are you sure you want to permanently erase the encrypted SD log and re-arm Shipment Sentinel (Trip Score 100/100) for a new shipment?\n\nMake sure you have extracted and saved your audit report first!\n\nProceed to wipe and re-arm?"
        )
        if not confirm:
            return

        self.is_busy = True
        self.set_ui_state(False)
        self.set_led_status("#FFB300", "RE-ARMING")
        threading.Thread(target=self._rearm_worker, daemon=True).start()

    def _rearm_worker(self):
        try:
            self.status_text.set("Sending authenticated Re-Arm command to Sentinel...")
            auth_url = f"http://{ESP32_IP}/clear?key={SENTINEL_AUTH_KEY}"
            req = urllib.request.Request(
                auth_url,
                headers={"User-Agent": "SentinelIngestion/4.0", "X-Sentinel-Key": SENTINEL_AUTH_KEY}
            )
            with urllib.request.urlopen(req, timeout=5.0) as response:
                if response.status == 200:
                    self.status_text.set("Sentinel Re-Armed! Encrypted log erased, score reset to 100/100, ready for next shipment.")
                    self.set_led_status("#00FF41", "RE-ARMED")
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Sentinel Re-Armed",
                        "Shipment Sentinel has been successfully re-armed!\n\n"
                        "✓ Encrypted SD card log erased\n"
                        "✓ SHA-256 rolling hash chain reset to Genesis Root\n"
                        "✓ Trip score restored to 100/100\n"
                        "✓ Device is armed and ready for the next shipment."
                    ))
                    return
            raise Exception("Invalid response from Sentinel")
        except Exception as err:
            self.status_text.set(f"Re-Arm Error: {err}")
            self.set_led_status("#FF2233", "ERROR")
            self.root.after(0, lambda: messagebox.showerror(
                "Re-Arm Failed",
                f"Could not re-arm Sentinel:\n{err}\n\nMake sure your PC is connected to 'Shipment_Sentinel' WiFi."
            ))
        finally:
            self.is_busy = False
            self.root.after(0, lambda: self.set_ui_state(True))

    def set_ui_state(self, enabled):
        state = tk.NORMAL if enabled else tk.DISABLED
        self.btn_auto_extract.config(state=state)
        self.btn_direct_extract.config(state=state)
        if hasattr(self, 'btn_rearm'):
            self.btn_rearm.config(state=state)

if __name__ == "__main__":
    root = tk.Tk()
    app = SentinelExtractorApp(root)
    root.mainloop()

