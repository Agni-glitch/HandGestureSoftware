"""Gesture recognizer module: turns one frame of landmarks into a gesture label.

Geometry uses MediaPipe's world landmarks, which are metric and centred on the hand,
so results do not depend on the distance to the camera. Lengths are divided by palm
size so they also hold across different hand sizes. This module judges a single frame;
stability.py decides when a label has lasted long enough to act on.

World landmarks are the model's estimate of 3D shape and are sometimes badly wrong: on
some views of an open hand they report a straight finger as bent by 80 degrees. Finger
state is therefore cross-checked against the image, which can override them. A camera
projection can hide a bend, but it cannot make a bent finger look straight and long, or
a straight finger look sharply bent, so a decisive image reading can be trusted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

import numpy as np

from config import RecognizerConfig

WRIST = 0
THUMB_TIP = 4
INDEX_MCP = 5
INDEX_TIP = 8
MIDDLE_MCP = 9
# (MCP, PIP, DIP, TIP) joints of each finger.
FINGER_JOINTS = {
    "index": (5, 6, 7, 8),
    "middle": (9, 10, 11, 12),
    "ring": (13, 14, 15, 16),
    "pinky": (17, 18, 19, 20),
}
PALM_POINTS = [0, 5, 9, 13, 17]
# Pairs of landmarks joined when drawing a hand.
HAND_CONNECTIONS = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)  # fmt: skip


class Gesture(str, Enum):
    NONE = "NONE"
    OPEN_PALM = "OPEN_PALM"
    FIST = "FIST"
    PEACE = "PEACE"
    POINTER = "POINTER"
    PINCH = "PINCH"


class Finger(Enum):
    EXTENDED = "extended"
    CURLED = "curled"
    UNCERTAIN = "uncertain"


@dataclass
class GestureReading:
    gesture: Gesture
    pinch_active: bool  # Thumb and index are touching (with hysteresis).
    pinch_ratio: float  # Thumb-index distance, in palm lengths.
    tilt_deg: float  # Hand lean from vertical; positive leans right on screen.
    index_tip: tuple[float, float]  # Unsmoothed position, as a fraction of the frame.
    palm_center: tuple[float, float]  # Unsmoothed position, as a fraction of the frame.
    fingers: dict[str, Finger]
    pip_angles: dict[str, float]
    custom: str | None = None  # Id of the matching custom gesture; replaces gesture if set.


def joint_angle(a: np.ndarray, b: np.ndarray, c: np.ndarray) -> float:
    """Angle at b, in degrees. A straight joint is 180."""
    u, v = a - b, c - b
    norm = np.linalg.norm(u) * np.linalg.norm(v)
    if norm < 1e-9:
        return 180.0
    return math.degrees(math.acos(float(np.clip(np.dot(u, v) / norm, -1.0, 1.0))))


class GestureRecognizer:
    def __init__(self, cfg: RecognizerConfig) -> None:
        self.cfg = cfg
        self._pinch_active = False

    def reset(self) -> None:
        self._pinch_active = False

    def recognize(self, world: np.ndarray, raw_image: np.ndarray, aspect: float) -> GestureReading:
        """aspect is the frame's width / height, needed to measure the image undistorted."""
        flat = raw_image[:, :2] * (aspect, 1.0)
        flat_palm = max(float(np.linalg.norm(flat[WRIST] - flat[MIDDLE_MCP])), 1e-6)

        fingers: dict[str, Finger] = {}
        pip_angles: dict[str, float] = {}
        for name, joints in FINGER_JOINTS.items():
            state, pip_angles[name] = self._finger_state(world, joints)
            fingers[name] = self._image_override(flat, flat_palm, joints) or state

        palm_size = max(float(np.linalg.norm(world[WRIST] - world[MIDDLE_MCP])), 1e-6)
        pinch_ratio = float(np.linalg.norm(world[THUMB_TIP] - world[INDEX_TIP])) / palm_size
        threshold = self.cfg.pinch_off_ratio if self._pinch_active else self.cfg.pinch_on_ratio
        self._pinch_active = pinch_ratio < threshold

        thumb_ratio = float(np.linalg.norm(world[THUMB_TIP] - world[INDEX_MCP])) / palm_size
        gesture = classify(
            fingers, self._pinch_active, thumb_ratio <= self.cfg.fist_thumb_max_ratio
        )

        # World x and y share the image's axes (y points down).
        lean = world[MIDDLE_MCP] - world[WRIST]
        tilt_deg = math.degrees(math.atan2(lean[0], -lean[1]))

        palm = raw_image[PALM_POINTS].mean(axis=0)
        return GestureReading(
            gesture=gesture,
            pinch_active=self._pinch_active,
            pinch_ratio=pinch_ratio,
            tilt_deg=tilt_deg,
            index_tip=(float(raw_image[INDEX_TIP][0]), float(raw_image[INDEX_TIP][1])),
            palm_center=(float(palm[0]), float(palm[1])),
            fingers=fingers,
            pip_angles=pip_angles,
        )

    def _finger_state(
        self, world: np.ndarray, joints: tuple[int, int, int, int]
    ) -> tuple[Finger, float]:
        mcp, pip, dip, tip = (world[j] for j in joints)
        wrist = world[WRIST]
        mcp_angle = joint_angle(wrist, mcp, pip)
        pip_angle = joint_angle(mcp, pip, dip)
        dip_angle = joint_angle(pip, dip, tip)
        tip_beyond_pip = np.linalg.norm(tip - wrist) > np.linalg.norm(pip - wrist)

        if (
            pip_angle >= self.cfg.extended_pip_deg
            and dip_angle >= self.cfg.extended_dip_deg
            and mcp_angle >= self.cfg.extended_mcp_deg
            and tip_beyond_pip
        ):
            return Finger.EXTENDED, pip_angle
        if pip_angle <= self.cfg.curled_pip_deg or not tip_beyond_pip:
            return Finger.CURLED, pip_angle
        return Finger.UNCERTAIN, pip_angle

    def _image_override(
        self, flat: np.ndarray, flat_palm: float, joints: tuple[int, int, int, int]
    ) -> Finger | None:
        """The finger's state if the image alone settles it, otherwise None."""
        mcp, pip, dip, tip = (flat[j] for j in joints)
        pip_angle = joint_angle(mcp, pip, dip)
        if pip_angle <= self.cfg.image_bent_deg:
            return Finger.CURLED
        length = float(np.linalg.norm(tip - mcp)) / flat_palm
        if (
            pip_angle >= self.cfg.image_straight_deg
            and joint_angle(pip, dip, tip) >= self.cfg.image_straight_deg
            and length >= self.cfg.image_min_finger_length
        ):
            return Finger.EXTENDED
        return None


def classify(fingers: dict[str, Finger], pinch_active: bool, thumb_tucked: bool) -> Gesture:
    index, middle, ring, pinky = (fingers[n] for n in ("index", "middle", "ring", "pinky"))
    others = (middle, ring, pinky)
    others_up = all(f is Finger.EXTENDED for f in others)
    others_down = all(f is Finger.CURLED for f in others)

    # Checked before the pinch: a thumb wrapped over a fist often touches the index tip.
    if others_down and index is Finger.CURLED:
        return Gesture.FIST if thumb_tucked else Gesture.NONE
    if pinch_active:
        if others_up:
            return Gesture.PINCH
        # A pinch bends the index finger, so it is not required to be extended here.
        if others_down:
            return Gesture.POINTER
        return Gesture.NONE
    if index is Finger.EXTENDED and others_up:
        return Gesture.OPEN_PALM
    if (
        index is Finger.EXTENDED
        and middle is Finger.EXTENDED
        and ring is Finger.CURLED
        and pinky is Finger.CURLED
    ):
        return Gesture.PEACE
    if index is Finger.EXTENDED and others_down:
        return Gesture.POINTER
    return Gesture.NONE
