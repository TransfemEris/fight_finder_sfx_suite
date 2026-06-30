
from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, messagebox, simpledialog, ttk
from typing import Any

TOKEN_LABELS: dict[str, str] = {
    "accent":           "Accent",
    "accent_dark":      "Accent Dark",
    "accent_dim":       "Accent Dim",
    "bg_header":        "BG Header",
    "bg_section":       "BG Section",
    "bg_base":          "BG Base",
    "bg_field":         "BG Field",
    "sep_color":        "Separator",
    "text_main":        "Text Main",
    "text_dim":         "Text Dim",
    "text_on":          "Text On (green)",
    "text_error":       "Text Error (red)",
    "text_accent_lbl":  "Text Accent Label",
    "tab_sel_bg":       "Tab Selected BG",
}

PRESETS: dict[str, dict[str, str]] = {
    "Crimson": {
        "accent":          "#c1393c",
        "accent_dark":     "#8e2629",
        "accent_dim":      "#3a2426",
        "bg_header":       "#14171a",
        "bg_section":      "#1b1f24",
        "bg_base":         "#232830",
        "bg_field":        "#14171c",
        "sep_color":       "#3a4149",
        "text_main":       "#e8ebef",
        "text_dim":        "#7d8590",
        "text_on":         "#4ddb8a",
        "text_error":      "#e2696c",
        "text_accent_lbl": "#c1393c",
        "tab_sel_bg":      "#2c323b",
    },
    "Gilded": {
        "accent":          "#d4a843",
        "accent_dark":     "#a07820",
        "accent_dim":      "#2e2412",
        "bg_header":       "#0d0d0d",
        "bg_section":      "#141210",
        "bg_base":         "#1c1914",
        "bg_field":        "#0e0c0a",
        "sep_color":       "#3a3020",
        "text_main":       "#f0ece0",
        "text_dim":        "#847a62",
        "text_on":         "#e8c86a",
        "text_error":      "#cc2b2b",
        "text_accent_lbl": "#d4a843",
        "tab_sel_bg":      "#2a2418",
    },
    "Violet": {
        "accent":          "#7c6af7",
        "accent_dark":     "#5548cc",
        "accent_dim":      "#201e40",
        "bg_header":       "#0f0f1a",
        "bg_section":      "#151525",
        "bg_base":         "#1a1a2e",
        "bg_field":        "#0d0d1c",
        "sep_color":       "#2e2e50",
        "text_main":       "#dddaf5",
        "text_dim":        "#6b6890",
        "text_on":         "#5af0b0",
        "text_error":      "#f06070",
        "text_accent_lbl": "#9d8ff7",
        "tab_sel_bg":      "#25253d",
    },
    "Phosphor": {
        "accent":          "#00ff41",
        "accent_dark":     "#00c030",
        "accent_dim":      "#001a08",
        "bg_header":       "#000000",
        "bg_section":      "#050f05",
        "bg_base":         "#080f08",
        "bg_field":        "#000000",
        "sep_color":       "#1a3a1a",
        "text_main":       "#c8ffc8",
        "text_dim":        "#3a6a3a",
        "text_on":         "#00ff41",
        "text_error":      "#ff3030",
        "text_accent_lbl": "#00ff41",
        "tab_sel_bg":      "#0d1f0d",
    },
    "Aqua": {
        "accent":          "#1fb8c8",
        "accent_dark":     "#0f8a98",
        "accent_dim":      "#0a2535",
        "bg_header":       "#080e18",
        "bg_section":      "#0e1824",
        "bg_base":         "#121e2e",
        "bg_field":        "#080e1a",
        "sep_color":       "#1e3248",
        "text_main":       "#c8dff0",
        "text_dim":        "#4a6a80",
        "text_on":         "#40e8b0",
        "text_error":      "#f06050",
        "text_accent_lbl": "#1fb8c8",
        "tab_sel_bg":      "#182030",
    },
    "Dusk": {
        "accent":          "#c07cf0",
        "accent_dark":     "#8a4acc",
        "accent_dim":      "#28183a",
        "bg_header":       "#100d18",
        "bg_section":      "#181420",
        "bg_base":         "#1e1a28",
        "bg_field":        "#100d1c",
        "sep_color":       "#2e2840",
        "text_main":       "#e8d8f8",
        "text_dim":        "#6a5a80",
        "text_on":         "#80f0c0",
        "text_error":      "#f07888",
        "text_accent_lbl": "#c07cf0",
        "tab_sel_bg":      "#26203a",
    },
    "Abyss": {
        "accent":          "#4fc3f7",
        "accent_dark":     "#0288d1",
        "accent_dim":      "#001830",
        "bg_header":       "#000000",
        "bg_section":      "#030303",
        "bg_base":         "#050505",
        "bg_field":        "#000000",
        "sep_color":       "#111820",
        "text_main":       "#e8f4f8",
        "text_dim":        "#304050",
        "text_on":         "#40e8a0",
        "text_error":      "#f04040",
        "text_accent_lbl": "#4fc3f7",
        "tab_sel_bg":      "#0a1018",
    },
    "Slate": {
        "accent":          "#1565c0",
        "accent_dark":     "#0d47a1",
        "accent_dim":      "#bbdefb",
        "bg_header":       "#e8edf2",
        "bg_section":      "#f0f4f8",
        "bg_base":         "#f8fafc",
        "bg_field":        "#ffffff",
        "sep_color":       "#c8d4e0",
        "text_main":       "#1a2332",
        "text_dim":        "#607080",
        "text_on":         "#2e7d32",
        "text_error":      "#c62828",
        "text_accent_lbl": "#1565c0",
        "tab_sel_bg":      "#dde4ee",
    },
}

