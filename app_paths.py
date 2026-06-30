import os
import shutil
import sys
from pathlib import Path

APP_NAME = "Fight Finder SFX Suite"


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        base = appdata / APP_NAME
    else:
        base = Path(sys.argv[0]).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def user_path(name: str) -> Path:
    target = base_dir() / name

    if getattr(sys, "frozen", False) and not target.exists():
        old = Path(sys.executable).parent / name
        if old.exists():
            try:
                if old.is_dir():
                    shutil.copytree(old, target)
                else:
                    shutil.copy2(old, target)
            except Exception:
                pass

    return target
