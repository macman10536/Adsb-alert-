#!/usr/bin/env python3
import json, time, math, os, socket, threading, subprocess
import tkinter as tk
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from collections import deque

AIRCRAFT_JSON       = "/run/readsb/aircraft.json"
RING_CAUTION_MI     = 3.0
RING_WARN_MI        = 1.0
RING_DANGER_MI      = 0.4
MAX_ALT_FT          = 1000
HEADING_WINDOW_DEG  = 45
MIN_CLOSING_MPH     = 5
WARN_COOLDOWN_SEC   = 25
DANGER_COOLDOWN_SEC = 4
ORBIT_COOLDOWN_SEC  = 60
WATCH_COOLDOWN_SEC  = 1800
SAMPLE_SEC          = 1.0
FIELD_ELEV_FT       = 0
GPS_DEVICE          = "/dev/ttyAMA0"
ORBIT_HEADING_THRESHOLD = 270
ORBIT_TIME_WINDOW   = 120
WATCHLIST_FILE      = os.path.expanduser("~/.adsb_watchlist")
LOG_MAX_LINES       = 500

DB_CANDIDATES = [
    "/usr/share/tar1090/html/db/aircraft.json",
    "/usr/local/share/tar1090/html/db/aircraft.json",
]

C = {
    "bg":            "#060a06",
    "panel":         "#080d08",
    "border":        "#0f2010",
    "border_bright": "#1a3a1c",
    "text":          "#a0c8a0",
    "text_dim":      "#2a4a2c",
    "text_bright":   "#d0f0d0",
    "green":         "#00e676",
    "green_dim":     "#003010",
    "green_radar":   "#00ff41",
    "green_ring":    "#0a3a0c",
    "green_sweep":   "#00ff41",
    "yellow":        "#ffd600",
    "yellow_dim":    "#2a2000",
    "orange":        "#ff6d00",
    "orange_dim":    "#2a1000",
    "red":           "#ff1744",
    "red_dim":       "#2a0008",
    "red_bright":    "#ff5252",
    "cyan":          "#00e5ff",
    "blue":          "#2979ff",
    "blue_dim":      "#0a1a3a",
    "grey":          "#1a2a1c",
}

COMPASS_POINTS = [
    "NORTH", "NORTH-NORTHEAST", "NORTHEAST", "EAST-NORTHEAST",
    "EAST", "EAST-SOUTHEAST", "SOUTHEAST", "SOUTH-SOUTHEAST",
    "SOUTH", "SOUTH-SOUTHWEST", "SOUTHWEST", "WEST-SOUTHWEST",
    "WEST", "WEST-NORTHWEST", "NORTHWEST", "NORTH-NORTHWEST"
]

def bearing_to_compass(bearing):
    idx = int((bearing + 11.25) / 22.5) % 16
    return COMPASS_POINTS[idx]

