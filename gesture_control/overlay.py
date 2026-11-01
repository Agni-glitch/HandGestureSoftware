"""UI overlay module: a minimal HUD drawn onto the camera feed."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from config import PointerConfig, UIConfig
from gesture_control.actions import MapperStatus, Mode
from gesture_control.detection import HandData
from gesture_control.recognizer import HAND_CONNECTIONS, Gesture, GestureReading

FONT = cv2.FONT_HERSHEY_SIMPLEX

# Colours are BGR.
WHITE = (240, 240, 240)
MUTED = (165, 165, 165)
ACCENT = (200, 215, 60)
AMBER = (60, 175, 245)
RED = (80, 80, 235)
PANEL = (24, 24, 24)

_GESTURE_NAMES = {
    Gesture.OPEN_PALM: "OPEN PALM",
    Gesture.FIST: "FIST",
    Gesture.PEACE: "PEACE",
    Gesture.POINTER: "POINTER",
    Gesture.PINCH: "PINCH",
}


@dataclass
class HudStats:
    fps: float
    inference_ms: float


def panel(img: np.ndarray, x0: int, y0: int, x1: int, y1: int, alpha: float = 0.62) -> None:
    """Darken a rectangle in place. Only touches that region, so it stays cheap."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
    if x1 <= x0 or y1 <= y0:
        return
    roi = img[y0:y1, x0:x1]
    cv2.addWeighted(roi, 1.0 - alpha, np.full_like(roi, PANEL), alpha, 0.0, dst=roi)


