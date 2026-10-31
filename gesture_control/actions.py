"""Action mapper module: turns gestures into OS commands, with modes and cooldowns.

Volume and pointer are modes: once entered they stay on, even as the pose changes,
until a deliberate exit. That is what lets a single pinch mean two different things.

    IDLE    --hold OK-sign pinch-->  VOLUME   (exit: fist/pointer/peace, or hand lost)
    IDLE    --hold pointing pose-->  POINTER  (exit: palm/fist, or hand lost)
    IDLE    fist, peace, custom gestures and open-palm swipes fire one-shot actions

In POINTER, a pinch presses the mouse button and a peace sign double clicks: raising the
middle finger leaves the index fingertip, and so the cursor, where it was.

What each gesture actually does is looked up through the router, at the moment it fires.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from enum import Enum

import numpy as np

from config import AppConfig
from gesture_control.filters import OneEuroFilter
from gesture_control.os_control import OSController
from gesture_control.profile import Profile, default_bindings
from gesture_control.recognizer import Gesture, GestureReading
from gesture_control.router import ProfileRouter
from gesture_control.stability import SWIPE_RIGHT, GestureStabilizer, SwipeDetector

log = logging.getLogger(__name__)

_VOLUME_EXITS = {Gesture.FIST, Gesture.POINTER, Gesture.PEACE}
_POINTER_EXITS = {Gesture.FIST, Gesture.OPEN_PALM}


class Mode(str, Enum):
    IDLE = "IDLE"
    VOLUME = "VOLUME"
    POINTER = "POINTER"
    PAUSED = "PAUSED"


@dataclass
class MapperStatus:
    """Everything the HUD needs to know."""

    mode: Mode
    gesture: Gesture | str
    hold_progress: float
    fired_count: int = 0
    volume: float | None = None
    volume_target: float | None = None
    volume_engaged: bool = False
    mouse_down: bool = False
    flash: str | None = None
    flash_at: float = 0.0


class VolumeSession:
    """Maps pinch distance to volume, with no jump on entry and none on exit.

    A pinch is always near-closed when engaged, so following it straight away would
    drop the volume to zero. Instead the volume waits until the pinch distance reaches
    its current level ("pickup", as on hardware mixers), then tracks it.
    """

    def __init__(self, cfg, os_ctl: OSController) -> None:
        self.cfg = cfg
        self.os = os_ctl
        os_ctl.refresh_audio_device()
        self.level = os_ctl.get_volume()
        self.target = self.level
        self.engaged = False
        self._last_sent = self.level
        self._prev_mapped: float | None = None
        self._smoothed: float | None = None
        self._history: deque[tuple[float, float]] = deque()
        self._last_t = 0.0

    def map_ratio(self, ratio: float) -> float:
        span = self.cfg.max_ratio - self.cfg.min_ratio
        return float(np.clip((ratio - self.cfg.min_ratio) / span, 0.0, 1.0))

    def update(self, ratio: float, t: float) -> None:
        self._last_t = t
        mapped = self.map_ratio(ratio)
        self.target = mapped

        if not self.engaged:
            close = abs(mapped - self.level) <= self.cfg.pickup_tolerance
            crossed = (
                self._prev_mapped is not None
                and (self._prev_mapped - self.level) * (mapped - self.level) <= 0
            )
            self._prev_mapped = mapped
            if not (close or crossed):
                return
            self.engaged = True
            self._smoothed = self.level

        self._smoothed += self.cfg.smoothing * (mapped - self._smoothed)
        self.level = self._smoothed
        self._history.append((t, self.level))
        while len(self._history) > 2 and t - self._history[1][0] > self.cfg.exit_rollback_s:
            self._history.popleft()
        if abs(self.level - self._last_sent) >= self.cfg.min_step:
            self._send(self.level)

    def _send(self, level: float) -> None:
        self.os.set_volume(float(np.clip(level, 0.0, 1.0)))
        self._last_sent = level

    def finish(self) -> None:
        """Undo whatever the hand did on its way out of the pose."""
        if self.engaged and self._history:
            self.level = self._history[0][1]
            self._send(self.level)


class CursorController:
    """Moves the cursor from the index fingertip and turns pinches into mouse presses."""

    def __init__(self, cfg: AppConfig, os_ctl: OSController) -> None:
        self.cfg = cfg.pointer
        self.pinch_off_ratio = cfg.recognizer.pinch_off_ratio
        self.os = os_ctl
        self._filter = OneEuroFilter(
            cfg.filters.cursor_min_cutoff, cfg.filters.cursor_beta, cfg.filters.d_cutoff
        )
        self._pos: np.ndarray | None = None
        self._last_sent: np.ndarray | None = None
        self._press_pos: np.ndarray | None = None
        self._dragging = False
        self._released_at = float("-inf")
        self._pinch_frames = 0
        # Entering pointer mode mid-pinch must not count as a click.
        self._armed = False
        self.button_down = False
        self.clicks_enabled = True

    def update(self, reading: GestureReading, t: float) -> bool:
        """Returns True on the frame a click starts."""
        x0, y0, x1, y1 = self.cfg.active_region
        width, height = self.os.screen_size
        tip_x, tip_y = reading.index_tip
        norm = np.clip(np.array([(tip_x - x0) / (x1 - x0), (tip_y - y0) / (y1 - y0)]), 0.0, 1.0)
        self._pos = self._filter(norm, t) * np.array([width - 1, height - 1])

        clicked = self._update_button(reading, t)
        if not self._cursor_frozen(reading, t):
            self._move()
        return clicked

    def _update_button(self, reading: GestureReading, t: float) -> bool:
        if not reading.pinch_active:
            self._armed = True
            self._pinch_frames = 0
            if self.button_down:
                self.release()
                self._released_at = t
            return False
        self._pinch_frames += 1
        if (
            self.clicks_enabled
            and self._armed
            and not self.button_down
            and self._pinch_frames >= self.cfg.click_hold_frames
        ):
            self.os.mouse_down()
            self.button_down = True
            self._press_pos = self._pos.copy()
            self._dragging = False
            return True
        return False

    def _cursor_frozen(self, reading: GestureReading, t: float) -> bool:
        """Closing a pinch moves the fingertip, which would make clicks miss their target."""
        if self.button_down:
            if not self._dragging:
                moved = float(np.linalg.norm(self._pos - self._press_pos))
                self._dragging = moved >= self.cfg.drag_threshold_px
            return not self._dragging
        if t - self._released_at < self.cfg.post_click_freeze_s:
            return True
        # Stays put from the first frame of a double click, before it has been held long enough.
        if reading.gesture is Gesture.PEACE:
            return True
        return reading.pinch_ratio < self.pinch_off_ratio * self.cfg.freeze_ratio_margin

    def _move(self) -> None:
        if (
            self._last_sent is not None
            and np.linalg.norm(self._pos - self._last_sent) < self.cfg.deadzone_px
        ):
            return
        self.os.move_cursor(int(self._pos[0]), int(self._pos[1]))
        self._last_sent = self._pos.copy()

    def release(self) -> None:
        if self.button_down:
            self.os.mouse_up()
            self.button_down = False


class ActionMapper:
    def __init__(self, cfg: AppConfig, os_ctl: OSController, router: ProfileRouter | None = None) -> None:
        self.cfg = cfg
        self.os = os_ctl
        self.router = router or ProfileRouter(
            Profile(bindings=default_bindings(cfg.actions)), os_ctl
        )
        self.fired_count = 0
        self.stabilizer = GestureStabilizer(cfg.stability)
        self.swipe = SwipeDetector(cfg.swipe)
        self.mode = Mode.IDLE
        self.paused = False
        self._volume: VolumeSession | None = None
        self._cursor: CursorController | None = None
        self._blocked_until = float("-inf")
        self._last_fired: dict[str, float] = {}
        self._last_hand_at = float("-inf")
        self._hand_tracked = False
        self._flash: str | None = None
        self._flash_at = 0.0

    def set_paused(self, paused: bool, t: float) -> None:
        self.paused = paused
        self._leave_mode(t)
        self.stabilizer.reset()
        self.swipe.reset()

    def update(self, reading: GestureReading | None, t: float) -> MapperStatus:
        if reading is None:
            self._on_no_hand(t)
            return self._status()
        self._last_hand_at = t
        self._hand_tracked = True

        changed = self.stabilizer.update(reading.custom or reading.gesture)
        if self.paused:
            return self._status()

        if self.mode is Mode.VOLUME:
            self._update_volume(reading, t)
        elif self.mode is Mode.POINTER:
            self._update_pointer(reading, changed, t)
        else:
            self._update_idle(reading, changed, t)
        return self._status()

    def _on_no_hand(self, t: float) -> None:
        if self._hand_tracked and t - self._last_hand_at > self.cfg.stability.hand_lost_grace_s:
            self._hand_tracked = False
            self.stabilizer.reset()
            self.swipe.reset()
            self._leave_mode(t)

    def _update_idle(self, reading: GestureReading, changed: bool, t: float) -> None:
        stable = self.stabilizer.stable
        is_open = reading.gesture is Gesture.OPEN_PALM
        cx, cy = reading.palm_center
        direction = self.swipe.update(t, cx, cy, is_open)
        if t < self._blocked_until:
            return

        if direction is not None:
            self._fire("swipe_right" if direction == SWIPE_RIGHT else "swipe_left", t)
        elif stable is Gesture.POINTER:
            self._enter_mode("pointer_mode", changed, t)
        elif stable is Gesture.PINCH:
            self._enter_mode("volume_mode", changed, t)
        elif changed and stable is Gesture.FIST:
            self._fire("fist", t)
        elif changed and stable is Gesture.PEACE:
            leaning_left = reading.tilt_deg < -self.cfg.actions.desktop_prev_tilt_deg
            self._fire("peace_left" if leaning_left else "peace", t)
        elif changed and not isinstance(stable, Gesture):
            self._fire(stable, t)

    def _enter_mode(self, gesture_id: str, changed: bool, t: float) -> None:
        if not self.router.is_enabled(gesture_id):
            return
        reason = self.router.mode_block(gesture_id)
        if reason is not None:
            # The pose is checked every frame it is held, so only report it once.
            if changed:
                self._notify(f"Blocked: {reason}", t)
            return
        if gesture_id == "pointer_mode":
            self.mode = Mode.POINTER
            self._cursor = CursorController(self.cfg, self.os)
            self._notify("POINTER ON", t)
        else:
            self.mode = Mode.VOLUME
            self._volume = VolumeSession(self.cfg.volume, self.os)
            self._notify("VOLUME ON", t)

    def _fire(self, gesture_id: str, t: float) -> None:
        if not self.router.is_enabled(gesture_id):
            return
        last = self._last_fired.get(gesture_id, float("-inf"))
        if t - last < self.router.cooldown(gesture_id):
            return
        self._last_fired[gesture_id] = t
        ran, message = self.router.fire(gesture_id)
        if ran:
            self.fired_count += 1
            self._notify(message, t)
        else:
            self._notify(f"Blocked: {message}", t)

    def _update_volume(self, reading: GestureReading, t: float) -> None:
        if self.stabilizer.stable in _VOLUME_EXITS:
            self._leave_mode(t)
            return
        self._volume.update(reading.pinch_ratio, t)

    def _update_pointer(self, reading: GestureReading, changed: bool, t: float) -> None:
        stable = self.stabilizer.stable
        if stable in _POINTER_EXITS:
            self._leave_mode(t)
            return
        self._cursor.clicks_enabled = self.router.is_enabled("click")
        if self._cursor.update(reading, t):
            self._notify("CLICK", t)
        if changed and stable is Gesture.PEACE and self.router.is_enabled("double_click"):
            if not self._cursor.button_down:
                self.os.double_click()
                self._notify("DOUBLE CLICK", t)

    def _leave_mode(self, t: float) -> None:
        if self.mode is Mode.IDLE:
            return
        if self._volume is not None:
            self._volume.finish()
            self._volume = None
        if self._cursor is not None:
            self._cursor.release()
            self._cursor = None
        self.mode = Mode.IDLE
        self.swipe.reset()
        self._blocked_until = t + self.cfg.stability.mode_exit_cooldown_s

    def _notify(self, text: str, t: float) -> None:
        log.info("%s", text)
        self._flash = text
        self._flash_at = t

    def _status(self) -> MapperStatus:
        status = MapperStatus(
            mode=Mode.PAUSED if self.paused else self.mode,
            gesture=self.stabilizer.stable,
            hold_progress=self.stabilizer.progress,
            fired_count=self.fired_count,
            flash=self._flash,
            flash_at=self._flash_at,
        )
        if self._volume is not None:
            status.volume = self._volume.level
            status.volume_target = self._volume.target
            status.volume_engaged = self._volume.engaged
        if self._cursor is not None:
            status.mouse_down = self._cursor.button_down
        return status

    def close(self, t: float) -> None:
        self._leave_mode(t)
        self.router.cancel()
        self.os.close()
