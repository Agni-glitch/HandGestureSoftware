"""The only module that touches the operating system: keys, mouse and system volume."""

from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
import sys
import threading
import webbrowser
from typing import Protocol

log = logging.getLogger(__name__)

# Named actions. "+" joins keys of a chord. Names come from pynput.keyboard.Key, or are
# single characters.
_MEDIA_SHORTCUTS = {
    "play_pause": "media_play_pause",
    "mute": "media_volume_mute",
    "media_next": "media_next",
    "media_previous": "media_previous",
    "volume_up": "media_volume_up",
    "volume_down": "media_volume_down",
    "tab_next": "ctrl+tab",
    "tab_previous": "ctrl+shift+tab",
}
_SHORTCUTS = {
    "win32": {
        **_MEDIA_SHORTCUTS,
        "desktop_next": "ctrl+cmd+right",
        "desktop_previous": "ctrl+cmd+left",
        "show_desktop": "cmd+d",
        "switch_app": "alt+tab",
        "screenshot": "cmd+shift+s",
        "copy": "ctrl+c",
        "paste": "ctrl+v",
        "close_window": "alt+f4",
    },
    "darwin": {
        **_MEDIA_SHORTCUTS,
        "desktop_next": "ctrl+right",
        "desktop_previous": "ctrl+left",
        "show_desktop": "cmd+f3",
        "switch_app": "cmd+tab",
        "screenshot": "cmd+shift+4",
        "copy": "cmd+c",
        "paste": "cmd+v",
        "close_window": "cmd+w",
    },
    "linux": {
        **_MEDIA_SHORTCUTS,
        "desktop_next": "ctrl+alt+right",
        "desktop_previous": "ctrl+alt+left",
        "show_desktop": "cmd+d",
        "switch_app": "alt+tab",
        "screenshot": "print_screen",
        "copy": "ctrl+c",
        "paste": "ctrl+v",
        "close_window": "alt+f4",
    },
}

# Everyday names for keys, mapped to the names used above.
KEY_ALIASES = {
    "control": "ctrl",
    "win": "cmd",
    "windows": "cmd",
    "command": "cmd",
    "option": "alt",
    "return": "enter",
    "escape": "esc",
    "del": "delete",
    "pgup": "page_up",
    "pageup": "page_up",
    "pgdn": "page_down",
    "pagedown": "page_down",
    "prtsc": "print_screen",
    "printscreen": "print_screen",
    "spacebar": "space",
}
_NAMED_KEYS = {
    "ctrl", "shift", "alt", "cmd", "enter", "esc", "tab", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end", "page_up", "page_down", "insert",
    "print_screen", "caps_lock", "media_play_pause", "media_next", "media_previous",
    "media_volume_mute", "media_volume_up", "media_volume_down",
    *(f"f{n}" for n in range(1, 21)),
}  # fmt: skip


def is_valid_key(name: str) -> bool:
    return name in _NAMED_KEYS or (len(name) == 1 and name.isprintable() and name != " ")


class OSController(Protocol):
    """What the action mapper needs from the OS. Tests provide a fake one."""

    screen_size: tuple[int, int]

    def trigger(self, action: str) -> None: ...
    def press_keys(self, keys: list[str]) -> None: ...
    def type_text(self, text: str) -> None: ...
    def open_app(self, path: str) -> None: ...
    def open_url(self, url: str) -> None: ...
    def move_cursor(self, x: int, y: int) -> None: ...
    def mouse_down(self) -> None: ...
    def mouse_up(self) -> None: ...
    def double_click(self) -> None: ...
    def get_volume(self) -> float: ...
    def set_volume(self, level: float) -> None: ...
    def refresh_audio_device(self) -> None: ...
    def close(self) -> None: ...


