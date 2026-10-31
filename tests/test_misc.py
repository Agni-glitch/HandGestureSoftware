"""Calibration, config loading, HUD rendering, preprocessing and the detector wrapper."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from config import ROOT, AppConfig, CaptureConfig, load_config
from gesture_control import __version__, shell_integration
from gesture_control.actions import MapperStatus, Mode
from gesture_control.calibrate import compute_calibration
from gesture_control.capture import CameraStream, Frame, preprocess
from gesture_control.detection import HandData, HandDetector
from gesture_control.os_control import _SHORTCUTS
from gesture_control.overlay import HudStats, Overlay
from gesture_control.recognizer import Finger, Gesture, GestureReading
from gesture_control.steps import SYSTEM_ACTIONS


def test_built_exe_keeps_its_data_beside_the_exe(tmp_path):
    fake_exe = tmp_path / "HandGestureControl.exe"
    code = f"import sys; sys.frozen = True; sys.executable = {str(fake_exe)!r}; import config; print(config.ROOT)"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert Path(out.stdout.strip()) == tmp_path.resolve()


def test_shortcuts_open_the_exe_when_built(monkeypatch, tmp_path):
    fake_exe = tmp_path / "HandGestureControl.exe"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe))
    assert shell_integration.launch_command() == (fake_exe, "")


def test_shortcuts_open_the_script_from_source():
    _, arguments = shell_integration.launch_command()
    assert arguments == f'"{shell_integration.ENTRY_POINT}"'


def test_calibration_orders_thresholds_sensibly():
    values = compute_calibration([0.15, 0.18, 0.2, 0.22], [1.3, 1.4, 1.5])
    assert values["pinch_on_ratio"] < values["pinch_off_ratio"]
    assert values["volume_min_ratio"] < values["volume_max_ratio"]
    assert values["volume_max_ratio"] < 1.4


def test_calibration_rejects_measurements_that_overlap():
    with pytest.raises(ValueError):
        compute_calibration([0.5, 0.5, 0.5], [0.6, 0.6, 0.6])


def test_config_defaults_when_nothing_is_calibrated(tmp_path):
    cfg = load_config(tmp_path / "missing.json")
    assert cfg.recognizer.pinch_on_ratio == AppConfig().recognizer.pinch_on_ratio


def test_config_applies_saved_calibration(tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text(
        json.dumps(compute_calibration([0.15, 0.2], [1.3, 1.5])), encoding="utf-8"
    )
    cfg = load_config(path)
    assert cfg.volume.max_ratio == pytest.approx(1.33)


@pytest.mark.parametrize(
    "text", ["{not json", "[]", '{"pinch_on_ratio": 0.3}', '{"pinch_on_ratio": "a", "pinch_off_ratio": 1, '
             '"volume_min_ratio": 1, "volume_max_ratio": 2}']
)
def test_config_ignores_a_damaged_calibration_file(tmp_path, text):
    path = tmp_path / "calibration.json"
    path.write_text(text, encoding="utf-8")
    cfg = load_config(path)
    assert cfg.recognizer.pinch_on_ratio == AppConfig().recognizer.pinch_on_ratio
    assert cfg.volume.max_ratio == AppConfig().volume.max_ratio


def test_changelog_describes_the_current_version():
    assert f"## [{__version__}]" in (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")


def make_frame(n: int) -> Frame:
    return Frame(np.zeros((1, 1, 3), np.uint8), np.zeros((1, 1, 3), np.uint8), float(n))


def test_camera_queue_keeps_a_burst_in_order():
    stream = CameraStream(CaptureConfig())
    stream._frames.extend([make_frame(1), make_frame(2)])
    assert stream.read(timeout=0).timestamp == 1.0
    assert stream.read(timeout=0).timestamp == 2.0
    assert stream.read(timeout=0) is None


def test_camera_queue_drops_the_oldest_frame_when_the_loop_falls_behind():
    stream = CameraStream(CaptureConfig())
    stream._frames.extend([make_frame(1), make_frame(2), make_frame(3)])
    assert stream.read(timeout=0).timestamp == 2.0


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_all_shortcuts_are_valid_and_cover_every_action(platform):
    pytest.importorskip("pynput.keyboard")
    from pynput.keyboard import Key

    table = _SHORTCUTS[platform]
    assert set(table) == set(SYSTEM_ACTIONS)
    for chord in table.values():
        for key in chord.split("+"):
            assert len(key) == 1 or hasattr(Key, key), key


def test_preprocess_mirrors_converts_and_scales():
    raw = np.zeros((100, 200, 3), np.uint8)
    raw[:, 0] = (255, 0, 0)  # Blue in BGR, in the leftmost column.
    bgr, rgb = preprocess(raw, mirror=True, scale=0.5)
    assert bgr.shape == (100, 200, 3)
    assert tuple(bgr[0, -1]) == (255, 0, 0)
    assert rgb.shape == (50, 100, 3)


def test_preprocess_leaves_full_size_input_alone_when_not_mirroring():
    raw = np.zeros((10, 10, 3), np.uint8)
    bgr, rgb = preprocess(raw, mirror=False, scale=1.0)
    assert bgr is raw and rgb.shape == raw.shape


def make_hand() -> HandData:
    rng = np.random.default_rng(1)
    points = rng.random((21, 3))
    return HandData(points, points, points, 4 / 3, "Right", 0.9)


READING = GestureReading(
    Gesture.PINCH,
    True,
    0.2,
    5.0,
    (0.5, 0.5),
    (0.5, 0.5),
    dict.fromkeys(("index", "middle", "ring", "pinky"), Finger.EXTENDED),
    dict.fromkeys(("index", "middle", "ring", "pinky"), 170.0),
)


@pytest.mark.parametrize(
    "status",
    [
        MapperStatus(Mode.IDLE, Gesture.NONE, 0.0),
        MapperStatus(Mode.IDLE, Gesture.FIST, 1.0, flash="PLAY/PAUSE", flash_at=0.0),
        MapperStatus(Mode.PAUSED, Gesture.NONE, 0.0),
        MapperStatus(Mode.POINTER, Gesture.POINTER, 1.0, mouse_down=True),
        MapperStatus(Mode.VOLUME, Gesture.PINCH, 1.0, volume=0.5, volume_target=0.1),
        MapperStatus(Mode.VOLUME, Gesture.PINCH, 1.0, volume=1.0, volume_target=1.0, volume_engaged=True),
    ],
)
@pytest.mark.parametrize("shape", [(480, 640, 3), (240, 320, 3)])
def test_every_hud_state_renders(status, shape):
    cfg = AppConfig()
    img = np.full(shape, 90, np.uint8)
    Overlay(cfg.ui, cfg.pointer, debug=True).draw(
        img, make_hand(), READING, status, HudStats(30.0, 11.0), now=0.1
    )
    assert img.std() > 0  # Something was drawn.


def test_detector_runs_on_an_empty_frame_with_repeated_timestamps():
    cfg = AppConfig()
    if not cfg.detection.model_path.exists():
        pytest.skip("model not downloaded")
    detector = HandDetector(cfg.detection, cfg.filters)
    frame = np.full((240, 320, 3), 128, np.uint8)
    try:
        # MediaPipe rejects non-increasing timestamps; the wrapper must prevent that.
        assert detector.detect(frame, 1.0) is None
        assert detector.detect(frame, 1.0) is None
        assert detector.detect(frame, 0.5) is None
    finally:
        detector.close()


def test_missing_model_gives_an_actionable_error(tmp_path):
    cfg = AppConfig()
    cfg.detection.model_path = tmp_path / "nope.task"
    with pytest.raises(FileNotFoundError, match="download_model"):
        HandDetector(cfg.detection, cfg.filters)
