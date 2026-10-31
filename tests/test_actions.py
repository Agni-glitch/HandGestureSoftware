"""Exercises the mapper as a scripted session, against a fake OS that records everything."""

import pytest

from config import AppConfig
from gesture_control.actions import ActionMapper, Mode
from gesture_control.profile import Profile, default_bindings
from gesture_control.recognizer import Gesture, GestureReading
from gesture_control.router import ProfileRouter

DT = 1 / 30


class FakeOS:
    screen_size = (1000, 500)

    def __init__(self, volume=0.5):
        self.actions = []
        self.log = []
        self.cursor = []
        self.mouse = []
        self.volume = volume
        self.volume_sets = []
        self.closed = False
        self.fail_on_app = False

    def trigger(self, action):
        self.actions.append(action)

    def press_keys(self, keys):
        self.log.append(("keys", keys))

    def type_text(self, text):
        self.log.append(("type", text))

    def open_app(self, path):
        if self.fail_on_app:
            raise OSError("no such app")
        self.log.append(("app", path))

    def open_url(self, url):
        self.log.append(("url", url))

    def move_cursor(self, x, y):
        self.cursor.append((x, y))

    def mouse_down(self):
        self.mouse.append("down")

    def mouse_up(self):
        self.mouse.append("up")

    def double_click(self):
        self.mouse.append("double")

    def get_volume(self):
        return self.volume

    def set_volume(self, level):
        self.volume = level
        self.volume_sets.append(level)

    def refresh_audio_device(self):
        pass

    def close(self):
        self.closed = True


def reading(gesture=Gesture.NONE, pinch=False, ratio=1.0, tilt=0.0, tip=(0.5, 0.4), palm=(0.5, 0.5)):
    return GestureReading(gesture, pinch, ratio, tilt, tip, palm, {}, {})


class AnyWindow:
    """Tests must not depend on whichever window happens to be in front."""

    def current(self):
        return None

    def is_own_window(self):
        return False


class Session:
    def __init__(self, volume=0.5, **actions):
        self.cfg = AppConfig()
        for key, value in actions.items():
            setattr(self.cfg.actions, key, value)
        self.os = FakeOS(volume)
        profile = Profile(bindings=default_bindings(self.cfg.actions))
        self.mapper = ActionMapper(self.cfg, self.os, ProfileRouter(profile, self.os, AnyWindow()))
        self.t = 100.0

    def hold(self, frames=15, **kwargs):
        status = None
        for _ in range(frames):
            status = self.mapper.update(reading(**kwargs), self.t)
            self.t += DT
        return status

    def no_hand(self, seconds=1.0):
        status = None
        for _ in range(int(seconds / DT)):
            status = self.mapper.update(None, self.t)
            self.t += DT
        return status

    def wait_out_cooldowns(self):
        self.hold(frames=int(self.cfg.stability.action_cooldown_s / DT) + 2)


def enter_volume(s):
    s.hold(gesture=Gesture.PINCH, pinch=True, ratio=0.2)
    assert s.mapper.mode is Mode.VOLUME


def enter_pointer(s):
    s.hold(gesture=Gesture.POINTER)
    assert s.mapper.mode is Mode.POINTER


# One-shot gestures


def test_holding_a_fist_fires_exactly_once():
    s = Session()
    s.hold(frames=120, gesture=Gesture.FIST)
    assert s.os.actions == ["play_pause"]


def test_fist_can_be_configured_to_mute():
    s = Session(fist_action="mute")
    s.hold(gesture=Gesture.FIST)
    assert s.os.actions == ["mute"]


def test_a_flickering_fist_does_not_fire():
    s = Session()
    for _ in range(30):
        s.hold(frames=3, gesture=Gesture.FIST)
        s.hold(frames=1)
    assert s.os.actions == []


