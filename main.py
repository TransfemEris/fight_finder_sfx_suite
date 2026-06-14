import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import crash_handler
import profiles as prof
from audio import AudioEngine, list_output_devices
from ovr_input import OVRInput

BUTTON_LABELS: dict[str, str] = {
    "trigger":    "Trigger",
    "grip":       "Grip",
    "primary":    "A/X",
    "secondary":  "B/Y",
    "thumbstick": "Stick",
}


# ──────────────────────────────────────── Swing Logic ──

class SwingHandler:

    def __init__(self, source: str, audio: AudioEngine):
        self.source = source
        self.audio  = audio
        self._lock  = threading.Lock()
        self._cfg: dict = {"low_threshold": 0.5, "tier": {}, "master_vol": 1.0}
        self._state: dict = {"window_start": 0.0, "peak": 0.0, "fired": False}
        self._active_streams: list = []

    def update(self, swing_cfg: dict, master_vol: float):
        with self._lock:
            self._cfg = {**swing_cfg, "master_vol": master_vol}

    def on_velocity(self, mag: float):
        with self._lock:
            cfg = dict(self._cfg)

        low        = cfg.get("low_threshold", 0.5)
        master_vol = cfg.get("master_vol", 1.0)
        tier       = cfg.get("tier", {})

        if mag < low or not tier:
            return

        file       = tier.get("file", "")
        vel_thresh = tier.get("vel_threshold", 1.0)
        t_window   = max(tier.get("time_window", 0.3), 0.01)
        volume     = tier.get("volume", 1.0) * master_vol

        if not file:
            return

        now = time.monotonic()

        if now - self._state["window_start"] > t_window:
            self._state["window_start"] = now
            self._state["peak"]         = 0.0
            self._state["fired"]        = False

        if mag > self._state["peak"]:
            self._state["peak"] = mag

        if not self._state["fired"] and self._state["peak"] >= vel_thresh:
            self._active_streams = [s for s in self._active_streams if not s.done]
            streams = self.audio.play(file, volume, loop=False)
            self._active_streams.extend(streams)
            self._state["fired"] = True


def _browse() -> str:
    return filedialog.askopenfilename(
        title="Select audio file",
        filetypes=[("Audio", "*.wav *.ogg *.mp3 *.flac"), ("All", "*.*")],
    )


def _slider_row(parent: tk.Widget, label: str, var: tk.DoubleVar,
                lo: float, hi: float, width: int = 200, unit: str = "") -> ttk.Frame:
    
    row = ttk.Frame(parent)
    row.pack(fill="x", padx=3, pady=1)
    ttk.Label(row, text=label, width=10, anchor="e").pack(side="left")
    ttk.Scale(row, variable=var, from_=lo, to=hi,
              orient="horizontal", length=width).pack(side="left", padx=3)
    lbl = ttk.Label(row, text=f"{var.get():.2f}{unit}", width=8)
    lbl.pack(side="left")
    var.trace_add("write", lambda *_, v=var, l=lbl, u=unit: l.config(text=f"{v.get():.2f}{u}"))
    return row


def _info_button(parent: tk.Widget, text: str, side: str = "right"):
    
    def _show():
        win = tk.Toplevel()
        win.title("How does this work?")
        win.resizable(False, False)
        win.attributes("-topmost", True)

        # Position near the button
        try:
            bx = btn.winfo_rootx()
            by = btn.winfo_rooty()
            win.geometry(f"+{bx + 24}+{by + 24}")
        except Exception:
            pass

        frame = ttk.Frame(win, padding=10)
        frame.pack(fill="both", expand=True)

        msg = ttk.Label(frame, text=text, wraplength=340, justify="left",
                        font=("TkDefaultFont", 9))
        msg.pack(fill="both", expand=True)

        ttk.Button(frame, text="Got it", command=win.destroy).pack(pady=(8, 0))
        win.bind("<Escape>", lambda _: win.destroy())
        win.focus_force()

    btn = ttk.Button(parent, text="ℹ", width=2, command=_show)
    btn.pack(side=side, padx=(2, 0))
    return btn