def fix_gps_setup():
    try:
        subprocess.run(["sudo", "/bin/chmod", "666", GPS_DEVICE],
                       check=True, timeout=5,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    try:
        subprocess.run(["sudo", "/usr/bin/systemctl", "restart", "gpsd"],
                       check=True, timeout=10,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    time.sleep(2)

def load_watchlist():
    try:
        with open(WATCHLIST_FILE) as f:
            entries = [line.strip().upper() for line in f if line.strip()]
            return entries[:10]
    except Exception:
        return []

def save_watchlist(entries):
    try:
        with open(WATCHLIST_FILE, "w") as f:
            for e in entries:
                f.write(e + "\n")
    except Exception:
        pass

def matches_watchlist(ident_set, watchlist):
    for watch in watchlist:
        for ident in ident_set:
            if ident.endswith(watch) or ident == watch:
                return True
    return False

@dataclass
class Aircraft:
    hexid: str
    lat: float
    lon: float
    alt_ft: int
    dist_mi: float
    track: Optional[float]
    speed_kts: Optional[float]
    flight: str
    tail: Optional[str]
    closing_mph: Optional[float]
    eta_1mi_sec: Optional[float]
    threat_level: int
    bearing_from_me: float
    is_orbiting: bool = False
    is_watched: bool = False

    @property
    def ident(self):
        if self.tail:
            return self.tail + (f" {self.flight}" if self.flight else "")
        return self.flight or self.hexid.upper()

    @property
    def alt_agl(self):
        return self.alt_ft - FIELD_ELEV_FT

    @property
    def eta_str(self):
        if self.eta_1mi_sec is None:
            return "ETA ---"
        if self.eta_1mi_sec < 60:
            return f"ETA {int(self.eta_1mi_sec)}s"
        return f"ETA {self.eta_1mi_sec/60:.1f}m"

    @property
    def closing_str(self):
        if self.closing_mph is None or self.closing_mph <= 0:
            return ""
        return f"{self.closing_mph:.0f}mph"
class OrbitTracker:
    def __init__(self):
        self._history = {}

    def update(self, hexid, track, now):
        if track is None:
            return False
        if hexid not in self._history:
            self._history[hexid] = deque()
        self._history[hexid].append((now, float(track)))
        cutoff = now - ORBIT_TIME_WINDOW
        while self._history[hexid] and self._history[hexid][0][0] < cutoff:
            self._history[hexid].popleft()
        entries = self._history[hexid]
        if len(entries) < 6:
            return False
        total_turn = 0.0
        prev_heading = entries[0][1]
        for _, heading in list(entries)[1:]:
            diff = (heading - prev_heading + 180) % 360 - 180
            total_turn += diff
            prev_heading = heading
        return abs(total_turn) >= ORBIT_HEADING_THRESHOLD

    def cleanup(self, active_hexids):
        for hexid in list(self._history.keys()):
            if hexid not in active_hexids:
                del self._history[hexid]


class AudioEngine:
    def __init__(self):
        self._lock = threading.Lock()

    def _run(self, cmd):
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def speak(self, text):
        def _do():
            with self._lock:
                try:
                    self._run(["espeak-ng", "-s", "150", "-p", "45", "-a", "200", text])
                    time.sleep(0.1)
                except FileNotFoundError:
                    pass
        threading.Thread(target=_do, daemon=True).start()

    def _try_beep(self, freq, dur_ms):
        import wave, struct, tempfile
        sr = 22050
        samples = int(sr * dur_ms / 1000)
        amp = 28000
        data = [int(amp * math.sin(2 * math.pi * freq * i / sr)) for i in range(samples)]
        fade = min(200, samples // 4)
        for i in range(fade):
            data[i] = int(data[i] * i / fade)
            data[-(i+1)] = int(data[-(i+1)] * i / fade)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            path = tmp.name
            with wave.open(tmp, 'w') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(struct.pack(f'<{samples}h', *data))
        try:
            self._run(["paplay", path])
        except FileNotFoundError:
            self._run(["aplay", "-q", path])
        threading.Thread(target=lambda: (time.sleep(2), os.unlink(path)), daemon=True).start()

    def beep(self, freq=880, dur_ms=120, count=1, gap_ms=80):
        def _do():
            for i in range(count):
                self._try_beep(freq, dur_ms)
                if i < count - 1:
                    time.sleep(gap_ms / 1000)
        threading.Thread(target=_do, daemon=True).start()

    def caution_tone(self):
        self.beep(freq=660, dur_ms=180, count=1)

    def warning_tone(self):
        self.beep(freq=880, dur_ms=150, count=2, gap_ms=100)

    def danger_tone(self):
        self.beep(freq=1100, dur_ms=120, count=3, gap_ms=70)

    def orbit_tone(self):
        self.beep(freq=520, dur_ms=300, count=2, gap_ms=200)

    def watch_tone(self):
        self.beep(freq=750, dur_ms=200, count=2, gap_ms=150)


class RadarWidget(tk.Canvas):
    def __init__(self, parent, size=240, on_select=None, **kw):
        super().__init__(parent, width=size, height=size,
                         bg=C["bg"], highlightthickness=1,
                         highlightbackground=C["border_bright"], **kw)
        self.size = size
        self.cx = size // 2
        self.cy = size // 2
        self.r = (size // 2) - 14
        self._sweep_angle = 0
        self._all_aircraft = []
        self._selected_hexid = None
        self._on_select = on_select
        self._blink_state = False
        self._draw_static()
        self._animate_sweep()
        self._animate_blink()
        self.bind("<Button-1>", self._on_click)

    def _draw_static(self):
        self.delete("static")
        outer = RING_CAUTION_MI
        for dist, label in [
            (outer, f"{outer:.0f}mi"),
            (RING_WARN_MI, f"{RING_WARN_MI:.1f}mi"),
            (RING_DANGER_MI, f"{RING_DANGER_MI:.1f}mi"),
        ]:
            frac = min(dist / outer, 1.0)
            r = int(self.r * frac)
            color = C["border_bright"] if dist == outer else C["green_ring"]
            self.create_oval(self.cx-r, self.cy-r, self.cx+r, self.cy+r,
                             outline=color, width=1, tags="static")
            self.create_text(self.cx + r - 3, self.cy - 7,
                             text=label, fill=C["text_dim"],
                             font=("Courier", 8), anchor="e", tags="static")
        for angle, label in [(0, "N"), (90, "E"), (180, "S"), (270, "W")]:
            rad = math.radians(angle - 90)
            x = self.cx + self.r * math.cos(rad)
            y = self.cy + self.r * math.sin(rad)
            self.create_line(self.cx, self.cy, x, y,
                             fill=C["green_ring"], dash=(3, 8), tags="static")
            lx = self.cx + (self.r + 10) * math.cos(rad)
            ly = self.cy + (self.r + 10) * math.sin(rad)
            fn = ("Courier", 9, "bold") if label == "N" else ("Courier", 8)
            fc = C["green_radar"] if label == "N" else C["text_dim"]
            self.create_text(lx, ly, text=label, fill=fc, font=fn, tags="static")

    def _animate_sweep(self):
        self.delete("sweep")
        cx, cy, r = self.cx, self.cy, self.r
        num_trails = 30
        for i in range(num_trails):
            trail_angle = self._sweep_angle - (i * 2.5)
            trail_rad = math.radians(trail_angle - 90)
            alpha = 1.0 - (i / num_trails)
            green_val = int(180 * alpha)
            color = f"#00{green_val:02x}00" if green_val > 15 else C["bg"]
            ex = cx + r * math.cos(trail_rad)
            ey = cy + r * math.sin(trail_rad)
            width = max(1, int(3 * alpha))
            self.create_line(cx, cy, ex, ey, fill=color, width=width, tags="sweep")
        angle_rad = math.radians(self._sweep_angle - 90)
        ex = cx + r * math.cos(angle_rad)
        ey = cy + r * math.sin(angle_rad)
        self.create_line(cx, cy, ex, ey, fill=C["green_sweep"], width=2, tags="sweep")
        self._sweep_angle = (self._sweep_angle + 2) % 360
        self.after(80, self._animate_sweep)

    def _animate_blink(self):
        self._blink_state = not self._blink_state
        self._redraw_aircraft()
        self.after(500, self._animate_blink)

    def _ac_screen_pos(self, ac):
        angle_rad = math.radians(ac.bearing_from_me - 90)
        frac = min(ac.dist_mi / RING_CAUTION_MI, 1.0)
        px = self.cx + self.r * frac * math.cos(angle_rad)
        py = self.cy + self.r * frac * math.sin(angle_rad)
        return px, py

    def _on_click(self, event):
        best = None
        best_dist = 18
        for ac in self._all_aircraft:
            px, py = self._ac_screen_pos(ac)
            d = math.hypot(event.x - px, event.y - py)
            if d < best_dist:
                best_dist = d
                best = ac
        self._selected_hexid = best.hexid if best else None
        if self._on_select:
            self._on_select(best)

    def update_aircraft(self, threats, safe):
        self._all_aircraft = threats + safe
        self._redraw_aircraft()

    def _redraw_aircraft(self):
        self.delete("dynamic")
        self.create_oval(self.cx-5, self.cy-5, self.cx+5, self.cy+5,
                         fill=C["green_radar"], outline=C["green_radar"], tags="dynamic")
        self.create_text(self.cx, self.cy+14, text="YOU",
                         fill=C["green_radar"], font=("Courier", 8, "bold"), tags="dynamic")
        for ac in self._all_aircraft:
            px, py = self._ac_screen_pos(ac)
            is_sel = ac.hexid == self._selected_hexid
            if ac.is_watched:
                color = C["red"] if self._blink_state else C["red_dim"]
                size = 7 if is_sel else 5
                self.create_oval(px-size, py-size, px+size, py+size,
                                 fill=color, outline="white" if is_sel else "",
                                 width=2 if is_sel else 0, tags="dynamic")
                self.create_text(px+8, py-10, text=ac.ident[:10],
                                 fill=color, font=("Courier", 8, "bold"),
                                 anchor="w", tags="dynamic")
            elif ac.threat_level > 0:
                color = [C["yellow"], C["orange"], C["red"]][ac.threat_level - 1]
                size = 8 if is_sel else 6
                self.create_oval(px-size, py-size, px+size, py+size,
                                 fill=color, outline="white" if is_sel else "",
                                 width=2 if is_sel else 0, tags="dynamic")
                if ac.track is not None:
                    tr = math.radians(ac.track - 90)
                    ax = px + 16 * math.cos(tr)
                    ay = py + 16 * math.sin(tr)
                    self.create_line(px, py, ax, ay, fill=color,
                                     width=2, arrow=tk.LAST, arrowshape=(7, 9, 3),
                                     tags="dynamic")
                self.create_text(px+8, py-10, text=ac.ident[:10],
                                 fill=color, font=("Courier", 8, "bold"),
                                 anchor="w", tags="dynamic")
            else:
                color = C["cyan"] if ac.is_orbiting else C["blue"]
                size = 6 if is_sel else 4
                self.create_oval(px-size, py-size, px+size, py+size,
                                 fill=color, outline="white" if is_sel else "",
                                 width=2 if is_sel else 0, tags="dynamic")
                if ac.track is not None:
                    tr = math.radians(ac.track - 90)
                    ax = px + 12 * math.cos(tr)
                    ay = py + 12 * math.sin(tr)
                    self.create_line(px, py, ax, ay, fill=color,
                                     width=1, arrow=tk.LAST, arrowshape=(5, 7, 2),
                                     tags="dynamic")
class AlertBanner(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.lbl = tk.Label(self,
                            text="  MONITORING  --  NO THREATS DETECTED  ",
                            font=("Courier New", 16, "bold"),
                            bg=C["green_dim"], fg=C["green"], pady=8)
        self.lbl.pack(fill="x")
        self._blink_state = False
        self._blink_job = None

    def set_clear(self):
        self._stop_blink()
        self.lbl.config(text="  MONITORING  --  NO THREATS DETECTED  ",
                        bg=C["green_dim"], fg=C["green"])

    def set_caution(self, msg):
        self._stop_blink()
        self.lbl.config(text=f"  CAUTION  --  {msg}  ",
                        bg=C["yellow_dim"], fg=C["yellow"])

    def set_warning(self, msg):
        self._stop_blink()
        self.lbl.config(text=f"  WARNING  --  {msg}  ",
                        bg=C["orange_dim"], fg=C["orange"])

    def set_danger(self, msg):
        self._start_blink(msg)

    def set_orbit(self, msg):
        self._stop_blink()
        self.lbl.config(text=f"  SKY CIRCLE  --  {msg}  ",
                        bg=C["blue_dim"], fg=C["cyan"])

    def set_watch(self, msg):
        self._stop_blink()
        self.lbl.config(text=f"  WATCHLIST  --  {msg}  ",
                        bg=C["red_dim"], fg=C["red_bright"])

    def _start_blink(self, msg):
        self._stop_blink()
        self._blink_msg = msg
        self._blink()

    def _blink(self):
        if self._blink_state:
            self.lbl.config(text=f"  DANGER  --  {self._blink_msg}  ",
                            bg=C["red"], fg=C["bg"])
        else:
            self.lbl.config(text=f"  DANGER  --  {self._blink_msg}  ",
                            bg=C["red_dim"], fg=C["red_bright"])
        self._blink_state = not self._blink_state
        self._blink_job = self.after(400, self._blink)

    def _stop_blink(self):
        if self._blink_job:
            self.after_cancel(self._blink_job)
            self._blink_job = None


class ThreatCard(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["panel"], **kw)
        self._build()

    def _build(self):
        self.level_bar = tk.Frame(self, width=6, bg=C["grey"])
        self.level_bar.pack(side="left", fill="y")
        body = tk.Frame(self, bg=C["panel"])
        body.pack(side="left", fill="both", expand=True, padx=10, pady=4)
        self.lbl_ident = tk.Label(body, text="", bg=C["panel"],
                                  fg=C["text_bright"], font=("Courier New", 18, "bold"))
        self.lbl_ident.pack(anchor="w")
        row2 = tk.Frame(body, bg=C["panel"])
        row2.pack(anchor="w", fill="x")
        self.lbl_dist = tk.Label(row2, text="", bg=C["panel"],
                                 fg=C["yellow"], font=("Courier New", 26, "bold"))
        self.lbl_dist.pack(side="left")
        self.lbl_eta = tk.Label(row2, text="", bg=C["panel"],
                                fg=C["cyan"], font=("Courier New", 17))
        self.lbl_eta.pack(side="left", padx=14)
        row3 = tk.Frame(body, bg=C["panel"])
        row3.pack(anchor="w")
        self.lbl_alt = tk.Label(row3, text="", bg=C["panel"],
                                fg=C["text"], font=("Courier New", 15))
        self.lbl_alt.pack(side="left")
        self.lbl_closing = tk.Label(row3, text="", bg=C["panel"],
                                    fg=C["orange"], font=("Courier New", 15, "bold"))
        self.lbl_closing.pack(side="left", padx=10)

    def update(self, ac):
        if ac.is_watched and ac.threat_level == 0:
            color = C["red_bright"]
        elif ac.is_orbiting and ac.threat_level == 0:
            color = C["cyan"]
        else:
            color = [C["yellow"], C["orange"], C["red"]][min(ac.threat_level, 2)]
        self.level_bar.config(bg=color)
        tags = []
        if ac.is_orbiting:
            tags.append("[ORBIT]")
        if ac.is_watched:
            tags.append("[WATCH]")
        tag_str = " ".join(tags)
        self.lbl_ident.config(text=ac.ident + (f" {tag_str}" if tag_str else ""))
        self.lbl_dist.config(text=f"{ac.dist_mi:.2f} mi", fg=color)
        self.lbl_eta.config(text=ac.eta_str)
        self.lbl_alt.config(text=f"{ac.alt_ft}ft MSL  ({ac.alt_agl:+d}ft AGL)")
        self.lbl_closing.config(text=ac.closing_str)

    def clear(self):
        self.level_bar.config(bg=C["grey"])
        for lbl in [self.lbl_ident, self.lbl_dist, self.lbl_eta,
                    self.lbl_alt, self.lbl_closing]:
            lbl.config(text="")


class SelectedPanel(tk.Frame):
    def __init__(self, parent, **kw):
        super().__init__(parent, bg=C["panel"],
                         highlightbackground=C["border_bright"],
                         highlightthickness=1, **kw)
        tk.Label(self, text="SELECTED", bg=C["panel"],
                 fg=C["text_dim"], font=("Courier New", 9)).pack(anchor="w", padx=6, pady=(3, 0))
        tk.Frame(self, bg=C["border_bright"], height=1).pack(fill="x")
        body = tk.Frame(self, bg=C["panel"])
        body.pack(fill="both", expand=True, padx=6, pady=4)
        self.lbl_ident = tk.Label(body, text="--", bg=C["panel"],
                                  fg=C["text_bright"], font=("Courier New", 12, "bold"))
        self.lbl_ident.pack(anchor="w")
        self.lbl_dist = tk.Label(body, text="", bg=C["panel"],
                                 fg=C["green_radar"], font=("Courier New", 11))
        self.lbl_dist.pack(anchor="w")
        self.lbl_alt = tk.Label(body, text="", bg=C["panel"],
                                fg=C["text"], font=("Courier New", 10))
        self.lbl_alt.pack(anchor="w")
        self.lbl_spd = tk.Label(body, text="", bg=C["panel"],
                                fg=C["text"], font=("Courier New", 10))
        self.lbl_spd.pack(anchor="w")
        self.lbl_status = tk.Label(body, text="", bg=C["panel"],
                                   fg=C["cyan"], font=("Courier New", 10, "bold"))
        self.lbl_status.pack(anchor="w")

    def update(self, ac):
        if ac is None:
            self.lbl_ident.config(text="--")
            for lbl in [self.lbl_dist, self.lbl_alt, self.lbl_spd, self.lbl_status]:
                lbl.config(text="")
            return
        self.lbl_ident.config(text=ac.ident)
        compass = bearing_to_compass(ac.bearing_from_me)
        self.lbl_dist.config(text=f"{ac.dist_mi:.2f} mi  {compass}")
        self.lbl_alt.config(text=f"{ac.alt_ft}ft MSL  ({ac.alt_agl:+d}ft AGL)")
        spd = f"{ac.speed_kts:.0f}kts" if ac.speed_kts else "---kts"
        trk = f"  HDG {ac.track:.0f}" if ac.track else ""
        self.lbl_spd.config(text=spd + trk)
        tags = []
        if ac.is_watched:   tags.append("WATCHLIST")
        if ac.is_orbiting:  tags.append("CIRCLING")
        if ac.threat_level == 2: tags.append("DANGER")
        elif ac.threat_level == 1: tags.append("WARNING")
        self.lbl_status.config(
            text=" | ".join(tags),
            fg=C["red_bright"] if ac.is_watched else C["cyan"])


class WatchlistPanel(tk.Frame):
    def __init__(self, parent, on_change=None, **kw):
        super().__init__(parent, bg=C["panel"],
                         highlightbackground=C["border_bright"],
                         highlightthickness=1, **kw)
        self._on_change = on_change
        tk.Label(self, text="WATCHLIST", bg=C["panel"],
                 fg=C["text_dim"], font=("Courier New", 9)).pack(anchor="w", padx=6, pady=(3, 0))
        tk.Frame(self, bg=C["border_bright"], height=1).pack(fill="x")
        cols = tk.Frame(self, bg=C["panel"])
        cols.pack(fill="both", expand=True, padx=4, pady=4)
        self._texts = []
        self._canvases = []
        for col in range(2):
            col_frame = tk.Frame(cols, bg=C["panel"])
            col_frame.pack(side="left", padx=(0 if col == 0 else 4, 0))
            t = tk.Text(col_frame, width=8, height=5,
                        bg=C["bg"], fg=C["red_bright"],
                        insertbackground=C["red_bright"],
                        font=("Courier New", 11),
                        relief="flat",
                        highlightbackground=C["border_bright"],
                        highlightthickness=1,
                        spacing3=4)
            t.pack()
            t.bind("<KeyRelease>", self._on_key)
            self._texts.append(t)
            c = tk.Canvas(col_frame, bg=C["panel"], height=10,
                          width=80, highlightthickness=0)
            c.pack(fill="x")
            self._draw_dots(c)
            self._canvases.append(c)
        self._loading = False

    def _draw_dots(self, canvas):
        canvas.delete("all")
        w = 80
        y = 5
        x = 2
        while x < w:
            canvas.create_line(x, y, x+2, y, fill=C["green_ring"], width=1)
            x += 6

    def _on_key(self, event):
        if self._loading:
            return
        entries = self.get_entries()
        if self._on_change:
            self._on_change(entries)

    def get_entries(self):
        entries = []
        for t in self._texts:
            for line in t.get("1.0", "end").splitlines():
                val = line.strip().upper()
                if val:
                    entries.append(val)
        return entries[:10]

    def load_entries(self, entries):
        self._loading = True
        col1 = entries[:5]
        col2 = entries[5:10]
        self._texts[0].delete("1.0", "end")
        self._texts[0].insert("1.0", "\n".join(col1))
        self._texts[1].delete("1.0", "end")
        self._texts[1].insert("1.0", "\n".join(col2))
        self._loading = False


def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def bearing_deg(lat1, lon1, lat2, lon2):
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(phi2)
    x = math.cos(phi1)*math.sin(phi2) - math.sin(phi1)*math.cos(phi2)*math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def ang_diff(a, b):
    return abs((a - b + 180) % 360 - 180)


def eta_seconds(dist_now, ring_mi, closing_mph):
    if not closing_mph or closing_mph <= 0:
        return None
    remaining = dist_now - ring_mi
    if remaining <= 0:
        return 0.0
    return (remaining / closing_mph) * 3600.0


def gpsd_connect():
    s = socket.create_connection(("127.0.0.1", 2947), timeout=5)
    s.sendall(b'?WATCH={"enable":true,"json":true}\n')
    s.settimeout(2)
    return s


def gpsd_get_fix(sock):
    buf = b""
    for _ in range(20):
        try:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("gpsd disconnected")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", "ignore"))
                except Exception:
                    continue
                if msg.get("class") == "TPV":
                    return msg.get("lat"), msg.get("lon"), msg.get("mode", 0)
        except socket.timeout:
            break
    return None, None, 0


def load_reg_db():
    for path in DB_CANDIDATES:
        if os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
            except Exception:
                pass
    return None


def lookup_tail(db, hexid):
    if not db or not hexid:
        return None
    v = db.get(hexid.lower())
    if isinstance(v, dict):
        return v.get("r") or v.get("reg") or v.get("registration")
    return v if isinstance(v, str) else None
class ADSBMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ADS-B DRONE SAFETY MONITOR")
        self.root.configure(bg=C["bg"])
        self.root.geometry("800x480")
        self.root.resizable(True, True)
        self.audio = AudioEngine()
        self.reg_db = load_reg_db()
        self.orbit_tracker = OrbitTracker()
        self.my_lat = None
        self.my_lon = None
        self.gps_ok = False
        self.gps_sock = None
        self.last_dist = {}
        self.last_warn = {}
        self.last_orbit_warn = {}
        self.last_watch_warn = {}
        self.last_danger_beep = 0
        self.total_ac_seen = 0
        self.sdr_ok = False
        self.threats = []
        self.safe_ac = []
        self.selected_ac = None
        self.watchlist = load_watchlist()
        self.running = True
        self._build_ui()
        self._start_gps_thread()
        self._schedule_update()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _on_radar_select(self, ac):
        self.selected_ac = ac
        self.selected_panel.update(ac)

    def _on_watchlist_change(self, entries):
        self.watchlist = entries
        save_watchlist(entries)

    def _build_ui(self):
        topbar = tk.Frame(self.root, bg=C["panel"], height=30)
        topbar.pack(fill="x")
        topbar.pack_propagate(False)
        tk.Label(topbar, text="ADS-B", bg=C["panel"],
                 fg=C["green_radar"], font=("Courier New", 12, "bold")).pack(side="left", padx=8)
        tk.Label(topbar, text="DRONE SAFETY MONITOR", bg=C["panel"],
                 fg=C["text"], font=("Courier New", 10)).pack(side="left")
        tk.Label(topbar,
                 text="NON-ADS-B AIRCRAFT NOT DETECTED -- MAINTAIN VISUAL SCAN",
                 bg=C["panel"], fg=C["text_dim"],
                 font=("Courier New", 7)).pack(side="right", padx=8)

        self.banner = AlertBanner(self.root, bg=C["bg"])
        self.banner.pack(fill="x", padx=4, pady=(3, 0))

        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=4, pady=4)

        left = tk.Frame(body, bg=C["bg"])
        left.pack(side="left", fill="y", padx=(0, 6))

        self.radar = RadarWidget(left, size=240, on_select=self._on_radar_select)
        self.radar.pack()

        ctrl = tk.Frame(left, bg=C["panel"],
                        highlightbackground=C["border_bright"], highlightthickness=1)
        ctrl.pack(fill="x", pady=(4, 0))

        tk.Label(ctrl, text="CONTROLS", bg=C["panel"],
                 fg=C["text_dim"], font=("Courier New", 9)).pack(anchor="w", padx=6, pady=(3, 0))
        tk.Frame(ctrl, bg=C["border_bright"], height=1).pack(fill="x")

        row1 = tk.Frame(ctrl, bg=C["panel"])
        row1.pack(fill="x", padx=6, pady=3)
        tk.Label(row1, text="ELEV:", bg=C["panel"],
                 fg=C["text"], font=("Courier New", 12, "bold")).pack(side="left")
        self.elev_var = tk.StringVar(value=str(FIELD_ELEV_FT))
        tk.Entry(row1, textvariable=self.elev_var, width=5,
                 bg=C["bg"], fg=C["green_radar"],
                 insertbackground=C["green_radar"],
                 font=("Courier New", 12), relief="flat",
                 highlightbackground=C["border_bright"],
                 highlightthickness=1).pack(side="left", padx=4)
        tk.Label(row1, text="ft", bg=C["panel"],
                 fg=C["text_dim"], font=("Courier New", 12)).pack(side="left")
        tk.Label(row1, text="  CEIL:", bg=C["panel"],
                 fg=C["text"], font=("Courier New", 12, "bold")).pack(side="left")
        self.alt_var = tk.StringVar(value=str(MAX_ALT_FT))
        tk.Entry(row1, textvariable=self.alt_var, width=5,
                 bg=C["bg"], fg=C["green_radar"],
                 insertbackground=C["green_radar"],
                 font=("Courier New", 12), relief="flat",
                 highlightbackground=C["border_bright"],
                 highlightthickness=1).pack(side="left", padx=4)
        tk.Label(row1, text="ft", bg=C["panel"],
                 fg=C["text_dim"], font=("Courier New", 12)).pack(side="left")

        tk.Frame(ctrl, bg=C["border"], height=1).pack(fill="x")

        row2 = tk.Frame(ctrl, bg=C["panel"])
        row2.pack(fill="x", padx=6, pady=3)
        tk.Label(row2, text="RANGE:", bg=C["panel"],
                 fg=C["text"], font=("Courier New", 12, "bold")).pack(side="left")
        self.range_var = tk.DoubleVar(value=RING_CAUTION_MI)
        self.range_label = tk.Label(row2, text=f"{RING_CAUTION_MI:.0f} mi",
                                    bg=C["panel"], fg=C["green_radar"],
                                    font=("Courier New", 12, "bold"), width=5)
        self.range_label.pack(side="right")
        tk.Scale(row2, from_=1, to=10, resolution=0.5,
                 orient="horizontal", variable=self.range_var,
                 bg=C["panel"], fg=C["text_dim"],
                 troughcolor=C["bg"],
                 highlightthickness=0, showvalue=False,
                 activebackground=C["green_radar"], sliderlength=18,
                 command=self._on_range_change).pack(side="left", fill="x", expand=True)

        self.selected_panel = SelectedPanel(left)
        self.selected_panel.pack(fill="x", pady=(4, 0))

        self.watch_panel = WatchlistPanel(left, on_change=self._on_watchlist_change)
        self.watch_panel.pack(fill="x", pady=(4, 0))
        self.watch_panel.load_entries(self.watchlist)

        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True)

        tk.Label(right, text="ACTIVE THREATS", bg=C["bg"],
                 fg=C["text_dim"], font=("Courier New", 11)).pack(anchor="w", padx=2)

        self.cards = []
        for i in range(2):
            tk.Frame(right, bg=C["border_bright"], height=1).pack(fill="x")
            card = ThreatCard(right)
            card.pack(fill="x", padx=2, pady=1)
            self.cards.append(card)

        tk.Frame(right, bg=C["border_bright"], height=1).pack(fill="x", pady=(4, 2))

        tk.Label(right, text="ALERT LOG", bg=C["bg"],
                 fg=C["text_dim"], font=("Courier New", 11)).pack(anchor="w", padx=2)
        log_frame = tk.Frame(right, bg=C["panel"],
                             highlightbackground=C["border"], highlightthickness=1)
        log_frame.pack(fill="both", expand=True, padx=2)
        scrollbar = tk.Scrollbar(log_frame, bg=C["panel"],
                                 troughcolor=C["bg"],
                                 activebackground=C["green_radar"])
        scrollbar.pack(side="right", fill="y")
        self.log_text = tk.Text(log_frame, bg=C["bg"], fg=C["green"],
                                font=("Courier New", 11), state="disabled",
                                wrap="word", relief="flat", cursor="arrow",
                                yscrollcommand=scrollbar.set)
        self.log_text.pack(fill="both", expand=True, padx=4, pady=2)
        scrollbar.config(command=self.log_text.yview)
        self.log_text.tag_config("caution",  foreground=C["yellow"])
        self.log_text.tag_config("warning",  foreground=C["orange"])
        self.log_text.tag_config("danger",   foreground=C["red"])
        self.log_text.tag_config("orbit",    foreground=C["cyan"])
        self.log_text.tag_config("watch",    foreground=C["red_bright"])
        self.log_text.tag_config("safe",     foreground=C["blue"])
        self.log_text.tag_config("info",     foreground=C["text_dim"])

        sbar = tk.Frame(self.root, bg=C["panel"], height=28)
        sbar.pack(fill="x", side="bottom")
        sbar.pack_propagate(False)
        self.lbl_gps = tk.Label(sbar, text="GPS: INITIALIZING", bg=C["panel"],
                                fg=C["yellow"], font=("Courier New", 12))
        self.lbl_gps.pack(side="left", padx=10)
        self.lbl_sdr = tk.Label(sbar, text="SDR: --", bg=C["panel"],
                                fg=C["text_dim"], font=("Courier New", 12))
        self.lbl_sdr.pack(side="left", padx=10)
        self.lbl_ac = tk.Label(sbar, text="AC: 0", bg=C["panel"],
                               fg=C["text_dim"], font=("Courier New", 12))
        self.lbl_ac.pack(side="left", padx=10)
        self.lbl_time = tk.Label(sbar, text="", bg=C["panel"],
                                 fg=C["text_dim"], font=("Courier New", 12))
        self.lbl_time.pack(side="right", padx=10)
        self.lbl_pos = tk.Label(sbar, text="", bg=C["panel"],
                                fg=C["text_dim"], font=("Courier New", 12))
        self.lbl_pos.pack(side="right", padx=10)

    def _on_range_change(self, val):
        global RING_CAUTION_MI
        RING_CAUTION_MI = float(val)
        self.range_label.config(text=f"{float(val):.1f} mi")
        self.radar._draw_static()

    def _log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n", level)
        self.log_text.see("end")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > LOG_MAX_LINES:
            self.log_text.delete("1.0", "2.0")
        self.log_text.config(state="disabled")

    def _start_gps_thread(self):
        def run():
            fix_gps_setup()
            while self.running:
                try:
                    if not self.gps_sock:
                        self.gps_sock = gpsd_connect()
                    lat, lon, mode = gpsd_get_fix(self.gps_sock)
                    if mode >= 2 and lat is not None:
                        self.my_lat = lat
                        self.my_lon = lon
                        self.gps_ok = True
                    else:
                        self.gps_ok = False
                except Exception:
                    self.gps_ok = False
                    self.gps_sock = None
                    time.sleep(2)
                time.sleep(0.5)
        threading.Thread(target=run, daemon=True).start()

    def _schedule_update(self):
        if self.running:
            self._update()
            self.root.after(int(SAMPLE_SEC * 1000), self._schedule_update)

    def _update(self):
        global FIELD_ELEV_FT, MAX_ALT_FT
        now = time.time()
        try:
            FIELD_ELEV_FT = int(self.elev_var.get())
        except ValueError:
            pass
        try:
            MAX_ALT_FT = int(self.alt_var.get())
        except ValueError:
            pass
        self.lbl_time.config(text=datetime.now().strftime("%H:%M:%S"))
        if not self.gps_ok or self.my_lat is None:
            self.banner.set_caution("AWAITING GPS FIX")
            self.lbl_gps.config(text="GPS: ACQUIRING...", fg=C["yellow"])
            return
        self.lbl_gps.config(text="GPS: LOCKED", fg=C["green"])
        if self.my_lat and self.my_lon:
            self.lbl_pos.config(text=f"{self.my_lat:.4f}, {self.my_lon:.4f}")
        raw_threats = []
        raw_safe = []
        raw_watched = []
        try:
            with open(AIRCRAFT_JSON) as f:
                data = json.load(f)
            aircraft_list = data.get("aircraft", [])
            self.total_ac_seen = len(aircraft_list)
            self.sdr_ok = True
            self.lbl_sdr.config(text="SDR: OK", fg=C["green"])
            self.lbl_ac.config(text=f"AC: {self.total_ac_seen}",
                               fg=C["cyan"] if self.total_ac_seen > 0 else C["text_dim"])
        except Exception:
            self.sdr_ok = False
            self.lbl_sdr.config(text="SDR: NO DATA", fg=C["red"])
            return

        active_hexids = set()
        watchlist_set = set(self.watchlist)

        for ac in aircraft_list:
            hexid = ac.get("hex")
            lat = ac.get("lat")
            lon = ac.get("lon")
            if not hexid or lat is None or lon is None:
                continue
            alt = ac.get("alt_geom", ac.get("alt_baro"))
            if alt is None or isinstance(alt, str):
                continue

            tail   = lookup_tail(self.reg_db, hexid)
            flight = (ac.get("flight") or "").strip()

            ident_check = set()
            if tail:   ident_check.add(tail.upper())
            if flight: ident_check.add(flight.upper())
            ident_check.add(hexid.upper())
            is_watched = matches_watchlist(ident_check, watchlist_set)

            dist = haversine_miles(self.my_lat, self.my_lon, lat, lon)
            bear = bearing_deg(self.my_lat, self.my_lon, lat, lon)

            if not is_watched and dist > RING_CAUTION_MI:
                continue

            active_hexids.add(hexid)
            track = ac.get("track")
            is_orbiting = self.orbit_tracker.update(hexid, track, now)

            closing_mph = None
            if hexid in self.last_dist:
                prev_d, prev_t = self.last_dist[hexid]
                dt = now - prev_t
                if dt > 0.5:
                    closing_mph = ((prev_d - dist) / dt) * 3600.0
            self.last_dist[hexid] = (dist, now)

            eta_sec = eta_seconds(dist, RING_WARN_MI, closing_mph)

            is_threat = False
            if alt <= (MAX_ALT_FT + FIELD_ELEV_FT):
                if track is not None:
                    to_me = bearing_deg(lat, lon, self.my_lat, self.my_lon)
                    if ang_diff(float(track), to_me) <= HEADING_WINDOW_DEG:
                        if closing_mph is None or closing_mph >= MIN_CLOSING_MPH:
                            is_threat = True
                elif closing_mph is not None and closing_mph >= MIN_CLOSING_MPH:
                    is_threat = True

            if dist <= RING_DANGER_MI:   level = 2
            elif dist <= RING_WARN_MI:   level = 1
            else:                        level = 0

            obj = Aircraft(hexid=hexid, lat=lat, lon=lon,
                           alt_ft=int(alt), dist_mi=dist,
                           track=track, speed_kts=ac.get("gs"),
                           flight=flight, tail=tail,
                           closing_mph=closing_mph,
                           eta_1mi_sec=eta_sec,
                           threat_level=level if is_threat else 0,
                           bearing_from_me=bear,
                           is_orbiting=is_orbiting,
                           is_watched=is_watched)

            if is_watched:
                self._handle_watch_alert(obj, now)
                raw_watched.append(obj)
            elif is_threat:
                raw_threats.append(obj)
                self._handle_threat_alerts(obj, now)
            else:
                raw_safe.append(obj)

            if is_orbiting:
                self._handle_orbit_alert(obj, now, bear)

        self.orbit_tracker.cleanup(active_hexids)
        raw_threats.sort(key=lambda a: a.dist_mi)
        raw_safe.sort(key=lambda a: a.dist_mi)
        raw_watched.sort(key=lambda a: a.dist_mi)
        self.threats = raw_watched + raw_threats
        self.safe_ac = raw_safe

        if self.selected_ac:
            all_ac = self.threats + raw_safe
            updated = next((a for a in all_ac if a.hexid == self.selected_ac.hexid), None)
            self.selected_ac = updated
            self.selected_panel.update(updated)

        self._update_banner()
        self._update_cards()
        self.radar.update_aircraft(self.threats, self.safe_ac)

    def _handle_watch_alert(self, ac, now):
        hexid = ac.hexid
        if now - self.last_watch_warn.get(hexid, 0) >= WATCH_COOLDOWN_SEC:
            compass = bearing_to_compass(ac.bearing_from_me)
            msg = f"WATCHLIST: {ac.ident}  {ac.dist_mi:.2f}mi  {compass}  {ac.alt_ft}ft"
            self._log(msg, "watch")
            self.audio.watch_tone()
            self.audio.speak(
                f"Watchlist aircraft {ac.ident}, "
                f"{ac.dist_mi:.1f} miles, {compass.lower()}.")
            self.last_watch_warn[hexid] = now

    def _handle_orbit_alert(self, ac, now, bearing):
        hexid = ac.hexid
        if now - self.last_orbit_warn.get(hexid, 0) >= ORBIT_COOLDOWN_SEC:
            compass = bearing_to_compass(bearing)
            msg = f"SKY CIRCLE: {ac.ident}  {ac.dist_mi:.2f}mi  {compass}  {ac.alt_ft}ft"
            self._log(msg, "orbit")
            self.audio.orbit_tone()
            self.audio.speak(
                f"Caution. Circling aircraft {ac.ident}, "
                f"{ac.dist_mi:.1f} miles, {compass.lower()}.")
            self.last_orbit_warn[hexid] = now

    def _handle_threat_alerts(self, ac, now):
        hexid = ac.hexid
        ident = ac.ident
        if ac.threat_level == 2:
            if now - self.last_danger_beep >= DANGER_COOLDOWN_SEC:
                self._log(f"DANGER: {ident}  {ac.dist_mi:.2f}mi  {ac.alt_ft}ft", "danger")
                self.audio.danger_tone()
                self.audio.speak(
                    f"DANGER. Aircraft {ident}, {ac.dist_mi:.1f} miles, {ac.alt_ft} feet.")
                self.last_danger_beep = now
                self.last_warn[hexid] = now
            return
        if ac.threat_level == 1:
            if now - self.last_warn.get(hexid, 0) >= WARN_COOLDOWN_SEC:
                eta_s = (f", ETA {int(ac.eta_1mi_sec)} seconds"
                         if ac.eta_1mi_sec and ac.eta_1mi_sec < 120 else "")
                self._log(
                    f"WARNING: {ident}  {ac.dist_mi:.2f}mi  {ac.alt_ft}ft  {ac.eta_str}",
                    "warning")
                self.audio.warning_tone()
                self.audio.speak(
                    f"Warning. Aircraft {ident}, {ac.dist_mi:.1f} miles, "
                    f"{ac.alt_ft} feet{eta_s}.")
                self.last_warn[hexid] = now
            return
        if now - self.last_warn.get(hexid, 0) >= WARN_COOLDOWN_SEC:
            self._log(
                f"CAUTION: {ident}  {ac.dist_mi:.2f}mi  {ac.alt_ft}ft  {ac.closing_str}",
                "caution")
            self.audio.caution_tone()
            self.audio.speak(
                f"Caution. Aircraft {ident}, {ac.dist_mi:.1f} miles, "
                f"{ac.alt_ft} feet, closing.")
            self.last_warn[hexid] = now

    def _update_banner(self):
        watched = [a for a in self.threats if a.is_watched]
        if watched:
            w = watched[0]
            compass = bearing_to_compass(w.bearing_from_me)
            self.banner.set_watch(f"{w.ident}  {w.dist_mi:.2f}mi  {compass}")
            return
        threats = [a for a in self.threats if not a.is_watched]
        if threats:
            w = threats[0]
            msg = f"{w.ident}  {w.dist_mi:.2f}mi  {w.alt_ft}ft  {w.eta_str}"
            if w.threat_level == 2:      self.banner.set_danger(msg)
            elif w.threat_level == 1:    self.banner.set_warning(msg)
            else:                        self.banner.set_caution(msg)
            return
        orbiting = [a for a in self.safe_ac if a.is_orbiting]
        if orbiting:
            o = orbiting[0]
            compass = bearing_to_compass(o.bearing_from_me)
            self.banner.set_orbit(f"{o.ident}  {o.dist_mi:.2f}mi  {compass}")
            return
        self.banner.set_clear()

    def _update_cards(self):
        display = self.threats[:]
        if len(display) < 2:
            orbiting = [a for a in self.safe_ac if a.is_orbiting]
            display += orbiting[:2 - len(display)]
        for i, card in enumerate(self.cards):
            if i < len(display):
                card.update(display[i])
            else:
                card.clear()

    def _on_close(self):
        self.running = False
        self.root.destroy()


def main():
    fix_gps_setup()
    root = tk.Tk()
    app = ADSBMonitorApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