def test_cooldown_blocks_an_immediate_refire():
    s = Session()
    s.hold(gesture=Gesture.FIST)
    s.hold(frames=6)
    s.hold(gesture=Gesture.FIST)
    assert s.os.actions == ["play_pause"]


def test_fist_fires_again_after_the_cooldown():
    s = Session()
    s.hold(gesture=Gesture.FIST)
    s.wait_out_cooldowns()
    s.hold(gesture=Gesture.FIST)
    assert s.os.actions == ["play_pause", "play_pause"]


@pytest.mark.parametrize(
    "tilt, expected",
    [(0.0, "desktop_next"), (30.0, "desktop_next"), (-30.0, "desktop_previous")],
)
def test_peace_direction_follows_hand_tilt(tilt, expected):
    s = Session()
    s.hold(gesture=Gesture.PEACE, tilt=tilt)
    assert s.os.actions == [expected]


def swipe(s, x0, x1):
    s.hold(gesture=Gesture.OPEN_PALM, palm=(x0, 0.5))
    for i in range(8):
        x = x0 + (x1 - x0) * i / 7
        s.hold(frames=1, gesture=Gesture.OPEN_PALM, palm=(x, 0.5))


def test_swipes_map_to_media_keys_by_default():
    s = Session()
    swipe(s, 0.2, 0.7)
    assert s.os.actions == ["media_next"]


def test_swipes_can_map_to_tabs():
    s = Session(swipe_action="tabs")
    swipe(s, 0.7, 0.2)
    assert s.os.actions == ["tab_previous"]


# Volume mode


def test_engaging_volume_with_a_closed_pinch_does_not_change_the_level():
    """A closed pinch maps to 0%, so following it directly would silence the audio."""
    s = Session(volume=0.5)
    enter_volume(s)
    s.hold(frames=30, gesture=Gesture.PINCH, pinch=True, ratio=0.2)
    assert s.os.volume_sets == []


def test_volume_follows_the_pinch_after_pickup():
    s = Session(volume=0.5)
    enter_volume(s)
    s.hold(gesture=Gesture.OPEN_PALM, ratio=0.75)  # Reaches the 50% level.
    s.hold(frames=40, gesture=Gesture.OPEN_PALM, ratio=1.2)
    assert s.os.volume == pytest.approx(1.0, abs=0.02)
    s.hold(frames=40, gesture=Gesture.OPEN_PALM, ratio=0.3)
    assert s.os.volume == pytest.approx(0.0, abs=0.02)


def test_volume_moves_in_small_steps():
    s = Session(volume=0.5)
    enter_volume(s)
    s.hold(gesture=Gesture.OPEN_PALM, ratio=0.75)
    s.hold(frames=40, gesture=Gesture.OPEN_PALM, ratio=1.2)
    assert all(b - a <= 0.2 for a, b in zip(s.os.volume_sets, s.os.volume_sets[1:]))


def test_pickup_triggers_when_a_fast_move_jumps_over_the_level():
    s = Session(volume=0.5)
    enter_volume(s)
    s.hold(frames=2, gesture=Gesture.OPEN_PALM, ratio=1.2)
    assert s.mapper._volume.engaged


def test_exit_movement_is_rolled_back():
    s = Session(volume=0.5)
    enter_volume(s)
    s.hold(gesture=Gesture.OPEN_PALM, ratio=0.75)
    s.hold(frames=60, gesture=Gesture.OPEN_PALM, ratio=0.93)  # Settle at 70%.
    settled = s.os.volume
    assert settled == pytest.approx(0.7, abs=0.02)

    s.hold(frames=6, gesture=Gesture.OPEN_PALM, ratio=0.3)  # Hand falls away...
    assert s.os.volume < settled - 0.2
    s.no_hand()  # ...and is lost.
    assert s.mapper.mode is Mode.IDLE
    assert s.os.volume == pytest.approx(settled, abs=0.03)