_active: dict[str, str] = dict(PRESETS["Crimson"])

ACCENT          = _active["accent"]
ACCENT_DARK     = _active["accent_dark"]
ACCENT_DIM      = _active["accent_dim"]
BG_HEADER       = _active["bg_header"]
BG_SECTION      = _active["bg_section"]
BG_BASE         = _active["bg_base"]
BG_FIELD        = _active["bg_field"]
SEP_COLOR       = _active["sep_color"]
TEXT_MAIN       = _active["text_main"]
TEXT_DIM        = _active["text_dim"]
TEXT_ON         = _active["text_on"]
TEXT_ERROR      = _active["text_error"]

def _sync_uppercase_globals() -> None:
    g = globals()
    g["ACCENT"]      = _active["accent"]
    g["ACCENT_DARK"] = _active["accent_dark"]
    g["ACCENT_DIM"]  = _active["accent_dim"]
    g["BG_HEADER"]   = _active["bg_header"]
    g["BG_SECTION"]  = _active["bg_section"]
    g["BG_BASE"]     = _active["bg_base"]
    g["BG_FIELD"]    = _active["bg_field"]
    g["SEP_COLOR"]   = _active["sep_color"]
    g["TEXT_MAIN"]   = _active["text_main"]
    g["TEXT_DIM"]    = _active["text_dim"]
    g["TEXT_ON"]     = _active["text_on"]
    g["TEXT_ERROR"]  = _active["text_error"]

def get(token: str) -> str:
    return _active[token]

from app_paths import user_path

_THEMES_PATH: Path = user_path("themes.json")