# ──────────────────────────────────────── TierFrame ──

class TierFrame:
    
    def __init__(self, parent: tk.Widget, defaults: dict):
        self.vars: dict[str, tk.Variable] = {}
        self._build(parent, defaults)

    def _build(self, parent: tk.Widget, defaults: dict):
        lf = ttk.LabelFrame(parent, text="Velocity SFX")
        lf.pack(fill="x", padx=4, pady=2)

        _info_button(lf, (
            "Plays a sound when your controller's speed crosses the threshold.\n\n"
            "Vel ≥  —  how fast you need to move before the sound fires. "
            "Window  —  the app tracks your peak speed over this many seconds. "
            "Shorter = snappier, longer = catches slow builds. 0.3s is a good start.\n\n"
            "Volume  —  how loud this sound is. Stacks with Master Volume in the header.\n\n"
        ))

        # File row
        r0 = ttk.Frame(lf)
        r0.pack(fill="x", padx=3, pady=2)
        fv = tk.StringVar(value=defaults.get("file", ""))
        self.vars["file"] = fv
        ttk.Entry(r0, textvariable=fv, width=40).pack(side="left")
        ttk.Button(r0, text="Browse",
                   command=lambda: fv.set(_browse() or fv.get())).pack(side="left", padx=2)

        # Velocity threshold
        vel_var = tk.DoubleVar(value=defaults.get("vel_threshold", 1.0))
        self.vars["vel_threshold"] = vel_var
        _slider_row(lf, "Vel ≥", vel_var, 0.0, 15.0, unit=" m/s")

        # Time window
        tw_var = tk.DoubleVar(value=defaults.get("time_window", 0.3))
        self.vars["time_window"] = tw_var
        _slider_row(lf, "Window", tw_var, 0.05, 2.0, unit=" s")

        # Volume
        vol_var = tk.DoubleVar(value=defaults.get("volume", 1.0))
        self.vars["volume"] = vol_var
        _slider_row(lf, "Volume", vol_var, 0.0, 1.0)

    def to_dict(self) -> dict:
        return {
            "file":          self.vars["file"].get(),
            "vel_threshold": self.vars["vel_threshold"].get(),
            "time_window":   self.vars["time_window"].get(),
            "volume":        self.vars["volume"].get(),
        }

    def load_dict(self, d: dict):
        self.vars["file"].set(d.get("file", ""))
        self.vars["vel_threshold"].set(d.get("vel_threshold", 1.0))
        self.vars["time_window"].set(d.get("time_window", 0.3))
        self.vars["volume"].set(d.get("volume", 1.0))


# ──────────────────────────────────────── SwingFrame ──

class SwingFrame:

    def __init__(self, parent: tk.Widget, swing_data: dict):
        self.vars: dict[str, tk.Variable] = {}
        self._tier_frame: TierFrame | None = None
        self._build(parent, swing_data)

    def _build(self, parent: tk.Widget, swing_data: dict):
        sf = ttk.LabelFrame(parent, text="Swing / Velocity SFX")
        sf.pack(fill="x", padx=4, pady=3)

        _info_button(sf, (
            "Min vel is a noise gate — any movement slower than this is completely "
            "ignored before even checking the threshold.\n\n"
            "Set it just above your 'standing still' jitter level. "
            "Usually 0.3, If the sound keeps firing when you're not "
            "doing anything, bump this up a little."
        ))

        # Low threshold (minimum velocity to trigger anything)
        low_var = tk.DoubleVar(value=swing_data.get("low_threshold", 0.5))
        self.vars["low_threshold"] = low_var
        _slider_row(sf, "Min vel", low_var, 0.0, 5.0, unit=" m/s")

        # Single tier
        tier_data = swing_data.get("tier", {})
        self._tier_frame = TierFrame(sf, tier_data)

    def to_dict(self) -> dict:
        return {
            "low_threshold": self.vars["low_threshold"].get(),
            "tier": self._tier_frame.to_dict() if self._tier_frame else {},
        }

    def load_dict(self, d: dict):
        self.vars["low_threshold"].set(d.get("low_threshold", 0.5))
        if self._tier_frame:
            self._tier_frame.load_dict(d.get("tier", {}))


