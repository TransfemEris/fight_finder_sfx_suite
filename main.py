import json
import queue
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import crash_handler
import profiles as prof
import virtual_mic
import theme as _theme
from audio import AudioEngine, MicEngine, resolve_sfx_path, FOLDER_PREFIX, list_input_devices, list_output_devices
from osc_link import Bind, ComboBind, OSCLink
from ovr_input import OVRInput

def _resource_path(name: str) -> Path:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        base = Path(__file__).parent
    return base / name

def _user_data_path(name: str) -> Path:
    from app_paths import user_path
    return user_path(name)

def _relaunch_app() -> None:
    import subprocess

    try:
        if getattr(sys, "frozen", False):

            subprocess.Popen([sys.executable], close_fds=True)
        else:
            subprocess.Popen([sys.executable, __file__] + sys.argv[1:],
                              close_fds=True)
    except Exception as e:
        messagebox.showerror(
            "Restart failed",
            f"Couldn't start a new instance:\n{e}\n\n"
            "Please close and reopen the app manually.",
        )
        raise

    time.sleep(0.3)

BUTTON_LABELS: dict[str, str] = {
    "trigger":    "Trigger",
    "grip":       "Grip",
    "primary":    "A/X",
    "secondary":  "B/Y",
    "thumbstick": "Stick",
}

def _C(token: str) -> str:
    return _theme.get(token)

class _ColorProxy:
    def __getattr__(self, name: str) -> str:
        _map = {
            "ACCENT":          "accent",
            "ACCENT_DARK":     "accent_dark",
            "ACCENT_DIM":      "accent_dim",
            "BG_HEADER":       "bg_header",
            "BG_SECTION":      "bg_section",
            "BG_BASE":         "bg_base",
            "BG_FIELD":        "bg_field",
            "SEP_COLOR":       "sep_color",
            "TEXT_MAIN":       "text_main",
            "TEXT_DIM":        "text_dim",
            "TEXT_ON":         "text_on",
        }
        if name in _map:
            return _theme.get(_map[name])
        raise AttributeError(name)

_cp = _ColorProxy()

def _ACCENT()       -> str: return _theme.get("accent")
def _ACCENT_DARK()  -> str: return _theme.get("accent_dark")
def _ACCENT_DIM()   -> str: return _theme.get("accent_dim")
def _BG_HEADER()    -> str: return _theme.get("bg_header")
def _BG_SECTION()   -> str: return _theme.get("bg_section")
def _BG_BASE()      -> str: return _theme.get("bg_base")
def _BG_FIELD()     -> str: return _theme.get("bg_field")
def _SEP_COLOR()    -> str: return _theme.get("sep_color")
def _TEXT_MAIN()    -> str: return _theme.get("text_main")
def _TEXT_DIM()     -> str: return _theme.get("text_dim")
def _TEXT_ON()      -> str: return _theme.get("text_on")
def _TEXT_ERR()     -> str: return _theme.get("text_error")

ACCENT      = property(lambda s: _theme.get("accent"))
ACCENT_DARK = property(lambda s: _theme.get("accent_dark"))
ACCENT_DIM  = property(lambda s: _theme.get("accent_dim"))
BG_HEADER   = property(lambda s: _theme.get("bg_header"))
BG_SECTION  = property(lambda s: _theme.get("bg_section"))
BG_BASE     = property(lambda s: _theme.get("bg_base"))
BG_FIELD    = property(lambda s: _theme.get("bg_field"))
SEP_COLOR   = property(lambda s: _theme.get("sep_color"))
TEXT_MAIN   = property(lambda s: _theme.get("text_main"))
TEXT_DIM    = property(lambda s: _theme.get("text_dim"))
TEXT_ON     = property(lambda s: _theme.get("text_on"))

def _refresh_color_globals():
    global ACCENT, ACCENT_DARK, ACCENT_DIM, BG_HEADER, BG_SECTION
    global BG_BASE, BG_FIELD, SEP_COLOR, TEXT_MAIN, TEXT_DIM, TEXT_ON
    ACCENT      = _theme.get("accent")
    ACCENT_DARK = _theme.get("accent_dark")
    ACCENT_DIM  = _theme.get("accent_dim")
    BG_HEADER   = _theme.get("bg_header")
    BG_SECTION  = _theme.get("bg_section")
    BG_BASE     = _theme.get("bg_base")
    BG_FIELD    = _theme.get("bg_field")
    SEP_COLOR   = _theme.get("sep_color")
    TEXT_MAIN   = _theme.get("text_main")
    TEXT_DIM    = _theme.get("text_dim")
    TEXT_ON     = _theme.get("text_on")

_refresh_color_globals()

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

    _DEADZONE_TIME = 0.25

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
            last_fired = self._state.get("last_fired", 0.0)
            if now - last_fired < self._DEADZONE_TIME:
                return
            self._active_streams = [s for s in self._active_streams if not s.done]
            streams = self.audio.play(resolve_sfx_path(file), volume, loop=False)
            self._active_streams.extend(streams)
            self._state["fired"]      = True
            self._state["last_fired"] = now

def _browse() -> str:
    return filedialog.askopenfilename(
        title="Select audio file",
        filetypes=[("Audio", "*.wav *.ogg *.mp3 *.flac"), ("All", "*.*")],
    )

def _browse_folder() -> str:
    path = filedialog.askdirectory(title="Select audio folder")
    if path:
        return f"{FOLDER_PREFIX}{path}"
    return ""

def _pick_sfx(var: tk.StringVar):
    win = tk.Toplevel()
    win.title("Pick audio source")
    win.resizable(False, False)
    win.grab_set()
    ttk.Label(win, text="Choose audio source:",
              font=("Consolas", 9)).pack(padx=16, pady=(12, 4))
    cur = var.get()
    if cur:
        ttk.Label(win, text=_display_path(cur), foreground=ACCENT,
                  font=("Consolas", 8), wraplength=260).pack(padx=16, pady=(0, 6))

    def _use_file():
        win.destroy()
        result = _browse()
        if result:
            var.set(result)

    def _use_folder():
        win.destroy()
        result = _browse_folder()
        if result:
            var.set(result)

    def _clear():
        win.destroy()
        var.set("")

    btn_frame = ttk.Frame(win)
    btn_frame.pack(padx=16, pady=(0, 12))
    ttk.Button(btn_frame, text="📄 File",   command=_use_file,   width=11).pack(side="left", padx=3)
    ttk.Button(btn_frame, text="📁 Folder", command=_use_folder, width=11).pack(side="left", padx=3)
    ttk.Button(btn_frame, text="✕ Clear",  command=_clear,      width=11).pack(side="left", padx=3)

def _display_path(val: str) -> str:
    if not val:
        return ""
    if val.startswith(FOLDER_PREFIX):
        return f"[folder] {Path(val[len(FOLDER_PREFIX):]).name}/"
    return Path(val).name

def _expand_for_preload(vals: list[str]) -> list[str]:
    out: list[str] = []
    exts = {".wav", ".ogg", ".mp3", ".flac"}
    for v in vals:
        if not v:
            continue
        if v.startswith(FOLDER_PREFIX):
            folder = Path(v[len(FOLDER_PREFIX):])
            if folder.is_dir():
                out.extend(str(p) for p in folder.iterdir()
                           if p.suffix.lower() in exts and p.is_file())
        else:
            out.append(v)
    return out

def _editable_value(parent: tk.Widget, var, lo: float, hi: float,
                    unit: str = "", width: int = 7, fmt: str = "{:.2f}",
                    is_int: bool = False, on_commit=None) -> ttk.Entry:
    entry = ttk.Entry(parent, width=width, font=("Segoe UI Mono", 8),
                      justify="center")
    state = {"suppress": False}

    def _refresh(*_):
        if state["suppress"]:
            return
        try:
            val = var.get()
        except Exception:
            return
        text = f"{int(round(val))}{unit}" if is_int else f"{fmt.format(val)}{unit}"
        cur = entry.get()
        if cur != text:
            entry.delete(0, "end")
            entry.insert(0, text)

    def _commit(*_):
        raw = entry.get().strip()
        if unit and raw.endswith(unit):
            raw = raw[: -len(unit)].strip()
        try:
            val = float(raw) if raw else var.get()
        except (ValueError, tk.TclError):
            _refresh()
            return
        val = max(lo, min(hi, val))
        state["suppress"] = True
        try:
            var.set(int(round(val)) if is_int else val)
        except tk.TclError:
            pass
        state["suppress"] = False
        _refresh()
        if on_commit:
            on_commit(val)

    entry.bind("<Return>", _commit)
    entry.bind("<FocusOut>", _commit)
    var.trace_add("write", _refresh)
    _refresh()
    return entry

def _slider_row(parent: tk.Widget, label: str, var: tk.DoubleVar,
                lo: float, hi: float, width: int = 190, unit: str = "") -> ttk.Frame:
    row = ttk.Frame(parent)
    row.pack(fill="x", padx=4, pady=1)
    ttk.Label(row, text=label, width=10, anchor="e",
              foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
    ttk.Scale(row, variable=var, from_=lo, to=hi,
              orient="horizontal", length=width).pack(side="left", padx=4)
    _editable_value(row, var, lo, hi, unit=unit, width=8).pack(side="left")
    return row

def _section_label(parent: tk.Widget, text: str):
    f = tk.Frame(parent, bg=_theme.get("bg_base"))
    f.pack(fill="x", padx=6, pady=(10, 2))

    tk.Frame(f, bg=_theme.get("accent"), width=3).pack(
        side="left", fill="y", padx=(0, 6))

    tk.Label(f, text=text.upper(),
             font=("Consolas", 7, "bold"),
             bg=_theme.get("bg_base"),
             fg=_theme.get("text_dim")).pack(side="left")

    tk.Frame(f, bg=_theme.get("sep_color"), height=1).pack(
        side="left", fill="x", expand=True, padx=(8, 0))

def _info_button(parent: tk.Widget, text: str, side: str = "right"):
    lbl = tk.Label(parent, text="ⓘ", bg=_theme.get("bg_header"),
                   fg=_theme.get("text_dim"),
                   font=("Consolas", 8), cursor="hand2")
    lbl.pack(side=side, padx=(2, 0))
    _theme.attach_tooltip(lbl, text)
    return lbl

def _collapsible_section(parent: tk.Widget, title: str,
                          expanded: bool = True) -> tuple[tk.Frame, tk.Frame]:

    outer = tk.Frame(parent, bg=_theme.get("bg_base"))
    outer.pack(fill="x", padx=4, pady=(8, 2))

    state = {"open": expanded}

    hdr = tk.Frame(outer, bg=_theme.get("bg_section"), cursor="hand2")
    hdr.pack(fill="x")

    accent_bar = tk.Frame(hdr, bg=_theme.get("accent"), width=3)
    accent_bar.pack(side="left", fill="y")

    arrow_var = tk.StringVar(value="▾" if expanded else "▸")
    arrow_lbl = tk.Label(hdr, textvariable=arrow_var,
                         bg=_theme.get("bg_section"),
                         fg=_theme.get("accent"),
                         font=("Consolas", 9, "bold"), padx=6)
    arrow_lbl.pack(side="left")

    title_lbl = tk.Label(hdr, text=title.upper(),
                         bg=_theme.get("bg_section"),
                         fg=_theme.get("text_main"),
                         font=("Consolas", 8, "bold"), pady=5)
    title_lbl.pack(side="left")

    sep = tk.Frame(outer, bg=_theme.get("sep_color"), height=1)
    sep.pack(fill="x")

    body = tk.Frame(outer, bg=_theme.get("bg_base"))
    if expanded:
        body.pack(fill="x")

    def _toggle(_event=None):
        state["open"] = not state["open"]
        if state["open"]:
            body.pack(fill="x")
            arrow_var.set("▾")
        else:
            body.pack_forget()
            arrow_var.set("▸")

    for w in (hdr, accent_bar, arrow_lbl, title_lbl):
        w.bind("<Button-1>", _toggle)

    return hdr, body

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
            "Volume  —  how loud this sound is. Stacks with Master Volume in the header."
        ))

        r0 = ttk.Frame(lf)
        r0.pack(fill="x", padx=4, pady=3)
        fv = tk.StringVar(value=defaults.get("file", ""))
        self.vars["file"] = fv
        ttk.Entry(r0, textvariable=fv, width=38).pack(side="left")
        ttk.Button(r0, text="BROWSE",
                   command=lambda: _pick_sfx(fv)).pack(side="left", padx=3)

        vel_var = tk.DoubleVar(value=defaults.get("vel_threshold", 1.0))
        self.vars["vel_threshold"] = vel_var
        _slider_row(lf, "Vel ≥", vel_var, 0.0, 15.0, unit=" m/s")

        tw_var = tk.DoubleVar(value=defaults.get("time_window", 0.3))
        self.vars["time_window"] = tw_var
        _slider_row(lf, "Window", tw_var, 0.05, 2.0, unit=" s")

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

class SwingFrame:

    _FIXED_LOW = 0.15

    def __init__(self, parent: tk.Widget, swing_data: dict):
        self._tier_frame: TierFrame | None = None
        self._build(parent, swing_data)

    def _build(self, parent: tk.Widget, swing_data: dict):
        sf = ttk.LabelFrame(parent, text="Swing / Velocity SFX")
        sf.pack(fill="x", padx=4, pady=3)

        _info_button(sf, (
            "Plays a sound when your hand or head crosses the velocity threshold.\n\n"
            "A built-in 0.15 m/s noise gate filters idle jitter. "
            "A 250 ms deadzone after each fire prevents double-triggers.\n\n"
            "Vel ≥  —  speed needed before the SFX fires.\n"
            "Window  —  time in seconds the peak speed is tracked over. 0.3s is a good start.\n"
            "Volume  —  stacks with Master Volume."
        ))

        tier_data = swing_data.get("tier", {})
        self._tier_frame = TierFrame(sf, tier_data)

    def to_dict(self) -> dict:
        return {
            "low_threshold": self._FIXED_LOW,
            "tier": self._tier_frame.to_dict() if self._tier_frame else {},
        }

    def load_dict(self, d: dict):
        if self._tier_frame:
            self._tier_frame.load_dict(d.get("tier", {}))

class HandFrame:

    def __init__(self, parent: tk.Widget, hand: str, has_buttons: bool = True,
                 combo_frame: "ComboFrame | None" = None):
        self.hand         = hand
        self._has_buttons = has_buttons
        self.vars: dict[str, tk.Variable] = {}
        self._swing_frame: SwingFrame | None = None

        self._combo_frame: "ComboFrame | None" = combo_frame
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
            "Leave a field blank to play nothing. "
            "The three dots open a file browser."
        ))

        hdr = ttk.Frame(bf)
        hdr.pack(fill="x", padx=4, pady=(3, 0))
        ttk.Label(hdr, text="",        width=8).pack(side="left")
        ttk.Label(hdr, text="Press",   width=28, foreground=TEXT_DIM,
                  font=("Consolas", 8)).pack(side="left")
        ttk.Label(hdr, text="Release", width=28, foreground=TEXT_DIM,
                  font=("Consolas", 8)).pack(side="left")
        ttk.Label(hdr, text="Vol",     foreground=TEXT_DIM,
                  font=("Consolas", 8)).pack(side="left", padx=(6, 0))

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
        row.pack(fill="x", padx=4, pady=1)
        ttk.Label(row, text=label, width=8,
                  font=("Consolas", 8, "bold")).pack(side="left")
        ttk.Entry(row, textvariable=press_var, width=22).pack(side="left")
        ttk.Button(row, text="…", width=2,
                   command=lambda: _pick_sfx(press_var)
                   ).pack(side="left", padx=(1, 4))
        ttk.Entry(row, textvariable=rel_var, width=22).pack(side="left")
        ttk.Button(row, text="…", width=2,
                   command=lambda: _pick_sfx(rel_var)
                   ).pack(side="left", padx=(1, 4))
        ttk.Scale(row, variable=vol_var, from_=0.0, to=1.0,
                  orient="horizontal", length=65).pack(side="left", padx=(2, 0))
        _editable_value(row, vol_var, 0.0, 1.0, width=5).pack(side="left", padx=(2, 0))

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

    def get_combo_cfg(self) -> list[dict]:

        if self._combo_frame:
            return self._combo_frame.to_list()
        return [prof._default_combo_slot() for _ in range(3)]

    def to_dict(self) -> dict:
        d: dict = {"swing": self.get_swing_cfg()}
        if self._has_buttons:
            d["buttons"] = {b: self.get_button_cfg(b) for b in BUTTON_LABELS}
            d["combos"]  = self.get_combo_cfg()
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
            if self._combo_frame:
                self._combo_frame.load_list(d.get("combos", []))

