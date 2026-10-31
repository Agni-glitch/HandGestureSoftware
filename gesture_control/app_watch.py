"""Finds out which apps are running and which one is in front. Windows only, for now."""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil


@dataclass(frozen=True)
class AppWindow:
    exe: str  # Process name in lower case.
    title: str


def _pid_of(hwnd) -> int:
    pid = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _process_name(pid: int) -> str | None:
    try:
        return psutil.Process(pid).name().lower()
    except psutil.Error:
        return None


class ForegroundWatcher:
    """Reports the front app. Results are cached briefly, as this is asked for every frame."""

    _CACHE_S = 0.25

    def __init__(self) -> None:
        self._checked_at = float("-inf")
        self._value: tuple[int, str] | None = None

    def current(self) -> tuple[int, str] | None:
        """(pid, process name) of the front window, or None if it can't be determined."""
        if sys.platform != "win32":
            return None
        now = time.monotonic()
        if now - self._checked_at >= self._CACHE_S:
            self._checked_at = now
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            pid = _pid_of(hwnd) if hwnd else 0
            name = _process_name(pid) if pid else None
            self._value = (pid, name) if name else None
        return self._value

    def is_own_window(self) -> bool:
        current = self.current()
        return current is not None and current[0] == os.getpid()


def open_windows() -> list[AppWindow]:
    """Apps with a visible window, for offering as apps to connect. One entry per app."""
    if sys.platform != "win32":
        return []
    user32 = ctypes.windll.user32
    found: dict[str, AppWindow] = {}
    own_pid = os.getpid()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) > 0:
            pid = _pid_of(hwnd)
            name = _process_name(pid) if pid != own_pid else None
            if name and name not in found and name not in _SHELL_PROCESSES:
                buffer = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buffer, 256)
                found[name] = AppWindow(name, buffer.value)
        return True

    user32.EnumWindows(visit, 0)
    return sorted(found.values(), key=lambda w: w.exe)


_SHELL_PROCESSES = {"explorer.exe", "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe"}


def running_process_names() -> set[str]:
    names = set()
    for process in psutil.process_iter(["name"]):
        name = process.info.get("name")
        if name:
            names.add(name.lower())
    return names


def start_menu_apps() -> dict[str, str]:
    """Installed apps from the Start menu, as {name: shortcut path}, sorted by name."""
    if sys.platform != "win32":
        return {}
    roots = [
        Path(os.environ.get(variable, "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        for variable in ("APPDATA", "PROGRAMDATA")
    ]
    skip = ("uninstall", "readme", "help", "documentation", "website", "license")
    apps: dict[str, str] = {}
    for root in roots:
        if root.is_dir():
            for link in root.rglob("*.lnk"):
                if not any(word in link.stem.lower() for word in skip):
                    apps.setdefault(link.stem, str(link))
    return dict(sorted(apps.items(), key=lambda item: item[0].lower()))


def friendly_name(exe: str) -> str:
    stem = exe.rsplit(".", 1)[0]
    return stem.replace("_", " ").replace("-", " ").title()