def _load_themes_file() -> dict:
    try:
        return json.loads(_THEMES_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _save_themes_file(data: dict) -> None:
    _THEMES_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

def load_active_name() -> str:
    return _load_themes_file().get("active", "Crimson")

def list_custom() -> dict[str, dict[str, str]]:
    return _load_themes_file().get("custom", {})

def all_theme_names() -> list[str]:
    custom = list(list_custom().keys())
    return list(PRESETS.keys()) + custom

def get_theme_tokens(name: str) -> dict[str, str]:
    if name in PRESETS:
        return dict(PRESETS[name])
    custom = list_custom()
    if name in custom:
        return dict(custom[name])
    return dict(PRESETS["Crimson"])

def save_custom_theme(name: str, tokens: dict[str, str]) -> None:
    data = _load_themes_file()
    custom = data.get("custom", {})
    custom[name] = tokens
    data["custom"] = custom
    data["active"] = name
    _save_themes_file(data)

def delete_custom_theme(name: str) -> None:
    data = _load_themes_file()
    custom = data.get("custom", {})
    custom.pop(name, None)
    data["custom"] = custom
    if data.get("active") == name:
        data["active"] = "Crimson"
    _save_themes_file(data)

def set_active_name(name: str) -> None:
    data = _load_themes_file()
    data["active"] = name
    _save_themes_file(data)

def apply_theme(root: tk.Tk, tokens: dict[str, str] | None = None,
                name: str | None = None) -> None:
    global _active
    if tokens is None:
        tokens = get_theme_tokens(name or "Crimson")
    _active = dict(tokens)

    ACCENT          = tokens["accent"]
    ACCENT_DARK     = tokens["accent_dark"]
    ACCENT_DIM      = tokens["accent_dim"]
    BG_HEADER       = tokens["bg_header"]
    BG_SECTION      = tokens["bg_section"]
    BG_BASE         = tokens["bg_base"]
    BG_FIELD        = tokens["bg_field"]
    SEP_COLOR       = tokens["sep_color"]
    TEXT_MAIN       = tokens["text_main"]
    TEXT_DIM        = tokens["text_dim"]
    TEXT_ON         = tokens["text_on"]
    TAB_SEL_BG      = tokens["tab_sel_bg"]

    s = ttk.Style(root)
    s.theme_use("clam")

    s.configure(".", background=BG_BASE, foreground=TEXT_MAIN,
                font=("Consolas", 9), relief="flat")
    s.configure("TFrame",       background=BG_BASE)
    s.configure("TLabel",       background=BG_BASE, foreground=TEXT_MAIN)
    s.configure("TCheckbutton", background=BG_BASE, foreground=TEXT_MAIN,
                indicatorbackground=BG_FIELD, indicatorforeground=ACCENT)
    s.configure("TRadiobutton", background=BG_BASE, foreground=TEXT_MAIN,
                indicatorbackground=BG_FIELD, indicatorforeground=ACCENT)

    s.configure("TEntry", fieldbackground=BG_FIELD, foreground=TEXT_MAIN,
                insertcolor=TEXT_MAIN, relief="flat", borderwidth=1)
    s.map("TEntry",
          fieldbackground=[("readonly", BG_FIELD), ("disabled", BG_FIELD)],
          foreground=[("readonly", TEXT_MAIN), ("disabled", TEXT_DIM)])

    s.configure("TSpinbox", fieldbackground=BG_FIELD, background=BG_BASE,
                foreground=TEXT_MAIN, insertcolor=TEXT_MAIN,
                arrowcolor=TEXT_DIM, relief="flat", borderwidth=1)
    s.map("TSpinbox",
          fieldbackground=[("readonly", BG_FIELD), ("disabled", BG_FIELD)],
          foreground=[("readonly", TEXT_MAIN), ("disabled", TEXT_DIM)])

    s.configure("TCombobox", fieldbackground=BG_FIELD, foreground=TEXT_MAIN,
                background=BG_BASE, arrowcolor=TEXT_DIM,
                relief="flat", borderwidth=1)
    s.map("TCombobox",
          fieldbackground=[("readonly", BG_FIELD), ("disabled", BG_FIELD)],
          foreground=[("readonly", TEXT_MAIN), ("disabled", TEXT_DIM)],
          selectbackground=[("readonly", BG_FIELD)],
          selectforeground=[("readonly", TEXT_MAIN)],
          arrowcolor=[("readonly", TEXT_DIM), ("disabled", SEP_COLOR)])

    root.option_add("*TCombobox*Listbox.background", BG_FIELD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT_MAIN)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", "{Segoe UI} 9")

    s.configure("TNotebook",     background=BG_HEADER, tabmargins=[2, 4, 0, 0])
    s.configure("TNotebook.Tab", background=BG_BASE, foreground=TEXT_DIM,
                padding=[10, 4], font=("Consolas", 9))
    s.map("TNotebook.Tab",
          background=[("selected", TAB_SEL_BG)],
          foreground=[("selected", TEXT_MAIN)])

    s.configure("TLabelframe",       background=BG_BASE,
                relief="flat", borderwidth=1, bordercolor=ACCENT_DIM)
    s.configure("TLabelframe.Label", background=BG_BASE,
                foreground=ACCENT, font=("Consolas", 9, "bold"))

    s.configure("TScale", background=ACCENT, troughcolor=ACCENT_DIM,
                sliderrelief="flat", sliderthickness=12)
    s.map("TScale", background=[("active", ACCENT_DARK)])

    s.configure("TScrollbar", background="#394149", troughcolor=BG_FIELD,
                arrowcolor=TEXT_DIM, relief="flat", borderwidth=0)

    s.configure("TButton", background=BG_SECTION, foreground=TEXT_MAIN,
                relief="flat", borderwidth=1, bordercolor=SEP_COLOR,
                padding=[8, 3])
    s.map("TButton",
          background=[("active", TAB_SEL_BG), ("pressed", TAB_SEL_BG)],
          foreground=[("disabled", TEXT_DIM)],
          relief=[("pressed", "flat")])

    s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                relief="flat", borderwidth=0, padding=[8, 3],
                font=("Consolas", 9, "bold"))
    s.map("Accent.TButton",
          background=[("active", ACCENT_DARK), ("pressed", ACCENT_DARK)])

    s.configure("Header.TFrame",    background=BG_HEADER)
    s.configure("Header.TLabel",    background=BG_HEADER, foreground=TEXT_MAIN)
    s.configure("Header.TLabelframe", background=BG_HEADER,
                relief="flat", borderwidth=1, bordercolor=SEP_COLOR)
    s.configure("Header.TLabelframe.Label", background=BG_HEADER,
                foreground=ACCENT, font=("Consolas", 8, "bold"))

    s.configure("TSeparator", background=SEP_COLOR)

    s.configure("Dim.TLabel", background=BG_BASE, foreground=TEXT_DIM,
                font=("Consolas", 8))
    s.configure("Dim.Header.TLabel", background=BG_HEADER, foreground=TEXT_DIM,
                font=("Consolas", 8))

    s.configure("Value.TLabel", background=BG_BASE, foreground=ACCENT,
                font=("Segoe UI Mono", 8))

    root.configure(background=BG_HEADER)
    _sync_uppercase_globals()

