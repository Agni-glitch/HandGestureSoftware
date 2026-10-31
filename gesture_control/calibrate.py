"""Measures your own pinch range and saves it to calibration.json."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from config import CALIBRATION_PATH, AppConfig

_PREPARE_S = 3.0
_MEASURE_S = 3.0
_MIN_SAMPLES = 20
_PHASES = (
    ("touch", "Touch thumb and index fingertips, other fingers up"),
    ("spread", "Spread thumb and index as wide as is comfortable"),
)


def compute_calibration(touch: list[float], spread: list[float]) -> dict[str, float]:
    """Derive thresholds from measured pinch ratios. Raises ValueError if they overlap."""
    touch_high = float(np.percentile(touch, 90))
    spread_typical = float(np.median(spread))
    if spread_typical < touch_high * 2.0:
        raise ValueError(
            f"Touching ({touch_high:.2f}) and spread ({spread_typical:.2f}) measured too "
            "close together. Keep your whole hand in view and try again."
        )
    # The pinch must release before the spread is half done.
    pinch_on = touch_high * 1.2
    pinch_off = min(pinch_on * 1.5, (pinch_on + spread_typical) / 2)
    return {
        "pinch_on_ratio": round(pinch_on, 3),
        "pinch_off_ratio": round(pinch_off, 3),
        "volume_min_ratio": round(pinch_off, 3),
        # Slightly short of the measured spread, so that 100% is easy to reach.
        "volume_max_ratio": round(spread_typical * 0.95, 3),
    }


def run_calibration(cfg: AppConfig, output: Path = CALIBRATION_PATH) -> int:
    # Imported here because the desktop app only needs compute_calibration above, and MediaPipe
    # takes seconds to load, which would delay its window from opening.
    import cv2

    from gesture_control.capture import CameraStream
    from gesture_control.detection import HandDetector
    from gesture_control.overlay import ACCENT, AMBER, panel, text
    from gesture_control.recognizer import GestureRecognizer

    camera = CameraStream(cfg.capture).start()
    detector = HandDetector(cfg.detection, cfg.filters)
    recognizer = GestureRecognizer(cfg.recognizer)
    window = f"{cfg.ui.window_name} - Calibration"
    samples: dict[str, list[float]] = {}
    try:
        for key, instruction in _PHASES:
            samples[key] = []
            started = time.monotonic()
            while True:
                elapsed = time.monotonic() - started
                if elapsed >= _PREPARE_S + _MEASURE_S:
                    break
                frame = camera.read()
                if frame is None:
                    continue
                hand = detector.detect(frame.rgb, frame.timestamp)
                measuring = elapsed >= _PREPARE_S
                if hand is not None and measuring:
                    reading = recognizer.recognize(hand.world, hand.raw_image, hand.aspect)
                    samples[key].append(reading.pinch_ratio)

                img = frame.bgr.copy()
                panel(img, 0, 0, img.shape[1], 64, alpha=0.75)
                text(img, instruction, 12, 24, 0.5)
                if measuring:
                    status = f"Measuring... {_PREPARE_S + _MEASURE_S - elapsed:.1f}s"
                    text(img, status, 12, 50, 0.5, ACCENT)
                else:
                    text(img, f"Get ready... {_PREPARE_S - elapsed:.1f}s", 12, 50, 0.5, AMBER)
                cv2.imshow(window, img)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    print("Calibration cancelled; nothing was saved.")
                    return 1

            if len(samples[key]) < _MIN_SAMPLES:
                print(f"Saw too little of your hand during the '{key}' step. Please try again.")
                return 1
    finally:
        camera.stop()
        detector.close()
        cv2.destroyAllWindows()

    try:
        values = compute_calibration(samples["touch"], samples["spread"])
    except ValueError as exc:
        print(exc)
        return 1
    output.write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
    print(f"Saved to {output}: {values}")
    return 0
