"""Saved settings, step validation, permission enforcement, per-app rules and custom poses."""

import json
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from config import AppConfig, RecognizerConfig
from gesture_control import app_watch
from gesture_control.actions import ActionMapper, Mode
from gesture_control.custom_gestures import (
    BASE_THRESHOLD,
    CustomMatcher,
    RecordingError,
    build_template,
    pose_features,
    ready_pose_of,
)
from gesture_control.detection import HandData
from gesture_control.engine import GestureEngine, Sampler
from gesture_control.profile import (
    DEFAULT_APP,
    DEFAULT_WEBSITE,
    SCOPE_CONNECTED,
    ConnectedApp,
    CustomGesture,
    Profile,
    load_profile,
    new_custom_id,
    save_profile,
)
from gesture_control.recognizer import FINGER_JOINTS, Gesture, GestureRecognizer
from gesture_control.router import ProfileRouter
from gesture_control.steps import (
    APP,
    HOTKEY,
    SYSTEM,
    TEXT,
    URL,
    WAIT,
    Step,
    StepRunner,
    describe_step,
    describe_steps,
    parse_chord,
    required_permissions,
    validate_step,
)
from tests.test_actions import DT, FakeOS, reading

FIXTURES = np.load(Path(__file__).parent / "fixtures" / "real_hand_landmarks.npz")
CASES = sorted({k.rsplit("|", 1)[0] for k in FIXTURES.files})


class FakeWatcher:
    def __init__(self, exe="chrome.exe", own=False):
        self.exe = exe
        self.own = own

    def current(self):
        return (123, self.exe) if self.exe else None

    def is_own_window(self):
        return self.own


def make_mapper(profile=None, exe="chrome.exe"):
    os_ctl = FakeOS()
    profile = profile or Profile()
    cfg = AppConfig()
    cfg.stability.hold_frames = profile.hold_frames()
    watcher = FakeWatcher(exe)
    router = ProfileRouter(profile, os_ctl, watcher)
    return ActionMapper(cfg, os_ctl, router), os_ctl, watcher


def hold(mapper, t, frames=15, **kwargs):
    for _ in range(frames):
        status = mapper.update(reading(**kwargs), t)
        t += DT
    return status, t


def hold_custom(mapper, t, gesture_id, frames=15):
    for _ in range(frames):
        r = reading()
        r.custom = gesture_id
        status = mapper.update(r, t)
        t += DT
    return status, t


# Steps


@pytest.mark.parametrize(
    "text, keys",
    [
        ("ctrl+shift+s", ["ctrl", "shift", "s"]),
        ("Ctrl + Shift + S", ["ctrl", "shift", "s"]),
        ("Win+D", ["cmd", "d"]),
        ("alt+f4", ["alt", "f4"]),
        ("PgDn", ["page_down"]),
    ],
)
def test_shortcuts_parse(text, keys):
    assert parse_chord(text) == keys


@pytest.mark.parametrize("text", ["", "ctrl+", "ctrl+banana", "+", "f99"])
def test_bad_shortcuts_are_rejected(text):
    with pytest.raises(ValueError):
        parse_chord(text)


@pytest.mark.parametrize(
    "step, valid",
    [
        (Step(SYSTEM, "mute"), True),
        (Step(SYSTEM, "self_destruct"), False),
        (Step(URL, "https://example.com"), True),
        (Step(URL, "example.com"), False),
        (Step(URL, "file:///C:/Windows/system32/calc.exe"), False),
        (Step(URL, "javascript:alert(1)"), False),
        (Step(WAIT, "500"), True),
        (Step(WAIT, "0"), False),
        (Step(WAIT, "99999999"), False),
        (Step(WAIT, "soon"), False),
        (Step(APP, ""), False),
        (Step(TEXT, ""), False),
        (Step(TEXT, " "), True),
        (Step("run_command", "format c:"), False),
    ],
)
def test_step_validation(step, valid):
    assert (validate_step(step) is None) is valid


