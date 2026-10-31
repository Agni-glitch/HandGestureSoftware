from config import StabilityConfig, SwipeConfig
from gesture_control.recognizer import Gesture
from gesture_control.stability import SWIPE_LEFT, SWIPE_RIGHT, GestureStabilizer, SwipeDetector

DT = 1 / 30


def make_stabilizer() -> GestureStabilizer:
    return GestureStabilizer(StabilityConfig(hold_frames={"FIST": 4}, release_frames=3))


def test_gesture_needs_consecutive_frames():
    s = make_stabilizer()
    assert [s.update(Gesture.FIST) for _ in range(4)] == [False, False, False, True]
    assert s.stable is Gesture.FIST


def test_flicker_never_confirms():
    s = make_stabilizer()
    for _ in range(20):
        s.update(Gesture.FIST)
        s.update(Gesture.FIST)
        s.update(Gesture.NONE)
    assert s.stable is Gesture.NONE


def test_brief_dropout_does_not_release_a_stable_gesture():
    s = make_stabilizer()
    for _ in range(4):
        s.update(Gesture.FIST)
    s.update(Gesture.NONE)
    s.update(Gesture.NONE)
    assert s.stable is Gesture.FIST
    s.update(Gesture.NONE)
    assert s.stable is Gesture.NONE


def test_progress_reports_the_hold():
    s = make_stabilizer()
    assert s.progress == 0
    s.update(Gesture.FIST)
    s.update(Gesture.FIST)
    assert s.progress == 0.5


def stroke(det, start_t, x0, x1, frames=8, y0=0.5, y1=0.5, is_open=True):
    """Feed a straight movement. Returns any swipes fired and the end time."""
    fired = []
    t = start_t
    for i in range(frames):
        frac = i / (frames - 1)
        result = det.update(t, x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac, is_open)
        if result:
            fired.append(result)
        t += DT
    return fired, t


def armed_detector(x: float = 0.3) -> tuple[SwipeDetector, float]:
    """A detector that has already watched a still hand long enough to be armed."""
    det = SwipeDetector(SwipeConfig())
    _, t = stroke(det, 0.0, x, x, frames=10)
    return det, t


def test_fast_swipes_fire_in_the_right_direction():
    det, t = armed_detector()
    assert stroke(det, t, 0.3, 0.7)[0] == [SWIPE_RIGHT]
    det, t = armed_detector(0.7)
    assert stroke(det, t, 0.7, 0.3)[0] == [SWIPE_LEFT]


def test_one_stroke_fires_only_once():
    det, t = armed_detector()
    assert len(stroke(det, t, 0.1, 0.9, frames=9)[0]) == 1


def test_slow_movement_does_not_fire():
    det, t = armed_detector()
    assert stroke(det, t, 0.2, 0.8, frames=90)[0] == []


def test_mostly_vertical_movement_does_not_fire():
    det, t = armed_detector()
    assert stroke(det, t, 0.3, 0.6, y0=0.1, y1=0.9)[0] == []


def test_closed_hand_does_not_fire():
    det, t = armed_detector()
    assert stroke(det, t, 0.3, 0.7, is_open=False)[0] == []


def test_hand_that_just_appeared_does_not_fire():
    det = SwipeDetector(SwipeConfig())
    assert stroke(det, 0.0, 0.1, 0.5, frames=6)[0] == []


def test_return_stroke_is_ignored():
    det, t = armed_detector()
    fired, t = stroke(det, t, 0.2, 0.7)
    assert fired == [SWIPE_RIGHT]
    assert stroke(det, t, 0.7, 0.2)[0] == []


def test_opposite_swipe_works_after_the_block_expires():
    det, t = armed_detector()
    _, t = stroke(det, t, 0.2, 0.7)
    _, t = stroke(det, t, 0.7, 0.7, frames=45)
    assert stroke(det, t, 0.7, 0.2)[0] == [SWIPE_LEFT]


def test_return_block_survives_a_tracking_dropout():
    # Fast hands often lose tracking mid-return, which must not reopen the door.
    det, t = armed_detector()
    _, t = stroke(det, t, 0.2, 0.7)
    det.reset()
    _, t = stroke(det, t, 0.7, 0.7, frames=7)
    assert stroke(det, t, 0.7, 0.2)[0] == []
