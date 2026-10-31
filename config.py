"""All tunable settings. Edit the defaults here; `--calibrate` overrides the pinch values."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# In the built .exe, __file__ is inside the bundle. Models and settings sit beside the .exe.
ROOT = Path(sys.executable if getattr(sys, "frozen", False) else __file__).resolve().parent
CALIBRATION_PATH = ROOT / "calibration.json"


@dataclass
class CaptureConfig:
    camera_index: int = 0
    width: int = 640
    height: int = 480
    target_fps: int = 30
    # Drop camera frames above this rate to save CPU. 0 means no limit.
    max_fps: int = 0
    mirror: bool = True
    # Scale applied to the frame given to MediaPipe (the display stays full size).
    processing_scale: float = 1.0
    # "auto" picks DirectShow on Windows, which opens faster than Media Foundation.
    backend: str = "auto"
    # Frames discarded after opening: the first ones can be black during auto-exposure.
    warmup_frames: int = 5


@dataclass
class DetectionConfig:
    model_path: Path = ROOT / "models" / "hand_landmarker.task"
    num_hands: int = 1
    min_detection_confidence: float = 0.6
    min_presence_confidence: float = 0.5
    min_tracking_confidence: float = 0.5
    # Run the detector on every Nth frame. 2 halves the CPU load but adds latency.
    detect_every_n: int = 1


@dataclass
class FilterConfig:
    """One-Euro parameters: lower min_cutoff removes jitter, higher beta removes lag."""

    landmark_min_cutoff: float = 2.0
    landmark_beta: float = 4.0
    cursor_min_cutoff: float = 1.2
    cursor_beta: float = 8.0
    d_cutoff: float = 1.0


@dataclass
class RecognizerConfig:
    # A finger is extended when all three joint angles reach these values (degrees).
    extended_pip_deg: float = 140.0
    extended_dip_deg: float = 125.0
    extended_mcp_deg: float = 125.0
    # A finger is curled when its middle joint is at or below this angle.
    curled_pip_deg: float = 115.0
    # The same test in the camera image, which overrides the above when decisive. A finger
    # is extended if it looks straight and at least this long (in palm lengths)...
    image_straight_deg: float = 155.0
    image_min_finger_length: float = 0.5
    # ...and curled if its middle joint looks bent at least this sharply.
    image_bent_deg: float = 100.0
    # A thumb tip further than this from the index knuckle is "out" (thumbs-up, not fist).
    # Measured in palm lengths, like the pinch ratio.
    fist_thumb_max_ratio: float = 0.55
    # Thumb-to-index distance in palm lengths. On < off gives hysteresis.
    pinch_on_ratio: float = 0.28
    pinch_off_ratio: float = 0.42


@dataclass
class StabilityConfig:
    # Consecutive frames a pose must be seen before it counts.
    hold_frames: dict[str, int] = field(
        default_factory=lambda: {
            "FIST": 6,
            "PEACE": 6,
            "POINTER": 8,
            "PINCH": 8,
            "OPEN_PALM": 6,
        }
    )
    # Consecutive unrecognised frames before the current pose is dropped.
    release_frames: int = 5
    # Minimum time between two firings of the same action.
    action_cooldown_s: float = 1.0
    # After leaving volume or pointer mode, the hand is ignored for this long so the
    # pose used to leave a mode does not also fire its own action.
    mode_exit_cooldown_s: float = 0.8
    # Tracking can drop out briefly; modes survive gaps shorter than this.
    hand_lost_grace_s: float = 0.4


@dataclass
class SwipeConfig:
    window_s: float = 0.35
    # Horizontal travel required within the window, as a fraction of frame width.
    min_distance: float = 0.22
    # Maximum vertical travel, as a fraction of the horizontal travel.
    max_vertical_ratio: float = 0.6
    # Share of the window's frames in which the hand must read as an open palm.
    min_open_fraction: float = 0.6
    min_samples: int = 4
    # Ignore a hand that has only just appeared.
    arm_after_s: float = 0.2
    # Swiping one way and bringing the hand back should not fire the opposite swipe.
    return_block_s: float = 1.2


@dataclass
class VolumeConfig:
    # Pinch ratios that map to 0% and 100% volume.
    min_ratio: float = 0.30
    max_ratio: float = 1.20
    # The volume stays put until the pinch reaches the current level, so engaging
    # never makes it jump. This is how close counts as reaching it (0-1 scale).
    pickup_tolerance: float = 0.04
    smoothing: float = 0.35
    min_step: float = 0.01
    # On exit, restore the level from this long ago, to undo the hand's exit movement.
    exit_rollback_s: float = 0.4


@dataclass
class PointerConfig:
    # Part of the camera frame mapped to the full screen (x0, y0, x1, y1). Smaller than
    # the frame so the screen edges can be reached without the hand leaving the view.
    active_region: tuple[float, float, float, float] = (0.2, 0.15, 0.8, 0.7)
    # Ignore cursor moves smaller than this many pixels.
    deadzone_px: float = 1.5
    # Frames the pinch must persist before it presses the mouse button.
    click_hold_frames: int = 2
    # Pinching moves the fingertip, so the cursor freezes as the fingers close...
    freeze_ratio_margin: float = 1.15
    # ...and stays frozen until it has moved this far, which then counts as a drag...
    drag_threshold_px: float = 40.0
    # ...and for this long after release.
    post_click_freeze_s: float = 0.15


@dataclass
class ActionConfig:
    # "play_pause" or "mute".
    fist_action: str = "play_pause"
    # "media" (next/previous track) or "tabs" (next/previous browser tab).
    swipe_action: str = "media"
    # Tilt the peace sign further left than this to go to the previous desktop.
    desktop_prev_tilt_deg: float = 15.0
    # Global on/off switch that works while another window has focus. None disables it.
    pause_hotkey: str | None = "<ctrl>+<alt>+g"


@dataclass
class UIConfig:
    window_name: str = "Hand Gesture Control"
    always_on_top: bool = True
    draw_landmarks: bool = True
    action_flash_s: float = 1.2


@dataclass
class AppConfig:
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    filters: FilterConfig = field(default_factory=FilterConfig)
    recognizer: RecognizerConfig = field(default_factory=RecognizerConfig)
    stability: StabilityConfig = field(default_factory=StabilityConfig)
    swipe: SwipeConfig = field(default_factory=SwipeConfig)
    volume: VolumeConfig = field(default_factory=VolumeConfig)
    pointer: PointerConfig = field(default_factory=PointerConfig)
    actions: ActionConfig = field(default_factory=ActionConfig)
    ui: UIConfig = field(default_factory=UIConfig)


def load_config(calibration_path: Path = CALIBRATION_PATH) -> AppConfig:
    """Return the defaults with any saved calibration applied."""
    cfg = AppConfig()
    if not calibration_path.exists():
        return cfg
    try:
        data = json.loads(calibration_path.read_text(encoding="utf-8"))
        # All four or none: a mix of saved values and defaults could put them out of order.
        pinch_on, pinch_off = float(data["pinch_on_ratio"]), float(data["pinch_off_ratio"])
        volume_min, volume_max = float(data["volume_min_ratio"]), float(data["volume_max_ratio"])
    except (ValueError, KeyError, TypeError) as exc:
        log.warning("Ignoring unreadable calibration file %s: %r", calibration_path, exc)
        return cfg
    cfg.recognizer.pinch_on_ratio = pinch_on
    cfg.recognizer.pinch_off_ratio = pinch_off
    cfg.volume.min_ratio = volume_min
    cfg.volume.max_ratio = volume_max
    return cfg