def test_steps_are_described_for_people():
    steps = [Step(HOTKEY, "ctrl+t"), Step(WAIT, "500"), Step(URL, "https://example.com/page")]
    assert describe_steps(steps) == "Ctrl+T  >  Wait 0.5s  >  Open example.com"
    assert describe_steps([]) == "Does nothing"


def test_required_permissions():
    steps = [Step(SYSTEM, "mute"), Step(WAIT, "5"), Step(URL, "https://a.b")]
    assert required_permissions(steps) == {"keyboard", "websites"}


def wait_for_worker(runner):
    if runner._worker is not None:
        runner._worker.join(timeout=3)


def test_runner_runs_a_sequence_in_order():
    os_ctl = FakeOS()
    runner = StepRunner(os_ctl, lambda p: True)
    assert runner.run([Step(HOTKEY, "ctrl+t"), Step(WAIT, "1"), Step(TEXT, "hi"), Step(URL, "https://a.b")]) is None
    wait_for_worker(runner)
    assert os_ctl.log == [("keys", ["ctrl", "t"]), ("type", "hi"), ("url", "https://a.b")]


def test_a_bad_lone_step_is_reported_and_does_not_reach_the_engine():
    # Settings files can be edited by hand, and this runs on the engine thread.
    runner = StepRunner(FakeOS(), lambda p: True)
    assert "failed" in runner.run([Step(HOTKEY, "ctrl+notakey")])


def test_typed_text_stays_out_of_the_activity_feed_and_the_log():
    profile = Profile()
    profile.permissions["typing"] = True
    profile.bindings["fist"].steps = [Step(TEXT, "my-password")]
    mapper, os_ctl, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.FIST)
    wait_for_worker(mapper.router.runner)
    assert os_ctl.log == [("type", "my-password")]
    # What the mapper reports here is exactly what goes to the feed and to app.log.
    assert mapper._flash == "Type text"


def test_runner_refuses_the_whole_sequence_if_one_permission_is_missing():
    os_ctl = FakeOS()
    runner = StepRunner(os_ctl, lambda p: p == "keyboard")
    reason = runner.run([Step(HOTKEY, "ctrl+t"), Step(TEXT, "hi")])
    wait_for_worker(runner)
    assert "typing" in reason
    assert os_ctl.log == []


def test_runner_does_not_start_a_second_sequence_while_one_runs():
    os_ctl = FakeOS()
    runner = StepRunner(os_ctl, lambda p: True)
    runner.run([Step(WAIT, "300"), Step(TEXT, "first")])
    assert "still running" in runner.run([Step(TEXT, "second"), Step(WAIT, "1")])
    runner.cancel()
    wait_for_worker(runner)
    assert ("type", "second") not in os_ctl.log


def test_cancel_ends_a_long_wait_quickly():
    os_ctl = FakeOS()
    runner = StepRunner(os_ctl, lambda p: True)
    runner.run([Step(WAIT, "30000"), Step(TEXT, "never")])
    runner.cancel()
    runner._worker.join(timeout=1)
    assert not runner._worker.is_alive()
    assert os_ctl.log == []


def test_a_failing_step_stops_the_sequence_without_crashing():
    os_ctl = FakeOS()
    os_ctl.fail_on_app = True
    runner = StepRunner(os_ctl, lambda p: True)
    runner.run([Step(APP, "missing.exe"), Step(TEXT, "after")])
    wait_for_worker(runner)
    assert os_ctl.log == []


# Profile persistence


def full_profile() -> Profile:
    profile = Profile()
    profile.permissions["typing"] = True
    profile.bindings["fist"].steps = [Step(HOTKEY, "ctrl+t"), Step(WAIT, "100")]
    profile.bindings["peace"].enabled = False
    profile.custom.append(
        CustomGesture(new_custom_id(), "Rock", [0.1, 0.2], 0.9, steps=[Step(TEXT, "hi")])
    )
    profile.apps.append(ConnectedApp("Chrome", "chrome.exe", overrides={"fist": [Step(SYSTEM, "mute")]}))
    profile.scope = SCOPE_CONNECTED
    profile.general.camera_index = 2
    return profile


