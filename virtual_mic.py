

import sys
from pathlib import Path

import sounddevice as sd

_INSTALLER_NAMES = ("VBCABLE_Setup_x64.exe", "VBCABLE_Setup.exe")

_CABLE_INPUT_HINT  = "cable input"    
_CABLE_OUTPUT_HINT = "cable output"   

def _base_dir() -> Path:
    

    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).parent

def installer_path() -> Path | None:
    base = _base_dir()
    for name in _INSTALLER_NAMES:
        p = base / name
        if p.exists():
            return p
    return None

def refresh_devices():
    

    try:
        sd._terminate()
        sd._initialize()
    except Exception:
        pass

def find_cable_input_device() -> int | None:
    

    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and _CABLE_INPUT_HINT in d["name"].lower():
                return i
    except Exception:
        pass
    return None

def find_cable_output_device() -> int | None:
    

    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and _CABLE_OUTPUT_HINT in d["name"].lower():
                return i
    except Exception:
        pass
    return None

def is_installed() -> bool:
    return find_cable_input_device() is not None and find_cable_output_device() is not None

def run_installer() -> tuple[bool, str]:
    

    if sys.platform != "win32":
        return False, "Virtual mic install is Windows-only"

    src = installer_path()
    if src is None:
        return False, (
            "Installer not bundled with this build. Download "
            "VBCABLE_Setup_x64.exe from vb-audio.com/Cable/ and place it "
            "next to the app, then try again."
        )

    import ctypes
    import shutil
    import tempfile

    
    
    
    _VBCABLE_FILES = [
        "VBCABLE_Setup_x64.exe",
        "VBCABLE_ControlPanel.exe",
        "pin_in.ico",
        "pin_out.ico",
        "readme.txt",
        
        "vbaudio_cable64arm_win10.sys",
        "vbaudio_cable64_win10.sys",
        "vbaudio_cable64_win10.cat",
        
        "vbaudio_cable64_2003.sys",
        "vbaudio_cable64_2003.cat",
        "vbaudio_cable64_vista.sys",
        "vbaudio_cable64_vista.cat",
        "vbaudio_cable64_win7.sys",
        "vbaudio_cable64_win7.cat",
        
        "vbaudio_cable_2003.sys",
        "vbaudio_cable_2003.cat",
        "vbaudio_cable_vista.sys",
        "vbaudio_cable_vista.cat",
        "vbaudio_cable_win7.sys",
        "vbaudio_cable_win7.cat",
        "vbaudio_cable_xp.sys",
        "vbaudio_cable_xp.cat",
        
        "vbMmeCable64_2003.inf",
        "vbMmeCable64_vista.inf",
        "vbMmeCable64_win10.inf",
        "vbMmeCable64_win7.inf",
        
        "vbMmeCable_2003.inf",
        "vbMmeCable_vista.inf",
        "vbMmeCable_win7.inf",
        "vbMmeCable_xp.inf",
    ]
    try:
        tmp_dir = Path(tempfile.mkdtemp(prefix="vbcable_"))
        src_dir = src.parent   
        for _fname in _VBCABLE_FILES:
            _fp = src_dir / _fname
            if _fp.exists():
                shutil.copy2(_fp, tmp_dir / _fname)
        dst = tmp_dir / src.name
        if not dst.exists():
            return False, "Installer exe missing from bundle"
    except Exception as e:
        return False, f"Could not stage installer: {e}"

    try:
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", str(dst), "", str(tmp_dir), 1
        )
        
        if ret <= 32:
            return False, f"Could not launch installer (code {ret}) — UAC may have been declined"
        return True, ""
    except Exception as e:
        return False, str(e)