class Tooltip:

    _DELAY_MS = 500
    _WRAP_PX  = 320

    def __init__(self, widget: tk.Widget, text: str) -> None:
        self._widget  = widget
        self._text    = text
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>",  self._on_enter, add="+")
        widget.bind("<Leave>",  self._on_leave, add="+")
        widget.bind("<Destroy>", self._on_leave, add="+")

    def _on_enter(self, _event: Any = None) -> None:
        self._cancel()
        self._job = self._widget.after(self._DELAY_MS, self._show)

    def _on_leave(self, _event: Any = None) -> None:
        self._cancel()
        self._hide()

    def _cancel(self) -> None:
        if self._job:
            try:
                self._widget.after_cancel(self._job)
            except Exception:
                pass
            self._job = None

    def _show(self) -> None:
        if self._tip:
            return
        tokens = _active
        bg   = tokens.get("bg_section",  "#1b1f24")
        fg   = tokens.get("text_main",   "#e8ebef")
        acc  = tokens.get("accent",      "#c1393c")
        sep  = tokens.get("sep_color",   "#3a4149")

        try:
            x = self._widget.winfo_rootx() + 16
            y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        except Exception:
            return

        tip = tk.Toplevel(self._widget)
        tip.wm_overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        tip.configure(background=acc)

        inner = tk.Frame(tip, bg=bg, padx=10, pady=8)
        inner.pack(padx=1, pady=1)

        tk.Label(
            inner,
            text=self._text,
            wraplength=self._WRAP_PX,
            justify="left",
            bg=bg,
            fg=fg,
            font=("Consolas", 9),
        ).pack()

        tip.wm_geometry(f"+{x}+{y}")
        self._tip = tip

    def _hide(self) -> None:
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None

def attach_tooltip(widget: tk.Widget, text: str) -> Tooltip:
    return Tooltip(widget, text)