# ──────────────────────────────────────── HandFrame ──

class HandFrame:

    def __init__(self, parent: tk.Widget, hand: str, has_buttons: bool = True):
        self.hand         = hand
        self._has_buttons = has_buttons
        self.vars: dict[str, tk.Variable] = {}
        self._swing_frame: SwingFrame | None = None
        self._build(parent)

    def _build(self, parent: tk.Widget, data: dict | None = None):
        data = data or {}
        self._swing_frame = SwingFrame(parent, data.get("swing", {}))

        if not self._has_buttons:
            return

        bf = ttk.LabelFrame(parent, text="Buttons")
        bf.pack(fill="x", padx=4, pady=3)

        _info_button(bf, (
            "Assign sounds to controller button presses and releases.\n\n"
            "Press  —  fires the moment you push the button down.\n"
            "Release  —  fires when you let go.\n"
            "Vol  —  per-button volume, multiplied by Master Volume.\n\n"
            "Leave a field blank to play nothing for that press. "
            "The three dots open a file browser so you can pick your audio file."
        ))

        hdr = ttk.Frame(bf)
        hdr.pack(fill="x", padx=3, pady=(2, 0))
        ttk.Label(hdr, text="",        width=8).pack(side="left")
        ttk.Label(hdr, text="Press",   width=28).pack(side="left")
        ttk.Label(hdr, text="Release", width=28).pack(side="left")
        ttk.Label(hdr, text="Vol").pack(side="left", padx=(6, 0))

        for btn_id, label in BUTTON_LABELS.items():
            self._btn_row(bf, btn_id, label)

    def _btn_row(self, parent: tk.Widget, btn_id: str, label: str):
        press_var = tk.StringVar()
        rel_var   = tk.StringVar()
        vol_var   = tk.DoubleVar(value=1.0)
        self.vars[f"{btn_id}_press"]   = press_var
        self.vars[f"{btn_id}_release"] = rel_var
        self.vars[f"{btn_id}_vol"]     = vol_var

        row = ttk.Frame(parent)
        row.pack(fill="x", padx=3, pady=1)
        ttk.Label(row, text=label, width=8).pack(side="left")
        ttk.Entry(row, textvariable=press_var, width=24).pack(side="left")
        ttk.Button(row, text="...", width=2,
                   command=lambda: press_var.set(_browse() or press_var.get())
                   ).pack(side="left", padx=(1, 4))
        ttk.Entry(row, textvariable=rel_var, width=24).pack(side="left")
        ttk.Button(row, text="...", width=2,
                   command=lambda: rel_var.set(_browse() or rel_var.get())
                   ).pack(side="left", padx=(1, 4))
        ttk.Scale(row, variable=vol_var, from_=0.0, to=1.0,
                  orient="horizontal", length=70).pack(side="left")

    def get_swing_cfg(self) -> dict:
        if self._swing_frame:
            return self._swing_frame.to_dict()
        return prof._default_swing()

    def get_button_cfg(self, btn_id: str) -> dict:
        return {
            "press":   self.vars.get(f"{btn_id}_press",   tk.StringVar()).get(),
            "release": self.vars.get(f"{btn_id}_release", tk.StringVar()).get(),
            "volume":  self.vars.get(f"{btn_id}_vol",     tk.DoubleVar(value=1.0)).get(),
        }

    def to_dict(self) -> dict:
        d: dict = {"swing": self.get_swing_cfg()}
        if self._has_buttons:
            d["buttons"] = {b: self.get_button_cfg(b) for b in BUTTON_LABELS}
        return d

    def load_dict(self, d: dict):
        if self._swing_frame:
            self._swing_frame.load_dict(d.get("swing", {}))
        if self._has_buttons:
            for btn_id in BUTTON_LABELS:
                bd = d.get("buttons", {}).get(btn_id, {})
                self.vars[f"{btn_id}_press"].set(bd.get("press", ""))
                self.vars[f"{btn_id}_release"].set(bd.get("release", ""))
                self.vars[f"{btn_id}_vol"].set(bd.get("volume", 1.0))