def test_fist_exits_volume_without_also_firing_play_pause():
    s = Session()
    enter_volume(s)
    s.hold(gesture=Gesture.FIST)
    assert s.mapper.mode is Mode.IDLE
    s.hold(frames=60, gesture=Gesture.FIST)
    assert s.os.actions == []


def test_open_hand_during_volume_mode_does_not_swipe():
    s = Session()
    enter_volume(s)
    for i in range(8):
        s.hold(frames=1, gesture=Gesture.OPEN_PALM, palm=(0.2 + i * 0.1, 0.5), ratio=0.3)
    assert s.os.actions == []
    assert s.mapper.mode is Mode.VOLUME


# Pointer mode


def test_cursor_maps_the_active_region_to_the_full_screen():
    s = Session()
    enter_pointer(s)
    x0, y0, x1, y1 = s.cfg.pointer.active_region
    s.hold(frames=60, gesture=Gesture.POINTER, tip=(x1, y1))
    assert s.os.cursor[-1] == pytest.approx((999, 499), abs=2)
    s.hold(frames=60, gesture=Gesture.POINTER, tip=(x0, y0))
    assert s.os.cursor[-1] == pytest.approx((0, 0), abs=2)


def test_fingertip_outside_the_region_stays_on_screen():
    s = Session()
    enter_pointer(s)
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(1.0, 1.0))
    assert s.os.cursor[-1][0] <= 999 and s.os.cursor[-1][1] <= 499


def test_pinch_clicks_in_pointer_mode_and_does_not_touch_volume():
    s = Session()
    enter_pointer(s)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    assert s.os.mouse == ["down"]
    s.hold(frames=4, gesture=Gesture.POINTER)
    assert s.os.mouse == ["down", "up"]
    assert s.os.volume_sets == []


def test_entering_pointer_mode_mid_pinch_does_not_click():
    s = Session()
    s.hold(gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    assert s.mapper.mode is Mode.POINTER
    s.hold(frames=10, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    assert s.os.mouse == []
    s.hold(frames=2, gesture=Gesture.POINTER)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    assert s.os.mouse == ["down"]


def test_small_fingertip_movement_during_a_click_does_not_move_the_cursor():
    s = Session()
    enter_pointer(s)
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.5, 0.4))
    moves_before = len(s.os.cursor)
    s.hold(frames=5, gesture=Gesture.POINTER, pinch=True, ratio=0.1, tip=(0.51, 0.41))
    assert len(s.os.cursor) == moves_before


def test_large_movement_while_pinched_drags():
    s = Session()
    enter_pointer(s)
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.3, 0.4))
    s.hold(frames=5, gesture=Gesture.POINTER, pinch=True, ratio=0.1, tip=(0.3, 0.4))
    before = s.os.cursor[-1]
    s.hold(frames=20, gesture=Gesture.POINTER, pinch=True, ratio=0.1, tip=(0.7, 0.4))
    assert s.os.mouse == ["down"]
    assert s.os.cursor[-1][0] > before[0] + 100


def test_cursor_is_not_held_frozen_when_a_pinch_never_happens():
    s = Session()
    enter_pointer(s)
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.3, 0.4))
    a = s.os.cursor[-1]
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.7, 0.4))
    assert s.os.cursor[-1][0] > a[0]


def test_open_palm_leaves_pointer_mode():
    s = Session()
    enter_pointer(s)
    s.hold(gesture=Gesture.OPEN_PALM)
    assert s.mapper.mode is Mode.IDLE


def test_raising_the_middle_finger_double_clicks_once_and_stays_in_pointer_mode():
    s = Session()
    enter_pointer(s)
    s.hold(gesture=Gesture.PEACE)
    s.hold(frames=30, gesture=Gesture.PEACE)
    assert s.os.mouse == ["double"]
    assert s.mapper.mode is Mode.POINTER
    assert s.os.actions == []  # And not the peace sign's own action.


