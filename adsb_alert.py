#!/usr/bin/env python3
"""
ADS-B Aircraft Monitor – GPS-based airspace threat detection.
"""
import json, time, math, os, socket, threading, subprocess
import tkinter as tk
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
from collections import deque

# ── Constants ──────────────────────────────────────────────────────────────────
AIRCRAFT_JSON        = "/run/readsb/aircraft.json"
RING_CAUTION_MI      = 3.0
RING_WARN_MI         = 1.0
RING_DANGER_MI       = 0.4
MAX_ALT_FT           = 1000
HEADING_WINDOW_DEG   = 45
MIN_CLOSING_MPH      = 5
WARN_COOLDOWN_SEC    = 25
DANGER_COOLDOWN_SEC  = 4
ORBIT_COOLDOWN_SEC   = 60
SAMPLE_SEC           = 1.0
FIELD_ELEV_FT        = 0
GPS_DEVICE           = "/dev/ttyAMA0"
ORBIT_HEADING_THRESHOLD  = 270
ORBIT_TIME_WINDOW        = 120
ORBIT_MIN_TURN_RATE_DPS  = 1.5   # deg/s — filters slow heading drift from true orbits
REG_DB_PATH          = "/etc/adsb-alert/reg.json"

# ── Colour palette ─────────────────────────────────────────────────────────────
C = {
    "bg":            "#0a0f0a",
    "panel":         "#0d150d",
    "border":        "#1a2a1a",
    "border_bright": "#2a4a2a",
    "text":          "#c8e6c8",
    "text_dim":      "#5a8a5a",
    "green":         "#00ff41",
    "green_radar":   "#00c830",
    "yellow":        "#ffd700",
    "orange":        "#ff8c00",
    "red":           "#ff2020",
    "cyan":          "#00e5ff",
    "blue":          "#4488ff",
}

COMPASS_POINTS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

# ── Math helpers ───────────────────────────────────────────────────────────────
def haversine_miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 +
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
         math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def bearing_deg(lat1, lon1, lat2, lon2):
    dlon = math.radians(lon2 - lon1)
    lat1r, lat2r = math.radians(lat1), math.radians(lat2)
    x = math.sin(dlon) * math.cos(lat2r)
    y = (math.cos(lat1r) * math.sin(lat2r) -
         math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon))
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def ang_diff(a, b):
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def eta_seconds(dist_mi, target_mi, closing_mph):
    if closing_mph is None or closing_mph <= 0 or dist_mi <= target_mi:
        return None
    return (dist_mi - target_mi) / closing_mph * 3600


def bearing_to_compass(bearing):
    return COMPASS_POINTS[int((bearing + 11.25) / 22.5) % 16]


def lookup_tail(reg_db, hexid):
    if not reg_db:
        return None
    return reg_db.get(hexid.upper())