def test_profile_survives_a_save_and_load(tmp_path):
    path = tmp_path / "s.json"
    original = full_profile()
    save_profile(original, path)
    loaded, warning = load_profile(path)
    assert warning is None
    assert loaded == original


def test_missing_settings_give_defaults(tmp_path):
    loaded, warning = load_profile(tmp_path / "none.json")
    assert warning is None
    assert loaded == Profile()


def test_only_what_the_default_gestures_need_is_on_by_default():
    p = Profile().permissions
    assert not p["typing"] and not p["startup"]
    assert p["camera"] and p["keyboard"] and p["mouse"] and p["open_apps"] and p["websites"]


@pytest.mark.parametrize("content", ["{not json", '{"custom": [{"id": 1}]}', '{"bindings": {"fist": 7}}'])
def test_corrupt_settings_are_kept_aside_not_destroyed(tmp_path, content):
    path = tmp_path / "s.json"
    path.write_text(content, encoding="utf-8")
    loaded, warning = load_profile(path)
    assert loaded == Profile()
    assert "could not be read" in warning
    assert (tmp_path / "s.broken.json").read_text(encoding="utf-8") == content


def test_steps_using_lists_what_a_permission_would_break():
    profile = Profile()
    assert "Fist" in profile.steps_using("keyboard")
    assert profile.steps_using("volume") == ["Volume pinch"]
    assert profile.steps_using("typing") == []


# Permissions and bindings take effect


def test_edited_binding_is_what_fires():
    profile = Profile()
    profile.bindings["fist"].steps = [Step(HOTKEY, "ctrl+t")]
    mapper, os_ctl, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.log == [("keys", ["ctrl", "t"])]


def test_disabled_gesture_does_nothing():
    profile = Profile()
    profile.bindings["fist"].enabled = False
    mapper, os_ctl, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == []


def test_removing_keyboard_permission_blocks_actions():
    profile = Profile()
    profile.permissions["keyboard"] = False
    mapper, os_ctl, _ = make_mapper(profile)
    status, _ = hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == []
    assert "Blocked" in status.flash and "keyboard" in status.flash


def test_removing_volume_permission_blocks_volume_mode():
    profile = Profile()
    profile.permissions["volume"] = False
    mapper, os_ctl, _ = make_mapper(profile)
    status, _ = hold(mapper, 100.0, frames=60, gesture=Gesture.PINCH, pinch=True, ratio=0.2)
    assert mapper.mode is Mode.IDLE
    assert "Blocked" in status.flash


def test_removing_mouse_permission_blocks_air_mouse():
    profile = Profile()
    profile.permissions["mouse"] = False
    mapper, os_ctl, _ = make_mapper(profile)
    hold(mapper, 100.0, frames=60, gesture=Gesture.POINTER)
    assert mapper.mode is Mode.IDLE
    assert os_ctl.cursor == []


def test_disabled_mode_is_not_entered():
    profile = Profile()
    profile.bindings["pointer_mode"].enabled = False
    mapper, _, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.POINTER)
    assert mapper.mode is Mode.IDLE


def test_fired_actions_are_counted():
    mapper, _, _ = make_mapper()
    status, t = hold(mapper, 100.0, gesture=Gesture.FIST)
    assert status.fired_count == 1


# Connected apps and scope


def test_all_apps_scope_works_in_an_unlisted_app():
    mapper, os_ctl, _ = make_mapper()
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == ["play_pause"]


def test_connected_scope_blocks_an_unlisted_app():
    profile = Profile(scope=SCOPE_CONNECTED)
    mapper, os_ctl, _ = make_mapper(profile)
    status, _ = hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == []
    assert "not connected" in status.flash


def test_connected_scope_allows_a_listed_app():
    profile = Profile(scope=SCOPE_CONNECTED, apps=[ConnectedApp("Chrome", "chrome.exe")])
    mapper, os_ctl, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == ["play_pause"]