def test_double_clicking_again_needs_the_finger_lowered_first():
    s = Session()
    enter_pointer(s)
    s.hold(gesture=Gesture.PEACE)
    s.hold(gesture=Gesture.POINTER)
    s.hold(gesture=Gesture.PEACE)
    assert s.os.mouse == ["double", "double"]


def test_a_brief_flicker_of_the_middle_finger_does_not_double_click():
    s = Session()
    enter_pointer(s)
    s.hold(frames=3, gesture=Gesture.PEACE)
    s.hold(frames=5, gesture=Gesture.POINTER)
    assert s.os.mouse == []


def test_cursor_stays_put_from_the_first_frame_of_a_double_click():
    s = Session()
    enter_pointer(s)
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.3, 0.4))
    moves_before = len(s.os.cursor)
    s.hold(frames=8, gesture=Gesture.PEACE, tip=(0.7, 0.4))
    assert len(s.os.cursor) == moves_before
    assert s.os.mouse == ["double"]


def test_no_double_click_in_the_middle_of_a_drag():
    s = Session()
    enter_pointer(s)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    s.hold(gesture=Gesture.PEACE, pinch=True, ratio=0.1)
    assert "double" not in s.os.mouse


def test_peace_sign_outside_pointer_mode_does_not_double_click():
    s = Session()
    s.hold(gesture=Gesture.PEACE)
    assert s.os.mouse == []
    assert s.os.actions == ["desktop_next"]


def test_double_click_can_be_turned_off():
    s = Session()
    s.mapper.router.profile.bindings["double_click"].enabled = False
    enter_pointer(s)
    s.hold(gesture=Gesture.PEACE)
    assert s.os.mouse == []
    assert s.mapper.mode is Mode.POINTER


def test_click_can_be_turned_off_without_stopping_the_cursor():
    s = Session()
    s.mapper.router.profile.bindings["click"].enabled = False
    enter_pointer(s)
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.3, 0.4))
    s.hold(frames=5, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    assert s.os.mouse == []
    a = s.os.cursor[-1]
    s.hold(frames=30, gesture=Gesture.POINTER, tip=(0.7, 0.4))
    assert s.os.cursor[-1][0] > a[0]


def test_pointer_mode_survives_a_brief_tracking_dropout():
    s = Session()
    enter_pointer(s)
    s.no_hand(0.2)
    assert s.mapper.mode is Mode.POINTER


# Things that must never get stuck


def test_losing_the_hand_mid_drag_releases_the_mouse():
    s = Session()
    enter_pointer(s)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    s.no_hand()
    assert s.os.mouse == ["down", "up"]
    assert s.mapper.mode is Mode.IDLE


def test_leaving_pointer_mode_mid_drag_releases_the_mouse():
    s = Session()
    enter_pointer(s)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    s.hold(gesture=Gesture.FIST)
    assert s.os.mouse == ["down", "up"]


def test_closing_mid_drag_releases_the_mouse():
    s = Session()
    enter_pointer(s)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    s.mapper.close(s.t)
    assert s.os.mouse == ["down", "up"]
    assert s.os.closed


def test_pausing_mid_drag_releases_the_mouse():
    s = Session()
    enter_pointer(s)
    s.hold(frames=4, gesture=Gesture.POINTER, pinch=True, ratio=0.1)
    s.mapper.set_paused(True, s.t)
    assert s.os.mouse == ["down", "up"]


def test_nothing_fires_while_paused_and_it_works_after_resuming():
    s = Session()
    s.mapper.set_paused(True, s.t)
    s.hold(gesture=Gesture.FIST)
    s.hold(gesture=Gesture.POINTER, tip=(0.7, 0.4))
    assert s.os.actions == [] and s.os.cursor == []
    assert s.hold(frames=1).mode is Mode.PAUSED

    s.mapper.set_paused(False, s.t)
    s.hold(gesture=Gesture.FIST)
    assert s.os.actions == ["play_pause"]