def load_reg_db():
    try:
        with open(REG_DB_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


# ── GPS helpers ────────────────────────────────────────────────────────────────
def _gpsd_responding():
    """Return True if gpsd is already listening on port 2947."""
    try:
        s = socket.create_connection(("127.0.0.1", 2947), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def fix_gps_setup():
    """Ensure gpsd is running.

    Only *starts* gpsd if it is not already responding — never restarts a
    healthy daemon, which would interrupt an active fix.  Waits up to 15 s
    for the port to become available after a start.
    """
    if _gpsd_responding():
        return  # already up, nothing to do

    try:
        subprocess.run(
            ["sudo", "/usr/bin/systemctl", "start", "gpsd"],
            check=True, timeout=10,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass  # best-effort; the wait loop below will tell us if it worked

    for _ in range(15):
        time.sleep(1)
        if _gpsd_responding():
            return


def gpsd_connect():
    sock = socket.create_connection(("127.0.0.1", 2947), timeout=5)
    sock.settimeout(2.0)
    sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
    return sock


def gpsd_get_fix(sock):
    """Return (lat, lon, mode) from the next valid fix in the gpsd stream.

    Key behaviours vs. the old implementation:
    - Uses a wall-clock deadline (5 s) instead of a fixed iteration count,
      so behaviour is predictable regardless of socket timeout settings.
    - Skips TPV messages with mode < 2 (no fix yet) and keeps reading rather
      than returning immediately with (None, None, 1).  This is critical right
      after a reboot when gpsd emits many mode-1 messages before acquiring.
    - The receive buffer is local to the call but spans multiple recv() slices
      within the same 5-second window, so messages split across TCP packets
      are never lost.
    """
    buf = b""
    deadline = time.time() + 5.0

    while time.time() < deadline:
        try:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("gpsd disconnected")
            buf += chunk
        except socket.timeout:
            pass  # nothing arrived this slice — keep trying until deadline

        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8", "ignore"))
            except Exception:
                continue
            if msg.get("class") != "TPV":
                continue
            mode = msg.get("mode", 0)
            if mode < 2:
                continue  # no fix yet — discard and keep reading
            lat = msg.get("lat")
            lon = msg.get("lon")
            if lat is None or lon is None:
                continue
            return lat, lon, mode

    return None, None, 0


# ── Data model ─────────────────────────────────────────────────────────────────
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
    alt_agl: int          # computed once at creation — not a global-dependent property
    is_orbiting: bool = False

    @property
    def ident(self):
        if self.tail:
            return self.tail + (f" {self.flight}" if self.flight else "")
        return self.flight or self.hexid.upper()

    @property
    def eta_str(self):
        if self.eta_1mi_sec is None:
            return "ETA ---"
        if self.eta_1mi_sec < 60:
            return f"ETA {int(self.eta_1mi_sec)}s"
        return f"ETA {self.eta_1mi_sec / 60:.1f}m"

    @property
    def closing_str(self):
        if self.closing_mph is None or self.closing_mph <= 0:
            return ""
        return f"{self.closing_mph:.0f}mph"


# ── Orbit detector ─────────────────────────────────────────────────────────────
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
        time_span = entries[-1][0] - entries[0][0]
        if time_span < 10:
            return False
        total_turn = 0.0
        prev = entries[0][1]
        for _, heading in list(entries)[1:]:
            diff = (heading - prev + 180) % 360 - 180
            total_turn += diff
            prev = heading
        if abs(total_turn) < ORBIT_HEADING_THRESHOLD:
            return False
        # Require a sustained turn rate so normal gradual course changes
        # don't accumulate enough degrees to look like an orbit.
        return (abs(total_turn) / time_span) >= ORBIT_MIN_TURN_RATE_DPS

    def cleanup(self, active_hexids):
        for hexid in list(self._history.keys()):
            if hexid not in active_hexids:
                del self._history[hexid]


# ── Audio engine ───────────────────────────────────────────────────────────────
class AudioEngine:
    def __init__(self):
        self._lock = threading.Lock()
        # Allow at most 2 slots: one currently playing + one queued.
        # Any additional requests are dropped so old alerts don't play
        # out long after the threat has passed.
        self._slots = threading.Semaphore(2)

    def _run_blocking(self, cmd):
        """Run an audio process and wait for it to finish before returning."""
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            pass

    def speak(self, text):
        if not self._slots.acquire(blocking=False):
            return  # queue full — drop stale alert
        def _do():
            try:
                with self._lock:
                    self._run_blocking(
                        ["espeak-ng", "-s", "150", "-p", "45", "-a", "200", text]
                    )
            finally:
                self._slots.release()
        threading.Thread(target=_do, daemon=True).start()

    def _try_beep(self, freq, dur_ms):
        import wave, struct, tempfile
        sr = 22050
        samples = max(1, int(sr * dur_ms / 1000))
        amp = 28000
        data = [int(amp * math.sin(2 * math.pi * freq * i / sr)) for i in range(samples)]
        fade = min(200, samples // 4)
        for i in range(fade):
            data[i] = int(data[i] * i / fade)
            data[-(i + 1)] = int(data[-(i + 1)] * i / fade)
        fd, path = tempfile.mkstemp(suffix=".wav")
        try:
            with os.fdopen(fd, "wb") as raw:
                with wave.open(raw, "w") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(sr)
                    wf.writeframes(struct.pack(f"<{samples}h", *data))
            try:
                self._run_blocking(["paplay", path])
            except FileNotFoundError:
                self._run_blocking(["aplay", "-q", path])
        except Exception:
            pass
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass

    def beep(self, freq=880, dur_ms=120, count=1, gap_ms=80):
        if not self._slots.acquire(blocking=False):
            return  # queue full — drop stale alert
        def _do():
            try:
                with self._lock:
                    for i in range(count):
                        self._try_beep(freq, dur_ms)
                        if i < count - 1:
                            time.sleep(gap_ms / 1000)
            finally:
                self._slots.release()
        threading.Thread(target=_do, daemon=True).start()

    def caution_tone(self):  self.beep(880,  120)
    def warning_tone(self):  self.beep(1100, 150, count=2, gap_ms=60)
    def danger_tone(self):   self.beep(1400, 180, count=3, gap_ms=50)
    def orbit_tone(self):    self.beep(660,  200)


# ── UI widgets ─────────────────────────────────────────────────────────────────
class AlertBanner(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["panel"], height=36)
        self.pack_propagate(False)
        self._lbl = tk.Label(self, text="", bg=C["panel"],
                             font=("Courier New", 14, "bold"))
        self._lbl.pack(fill="both", expand=True)

    def _set(self, text, fg, bg):
        self.config(bg=bg)
        self._lbl.config(text=text, fg=fg, bg=bg)

    def set_clear(self):         self._set("  AIRSPACE CLEAR",    C["green"],  C["panel"])
    def set_caution(self, msg):  self._set(f"  CAUTION: {msg}",    C["yellow"], "#1a1600")
    def set_warning(self, msg):  self._set(f"  WARNING: {msg}",    C["orange"], "#1a0a00")
    def set_danger(self, msg):   self._set(f"  DANGER:  {msg}",    "#ffffff",   "#3a0000")
    def set_orbit(self, msg):    self._set(f"  ORBIT:   {msg}",    C["cyan"],   "#001a1a")


class ThreatCard(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["panel"], height=52)
        self.pack_propagate(False)
        self._line1 = tk.Label(self, text="", bg=C["panel"],
                               fg=C["text_dim"], font=("Courier New", 12, "bold"),
                               anchor="w")
        self._line1.pack(fill="x", padx=6, pady=(4, 0))
        self._line2 = tk.Label(self, text="", bg=C["panel"],
                               fg=C["text_dim"], font=("Courier New", 11),
                               anchor="w")
        self._line2.pack(fill="x", padx=6)

    def update(self, ac: Aircraft):
        colors = [C["yellow"], C["orange"], C["red"]]
        fg = colors[min(ac.threat_level, 2)] if ac.threat_level else C["cyan"]
        self._line1.config(
            text=f"{ac.ident:<12}  {ac.dist_mi:.2f} mi  {ac.alt_ft} ft", fg=fg)
        self._line2.config(
            text=f"  {ac.eta_str}  {ac.closing_str}  {'ORBIT' if ac.is_orbiting else ''}",
            fg=C["text_dim"])

    def clear(self):
        self._line1.config(text="")
        self._line2.config(text="")


class SelectedPanel(tk.Frame):
    def __init__(self, parent):
        super().__init__(parent, bg=C["panel"],
                         highlightbackground=C["border_bright"],
                         highlightthickness=1)
        tk.Label(self, text="SELECTED", bg=C["panel"],
                 fg=C["text_dim"], font=("Courier New", 10)).pack(anchor="w", padx=4, pady=(2, 0))
        self._labels = []
        for _ in range(4):
            lbl = tk.Label(self, text="", bg=C["panel"],
                           fg=C["text"], font=("Courier New", 11), anchor="w")
            lbl.pack(fill="x", padx=6)
            self._labels.append(lbl)

    def update(self, ac: Optional[Aircraft]):
        if ac is None:
            for lbl in self._labels:
                lbl.config(text="")
            return
        compass = bearing_to_compass(ac.bearing_from_me)
        lines = [
            f"{ac.ident}",
            f"  {ac.dist_mi:.2f} mi  {compass}  {ac.bearing_from_me:.0f}°",
            f"  ALT {ac.alt_ft} ft  AGL {ac.alt_agl} ft",
            f"  {ac.closing_str}  {ac.eta_str}",
        ]
        for lbl, txt in zip(self._labels, lines):
            lbl.config(text=txt)


class RadarWidget(tk.Canvas):
    def __init__(self, parent, on_select=None, **kwargs):
        super().__init__(parent, bg=C["bg"], highlightthickness=0, **kwargs)
        self._on_select = on_select
        self._all_aircraft = []
        self._selected_hexid = None
        self._sweep_angle = 0
        self.bind("<Configure>", lambda e: self._draw_static())
        self.bind("<Button-1>", self._on_click)
        self._draw_static()
        self._animate_sweep()

    def _cx(self): return self.winfo_width() / 2
    def _cy(self): return self.winfo_height() / 2
    def _r(self):  return min(self.winfo_width(), self.winfo_height()) / 2 - 8

    def _draw_static(self):
        self.delete("static")
        cx, cy, r = self._cx(), self._cy(), self._r()
        if r <= 0:
            return
        for frac, label, color in [
            (RING_DANGER_MI / RING_CAUTION_MI, f"{RING_DANGER_MI:.1f}mi", C["red"]),
            (RING_WARN_MI   / RING_CAUTION_MI, f"{RING_WARN_MI:.1f}mi",   C["orange"]),
            (1.0,                              f"{RING_CAUTION_MI:.0f}mi", C["text_dim"]),
        ]:
            rr = r * frac
            self.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                             outline=color, width=1, tags="static")
            self.create_text(cx + rr - 4, cy + 4, text=label,
                             fill=color, font=("Courier New", 8),
                             anchor="ne", tags="static")
        self.create_line(cx, cy - r, cx, cy + r, fill=C["border"], tags="static")
        self.create_line(cx - r, cy, cx + r, cy, fill=C["border"], tags="static")
        self.create_text(cx, cy, text="+", fill=C["green"],
                         font=("Courier New", 12, "bold"), tags="static")
        self.create_text(cx, cy - r + 10, text="N", fill=C["text_dim"],
                         font=("Courier New", 9, "bold"), tags="static")

    def _ac_screen_pos(self, ac):
        cx, cy, r = self._cx(), self._cy(), self._r()
        frac = min(ac.dist_mi / RING_CAUTION_MI, 1.0)
        rad = math.radians(ac.bearing_from_me)
        return cx + r * frac * math.sin(rad), cy - r * frac * math.cos(rad)

    def _animate_sweep(self):
        self.delete("sweep")
        cx, cy, r = self._cx(), self._cy(), self._r()
        if r > 0:
            for i in range(30):
                angle = (self._sweep_angle - i * 3) % 360
                alpha = max(0, 1 - i / 30)
                rad = math.radians(angle)
                ex = cx + r * math.sin(rad)
                ey = cy - r * math.cos(rad)
                brightness = int(alpha * 0x28)
                color = f"#{brightness:02x}{brightness + 0x10:02x}{brightness:02x}"
                try:
                    self.create_line(cx, cy, ex, ey, fill=color, tags="sweep")
                except Exception:
                    pass
        self._sweep_angle = (self._sweep_angle + 3) % 360
        self.after(80, self._animate_sweep)

    def update_aircraft(self, threats, safe_ac):
        self._all_aircraft = threats + safe_ac
        self.delete("aircraft")
        for ac in safe_ac:
            self._draw_ac(ac)
        for ac in threats:
            self._draw_ac(ac)

    def _draw_ac(self, ac):
        px, py = self._ac_screen_pos(ac)
        is_sel = ac.hexid == self._selected_hexid
        if ac.threat_level == 2:        color = C["red"]
        elif ac.threat_level == 1:      color = C["orange"]
        elif ac.is_orbiting:            color = C["cyan"]
        else:                           color = C["green_radar"]
        r = 5 if is_sel else 3
        self.create_oval(px - r, py - r, px + r, py + r,
                         fill=color, outline=color, tags="aircraft")
        self.create_text(px + 6, py - 6, text=ac.ident,
                         fill=color, font=("Courier New", 8),
                         anchor="w", tags="aircraft")

    def _on_click(self, event):
        best, best_dist = None, 18
        for ac in self._all_aircraft:
            px, py = self._ac_screen_pos(ac)
            d = math.hypot(event.x - px, event.y - py)
            if d < best_dist:
                best_dist, best = d, ac
        self._selected_hexid = best.hexid if best else None
        if self._on_select:
            self._on_select(best)


# ── Main application ───────────────────────────────────────────────────────────
class ADSBMonitorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("ADS-B AIRCRAFT MONITOR")
        self.root.configure(bg=C["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.running = True
        self.audio = AudioEngine()
        self.orbit_tracker = OrbitTracker()
        self.reg_db = load_reg_db()

        # GPS state — all writes go through _gps_lock so _update() can take
        # an atomic snapshot without racing the GPS thread.
        self._gps_lock = threading.Lock()
        self.my_lat = None
        self.my_lon = None
        self.gps_ok = False
        self.gps_sock = None

        self.last_dist = {}
        self.last_warn = {}
        self.last_orbit_warn = {}
        self.last_danger_beep = 0
        self.threats = []
        self.safe_ac = []
        self.selected_ac = None
        self.total_ac_seen = 0
        self.sdr_ok = False

        self._build_ui()
        self._start_gps_thread()
        self._schedule_update()

    # ── UI construction ────────────────────────────────────────────────────────
    def _build_ui(self):
        self.banner = AlertBanner(self.root)
        self.banner.pack(fill="x")

        body = tk.Frame(self.root, bg=C["bg"])
        body.pack(fill="both", expand=True)

        left = tk.Frame(body, bg=C["bg"], width=220)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)

        ctrl = tk.Frame(left, bg=C["panel"],
                        highlightbackground=C["border_bright"],
                        highlightthickness=1)
        ctrl.pack(fill="x", padx=2, pady=2)

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

        self.radar = RadarWidget(left, on_select=self._on_ac_select,
                                 width=216, height=216)
        self.radar.pack(padx=2, pady=2)

        self.selected_panel = SelectedPanel(left)
        self.selected_panel.pack(fill="x", pady=(4, 0))

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
                             highlightbackground=C["border_bright"], highlightthickness=1)
        log_frame.pack(fill="both", expand=True, padx=2)
        self.log_text = tk.Text(log_frame, bg=C["panel"], fg=C["text_dim"],
                                font=("Courier New", 11), state="disabled",
                                wrap="word", relief="flat", cursor="arrow")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=2)
        self.log_text.tag_config("caution", foreground=C["yellow"])
        self.log_text.tag_config("warning", foreground=C["orange"])
        self.log_text.tag_config("danger",  foreground=C["red"])
        self.log_text.tag_config("orbit",   foreground=C["cyan"])
        self.log_text.tag_config("safe",    foreground=C["blue"])
        self.log_text.tag_config("info",    foreground=C["text_dim"])

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

    # ── Callbacks ──────────────────────────────────────────────────────────────
    def _on_range_change(self, val):
        global RING_CAUTION_MI
        RING_CAUTION_MI = float(val)
        self.range_label.config(text=f"{float(val):.1f} mi")
        self.radar._draw_static()

    def _on_ac_select(self, ac):
        self.selected_ac = ac
        self.selected_panel.update(ac)

    def _log(self, msg, level="info"):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.config(state="normal")
        self.log_text.insert("end", f"[{ts}] {msg}\n", level)
        self.log_text.see("end")
        lines = int(self.log_text.index("end-1c").split(".")[0])
        if lines > 200:
            self.log_text.delete("1.0", "2.0")
        self.log_text.config(state="disabled")

    # ── GPS thread ─────────────────────────────────────────────────────────────
    def _start_gps_thread(self):
        def run():
            fix_gps_setup()
            delay = 1.0
            while self.running:
                try:
                    if not self.gps_sock:
                        self.gps_sock = gpsd_connect()
                        delay = 1.0  # reset backoff on successful connect
                    lat, lon, mode = gpsd_get_fix(self.gps_sock)
                    if mode >= 2 and lat is not None:
                        with self._gps_lock:
                            self.my_lat = lat
                            self.my_lon = lon
                            self.gps_ok = True
                    else:
                        with self._gps_lock:
                            self.gps_ok = False
                except Exception:
                    with self._gps_lock:
                        self.gps_ok = False
                    if self.gps_sock:
                        try:
                            self.gps_sock.close()
                        except Exception:
                            pass
                        self.gps_sock = None
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)  # exponential backoff, cap at 30 s
                    continue
                time.sleep(0.5)
        threading.Thread(target=run, daemon=True).start()

    # ── Update loop ────────────────────────────────────────────────────────────
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

        # Atomic GPS snapshot — never read shared GPS state without this lock.
        with self._gps_lock:
            my_lat = self.my_lat
            my_lon = self.my_lon
            gps_ok = self.gps_ok

        if not gps_ok or my_lat is None:
            self.banner.set_caution("AWAITING GPS FIX")
            self.lbl_gps.config(text="GPS: ACQUIRING...", fg=C["yellow"])
            # Discard stale distance history so closing_mph cannot be computed
            # across a GPS gap, preventing phantom DANGER alerts on re-acquire.
            self.last_dist.clear()
            self._clear_display()
            return

        self.lbl_gps.config(text="GPS: LOCKED", fg=C["green"])
        self.lbl_pos.config(text=f"{my_lat:.4f}, {my_lon:.4f}")

        raw_threats = []
        raw_safe = []
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
            self._clear_display()
            return

        # Snapshot the range limit so it cannot change mid-loop if the slider
        # fires a callback while we are iterating.
        ring_caution = RING_CAUTION_MI
        active_hexids = set()

        for ac in aircraft_list:
            hexid = ac.get("hex")
            lat   = ac.get("lat")
            lon   = ac.get("lon")
            if not hexid or lat is None or lon is None:
                continue
            alt = ac.get("alt_baro", ac.get("alt_geom"))  # baro is MSL like FIELD_ELEV_FT
            if alt is None or isinstance(alt, str):
                continue
            dist = haversine_miles(my_lat, my_lon, lat, lon)
            if dist > ring_caution:
                continue
            active_hexids.add(hexid)
            track      = ac.get("track")
            is_orbiting = self.orbit_tracker.update(hexid, track, now)

            closing_mph = None
            if hexid in self.last_dist:
                prev_d, prev_t = self.last_dist[hexid]
                dt = now - prev_t
                # Discard samples older than 30 s — they span a data gap and
                # would produce wildly inaccurate closing speed estimates.
                if 0.5 < dt < 30:
                    closing_mph = ((prev_d - dist) / dt) * 3600.0
            self.last_dist[hexid] = (dist, now)

            tail   = lookup_tail(self.reg_db, hexid)
            flight = (ac.get("flight") or "").strip()
            eta_sec = eta_seconds(dist, RING_WARN_MI, closing_mph)
            bear    = bearing_deg(my_lat, my_lon, lat, lon)
            alt_agl = int(alt) - FIELD_ELEV_FT

            is_threat = False
            if alt <= (MAX_ALT_FT + FIELD_ELEV_FT):
                if track is not None:
                    to_me = bearing_deg(lat, lon, my_lat, my_lon)
                    if ang_diff(float(track), to_me) <= HEADING_WINDOW_DEG:
                        # Require a confirmed closing speed — do not flag
                        # first-contact aircraft whose closing_mph is None.
                        if closing_mph is not None and closing_mph >= MIN_CLOSING_MPH:
                            is_threat = True
                elif (closing_mph is not None and closing_mph >= MIN_CLOSING_MPH
                      and dist <= RING_WARN_MI):
                    # No heading data — only flag as threat when already inside
                    # the warning ring; beyond that we cannot distinguish a
                    # closing aircraft from a vehicle on a nearby road.
                    is_threat = True

            if dist <= RING_DANGER_MI:   level = 2
            elif dist <= RING_WARN_MI:   level = 1
            else:                        level = 0

            obj = Aircraft(
                hexid=hexid, lat=lat, lon=lon,
                alt_ft=int(alt), dist_mi=dist,
                track=track, speed_kts=ac.get("gs"),
                flight=flight, tail=tail,
                closing_mph=closing_mph,
                eta_1mi_sec=eta_sec,
                threat_level=level if is_threat else 0,
                bearing_from_me=bear,
                alt_agl=alt_agl,
                is_orbiting=is_orbiting,
            )
            if is_threat:
                raw_threats.append(obj)
                self._handle_threat_alerts(obj, now)
            else:
                raw_safe.append(obj)
            if is_orbiting:
                self._handle_orbit_alert(obj, now, bear)

        # Prune entries for aircraft that have left the caution ring so the
        # dicts do not grow unboundedly over a long session.
        stale = set(self.last_dist.keys()) - active_hexids
        for hexid in stale:
            self.last_dist.pop(hexid, None)
            self.last_warn.pop(hexid, None)

        self.orbit_tracker.cleanup(active_hexids)
        raw_threats.sort(key=lambda a: a.dist_mi)
        raw_safe.sort(key=lambda a: a.dist_mi)
        self.threats  = raw_threats
        self.safe_ac  = raw_safe

        if self.selected_ac:
            all_ac = raw_threats + raw_safe
            updated = next((a for a in all_ac if a.hexid == self.selected_ac.hexid), None)
            self.selected_ac = updated
            self.selected_panel.update(updated)

        self._update_banner()
        self._update_cards()
        self.radar.update_aircraft(self.threats, self.safe_ac)

    def _clear_display(self):
        """Clear threat cards and radar when data is unavailable."""
        self.threats = []
        self.safe_ac = []
        self._update_cards()
        self.radar.update_aircraft([], [])

    # ── Alert handlers ─────────────────────────────────────────────────────────
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

    # ── Display updates ────────────────────────────────────────────────────────
    def _update_banner(self):
        if self.threats:
            w = self.threats[0]
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


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    root = tk.Tk()
    app = ADSBMonitorApp(root)
    root.mainloop()