def test_scope_also_covers_modes():
    profile = Profile(scope=SCOPE_CONNECTED)
    mapper, _, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.POINTER)
    assert mapper.mode is Mode.IDLE


def test_app_turned_off_blocks_gestures_even_in_all_apps_scope():
    profile = Profile(apps=[ConnectedApp("Game", "chrome.exe", enabled=False)])
    mapper, os_ctl, _ = make_mapper(profile)
    status, _ = hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == []
    assert "off in Game" in status.flash


def test_this_apps_own_window_is_always_allowed():
    profile = Profile(scope=SCOPE_CONNECTED)
    mapper, os_ctl, watcher = make_mapper(profile)
    watcher.own = True
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == ["play_pause"]


def test_override_applies_only_in_its_app():
    profile = Profile(apps=[ConnectedApp("Chrome", "chrome.exe", overrides={"fist": [Step(HOTKEY, "ctrl+w")]})])
    mapper, os_ctl, watcher = make_mapper(profile)
    _, t = hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.log == [("keys", ["ctrl", "w"])]

    watcher.exe = "notepad.exe"
    _, t = hold(mapper, t + 5, frames=10)
    hold(mapper, t, gesture=Gesture.FIST)
    assert os_ctl.actions == ["play_pause"]


def test_empty_override_means_do_nothing_in_that_app():
    profile = Profile(apps=[ConnectedApp("Chrome", "chrome.exe", overrides={"fist": []})])
    mapper, os_ctl, _ = make_mapper(profile)
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == []


def test_unknown_front_app_does_not_block():
    profile = Profile(scope=SCOPE_CONNECTED)
    mapper, os_ctl, _ = make_mapper(profile, exe=None)
    hold(mapper, 100.0, gesture=Gesture.FIST)
    assert os_ctl.actions == ["play_pause"]


# Custom gestures in the mapper


def make_custom_profile():
    gesture = CustomGesture("custom:a", "Rock", [0.0], 0.9, steps=[Step(HOTKEY, "ctrl+k")], hold_frames=4)
    return Profile(custom=[gesture])


def test_custom_gesture_fires_once_after_its_hold():
    mapper, os_ctl, _ = make_mapper(make_custom_profile())
    hold_custom(mapper, 100.0, "custom:a", frames=3)
    assert os_ctl.log == []
    hold_custom(mapper, 100.1, "custom:a", frames=60)
    assert os_ctl.log == [("keys", ["ctrl", "k"])]


def test_disabled_custom_gesture_does_not_fire():
    profile = make_custom_profile()
    profile.custom[0].enabled = False
    mapper, os_ctl, _ = make_mapper(profile)
    hold_custom(mapper, 100.0, "custom:a", frames=30)
    assert os_ctl.log == []


def test_deleted_custom_gesture_does_not_fire_or_crash():
    mapper, os_ctl, _ = make_mapper(Profile())
    hold_custom(mapper, 100.0, "custom:gone", frames=30)
    assert os_ctl.log == []


# Custom pose matching on landmarks from real photos


def features_of(case):
    world = FIXTURES[f"{case}|world"].astype(float)
    image = FIXTURES[f"{case}|image"].astype(float)
    aspect = float(FIXTURES[f"{case}|aspect"])
    r = GestureRecognizer(RecognizerConfig()).recognize(world, image, aspect)
    return pose_features(r, world, image, aspect), r.gesture


def cases_for(photo):
    return [c for c in CASES if c.split("|")[1] == photo]


def jittered(feature, n=30, sigma=0.03, seed=0):
    rng = np.random.default_rng(seed)
    return [feature + rng.normal(0, sigma, feature.size) for _ in range(n)]


def record(photo, **kwargs):
    feature, label = features_of(cases_for(photo)[0])
    return build_template(jittered(feature), [label] * 30, lambda _: False, **kwargs)


