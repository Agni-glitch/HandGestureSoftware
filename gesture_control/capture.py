"""Capture module: webcam streaming, FPS limiting and frame preprocessing.

Frames are read on a background thread into a two-frame queue. Webcams often deliver
frames in pairs, and two slots ride that out without losing frames. A processing loop that
is genuinely too slow loses the oldest ones instead of falling further behind the camera.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

import cv2
import numpy as np

from config import CaptureConfig

log = logging.getLogger(__name__)

_REOPEN_AFTER_FAILURES = 30
_REOPEN_DELAY_S = 1.0


@dataclass
class Frame:
    bgr: np.ndarray  # Full-size (and mirrored) frame for display.
    rgb: np.ndarray  # Possibly downscaled frame for MediaPipe.
    timestamp: float  # time.monotonic() when the frame was read.


def preprocess(raw: np.ndarray, mirror: bool, scale: float) -> tuple[np.ndarray, np.ndarray]:
    """Return the (display_bgr, detection_rgb) pair for a raw camera frame."""
    bgr = cv2.flip(raw, 1) if mirror else raw
    small = bgr
    if scale != 1.0:
        small = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return bgr, cv2.cvtColor(small, cv2.COLOR_BGR2RGB)


class CameraStream:
    def __init__(self, cfg: CaptureConfig) -> None:
        self.cfg = cfg
        self._cap: cv2.VideoCapture | None = None
        self._frames: deque[Frame] = deque(maxlen=2)
        self._cond = threading.Condition()
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> CameraStream:
        self._open()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()
        return self

    def read(self, timeout: float = 1.0) -> Frame | None:
        """Return the next unprocessed frame, waiting for one. None means a timeout."""
        with self._cond:
            self._cond.wait_for(lambda: self._frames, timeout=timeout)
            return self._frames.popleft() if self._frames else None

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        if self._cap is not None:
            self._cap.release()

    def _backend(self) -> int:
        name = self.cfg.backend.lower()
        if name == "auto":
            return cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        return {"dshow": cv2.CAP_DSHOW, "msmf": cv2.CAP_MSMF, "any": cv2.CAP_ANY}[name]

    def _open(self) -> None:
        cap = cv2.VideoCapture(self.cfg.camera_index, self._backend())
        if not cap.isOpened():
            raise RuntimeError(
                f"Could not open camera {self.cfg.camera_index}. Check that no other app "
                "is using it and that camera access is allowed in your privacy settings."
            )
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        cap.set(cv2.CAP_PROP_FPS, self.cfg.target_fps)
        # Keep the driver's queue short so that frames are as fresh as possible.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        for _ in range(self.cfg.warmup_frames):
            cap.read()
        self._cap = cap

    def _loop(self) -> None:
        min_interval = 1.0 / self.cfg.max_fps if self.cfg.max_fps > 0 else 0.0
        failures = 0
        last_kept = 0.0
        while self._running:
            ok, raw = self._cap.read()
            now = time.monotonic()
            if not ok:
                failures += 1
                if failures >= _REOPEN_AFTER_FAILURES:
                    failures = self._try_reopen()
                continue
            failures = 0
            if now - last_kept < min_interval:
                continue
            last_kept = now
            bgr, rgb = preprocess(raw, self.cfg.mirror, self.cfg.processing_scale)
            with self._cond:
                self._frames.append(Frame(bgr, rgb, now))
                self._cond.notify_all()

    def _try_reopen(self) -> int:
        log.warning("Camera stopped delivering frames; reopening it.")
        self._cap.release()
        time.sleep(_REOPEN_DELAY_S)
        try:
            self._open()
        except RuntimeError as exc:
            log.warning("%s", exc)
            # Reuse the existing handle: read() keeps failing, so we retry soon.
            self._cap = cv2.VideoCapture()
        return 0
