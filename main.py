"""Command-line entry point. The desktop app (app.pyw) uses the same engine."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

import cv2

from config import AppConfig, load_config
from gesture_control import __version__
from gesture_control.calibrate import run_calibration
from gesture_control.engine import EngineStats, GestureEngine, start_pause_hotkey
from gesture_control.os_control import DryRunController, SystemController, enable_dpi_awareness
from gesture_control.profile import Profile, default_bindings

log = logging.getLogger("gesture_control")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Control your computer with hand gestures.")
    parser.add_argument("--version", action="version", version=f"Hand Gesture Control {__version__}")
    parser.add_argument("--dry-run", action="store_true", help="log actions instead of doing them")
    parser.add_argument("--calibrate", action="store_true", help="measure your pinch range and exit")
    parser.add_argument("--debug", action="store_true", help="show finger angles on the HUD")
    parser.add_argument("--no-preview", action="store_true", help="run without a window")
    parser.add_argument("--camera", type=int, help="camera index (default: 0)")
    parser.add_argument("--scale", type=float, help="scale of the frame given to the detector")
    parser.add_argument("--every-n", type=int, help="run the detector on every Nth frame")
    parser.add_argument("--max-fps", type=int, help="limit the camera frame rate")
    parser.add_argument("--fist-action", choices=["play_pause", "mute"])
    parser.add_argument("--swipe-action", choices=["media", "tabs"])
    parser.add_argument("--seconds", type=float, help="stop automatically after this many seconds")
    return parser.parse_args(argv)


def apply_args(cfg: AppConfig, args: argparse.Namespace) -> None:
    if args.camera is not None:
        cfg.capture.camera_index = args.camera
    if args.scale is not None:
        cfg.capture.processing_scale = args.scale
    if args.max_fps is not None:
        cfg.capture.max_fps = args.max_fps
    if args.every_n is not None:
        cfg.detection.detect_every_n = max(args.every_n, 1)
    if args.fist_action is not None:
        cfg.actions.fist_action = args.fist_action
    if args.swipe_action is not None:
        cfg.actions.swipe_action = args.swipe_action


def run(cfg: AppConfig, args: argparse.Namespace) -> int:
    os_ctl = DryRunController() if args.dry_run else SystemController()
    profile = Profile(bindings=default_bindings(cfg.actions))
    profile.general.swipe_distance = cfg.swipe.min_distance
    engine = GestureEngine(cfg, os_ctl, profile, debug=args.debug)

    hotkey = None
    if cfg.actions.pause_hotkey and not args.dry_run:
        hotkey = start_pause_hotkey(cfg.actions.pause_hotkey, engine.toggle_pause)

    stop = threading.Event()
    window = cfg.ui.window_name
    preview = not args.no_preview
    started: float | None = None
    window_opened = False

    def on_frame(image, _stats: EngineStats) -> None:
        nonlocal window_opened, started
        # Counted from the first frame: opening the camera can take several seconds.
        started = started if started is not None else time.monotonic()
        if args.seconds is not None and time.monotonic() - started >= args.seconds:
            stop.set()
        if not preview:
            return
        if not window_opened:
            cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
            if cfg.ui.always_on_top:
                cv2.setWindowProperty(window, cv2.WND_PROP_TOPMOST, 1)
            window_opened = True
        cv2.imshow(window, image)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27) or cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            stop.set()
        elif key == ord("p"):
            engine.toggle_pause.set()

    log.info("Running. Press Q or Esc in the window to quit, P to pause.")
    if hotkey is not None:
        log.info("%s pauses and resumes from any window.", cfg.actions.pause_hotkey)
    engine.preview_enabled = preview
    try:
        summary = engine.run(stop, on_frame)
    except KeyboardInterrupt:
        return 0
    finally:
        if hotkey is not None:
            hotkey.stop()
        if window_opened:
            cv2.destroyAllWindows()
    print(
        f"Frames: {summary.frames}, with a hand: {summary.hand_frames}. Recent FPS: {summary.fps:.1f}. "
        f"Detector: median {summary.inference_p50_ms:.1f} ms, "
        f"95th percentile {summary.inference_p95_ms:.1f} ms."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args(argv)
    enable_dpi_awareness()
    cfg = load_config()
    apply_args(cfg, args)

    try:
        if args.calibrate:
            return run_calibration(cfg)
        return run(cfg, args)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