def enable_dpi_awareness() -> None:
    """On Windows, use real pixels, so the cursor can reach the screen edge when scaled."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except OSError:
            ctypes.windll.user32.SetProcessDPIAware()


def get_screen_size() -> tuple[int, int]:
    if sys.platform == "win32":
        metrics = ctypes.windll.user32.GetSystemMetrics
        return metrics(0), metrics(1)
    if sys.platform == "darwin":
        import Quartz  # Installed alongside pynput on macOS.

        bounds = Quartz.CGDisplayBounds(Quartz.CGMainDisplayID())
        return int(bounds.size.width), int(bounds.size.height)
    import tkinter

    root = tkinter.Tk()
    root.withdraw()
    size = root.winfo_screenwidth(), root.winfo_screenheight()
    root.destroy()
    return size


class _WindowsVolume:
    def __init__(self) -> None:
        self._endpoint = None
        self.refresh()

    def refresh(self) -> None:
        from pycaw.pycaw import AudioUtilities

        self._endpoint = AudioUtilities.GetSpeakers().EndpointVolume

    def get(self) -> float:
        return float(self._endpoint.GetMasterVolumeLevelScalar())

    def set(self, level: float) -> None:
        self._endpoint.SetMasterVolumeLevelScalar(level, None)


class _CommandVolume:
    """Volume set through a slow external command, on a thread that skips stale values."""

    def __init__(self) -> None:
        self._pending: float | None = None
        self._wake = threading.Condition()
        threading.Thread(target=self._worker, name="volume", daemon=True).start()

    def refresh(self) -> None:
        pass

    def get(self) -> float:
        raise NotImplementedError

    def _apply(self, level: float) -> None:
        raise NotImplementedError

    def set(self, level: float) -> None:
        with self._wake:
            self._pending = level
            self._wake.notify()

    def _worker(self) -> None:
        while True:
            with self._wake:
                self._wake.wait_for(lambda: self._pending is not None)
                level, self._pending = self._pending, None
            try:
                self._apply(level)
            except (OSError, subprocess.SubprocessError) as exc:
                log.warning("Could not set the volume: %s", exc)


class _MacVolume(_CommandVolume):
    def get(self) -> float:
        out = subprocess.check_output(
            ["osascript", "-e", "output volume of (get volume settings)"], text=True
        )
        return int(out.strip()) / 100.0

    def _apply(self, level: float) -> None:
        subprocess.run(
            ["osascript", "-e", f"set volume output volume {round(level * 100)}"], check=True
        )


class _PulseVolume(_CommandVolume):
    def get(self) -> float:
        out = subprocess.check_output(
            ["pactl", "get-sink-volume", "@DEFAULT_SINK@"], text=True
        )
        return int(re.search(r"(\d+)%", out).group(1)) / 100.0

    def _apply(self, level: float) -> None:
        subprocess.run(
            ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{round(level * 100)}%"], check=True
        )


def _platform_key() -> str:
    return sys.platform if sys.platform in _SHORTCUTS else "linux"


def _make_volume_backend():
    if sys.platform == "win32":
        return _WindowsVolume()
    if sys.platform == "darwin":
        return _MacVolume()
    return _PulseVolume()


class SystemController:
    """Performs actions for real, using pynput and the platform's volume API."""

    def __init__(self) -> None:
        from pynput.keyboard import Controller as KeyboardController
        from pynput.mouse import Button
        from pynput.mouse import Controller as MouseController

        self._keyboard = KeyboardController()
        self._mouse = MouseController()
        self._left_button = Button.left
        self._button_down = False
        self._shortcuts = _SHORTCUTS[_platform_key()]
        self.screen_size = get_screen_size()
        self._volume = None
        self._last_volume = 0.5
        self.refresh_audio_device()

    def trigger(self, action: str) -> None:
        self.press_keys(self._shortcuts[action].split("+"))

    def press_keys(self, keys: list[str]) -> None:
        resolved = [self._resolve_key(k) for k in keys]
        pressed = []
        try:
            for key in resolved:
                self._keyboard.press(key)
                pressed.append(key)
        finally:
            # A key that stays down would break normal typing, so always release.
            for key in reversed(pressed):
                self._keyboard.release(key)

    @staticmethod
    def _resolve_key(name: str):
        from pynput.keyboard import Key

        return name if len(name) == 1 else getattr(Key, name)

    def type_text(self, text: str) -> None:
        self._keyboard.type(text)

    def open_app(self, path: str) -> None:
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    def move_cursor(self, x: int, y: int) -> None:
        self._mouse.position = (x, y)

    def mouse_down(self) -> None:
        if not self._button_down:
            self._mouse.press(self._left_button)
            self._button_down = True

    def mouse_up(self) -> None:
        if self._button_down:
            self._mouse.release(self._left_button)
            self._button_down = False

    def double_click(self) -> None:
        self._mouse.click(self._left_button, 2)

    # Audio devices come and go (headsets, docks, no device at all) and each platform
    # raises its own error types. Losing volume control must not stop the app.
    def get_volume(self) -> float:
        try:
            self._last_volume = self._volume.get()
        except Exception as exc:
            log.warning("Could not read the volume: %s", exc)
        return self._last_volume

    def set_volume(self, level: float) -> None:
        try:
            self._volume.set(level)
        except Exception as exc:
            log.warning("Could not set the volume: %s", exc)

    def refresh_audio_device(self) -> None:
        """Pick up the current default device, which may have changed since the last use."""
        try:
            if self._volume is None:
                self._volume = _make_volume_backend()
            else:
                self._volume.refresh()
        except Exception as exc:
            log.warning("No usable audio device: %s", exc)

    def close(self) -> None:
        self.mouse_up()


class DryRunController:
    """Logs what would happen instead of doing it, for trying out gestures safely."""

    def __init__(self) -> None:
        self.screen_size = (1920, 1080)
        self._volume = 0.5
        self._button_down = False

    def trigger(self, action: str) -> None:
        log.info("[dry-run] action: %s", action)

    def press_keys(self, keys: list[str]) -> None:
        log.info("[dry-run] keys: %s", "+".join(keys))

    def type_text(self, text: str) -> None:
        log.info("[dry-run] type %d characters", len(text))

    def open_app(self, path: str) -> None:
        log.info("[dry-run] open app: %s", path)

    def open_url(self, url: str) -> None:
        log.info("[dry-run] open website: %s", url)

    def move_cursor(self, x: int, y: int) -> None:
        pass

    def mouse_down(self) -> None:
        self._button_down = True
        log.info("[dry-run] mouse down")

    def mouse_up(self) -> None:
        if self._button_down:
            self._button_down = False
            log.info("[dry-run] mouse up")

    def double_click(self) -> None:
        log.info("[dry-run] double click")

    def get_volume(self) -> float:
        return self._volume

    def set_volume(self, level: float) -> None:
        self._volume = level
        log.info("[dry-run] volume: %d%%", round(level * 100))

    def refresh_audio_device(self) -> None:
        pass

    def close(self) -> None:
        self.mouse_up()
