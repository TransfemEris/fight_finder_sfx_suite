import json
from pathlib import Path

PROFILES_DIR = Path(__file__).parent / "profiles"

BUTTON_IDS = ("trigger", "grip", "primary", "secondary", "thumbstick")


def _ensure():
    PROFILES_DIR.mkdir(exist_ok=True)


def _default_tier() -> dict:
    return {
        "file":          "",
        "vel_threshold": 1.0,
        "time_window":   0.3,
        "volume":        1.0,
    }


def _default_swing() -> dict:
    return {
        "low_threshold": 0.5,
        "tier": _default_tier(),
    }


def _default_hand() -> dict:
    return {
        "swing": _default_swing(),
        "buttons": {
            btn: {"press": "", "release": "", "volume": 1.0}
            for btn in BUTTON_IDS
        },
    }


def _default_head() -> dict:
    return {
        "swing": _default_swing(),
    }


def _default_foot() -> dict:
    return {
        "tracker_serial": "",
        "floor_height":   0.10,
        "footstep_file":  "",
        "footstep_volume": 1.0,
        "swing": _default_swing(),
    }


def _default_controllers() -> dict:
    return {
        "prox_threshold":         0.30,   # metres — enter-range edge fires prox sound
        "prox_file":              "",
        "prox_volume":            1.0,
        "impact_speed_threshold": 1.50,   # m/s closing speed fires impact sound
        "impact_file":            "",
        "impact_volume":          1.0,
    }


def _default_music() -> dict:
    return {
        "file":      "",
        "volume1":   1.0,   # volume for output device 1
        "volume2":   1.0,   # volume for output device 2
        "loop":      True,
    }


def default_profile() -> dict:
    return {
        "device1": None,
        "device2": None,
        "master_volume": 1.0,
        "left":        _default_hand(),
        "right":       _default_hand(),
        "head":        _default_head(),
        "controllers": _default_controllers(),
        "music":       _default_music(),
    }


def list_profiles() -> list[str]:
    _ensure()
    return [p.stem for p in sorted(PROFILES_DIR.glob("*.json"))]


def load_profile(name: str) -> dict:
    path = PROFILES_DIR / f"{name}.json"
    if not path.exists():
        return default_profile()
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    # ── Migration: old multi-tier / old single-file swing → new single tier schema ──
    for source in ("left", "right", "head", "foot_left", "foot_right"):
        s = raw.get(source, {})
        sw = s.get("swing", {})
        if sw and "tier" not in sw:
            if "tiers" in sw:
                # Downgrade from multi-tier: keep the first tier's data
                old_tiers = sw.pop("tiers")
                sw["tier"] = old_tiers[0] if old_tiers else _default_tier()
            else:
                # Very old single-file format
                old_file = sw.get("file", "")
                old_vol  = sw.get("volume", 1.0)
                old_low  = sw.get("low_threshold", 0.5)
                old_high = sw.get("high_threshold", 1.0)
                sw["low_threshold"] = old_low
                sw["tier"] = {
                    "file":          old_file,
                    "vel_threshold": old_high,
                    "time_window":   0.3,
                    "volume":        old_vol,
                }
                for k in ("file", "volume", "high_threshold", "mode"):
                    sw.pop(k, None)
            s["swing"] = sw

    return raw


def save_profile(name: str, data: dict):
    _ensure()
    with open(PROFILES_DIR / f"{name}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def rename_profile(old: str, new: str):
    old_p = PROFILES_DIR / f"{old}.json"
    new_p = PROFILES_DIR / f"{new}.json"
    if old_p.exists():
        old_p.rename(new_p)