def text(
    img: np.ndarray,
    s: str,
    x: int,
    y: int,
    scale: float = 0.45,
    color: tuple[int, int, int] = WHITE,
    thickness: int = 1,
) -> None:
    cv2.putText(img, s, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


def ascii_text(s: str) -> str:
    """OpenCV's built-in font only covers ASCII."""
    return s.encode("ascii", "replace").decode("ascii")


def text_width(s: str, scale: float, thickness: int = 1) -> int:
    return cv2.getTextSize(s, FONT, scale, thickness)[0][0]


class Overlay:
    def __init__(self, ui: UIConfig, pointer: PointerConfig, debug: bool = False) -> None:
        self.ui = ui
        self.pointer = pointer
        self.debug = debug
        self.custom_names: dict[str, str] = {}

    def draw(
        self,
        img: np.ndarray,
        hand: HandData | None,
        reading: GestureReading | None,
        status: MapperStatus,
        stats: HudStats,
        now: float,
    ) -> None:
        if status.mode is Mode.POINTER:
            self._draw_active_region(img)
        if hand is not None and self.ui.draw_landmarks:
            self._draw_hand(img, hand, status)
        self._draw_status_bar(img, hand is not None, status, stats)
        self._draw_gesture(img, status)
        if status.mode is Mode.VOLUME:
            self._draw_volume(img, status)
        self._draw_flash(img, status, now)
        if self.debug and reading is not None:
            self._draw_debug(img, reading)

    def draw_compact(self, img: np.ndarray, hand: HandData | None, status: MapperStatus) -> None:
        """For the pinned camera. It's shown small, so lines are thicker, and its caption replaces the text."""
        self._draw_active_region(img, thickness=2)
        if hand is None or not self.ui.draw_landmarks:
            return
        h, w = img.shape[:2]
        pts = (hand.image[:, :2] * (w, h)).astype(np.int32)
        for a, b in HAND_CONNECTIONS:
            cv2.line(img, tuple(pts[a]), tuple(pts[b]), ACCENT, 2, cv2.LINE_AA)
        for p in pts:
            cv2.circle(img, tuple(p), 3, WHITE, -1, cv2.LINE_AA)
        tip = tuple(pts[8])
        if status.mouse_down:
            cv2.circle(img, tip, 12, RED, -1, cv2.LINE_AA)
            return
        # Outside the rectangle the cursor can't go any further, so the ring warns about that.
        x0, y0, x1, y1 = self.pointer.active_region
        inside = x0 * w <= tip[0] <= x1 * w and y0 * h <= tip[1] <= y1 * h
        cv2.circle(img, tip, 12, ACCENT if inside else AMBER, 3, cv2.LINE_AA)

    def _draw_status_bar(
        self, img: np.ndarray, tracking: bool, status: MapperStatus, stats: HudStats
    ) -> None:
        panel(img, 10, 10, 262, 38)
        if status.mode is Mode.PAUSED:
            color, label = AMBER, "PAUSED"
        elif tracking:
            color, label = ACCENT, "TRACKING"
        else:
            color, label = MUTED, "NO HAND"
        cv2.circle(img, (24, 24), 5, color, -1, cv2.LINE_AA)
        text(img, label, 36, 29, 0.42, WHITE)
        text(img, f"{stats.fps:4.0f} FPS", 118, 29, 0.42, MUTED)
        text(img, f"{stats.inference_ms:3.0f} ms", 200, 29, 0.42, MUTED)

        if status.mode in (Mode.VOLUME, Mode.POINTER):
            label = status.mode.value
            width = text_width(label, 0.42)
            right = img.shape[1] - 10
            panel(img, right - width - 22, 10, right, 38)
            text(img, label, right - width - 11, 29, 0.42, ACCENT)

    def _draw_gesture(self, img: np.ndarray, status: MapperStatus) -> None:
        name = _GESTURE_NAMES.get(status.gesture) or self.custom_names.get(status.gesture)
        if name is None:
            return
        name = ascii_text(name)
        h, w = img.shape[:2]
        width = text_width(name, 0.7, 2)
        x = (w - width) // 2
        panel(img, x - 16, h - 56, x + width + 16, h - 14)
        text(img, name, x, h - 28, 0.7, WHITE, 2)

    def _draw_volume(self, img: np.ndarray, status: MapperStatus) -> None:
        h, w = img.shape[:2]
        x0, x1 = w - 46, w - 26
        y0, y1 = 70, h - 90
        panel(img, x0 - 14, y0 - 14, x1 + 14, y1 + 40)
        cv2.rectangle(img, (x0, y0), (x1, y1), MUTED, 1, cv2.LINE_AA)

        level_y = int(y1 - status.volume * (y1 - y0))
        color = ACCENT if status.volume_engaged else MUTED
        cv2.rectangle(img, (x0 + 2, level_y), (x1 - 2, y1 - 2), color, -1)
        pct = f"{round(status.volume * 100)}%"
        text(img, pct, x0 + (x1 - x0 - text_width(pct, 0.42)) // 2, y1 + 24, 0.42)

        if not status.volume_engaged:
            # A marker shows where the pinch is, to be moved onto the level to engage.
            target_y = int(y1 - status.volume_target * (y1 - y0))
            cv2.line(img, (x0 - 6, target_y), (x1 + 6, target_y), AMBER, 2, cv2.LINE_AA)
            hint = "Spread fingers to reach the level"
            hint_w = text_width(hint, 0.42)
            hint_x = max((w - hint_w) // 2, 10)
            panel(img, hint_x - 10, 46, hint_x + hint_w + 10, 72)
            text(img, hint, hint_x, 64, 0.42, AMBER)

    def _draw_active_region(self, img: np.ndarray, thickness: int = 1) -> None:
        h, w = img.shape[:2]
        x0, y0, x1, y1 = self.pointer.active_region
        cv2.rectangle(
            img,
            (int(x0 * w), int(y0 * h)),
            (int(x1 * w), int(y1 * h)),
            ACCENT,
            thickness,
            cv2.LINE_AA,
        )

    def _draw_hand(self, img: np.ndarray, hand: HandData, status: MapperStatus) -> None:
        h, w = img.shape[:2]
        pts = (hand.image[:, :2] * (w, h)).astype(np.int32)
        for a, b in HAND_CONNECTIONS:
            cv2.line(img, tuple(pts[a]), tuple(pts[b]), ACCENT, 1, cv2.LINE_AA)
        for p in pts:
            cv2.circle(img, tuple(p), 2, WHITE, -1, cv2.LINE_AA)
        if status.mode is Mode.POINTER:
            tip = tuple(pts[8])
            if status.mouse_down:
                cv2.circle(img, tip, 9, RED, -1, cv2.LINE_AA)
            else:
                cv2.circle(img, tip, 9, ACCENT, 2, cv2.LINE_AA)

    def _draw_flash(self, img: np.ndarray, status: MapperStatus, now: float) -> None:
        if status.flash is None or now - status.flash_at > self.ui.action_flash_s:
            return
        w = img.shape[1]
        flash = ascii_text(status.flash)
        if len(flash) > 42:
            flash = flash[:39] + "..."
        width = text_width(flash, 0.5, 1)
        x = max((w - width) // 2, 24)
        panel(img, x - 14, 84, x + width + 14, 116)
        color = AMBER if flash.startswith("Blocked") else ACCENT
        text(img, flash, x, 105, 0.5, color, 1)

    def _draw_debug(self, img: np.ndarray, reading: GestureReading) -> None:
        panel(img, 10, 46, 196, 180)
        y = 64
        for name, state in reading.fingers.items():
            line = f"{name:<7}{reading.pip_angles[name]:4.0f} {state.value[:3]}"
            text(img, line, 18, y, 0.42, MUTED)
            y += 20
        text(img, f"pinch {reading.pinch_ratio:.2f}", 18, y, 0.42, MUTED)
        text(img, f"tilt {reading.tilt_deg:+.0f}", 18, y + 20, 0.42, MUTED)