@pytest.mark.parametrize(
    "photo", ["assets_thumb_up", "gesture_recognizer_thumbs_up", "assets_victory", "assets_pointing_up", "assets_fist"]
)
def test_a_recorded_pose_matches_itself(photo):
    template, threshold = record(photo)
    matcher = CustomMatcher([("g", template, threshold)])
    assert matcher.match(features_of(cases_for(photo)[0])[0]) == "g"


@pytest.mark.parametrize(
    "recorded, other",
    [
        ("assets_thumb_up", "assets_fist"),
        ("assets_thumb_up", "assets_pointing_up"),
        ("assets_thumb_up", "assets_victory"),
        ("assets_thumb_up", "assets_right_hands"),
        ("assets_fist", "assets_thumb_up"),
        ("assets_victory", "assets_pointing_up"),
        ("assets_pointing_up", "assets_victory"),
        ("assets_fist", "assets_pointing_up"),
    ],
)
def test_a_recorded_pose_does_not_match_a_different_pose(recorded, other):
    template, threshold = record(recorded)
    matcher = CustomMatcher([("g", template, threshold)])
    for case in cases_for(other):
        assert matcher.match(features_of(case)[0]) is None, case


def test_matcher_picks_the_closest_of_several():
    a, a_t = record("assets_thumb_up")
    b, b_t = record("assets_victory")
    matcher = CustomMatcher([("up", a, a_t), ("victory", b, b_t)])
    assert matcher.match(features_of(cases_for("assets_victory")[0])[0]) == "victory"
    assert matcher.match(features_of(cases_for("assets_thumb_up")[0])[0]) == "up"


def hand_from(case):
    world = FIXTURES[f"{case}|world"].astype(float)
    image = FIXTURES[f"{case}|image"].astype(float)
    return HandData(image, world, image, float(FIXTURES[f"{case}|aspect"]), "Right", 0.9)


def make_engine(profile):
    return GestureEngine(AppConfig(), FakeOS(), profile)


def test_engine_collects_samples_from_real_landmarks():
    engine = make_engine(Profile())
    engine.sampler = Sampler()
    case = cases_for("assets_fist")[0]
    engine._recognize(hand_from(case))
    assert len(engine.sampler.features) == 1 and engine.sampler.labels == [Gesture.FIST]
    assert engine.sampler.pinch_ratios[0] > 0


def record_for_engine(photo):
    template, threshold = record(photo)
    return CustomGesture("custom:a", "G", template, threshold, steps=[Step(HOTKEY, "ctrl+k")], hold_frames=3)


def test_engine_recognizes_a_recorded_pose_from_real_landmarks():
    engine = make_engine(Profile(custom=[record_for_engine("assets_thumb_up")]))
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom == "custom:a"
    assert engine._recognize(hand_from(cases_for("assets_victory")[0])).custom is None


def test_enabled_builtin_gesture_wins_over_a_custom_recording_of_the_same_pose():
    profile = Profile(custom=[record_for_engine("assets_fist")])
    assert make_engine(profile)._recognize(hand_from(cases_for("assets_fist")[0])).custom is None

    profile.bindings["fist"].enabled = False
    assert make_engine(profile)._recognize(hand_from(cases_for("assets_fist")[0])).custom == "custom:a"


def test_custom_pose_is_not_matched_during_a_control_mode():
    engine = make_engine(Profile(custom=[record_for_engine("assets_thumb_up")]))
    engine.mapper.mode = Mode.VOLUME
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom is None


def test_sampling_pauses_actions_then_restores_the_previous_state():
    engine = make_engine(Profile())
    engine.sampler = Sampler()
    engine._handle_sampling(1.0)
    assert engine.mapper.paused
    engine.sampler = None
    engine._handle_sampling(2.0)
    assert not engine.mapper.paused


def test_sampling_does_not_unpause_if_you_had_paused():
    engine = make_engine(Profile())
    engine.mapper.set_paused(True, 0.0)
    engine.sampler = Sampler()
    engine._handle_sampling(1.0)
    engine.sampler = None
    engine._handle_sampling(2.0)
    assert engine.mapper.paused