class ComboFrame:

    _BTN_IDS    = list(prof.BUTTON_IDS)
    _BTN_LABELS = {"left":  ["Tri", "Grp", "X",  "Y",  "Stk"],
                   "right": ["Tri", "Grp", "A",  "B",  "Stk"]}
    _HANDS      = [("left", "L"), ("right", "R")]

    _C_REM  = 0
    _C_HL   = (1, 7)
    _C_CB   = (range(2, 7), range(8, 13))
    _C_FILE = 13
    _C_BROW = 14
    _C_VOL  = 15

    def __init__(self, parent: tk.Widget):
        self._slots: list[dict] = []
        self._lf: ttk.LabelFrame | None = None
        self._slots_frame: ttk.Frame | None = None
        self._build(parent)

    def _build(self, parent: tk.Widget):
        self._lf = ttk.LabelFrame(parent, text="Button Combos")
        self._lf.pack(anchor="center", padx=4, pady=3)

        top = ttk.Frame(self._lf)
        top.pack(fill="x")
        _info_button(top, (
            "Play a sound when buttons are held at the same time.\n\n"
            "Each combo has L and R columns — tick buttons on either or both "
            "hands. When all ticked buttons are pressed simultaneously the combo "
            "SFX fires — any individual button press sounds for those buttons "
            "are suppressed and stopped.\n\n"
            "Leave the file blank to disable a slot. Vol stacks with Master Volume."
        ))

        self._grid = ttk.Frame(self._lf)
        self._grid.pack(anchor="center", pady=(2, 3))
        self._slots_frame = self._grid
        self._next_row = 0
        self._build_header()

        ctrl = ttk.Frame(self._lf)
        ctrl.pack(anchor="center", pady=(0, 3))
        ttk.Button(ctrl, text="+ Add Combo", width=12,
                   command=self._add_slot_ui).pack(side="left")

        self._add_slot_ui()

    def _build_header(self):
        g = self._grid
        r = self._next_row
        self._next_row += 1

        ttk.Label(g, text="", width=3).grid(row=r, column=self._C_REM, padx=(0, 2))
        for hi, (hand, hl) in enumerate(self._HANDS):
            ttk.Label(g, text=hl, foreground=TEXT_DIM,
                      font=("Consolas", 8, "bold"), width=2, anchor="center"
                      ).grid(row=r, column=self._C_HL[hi], padx=(6, 0))
            for ci, bl in enumerate(self._BTN_LABELS[hand]):
                ttk.Label(g, text=bl, foreground=TEXT_DIM,
                          font=("Consolas", 7), width=3, anchor="center"
                          ).grid(row=r, column=list(self._C_CB[hi])[ci], padx=1)
        ttk.Label(g, text="File", foreground=TEXT_DIM,
                  font=("Consolas", 8), width=14, anchor="w"
                  ).grid(row=r, column=self._C_FILE, padx=(8, 0))
        ttk.Label(g, text="", width=2).grid(row=r, column=self._C_BROW)
        ttk.Label(g, text="Vol", foreground=TEXT_DIM,
                  font=("Consolas", 8)).grid(row=r, column=self._C_VOL, padx=(4, 0))

    def _add_slot_ui(self, data: dict | None = None):
        g   = self._grid
        r   = self._next_row
        self._next_row += 1

        g.grid_rowconfigure(r, pad=2)

        rem_btn = ttk.Button(g, text="−", width=2)
        rem_btn.grid(row=r, column=self._C_REM, padx=(0, 2))

        btn_vars: dict[tuple[str, str], tk.BooleanVar] = {}
        for hi, (hand, hl) in enumerate(self._HANDS):
            ttk.Label(g, text=hl, foreground=TEXT_DIM,
                      font=("Consolas", 8, "bold"), width=2, anchor="center"
                      ).grid(row=r, column=self._C_HL[hi], padx=(6, 0))
            for ci, btn_id in enumerate(self._BTN_IDS):
                v = tk.BooleanVar(value=False)
                btn_vars[(hand, btn_id)] = v
                ttk.Checkbutton(g, variable=v
                                ).grid(row=r, column=list(self._C_CB[hi])[ci], padx=1)

        file_var = tk.StringVar()
        ttk.Entry(g, textvariable=file_var, width=16
                  ).grid(row=r, column=self._C_FILE, padx=(8, 1))
        ttk.Button(g, text="…", width=2,
                   command=lambda fv=file_var: _pick_sfx(fv)
                   ).grid(row=r, column=self._C_BROW, padx=(0, 4))

        vol_var = tk.DoubleVar(value=1.0)
        vol_cell = ttk.Frame(g)
        vol_cell.grid(row=r, column=self._C_VOL)
        ttk.Scale(vol_cell, variable=vol_var, from_=0.0, to=1.0,
                  orient="horizontal", length=55).pack(side="left")
        _editable_value(vol_cell, vol_var, 0.0, 1.0, width=5).pack(side="left", padx=(2, 0))

        slot = {"grid_row": r, "btn_vars": btn_vars,
                "file_var": file_var, "vol_var": vol_var}
        rem_btn.configure(command=lambda s=slot: self._remove_slot(s))
        self._slots.append(slot)

        if data:
            raw  = data.get("buttons", [])
            btns = set()
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) == 2:
                    btns.add((item[0], item[1]))
                else:
                    btns.add(("left", item))
            for key, v in btn_vars.items():
                v.set(key in btns)
            file_var.set(data.get("file", ""))
            vol_var.set(data.get("volume", 1.0))

        self._refresh_remove_buttons()

    def _remove_slot(self, slot: dict):
        if len(self._slots) <= 1:
            return
        r = slot["grid_row"]

        for w in self._grid.grid_slaves(row=r):
            w.destroy()
        if slot in self._slots:
            self._slots.remove(slot)
        self._refresh_remove_buttons()

    def _refresh_remove_buttons(self):
        can = len(self._slots) > 1
        for s in self._slots:
            r = s["grid_row"]
            for w in self._grid.grid_slaves(row=r):
                if isinstance(w, ttk.Button) and w.cget("text") == "−":
                    w.configure(state="normal" if can else "disabled")

    def to_list(self) -> list[dict]:
        return [{"buttons": [[h, b] for (h, b), v in s["btn_vars"].items() if v.get()],
                 "file":    s["file_var"].get(),
                 "volume":  s["vol_var"].get()} for s in self._slots]

    def load_list(self, data: list[dict]):

        for w in self._grid.grid_slaves():
            info = w.grid_info()
            if info and int(info.get("row", 0)) > 0:
                w.destroy()
        self._slots.clear()
        self._next_row = 1
        for sd in (data or []):
            self._add_slot_ui(data=sd)
        if not self._slots:
            self._add_slot_ui()

    def preload_files(self) -> list[str]:
        return _expand_for_preload([s["file_var"].get() for s in self._slots])

class MusicStrip:

    def __init__(self, parent: tk.Widget, audio: AudioEngine, app: "App"):
        self._audio          = audio
        self._app            = app
        self.vars: dict[str, tk.Variable] = {}
        self._streams: list  = []
        self._shuffle_queue: list = []
        self._current_file   = ""
        self._build(parent)

    def _build(self, parent: tk.Widget):
        lf = ttk.LabelFrame(parent, text="Music", style="Header.TLabelframe")
        lf.pack(side="left", fill="both", padx=3, pady=2)

        r1 = ttk.Frame(lf, style="Header.TFrame")
        r1.pack(fill="x", padx=4, pady=(2, 1))

        file_var = tk.StringVar()
        self.vars["file"] = file_var
        ttk.Entry(r1, textvariable=file_var, width=30).pack(side="left")
        ttk.Button(r1, text="…", width=2,
                   command=lambda: _pick_sfx(file_var)
                   ).pack(side="left", padx=(2, 6))

        loop_var = tk.BooleanVar(value=True)
        self.vars["loop"] = loop_var
        ttk.Checkbutton(r1, text="Loop", variable=loop_var).pack(side="left", padx=(0, 4))

        shuffle_var = tk.BooleanVar(value=False)
        self.vars["shuffle"] = shuffle_var
        ttk.Checkbutton(r1, text="Shuffle", variable=shuffle_var).pack(side="left", padx=(0, 6))

        def _update_shuffle_state(*_):
            val = file_var.get()
            state = "normal" if val.startswith(FOLDER_PREFIX) else "disabled"
            shuffle_cb.config(state=state)

        shuffle_cb = r1.winfo_children()[-1]
        file_var.trace_add("write", _update_shuffle_state)
        _update_shuffle_state()

        self._play_btn = ttk.Button(r1, text="▶ Play",  command=self._play,
                                    style="Accent.TButton", width=7)
        self._stop_btn = ttk.Button(r1, text="■ Stop",  command=self._stop, width=7)
        self._play_btn.pack(side="left", padx=(0, 2))
        self._stop_btn.pack(side="left", padx=(0, 6))

        self._status_lbl = ttk.Label(r1, text="■ IDLE", foreground=TEXT_DIM,
                                     font=("Consolas", 8), style="Header.TLabel")
        self._status_lbl.pack(side="left")

        r2 = ttk.Frame(lf, style="Header.TFrame")
        r2.pack(fill="x", padx=4, pady=(0, 3))

        vol1_var = tk.DoubleVar(value=1.0)
        vol2_var = tk.DoubleVar(value=1.0)
        self.vars["volume1"] = vol1_var
        self.vars["volume2"] = vol2_var

        for label, var in [("Out 1", vol1_var), ("Out 2", vol2_var)]:
            ttk.Label(r2, text=label, font=("Consolas", 8),
                      foreground=TEXT_DIM, style="Header.TLabel").pack(side="left")
            ttk.Scale(r2, variable=var, from_=0.0, to=1.0,
                      orient="horizontal", length=80).pack(side="left", padx=(2, 8))

    def _play(self):
        alive = [v for v in self._streams if not v.done]
        if alive:
            return

        val = self.vars["file"].get()
        if not val:
            self._status_lbl.config(text="── NO FILE ──", foreground=_theme.get("text_error"))
            return

        is_folder = val.startswith(FOLDER_PREFIX)
        shuffle   = is_folder and self.vars.get("shuffle", tk.BooleanVar(value=False)).get()

        if is_folder:
            if shuffle:
                if not self._shuffle_queue:
                    self._shuffle_queue = self._folder_files(val)
                    import random as _r; _r.shuffle(self._shuffle_queue)
                file = self._shuffle_queue.pop(0) if self._shuffle_queue else ""
            else:
                file = resolve_sfx_path(val)
        else:
            file = val

        if not file:
            self._status_lbl.config(text="── EMPTY FOLDER ──", foreground=_theme.get("text_error"))
            return

        loop = self.vars["loop"].get()
        if is_folder:
            loop = False
        vol1 = self.vars["volume1"].get()
        vol2 = self.vars["volume2"].get()
        dev1 = self._audio.device1
        dev2 = self._audio.device2

        try:
            data, sr = self._audio._load(file)
        except Exception as e:
            self._status_lbl.config(text=f"Error: {e}", foreground=_theme.get("text_error"))
            return

        from audio import _Voice
        self._streams = []

        seen: set[int] = set()
        for dev, vol in ((dev1, vol1), (dev2, vol2)):
            if dev is None or dev in seen:
                continue
            seen.add(dev)
            d_ch  = self._audio._adapt_channels(data, dev)
            mixer = self._audio._get_mixer(dev, sr, d_ch.shape[1])
            if mixer is None:
                continue
            v = _Voice(d_ch, vol, loop)
            mixer.add_voice(v)
            self._streams.append(v)

        if self._streams:
            fname  = Path(file).name
            suffix = " ↻" if loop else (" 🔀" if shuffle else "")
            self._status_lbl.config(
                text=f"▶ {fname}{suffix}", foreground=TEXT_ON)
            self._poll_done()
        else:
            self._status_lbl.config(text="No output device set", foreground=_theme.get("text_error"))

    @staticmethod
    def _folder_files(val: str) -> list:
        folder = Path(val[len(FOLDER_PREFIX):])
        if not folder.is_dir():
            return []
        exts = {".wav", ".ogg", ".mp3", ".flac"}
        return [str(p) for p in sorted(folder.iterdir())
                if p.suffix.lower() in exts and p.is_file()]

    def _stop(self):
        for s in self._streams:
            s.stop()
        self._streams = []
        self._status_lbl.config(text="■ IDLE", foreground=_theme.get("text_dim"))

    def _poll_done(self):
        if not self._streams:
            return
        alive = [s for s in self._streams if not s.done]
        if not alive:
            self._streams = []
            val = self.vars["file"].get()
            if val.startswith(FOLDER_PREFIX):

                try:
                    self._app.root.after(100, self._play)
                except Exception:
                    self._status_lbl.config(text="■ IDLE", foreground=_theme.get("text_dim"))
            else:
                self._status_lbl.config(text="■ IDLE", foreground=_theme.get("text_dim"))
            return
        try:
            self._app.root.after(500, self._poll_done)
        except Exception:
            pass

    def to_dict(self) -> dict:
        return {
            "file":    self.vars["file"].get(),
            "volume1": self.vars["volume1"].get(),
            "volume2": self.vars["volume2"].get(),
            "loop":    self.vars["loop"].get(),
            "shuffle": self.vars.get("shuffle", tk.BooleanVar(value=False)).get(),
        }

    def load_dict(self, d: dict):
        self.vars["file"].set(d.get("file", ""))
        self.vars["volume1"].set(d.get("volume1", 1.0))
        self.vars["volume2"].set(d.get("volume2", 1.0))
        self.vars["loop"].set(d.get("loop", True))
        if "shuffle" in self.vars:
            self.vars["shuffle"].set(d.get("shuffle", False))
        self._shuffle_queue = []

    def preload_files(self) -> list[str]:
        val = self.vars["file"].get()
        if not val:
            return []
        if val.startswith(FOLDER_PREFIX):
            return self._folder_files(val)
        return [val]

