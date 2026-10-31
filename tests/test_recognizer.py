"""Uses landmarks the real MediaPipe model produced from photos of each pose.

Keys in the fixture file are "<expected label>|<photo>|<raw or mirrored>|<hand>|<field>".
"""

from pathlib import Path

import numpy as np
import pytest

from config import RecognizerConfig
from gesture_control.recognizer import (
    INDEX_TIP,
    THUMB_TIP,
    Finger,
    Gesture,
    GestureRecognizer,
    classify,
    joint_angle,
)

FIXTURES = np.load(Path(__file__).parent / "fixtures" / "real_hand_landmarks.npz")
CASES = sorted({k.rsplit("|", 1)[0] for k in FIXTURES.files})


def load(case: str) -> tuple[np.ndarray, np.ndarray, float]:
    return (
        FIXTURES[f"{case}|world"].astype(float),
        FIXTURES[f"{case}|image"].astype(float),
        float(FIXTURES[f"{case}|aspect"]),
    )


def first_case(label: str) -> str:
    return next(c for c in CASES if c.startswith(label + "|"))


def with_thumb_on_index(world: np.ndarray) -> np.ndarray:
    pinched = world.copy()
    pinched[THUMB_TIP] = pinched[INDEX_TIP]
    return pinched


@pytest.mark.parametrize("case", CASES)
def test_real_hand_photos(case):
    expected = case.split("|")[0]
    world, image, aspect = load(case)
    assert GestureRecognizer(RecognizerConfig()).recognize(world, image, aspect).gesture.value == expected


def test_thumb_on_index_with_other_fingers_up_is_a_pinch():
    world, image, aspect = load(first_case("OPEN_PALM"))
    reading = GestureRecognizer(RecognizerConfig()).recognize(
        with_thumb_on_index(world), image, aspect
    )
    assert reading.gesture is Gesture.PINCH
    assert reading.pinch_active


def test_thumb_on_index_with_other_fingers_down_stays_pointer():
    world, image, aspect = load(first_case("POINTER"))
    reading = GestureRecognizer(RecognizerConfig()).recognize(
        with_thumb_on_index(world), image, aspect
    )
    assert reading.gesture is Gesture.POINTER
    assert reading.pinch_active


def test_pinch_hysteresis_holds_between_the_two_thresholds():
    cfg = RecognizerConfig(pinch_on_ratio=0.3, pinch_off_ratio=0.5)
    recognizer = GestureRecognizer(cfg)
    world, image, aspect = load(first_case("OPEN_PALM"))
    palm = np.linalg.norm(world[0] - world[9])

    def at_ratio(ratio):
        moved = world.copy()
        moved[THUMB_TIP] = moved[INDEX_TIP] + np.array([ratio * palm, 0, 0])
        return recognizer.recognize(moved, image, aspect).pinch_active

    assert not at_ratio(0.4)  # In the gap, but it was never on.
    assert at_ratio(0.2)
    assert at_ratio(0.4)  # Same distance, now on: this is the hysteresis.
    assert not at_ratio(0.6)


def test_wrapped_thumb_touching_the_index_is_still_a_fist():
    down = dict.fromkeys(("index", "middle", "ring", "pinky"), Finger.CURLED)
    assert classify(down, pinch_active=True, thumb_tucked=True) is Gesture.FIST


def test_thumb_held_out_is_not_a_fist():
    down = dict.fromkeys(("index", "middle", "ring", "pinky"), Finger.CURLED)
    assert classify(down, pinch_active=False, thumb_tucked=False) is Gesture.NONE


def test_uncertain_fingers_give_no_gesture():
    unsure = dict.fromkeys(("index", "middle", "ring", "pinky"), Finger.UNCERTAIN)
    assert classify(unsure, pinch_active=False, thumb_tucked=True) is Gesture.NONE


def test_joint_angle():
    a, b, c = np.array([1.0, 0, 0]), np.zeros(3), np.array([-1.0, 0, 0])
    assert joint_angle(a, b, c) == pytest.approx(180)
    assert joint_angle(a, b, np.array([0, 1.0, 0])) == pytest.approx(90)
    assert joint_angle(b, b, c) == 180  # Degenerate input must not raise.
