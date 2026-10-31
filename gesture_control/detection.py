"""Detection module: wraps MediaPipe's HandLandmarker and smooths its output."""

from __future__ import annotations

from dataclasses import dataclass

import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import BaseOptions, vision

from config import DetectionConfig, FilterConfig
from gesture_control.filters import OneEuroFilter


@dataclass
class HandData:
    # 21x3 arrays, ordered as in MediaPipe's hand model.
    image: np.ndarray  # Smoothed x/y as a fraction of the frame; used for drawing.
    world: np.ndarray  # Smoothed metres around the hand's centre; used for geometry.
    raw_image: np.ndarray  # Unsmoothed image points; has no lag, used for swipes and cursor.
    aspect: float  # Frame width / height.
    handedness: str
    score: float


def _to_array(landmarks) -> np.ndarray:
    return np.array([(p.x, p.y, p.z) for p in landmarks], dtype=np.float64)


class HandDetector:
    def __init__(self, cfg: DetectionConfig, filters: FilterConfig) -> None:
        if not cfg.model_path.exists():
            raise FileNotFoundError(
                f"Hand model not found at {cfg.model_path}. "
                "Run `python scripts/download_model.py` once while online."
            )
        options = vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(cfg.model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=cfg.num_hands,
            min_hand_detection_confidence=cfg.min_detection_confidence,
            min_hand_presence_confidence=cfg.min_presence_confidence,
            min_tracking_confidence=cfg.min_tracking_confidence,
        )
        self._landmarker = vision.HandLandmarker.create_from_options(options)
        self._last_ts_ms = -1
        self._image_filter = OneEuroFilter(
            filters.landmark_min_cutoff, filters.landmark_beta, filters.d_cutoff
        )
        # World points are in metres, so they span about 1/5 the range of image points,
        # and speeds scale the same way. beta is adjusted to keep the same behaviour.
        self._world_filter = OneEuroFilter(
            filters.landmark_min_cutoff, filters.landmark_beta * 5.0, filters.d_cutoff
        )

    def detect(self, rgb: np.ndarray, timestamp: float) -> HandData | None:
        # VIDEO mode requires strictly increasing timestamps.
        ts_ms = max(int(timestamp * 1000), self._last_ts_ms + 1)
        self._last_ts_ms = ts_ms

        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, ts_ms)
        if not result.hand_landmarks:
            return None

        raw_image = _to_array(result.hand_landmarks[0])
        raw_world = _to_array(result.hand_world_landmarks[0])
        category = result.handedness[0][0]
        return HandData(
            image=self._image_filter(raw_image, timestamp),
            world=self._world_filter(raw_world, timestamp),
            raw_image=raw_image,
            aspect=rgb.shape[1] / rgb.shape[0],
            handedness=category.category_name,
            score=category.score,
        )

    def reset_filters(self) -> None:
        """Forget motion history, so a returning hand does not slide in from its old position."""
        self._image_filter.reset()
        self._world_filter.reset()

    def close(self) -> None:
        self._landmarker.close()