def test_profile_swap_applies_hold_times_and_gestures():
    engine = make_engine(Profile())
    new = Profile(custom=[record_for_engine("assets_thumb_up")])
    new.bindings["fist"].hold_frames = 20
    new.general.swipe_distance = 0.35
    engine._apply_profile(new)
    assert engine.cfg.stability.hold_frames["FIST"] == 20
    assert engine.cfg.stability.hold_frames["custom:a"] == 3
    assert engine.cfg.swipe.min_distance == 0.35
    assert engine.router.profile is new


# Ready-made poses, on landmarks from real photos


def ready_of(case):
    world, image, aspect = (
        FIXTURES[f"{case}|world"].astype(float),
        FIXTURES[f"{case}|image"].astype(float),
        float(FIXTURES[f"{case}|aspect"]),
    )
    cfg = RecognizerConfig()
    reading = GestureRecognizer(cfg).recognize(world, image, aspect)
    return ready_pose_of(reading, world, image, aspect, cfg.fist_thumb_max_ratio)


@pytest.mark.parametrize(
    "photo, tag, expected",
    [
        ("assets_thumb_up", "raw", "thumbs_up"),
        ("assets_thumb_up", "mirrored", "thumbs_up"),
        ("gesture_recognizer_thumbs_up", "raw", "thumbs_up"),
        ("gesture_recognizer_thumbs_down", "raw", "thumbs_down"),
        ("gesture_recognizer_thumbs_down", "mirrored", "thumbs_down"),
    ],
)
def test_ready_poses_recognize_real_photos(photo, tag, expected):
    case = next(c for c in CASES if c.split("|")[1] == photo and c.split("|")[2] == tag)
    assert ready_of(case) == expected


def test_no_builtin_pose_is_mistaken_for_a_ready_pose():
    # Fists, open palms, peace signs and pointing, from every real photo of them.
    for case in CASES:
        if case.split("|")[0] != "NONE":
            assert ready_of(case) is None, case


def graft_extended(case, fingers):
    """A real pointing hand with its real index finger copied onto other fingers."""
    world = FIXTURES[f"{case}|world"].astype(float).copy()
    image = FIXTURES[f"{case}|image"].astype(float).copy()
    source = FINGER_JOINTS["index"]
    for name in fingers:
        target = FINGER_JOINTS[name]
        for s, t in zip(source, target):
            world[t] = world[target[0]] + (world[s] - world[source[0]])
            image[t] = image[target[0]] + (image[s] - image[source[0]])
    return world, image, float(FIXTURES[f"{case}|aspect"])


@pytest.mark.parametrize("fingers, expected", [(["pinky"], "rock"), (["middle", "ring"], "three")])
def test_finger_patterns_on_assembled_hands(fingers, expected):
    # I have no photos of these poses, so this is weaker evidence than the tests above.
    cfg = RecognizerConfig()
    case = next(c for c in CASES if "assets_pointing_up|raw" in c)
    world, image, aspect = graft_extended(case, fingers)
    reading = GestureRecognizer(cfg).recognize(world, image, aspect)
    assert reading.gesture is Gesture.NONE  # So it can't clash with a built-in gesture.
    assert ready_pose_of(reading, world, image, aspect, cfg.fist_thumb_max_ratio) == expected


def ready_gesture(pose_id="thumbs_up", **kwargs):
    return CustomGesture("custom:ready", "Mine", [], 0.0, pose_id=pose_id, steps=[Step(HOTKEY, "ctrl+k")], hold_frames=3, **kwargs)


def test_engine_matches_a_ready_gesture_from_real_landmarks():
    engine = make_engine(Profile(custom=[ready_gesture()]))
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom == "custom:ready"
    assert engine._recognize(hand_from(cases_for("assets_fist")[0])).custom is None


