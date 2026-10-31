"""Custom gestures: record a hand pose by showing it to the camera, then match it live.

A pose is reduced to numbers that describe its shape, independent of distance to the camera.
A recording stores the typical values of those numbers. Matching is most reliable with the
hand used for the recording: the other hand often works, but measured distances between a
hand and its mirror image are close to the distances between different poses.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from gesture_control.recognizer import (
    FINGER_JOINTS,
    INDEX_MCP,
    MIDDLE_MCP,
    THUMB_TIP,
    WRIST,
    Finger,
    Gesture,
    GestureReading,
)

_THUMB_MCP = 2
_TIPS = (4, 8, 12, 16, 20)
_STATE_VALUE = {Finger.CURLED: 0.0, Finger.UNCERTAIN: 0.5, Finger.EXTENDED: 1.0}

# Which finger is up matters most; how the hand is held refines it.
_STATE_WEIGHT = 2.0
_THUMB_WEIGHT = 1.5
_SPREAD_WEIGHT = 0.8
_DIRECTION_WEIGHT = 0.8

MIN_SAMPLES = 20
# Accept live poses within this distance of the recording. Measured on real photos, different
# poses are at least 0.85 to 0.96 apart, and a false trigger is worse than a missed one...
BASE_THRESHOLD = 0.9
# ...or further if the recording itself varied, so a shaky recording still matches.
THRESHOLD_MARGIN = 1.5
# Recordings that vary more than this were not one steady pose.
MAX_RECORDING_SPREAD = 1.2


def _direction(vector: np.ndarray) -> np.ndarray:
    """Unit direction in the image, with left and right folded together."""
    norm = float(np.linalg.norm(vector)) or 1.0
    return np.array([abs(vector[0]) / norm, vector[1] / norm])


def pose_features(reading: GestureReading, world: np.ndarray, raw_image: np.ndarray, aspect: float) -> np.ndarray:
    palm = max(float(np.linalg.norm(world[WRIST] - world[MIDDLE_MCP])), 1e-6)
    states = [_STATE_VALUE[reading.fingers[name]] for name in FINGER_JOINTS]
    thumb_out = float(np.linalg.norm(world[THUMB_TIP] - world[INDEX_MCP])) / palm
    spreads = [
        float(np.linalg.norm(world[a] - world[b])) / palm for a, b in zip(_TIPS, _TIPS[1:])
    ]
    flat = raw_image[:, :2] * (aspect, 1.0)
    return np.concatenate(
        [
            _STATE_WEIGHT * np.array(states),
            [_THUMB_WEIGHT * thumb_out],
            _SPREAD_WEIGHT * np.array(spreads),
            _DIRECTION_WEIGHT * _direction(flat[MIDDLE_MCP] - flat[WRIST]),
            _DIRECTION_WEIGHT * _direction(flat[THUMB_TIP] - flat[_THUMB_MCP]),
        ]
    )


@dataclass(frozen=True)
class ReadyPose:
    """A pose in the ready-made list. It is judged by rules, so it needs no recording."""

    name: str
    how: str
    up: tuple[str, ...]  # Fingers that must be extended; the others must be curled.
    thumb_out: bool | None = None  # None means the thumb doesn't matter.
    thumb_direction: str | None = None  # "up" or "down".


# None of these match a built-in gesture, so they never clash with one.
READY_POSES = {
    "thumbs_up": ReadyPose("Thumbs up", "Make a fist with your thumb pointing up", (), True, "up"),
    "thumbs_down": ReadyPose("Thumbs down", "Make a fist with your thumb pointing down", (), True, "down"),
    "rock": ReadyPose("Rock sign", "Raise your index and little fingers", ("index", "pinky")),
    "three": ReadyPose("Three fingers", "Raise your index, middle and ring fingers", ("index", "middle", "ring")),
    "call": ReadyPose("Call me", "Stick out your thumb and little finger", ("pinky",), True),
    "pinky": ReadyPose("Little finger", "Raise only your little finger, thumb tucked", ("pinky",), False),
}
_THUMB_DIRECTION_MIN = 0.5


def ready_pose_of(
    reading: GestureReading, world: np.ndarray, raw_image: np.ndarray, aspect: float, thumb_out_ratio: float
) -> str | None:
    """Which ready-made pose this hand is showing, if any."""
    palm = max(float(np.linalg.norm(world[WRIST] - world[MIDDLE_MCP])), 1e-6)
    thumb_out = float(np.linalg.norm(world[THUMB_TIP] - world[INDEX_MCP])) / palm > thumb_out_ratio
    flat = raw_image[:, :2] * (aspect, 1.0)
    thumb = flat[THUMB_TIP] - flat[_THUMB_MCP]
    thumb_y = float(thumb[1] / (np.linalg.norm(thumb) or 1.0))

    for key, pose in READY_POSES.items():
        if any(
            state is not (Finger.EXTENDED if name in pose.up else Finger.CURLED)
            for name, state in reading.fingers.items()
        ):
            continue
        if pose.thumb_out is not None and pose.thumb_out != thumb_out:
            continue
        if pose.thumb_direction == "up" and thumb_y > -_THUMB_DIRECTION_MIN:
            continue
        if pose.thumb_direction == "down" and thumb_y < _THUMB_DIRECTION_MIN:
            continue
        return key
    return None


class RecordingError(ValueError):
    """Why a recording can't be used, worded for the person recording it."""