# ──────────────────────────────────────── MusicFrame ──

class MusicFrame:

    def __init__(self, parent: tk.Widget, audio: AudioEngine, app: "App"):
        self._audio    = audio
        self._app      = app
        self.vars: dict[str, tk.Variable] = {}
        self._streams: list = []   # active _Stream objects
        self._build(parent)

    def _build(self, parent: tk.Widget):
        # ── File picker ──
        ff = ttk.LabelFrame(parent, text="Music File")
        ff.pack(fill="x", padx=6, pady=(8, 4))

        _info_button(ff, (
            "Plays a mp3 through your output devices at the same time.\n\n"
            "Out 1 and Out 2 each have their own volume — so you can run it loud "
            "through your headset and quiet through a virtual cable "
            "for nusic, without touching each other.\n\n"
            "Loop keeps it going on repeat. "
            "Hitting Play while something's already playing does nothing — "
            "stop it first if you want to restart or switch tracks. (This should be obvious.)"
        ))

        fr = ttk.Frame(ff)
        fr.pack(fill="x", padx=4, pady=4)
        file_var = tk.StringVar()
        self.vars["file"] = file_var
        ttk.Entry(fr, textvariable=file_var, width=44).pack(side="left")
        ttk.Button(fr, text="Browse",
                   command=lambda: file_var.set(_browse() or file_var.get())
                   ).pack(side="left", padx=4)

        # ── Volume controls ──
        vf = ttk.LabelFrame(parent, text="Volume")
        vf.pack(fill="x", padx=6, pady=4)

        vol1_var = tk.DoubleVar(value=1.0)
        vol2_var = tk.DoubleVar(value=1.0)
        self.vars["volume1"] = vol1_var
        self.vars["volume2"] = vol2_var
        _slider_row(vf, "Out 1", vol1_var, 0.0, 1.0)
        _slider_row(vf, "Out 2", vol2_var, 0.0, 1.0)

        note = ttk.Label(vf,
            text="Each output plays at its own volume. Streams are independent.",
            foreground="grey", font=("TkDefaultFont", 8))
        note.pack(padx=6, pady=(0, 4))

        # ── Loop toggle ──
        lf = ttk.Frame(parent)
        lf.pack(fill="x", padx=6, pady=2)
        loop_var = tk.BooleanVar(value=True)
        self.vars["loop"] = loop_var
        ttk.Checkbutton(lf, text="Loop", variable=loop_var).pack(side="left")

        # ── Transport buttons ──
        tf = ttk.Frame(parent)
        tf.pack(fill="x", padx=6, pady=(6, 4))

        self._play_btn = ttk.Button(tf, text="Play",  command=self._play,  width=12)
        self._stop_btn = ttk.Button(tf, text="Stop",  command=self._stop,  width=12)
        self._play_btn.pack(side="left", padx=(0, 6))
        self._stop_btn.pack(side="left")

        self._status_lbl = ttk.Label(tf, text="Stopped", foreground="grey")
        self._status_lbl.pack(side="left", padx=10)

    # ── Playback ──────────────────────────────────────────

    def _play(self):
        # Don't interrupt if already playing
        alive = [v for v in self._streams if not v.done]
        if alive:
            return

        file = self.vars["file"].get()
        if not file:
            self._status_lbl.config(text="No file selected", foreground="red")
            return

        loop = self.vars["loop"].get()
        vol1 = self.vars["volume1"].get()
        vol2 = self.vars["volume2"].get()
        dev1 = self._audio.device1
        dev2 = self._audio.device2

        try:
            data, sr = self._audio._load(file)
        except Exception as e:
            self._status_lbl.config(text=f"Load error: {e}", foreground="red")
            return

        from audio import _Voice
        self._streams = []

        seen: set[int] = set()
        for dev, vol in ((dev1, vol1), (dev2, vol2)):
            if dev is None or dev in seen:
                continue
            seen.add(dev)
            d_ch = self._audio._adapt_channels(data, dev)
            mixer = self._audio._get_mixer(dev, sr, d_ch.shape[1])
            if mixer is None:
                continue
            v = _Voice(d_ch, vol, loop)
            mixer.add_voice(v)
            self._streams.append(v)

        if self._streams:
            self._status_lbl.config(text="Playing" + (" (loop)" if loop else ""), foreground="green")
            self._poll_done()
        else:
            self._status_lbl.config(text="No output device set", foreground="red")

    def _stop(self):
        for s in self._streams:
            s.stop()
        self._streams = []
        self._status_lbl.config(text="Stopped", foreground="grey")

    def _poll_done(self):
        
        if not self._streams:
            return
        alive = [s for s in self._streams if not s.done]
        if not alive:
            self._streams = []
            self._status_lbl.config(text="Stopped", foreground="grey")
            return
        # Schedule next check via the app's root
        try:
            self._app.root.after(500, self._poll_done)
        except Exception:
            pass

    # ── Serialisation ─────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "file":    self.vars["file"].get(),
            "volume1": self.vars["volume1"].get(),
            "volume2": self.vars["volume2"].get(),
            "loop":    self.vars["loop"].get(),
        }

    def load_dict(self, d: dict):
        self.vars["file"].set(d.get("file", ""))
        self.vars["volume1"].set(d.get("volume1", 1.0))
        self.vars["volume2"].set(d.get("volume2", 1.0))
        self.vars["loop"].set(d.get("loop", True))