def test_ready_gesture_fires_its_action_from_real_landmarks():
    engine = make_engine(Profile(custom=[ready_gesture()]))
    engine.router.watcher = FakeWatcher()
    hand = hand_from(cases_for("assets_thumb_up")[0])
    t = 100.0
    for _ in range(15):
        engine.mapper.update(engine._recognize(hand), t)
        t += DT
    assert engine.os.log == [("keys", ["ctrl", "k"])]


def test_disabled_ready_gesture_is_ignored():
    profile = Profile(custom=[ready_gesture(enabled=False)])
    profile.bindings["open_app"].enabled = False
    engine = make_engine(profile)
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom is None


def fire_from_photo(engine, photo):
    engine.router.watcher = FakeWatcher()
    hand = hand_from(cases_for(photo)[0])
    t = 100.0
    for _ in range(15):
        engine.mapper.update(engine._recognize(hand), t)
        t += DT
    return engine.os.log


def wait_for_step(os_log):
    deadline = time.monotonic() + 2.0
    while not os_log and time.monotonic() < deadline:
        time.sleep(0.01)
    return os_log


def test_thumbs_up_opens_the_default_app_from_real_landmarks():
    engine = make_engine(Profile())
    assert wait_for_step(fire_from_photo(engine, "assets_thumb_up")) == [("app", DEFAULT_APP)]


def test_thumbs_down_opens_the_default_website_from_real_landmarks():
    engine = make_engine(Profile())
    assert wait_for_step(fire_from_photo(engine, "gesture_recognizer_thumbs_down")) == [("url", DEFAULT_WEBSITE)]


def test_opening_an_app_needs_its_permission():
    profile = Profile()
    profile.permissions["open_apps"] = False
    engine = make_engine(profile)
    assert fire_from_photo(engine, "assets_thumb_up") == []
    assert "needs permission" in engine.mapper._flash


def test_turned_off_default_pose_does_nothing():
    profile = Profile()
    profile.bindings["open_app"].enabled = False
    engine = make_engine(profile)
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom is None


def test_your_own_gesture_beats_a_default_pose():
    engine = make_engine(Profile(custom=[ready_gesture()]))
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom == "custom:ready"


def test_older_settings_keep_your_gesture_on_a_pose_that_is_now_a_default(tmp_path):
    path = tmp_path / "s.json"
    saved = asdict(Profile(custom=[ready_gesture()]))
    del saved["bindings"]["open_app"], saved["bindings"]["open_website"]
    path.write_text(json.dumps(saved), encoding="utf-8")
    loaded = load_profile(path)[0]
    assert not loaded.bindings["open_app"].enabled
    assert loaded.bindings["open_website"].enabled


def test_turning_a_default_gesture_back_on_is_remembered(tmp_path):
    path = tmp_path / "s.json"
    profile = Profile(custom=[ready_gesture()])
    save_profile(profile, path)
    assert load_profile(path)[0].bindings["open_app"].enabled


def test_older_settings_get_the_new_gestures_without_changing_permissions(tmp_path):
    path = tmp_path / "s.json"
    saved = asdict(Profile())
    saved["permissions"]["open_apps"] = False
    for key in ("click", "double_click", "open_app", "open_website"):
        del saved["bindings"][key]
    path.write_text(json.dumps(saved), encoding="utf-8")
    loaded = load_profile(path)[0]
    assert loaded.bindings["open_website"].steps == [Step(URL, DEFAULT_WEBSITE)]
    assert loaded.bindings["double_click"].enabled
    assert not loaded.permissions["open_apps"]


def test_pose_taken_by():
    profile = Profile()
    assert profile.pose_taken_by("thumbs_up") == "Open an app"
    assert profile.pose_taken_by("rock") is None
    profile.custom.append(ready_gesture())
    assert profile.pose_taken_by("thumbs_up") == "Mine"
    assert profile.pose_taken_by("thumbs_up", "custom:ready") is None  # It may keep its own pose.


def test_app_names_read_plainly():
    assert describe_step(Step(APP, "explorer.exe")) == "Open File Explorer"
    assert describe_step(Step(APP, r"C:\Apps\Notes.exe")) == "Open Notes"