_BUILTIN_NAMES = {
    Gesture.FIST: "Fist",
    Gesture.PEACE: "Peace sign",
    Gesture.POINTER: "Pointer (air mouse)",
    Gesture.PINCH: "Volume pinch",
    Gesture.OPEN_PALM: "Open palm (swipes)",
}


def build_template(
    samples: list[np.ndarray],
    labels: list[Gesture],
    builtin_enabled=lambda label: True,
    existing: list[tuple[str, np.ndarray, float]] = (),
) -> tuple[list[float], float]:
    """Turn recorded samples into (template, threshold). Raises RecordingError if unusable.

    existing holds (name, template, threshold) of the custom gestures to stay distinct from.
    """
    if len(samples) < MIN_SAMPLES:
        raise RecordingError("Your hand wasn't visible for long enough. Keep it in view and try again.")

    stack = np.array(samples)
    template = np.median(stack, axis=0)
    distances = np.linalg.norm(stack - template, axis=1)
    spread = float(np.percentile(distances, 90))
    if spread > MAX_RECORDING_SPREAD:
        raise RecordingError("Your hand changed shape during the recording. Hold one pose steady and try again.")

    # A pose the app already uses would fire two things at once.
    values, counts = np.unique([str(getattr(item, "value", item)) for item in labels], return_counts=True)
    common = Gesture(values[int(np.argmax(counts))])
    if common is not Gesture.NONE and builtin_enabled(common.value):
        raise RecordingError(
            f"That pose is already used by {_BUILTIN_NAMES[common]}. "
            "Try a different pose, or turn that gesture off first."
        )

    threshold = max(BASE_THRESHOLD, THRESHOLD_MARGIN * spread)
    for name, other, other_threshold in existing:
        if float(np.linalg.norm(template - other)) < max(threshold, other_threshold):
            raise RecordingError(f'That pose is too close to your "{name}" gesture. Try a more different pose.')
    return [round(float(v), 4) for v in template], round(threshold, 4)


def build_outline(landmarks: list[np.ndarray]) -> list[list[float]]:
    """A picture of the recorded hand: its typical 2D landmarks, fitted to a 0 to 1 box."""
    if not landmarks:
        return []
    points = np.median(np.array(landmarks), axis=0)
    low, high = points.min(axis=0), points.max(axis=0)
    extent = max(float((high - low).max()), 1e-6)
    fitted = (points - low) / extent + (1.0 - (high - low) / extent) / 2.0
    return [[round(float(x), 3), round(float(y), 3)] for x, y in fitted]


class CustomMatcher:
    """Finds the closest recorded pose, if any is close enough."""

    def __init__(self, gestures: list[tuple[str, list[float], float]]) -> None:
        self._ids = [g[0] for g in gestures]
        self._templates = np.array([g[1] for g in gestures], dtype=np.float64) if gestures else None
        self._thresholds = np.array([g[2] for g in gestures], dtype=np.float64)

    def match(self, features: np.ndarray) -> str | None:
        if self._templates is None or self._templates.shape[1] != features.size:
            return None
        distances = np.linalg.norm(self._templates - features, axis=1)
        best = int(np.argmin(distances))
        return self._ids[best] if distances[best] <= self._thresholds[best] else None
