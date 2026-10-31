"""Temporal logic: the hold-to-confirm state machine and the swipe detector."""

from __future__ import annotations

from collections import deque

from config import StabilityConfig, SwipeConfig
from gesture_control.recognizer import Gesture

SWIPE_LEFT = "left"
SWIPE_RIGHT = "right"


class GestureStabilizer:
    """Only reports a pose after it has been seen for N consecutive frames.

    Labels are Gesture values, or the id strings of custom gestures.
    """

    def __init__(self, cfg: StabilityConfig) -> None:
        self.cfg = cfg
        self.reset()

    def reset(self) -> None:
        self.stable: Gesture | str = Gesture.NONE
        self.candidate: Gesture | str = Gesture.NONE
        self.count = 0

    def _required(self, label: Gesture | str) -> int:
        if label == Gesture.NONE:
            return self.cfg.release_frames
        return self.cfg.hold_frames.get(getattr(label, "value", label), 5)

    def update(self, label: Gesture | str) -> bool:
        """Feed one frame's label. Returns True on the frame the stable pose changes."""
        if label == self.candidate:
            self.count += 1
        else:
            self.candidate = label
            self.count = 1
        if self.candidate != self.stable and self.count >= self._required(self.candidate):
            self.stable = self.candidate
            return True
        return False

    @property
    def progress(self) -> float:
        """How far the current candidate is towards being confirmed, from 0 to 1."""
        if self.candidate == Gesture.NONE:
            return 0.0
        if self.candidate == self.stable:
            return 1.0
        return min(self.count / self._required(self.candidate), 1.0)


class SwipeDetector:
    """Spots fast horizontal palm movement.

    This reads every frame directly instead of going through the stabilizer: a fast
    hand blurs, so a run of consecutive open-palm frames is not guaranteed. It requires
    a share of the window to look like an open palm instead.
    """

    def __init__(self, cfg: SwipeConfig) -> None:
        self.cfg = cfg
        self._history: deque[tuple[float, float, float, bool]] = deque()
        self._started_at: float | None = None
        self._last_direction: str | None = None
        self._last_fired_at = float("-inf")

    def reset(self) -> None:
        """Clear the motion history. The return-stroke block deliberately survives this."""
        self._history.clear()
        self._started_at = None

    def update(self, t: float, x: float, y: float, is_open: bool) -> str | None:
        if self._started_at is None:
            self._started_at = t
        self._history.append((t, x, y, is_open))
        while self._history and t - self._history[0][0] > self.cfg.window_s:
            self._history.popleft()

        if t - self._started_at < self.cfg.arm_after_s:
            return None
        if len(self._history) < self.cfg.min_samples:
            return None

        _, x0, y0, _ = self._history[0]
        dx, dy = x - x0, y - y0
        if abs(dx) < self.cfg.min_distance:
            return None
        if abs(dy) > self.cfg.max_vertical_ratio * abs(dx):
            return None
        open_share = sum(1 for s in self._history if s[3]) / len(self._history)
        if open_share < self.cfg.min_open_fraction:
            return None

        direction = SWIPE_RIGHT if dx > 0 else SWIPE_LEFT
        is_return_stroke = (
            self._last_direction is not None
            and direction != self._last_direction
            and t - self._last_fired_at < self.cfg.return_block_s
        )
        self._history.clear()
        if is_return_stroke:
            return None
        self._last_direction = direction
        self._last_fired_at = t
        return direction