def test_ready_and_recorded_gestures_work_side_by_side():
    recorded = record_for_engine("assets_victory")
    profile = Profile(custom=[ready_gesture(), recorded])
    profile.bindings["peace"].enabled = False
    profile.bindings["peace_left"].enabled = False
    engine = make_engine(profile)
    assert engine._recognize(hand_from(cases_for("assets_thumb_up")[0])).custom == "custom:ready"
    assert engine._recognize(hand_from(cases_for("assets_victory")[0])).custom == "custom:a"


def test_sampler_notes_the_ready_pose_being_shown():
    engine = make_engine(Profile())
    engine.sampler = Sampler()
    engine._recognize(hand_from(cases_for("assets_thumb_up")[0]))
    assert engine.sampler.ready_poses == ["thumbs_up"]


def test_gesture_using_pose():
    profile = Profile(custom=[ready_gesture()])
    assert profile.gesture_using_pose("thumbs_up").name == "Mine"
    assert profile.gesture_using_pose("rock") is None
    assert profile.gesture_using_pose("thumbs_up", ignore_id="custom:ready") is None


def test_ready_gesture_survives_a_save_and_load(tmp_path):
    path = tmp_path / "s.json"
    save_profile(Profile(custom=[ready_gesture()]), path)
    assert load_profile(path)[0].custom[0].pose_id == "thumbs_up"


def test_settings_from_before_ready_poses_still_load(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(
        '{"custom": [{"id": "c", "name": "Old", "template": [0.1], "threshold": 0.9}], "general": {"practice_mode": true}}',
        encoding="utf-8",
    )
    profile, warning = load_profile(path)
    assert warning is None and profile.custom[0].pose_id == "" and profile.general.show_welcome


def test_start_menu_list_skips_uninstallers(tmp_path, monkeypatch):
    menu = tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Folder"
    menu.mkdir(parents=True)
    (menu / "Notes.lnk").touch()
    (menu / "Uninstall Notes.lnk").touch()
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "none"))
    monkeypatch.setattr(app_watch.sys, "platform", "win32")
    assert list(app_watch.start_menu_apps()) == ["Notes"]


def test_matcher_ignores_templates_of_a_different_size():
    assert CustomMatcher([("g", [1.0, 2.0], 0.9)]).match(np.zeros(5)) is None


def test_matcher_with_no_gestures():
    assert CustomMatcher([]).match(np.zeros(5)) is None


def test_recording_too_short_is_rejected():
    with pytest.raises(RecordingError, match="long enough"):
        build_template([np.zeros(16)] * 3, [Gesture.NONE] * 3)


def test_recording_that_changes_shape_is_rejected():
    a, _ = features_of(cases_for("assets_fist")[0])
    b, _ = features_of(cases_for("assets_right_hands")[0])
    with pytest.raises(RecordingError, match="changed shape"):
        build_template([a] * 15 + [b] * 15, [Gesture.NONE] * 30)


def test_recording_a_pose_already_in_use_is_rejected():
    feature, label = features_of(cases_for("assets_fist")[0])
    assert label is Gesture.FIST
    with pytest.raises(RecordingError, match="already used by Fist"):
        build_template(jittered(feature), [label] * 30, lambda _: True)


def test_recording_a_builtin_pose_is_allowed_once_that_gesture_is_off():
    feature, label = features_of(cases_for("assets_fist")[0])
    template, threshold = build_template(jittered(feature), [label] * 30, lambda _: False)
    assert threshold >= BASE_THRESHOLD and len(template) == feature.size


def test_recording_close_to_another_custom_gesture_is_rejected():
    feature, label = features_of(cases_for("assets_thumb_up")[0])
    template, threshold = build_template(jittered(feature), [label] * 30, lambda _: False)
    with pytest.raises(RecordingError, match='too close to your "First"'):
        build_template(
            jittered(feature, seed=1), [label] * 30, lambda _: False, existing=[("First", np.array(template), threshold)]
        )
