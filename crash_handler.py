
import sys
import threading
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

# Where crash logs land — next to the script
LOGS_DIR  = Path(__file__).parent / "crash_logs"
LOGS: list[Path] = []          # most recent log paths (capped at 20)
_MAX_LOGS = 20

# Populated by install() so we can show a Tk dialog
_tk_root = None

# Optional hook: called after the log is written with (log_path, tb_text)
# Use this to update a status label etc.
on_crash: Callable[[Path, str], None] | None = None


def _write_log(tb_text: str) -> Path:
    LOGS_DIR.mkdir(exist_ok=True)
    stamp    = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = LOGS_DIR / f"crash_{stamp}.txt"
    log_path.write_text(tb_text, encoding="utf-8")
    LOGS.append(log_path)
    if len(LOGS) > _MAX_LOGS:
        LOGS.pop(0)
    return log_path


def _show_dialog(title: str, message: str, log_path: Path):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = _tk_root
        if root is None:
            root = tk.Tk()
            root.withdraw()
        messagebox.showerror(
            title,
            f"{message}\n\nLog saved to:\n{log_path}",
            parent=root,
        )
    except Exception:
        print(f"[crash] {title}: {message}", file=sys.stderr)
        print(f"[crash] log: {log_path}", file=sys.stderr)


def _handle(exc_type, exc_value, exc_tb, source: str = "main thread"):
    tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
    tb_text  = (
        f"=== Fight Finder SFX Suite — Crash Report ===\n"
        f"Source  : {source}\n"
        f"Time    : {datetime.now().isoformat()}\n"
        f"Python  : {sys.version}\n"
        f"{'=' * 47}\n\n"
        + "".join(tb_lines)
    )
    print(tb_text, file=sys.stderr)
    log_path = _write_log(tb_text)

    if on_crash:
        try:
            on_crash(log_path, tb_text)
        except Exception:
            pass

    _show_dialog(
        "Unexpected Error",
        f"{exc_type.__name__}: {exc_value}",
        log_path,
    )
    return log_path


# ── Public API ────────────────────────────────────────

def install(tk_root=None):
    global _tk_root
    _tk_root = tk_root

    # Main-thread unhandled exceptions
    sys.excepthook = _excepthook

    # Background thread unhandled exceptions (Python 3.8+)
    threading.excepthook = _thread_excepthook


def _excepthook(exc_type, exc_value, exc_tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    _handle(exc_type, exc_value, exc_tb, source="main thread")


def _thread_excepthook(args: threading.ExceptHookArgs):
    if args.exc_type is None or issubclass(args.exc_type, SystemExit):
        return
    name = getattr(args.thread, "name", "unknown thread")
    _handle(args.exc_type, args.exc_value, args.exc_tb, source=f"thread '{name}'")


def wrap(fn: Callable, source: str = "") -> Callable:
    src = source or getattr(fn, "__name__", repr(fn))

    def _wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            _handle(*sys.exc_info(), source=src)

    _wrapped.__name__ = getattr(fn, "__name__", "_wrapped")
    return _wrapped


def log_dir() -> Path:
    return LOGS_DIR