# ──────────────────────────────────────── App ──

class App:
    def __init__(self, root: tk.Tk):
        self.root  = root
        root.title("Fight Finder SFX Suite")
        root.resizable(True, True)

        self.audio = AudioEngine()
        self.ovr   = OVRInput()

        # Register crash hook so we auto-save the profile and update status label
        crash_handler.on_crash = self._on_crash

        self._evt_q: queue.Queue = queue.Queue()
        self._current_profile    = "Default"

        self._hand_frames: dict[str, HandFrame | None] = {
            "left": None, "right": None, "head": None,
        }
        self._swing:      dict[str, SwingHandler] = {}
        self._dev_map: dict[str, int | None]  = {}
        self._music_frame: "MusicFrame | None" = None

        # Wire OVR callbacks
        self.ovr.on_velocity       = self._on_velocity
        self.ovr.on_button_press   = self._on_button_press
        self.ovr.on_button_release = self._on_button_release

        self._build_header()
        self._build_tabs()
        self._load_profile("Default")

        root.after(50,  self._process_evt_queue)
        root.after(100, self._sync_swing_cfg)

    # ── Header ──

    def _build_header(self):
        hdr = ttk.Frame(self.root)
        hdr.pack(fill="x", padx=4, pady=3)

        # OVR connection
        cf = ttk.LabelFrame(hdr, text="OVR")
        cf.grid(row=0, column=0, padx=3, sticky="nsew")
        ttk.Button(cf, text="Connect",    command=self._connect).pack(side="left", padx=3, pady=2)
        ttk.Button(cf, text="Disconnect", command=self._disconnect).pack(side="left", pady=2)
        self.status_lbl = ttk.Label(cf, text="Disconnected")
        self.status_lbl.pack(side="left", padx=6)
        _info_button(cf, (
            "Connects to SteamVR via OpenVR (OVR). SteamVR needs to already be running "
            "before you hit Connect.\n\n"
            "If it shows an error: make sure SteamVR is open and your headset is "
            "on or in standby mode. You can reconnect without restarting the app.\n\n"
            "If the status says 'poll thread died', something crashed in the background — "
            "check the crash log if it appears, then reconnect."
        ), side="left")

        # Crash log button (hidden until a crash occurs)
        self._crash_btn = ttk.Button(cf, text="View Crash Log",
                                     command=self._show_crash_log)
        # Not packed yet — shown on first crash

        # Profile
        pf = ttk.LabelFrame(hdr, text="Profile")
        pf.grid(row=0, column=1, padx=3, sticky="nsew")
        self.profile_var = tk.StringVar()
        self.profile_cb  = ttk.Combobox(pf, textvariable=self.profile_var,
                                        width=16, state="readonly")
        self.profile_cb.pack(side="left", padx=3, pady=2)
        self.profile_cb.bind("<<ComboboxSelected>>", self._on_profile_select)
        ttk.Button(pf, text="New",    command=self._new_profile).pack(side="left", padx=2, pady=2)
        ttk.Button(pf, text="Rename", command=self._rename_profile).pack(side="left", padx=2, pady=2)
        ttk.Button(pf, text="Save",   command=self._save_profile).pack(side="left", padx=2, pady=2)

        # Audio output
        of = ttk.LabelFrame(hdr, text="Output")
        of.grid(row=0, column=2, padx=3, sticky="nsew")
        devs     = list_output_devices()
        dev_opts = ["(none)"] + [f"{i}: {n}" for i, n in devs]
        self._dev_map = {"(none)": None, **{f"{i}: {n}": i for i, n in devs}}
        self.dev1_var = tk.StringVar(value="(none)")
        self.dev2_var = tk.StringVar(value="(none)")
        self.mvol_var = tk.DoubleVar(value=1.0)

        for label, var in [("Out 1:", self.dev1_var), ("Out 2:", self.dev2_var)]:
            r = ttk.Frame(of)
            r.pack(fill="x", padx=3, pady=1)
            ttk.Label(r, text=label, width=6).pack(side="left")
            cb = ttk.Combobox(r, textvariable=var, values=dev_opts, width=28, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", self._update_devices)

        mr = ttk.Frame(of)
        mr.pack(fill="x", padx=3, pady=(1, 2))
        ttk.Label(mr, text="Master:", width=7).pack(side="left")
        ttk.Scale(mr, variable=self.mvol_var, from_=0.0, to=1.0,
                  orient="horizontal", length=120).pack(side="left")
        mv_lbl = ttk.Label(mr, text="1.00", width=5)
        mv_lbl.pack(side="left")
        self.mvol_var.trace_add("write",
            lambda *_: mv_lbl.config(text=f"{self.mvol_var.get():.2f}"))
        _info_button(mr, (
            "Out 1 is usually your headset audio. "
            "Out 2 is optional — use it for a second device like a "
            "virtual audio cable for sound effects.\n\n"
            "Master Volume scales every sound in the whole app. "
            "Individual sounds have their own volume sliders too — "
            "the final level is Master × that sound's volume."
        ), side="left")

    # ── Tabs ──

    def _build_tabs(self):
        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._tab_frames: dict[str, tk.Frame] = {}
        tab_specs = [
            ("left",  "Left Hand"),
            ("right", "Right Hand"),
            ("head",  "Head"),
            ("music", "Music"),
        ]
        for key, label in tab_specs:
            tab = tk.Frame(self._nb)
            scroll = self._scrollable(tab)
            self._nb.add(tab, text=label)
            self._tab_frames[key] = scroll

    def _scrollable(self, parent: tk.Widget) -> tk.Frame:
        
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0)
        sb     = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        inner  = ttk.Frame(canvas)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=sb.set)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _resize(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(win_id, width=event.width)
        inner.bind("<Configure>", _resize)
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(win_id, width=e.width))

        def _mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind_all("<MouseWheel>", _mousewheel)
        return inner

    # ── Profile management ──

    def _refresh_profile_cb(self):
        names = prof.list_profiles() or ["Default"]
        self.profile_cb["values"] = names
        if self._current_profile not in names:
            self._current_profile = names[0]
        self.profile_var.set(self._current_profile)

    def _current_data(self) -> dict:
        d: dict = {
            "device1":       self.dev1_var.get(),
            "device2":       self.dev2_var.get(),
            "master_volume": self.mvol_var.get(),
        }
        for key in ("left", "right", "head"):
            hf = self._hand_frames.get(key)
            d[key] = hf.to_dict() if hf else {}
        if self._music_frame:
            d["music"] = self._music_frame.to_dict()
        return d

    def _save_profile(self):
        prof.save_profile(self._current_profile, self._current_data())
        self._refresh_profile_cb()

    def _on_profile_select(self, _event=None):
        target = self.profile_var.get()
        self._save_profile()
        self._load_profile(target)

    def _load_profile(self, name: str):
        self._current_profile = name
        data = prof.load_profile(name)

        # Hand / head tabs
        for hand, has_btns in [("left", True), ("right", True), ("head", False)]:
            tab = self._tab_frames[hand]
            for w in tab.winfo_children():
                w.destroy()
            hf = HandFrame(tab, hand, has_buttons=has_btns)
            hf.load_dict(data.get(hand, {}))
            self._hand_frames[hand] = hf
            self._swing[hand] = SwingHandler(hand, self.audio)

        # Music tab
        tab = self._tab_frames["music"]
        for w in tab.winfo_children():
            w.destroy()
        self._music_frame = MusicFrame(tab, self.audio, self)
        self._music_frame.load_dict(data.get("music", {}))

        d1 = data.get("device1") or "(none)"
        d2 = data.get("device2") or "(none)"
        if d1 in self._dev_map: self.dev1_var.set(d1)
        if d2 in self._dev_map: self.dev2_var.set(d2)
        self.mvol_var.set(data.get("master_volume", 1.0))
        self._update_devices()
        self._refresh_profile_cb()
        self._preload_profile(data)

    def _preload_profile(self, data: dict):
        files: list[str] = []
        for hand in ("left", "right", "head"):
            hdata = data.get(hand, {})
            f = hdata.get("swing", {}).get("tier", {}).get("file", "")
            if f: files.append(f)
            for btn_cfg in hdata.get("buttons", {}).values():
                for key in ("press", "release"):
                    f = btn_cfg.get(key, "")
                    if f: files.append(f)
        music_file = data.get("music", {}).get("file", "")
        if music_file:
            files.append(music_file)
        self.audio.preload(files)

    def _new_profile(self):
        name = simpledialog.askstring("New Profile", "Name:", parent=self.root)
        if not name or not name.strip():
            return
        name = name.strip()
        self._save_profile()
        prof.save_profile(name, prof.default_profile())
        self._load_profile(name)

    def _rename_profile(self):
        new = simpledialog.askstring(
            "Rename", f"Rename '{self._current_profile}' to:", parent=self.root)
        if not new or not new.strip():
            return
        new = new.strip()
        self._save_profile()
        prof.rename_profile(self._current_profile, new)
        self._current_profile = new
        self._refresh_profile_cb()
        self.profile_var.set(new)

    def _update_devices(self, *_):
        self.audio.device1 = self._dev_map.get(self.dev1_var.get())
        self.audio.device2 = self._dev_map.get(self.dev2_var.get())

    # ── OVR ──

    def _connect(self):
        ok, err = self.ovr.connect()
        self.status_lbl.config(text="Connected" if ok else f"Error: {err or 'unknown'}")

    def _disconnect(self):
        self.ovr.disconnect()
        self.status_lbl.config(text="Disconnected")

    # ── Callbacks from OVR thread ──

    def _on_velocity(self, source: str, mag: float):
        sh = self._swing.get(source)
        if sh:
            sh.on_velocity(mag)

    def _on_button_press(self, hand: str, btn: str):
        self._evt_q.put(("press", hand, btn))

    def _on_button_release(self, hand: str, btn: str):
        self._evt_q.put(("release", hand, btn))

    # ── Main-thread schedulers ──

    def _process_evt_queue(self):
        try:
            while True:
                kind, hand, btn = self._evt_q.get_nowait()
                hf = self._hand_frames.get(hand)
                if not hf:
                    continue
                cfg  = hf.get_button_cfg(btn)
                file = cfg["press"] if kind == "press" else cfg["release"]
                vol  = cfg["volume"] * self.mvol_var.get()
                if file:
                    self.audio.play(file, vol, loop=False)
        except queue.Empty:
            pass
        self.root.after(50, self._process_evt_queue)

    def _sync_swing_cfg(self):
        mv = self.mvol_var.get()

        # Sync hand/head swing handlers
        for hand, sh in self._swing.items():
            hf = self._hand_frames.get(hand)
            if hf:
                sh.update(hf.get_swing_cfg(), mv)

        # Detect if OVR poll thread died unexpectedly
        if (self.ovr.connected is False
                and self.status_lbl.cget("text") == "Connected"):
            self.status_lbl.config(text="OVR disconnected (poll thread died)")

        self.root.after(100, self._sync_swing_cfg)

    # ── Crash handling ──

    def _on_crash(self, log_path, tb_text: str):
        
        # Auto-save the current profile so work isn't lost
        try:
            self._save_profile()
        except Exception:
            pass
        # Schedule UI update on main thread
        try:
            self.root.after(0, lambda: self._show_crash_notice(log_path))
        except Exception:
            pass

    def _show_crash_notice(self, log_path):
        
        self.status_lbl.config(text="Crashed — profile auto-saved")
        self._crash_btn.pack(side="left", padx=(4, 2), pady=2)

    def _show_crash_log(self):
        
        logs = crash_handler.LOGS
        if not logs:
            messagebox.showinfo("Crash Logs", "No crash logs recorded this session.",
                                parent=self.root)
            return
        path = logs[-1]
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            messagebox.showerror("Crash Log", f"Could not read log:\n{e}", parent=self.root)
            return

        win = tk.Toplevel(self.root)
        win.title(f"Crash Log — {path.name}")
        win.geometry("780x480")

        toolbar = ttk.Frame(win)
        toolbar.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(toolbar, text=str(path), foreground="grey").pack(side="left")
        ttk.Button(toolbar, text="Open folder",
                   command=lambda: self._open_folder(path.parent)).pack(side="right")

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        sb = ttk.Scrollbar(frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(frame, wrap="none", font=("Courier", 9),
                      yscrollcommand=sb.set)
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.insert("1.0", text)
        txt.config(state="disabled")

        # Horizontal scrollbar
        hsb = ttk.Scrollbar(win, orient="horizontal", command=txt.xview)
        hsb.pack(fill="x", padx=4, pady=(0, 4))
        txt.config(xscrollcommand=hsb.set)

        # If there are multiple logs this session show a picker
        if len(logs) > 1:
            def _pick(*_):
                idx  = picker_var.get()
                p    = logs[int(idx)]
                body = p.read_text(encoding="utf-8")
                txt.config(state="normal")
                txt.delete("1.0", "end")
                txt.insert("1.0", body)
                txt.config(state="disabled")
                toolbar.winfo_children()[0].config(text=str(p))

            picker_var = tk.StringVar(value=str(len(logs) - 1))
            pf = ttk.Frame(win)
            pf.pack(fill="x", padx=4, pady=(0, 4))
            ttk.Label(pf, text="Log:").pack(side="left")
            for i, lp in enumerate(logs):
                ttk.Radiobutton(pf, text=lp.name, variable=picker_var,
                                value=str(i), command=_pick).pack(side="left", padx=2)

    @staticmethod
    def _open_folder(path):
        import subprocess, os
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


if __name__ == "__main__":
    root = tk.Tk()

    # Install crash handler before building App so any init error is also caught
    crash_handler.install(tk_root=root)

    try:
        app = App(root)
    except Exception:
        # App failed to initialise — log it and exit cleanly
        crash_handler._handle(*sys.exc_info(), source="App.__init__")
        sys.exit(1)

    try:
        root.mainloop()
    except Exception:
        crash_handler._handle(*sys.exc_info(), source="mainloop")
        try:
            app._save_profile()
        except Exception:
            pass
        sys.exit(1)
    finally:
        try:
            app.audio.close_mixers()
        except Exception:
            pass
