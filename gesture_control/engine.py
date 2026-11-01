"""The processing loop, shared by the desktop app and the command line."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from config import AppConfig
from gesture_control.actions import ActionMapper, MapperStatus, Mode, screen_fraction
from gesture_control.capture import CameraStream
from gesture_control.custom_gestures import CustomMatcher, pose_features, ready_pose_of
from gesture_control.detection import HandData, HandDetector
from gesture_control.overlay import HudStats, Overlay
from gesture_control.profile import BUILTINS, POSE_BUILTINS, Profile
from gesture_control.recognizer import Gesture, GestureReading, GestureRecognizer
from gesture_control.router import ProfileRouter

log = logging.getLogger(__name__)


@dataclass
class EngineStats:
    fps: float
    inference_ms: float
    tracking: bool
    status: MapperStatus
    # The cursor, or where your fingertip would put it, as a fraction of the screen.
    screen_position: tuple[float, float] | None = None


@dataclass
class RunSummary:
    frames: int
    hand_frames: int
    fps: float
    inference_p50_ms: float
    inference_p95_ms: float


class Sampler:
    """Collects pose data for recording custom gestures and calibrating."""

    def __init__(self) -> None:
        self.features: list[np.ndarray] = []
        self.labels: list[Gesture] = []
        self.pinch_ratios: list[float] = []
        self.landmarks: list[np.ndarray] = []  # For drawing your recorded hand.
        self.ready_poses: list[str | None] = []


class GestureEngine:
    def __init__(self, cfg: AppConfig, os_ctl, profile: Profile, debug: bool = False) -> None:
        self.cfg = cfg
        self.os = os_ctl
        self.profile = profile
        self.router = ProfileRouter(profile, os_ctl)
        self.mapper = ActionMapper(cfg, os_ctl, self.router)
        self.recognizer = GestureRecognizer(cfg.recognizer)
        self.overlay = Overlay(cfg.ui, cfg.pointer, debug=debug)
        self.matcher = CustomMatcher([])
        self.preview_enabled = True
        self.compact_preview = False
        self.toggle_pause = threading.Event()
        self.sampler: Sampler | None = None
        self._pending_profile: Profile | None = None
        self._paused_for_sampling = False
        self._paused_before_sampling = False
        self._apply_profile(profile)

    def set_profile(self, profile: Profile) -> None:
        """Safe from any thread: the swap happens between frames. Pass a private copy."""
        self._pending_profile = profile

    def _apply_profile(self, profile: Profile) -> None:
        self.profile = profile
        self.router.profile = profile
        self.cfg.stability.hold_frames = profile.hold_frames()
        self.cfg.swipe.min_distance = profile.general.swipe_distance
        active = [g for g in profile.custom if g.enabled]
        self.ready_gestures = {g.pose_id: g.id for g in active if g.pose_id}
        self.builtin_poses = {pose: key for key, pose in POSE_BUILTINS.items() if profile.bindings[key].enabled}
        self.matcher = CustomMatcher([(g.id, g.template, g.threshold) for g in active if not g.pose_id])
        self.overlay.custom_names = {key: BUILTINS[key].name for key in POSE_BUILTINS}
        self.overlay.custom_names.update({g.id: g.name for g in profile.custom})

    def _handle_sampling(self, t: float) -> None:
        # While sampling, nothing fires: a recorded pose must not trigger actions.
        sampling = self.sampler is not None
        if sampling != self._paused_for_sampling:
            self._paused_for_sampling = sampling
            if sampling:
                self._paused_before_sampling = self.mapper.paused
                self.mapper.set_paused(True, t)
            else:
                self.mapper.set_paused(self._paused_before_sampling, t)

    def _recognize(self, hand: HandData) -> GestureReading:
        reading = self.recognizer.recognize(hand.world, hand.raw_image, hand.aspect)
        sampler = self.sampler
        in_idle = self.mapper.mode is Mode.IDLE
        if sampler is None and not in_idle:
            return reading
        label_free = reading.gesture is Gesture.NONE or not self.profile.builtin_enabled(reading.gesture.value)
        if sampler is not None or label_free:
            features = pose_features(reading, hand.world, hand.raw_image, hand.aspect)
            ready = ready_pose_of(
                reading, hand.world, hand.raw_image, hand.aspect, self.cfg.recognizer.fist_thumb_max_ratio
            )
            if sampler is not None:
                sampler.features.append(features)
                sampler.labels.append(reading.gesture)
                sampler.pinch_ratios.append(reading.pinch_ratio)
                sampler.landmarks.append(hand.raw_image[:, :2] * (hand.aspect, 1.0))
                sampler.ready_poses.append(ready)
            elif label_free:
                # Your own gestures come first, in case one recorded earlier looks like a built-in pose.
                reading.custom = (
                    self.ready_gestures.get(ready) or self.matcher.match(features) or self.builtin_poses.get(ready)
                )
        return reading

    def _screen_position(self, reading: GestureReading | None, status: MapperStatus) -> tuple[float, float] | None:
        # The real cursor comes first: it stays put during a click, even though the fingertip moves.
        if status.cursor is not None:
            return status.cursor
        if reading is None:
            return None
        x, y = screen_fraction(self.cfg.pointer.active_region, reading.index_tip)
        return float(x), float(y)

    def run(
        self,
        stop: threading.Event,
        on_frame: Callable[[np.ndarray, EngineStats], None] | None = None,
        on_started: Callable[[], None] | None = None,
    ) -> RunSummary:
        """Runs until stop is set. on_frame gets the annotated picture, or None without preview."""
        detector = HandDetector(self.cfg.detection, self.cfg.filters)
        camera: CameraStream | None = None
        frame_times: deque[float] = deque(maxlen=60)
        inference_ms: deque[float] = deque(maxlen=600)
        hand: HandData | None = None
        reading: GestureReading | None = None
        status = MapperStatus(Mode.IDLE, Gesture.NONE, 0.0)
        frames = hand_frames = 0
        last_hand_at = float("-inf")
        filters_cleared = True
        fps = 0.0
        try:
            camera = CameraStream(self.cfg.capture).start()
            if on_started is not None:
                on_started()
            while not stop.is_set():
                frame = camera.read(timeout=0.25)
                now = time.monotonic()
                if self._pending_profile is not None:
                    pending, self._pending_profile = self._pending_profile, None
                    self._apply_profile(pending)
                if self.toggle_pause.is_set():
                    self.toggle_pause.clear()
                    self.mapper.set_paused(not self.mapper.paused, now)
                if frame is None:
                    continue
                frames += 1
                frame_times.append(frame.timestamp)
                self._handle_sampling(frame.timestamp)

                # On skipped frames the HUD simply keeps showing the previous result.
                if frames % self.cfg.detection.detect_every_n == 0:
                    t0 = time.perf_counter()
                    hand = detector.detect(frame.rgb, frame.timestamp)
                    inference_ms.append((time.perf_counter() - t0) * 1000.0)
                    if hand is not None:
                        hand_frames += 1
                        last_hand_at = frame.timestamp
                        filters_cleared = False
                        reading = self._recognize(hand)
                    else:
                        reading = None
                        grace = self.cfg.stability.hand_lost_grace_s
                        if not filters_cleared and frame.timestamp - last_hand_at > grace:
                            detector.reset_filters()
                            self.recognizer.reset()
                            filters_cleared = True
                    status = self.mapper.update(reading, frame.timestamp)

                if on_frame is None:
                    continue
                if len(frame_times) > 1:
                    fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
                latest_ms = inference_ms[-1] if inference_ms else 0.0
                image = None
                if self.preview_enabled:
                    if self.compact_preview:
                        self.overlay.draw_compact(frame.bgr, hand, status)
                    else:
                        self.overlay.draw(frame.bgr, hand, reading, status, HudStats(fps, latest_ms), now)
                    image = frame.bgr
                position = self._screen_position(reading, status)
                on_frame(image, EngineStats(fps, latest_ms, hand is not None, status, position))
        finally:
            # Runs on any exit, so a held mouse button is never left pressed.
            self.mapper.close(time.monotonic())
            if camera is not None:
                camera.stop()
            detector.close()

        final_fps = 0.0
        if len(frame_times) > 1:
            final_fps = (len(frame_times) - 1) / (frame_times[-1] - frame_times[0])
        timings = np.array(inference_ms) if inference_ms else np.zeros(1)
        return RunSummary(
            frames,
            hand_frames,
            final_fps,
            float(np.percentile(timings, 50)),
            float(np.percentile(timings, 95)),
        )


def start_pause_hotkey(combo: str, toggle: threading.Event):
    """Listen for the global pause shortcut. Failing to is not fatal: P still works."""
    try:
        from pynput.keyboard import GlobalHotKeys

        listener = GlobalHotKeys({combo: toggle.set})
        listener.start()
        return listener
    except Exception as exc:
        log.warning("Global pause hotkey unavailable: %s", exc)
        return None
