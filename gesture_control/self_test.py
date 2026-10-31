"""Checks a built .exe, which has no console to show errors on. scripts/build_exe.py runs this.

Most of the app's libraries are only imported once you press Start, so a bundle missing one of
them would still open normally. This loads them all without using the camera or pressing anything.
"""

from __future__ import annotations

import traceback
from pathlib import Path


def _check() -> None:
    import comtypes  # noqa: F401
    import cv2
    import numpy as np
    import pycaw.pycaw  # noqa: F401

    from config import load_config
    from gesture_control.detection import HandDetector
    from gesture_control.engine import GestureEngine, start_pause_hotkey  # noqa: F401
    from gesture_control.os_control import SystemController

    cfg = load_config()
    detector = HandDetector(cfg.detection, cfg.filters)
    try:
        detector.detect(np.zeros((cfg.capture.height, cfg.capture.width, 3), dtype=np.uint8), 0.0)
    finally:
        detector.close()
    if not cv2.videoio_registry.hasBackend(cv2.CAP_DSHOW):
        raise RuntimeError("OpenCV was bundled without DirectShow, so the camera can't open.")
    # Copes with having no audio device, as on a build server, so a release can be built on one.
    SystemController()


def run_self_test(report_path: str) -> int:
    try:
        _check()
    except Exception:
        Path(report_path).write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    Path(report_path).write_text("OK\n", encoding="utf-8")
    return 0