def _osc_mode_row(parent: tk.Widget,
                  mode_var: tk.StringVar,
                  val_a_var,
                  val_b_var,
                  float_a_var: tk.StringVar,
                  float_b_var: tk.StringVar,
                  refresh_fn) -> tuple[tk.Widget, tk.Widget, tk.Widget]:
    mode_row = ttk.Frame(parent)
    mode_row.pack(fill="x", padx=4, pady=(2, 0))
    ttk.Label(mode_row, text="Mode", width=10, anchor="e",
              foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
    for text, val in [("Bool", "bool"), ("Int", "int"), ("Float", "float")]:
        ttk.Radiobutton(mode_row, text=text, variable=mode_var,
                        value=val, command=refresh_fn
                        ).pack(side="left", padx=(3, 6))

    int_row = ttk.Frame(parent)
    ttk.Label(int_row, text="Values", width=10, anchor="e",
              foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
    ttk.Label(int_row, text="ON:").pack(side="left", padx=(3, 1))
    ttk.Spinbox(int_row, textvariable=val_a_var,
                from_=-9999, to=9999, width=6).pack(side="left", padx=(0, 6))
    ttk.Label(int_row, text="OFF:").pack(side="left", padx=(0, 1))
    ttk.Spinbox(int_row, textvariable=val_b_var,
                from_=-9999, to=9999, width=6).pack(side="left")

    float_row = ttk.Frame(parent)
    ttk.Label(float_row, text="Values", width=10, anchor="e",
              foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
    ttk.Label(float_row, text="ON:").pack(side="left", padx=(3, 1))
    ttk.Entry(float_row, textvariable=float_a_var, width=8).pack(side="left", padx=(0, 6))
    ttk.Label(float_row, text="OFF:").pack(side="left", padx=(0, 1))
    ttk.Entry(float_row, textvariable=float_b_var, width=8).pack(side="left")
    ttk.Label(float_row, text="  (e.g. 0.75, -1.0)",
              foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")

    return int_row, float_row

def _osc_send_typed(osc: "OSCLink", param: str, mode: str,
                    val_on, val_off, float_on: str, float_off: str, state: bool,
                    invert: bool = False):

    if not param or not osc.client:
        return
    if mode == "int":
        try:
            value = int(val_on if state else val_off)
        except (ValueError, tk.TclError):
            value = 1 if state else 0
        osc.send(param, value)
    elif mode == "float":
        try:
            value = float((float_on if state else float_off) or ("1.0" if state else "0.0"))
        except ValueError:
            value = 1.0 if state else 0.0
        osc.send(param, value)
    else:
        osc.send(param, (not state) if invert else state)

class _OSCComboSlotWidget:

    def __init__(self, parent: tk.Widget, idx: int, osc_frame: "OSCFrame"):
        self._osc_frame = osc_frame
        self.bind = ComboBind(buttons=[], param="")
        self.btn_vars: dict[tuple[str, str], tk.BooleanVar] = {}
        self._outer: tk.Widget | None = None
        self._build(parent, idx)

    _BTN_IDS    = list(BUTTON_LABELS.keys())
    _BTN_LABELS = {"left":  ["Tri", "Grp", "X",  "Y",  "Stk"],
                   "right": ["Tri", "Grp", "A",  "B",  "Stk"]}
    _C_REM = 0
    _C_HL  = (1, 7)
    _C_CB  = (range(2, 7), range(8, 13))

    def _build(self, parent: tk.Widget, idx: int):
        outer = ttk.Frame(parent)
        outer.pack(anchor="center", padx=2, pady=3)
        self._outer = outer

        cb_row = ttk.Frame(outer)
        cb_row.pack(anchor="center")

        self._rem_btn = ttk.Button(cb_row, text="−", width=2,
                                   command=self._remove)
        self._rem_btn.grid(row=0, column=self._C_REM, padx=(0, 2))

        for hi, (hand, hl) in enumerate((("left", "L"), ("right", "R"))):
            ttk.Label(cb_row, text=hl, foreground=TEXT_DIM,
                      font=("Consolas", 8, "bold"), width=2, anchor="center"
                      ).grid(row=0, column=self._C_HL[hi], padx=(6, 0))
            for ci, btn_id in enumerate(self._BTN_IDS):
                v = tk.BooleanVar(value=False)
                self.btn_vars[(hand, btn_id)] = v
                ttk.Checkbutton(cb_row, variable=v,
                                command=self._on_check_change
                                ).grid(row=0, column=list(self._C_CB[hi])[ci], padx=1)

        bind_row = ttk.Frame(outer)
        bind_row.pack(fill="x", pady=(2, 0))
        ttk.Button(bind_row, text="Bind →", width=8,
                   command=self._bind_param).pack(side="left")
        self._status_lbl = ttk.Label(bind_row, text="── no param ──",
                                     foreground=TEXT_DIM, font=("Consolas", 8))
        self._status_lbl.pack(side="left", padx=6)
        self._state_lbl = ttk.Label(bind_row, text="", font=("Consolas", 8, "bold"))
        self._state_lbl.pack(side="left", padx=4)
        self._invert_var = tk.BooleanVar(value=self.bind.invert)
        ttk.Checkbutton(bind_row, text="Invert", variable=self._invert_var,
                        command=self._on_invert_change).pack(side="left", padx=(4, 0))
        ttk.Button(bind_row, text="✕", width=2,
                   command=self._clear).pack(side="right", padx=4)

        self._mode_row = ttk.Frame(outer)
        self._mode_row.pack(fill="x", pady=(1, 2))
        ttk.Label(self._mode_row, text="Trigger:", foreground=TEXT_DIM,
                  font=("Consolas", 7)).pack(side="left")
        self._tmode_var = tk.StringVar(value=self.bind.trigger_mode)
        for label, val in [("Toggle", "toggle"), ("Hold", "hold"), ("Delay off", "delay")]:
            ttk.Radiobutton(self._mode_row, text=label, variable=self._tmode_var, value=val,
                            command=self._on_trigger_mode_change
                            ).pack(side="left", padx=(4, 0))

        self._values_row = ttk.Frame(outer)
        self._values_row.pack(fill="x", pady=(0, 2))
        ttk.Label(self._values_row, text="Values:", foreground=TEXT_DIM,
                  font=("Consolas", 7)).pack(side="left")

        self._val_a_int_var   = tk.IntVar(value=self.bind.val_a)
        self._val_b_int_var   = tk.IntVar(value=self.bind.val_b)
        self._val_a_float_var = tk.StringVar(value=str(self.bind.float_a))
        self._val_b_float_var = tk.StringVar(value=str(self.bind.float_b))

        self._int_vals_frame = ttk.Frame(self._values_row)
        ttk.Label(self._int_vals_frame, text="ON:", font=("Consolas", 7)).pack(side="left", padx=(3, 1))
        ttk.Spinbox(self._int_vals_frame, textvariable=self._val_a_int_var,
                    from_=-9999, to=9999, width=6).pack(side="left", padx=(0, 4))
        ttk.Label(self._int_vals_frame, text="OFF:", font=("Consolas", 7)).pack(side="left", padx=(0, 1))
        ttk.Spinbox(self._int_vals_frame, textvariable=self._val_b_int_var,
                    from_=-9999, to=9999, width=6).pack(side="left")
        self._val_a_int_var.trace_add("write", self._on_int_vals_change)
        self._val_b_int_var.trace_add("write", self._on_int_vals_change)

        self._float_vals_frame = ttk.Frame(self._values_row)
        ttk.Label(self._float_vals_frame, text="ON:", font=("Consolas", 7)).pack(side="left", padx=(3, 1))
        ttk.Entry(self._float_vals_frame, textvariable=self._val_a_float_var,
                  width=8, font=("Consolas", 7)).pack(side="left", padx=(0, 4))
        ttk.Label(self._float_vals_frame, text="OFF:", font=("Consolas", 7)).pack(side="left", padx=(0, 1))
        ttk.Entry(self._float_vals_frame, textvariable=self._val_b_float_var,
                  width=8, font=("Consolas", 7)).pack(side="left")
        self._val_a_float_var.trace_add("write", self._on_float_vals_change)
        self._val_b_float_var.trace_add("write", self._on_float_vals_change)

        self._refresh_values_row()

        ttk.Separator(outer, orient="horizontal").pack(fill="x", pady=(3, 0))

    def _remove(self):
        self._osc_frame._remove_osc_combo_slot(self)

    def _on_check_change(self):
        self.bind.buttons = [bh for bh, v in self.btn_vars.items() if v.get()]

    def _on_invert_change(self):
        self.bind.invert = self._invert_var.get()

    def _bind_param(self):
        osc_frame = self._osc_frame
        param = osc_frame._get_selected_param()
        if not param:
            self._status_lbl.config(text="Select a param first", foreground=_theme.get("text_error"))
            return
        if len(self.bind.buttons) < 2:
            self._status_lbl.config(text="Tick 2+ buttons first", foreground=_theme.get("text_error"))
            return
        last_val = osc_frame._params_cache.get(param)
        if isinstance(last_val, bool):
            mode = "bool"
        elif isinstance(last_val, int):
            mode = "int"
        elif isinstance(last_val, float):
            mode = "float"
        else:
            mode = "bool"
        self.bind.param   = param
        self.bind.mode    = mode
        self.bind.state   = False
        self.bind.int_idx = 0
        if mode == "int":
            self.bind.val_a = int(last_val) if isinstance(last_val, int) else 0
            self.bind.val_b = self.bind.val_a + 1
        elif mode == "float":
            self.bind.float_a = str(last_val) if isinstance(last_val, float) else "1.0"
            self.bind.float_b = "0.0"
        self._val_a_int_var.set(self.bind.val_a)
        self._val_b_int_var.set(self.bind.val_b)
        self._val_a_float_var.set(str(self.bind.float_a))
        self._val_b_float_var.set(str(self.bind.float_b))
        self._refresh_values_row()
        self._status_lbl.config(text=f"→ {param}", foreground=_theme.get("text_on"))
        self.refresh_state_display()
        osc_frame._reset_combo_match_tracking()

    def _clear(self):
        self.bind.param   = ""
        self.bind.buttons = []
        self.bind.mode    = "bool"
        self.bind.state   = False
        self.bind.int_idx = 0
        self.bind.invert  = False
        self._invert_var.set(False)
        for v in self.btn_vars.values():
            v.set(False)
        self._refresh_values_row()
        self._status_lbl.config(text="── no param ──", foreground=TEXT_DIM)
        self.refresh_state_display()
        self._osc_frame._reset_combo_match_tracking()

    def _on_trigger_mode_change(self):
        self.bind.trigger_mode = self._tmode_var.get()
        self._refresh_delay_widget()

    def _refresh_delay_widget(self):
        for w in self._mode_row.winfo_children():
            if getattr(w, "_is_delay_widget", False):
                w.destroy()
        if self._tmode_var.get() == "delay":
            delay_var = tk.DoubleVar(value=self.bind.delay)
            def _dv(*_, dv=delay_var):
                self.bind.delay = dv.get()
            delay_var.trace_add("write", _dv)
            f = ttk.Frame(self._mode_row)
            f._is_delay_widget = True
            ttk.Label(f, text="  off after", foreground=TEXT_DIM,
                      font=("Consolas", 7)).pack(side="left")
            ttk.Scale(f, variable=delay_var, from_=0.1, to=30.0,
                      orient="horizontal", length=80).pack(side="left", padx=2)
            _editable_value(f, delay_var, 0.1, 30.0, unit="s", width=6).pack(side="left")
            f.pack(side="left")

    def _on_int_vals_change(self, *_):
        try:
            self.bind.val_a = self._val_a_int_var.get()
        except (tk.TclError, ValueError):
            pass
        try:
            self.bind.val_b = self._val_b_int_var.get()
        except (tk.TclError, ValueError):
            pass
        self.refresh_state_display()

    def _on_float_vals_change(self, *_):
        self.bind.float_a = self._val_a_float_var.get()
        self.bind.float_b = self._val_b_float_var.get()
        self.refresh_state_display()

    def _refresh_values_row(self):
        self._int_vals_frame.pack_forget()
        self._float_vals_frame.pack_forget()
        if self.bind.mode == "int":
            self._int_vals_frame.pack(side="left")
        elif self.bind.mode == "float":
            self._float_vals_frame.pack(side="left")

    def refresh_state_display(self):
        if not self.bind.param:
            self._state_lbl.config(text="")
            return
        active = self.bind.is_active()
        self._state_lbl.config(
            text=self.bind.current_display(),
            foreground=(_theme.get("text_on") if active else TEXT_DIM))

    def refresh_from_bind(self):
        held = set(self.bind.buttons)
        for bh, v in self.btn_vars.items():
            v.set(bh in held)
        self._tmode_var.set(self.bind.trigger_mode)
        self._refresh_delay_widget()
        self._invert_var.set(self.bind.invert)
        self._val_a_int_var.set(self.bind.val_a)
        self._val_b_int_var.set(self.bind.val_b)
        self._val_a_float_var.set(str(self.bind.float_a))
        self._val_b_float_var.set(str(self.bind.float_b))
        self._refresh_values_row()
        if self.bind.param:
            self._status_lbl.config(text=f"→ {self.bind.param}", foreground=_theme.get("text_on"))
        else:
            self._status_lbl.config(text="── no param ──", foreground=TEXT_DIM)
        self.refresh_state_display()

class OSCFrame:

    def __init__(self, parent: tk.Widget, osc: OSCLink, app: "App",
                 shoulder_frame: "ShoulderFrame | None" = None,
                 pose_frame: "PoseFrame | None" = None):
        self._osc            = osc
        self._app            = app
        self._shoulder_frame = shoulder_frame
        self._pose_frame     = pose_frame
        self.binds: list[Bind] = []
        self._bind_rows: list[dict] = []
        self.combo_binds: list[ComboBind] = []
        self._combo_slot_widgets: list[_OSCComboSlotWidget] = []
        self._combo_prev_matched: set[int] = set()
        self._osc_combo_slots_frame: tk.Frame | None = None
        self._params_cache: dict[str, object] = dict(osc.params)
        self._params_dirty      = False
        self._pending_param: str | None = None
        self._pending_mode:  str = "bool"
        self._listening_for_btn = False
        self._build(parent)
        osc.on_param = self._on_osc_param
        self._app.root.after(250, self._poll_param_refresh)

    def set_shoulder_frame(self, sf: "ShoulderFrame"):
        self._shoulder_frame = sf

    def set_pose_frame(self, pf: "PoseFrame"):
        self._pose_frame = pf

    def _build(self, parent: tk.Widget):

        lf = ttk.LabelFrame(parent, text="OSC LISTENER")
        lf.pack(fill="x", padx=4, pady=(4, 2))

        _info_button(lf, (
            "Listens for VRChat's avatar parameter OSC messages on port 9001, "
            "and can send values back on port 9000 when a bound button is pressed.\n\n"
            "Bool params flip True/False each press. Int params swap between two "
            "integer values. Float params swap between two decimal values.\n\n"
            "VRChat needs OSC enabled: Action Menu → Options → OSC → Enabled."
        ))

        row = ttk.Frame(lf)
        row.pack(fill="x", padx=4, pady=3)
        self._status_lbl = ttk.Label(row, text="", foreground=TEXT_DIM)
        self._status_lbl.pack(side="left", padx=(0, 8))
        self._toggle_btn = ttk.Button(row, text="STOP", command=self._toggle_listener)
        self._toggle_btn.pack(side="left")
        self._ensure_listening()

        _section_label(parent, "Incoming Params")
        pf = ttk.Frame(parent)
        pf.pack(fill="x", padx=4, pady=2)

        fr = ttk.Frame(pf)
        fr.pack(fill="x", pady=(0, 2))
        ttk.Label(fr, text="Filter", foreground=TEXT_DIM,
                  font=("Consolas", 8)).pack(side="left", padx=(0, 4))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add("write", lambda *_: self._refresh_param_list())
        ttk.Entry(fr, textvariable=self._filter_var, width=30).pack(side="left")

        lb_f = ttk.Frame(pf)
        lb_f.pack(fill="x", pady=2)
        param_sb = ttk.Scrollbar(lb_f, orient="vertical")
        param_sb.pack(side="right", fill="y")
        self._param_lb = tk.Listbox(lb_f, height=7, exportselection=False,
                                    yscrollcommand=param_sb.set,
                                    font=("Segoe UI Mono", 8),
                                    bg=BG_FIELD, fg=TEXT_MAIN,
                                    selectbackground=_theme.get("accent"),
                                    selectforeground="#ffffff",
                                    activestyle="none",
                                    relief="flat", borderwidth=1,
                                    highlightthickness=1,
                                    highlightcolor=ACCENT,
                                    highlightbackground=SEP_COLOR)
        self._param_lb.pack(side="left", fill="both", expand=True)
        param_sb.config(command=self._param_lb.yview)
        self._param_lb.bind("<<ListboxSelect>>", self._on_param_select)
        self._param_lb.bind("<MouseWheel>", self._on_param_lb_wheel)
        self._param_lb.bind("<Button-4>", lambda e: self._on_param_lb_wheel(e, delta=120))
        self._param_lb.bind("<Button-5>", lambda e: self._on_param_lb_wheel(e, delta=-120))

        br = ttk.Frame(pf)
        br.pack(fill="x", pady=(2, 4))
        self._bind_btn = ttk.Button(br, text="Bind selected →",
                                    command=self._start_bind, style="Accent.TButton")
        self._bind_btn.pack(side="left")
        self._bind_status = ttk.Label(br, text="", foreground=TEXT_DIM,
                                      font=("Consolas", 8))
        self._bind_status.pack(side="left", padx=8)

        _section_label(parent, "Active Binds")

        _info_button(parent, (
            "Select a param above, hit \"Bind selected\", then press the "
            "controller button you want to trigger it.\n\n"
            "Bool params: flip True/False each press.\n"
            "Int params: swap between the two spinbox values.\n"
            "Float params: swap between the two text values.\n\n"
            "Trigger modes:\n"
            "  Toggle  —  flip state each press (default).\n"
            "  Hold    —  ON while button held, OFF on release.\n"
            "  Delay off  —  turns ON immediately, then auto-OFF after N seconds.\n\n"
            "Invert (bool params only) — sends the opposite of what's shown here, "
            "so the bind's own TRUE/FALSE display still tracks the button, but "
            "the param itself flips the other way.\n\n"
            "Re-binding the same param to the same button replaces the old bind."
        ), side="left")

        self._binds_body = ttk.Frame(parent)
        self._binds_body.pack(fill="x", padx=4, pady=2)

        ttk.Button(parent, text="Clear all binds",
                   command=self._clear_binds).pack(anchor="e", padx=8, pady=(0, 4))

        _, self._combos_body = _collapsible_section(parent, "OSC Button Combos", expanded=True)
        _info_button(self._combos_body, (
            "Tick 2+ buttons (either hand, or both) to form a combo, select a "
            "param above, then hit \"Bind →\".\n\n"
            "When every ticked button is held at once, the combo fires — and "
            "automatically untriggers any single bind or smaller combo whose "
            "buttons are entirely contained within it. e.g. a Trigger+Grip+A "
            "combo firing will turn off a lone Trigger bind or a Trigger+Grip "
            "combo that was active.\n\n"
            "Trigger modes work the same as single binds: Toggle / Hold / Delay off."
        ), side="left")

        osc_combo_hdr = ttk.Frame(self._combos_body)
        osc_combo_hdr.pack(anchor="center", pady=(2, 0))
        self._build_combo_header(osc_combo_hdr)

        self._osc_combo_slots_frame = ttk.Frame(self._combos_body)
        self._osc_combo_slots_frame.pack(anchor="center")

        osc_combo_ctrl = ttk.Frame(self._combos_body)
        osc_combo_ctrl.pack(anchor="center", pady=(2, 3))
        self._add_osc_combo_btn = ttk.Button(osc_combo_ctrl, text="+ Add Combo", width=12,
                                             command=self._add_osc_combo_slot)
        self._add_osc_combo_btn.pack(side="left")

        self._add_osc_combo_slot()

        _, self._shoulders_body = _collapsible_section(parent, "Shoulder Holsters", expanded=True)

        _, self._poses_body = _collapsible_section(parent, "Poses", expanded=True)

        self._refresh_param_list()
        self._refresh_binds()

    def _ensure_listening(self):
        if not self._osc.running:
            ok, err = self._osc.start()
            if not ok:
                self._status_lbl.config(text=f"Off — {err}", foreground=_theme.get("text_error"))
                self._toggle_btn.config(text="Retry", command=self._ensure_listening)
                return
        self._status_lbl.config(text="Listening :9001  →  sending :9000",
                                 foreground=TEXT_ON)
        self._toggle_btn.config(text="STOP", command=self._toggle_listener)

    def _toggle_listener(self):
        if self._osc.running:
            self._osc.stop()
            self._status_lbl.config(text="■ IDLE", foreground=_theme.get("text_dim"))
            self._toggle_btn.config(text="START", command=self._ensure_listening)
        else:
            self._ensure_listening()

    def _on_osc_param(self, name: str, value):
        self._params_cache[name] = value
        self._params_dirty = True

    def _poll_param_refresh(self):
        try:
            if not self._param_lb.winfo_exists():
                return
        except tk.TclError:
            return
        if self._params_dirty:
            self._params_dirty = False
            self._refresh_param_list()
        self._app.root.after(250, self._poll_param_refresh)

    def _refresh_param_list(self):
        filt         = self._filter_var.get().lower()
        sel_before   = self._get_selected_param()
        yview_before = self._param_lb.yview()[0]
        self._param_lb.delete(0, "end")
        for name, val in sorted(self._params_cache.items()):
            if filt and filt not in name.lower():
                continue
            self._param_lb.insert("end", f"{name}  =  {val}")
        if sel_before:
            for i in range(self._param_lb.size()):
                if self._param_lb.get(i).split("  =  ")[0] == sel_before:
                    self._param_lb.selection_set(i)
                    break
        self._param_lb.yview_moveto(yview_before)

    def _get_selected_param(self) -> str | None:
        sel = self._param_lb.curselection()
        if not sel:
            return None
        return self._param_lb.get(sel[0]).split("  =  ")[0]

    def _on_param_select(self, _event=None):
        p = self._get_selected_param()
        if p:
            self._bind_status.config(text=f'Selected: "{p}"', foreground=_theme.get("text_dim"))

    def _on_param_lb_wheel(self, event, delta: int | None = None):
        d = delta if delta is not None else event.delta
        self._param_lb.yview_scroll(int(-1 * (d / 120)), "units")
        return "break"

    def _start_bind(self):
        param = self._get_selected_param()
        if not param:
            self._bind_status.config(text="Select a param first", foreground=_theme.get("text_error"))
            return
        if not self._app.ovr.connected:
            self._bind_status.config(text="Connect OVR first (top bar)", foreground=_theme.get("text_error"))
            return

        last_val = self._params_cache.get(param)
        if isinstance(last_val, bool):
            self._pending_mode = "bool"
        elif isinstance(last_val, int):
            self._pending_mode = "int"
        elif isinstance(last_val, float):
            self._pending_mode = "float"
        else:
            self._pending_mode = "bool"

        self._pending_param     = param
        self._listening_for_btn = True
        type_hint = {"bool": "bool toggle", "int": "int swap", "float": "float swap"
                     }.get(self._pending_mode, "bool toggle")
        self._bind_status.config(
            text=f'Waiting for button… [{type_hint}]', foreground=ACCENT)

    def handle_button_press(self, hand: str, btn: str):

        if self._listening_for_btn and self._pending_param:
            param = self._pending_param
            mode  = self._pending_mode
            last_val = self._params_cache.get(param)
            if mode == "int":
                val_a = int(last_val) if isinstance(last_val, int) else 0
                val_b = val_a + 1
                b = Bind(hand, btn, param, mode="int", val_a=val_a, val_b=val_b)
            elif mode == "float":
                b = Bind(hand, btn, param, mode="float",
                         float_a=str(last_val) if isinstance(last_val, float) else "1.0",
                         float_b="0.0")
            else:
                b = Bind(hand, btn, param, mode="bool")
            self.binds = [x for x in self.binds
                          if not (x.hand == hand and x.btn == btn and x.param == param)]
            self.binds.append(b)
            self._pending_param     = None
            self._listening_for_btn = False
            self._bind_status.config(text=f"Bound {b.label()} → {b.param}", foreground=_theme.get("text_on"))
            self._refresh_binds()
        else:
            fired = False
            for b in self.binds:
                if b.hand == hand and b.btn == btn:
                    b.fire(self._osc)
                    fired = True
            combo_changed = self._evaluate_combos()
            if fired or combo_changed:
                self._update_bind_states()
            if combo_changed:
                self._refresh_combo_displays()

    def handle_button_release(self, hand: str, btn: str):
        released = False
        for b in self.binds:
            if b.hand == hand and b.btn == btn:
                b.release(self._osc)
                released = True
        combo_changed = self._evaluate_combos()
        if released or combo_changed:
            self._update_bind_states()
        if combo_changed:
            self._refresh_combo_displays()

    def _global_held(self) -> set[tuple[str, str]]:
        held: set[tuple[str, str]] = set()
        for hand, btns in getattr(self._app, "_held_buttons", {}).items():
            for b in btns:
                held.add((hand, b))
        return held

    def _build_combo_header(self, hdr: ttk.Frame):
        btn_labels = {"left":  ["Tri", "Grp", "X",  "Y",  "Stk"],
                      "right": ["Tri", "Grp", "A",  "B",  "Stk"]}
        c_rem  = _OSCComboSlotWidget._C_REM
        c_hl   = _OSCComboSlotWidget._C_HL
        c_cb   = _OSCComboSlotWidget._C_CB
        ttk.Label(hdr, text="", width=3).grid(row=0, column=c_rem)
        for hi, (hand, hl) in enumerate((("left", "L"), ("right", "R"))):
            ttk.Label(hdr, text=hl, foreground=TEXT_DIM,
                      font=("Consolas", 8, "bold"), width=2, anchor="center"
                      ).grid(row=0, column=c_hl[hi], padx=(6, 0))
            for ci, bl in enumerate(btn_labels[hand]):
                ttk.Label(hdr, text=bl, foreground=TEXT_DIM,
                          font=("Consolas", 7), width=3, anchor="center"
                          ).grid(row=0, column=list(c_cb[hi])[ci], padx=1)

    def _add_osc_combo_slot(self, bind_data: "ComboBind | None" = None):
        idx = len(self._combo_slot_widgets)
        w = _OSCComboSlotWidget(self._osc_combo_slots_frame, idx, self)
        if bind_data is not None:
            w.bind = bind_data
            w.refresh_from_bind()
        self._combo_slot_widgets.append(w)
        self.combo_binds.append(w.bind)
        self._refresh_osc_combo_remove_buttons()
        self._reset_combo_match_tracking()

    def _remove_osc_combo_slot(self, widget: "_OSCComboSlotWidget"):
        if len(self._combo_slot_widgets) <= 1:
            return
        idx = self._combo_slot_widgets.index(widget)
        self._combo_slot_widgets.pop(idx)
        self.combo_binds.pop(idx)
        if widget._outer:
            widget._outer.destroy()

        for i, w in enumerate(self._combo_slot_widgets):
            pass
        self._refresh_osc_combo_remove_buttons()
        self._reset_combo_match_tracking()

    def _refresh_osc_combo_remove_buttons(self):
        can_remove = len(self._combo_slot_widgets) > 1
        for w in self._combo_slot_widgets:
            if hasattr(w, "_rem_btn"):
                w._rem_btn.configure(state="normal" if can_remove else "disabled")

    def _reset_combo_match_tracking(self):
        self._combo_prev_matched.clear()

    def _evaluate_combos(self) -> bool:
        held    = self._global_held()
        changed = False

        newly_matched: list[ComboBind] = []
        for cb in self.combo_binds:
            btnset      = set(cb.buttons)
            if len(btnset) < 2 or not cb.param:
                continue
            is_matched  = btnset.issubset(held)
            was_matched = id(cb) in self._combo_prev_matched
            if is_matched and not was_matched:
                newly_matched.append(cb)
            elif not is_matched and was_matched and cb.trigger_mode == "hold":
                cb.release(self._osc)
                changed = True

        self._combo_prev_matched = {
            id(cb) for cb in self.combo_binds
            if cb.param and len(cb.buttons) >= 2 and set(cb.buttons).issubset(held)
        }

        if not newly_matched:
            return changed

        newly_matched.sort(key=lambda cb: len(cb.buttons), reverse=True)
        fired_this_pass: list[ComboBind] = []
        for cb in newly_matched:
            cb_btns = set(cb.buttons)

            if any(set(f.buttons) >= cb_btns for f in fired_this_pass):
                continue
            cb.fire(self._osc)
            fired_this_pass.append(cb)
            changed = True

            for b in self.binds:
                if {(b.hand, b.btn)} < cb_btns and b.is_active():
                    b.force_off(self._osc)
            for other in self.combo_binds:
                if other is cb or not other.param or len(other.buttons) < 2:
                    continue
                if set(other.buttons) < cb_btns and other.is_active():
                    other.force_off(self._osc)
                    self._combo_prev_matched.discard(id(other))
        return changed

    def _refresh_combo_displays(self):
        for w in self._combo_slot_widgets:
            w.refresh_state_display()

    def _refresh_binds(self):
        for w in self._binds_body.winfo_children():
            w.destroy()
        self._bind_rows = []

        if not self.binds:
            ttk.Label(self._binds_body, text="── NO BINDS ──",
                      foreground=TEXT_DIM, font=("Consolas", 8)).pack(padx=6, pady=8)
            return

        for i, b in enumerate(self.binds):
            outer = ttk.Frame(self._binds_body)
            outer.pack(fill="x", padx=2, pady=2)

            row = ttk.Frame(outer)
            row.pack(fill="x")

            row_refs: dict = {}

            ttk.Button(row, text="✕", width=2,
                       command=lambda i=i: self._remove_bind(i)).pack(side="right", padx=4)

            ttk.Label(row, text=b.label(), width=10,
                      font=("Consolas", 8, "bold"),
                      foreground=ACCENT).pack(side="left")
            ttk.Label(row, text=b.param, width=24, anchor="w",
                      font=("Segoe UI Mono", 8)).pack(side="left", padx=(2, 8))

            if b.mode == "int":
                var_a = tk.IntVar(value=b.val_a)
                var_b = tk.IntVar(value=b.val_b)
                def _tracer(bind=b, va=var_a, vb=var_b):
                    try:
                        bind.val_a = va.get()
                        bind.val_b = vb.get()
                    except tk.TclError:
                        pass
                var_a.trace_add("write", lambda *_: _tracer())
                var_b.trace_add("write", lambda *_: _tracer())
                ttk.Spinbox(row, textvariable=var_a, from_=-9999, to=9999, width=5
                            ).pack(side="left", padx=(2, 1))
                ttk.Label(row, text="↔", foreground=TEXT_DIM).pack(side="left")
                ttk.Spinbox(row, textvariable=var_b, from_=-9999, to=9999, width=5
                            ).pack(side="left", padx=(1, 6))
                cur = b.val_a if b.int_idx == 0 else b.val_b
                cur_lbl = ttk.Label(row, text=f"[{cur}]", foreground=ACCENT)
                cur_lbl.pack(side="left")
                row_refs["cur_lbl"] = cur_lbl

            elif b.mode == "float":
                fa_var = tk.StringVar(value=getattr(b, "float_a", "1.0"))
                fb_var = tk.StringVar(value=getattr(b, "float_b", "0.0"))
                def _float_tracer(bind=b, va=fa_var, vb=fb_var):
                    bind.float_a = va.get()
                    bind.float_b = vb.get()
                fa_var.trace_add("write", lambda *_: _float_tracer())
                fb_var.trace_add("write", lambda *_: _float_tracer())
                ttk.Entry(row, textvariable=fa_var, width=7).pack(side="left", padx=(2, 1))
                ttk.Label(row, text="↔", foreground=TEXT_DIM).pack(side="left")
                ttk.Entry(row, textvariable=fb_var, width=7).pack(side="left", padx=(1, 6))
                cur_f = getattr(b, "float_a", "1.0") if b.int_idx == 0 else getattr(b, "float_b", "0.0")
                cur_lbl = ttk.Label(row, text=f"[{cur_f}]", foreground=ACCENT)
                cur_lbl.pack(side="left")
                row_refs["cur_lbl"] = cur_lbl

            else:
                state_text = "TRUE" if b.state else "FALSE"
                state_lbl = ttk.Label(row, text=state_text,
                          foreground=(TEXT_ON if b.state else TEXT_DIM),
                          font=("Consolas", 8, "bold"))
                state_lbl.pack(side="left")
                row_refs["state_lbl"] = state_lbl
                inv_var = tk.BooleanVar(value=b.invert)
                def _inv_changed(bind=b, var=inv_var):
                    bind.invert = var.get()
                ttk.Checkbutton(row, text="Invert", variable=inv_var,
                                command=_inv_changed).pack(side="left", padx=(8, 0))

            mode_row = ttk.Frame(outer)
            mode_row.pack(fill="x", padx=(36, 4), pady=(1, 2))
            ttk.Label(mode_row, text="Trigger:", foreground=TEXT_DIM,
                      font=("Consolas", 7)).pack(side="left")
            tmode_var = tk.StringVar(value=b.trigger_mode)
            def _tmode_changed(bind=b, var=tmode_var):
                bind.trigger_mode = var.get()
            for label, val in [("Toggle", "toggle"), ("Hold", "hold"), ("Delay off", "delay")]:
                ttk.Radiobutton(mode_row, text=label, variable=tmode_var, value=val,
                                command=lambda bind=b, var=tmode_var, dr=mode_row: (
                                    _tmode_changed(bind, var),
                                    self._refresh_bind_delay_widget(dr, bind, var)
                                )).pack(side="left", padx=(4, 0))
            self._refresh_bind_delay_widget(mode_row, b, tmode_var)
            self._bind_rows.append(row_refs)

    def _update_bind_states(self):
        """Cheap, in-place refresh of bind display state (fired on every
        combo/bind press). Does NOT touch widgets or tk Variables, so it
        can't leak Tcl trace commands the way a full _refresh_binds()
        rebuild does. Falls back to a full rebuild only if the bind list
        itself changed shape since the last build."""
        if len(self._bind_rows) != len(self.binds):
            self._refresh_binds()
            return
        for row_refs, b in zip(self._bind_rows, self.binds):
            if b.mode == "int":
                cur = b.val_a if b.int_idx == 0 else b.val_b
                lbl = row_refs.get("cur_lbl")
                if lbl is not None:
                    lbl.config(text=f"[{cur}]")
            elif b.mode == "float":
                cur_f = b.float_a if b.int_idx == 0 else b.float_b
                lbl = row_refs.get("cur_lbl")
                if lbl is not None:
                    lbl.config(text=f"[{cur_f}]")
            else:
                lbl = row_refs.get("state_lbl")
                if lbl is not None:
                    lbl.config(text=("TRUE" if b.state else "FALSE"),
                               foreground=(TEXT_ON if b.state else TEXT_DIM))

    def _refresh_bind_delay_widget(self, row: tk.Widget, bind, tmode_var: tk.StringVar):
        for w in row.winfo_children():
            if getattr(w, "_is_delay_widget", False):
                w.destroy()
        if tmode_var.get() == "delay":
            delay_var = tk.DoubleVar(value=bind.delay)
            def _dv_changed(*_, dv=delay_var, b=bind):
                b.delay = dv.get()
            delay_var.trace_add("write", _dv_changed)
            f = ttk.Frame(row)
            f._is_delay_widget = True
            ttk.Label(f, text="  off after", foreground=TEXT_DIM,
                      font=("Consolas", 7)).pack(side="left")
            ttk.Scale(f, variable=delay_var, from_=0.1, to=30.0,
                      orient="horizontal", length=80).pack(side="left", padx=2)
            _editable_value(f, delay_var, 0.1, 30.0, unit="s", width=6).pack(side="left")
            f.pack(side="left")

    def _remove_bind(self, idx: int):
        if 0 <= idx < len(self.binds):
            self.binds.pop(idx)
            self._refresh_binds()

    def _clear_binds(self):
        if not self.binds:
            return
        if messagebox.askyesno("Clear binds", "Remove all OSC binds?", parent=self._app.root):
            self.binds.clear()
            self._refresh_binds()

    def shoulders_body(self) -> tk.Frame:
        return self._shoulders_body

    def poses_body(self) -> tk.Frame:
        return self._poses_body

    def to_dict(self) -> dict:
        return {
            "binds": [b.to_dict() for b in self.binds],
            "combo_binds": [cb.to_dict() for cb in self.combo_binds],
        }

    def load_dict(self, d: dict):
        self.binds = [Bind.from_dict(bd) for bd in d.get("binds", [])]
        combo_data = d.get("combo_binds", [])

        for w in list(self._combo_slot_widgets):
            if w._outer:
                try:
                    w._outer.destroy()
                except Exception:
                    pass
        self._combo_slot_widgets.clear()
        self.combo_binds.clear()

        if combo_data:
            for cd in combo_data:
                self._add_osc_combo_slot(bind_data=ComboBind.from_dict(cd))
        else:
            self._add_osc_combo_slot()

        self._reset_combo_match_tracking()
        self._refresh_binds()

class _ShoulderSlotWidget:

    def __init__(self, parent: tk.Widget, shoulder: str,
                 osc: "OSCLink", audio: "AudioEngine", app: "App",
                 osc_frame: "OSCFrame | None" = None):
        self._shoulder = shoulder
        self._osc      = osc
        self._audio    = audio
        self._app      = app
        self._osc_frame = osc_frame

        self.osc_param_var   = tk.StringVar()
        self.sfx_on_var      = tk.StringVar()
        self.sfx_off_var     = tk.StringVar()
        self.volume_var      = tk.DoubleVar(value=1.0)
        self.osc_mode_var    = tk.StringVar(value="bool")
        self.osc_val_on_var  = tk.IntVar(value=1)
        self.osc_val_off_var = tk.IntVar(value=0)
        self.float_on_var    = tk.StringVar(value="1.0")
        self.float_off_var   = tk.StringVar(value="0.0")
        self.invert_var      = tk.BooleanVar(value=False)
        self._state          = False
        self._trigger_mode   = "toggle"
        self._delay          = 1.0
        self._delay_timer: "threading.Timer | None" = None
        self._int_row: tk.Widget | None   = None
        self._float_row: tk.Widget | None = None
        self._invert_row: tk.Widget | None = None

        self._build(parent)

    def _on_trigger_mode_change(self):
        mode = self.trigger_mode_var.get()
        self._trigger_mode = mode
        if mode == "delay":
            self._delay_frame.pack(fill="x", padx=4, pady=1)
        else:
            self._delay_frame.pack_forget()

    def _start_bind(self):
        if self._osc_frame is None:
            self._bind_status_lbl.config(text="No OSC frame", foreground=_theme.get("text_error"))
            return
        param = self._osc_frame._get_selected_param()
        if not param:
            self._bind_status_lbl.config(text="Select a param in OSC first",
                                         foreground=_theme.get("text_error"))
            return
        self.osc_param_var.set(param)
        self._bind_status_lbl.config(text=f"Set to: {param}", foreground=_theme.get("text_on"))

    def _build(self, parent: tk.Widget):
        label = "Left Shoulder" if self._shoulder == "left" else "Right Shoulder"
        lf = ttk.LabelFrame(parent, text=label)
        lf.pack(fill="x", padx=4, pady=3)

        top = ttk.Frame(lf)
        top.pack(fill="x", padx=4, pady=(3, 0))
        self._state_lbl = ttk.Label(top, text="■ OFF", foreground=TEXT_DIM,
                                    width=6, font=("Consolas", 9, "bold"))
        self._state_lbl.pack(side="left")
        ttk.Button(top, text="TEST FIRE", command=self.fire).pack(side="left", padx=4)
        _info_button(top, (
            "Grab this slot by pressing grip while your hand is inside the "
            "shoulder zone (anchored to your HMD position and facing direction).\n\n"
            "Each grab toggles ON/OFF. SFX On plays when turning on, "
            "SFX Off plays when turning off.\n\n"
            "OSC Param mode:\n"
            "  Bool  — sends True/False each toggle.\n"
            "  Int   — sends the two integer values you set.\n"
            "  Float — sends the two decimal values you set.\n\n"
            "Trigger modes:\n"
            "  Toggle  —  flip state each grab.\n"
            "  Hold    —  ON while hand in zone with grip held, OFF on release.\n"
            "  Delay off  —  turns ON on grab, auto-OFF after N seconds.\n\n"
            "Adjust where the zone sits with the detection tuning sliders below."
        ), side="left")

        bind_row = ttk.Frame(lf)
        bind_row.pack(fill="x", padx=4, pady=(2, 0))
        self._bind_btn = ttk.Button(bind_row, text="Bind selected param →",
                                    command=self._start_bind, style="Accent.TButton")
        self._bind_btn.pack(side="left")
        self._bind_status_lbl = ttk.Label(bind_row, text="", foreground=TEXT_DIM,
                                          font=("Consolas", 8))
        self._bind_status_lbl.pack(side="left", padx=6)

        r1 = ttk.Frame(lf)
        r1.pack(fill="x", padx=4, pady=1)
        ttk.Label(r1, text="OSC Param", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(r1, textvariable=self.osc_param_var, width=32).pack(side="left", padx=3)

        tmode_row = ttk.Frame(lf)
        tmode_row.pack(fill="x", padx=4, pady=(1, 0))
        ttk.Label(tmode_row, text="Trigger", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        self.trigger_mode_var = tk.StringVar(value=self._trigger_mode)
        for label_t, val in [("Toggle", "toggle"), ("Hold", "hold"), ("Delay off", "delay")]:
            ttk.Radiobutton(tmode_row, text=label_t, variable=self.trigger_mode_var,
                            value=val, command=self._on_trigger_mode_change
                            ).pack(side="left", padx=(3, 4))
        self._delay_frame = ttk.Frame(lf)
        ttk.Label(self._delay_frame, text="Off after", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        self._delay_var = tk.DoubleVar(value=self._delay)
        self._delay_var.trace_add("write", lambda *_: setattr(self, "_delay", self._delay_var.get()))
        ttk.Scale(self._delay_frame, variable=self._delay_var, from_=0.1, to=30.0,
                  orient="horizontal", length=100).pack(side="left", padx=2)
        _editable_value(self._delay_frame, self._delay_var, 0.1, 30.0, unit="s", width=6).pack(side="left")

        self._int_row, self._float_row = _osc_mode_row(
            lf, self.osc_mode_var,
            self.osc_val_on_var, self.osc_val_off_var,
            self.float_on_var, self.float_off_var,
            self._refresh_value_rows,
        )

        self._invert_row = ttk.Frame(lf)
        ttk.Checkbutton(self._invert_row, text="Invert (send opposite bool)",
                        variable=self.invert_var).pack(side="left", padx=(74, 0))

        r2 = ttk.Frame(lf)
        r2.pack(fill="x", padx=4, pady=1)
        ttk.Label(r2, text="SFX On", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(r2, textvariable=self.sfx_on_var, width=28).pack(side="left")
        ttk.Button(r2, text="…", width=2,
                   command=lambda: _pick_sfx(self.sfx_on_var)
                   ).pack(side="left", padx=(2, 6))

        r3 = ttk.Frame(lf)
        r3.pack(fill="x", padx=4, pady=1)
        ttk.Label(r3, text="SFX Off", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(r3, textvariable=self.sfx_off_var, width=28).pack(side="left")
        ttk.Button(r3, text="…", width=2,
                   command=lambda: _pick_sfx(self.sfx_off_var)
                   ).pack(side="left", padx=(2, 6))

        _slider_row(lf, "Volume", self.volume_var, 0.0, 1.0)
        self._refresh_value_rows()
        self._on_trigger_mode_change()

    def _refresh_value_rows(self):
        mode = self.osc_mode_var.get()
        if self._int_row:
            self._int_row.pack_forget()
        if self._float_row:
            self._float_row.pack_forget()
        if self._invert_row:
            self._invert_row.pack_forget()
        if mode == "int" and self._int_row:
            self._int_row.pack(fill="x", padx=4, pady=1)
        elif mode == "float" and self._float_row:
            self._float_row.pack(fill="x", padx=4, pady=1)
        elif mode == "bool" and self._invert_row:
            self._invert_row.pack(fill="x", padx=4, pady=1)

    def _send_osc(self, state: bool):
        param = self.osc_param_var.get().strip()
        _osc_send_typed(self._osc, param,
                        self.osc_mode_var.get(),
                        self.osc_val_on_var.get(), self.osc_val_off_var.get(),
                        self.float_on_var.get(), self.float_off_var.get(),
                        state, invert=self.invert_var.get())

    def fire(self):
        tmode = self.trigger_mode_var.get() if hasattr(self, "trigger_mode_var") else "toggle"
        vol = self.volume_var.get() * self._app.mvol_var.get()

        if tmode == "toggle":
            self._state = not self._state
            if self._state:
                self._state_lbl.config(text="▶ ON", foreground=_theme.get("text_on"))
                sfx = self.sfx_on_var.get()
            else:
                self._state_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
                sfx = self.sfx_off_var.get()
            if sfx:
                self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
            self._send_osc(self._state)

        elif tmode == "delay":
            if self._delay_timer is not None:
                self._delay_timer.cancel()
                self._delay_timer = None
            self._state = True
            self._state_lbl.config(text="▶ ON", foreground=_theme.get("text_on"))
            sfx = self.sfx_on_var.get()
            if sfx:
                self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
            self._send_osc(True)
            delay = self._delay_var.get() if hasattr(self, "_delay_var") else self._delay
            def _auto_off():
                self._state = False
                try:
                    self._state_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
                except Exception:
                    pass
                sfx_off = self.sfx_off_var.get()
                if sfx_off:
                    self._audio.play(resolve_sfx_path(sfx_off), vol, loop=False)
                self._send_osc(False)
            t = threading.Timer(max(delay, 0.05), _auto_off)
            t.daemon = True
            t.start()
            self._delay_timer = t

        else:
            self._state = not self._state
            if self._state:
                self._state_lbl.config(text="▶ ON", foreground=_theme.get("text_on"))
                sfx = self.sfx_on_var.get()
            else:
                self._state_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
                sfx = self.sfx_off_var.get()
            if sfx:
                self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
            self._send_osc(self._state)

    def to_dict(self) -> dict:
        return {
            "osc_param":    self.osc_param_var.get(),
            "sfx_on":       self.sfx_on_var.get(),
            "sfx_off":      self.sfx_off_var.get(),
            "volume":       self.volume_var.get(),
            "state":        self._state,
            "osc_mode":     self.osc_mode_var.get(),
            "osc_val_on":   self.osc_val_on_var.get(),
            "osc_val_off":  self.osc_val_off_var.get(),
            "float_on":     self.float_on_var.get(),
            "float_off":    self.float_off_var.get(),
            "invert":       self.invert_var.get(),
            "trigger_mode": self.trigger_mode_var.get() if hasattr(self, "trigger_mode_var") else "toggle",
            "delay":        self._delay_var.get() if hasattr(self, "_delay_var") else self._delay,
        }

    def load_dict(self, d: dict):
        self.osc_param_var.set(d.get("osc_param", ""))
        self.sfx_on_var.set(d.get("sfx_on", ""))
        self.sfx_off_var.set(d.get("sfx_off", ""))
        self.volume_var.set(d.get("volume", 1.0))
        self._state = d.get("state", False)
        self.osc_mode_var.set(d.get("osc_mode", "bool"))
        self.float_on_var.set(str(d.get("float_on", "1.0")))
        self.float_off_var.set(str(d.get("float_off", "0.0")))
        self.invert_var.set(bool(d.get("invert", False)))
        try:
            self.osc_val_on_var.set(int(d.get("osc_val_on", 1)))
            self.osc_val_off_var.set(int(d.get("osc_val_off", 0)))
        except (ValueError, tk.TclError):
            self.osc_val_on_var.set(1)
            self.osc_val_off_var.set(0)
        if hasattr(self, "trigger_mode_var"):
            self.trigger_mode_var.set(d.get("trigger_mode", "toggle"))
            self._trigger_mode = d.get("trigger_mode", "toggle")
        if hasattr(self, "_delay_var"):
            self._delay = float(d.get("delay", 1.0))
            self._delay_var.set(self._delay)
        self._refresh_value_rows()
        self._on_trigger_mode_change()
        self._state_lbl.config(
            text="▶ ON" if self._state else "■ OFF",
            foreground=TEXT_ON if self._state else TEXT_DIM,
        )

class ShoulderFrame:

    def __init__(self, parent: tk.Widget, osc: "OSCLink",
                 audio: "AudioEngine", app: "App",
                 slots_parent: tk.Widget | None = None,
                 osc_frame: "OSCFrame | None" = None):
        self._osc       = osc
        self._audio     = audio
        self._app       = app
        self._osc_frame = osc_frame
        self.slots: dict[str, _ShoulderSlotWidget] = {}
        self._build(parent, slots_parent or parent)

    def _build(self, tuning_parent: tk.Widget, slots_parent: tk.Widget):
        tf = ttk.LabelFrame(tuning_parent, text="DETECTION TUNING")
        tf.pack(fill="x", padx=4, pady=(4, 2))

        _info_button(tf, (
            "Controls where the shoulder holster zones sit relative to your HMD.\n\n"
            "Radius  —  detection sphere size in metres.\n"
            "Down    —  how far below the HMD the zone sits.\n"
            "Side    —  how far out to each side.\n"
            "Back    —  how far behind the HMD.\n\n"
            "Changes take effect immediately without reconnecting OVR."
        ))

        self._radius_var = tk.DoubleVar(value=0.33)
        self._down_var   = tk.DoubleVar(value=0.00)
        self._side_var   = tk.DoubleVar(value=0.28)
        self._back_var   = tk.DoubleVar(value=0.25)

        self._radius_var.trace_add("write", lambda *_: setattr(self._app.ovr, "shoulder_radius",      self._radius_var.get()))
        self._down_var.trace_add(  "write", lambda *_: setattr(self._app.ovr, "shoulder_down_offset", self._down_var.get()))
        self._side_var.trace_add(  "write", lambda *_: setattr(self._app.ovr, "shoulder_side_offset", self._side_var.get()))
        self._back_var.trace_add(  "write", lambda *_: setattr(self._app.ovr, "shoulder_back_offset", self._back_var.get()))

        self._app.ovr.shoulder_radius      = 0.33
        self._app.ovr.shoulder_down_offset = 0.00
        self._app.ovr.shoulder_side_offset = 0.28
        self._app.ovr.shoulder_back_offset = 0.25

        _slider_row(tf, "Radius", self._radius_var, 0.05, 0.60, unit=" m")
        _slider_row(tf, "Down",   self._down_var,   0.00, 0.70, unit=" m")
        _slider_row(tf, "Side",   self._side_var,   0.00, 0.60, unit=" m")
        _slider_row(tf, "Back",   self._back_var,   0.00, 0.60, unit=" m")

        for shoulder in ("left", "right"):
            w = _ShoulderSlotWidget(slots_parent, shoulder,
                                    self._osc, self._audio, self._app,
                                    osc_frame=self._osc_frame)
            self.slots[shoulder] = w

    def handle_shoulder_grab(self, shoulder: str):
        slot = self.slots.get(shoulder)
        if slot:
            slot.fire()

    def to_dict(self) -> dict:
        return {
            "radius":      self._radius_var.get(),
            "down_offset": self._down_var.get(),
            "side_offset": self._side_var.get(),
            "back_offset": self._back_var.get(),
            "slots":       {s: w.to_dict() for s, w in self.slots.items()},
        }

    def load_dict(self, d: dict):
        self._radius_var.set(d.get("radius",      0.33))
        self._down_var.set(  d.get("down_offset", 0.00))
        self._side_var.set(  d.get("side_offset", 0.28))
        self._back_var.set(  d.get("back_offset", 0.25))
        for shoulder, w in self.slots.items():
            w.load_dict(d.get("slots", {}).get(shoulder, {}))

    def preload_files(self) -> list[str]:
        vals: list[str] = []
        for w in self.slots.values():
            vals += [w.sfx_on_var.get(), w.sfx_off_var.get()]
        return _expand_for_preload(vals)

class _PoseSlotWidget:

    def __init__(self, parent: tk.Widget, index: int, osc: "OSCLink",
                 audio: "AudioEngine", app: "App", owner: "PoseFrame"):
        self._osc   = osc
        self._audio = audio
        self._app   = app
        self._owner = owner

        self._target: dict | None = None
        self._countdown_deadline: float | None = None
        self._holding         = False
        self._hold_start      = 0.0
        self._fired_this_hold = False
        self._state           = False
        self._trigger_mode    = "toggle"
        self._delay           = 1.0
        self._delay_timer: "threading.Timer | None" = None

        self.name_var        = tk.StringVar(value=f"Pose {index + 1}")
        self.pos_tol_var     = tk.DoubleVar(value=0.10)
        self.rot_tol_var     = tk.DoubleVar(value=25.0)
        self.hold_var        = tk.DoubleVar(value=0.6)
        self.delay_var       = tk.DoubleVar(value=5.0)
        self.confirm_sfx_var = tk.StringVar()
        self.osc_param_var   = tk.StringVar()
        self.osc_mode_var    = tk.StringVar(value="bool")
        self.osc_val_on_var  = tk.IntVar(value=1)
        self.osc_val_off_var = tk.IntVar(value=0)
        self.float_on_var    = tk.StringVar(value="1.0")
        self.float_off_var   = tk.StringVar(value="0.0")
        self.invert_var      = tk.BooleanVar(value=False)
        self.sfx_on_var      = tk.StringVar()
        self.sfx_off_var     = tk.StringVar()
        self.volume_var      = tk.DoubleVar(value=1.0)
        self._int_row: tk.Widget | None   = None
        self._float_row: tk.Widget | None = None
        self._invert_row: tk.Widget | None = None

        self._build(parent)

    def _build(self, parent: tk.Widget):
        self._lf = ttk.LabelFrame(parent, text=self.name_var.get())
        self._lf.pack(fill="x", padx=4, pady=4)
        self.name_var.trace_add(
            "write", lambda *_: self._lf.config(text=self.name_var.get() or "Pose"))

        top = ttk.Frame(self._lf)
        top.pack(fill="x", padx=4, pady=(3, 0))
        ttk.Entry(top, textvariable=self.name_var, width=16).pack(side="left")
        self._state_lbl = ttk.Label(top, text="■ OFF", foreground=TEXT_DIM,
                                    width=6, font=("Consolas", 9, "bold"))
        self._state_lbl.pack(side="left", padx=(8, 0))
        ttk.Button(top, text="✕", width=2, command=self._remove).pack(side="right")
        _info_button(top, (
            "Record a pose: click \"Arm Record\", then — while wearing the "
            "headset — press any controller button. After Delay seconds your "
            "HMD + controller positions and rotations are captured, relative "
            "to your head.\n\n"
            "Strike that same pose and hold it for Hold seconds to fire — "
            "sending OSC param and playing SFX On/Off.\n\n"
            "Trigger modes:\n"
            "  Toggle  —  flip ON/OFF each time the pose is confirmed.\n"
            "  Hold    —  ON while you keep holding the pose, OFF the instant "
            "you break it.\n"
            "  Delay off  —  turns ON when confirmed, auto-OFF after N seconds "
            "regardless of pose.\n\n"
            "Don't like the capture? Click the button again (it relabels to "
            "\"Re-record Pose\" once something's captured) to overwrite it "
            "with a fresh one. [WARNING, BROKEN. TO BE FIXED, JUST CLEAR INSTEAD]"
        ), side="right")

        rec_row = ttk.Frame(self._lf)
        rec_row.pack(fill="x", padx=4, pady=1)
        self._record_btn = ttk.Button(rec_row, text="ARM REC",
                                      command=self._arm_record, style="Accent.TButton")
        self._record_btn.pack(side="left")
        self._cancel_btn = ttk.Button(rec_row, text="CANCEL", command=self._cancel_record)
        ttk.Button(rec_row, text="TEST FIRE", command=self.fire).pack(side="left", padx=4)
        ttk.Button(rec_row, text="CLEAR", command=self._clear_capture).pack(side="left", padx=(0, 4))
        self._status_lbl = ttk.Label(rec_row, text="── NO CAPTURE ──",
                                     foreground=TEXT_DIM, font=("Consolas", 8))
        self._status_lbl.pack(side="left", padx=8)

        _slider_row(self._lf, "Pos Tol",  self.pos_tol_var, 0.02, 0.40, unit=" m")
        _slider_row(self._lf, "Rot Tol",  self.rot_tol_var, 5.0,  90.0, unit="°")
        _slider_row(self._lf, "Hold",     self.hold_var,    0.1,  3.0,  unit=" s")
        _slider_row(self._lf, "Delay",    self.delay_var,   1.0,  10.0, unit=" s")

        cf = ttk.Frame(self._lf)
        cf.pack(fill="x", padx=4, pady=1)
        ttk.Label(cf, text="Confirm SFX", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(cf, textvariable=self.confirm_sfx_var, width=28).pack(side="left")
        ttk.Button(cf, text="…", width=2,
                   command=lambda: _pick_sfx(self.confirm_sfx_var)
                   ).pack(side="left", padx=(2, 6))

        r1 = ttk.Frame(self._lf)
        r1.pack(fill="x", padx=4, pady=1)
        ttk.Label(r1, text="OSC Param", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(r1, textvariable=self.osc_param_var, width=24).pack(side="left", padx=3)
        self._pose_bind_btn = ttk.Button(r1, text="Bind selected",
                                         command=self._start_bind, style="Accent.TButton")
        self._pose_bind_btn.pack(side="left", padx=(2, 0))

        tmode_row = ttk.Frame(self._lf)
        tmode_row.pack(fill="x", padx=4, pady=(1, 0))
        ttk.Label(tmode_row, text="Trigger", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        self.trigger_mode_var = tk.StringVar(value=self._trigger_mode)
        for label_t, val in [("Toggle", "toggle"), ("Hold", "hold"), ("Delay off", "delay")]:
            ttk.Radiobutton(tmode_row, text=label_t, variable=self.trigger_mode_var,
                            value=val, command=self._on_trigger_mode_change
                            ).pack(side="left", padx=(3, 4))
        self._delay_frame = ttk.Frame(self._lf)
        ttk.Label(self._delay_frame, text="Off after", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        self._delay_var = tk.DoubleVar(value=self._delay)
        self._delay_var.trace_add("write", lambda *_: setattr(self, "_delay", self._delay_var.get()))
        ttk.Scale(self._delay_frame, variable=self._delay_var, from_=0.1, to=30.0,
                  orient="horizontal", length=100).pack(side="left", padx=2)
        _editable_value(self._delay_frame, self._delay_var, 0.1, 30.0, unit="s", width=6).pack(side="left")

        self._int_row, self._float_row = _osc_mode_row(
            self._lf, self.osc_mode_var,
            self.osc_val_on_var, self.osc_val_off_var,
            self.float_on_var, self.float_off_var,
            self._refresh_value_rows,
        )

        self._invert_row = ttk.Frame(self._lf)
        ttk.Checkbutton(self._invert_row, text="Invert (send opposite bool)",
                        variable=self.invert_var).pack(side="left", padx=(74, 0))

        r2 = ttk.Frame(self._lf)
        r2.pack(fill="x", padx=4, pady=1)
        ttk.Label(r2, text="SFX On", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(r2, textvariable=self.sfx_on_var, width=28).pack(side="left")
        ttk.Button(r2, text="…", width=2,
                   command=lambda: _pick_sfx(self.sfx_on_var)
                   ).pack(side="left", padx=(2, 6))

        r3 = ttk.Frame(self._lf)
        r3.pack(fill="x", padx=4, pady=1)
        ttk.Label(r3, text="SFX Off", width=10, anchor="e",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")
        ttk.Entry(r3, textvariable=self.sfx_off_var, width=28).pack(side="left")
        ttk.Button(r3, text="…", width=2,
                   command=lambda: _pick_sfx(self.sfx_off_var)
                   ).pack(side="left", padx=(2, 6))

        _slider_row(self._lf, "Volume", self.volume_var, 0.0, 1.0)

        self._refresh_value_rows()
        self._on_trigger_mode_change()
        if self._target:
            self._show_captured_status()

    def _refresh_value_rows(self):
        mode = self.osc_mode_var.get()
        if self._int_row:
            self._int_row.pack_forget()
        if self._float_row:
            self._float_row.pack_forget()
        if self._invert_row:
            self._invert_row.pack_forget()
        if mode == "int" and self._int_row:
            self._int_row.pack(fill="x", padx=4, pady=1)
        elif mode == "float" and self._float_row:
            self._float_row.pack(fill="x", padx=4, pady=1)
        elif mode == "bool" and self._invert_row:
            self._invert_row.pack(fill="x", padx=4, pady=1)

    def _on_trigger_mode_change(self):
        mode = self.trigger_mode_var.get()
        self._trigger_mode = mode
        if mode == "delay":
            self._delay_frame.pack(fill="x", padx=4, pady=1)
        else:
            self._delay_frame.pack_forget()

    def _cancel_delay_timer(self):
        if self._delay_timer is not None:
            self._delay_timer.cancel()
            self._delay_timer = None

    def _send_osc(self, state: bool):
        param = self.osc_param_var.get().strip()
        _osc_send_typed(self._osc, param,
                        self.osc_mode_var.get(),
                        self.osc_val_on_var.get(), self.osc_val_off_var.get(),
                        self.float_on_var.get(), self.float_off_var.get(),
                        state, invert=self.invert_var.get())

    def _start_bind(self):
        osc_frame = getattr(self._owner, "_osc_frame", None)
        if osc_frame is None:
            return
        param = osc_frame._get_selected_param()
        if not param:
            return
        self.osc_param_var.set(param)

    def _remove(self):
        if messagebox.askyesno("Remove pose", f'Remove "{self.name_var.get()}"?',
                                parent=self._app.root):
            self._owner.remove_slot(self)

    def destroy_ui(self):
        self._cancel_delay_timer()
        try:
            self._lf.destroy()
        except Exception:
            pass

    def set_status(self, text: str, color: str = "grey"):
        try:
            self._status_lbl.config(text=text, foreground=color)
        except tk.TclError:
            pass

    def _show_captured_status(self):
        hands = "+".join(h for h in ("left", "right") if self._target and self._target.get(h))
        self.set_status(f"Captured ({hands or 'none'}) — click Re-record to redo", TEXT_ON)
        self._refresh_record_btn()

    def _refresh_record_btn(self):
        if self._target is not None:
            self._record_btn.config(text="RE-REC POSE")
        else:
            self._record_btn.config(text="ARM REC")

    def _clear_capture(self):
        if self._target is None:
            return
        if not messagebox.askyesno(
                "Clear pose", f'Clear the recorded capture for "{self.name_var.get()}"?',
                parent=self._app.root):
            return
        self._target = None
        self._holding = False
        self._fired_this_hold = False
        self._cancel_delay_timer()
        self._refresh_record_btn()
        self.set_status("No pose recorded", TEXT_DIM)

    def _arm_record(self):
        if not self._app.ovr.connected:
            self.set_status("Connect OVR first (top bar)", _theme.get("text_error"))
            return
        self._owner.arm_record(self)
        self.set_status("Waiting for a controller button press…", ACCENT)

    def begin_countdown(self):
        delay = max(self.delay_var.get(), 0.0)
        self._countdown_deadline = time.monotonic() + delay
        self._cancel_btn.pack(side="left", padx=4)
        self.set_status(f"Get into pose… capturing in {delay:0.1f}s", ACCENT)

    def _cancel_record(self):
        self._countdown_deadline = None
        self._owner.cancel_if_listening(self)
        self._cancel_btn.pack_forget()
        self.set_status("Recording cancelled", TEXT_DIM)

    def _capture_now(self):
        self._countdown_deadline = None
        self._cancel_btn.pack_forget()
        snap = self._app.ovr.capture_relative_pose()
        if not snap.get("left") and not snap.get("right"):
            self.set_status("Capture failed — no controllers tracked", _theme.get("text_error"))
            return
        self._target = snap
        self._holding = False
        self._fired_this_hold = False
        self._show_captured_status()
        sfx = self.confirm_sfx_var.get()
        if sfx:
            self._audio.play(resolve_sfx_path(sfx), self.volume_var.get() * self._app.mvol_var.get(), loop=False)

    def tick(self):
        if self._countdown_deadline is not None:
            remaining = self._countdown_deadline - time.monotonic()
            if remaining <= 0:
                self._capture_now()
            else:
                self.set_status(f"Capturing in {remaining:0.1f}s…", ACCENT)
            return

        if self._target is None:
            return

        if not self._app.ovr.connected:
            self._holding = False
            self._fired_this_hold = False
            self.set_status("OVR not connected", TEXT_DIM)
            return

        matched = self._app.ovr.pose_match(
            self._target, self.pos_tol_var.get(), self.rot_tol_var.get())
        now = time.monotonic()

        if matched:
            if not self._holding:
                self._holding = True
                self._hold_start = now
                self._fired_this_hold = False
            held        = now - self._hold_start
            hold_needed = max(self.hold_var.get(), 0.0)
            if not self._fired_this_hold and held >= hold_needed:
                self._fired_this_hold = True
                self.fire()
                msg = ("Holding — drop pose to release."
                       if self.trigger_mode_var.get() == "hold"
                       else "Fired! Release pose to re-arm.")
                self.set_status(msg, TEXT_ON)
            elif not self._fired_this_hold:
                self.set_status(f"Matched — holding {held:0.1f}/{hold_needed:0.1f}s", ACCENT)
        else:
            if self._holding and self._fired_this_hold and self.trigger_mode_var.get() == "hold":
                self._release_hold()
            self._holding = False
            self._fired_this_hold = False
            self.set_status("Captured — out of pose", TEXT_DIM)

    def fire(self):
        tmode = self.trigger_mode_var.get() if hasattr(self, "trigger_mode_var") else "toggle"
        vol = self.volume_var.get() * self._app.mvol_var.get()

        if tmode == "hold":
            self._cancel_delay_timer()
            self._state = True
            self._state_lbl.config(text="▶ ON", foreground=_theme.get("text_on"))
            sfx = self.sfx_on_var.get()
            if sfx:
                self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
            self._send_osc(True)
            return

        if tmode == "delay":
            self._cancel_delay_timer()
            self._state = True
            self._state_lbl.config(text="▶ ON", foreground=_theme.get("text_on"))
            sfx = self.sfx_on_var.get()
            if sfx:
                self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
            self._send_osc(True)
            delay = self._delay_var.get() if hasattr(self, "_delay_var") else self._delay

            def _auto_off():
                self._state = False
                try:
                    self._state_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
                except Exception:
                    pass
                sfx_off = self.sfx_off_var.get()
                if sfx_off:
                    self._audio.play(resolve_sfx_path(sfx_off), vol, loop=False)
                self._send_osc(False)

            t = threading.Timer(max(delay, 0.05), _auto_off)
            t.daemon = True
            t.start()
            self._delay_timer = t
            return

        self._state = not self._state
        if self._state:
            self._state_lbl.config(text="▶ ON", foreground=_theme.get("text_on"))
            sfx = self.sfx_on_var.get()
        else:
            self._state_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
            sfx = self.sfx_off_var.get()
        if sfx:
            self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
        self._send_osc(self._state)

    def _release_hold(self):
        if not self._state:
            return
        vol = self.volume_var.get() * self._app.mvol_var.get()
        self._state = False
        self._state_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
        sfx = self.sfx_off_var.get()
        if sfx:
            self._audio.play(resolve_sfx_path(sfx), vol, loop=False)
        self._send_osc(False)

    def to_dict(self) -> dict:
        return {
            "name":         self.name_var.get(),
            "pos_tol":      self.pos_tol_var.get(),
            "rot_tol":      self.rot_tol_var.get(),
            "hold_time":    self.hold_var.get(),
            "record_delay": self.delay_var.get(),
            "confirm_sfx":  self.confirm_sfx_var.get(),
            "target":       self._target,
            "osc_param":    self.osc_param_var.get(),
            "osc_mode":     self.osc_mode_var.get(),
            "osc_val_on":   self.osc_val_on_var.get(),
            "osc_val_off":  self.osc_val_off_var.get(),
            "float_on":     self.float_on_var.get(),
            "float_off":    self.float_off_var.get(),
            "invert":       self.invert_var.get(),
            "sfx_on":       self.sfx_on_var.get(),
            "sfx_off":      self.sfx_off_var.get(),
            "volume":       self.volume_var.get(),
            "state":        self._state,
            "trigger_mode": self.trigger_mode_var.get() if hasattr(self, "trigger_mode_var") else "toggle",
            "delay":        self._delay_var.get() if hasattr(self, "_delay_var") else self._delay,
        }

    def load_dict(self, d: dict):
        self.name_var.set(d.get("name", self.name_var.get()))
        self.pos_tol_var.set(d.get("pos_tol", 0.10))
        self.rot_tol_var.set(d.get("rot_tol", 25.0))
        self.hold_var.set(d.get("hold_time", 0.6))
        self.delay_var.set(d.get("record_delay", 5.0))
        self.confirm_sfx_var.set(d.get("confirm_sfx", ""))
        self._target = d.get("target")
        self.osc_param_var.set(d.get("osc_param", ""))
        self.sfx_on_var.set(d.get("sfx_on", ""))
        self.sfx_off_var.set(d.get("sfx_off", ""))
        self.volume_var.set(d.get("volume", 1.0))
        self._state = d.get("state", False)
        self.osc_mode_var.set(d.get("osc_mode", "bool"))
        self.float_on_var.set(str(d.get("float_on", "1.0")))
        self.float_off_var.set(str(d.get("float_off", "0.0")))
        self.invert_var.set(bool(d.get("invert", False)))
        try:
            self.osc_val_on_var.set(int(d.get("osc_val_on", 1)))
            self.osc_val_off_var.set(int(d.get("osc_val_off", 0)))
        except (ValueError, tk.TclError):
            self.osc_val_on_var.set(1)
            self.osc_val_off_var.set(0)
        if hasattr(self, "trigger_mode_var"):
            self.trigger_mode_var.set(d.get("trigger_mode", "toggle"))
            self._trigger_mode = d.get("trigger_mode", "toggle")
        if hasattr(self, "_delay_var"):
            self._delay = float(d.get("delay", 1.0))
            self._delay_var.set(self._delay)
        self._refresh_value_rows()
        self._on_trigger_mode_change()
        self._refresh_record_btn()
        if self._target:
            self._show_captured_status()
        self._state_lbl.config(
            text="▶ ON" if self._state else "■ OFF",
            foreground=TEXT_ON if self._state else TEXT_DIM,
        )

class PoseFrame:

    def __init__(self, parent: tk.Widget, osc: "OSCLink",
                 audio: "AudioEngine", app: "App",
                 osc_frame: "OSCFrame | None" = None):
        self._osc       = osc
        self._audio     = audio
        self._app       = app
        self._osc_frame = osc_frame
        self.slots: list[_PoseSlotWidget] = []
        self._listening_slot: _PoseSlotWidget | None = None
        self._build(parent)

    def _build(self, parent: tk.Widget):
        hdr = ttk.Frame(parent)
        hdr.pack(fill="x", padx=4, pady=(4, 0))
        ttk.Label(hdr, text="Whole-body poses — strike a pose, hold it, fire.",
                  foreground=TEXT_DIM, font=("Consolas", 8)).pack(side="left")

        self._slots_body = ttk.Frame(parent)
        self._slots_body.pack(fill="x")

        ttk.Button(parent, text="+ ADD POSE",
                   command=lambda: self._add_slot(),
                   style="Accent.TButton"
                   ).pack(anchor="w", padx=4, pady=6)

    def _add_slot(self, data: dict | None = None) -> _PoseSlotWidget:
        w = _PoseSlotWidget(self._slots_body, len(self.slots), self._osc,
                            self._audio, self._app, self)
        if data:
            w.load_dict(data)
        self.slots.append(w)
        return w

    def remove_slot(self, widget: _PoseSlotWidget):
        if widget in self.slots:
            self.slots.remove(widget)
        if self._listening_slot is widget:
            self._listening_slot = None
        widget.destroy_ui()

    def arm_record(self, slot: _PoseSlotWidget):
        self._listening_slot = slot

    def cancel_if_listening(self, slot: _PoseSlotWidget):
        if self._listening_slot is slot:
            self._listening_slot = None

    def handle_button_press(self, hand: str, btn: str):
        if self._listening_slot is not None:
            slot = self._listening_slot
            self._listening_slot = None
            slot.begin_countdown()

    def tick(self):
        for w in list(self.slots):
            w.tick()

    def to_dict(self) -> dict:
        return {"slots": [w.to_dict() for w in self.slots]}

    def load_dict(self, d: dict):
        for w in list(self.slots):
            w.destroy_ui()
        self.slots = []
        self._listening_slot = None
        for sd in d.get("slots", []):
            self._add_slot(sd)

    def preload_files(self) -> list[str]:
        vals: list[str] = []
        for w in self.slots:
            vals += [w.sfx_on_var.get(), w.sfx_off_var.get(), w.confirm_sfx_var.get()]
        return _expand_for_preload(vals)

class App:
    def __init__(self, root: tk.Tk):
        self.root  = root
        root.title("⬡ FIGHT FINDER // SFX SUITE")
        root.resizable(True, True)
        root.minsize(1200, 560)

        active_name = _theme.load_active_name()
        _theme.apply_theme(root, name=active_name)
        _refresh_color_globals()

        self.audio = AudioEngine()
        self.ovr   = OVRInput()
        self.osc   = OSCLink()

        self._avatar_map: dict[str, str] = self._load_avatar_map()
        self._avatar_autoswitch_var = tk.BooleanVar(value=True)
        self._last_seen_avatar_id: str | None = None
        self._pending_avatar_id: str | None = None
        self.osc.on_avatar_change = self._on_avatar_change_thread

        crash_handler.on_crash = self._on_crash

        self._evt_q: queue.Queue = queue.Queue()
        self._current_profile    = "Default"

        self._hand_frames: dict[str, HandFrame | None] = {
            "left": None, "right": None, "head": None,
        }
        self._swing:               dict[str, SwingHandler]  = {}
        self._dev_map:             dict[str, int | None]    = {}
        self._music_strip:         "MusicStrip | None"      = None
        self._osc_frame:           "OSCFrame | None"        = None
        self._shoulder_frame:      "ShoulderFrame | None"   = None
        self._pose_frame:          "PoseFrame | None"       = None
        self._mic_engine:          "MicEngine | None"       = None
        self._shared_combo_frame:  "ComboFrame | None"      = None

        self._held_buttons: dict[str, set[str]] = {"left": set(), "right": set()}
        self._active_btn_voices: dict[str, dict[str, list]] = {
            "left":  {b: [] for b in prof.BUTTON_IDS},
            "right": {b: [] for b in prof.BUTTON_IDS},
        }

        self.ovr.on_velocity       = self._on_velocity
        self.ovr.on_button_press   = self._on_button_press
        self.ovr.on_button_release = self._on_button_release
        self.ovr.on_shoulder_grab  = self._on_shoulder_grab

        self._build_header()
        self._build_tabs()
        self._load_profile("Default")
        self._refresh_vmic_ui()

        root.after(50,  self._process_evt_queue)
        root.after(100, self._sync_swing_cfg)
        root.after(50,  self._poll_poses)
        root.after(300, self._poll_avatar_change)

    def _build_header(self):

        accent_bar = tk.Frame(self.root, bg=_theme.get("accent"), height=3)
        accent_bar.pack(fill="x")

        hdr_outer = tk.Frame(self.root, bg=_theme.get("bg_header"))
        hdr_outer.pack(fill="x", padx=0, pady=0)

        hdr_hscroll = ttk.Scrollbar(hdr_outer, orient="horizontal")
        hdr_hscroll.pack(side="bottom", fill="x")

        hdr_canvas = tk.Canvas(hdr_outer, bg=_theme.get("bg_header"),
                               highlightthickness=0, height=1,
                               xscrollcommand=hdr_hscroll.set)
        hdr_canvas.pack(side="top", fill="x", expand=True)
        hdr_hscroll.config(command=hdr_canvas.xview)

        hdr = tk.Frame(hdr_canvas, bg=_theme.get("bg_header"), pady=2)
        hdr_window = hdr_canvas.create_window((0, 0), window=hdr, anchor="nw")

        def _on_hdr_configure(event):

            hdr_canvas.configure(scrollregion=hdr_canvas.bbox("all"))

            hdr_canvas.configure(height=hdr.winfo_reqheight())

            content_w = hdr.winfo_reqwidth()
            canvas_w  = hdr_canvas.winfo_width()
            if content_w <= canvas_w:
                hdr_hscroll.pack_forget()
            else:
                hdr_hscroll.pack(side="bottom", fill="x")

        def _on_canvas_resize(event):

            hdr_canvas.configure(height=hdr.winfo_reqheight())
            content_w = hdr.winfo_reqwidth()
            canvas_w  = event.width
            if content_w <= canvas_w:
                hdr_hscroll.pack_forget()
            else:
                hdr_hscroll.pack(side="bottom", fill="x")

        hdr.bind("<Configure>", _on_hdr_configure)
        hdr_canvas.bind("<Configure>", _on_canvas_resize)

        self._hdr_canvas = hdr_canvas
        self._hdr_outer  = hdr_outer

        cf = ttk.LabelFrame(hdr, text="OVR", style="Header.TLabelframe")
        cf.pack(side="left", fill="y", padx=(6, 3), pady=3)

        btn_row = ttk.Frame(cf, style="Header.TFrame")
        btn_row.pack(fill="x", padx=4, pady=(3, 1))
        ttk.Button(btn_row, text="CONNECT",
                   command=self._connect, style="Accent.TButton").pack(side="left", padx=(0, 2))
        ttk.Button(btn_row, text="DISCONNECT",
                   command=self._disconnect).pack(side="left")

        status_row = ttk.Frame(cf, style="Header.TFrame")
        status_row.pack(fill="x", padx=4, pady=(0, 3))

        self._ovr_dot = tk.Label(status_row, text="●",
                                 bg=_theme.get("bg_header"),
                                 fg=_theme.get("text_dim"),
                                 font=("Consolas", 8))
        self._ovr_dot.pack(side="left", padx=(0, 2))

        self.status_lbl = ttk.Label(status_row, text="■ UNLINKED",
                                    foreground=_theme.get("text_dim"),
                                    font=("Consolas", 8),
                                    style="Header.TLabel")
        self.status_lbl.pack(side="left")
        _info_button(status_row, (
            "Connects to SteamVR via OpenVR. SteamVR needs to already be running "
            "before you hit Connect.\n\n"
            "If it shows an error: make sure SteamVR is open and your headset is "
            "on or in standby. You can reconnect without restarting.\n\n"
            "If the status says 'poll thread died', something crashed in the background — "
            "check the crash log if it appears, then reconnect."
        ), side="left")

        self._crash_btn = ttk.Button(status_row, text="CRASH LOG",
                                     command=self._show_crash_log)

        pf = ttk.LabelFrame(hdr, text="PROFILE", style="Header.TLabelframe")
        pf.pack(side="left", fill="y", padx=3, pady=3)

        self.profile_var = tk.StringVar()
        self.profile_cb  = ttk.Combobox(pf, textvariable=self.profile_var,
                                        width=14, state="readonly")
        self.profile_cb.pack(side="left", padx=(4, 2), pady=4)
        self.profile_cb.bind("<<ComboboxSelected>>", self._on_profile_select)
        ttk.Button(pf, text="NEW",    command=self._new_profile).pack(side="left", padx=1)
        ttk.Button(pf, text="RENAME", command=self._rename_profile).pack(side="left", padx=1)
        ttk.Button(pf, text="SAVE",   command=self._save_profile,
                   style="Accent.TButton").pack(side="left", padx=(1, 4))

        af = ttk.LabelFrame(hdr, text="AVATAR", style="Header.TLabelframe")
        af.pack(side="left", fill="y", padx=3, pady=3)

        ar1 = ttk.Frame(af, style="Header.TFrame")
        ar1.pack(fill="x", padx=4, pady=(3, 1))
        ttk.Label(ar1, text="ID", width=3, foreground=_theme.get("text_dim"),
                  font=("Consolas", 8), style="Header.TLabel").pack(side="left")
        self._avatar_id_var = tk.StringVar(value="(none yet)")
        ttk.Entry(ar1, textvariable=self._avatar_id_var, width=22,
                  state="readonly", font=("Consolas", 8)).pack(side="left")

        ar2 = ttk.Frame(af, style="Header.TFrame")
        ar2.pack(fill="x", padx=4, pady=(0, 1))
        ttk.Checkbutton(ar2, text="Auto-switch",
                        variable=self._avatar_autoswitch_var).pack(side="left")
        ttk.Button(ar2, text="Assign →", width=9,
                   command=self._assign_current_avatar).pack(side="left", padx=(4, 1))
        ttk.Button(ar2, text="Manage…", width=8,
                   command=self._open_avatar_map_manager).pack(side="left", padx=1)
        _info_button(ar2, (
            "VRChat sends the avatar's ID over OSC every time you switch "
            "avatars (/avatar/change). This box shows the last one it saw.\n\n"
            "Wear the avatar you want to link, make sure the profile you "
            "want is loaded, then click \"Assign →\" — next time OSC sees "
            "that avatar ID, this app auto-loads the linked profile.\n\n"
            "\"Manage…\" lists and lets you remove existing avatar→profile "
            "links. Needs the OSC listener running (START, in the OSC tab) "
            "to actually receive avatar IDs."
        ), side="left")

        ar3 = ttk.Frame(af, style="Header.TFrame")
        ar3.pack(fill="x", padx=4, pady=(0, 3))
        self._avatar_status_lbl = ttk.Label(ar3, text="(no avatar seen yet)",
                                            foreground=_theme.get("text_dim"),
                                            font=("Consolas", 8), style="Header.TLabel")
        self._avatar_status_lbl.pack(side="left")

        of = ttk.LabelFrame(hdr, text="OUTPUT", style="Header.TLabelframe")
        of.pack(side="left", fill="y", padx=3, pady=3)

        devs     = list_output_devices()
        dev_opts = ["(none)"] + [f"{i}: {n}" for i, n in devs]
        self._dev_map = {"(none)": None, **{f"{i}: {n}": i for i, n in devs}}
        self.dev1_var = tk.StringVar(value="(none)")
        self.dev2_var = tk.StringVar(value="(none)")
        self.mvol_var = tk.DoubleVar(value=1.0)

        r = ttk.Frame(of, style="Header.TFrame")
        r.pack(fill="x", padx=4, pady=1)
        ttk.Label(r, text="Out 1", width=5, foreground=_theme.get("text_dim"),
                  font=("Consolas", 8), style="Header.TLabel").pack(side="left")
        self._cb_out1 = ttk.Combobox(r, textvariable=self.dev1_var, values=dev_opts, width=24, state="readonly")
        self._cb_out1.pack(side="left")
        self._cb_out1.bind("<<ComboboxSelected>>", self._update_devices)

        r2 = ttk.Frame(of, style="Header.TFrame")
        r2.pack(fill="x", padx=4, pady=1)
        ttk.Label(r2, text="Out 2", width=5, foreground=_theme.get("text_dim"),
                  font=("Consolas", 8), style="Header.TLabel").pack(side="left")
        self._cb_out2 = ttk.Combobox(r2, textvariable=self.dev2_var, values=dev_opts, width=24, state="readonly")
        self._cb_out2.pack(side="left")
        self._cb_out2.bind("<<ComboboxSelected>>", self._update_devices)
        _info_button(r2, (
            "Out 1 and Out 2 both receive music and SFX (swings, button "
            "sounds, combos, etc.) — set Out 2 to a second device if you "
            "want sound on two outputs at once.\n\n"
            "The mic (below) always routes to Out 2."
        ), side="left")

        vr = ttk.Frame(of, style="Header.TFrame")
        vr.pack(fill="x", padx=4, pady=1)
        self._vmic_status_lbl = ttk.Label(vr, text="VMIC: scanning…",
                                           foreground=_theme.get("text_dim"),
                                           font=("Consolas", 8),
                                           style="Header.TLabel")
        self._vmic_status_lbl.pack(side="left")
        self._vmic_btn = ttk.Button(vr, text="INSTALL VMIC",
                                     command=self._on_vmic_button)
        self._vmic_btn.pack(side="left", padx=(6, 0))
        _info_button(vr, (
            "Installs VB-CABLE, a free virtual audio device, so the app "
            "has a built-in mic other software can use.\n\n"
            "Once installed, set Out 2 to \"CABLE Input\" (the app will "
            "do this automatically) and apps like VRChat or Discord can "
            "pick \"CABLE Output\" as their microphone — they'll hear "
            "your SFX/music, and your real mic too if mic passthrough is "
            "enabled, mixed together.\n\n"
            "Windows needs a moment to register the new device after "
            "install — click Rescan Devices once the installer finishes."
        ), side="left")

        mr = ttk.Frame(of, style="Header.TFrame")
        mr.pack(fill="x", padx=4, pady=(1, 3))
        ttk.Label(mr, text="Master", width=6, foreground=_theme.get("text_dim"),
                  font=("Consolas", 8), style="Header.TLabel").pack(side="left")
        ttk.Scale(mr, variable=self.mvol_var, from_=0.0, to=1.0,
                  orient="horizontal", length=100).pack(side="left", padx=2)
        _editable_value(mr, self.mvol_var, 0.0, 1.0, width=5).pack(side="left")

        mf = ttk.LabelFrame(hdr, text="MIC", style="Header.TLabelframe")
        mf.pack(side="left", fill="y", padx=3, pady=3)

        in_devs      = list_input_devices()
        in_opts      = ["(none)"] + [f"{i}: {n}" for i, n in in_devs]
        self._in_dev_map  = {"(none)": None, **{f"{i}: {n}": i for i, n in in_devs}}

        self.mic_in_var  = tk.StringVar(value="(none)")
        self.mic_vol_var = tk.DoubleVar(value=1.0)
        self.mic_on_var  = tk.BooleanVar(value=False)

        r_in = ttk.Frame(mf, style="Header.TFrame")
        r_in.pack(fill="x", padx=4, pady=1)
        ttk.Label(r_in, text="In", width=4, foreground=_theme.get("text_dim"),
                  font=("Consolas", 8), style="Header.TLabel").pack(side="left")
        self._cb_mic_in = ttk.Combobox(r_in, textvariable=self.mic_in_var,
                                        values=in_opts, width=22, state="readonly")
        self._cb_mic_in.pack(side="left")
        self._cb_mic_in.bind("<<ComboboxSelected>>", self._on_mic_device_change)
        _info_button(r_in, (
            "Mic input is mixed live and sent out Out 2 (set above in "
            "Output) — it always uses that device, so there's no separate "
            "mic output to configure."
        ), side="left")

        r_mv = ttk.Frame(mf, style="Header.TFrame")
        r_mv.pack(fill="x", padx=4, pady=(1, 3))
        ttk.Label(r_mv, text="Vol", width=4, foreground=_theme.get("text_dim"),
                  font=("Consolas", 8), style="Header.TLabel").pack(side="left")
        ttk.Scale(r_mv, variable=self.mic_vol_var, from_=0.0, to=2.0,
                  orient="horizontal", length=80).pack(side="left", padx=2)
        _editable_value(r_mv, self.mic_vol_var, 0.0, 2.0, width=5).pack(side="left")
        self.mic_vol_var.trace_add("write", lambda *_: (
            self._mic_engine.set_volume(self.mic_vol_var.get())
            if self._mic_engine else None,
        ))

        mic_status_row = ttk.Frame(mf, style="Header.TFrame")
        mic_status_row.pack(fill="x", padx=4, pady=(0, 1))

        self._mic_dot = tk.Label(mic_status_row, text="●",
                                 bg=_theme.get("bg_header"),
                                 fg=_theme.get("text_dim"),
                                 font=("Consolas", 8))
        self._mic_dot.pack(side="left", padx=(0, 2))

        self._mic_status_lbl = ttk.Label(mic_status_row, text="■ OFF",
                                          foreground=_theme.get("text_dim"),
                                          font=("Consolas", 8),
                                          style="Header.TLabel")
        self._mic_status_lbl.pack(side="left")

        self._mic_toggle_btn = ttk.Button(mf, text="ENABLE MIC",
                                           command=self._toggle_mic,
                                           style="Accent.TButton")
        self._mic_toggle_btn.pack(padx=4, pady=(0, 3))

        self._music_strip = MusicStrip(hdr, self.audio, self)

        self._theme_bar = _theme.ThemeBar(hdr, self.root, self._on_theme_apply)

    def _on_theme_apply(self, name: str, tokens: dict) -> None:
        _theme.apply_theme(self.root, tokens=tokens, name=name)
        _refresh_color_globals()

        if messagebox.askyesno(
            "Restart to apply",
            f'Theme "{name}" saved.\n\n'
            "Some elements won't fully update until the app restarts.\n"
            "Restart now?",
            icon="question",
        ):
            try:
                _relaunch_app()
            except Exception:

                return
            self.root.destroy()
            sys.exit(0)
            return

        try:
            for w in self.root.winfo_children():
                if isinstance(w, tk.Frame) and w.cget("height") == 3:
                    w.configure(bg=_theme.get("accent"))
                    break
        except Exception:
            pass

        try:
            self._hdr_canvas.configure(bg=_theme.get("bg_header"))
            self._hdr_outer.configure(bg=_theme.get("bg_header"))
        except Exception:
            pass

        try:
            self._ovr_dot.configure(bg=_theme.get("bg_header"))
            self._mic_dot.configure(bg=_theme.get("bg_header"))
        except Exception:
            pass

    def _build_tabs(self):
        self._nb = ttk.Notebook(self.root)
        self._nb.pack(fill="both", expand=True, padx=4, pady=(0, 4))
        self._tab_frames:   dict[str, tk.Frame]  = {}
        self._tab_canvases: dict[str, tk.Canvas] = {}
        self._tab_order = ["sfx", "osc"]
        tab_specs = [
            ("sfx", "◈ SFX"),
            ("osc", "⬡ OSC · SHOULDERS · POSES"),
        ]
        for key, label in tab_specs:
            tab = tk.Frame(self._nb, bg=_theme.get("bg_base"))
            self._nb.add(tab, text=label)
            if key == "sfx":
                self._tab_frames["sfx_outer"] = tab

                # Grid (not pack) so every row gets a *proportional* share of
                # height as the window shrinks, instead of fixed-size rows
                # eating their full request first and starving the rest down
                # to zero. minsize keeps every section reachable even when
                # squeezed hard; each section scrolls internally past that.
                tab.rowconfigure(0, weight=3, minsize=90)
                tab.rowconfigure(1, weight=1, minsize=50)
                tab.rowconfigure(2, weight=1, minsize=50)
                tab.columnconfigure(0, weight=1)

                hands_row = tk.Frame(tab, bg=_theme.get("bg_base"))
                hands_row.grid(row=0, column=0, sticky="nsew")
                hands_row.rowconfigure(0, weight=1)
                hands_row.columnconfigure(0, weight=1)
                hands_row.columnconfigure(2, weight=1)

                left_outer, left_canvas, left_body = \
                    self._scroll_section(hands_row, header="◈ LEFT HAND")
                left_outer.grid(row=0, column=0, sticky="nsew")

                div = tk.Frame(hands_row, bg=_theme.get("sep_color"), width=1)
                div.grid(row=0, column=1, sticky="ns")

                right_outer, right_canvas, right_body = \
                    self._scroll_section(hands_row, header="◈ RIGHT HAND")
                right_outer.grid(row=0, column=2, sticky="nsew")

                self._tab_frames["left"]   = left_body
                self._tab_canvases["left"] = left_canvas
                self._tab_frames["right"]   = right_body
                self._tab_canvases["right"] = right_canvas

                head_outer, head_canvas, head_body = \
                    self._scroll_section(tab, header="◉ HEAD")
                head_outer.grid(row=1, column=0, sticky="nsew")
                self._tab_frames["head"]   = head_body
                self._tab_canvases["head"] = head_canvas

                combo_outer, combo_canvas, combo_body = \
                    self._scroll_section(tab, header="◈ BUTTON COMBOS")
                combo_outer.grid(row=2, column=0, sticky="nsew")
                self._tab_frames["combo_bar"]   = combo_body
                self._tab_canvases["combo_bar"] = combo_canvas
            else:
                canvas, scroll = self._scrollable(tab)
                self._tab_frames[key]   = scroll
                self._tab_canvases[key] = canvas

        self._nb.bind_all("<MouseWheel>", self._on_tab_mousewheel)

    def _on_tab_mousewheel(self, event):
        try:
            idx = self._nb.index(self._nb.select())
            key = self._tab_order[idx]
        except Exception:
            return
        scroll_units = int(-1 * (event.delta / 120))
        if key == "sfx":
            x = event.x_root
            y = event.y_root
            for side in ("left", "right", "head", "combo_bar"):
                c = self._tab_canvases.get(side)
                if c:
                    try:
                        cx = c.winfo_rootx()
                        cw = c.winfo_width()
                        cy = c.winfo_rooty()
                        ch = c.winfo_height()
                        if cx <= x < cx + cw and cy <= y < cy + ch:
                            c.yview_scroll(scroll_units, "units")
                            return
                    except Exception:
                        pass
        else:
            canvas = self._tab_canvases.get(key)
            if canvas:
                canvas.yview_scroll(scroll_units, "units")

    def _scroll_section(self, parent: tk.Widget, header: str = "",
                         bg_key: str = "bg_base"
                         ) -> tuple[tk.Frame, tk.Canvas, tk.Frame]:
        """Build a self-contained scrollable section: an outer frame the
        caller grids/packs wherever it likes, holding a header, a canvas,
        a scrollbar, and an inner body frame that content gets added to.
        Returns (outer, canvas, body).
        """
        bg = _theme.get(bg_key)
        outer = tk.Frame(parent, bg=bg)

        if header:
            hdr = tk.Frame(outer, bg=_theme.get("bg_section"))
            hdr.pack(side="top", fill="x")
            tk.Label(hdr, text=header, bg=_theme.get("bg_section"),
                     fg=ACCENT, font=("Consolas", 8, "bold"), pady=4).pack()

        canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0,
                            bg=bg, yscrollincrement=1)
        sb = ttk.Scrollbar(outer, orient="vertical")
        body = tk.Frame(canvas, bg=bg)

        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        win_id = canvas.create_window((0, 0), window=body, anchor="nw")

        def _on_body_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(win_id, width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))

        body.bind("<Configure>", _on_body_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        canvas.configure(yscrollcommand=sb.set)
        sb.configure(command=canvas.yview)

        return outer, canvas, body

    def _make_canvas_scroller(self, parent: tk.Widget) -> tuple[tk.Canvas, tk.Frame]:
        bg = _theme.get("bg_base")
        canvas = tk.Canvas(parent, borderwidth=0, highlightthickness=0,
                           bg=bg, yscrollincrement=1)
        sb = ttk.Scrollbar(parent, orient="vertical")
        inner = tk.Frame(canvas, bg=bg)

        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event):
            canvas.itemconfig(win_id, width=event.width)
            canvas.configure(scrollregion=canvas.bbox("all"))

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _yview_sync(*args):
            canvas.yview(*args)

        canvas.configure(yscrollcommand=sb.set)
        sb.configure(command=_yview_sync)

        return canvas, inner

    def _scrollable(self, parent: tk.Widget) -> tuple[tk.Canvas, tk.Frame]:
        return self._make_canvas_scroller(parent)

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
            "mic_device":    self.mic_in_var.get(),
            "mic_volume":    self.mic_vol_var.get(),
            "mic_enabled":   bool(self._mic_engine and self._mic_engine.running),
        }
        for key in ("left", "right", "head"):
            hf = self._hand_frames.get(key)
            d[key] = hf.to_dict() if hf else {}
        if self._music_strip:
            d["music"] = self._music_strip.to_dict()
        if self._osc_frame:
            d["osc"] = self._osc_frame.to_dict()
        if self._shoulder_frame:
            d["shoulders"] = self._shoulder_frame.to_dict()
        if self._pose_frame:
            d["poses"] = self._pose_frame.to_dict()
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

        self._held_buttons = {"left": set(), "right": set()}
        self._active_btn_voices = {
            "left":  {b: [] for b in prof.BUTTON_IDS},
            "right": {b: [] for b in prof.BUTTON_IDS},
        }

        for hand, has_btns in [("left", True), ("right", True)]:
            tab = self._tab_frames[hand]
            for w in tab.winfo_children():
                w.destroy()
            hf = HandFrame(tab, hand, has_buttons=has_btns,
                           combo_frame=None)
            hf.load_dict(data.get(hand, {}))
            self._hand_frames[hand] = hf
            self._swing[hand] = SwingHandler(hand, self.audio)

        combo_bar = self._tab_frames.get("combo_bar")
        if combo_bar:

            attr = "_shared_combo_widget_container"
            old = getattr(self, attr, None)
            if old:
                try:
                    old.destroy()
                except Exception:
                    pass
            container = tk.Frame(combo_bar, bg=_theme.get("bg_base"))
            container.pack(fill="x")
            setattr(self, attr, container)
            self._shared_combo_frame = ComboFrame(container)

            for h in ("left", "right"):
                hf2 = self._hand_frames.get(h)
                if hf2:
                    hf2._combo_frame = self._shared_combo_frame

            self._shared_combo_frame.load_list(data.get("left", {}).get("combos", []))

        tab = self._tab_frames["head"]
        for w in tab.winfo_children():
            w.destroy()
        hf = HandFrame(tab, "head", has_buttons=False)
        hf.load_dict(data.get("head", {}))
        self._hand_frames["head"] = hf
        self._swing["head"] = SwingHandler("head", self.audio)

        tab = self._tab_frames["osc"]
        for w in tab.winfo_children():
            w.destroy()
        self._osc_frame = OSCFrame(tab, self.osc, self)
        self._osc_frame.load_dict(data.get("osc", {}))

        self._shoulder_frame = ShoulderFrame(
            tab, self.osc, self.audio, self,
            slots_parent=self._osc_frame.shoulders_body(),
            osc_frame=self._osc_frame,
        )
        self._shoulder_frame.load_dict(data.get("shoulders", {}))
        self._osc_frame.set_shoulder_frame(self._shoulder_frame)

        self._pose_frame = PoseFrame(
            self._osc_frame.poses_body(), self.osc, self.audio, self,
            osc_frame=self._osc_frame)
        self._pose_frame.load_dict(data.get("poses", {}))
        self._osc_frame.set_pose_frame(self._pose_frame)

        if self._music_strip:
            self._music_strip.load_dict(data.get("music", {}))

        d1        = data.get("device1") or "(none)"
        d2        = data.get("device2") or "(none)"
        mic_in    = data.get("mic_device") or "(none)"
        mic_vol   = data.get("mic_volume", 1.0)
        mic_en    = data.get("mic_enabled", False)

        if d1 in self._dev_map:
            self.dev1_var.set(d1)
        if d2 in self._dev_map:
            self.dev2_var.set(d2)
        if mic_in in self._in_dev_map:
            self.mic_in_var.set(mic_in)
        self.mic_vol_var.set(mic_vol)
        self.mvol_var.set(data.get("master_volume", 1.0))
        self._autopick_vmic_out2()
        self._update_devices()

        if self._mic_engine:
            self._mic_engine.stop()
            self._mic_engine = None
        self._mic_toggle_btn.config(text="ENABLE MIC")
        self._mic_status_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))
        self._mic_dot.configure(fg=_theme.get("text_dim"))

        if mic_en:
            self._start_mic()

        self._refresh_profile_cb()
        self._preload_profile(data)

    def _preload_profile(self, data: dict):
        raw_files: list[str] = []
        for hand in ("left", "right", "head"):
            hdata = data.get(hand, {})
            f = hdata.get("swing", {}).get("tier", {}).get("file", "")
            if f: raw_files.append(f)
            for btn_cfg in hdata.get("buttons", {}).values():
                for key in ("press", "release"):
                    f = btn_cfg.get(key, "")
                    if f: raw_files.append(f)
            for combo in hdata.get("combos", []):
                f = combo.get("file", "")
                if f: raw_files.append(f)
        files = _expand_for_preload(raw_files)
        if self._music_strip:
            files.extend(self._music_strip.preload_files())
        if self._shoulder_frame:
            files.extend(self._shoulder_frame.preload_files())
        if self._pose_frame:
            files.extend(self._pose_frame.preload_files())
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
        if self._mic_engine and self._mic_engine.running:
            out_dev = self.audio.device2
            if out_dev is not None:
                in_dev = self._in_dev_map.get(self.mic_in_var.get())
                if in_dev is not None:
                    self._mic_engine.set_devices(in_dev, out_dev)
            else:
                self._mic_engine.stop()
                self._mic_status_lbl.config(text="■ OFF", foreground=_theme.get("text_dim"))

    def _autopick_vmic_out2(self):
        if self.dev2_var.get() != "(none)":
            return
        idx = virtual_mic.find_cable_input_device()
        if idx is None:
            return
        for label, i in self._dev_map.items():
            if i == idx:
                self.dev2_var.set(label)
                break

    def _refresh_vmic_ui(self):
        installed = virtual_mic.is_installed()
        if installed:
            self._vmic_status_lbl.config(text="▶ VMIC: installed",
                                         foreground=_theme.get("text_on"))
            self._vmic_btn.config(text="RESCAN")
        else:
            self._vmic_status_lbl.config(text="■ VMIC: absent",
                                         foreground=_theme.get("text_dim"))
            self._vmic_btn.config(text="INSTALL VMIC")

    def _rescan_devices(self):
        virtual_mic.refresh_devices()

        devs     = list_output_devices()
        dev_opts = ["(none)"] + [f"{i}: {n}" for i, n in devs]
        self._dev_map = {"(none)": None, **{f"{i}: {n}": i for i, n in devs}}
        self._cb_out1.config(values=dev_opts)
        self._cb_out2.config(values=dev_opts)
        if self.dev1_var.get() not in self._dev_map:
            self.dev1_var.set("(none)")
        if self.dev2_var.get() not in self._dev_map:
            self.dev2_var.set("(none)")

        in_devs = list_input_devices()
        in_opts = ["(none)"] + [f"{i}: {n}" for i, n in in_devs]
        self._in_dev_map = {"(none)": None, **{f"{i}: {n}": i for i, n in in_devs}}
        self._cb_mic_in.config(values=in_opts)
        if self.mic_in_var.get() not in self._in_dev_map:
            self.mic_in_var.set("(none)")

        self._autopick_vmic_out2()
        self._update_devices()
        self._refresh_vmic_ui()

    def _on_vmic_button(self):
        if virtual_mic.is_installed():
            self._rescan_devices()
            return

        ok, err = virtual_mic.run_installer()
        if not ok:
            messagebox.showerror("Virtual Mic", err, parent=self.root)
            return
        messagebox.showinfo(
            "Virtual Mic",
            "The VB-CABLE installer was launched — approve the UAC "
            "prompt and click through it.\n\n"
            "Once it finishes, click \"Rescan Devices\" (this button) to "
            "pick it up — no restart needed.",
            parent=self.root,
        )

    def _on_mic_device_change(self, *_):
        if self._mic_engine and self._mic_engine.running:
            in_dev  = self._in_dev_map.get(self.mic_in_var.get())
            out_dev = self.audio.device2
            if in_dev is not None and out_dev is not None:
                self._mic_engine.set_devices(in_dev, out_dev)
                self._mic_status_lbl.config(text="▶ LIVE",
                                            foreground=_theme.get("text_on"))
                self._mic_dot.configure(fg=_theme.get("text_on"))
            else:
                self._mic_engine.stop()
                self._mic_status_lbl.config(text="■ OFF",
                                            foreground=_theme.get("text_dim"))
                self._mic_dot.configure(fg=_theme.get("text_dim"))

    def _toggle_mic(self):
        if self._mic_engine and self._mic_engine.running:
            self._mic_engine.stop()
            self._mic_status_lbl.config(text="■ OFF",
                                        foreground=_theme.get("text_dim"))
            self._mic_dot.configure(fg=_theme.get("text_dim"))
            self._mic_toggle_btn.config(text="ENABLE MIC")
        else:
            self._start_mic()

    def _start_mic(self):
        in_dev  = self._in_dev_map.get(self.mic_in_var.get())
        out_dev = self.audio.device2
        if in_dev is None:
            self._mic_status_lbl.config(text="✕ NO INPUT",
                                        foreground=_theme.get("text_error"))
            self._mic_dot.configure(fg=_theme.get("text_error"))
            return
        if out_dev is None:
            self._mic_status_lbl.config(text="✕ SET OUT 2",
                                        foreground=_theme.get("text_error"))
            self._mic_dot.configure(fg=_theme.get("text_error"))
            return
        if self._mic_engine:
            self._mic_engine.stop()
        self._mic_engine = MicEngine(
            self.audio, in_dev, out_dev, volume=self.mic_vol_var.get())
        ok, err = self._mic_engine.start()
        if ok:
            self._mic_status_lbl.config(text="▶ LIVE",
                                        foreground=_theme.get("text_on"))
            self._mic_dot.configure(fg=_theme.get("text_on"))
            self._mic_toggle_btn.config(text="DISABLE MIC")
        else:
            self._mic_engine = None
            self._mic_status_lbl.config(text=f"Error: {err}",
                                        foreground=_theme.get("text_error"))
            self._mic_dot.configure(fg=_theme.get("text_error"))

    def _connect(self):
        ok, err = self.ovr.connect()
        if ok:
            self.status_lbl.config(text="▶ LINKED",
                                   foreground=_theme.get("text_on"))
            self._ovr_dot.configure(fg=_theme.get("text_on"))
        else:
            self.status_lbl.config(text=f"Error: {err or 'unknown'}",
                                   foreground=_theme.get("text_error"))
            self._ovr_dot.configure(fg=_theme.get("text_error"))

    def _disconnect(self):
        self.ovr.disconnect()
        self.status_lbl.config(text="■ UNLINKED",
                               foreground=_theme.get("text_dim"))
        self._ovr_dot.configure(fg=_theme.get("text_dim"))

    def _on_velocity(self, source: str, mag: float):
        sh = self._swing.get(source)
        if sh:
            sh.on_velocity(mag)

    def _on_button_press(self, hand: str, btn: str):
        self._evt_q.put(("press", hand, btn))

    def _on_button_release(self, hand: str, btn: str):
        self._evt_q.put(("release", hand, btn))

    def _on_shoulder_grab(self, shoulder: str):
        self._evt_q.put(("shoulder", shoulder))

    def _process_evt_queue(self):
        try:
            while True:
                evt = self._evt_q.get_nowait()
                kind = evt[0]

                if kind == "shoulder":
                    _, shoulder = evt
                    if self._shoulder_frame:
                        self._shoulder_frame.handle_shoulder_grab(shoulder)
                    continue

                _, hand, btn = evt

                if kind == "press":
                    self._held_buttons.get(hand, set()).add(btn)
                elif kind == "release":
                    self._held_buttons.get(hand, set()).discard(btn)

                if kind == "press" and self._osc_frame:
                    self._osc_frame.handle_button_press(hand, btn)
                if kind == "release" and self._osc_frame:
                    self._osc_frame.handle_button_release(hand, btn)
                if kind == "press" and self._pose_frame:
                    self._pose_frame.handle_button_press(hand, btn)

                combo_fired_for: dict[str, set[str]] = {"left": set(), "right": set()}
                if kind == "press" and hand in ("left", "right"):

                    seen_ids: set[int] = set()
                    all_slots: list[dict] = []
                    for h in ("left", "right"):
                        hf2 = self._hand_frames.get(h)
                        if hf2 and hf2._combo_frame and id(hf2._combo_frame) not in seen_ids:
                            seen_ids.add(id(hf2._combo_frame))
                            all_slots.extend(hf2._combo_frame.to_list())

                    if all_slots:
                        held_l = self._held_buttons.get("left",  set())
                        held_r = self._held_buttons.get("right", set())

                        matches = []
                        for slot in all_slots:
                            raw_btns = slot.get("buttons", [])
                            file     = slot.get("file", "")
                            if not file:
                                continue

                            combo_set: set[tuple[str, str]] = set()
                            for item in raw_btns:
                                if isinstance(item, (list, tuple)) and len(item) == 2:
                                    combo_set.add((item[0], item[1]))
                                else:
                                    combo_set.add((hand, item))
                            if len(combo_set) < 2:
                                continue

                            satisfied = all(
                                (btn in held_l if h == "left" else btn in held_r)
                                for h, btn in combo_set
                            )
                            if not satisfied:
                                continue
                            matches.append((combo_set, file, slot.get("volume", 1.0)))

                        if matches:
                            combo_set, file, vol_mult = max(matches, key=lambda m: len(m[0]))
                            for h, btn in combo_set:
                                voices = self._active_btn_voices.get(h, {}).get(btn, [])
                                for v in voices:
                                    v.stop()
                                if h in self._active_btn_voices:
                                    self._active_btn_voices[h][btn] = []
                                combo_fired_for[h].add(btn)
                            vol = vol_mult * self.mvol_var.get()
                            self.audio.play(resolve_sfx_path(file), vol, loop=False)

                hf = self._hand_frames.get(hand)
                if not hf:
                    continue
                if kind == "press" and btn in combo_fired_for.get(hand, set()):
                    continue

                cfg  = hf.get_button_cfg(btn)
                file = cfg["press"] if kind == "press" else cfg["release"]
                vol  = cfg["volume"] * self.mvol_var.get()
                if file:
                    voices = self.audio.play(resolve_sfx_path(file), vol, loop=False)

                    if kind == "press" and hand in self._active_btn_voices:
                        existing = self._active_btn_voices[hand].get(btn, [])

                        existing = [v for v in existing if not v.done]
                        existing.extend(voices)
                        self._active_btn_voices[hand][btn] = existing

        except queue.Empty:
            pass
        self.root.after(50, self._process_evt_queue)

    def _sync_swing_cfg(self):
        mv = self.mvol_var.get()
        for hand, sh in self._swing.items():
            hf = self._hand_frames.get(hand)
            if hf:
                sh.update(hf.get_swing_cfg(), mv)
        if (self.ovr.connected is False
                and self.status_lbl.cget("text") == "Connected"):
            self.status_lbl.config(text="OVR disconnected (poll thread died)",
                                   foreground=_theme.get("text_error"))
        self.root.after(100, self._sync_swing_cfg)

    def _poll_poses(self):
        if self._pose_frame:
            self._pose_frame.tick()
        self.root.after(50, self._poll_poses)

    def _poll_avatar_change(self):
        aid = self._pending_avatar_id
        if aid is not None and aid != self._last_seen_avatar_id:
            self._last_seen_avatar_id = aid
            self._on_avatar_changed(aid)
        self.root.after(300, self._poll_avatar_change)

    def _on_avatar_change_thread(self, avatar_id: str):

        self._pending_avatar_id = avatar_id

    def _on_avatar_changed(self, avatar_id: str):
        if hasattr(self, "_avatar_id_var"):
            self._avatar_id_var.set(avatar_id)

        target = self._avatar_map.get(avatar_id)
        status_lbl = getattr(self, "_avatar_status_lbl", None)

        if not target:
            if status_lbl:
                status_lbl.config(text="(not assigned)", foreground=_theme.get("text_dim"))
            return

        if not self._avatar_autoswitch_var.get():
            if status_lbl:
                status_lbl.config(text=f"linked to '{target}' (auto-switch off)",
                                  foreground=_theme.get("text_dim"))
            return

        if target == self._current_profile:
            if status_lbl:
                status_lbl.config(text=f"already on '{target}'", foreground=_theme.get("text_dim"))
            return

        if target not in (prof.list_profiles() or []):
            if status_lbl:
                status_lbl.config(text=f"linked profile '{target}' not found",
                                  foreground=_theme.get("text_error"))
            return

        self._save_profile()
        self._load_profile(target)
        if status_lbl:
            status_lbl.config(text=f"→ switched to '{target}'", foreground=_theme.get("text_on"))

    def _avatar_map_path(self) -> Path:
        return _user_data_path("avatar_profile_map.json")

    def _load_avatar_map(self) -> dict[str, str]:
        try:
            p = self._avatar_map_path()
            if p.exists():
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            print(f"[avatar-map] load failed: {e}")
        return {}

    def _save_avatar_map(self):
        try:
            with open(self._avatar_map_path(), "w", encoding="utf-8") as f:
                json.dump(self._avatar_map, f, indent=2)
        except Exception as e:
            print(f"[avatar-map] save failed: {e}")

    def _assign_current_avatar(self):
        aid = self._last_seen_avatar_id
        if not aid:
            messagebox.showinfo(
                "No avatar seen yet",
                "Haven't received an /avatar/change message yet.\n\n"
                "Make sure the OSC listener is running (START, in the OSC "
                "tab), then switch to — or reload — the avatar you want to "
                "assign in VRChat, and try again."
            )
            return
        self._avatar_map[aid] = self._current_profile
        self._save_avatar_map()
        if hasattr(self, "_avatar_status_lbl"):
            self._avatar_status_lbl.config(
                text=f"linked to '{self._current_profile}'", foreground=_theme.get("text_on"))
        messagebox.showinfo("Assigned",
                            f"Current avatar linked to profile '{self._current_profile}'.")

    def _open_avatar_map_manager(self):
        win = tk.Toplevel(self.root)
        win.title("Avatar → Profile Map")
        win.configure(bg=_theme.get("bg_base"))
        win.geometry("440x320")
        win.transient(self.root)

        body = ttk.Frame(win)
        body.pack(fill="both", expand=True, padx=10, pady=10)

        if not self._avatar_map:
            ttk.Label(body, text="No avatars assigned yet.\n\n"
                                  "Switch to an avatar in VRChat, then use "
                                  "\"Assign →\" in the AVATAR section of the header.",
                      foreground=_theme.get("text_dim"), justify="center").pack(pady=30)
            return

        canvas = tk.Canvas(body, bg=_theme.get("bg_base"), highlightthickness=0)
        sb     = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        inner  = ttk.Frame(canvas)
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        for aid, prof_name in sorted(self._avatar_map.items(), key=lambda kv: kv[1].lower()):
            row = ttk.Frame(inner)
            row.pack(fill="x", pady=2, padx=2)
            ttk.Label(row, text=prof_name, width=14, font=("Consolas", 9, "bold"),
                      foreground=_theme.get("text_on")).pack(side="left")
            ttk.Label(row, text=aid, font=("Consolas", 8),
                      foreground=_theme.get("text_dim")).pack(side="left", padx=6)
            ttk.Button(row, text="✕", width=2,
                       command=lambda a=aid, w=win: self._remove_avatar_map(a, w)
                       ).pack(side="right")

    def _remove_avatar_map(self, avatar_id: str, win: tk.Toplevel):
        self._avatar_map.pop(avatar_id, None)
        self._save_avatar_map()
        win.destroy()
        self._open_avatar_map_manager()

    def _on_crash(self, log_path, tb_text: str):
        try:
            self._save_profile()
        except Exception:
            pass
        try:
            self.root.after(0, lambda: self._show_crash_notice(log_path))
        except Exception:
            pass

    def _show_crash_notice(self, log_path):
        self.status_lbl.config(text="✕ CRASH — profile auto-saved", foreground=_theme.get("text_error"))
        self._crash_btn.pack(side="left", padx=(4, 2))

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
        ttk.Label(toolbar, text=str(path), foreground=TEXT_DIM,
                  font=("Consolas", 8)).pack(side="left")
        ttk.Button(toolbar, text="Open folder",
                   command=lambda: self._open_folder(path.parent)).pack(side="right")

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=4, pady=4)
        sb = ttk.Scrollbar(frame)
        sb.pack(side="right", fill="y")
        txt = tk.Text(frame, wrap="none", font=("Courier New", 9),
                      yscrollcommand=sb.set, relief="flat", borderwidth=0,
                      bg=BG_FIELD, fg=TEXT_MAIN, insertbackground=_theme.get("text_main"),
                      selectbackground=_theme.get("accent_dark"), selectforeground="#ffffff")
        txt.pack(fill="both", expand=True)
        sb.config(command=txt.yview)
        txt.insert("1.0", text)
        txt.config(state="disabled")

        hsb = ttk.Scrollbar(win, orient="horizontal", command=txt.xview)
        hsb.pack(fill="x", padx=4, pady=(0, 4))
        txt.config(xscrollcommand=hsb.set)

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
    try:
        icon_path = _resource_path("ffsxicon.ico")
        if icon_path.exists():
            root.iconbitmap(default=str(icon_path))
    except Exception:
        pass

    crash_handler.install(tk_root=root)

    def _tk_callback_exception(exc_type, exc_value, exc_tb):
        crash_handler._handle(exc_type, exc_value, exc_tb, source="tk callback")

    root.report_callback_exception = _tk_callback_exception

    try:
        app = App(root)
    except Exception:
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
        try:
            app.osc.stop()
        except Exception:
            pass
        try:
            if app._mic_engine:
                app._mic_engine.stop()
        except Exception:
            pass
