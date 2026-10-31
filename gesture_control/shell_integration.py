"""Windows integration: desktop and startup shortcuts, and the system camera privacy setting."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from config import ROOT

SHORTCUT_NAME = "Hand Gesture Control.lnk"
ENTRY_POINT = ROOT / "app.pyw"
ICON_PATH = ROOT / "assets" / "icon.ico"
_NO_WINDOW = 0x08000000

_CREATE_SCRIPT = """
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:HG_LINK)
$link.TargetPath = $env:HG_TARGET
$link.Arguments = $env:HG_ARGS
$link.WorkingDirectory = $env:HG_DIR
$link.IconLocation = $env:HG_ICON
$link.Description = 'Control your computer with hand gestures'
$link.Save()
$link.FullName
"""


def _powershell(script: str, **env: str) -> str:
    # Values travel in environment variables, so paths never need quoting inside the script.
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        timeout=30,
        creationflags=_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or "PowerShell failed")
    return result.stdout.strip()


def _windowed_python() -> Path:
    """pythonw.exe runs the app without a console window."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def launch_command() -> tuple[Path, str]:
    """The program and arguments that open this app, from the built .exe or from source."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable), ""
    return _windowed_python(), f'"{ENTRY_POINT}"'


def write_shortcut(link: Path, target: Path, arguments: str = "") -> Path:
    full_path = _powershell(
        _CREATE_SCRIPT,
        HG_LINK=str(link),
        HG_TARGET=str(target),
        HG_ARGS=arguments,
        HG_DIR=str(ROOT),
        HG_ICON=str(ICON_PATH),
    )
    return Path(full_path)


def create_shortcut(folder: str = "Desktop", arguments: str = "") -> Path:
    """folder is a Windows special folder: Desktop (which OneDrive may relocate) or Startup."""
    if sys.platform != "win32":
        raise OSError("Shortcuts are only supported on Windows.")
    target, base_arguments = launch_command()
    return write_shortcut(shortcut_path(folder), target, f"{base_arguments} {arguments}".strip())


def shortcut_path(folder: str) -> Path:
    if sys.platform != "win32":
        return Path(os.devnull)
    location = _powershell("[Environment]::GetFolderPath($env:HG_FOLDER)", HG_FOLDER=folder)
    return Path(location) / SHORTCUT_NAME


def set_start_with_windows(enabled: bool) -> None:
    if enabled:
        create_shortcut("Startup", "--minimized")
    else:
        shortcut_path("Startup").unlink(missing_ok=True)


def camera_privacy_status() -> str:
    """Windows' own camera setting for desktop apps: 'Allowed', 'Blocked' or 'Unknown'."""
    if sys.platform != "win32":
        return "Unknown"
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\webcam"
    for subkey in (base, base + r"\NonPackaged"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey) as key:
                if winreg.QueryValueEx(key, "Value")[0] == "Deny":
                    return "Blocked"
        except OSError:
            return "Unknown"
    return "Allowed"


def open_camera_privacy_settings() -> None:
    if sys.platform == "win32":
        os.startfile("ms-settings:privacy-webcam")
