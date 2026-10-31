"""Shared app state, and the engine running on its own thread."""

from __future__ import annotations

import copy
import gc
import logging
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from config import CALIBRATION_PATH, load_config
from gesture_control.profile import PROFILE_PATH, Profile, load_profile, save_profile

log = logging.getLogger(__name__)

_FRAME_INTERVAL_S = 1 / 15  # The preview doesn't need the full rate, and this saves CPU.
_STATS_INTERVAL_S = 0.2
_PAUSE_HOTKEY_FALLBACK = "<ctrl>+<alt>+g"


class AppState(QObject):
    """Owns the profile. Everything that edits it calls save(), which tells everyone else."""

    changed = Signal()

    def __init__(self, path: Path = PROFILE_PATH, calibration_path: Path = CALIBRATION_PATH) -> None:
        super().__init__()
        self.path = path
        self.calibration_path = calibration_path
        self.profile, self.load_warning = load_profile(path)

    def save(self) -> None:
        try:
            save_profile(self.profile, self.path)
        except OSError as exc:
            log.warning("Could not save settings: %s", exc)
        self.changed.emit()


class EngineController(QObject):
    STOPPED, STARTING, RUNNING, STOPPING = "stopped", "starting", "running", "stopping"

    state_changed = Signal(str)
    failed = Signal(str)
    frame_ready = Signal(object)
    stats_ready = Signal(object)

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.status = self.STOPPED
        self.engine = None
        self.practice = False
        # A silent run only exists to watch your hand for a recording. It can't act.
        self.silent = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        state.changed.connect(self._push_profile)
        if sys.platform == "win32":
            # comtypes sets up COM on whichever thread first imports it, and undoes that on the
            # main thread at exit. If that first import is on the engine thread, the exit undoes
            # setup that Qt owns, and the process crashes while shutting down.
            import comtypes  # noqa: F401

    @property
    def is_active(self) -> bool:
        return self.status in (self.STARTING, self.RUNNING)

    def start(self, practice: bool = False, silent: bool = False) -> None:
        if self.status != self.STOPPED:
            return
        if not self.state.profile.permissions.get("camera"):
            self.failed.emit("The Camera permission is off. Add it on the Permissions page to start.")
            return
        self.practice = practice or silent
        self.silent = silent
        self._stop = threading.Event()
        self._set_status(self.STARTING)
        self._thread = threading.Thread(target=self._run, args=(self._stop,), name="engine", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self.is_active:
            self._set_status(self.STOPPING)
            self._stop.set()

    def toggle_pause(self) -> None:
        if self.engine is not None:
            self.engine.toggle_pause.set()

    def shutdown(self, timeout: float = 5.0) -> None:
        """Stop and wait, for when the whole app is exiting."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _set_status(self, status: str) -> None:
        self.status = status
        self.state_changed.emit(status)

    def _push_profile(self) -> None:
        if self.engine is not None:
            # The engine reads this from another thread, so it gets its own copy.
            self.engine.set_profile(copy.deepcopy(self.state.profile))
            self.engine.preview_enabled = self.state.profile.general.show_preview or self.silent

    def _make_engine(self, profile: Profile):
        # Imported here because MediaPipe takes seconds to load, and the window shouldn't wait.
        from gesture_control.engine import GestureEngine
        from gesture_control.os_control import DryRunController, SystemController

        cfg = load_config(self.state.calibration_path)
        cfg.capture.camera_index = profile.general.camera_index
        os_ctl = DryRunController() if self.practice else SystemController()
        engine = GestureEngine(cfg, os_ctl, profile)
        engine.preview_enabled = profile.general.show_preview or self.silent
        return engine

    def _run(self, stop: threading.Event) -> None:
        """Everything on the engine thread, including COM setup, which is per-thread on Windows."""
        com = None
        if sys.platform == "win32":
            import comtypes as com

            com.CoInitialize()
        hotkey = None
        error = None
        try:
            from gesture_control.engine import start_pause_hotkey

            self.engine = self._make_engine(copy.deepcopy(self.state.profile))
            if not self.silent:
                combo = self.engine.cfg.actions.pause_hotkey or _PAUSE_HOTKEY_FALLBACK
                hotkey = start_pause_hotkey(combo, self.engine.toggle_pause)
            last_frame = last_stats = 0.0

            def on_frame(image, stats) -> None:
                nonlocal last_frame, last_stats
                now = time.monotonic()
                if image is not None and now - last_frame >= _FRAME_INTERVAL_S:
                    last_frame = now
                    self.frame_ready.emit(image)
                if now - last_stats >= _STATS_INTERVAL_S:
                    last_stats = now
                    self.stats_ready.emit(stats)

            self.engine.run(stop, on_frame, on_started=lambda: self._on_engine_started(stop))
        except Exception as exc:
            log.exception("Engine stopped")
            error = str(exc)
        finally:
            if hotkey is not None:
                hotkey.stop()
            self.engine = None
            # COM objects must be gone before COM shuts down on this thread.
            gc.collect()
            if com is not None:
                com.CoUninitialize()
        self._set_status(self.STOPPED)
        if error:
            self.failed.emit(error)

    def _on_engine_started(self, stop: threading.Event) -> None:
        if not stop.is_set():
            self._set_status(self.RUNNING)