class ThemeEditor:

    def __init__(self, root: tk.Tk, current_name: str,
                 on_apply: "callable[[str, dict], None]") -> None:
        self._root        = root
        self._on_apply    = on_apply
        self._edit_tokens: dict[str, str] = get_theme_tokens(current_name)
        self._edit_name   = current_name
        self._swatch_btns: dict[str, tk.Button] = {}

        self._win = tk.Toplevel(root)
        self._win.title("Theme Editor")
        self._win.resizable(False, False)
        self._win.attributes("-topmost", False)
        self._win.configure(background=_active.get("bg_header", "#14171a"))
        self._win.grab_set()

        self._build()

    def _build(self) -> None:
        tokens = _active
        BG_H  = tokens.get("bg_header",  "#14171a")
        BG_B  = tokens.get("bg_base",    "#232830")
        BG_S  = tokens.get("bg_section", "#1b1f24")
        TM    = tokens.get("text_main",  "#e8ebef")
        TD    = tokens.get("text_dim",   "#7d8590")
        ACC   = tokens.get("accent",     "#c1393c")
        SEP   = tokens.get("sep_color",  "#3a4149")

        win = self._win

        title_bar = tk.Frame(win, bg=BG_H)
        title_bar.pack(fill="x", padx=0, pady=0)
        tk.Frame(win, bg=ACC, height=2).pack(fill="x")

        tk.Label(title_bar, text="⬡  THEME EDITOR", bg=BG_H, fg=ACC,
                 font=("Consolas", 10, "bold"), pady=8, padx=12).pack(side="left")

        scroll_outer = tk.Frame(win, bg=BG_B)
        scroll_outer.pack(fill="both", expand=True, padx=0, pady=0)

        canvas = tk.Canvas(scroll_outer, bg=BG_B, highlightthickness=0,
                           width=440, height=380)
        sb = ttk.Scrollbar(scroll_outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        body = tk.Frame(canvas, bg=BG_B)
        win_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>",
                  lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>",
                    lambda e: canvas.itemconfig(win_id, width=e.width))
        canvas.bind("<MouseWheel>",
                    lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

        hdr = tk.Frame(body, bg=BG_S)
        hdr.pack(fill="x", padx=8, pady=(8, 4))
        tk.Label(hdr, text="TOKEN", bg=BG_S, fg=TD,
                 font=("Consolas", 8, "bold"), width=18, anchor="w",
                 padx=8, pady=4).pack(side="left")
        tk.Label(hdr, text="COLOR", bg=BG_S, fg=TD,
                 font=("Consolas", 8, "bold"), width=10, anchor="w").pack(side="left")
        tk.Label(hdr, text="HEX", bg=BG_S, fg=TD,
                 font=("Consolas", 8, "bold"), width=10, anchor="w").pack(side="left")

        for key, label in TOKEN_LABELS.items():
            self._token_row(body, key, label, BG_B, BG_S, TM, TD, ACC, SEP)

        sep_frame = tk.Frame(win, bg=SEP, height=1)
        sep_frame.pack(fill="x")

        prev_outer = tk.Frame(win, bg=BG_H)
        prev_outer.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(prev_outer, text="PREVIEW", bg=BG_H, fg=TD,
                 font=("Consolas", 7, "bold")).pack(anchor="w")

        self._preview_frame = tk.Frame(prev_outer, bg=BG_H, height=32)
        self._preview_frame.pack(fill="x", pady=(4, 0))
        self._preview_frame.pack_propagate(False)
        self._refresh_preview()

        sep2 = tk.Frame(win, bg=SEP, height=1)
        sep2.pack(fill="x")

        btn_row = tk.Frame(win, bg=BG_H)
        btn_row.pack(fill="x", padx=12, pady=8)

        tk.Label(btn_row, text="LOAD PRESET", bg=BG_H, fg=TD,
                 font=("Consolas", 8)).pack(side="left")
        self._preset_var = tk.StringVar(value=self._edit_name)
        preset_cb = ttk.Combobox(btn_row, textvariable=self._preset_var,
                                 values=all_theme_names(), width=16, state="readonly")
        preset_cb.pack(side="left", padx=(4, 0))
        preset_cb.bind("<<ComboboxSelected>>", self._load_preset)

        right = tk.Frame(btn_row, bg=BG_H)
        right.pack(side="right")

        self._delete_btn = tk.Button(
            right, text="DELETE",
            bg=BG_H, fg=TD, relief="flat", font=("Consolas", 9),
            activebackground=BG_H, activeforeground=tokens.get("text_error", "#e2696c"),
            padx=8, pady=3,
            command=self._delete_theme,
        )
        self._delete_btn.pack(side="left", padx=(0, 4))

        tk.Button(
            right, text="SAVE AS NEW",
            bg=tokens.get("bg_section", "#1b1f24"),
            fg=TM, relief="flat", font=("Consolas", 9),
            activebackground=tokens.get("tab_sel_bg", "#2c323b"),
            padx=8, pady=3,
            command=self._save_as_new,
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            right, text="CANCEL",
            bg=tokens.get("bg_section", "#1b1f24"),
            fg=TM, relief="flat", font=("Consolas", 9),
            activebackground=tokens.get("tab_sel_bg", "#2c323b"),
            padx=8, pady=3,
            command=self._win.destroy,
        ).pack(side="left", padx=(0, 4))

        tk.Button(
            right, text="APPLY",
            bg=ACC, fg="#ffffff", relief="flat",
            font=("Consolas", 9, "bold"),
            activebackground=tokens.get("accent_dark", "#8e2629"),
            padx=10, pady=3,
            command=self._apply,
        ).pack(side="left")

        self._update_delete_btn_state()

    def _token_row(self, parent: tk.Widget, key: str, label: str,
                   BG_B: str, BG_S: str, TM: str, TD: str,
                   ACC: str, SEP: str) -> None:
        color = self._edit_tokens.get(key, "#888888")

        row = tk.Frame(parent, bg=BG_B)
        row.pack(fill="x", padx=8, pady=1)

        tk.Frame(row, bg=ACC if key == "accent" else SEP, width=3).pack(
            side="left", fill="y", padx=(0, 6))

        tk.Label(row, text=label, bg=BG_B, fg=TM,
                 font=("Consolas", 9), width=16, anchor="w").pack(side="left")

        swatch = tk.Button(
            row, bg=color, width=4, height=1,
            relief="flat", cursor="hand2",
            activebackground=color,
            command=lambda k=key: self._pick_color(k),
        )
        swatch.pack(side="left", padx=(0, 6))
        self._swatch_btns[key] = swatch

        hex_lbl = tk.Label(row, text=color, bg=BG_B, fg=TD,
                           font=("Segoe UI Mono", 8), width=9, anchor="w")
        hex_lbl.pack(side="left")
        swatch._hex_lbl = hex_lbl

    def _pick_color(self, key: str) -> None:
        current = self._edit_tokens.get(key, "#888888")
        result  = colorchooser.askcolor(color=current, title=f"Pick color: {key}",
                                        parent=self._win)
        if result and result[1]:
            chosen = result[1].lower()
            self._edit_tokens[key] = chosen
            btn = self._swatch_btns[key]
            btn.configure(bg=chosen, activebackground=chosen)
            btn._hex_lbl.configure(text=chosen)
            self._refresh_preview()

    def _refresh_preview(self) -> None:
        for w in self._preview_frame.winfo_children():
            w.destroy()
        preview_tokens = [
            ("accent",    "Accent"),
            ("bg_base",   "Base"),
            ("bg_field",  "Field"),
            ("text_main", "Text"),
            ("text_on",   "On"),
            ("text_error","Error"),
        ]
        bg_h = _active.get("bg_header", "#14171a")
        for token, lbl_text in preview_tokens:
            color = self._edit_tokens.get(token, "#888888")

            lbl_fg = "#ffffff" if _luminance(color) < 0.4 else "#000000"
            cell = tk.Frame(self._preview_frame, bg=color)
            cell.pack(side="left", fill="both", expand=True)
            tk.Label(cell, text=lbl_text, bg=color, fg=lbl_fg,
                     font=("Consolas", 7), pady=4).pack(fill="both", expand=True)

    def _load_preset(self, _event: Any = None) -> None:
        name   = self._preset_var.get()
        tokens = get_theme_tokens(name)
        self._edit_tokens = tokens
        self._edit_name   = name

        BG_B = _active.get("bg_base",    "#232830")
        TD   = _active.get("text_dim",   "#7d8590")
        for key, btn in self._swatch_btns.items():
            color = tokens.get(key, "#888888")
            btn.configure(bg=color, activebackground=color)
            btn._hex_lbl.configure(text=color)
        self._refresh_preview()
        self._update_delete_btn_state()

    def _update_delete_btn_state(self) -> None:
        is_custom = self._edit_name not in PRESETS
        state_fg = _active.get("text_error", "#e2696c") if is_custom \
                   else _active.get("text_dim", "#7d8590")
        self._delete_btn.configure(
            fg=state_fg,
            state="normal" if is_custom else "disabled",
        )

    def _apply(self) -> None:
        name = self._edit_name

        if name not in PRESETS:
            save_custom_theme(name, self._edit_tokens)
        else:
            set_active_name(name)
        self._on_apply(name, self._edit_tokens)
        self._win.destroy()

    def _save_as_new(self) -> None:
        name = simpledialog.askstring(
            "Save Theme As", "THEME NAME:", parent=self._win)
        if not name or not name.strip():
            return
        name = name.strip()
        if name in PRESETS:
            messagebox.showerror(
                "Name taken",
                f'"{name}" is a built-in preset. Choose a different name.',
                parent=self._win,
            )
            return
        save_custom_theme(name, self._edit_tokens)
        self._edit_name = name
        self._preset_var.set(name)
        self._on_apply(name, self._edit_tokens)
        messagebox.showinfo("Saved", f'Theme "{name}" saved.', parent=self._win)
        self._update_delete_btn_state()

    def _delete_theme(self) -> None:
        name = self._edit_name
        if name in PRESETS:
            return
        if not messagebox.askyesno(
                "Delete theme", f'Delete "{name}"?', parent=self._win):
            return
        delete_custom_theme(name)

        self._edit_name = "Crimson"
        self._preset_var.set("Crimson")
        self._load_preset()
        self._on_apply("Crimson", get_theme_tokens("Crimson"))

class ThemeBar:

    def __init__(self, parent: tk.Widget, root: tk.Tk,
                 on_apply: "callable[[str, dict], None]") -> None:
        self._root     = root
        self._on_apply = on_apply

        active_name = load_active_name()

        tokens = _active
        BG_H  = tokens.get("bg_header",  "#14171a")
        TM    = tokens.get("text_main",  "#e8ebef")
        TD    = tokens.get("text_dim",   "#7d8590")
        ACC   = tokens.get("accent",     "#c1393c")

        lf = ttk.LabelFrame(parent, text="THEME", style="Header.TLabelframe")
        lf.pack(side="left", fill="y", padx=3, pady=3)

        row = ttk.Frame(lf, style="Header.TFrame")
        row.pack(padx=4, pady=4)

        self._var = tk.StringVar(value=active_name)
        self._cb  = ttk.Combobox(row, textvariable=self._var,
                                 values=all_theme_names(),
                                 width=14, state="readonly")
        self._cb.pack(side="left", padx=(0, 4))
        self._cb.bind("<<ComboboxSelected>>", self._on_select)

        gear_btn = ttk.Button(row, text="⚙", width=3,
                              command=self._open_editor)
        gear_btn.pack(side="left")
        attach_tooltip(gear_btn, "Open the full theme editor to customize "
                       "every color token, save custom themes, or delete them.")

        if active_name in all_theme_names():
            apply_theme(root, tokens=get_theme_tokens(active_name), name=active_name)

    def _on_select(self, _event: Any = None) -> None:
        self._apply_named(self._var.get())

    def _apply_named(self, name: str) -> None:
        tokens = get_theme_tokens(name)
        set_active_name(name)
        self._on_apply(name, tokens)

    def _open_editor(self) -> None:
        ThemeEditor(self._root, self._var.get(), self._editor_apply)

    def _editor_apply(self, name: str, tokens: dict[str, str]) -> None:
        self._var.set(name)
        self._cb.configure(values=all_theme_names())
        self._on_apply(name, tokens)

    def refresh_list(self) -> None:
        self._cb.configure(values=all_theme_names())

def _luminance(hex_color: str) -> float:
    try:
        h = hex_color.lstrip("#")
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255
    except Exception:
        return 0.5
