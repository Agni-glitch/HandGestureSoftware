"""Drives the real windows without showing them, with a temporary profile. No camera is used."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QPushButton, QWidget

from config import ROOT, RecognizerConfig
from gesture_control import shell_integration
from gesture_control.actions import MapperStatus, Mode
from gesture_control.custom_gestures import READY_POSES, build_outline, pose_features
from gesture_control.engine import EngineStats, Sampler
from gesture_control.gui import widgets as ui
from gesture_control.gui.capture_dialog import RECORDING_TIPS, CalibrationDialog, RecordPoseDialog
from gesture_control.gui.editors import ActionPicker, GestureEditor, MoreOptions, StepEditor, StepsDialog
from gesture_control.gui.illustrations import (
    GUIDE_STEPS,
    READY_POSE_PICTURES,
    SCENES,
    GestureDemo,
    GestureStill,
    OutlineStill,
    PoseStill,
    custom_picture,
    frame_at,
)
from gesture_control.gui.main_window import MainWindow, _ActivityHandler
from gesture_control.gui.mini_view import caption, corner_position
from gesture_control.gui.pages import AppOverridesDialog, DemoDialog, connected_summary, gesture_summary
from gesture_control.gui.state import AppState, EngineController
from gesture_control.gui.wizard import OWN_POSE, GestureWizard, TryItPanel
from gesture_control.os_control import DryRunController
from gesture_control.profile import BUILTINS, DEFAULT_APP, SCOPE_CONNECTED, ConnectedApp, CustomGesture, load_profile
from gesture_control.recognizer import Gesture, GestureRecognizer
from gesture_control.steps import APP, HOTKEY, SYSTEM, TEXT, URL, WAIT, Step

FIXTURES = np.load(Path(__file__).parent / "fixtures" / "real_hand_landmarks.npz")


@pytest.fixture(scope="session")
def app():
    application = QApplication.instance() or QApplication([])
    yield application
    # Widgets that outlive the application crash the process as it exits.
    for widget in QApplication.allWidgets():
        widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


@pytest.fixture
def env(app, tmp_path, monkeypatch):
    state = AppState(tmp_path / "settings.json", tmp_path / "calibration.json")
    controller = EngineController(state)
    window = MainWindow(state, controller)
    prompts = []

    def answer_yes(_parent, title, text):
        prompts.append((title, text))
        return True

    monkeypatch.setattr(ui, "confirm", answer_yes)
    monkeypatch.setattr(ui, "notify", lambda *_: prompts.append(("notify", _[2])))
    monkeypatch.setattr(ui, "show_error", lambda *_: prompts.append(("error", _[2])))

    class Env:
        pass

    e = Env()
    e.state, e.controller, e.window, e.prompts, e.path = state, controller, window, prompts, tmp_path / "settings.json"
    yield e
    controller.shutdown()
    window.really_quit = True
    window.mini_view.deleteLater()
    window.deleteLater()


def saved(env):
    profile, warning = load_profile(env.path)
    assert warning is None
    return profile


def recorded_pose(photo="assets_thumb_up"):
    prefix = next(k for k in FIXTURES.files if f"|{photo}|raw|0|" in k).rsplit("|", 1)[0]
    world = FIXTURES[f"{prefix}|world"].astype(float)
    image = FIXTURES[f"{prefix}|image"].astype(float)
    aspect = float(FIXTURES[f"{prefix}|aspect"])
    reading = GestureRecognizer(RecognizerConfig()).recognize(world, image, aspect)
    return pose_features(reading, world, image, aspect), reading


class FakeEngine:
    def __init__(self):
        self.sampler = None
        self.profiles = []

    def set_profile(self, profile):
        self.profiles.append(profile)


# Gesture guide and pictures


def render(widget, width=300, height=300):
    widget.resize(width, height)
    image = QImage(widget.size(), QImage.Format_RGB32)
    image.fill(0)
    widget.render(image)
    return image


def distinct_colours(image):
    return {image.pixel(x, y) for x in range(0, image.width(), 4) for y in range(0, image.height(), 4)}


def test_every_builtin_gesture_has_a_demo_and_instructions():
    assert set(SCENES) == set(BUILTINS) == set(GUIDE_STEPS)
    for steps in GUIDE_STEPS.values():
        assert len(steps) >= 3


@pytest.mark.parametrize("gesture_id", list(BUILTINS))
def test_every_demo_loops_with_a_caption_and_draws_something(app, gesture_id):
    scene = SCENES[gesture_id]
    assert [k.t for k in scene.keys] == sorted(k.t for k in scene.keys)
    assert all(k.caption for k in scene.keys)
    # Later keys must not overlap: every moment, including past the end, has a valid frame.
    for step in range(60):
        frame = frame_at(scene, step * 0.2)
        assert frame.caption
        assert 0.0 <= frame.highlight <= 1.0
    assert len(distinct_colours(render(GestureDemo(gesture_id)))) > 2


def test_demos_show_different_things(app):
    pictures = {gesture_id: render(GestureStill(gesture_id), 64, 64).bits().tobytes() for gesture_id in BUILTINS}
    assert len(set(pictures.values())) == len(BUILTINS)


def test_demo_moves_only_while_it_is_visible(app):
    demo = GestureDemo("fist")
    assert not demo.is_playing
    demo.show()
    assert demo.is_playing
    before = demo.time
    demo._tick()
    assert demo.time > before
    demo.hide()
    assert not demo.is_playing
    demo.deleteLater()


def test_a_swipe_demo_moves_the_way_its_name_says():
    for gesture_id, sign in (("swipe_right", 1), ("swipe_left", -1)):
        scene = SCENES[gesture_id]
        assert scene.arrow == gesture_id.split("_")[1]
        assert (frame_at(scene, 1.2).dx - frame_at(scene, 0.8).dx) * sign > 0


def page_texts(page):
    QApplication.processEvents()
    return [w.text() for w in page.findChildren(QLabel) if w.isVisibleTo(page)]


def test_gesture_list_follows_your_changes(env):
    env.state.profile.bindings["fist"].steps = [Step(HOTKEY, "ctrl+t")]
    env.state.profile.custom.append(CustomGesture("custom:a", "Rock", [0.0], 0.9))
    env.state.save()
    texts = page_texts(env.window.gestures)
    assert "Ctrl+T" in texts and "Rock" in texts


def test_summaries_use_plain_words(env):
    assert gesture_summary(env.state, "volume_mode") == "Changes the volume"
    assert gesture_summary(env.state, "pointer_mode") == "Moves the mouse and clicks"
    env.state.profile.bindings["fist"].enabled = False
    assert gesture_summary(env.state, "fist") == "Turned off"


def test_home_button_opens_the_gesture_list(env):
    QTest.mouseClick(env.window.dashboard.guide_button, Qt.LeftButton)
    assert env.window.stack.currentWidget() is env.window.gestures
    assert env.window.nav_buttons["Gestures"].isChecked()


def test_gesture_list_offers_every_free_ready_pose(env):
    texts = page_texts(env.window.gestures)
    for key, pose in READY_POSES.items():
        assert (pose.name in texts) == (env.state.profile.pose_taken_by(key) is None)
    assert "How to add your own gesture" in texts


def test_mouse_gestures_lead_the_list_and_click_has_nothing_to_change(env):
    rows = [w for w in env.window.gestures.findChildren(QWidget) if w.property("gesture_id")]
    assert [r.property("gesture_id") for r in rows][:5] == ["pointer_mode", "click", "double_click", "open_app", "open_website"]
    by_id = {r.property("gesture_id"): r for r in rows}

    def words(row):
        return {b.text() for b in row.findChildren(QPushButton) if not b.isHidden()}

    assert "Change" not in words(by_id["click"]) and "Change" not in words(by_id["double_click"])
    assert "Change" in words(by_id["open_app"])


def test_default_gestures_say_what_they_open(env):
    assert gesture_summary(env.state, "open_app") == "Open File Explorer"
    assert gesture_summary(env.state, "open_website") == "Open www.google.com"
    assert gesture_summary(env.state, "double_click") == "Double click where the cursor is"


def test_missing_mouse_permission_shows_on_click_rows(env):
    env.state.profile.permissions["mouse"] = False
    assert env.window.gestures._missing("double_click", env.state.profile.bindings["double_click"]) == ["Mouse control"]


def test_changing_what_the_default_app_gesture_opens(env):
    dialog = GestureEditor(env.window, env.state, env.controller, "open_app")
    assert dialog.editor.steps() == [Step(APP, DEFAULT_APP)]
    dialog.editor.set_steps([Step(APP, r"C:\Apps\Notes.exe")])
    dialog.save()
    assert saved(env).bindings["open_app"].steps == [Step(APP, r"C:\Apps\Notes.exe")]


def test_wizard_says_who_is_using_a_default_pose(env):
    wizard = GestureWizard(env.window, env.state, env.controller)
    assert not wizard.pose_cards["thumbs_up"].enabled
    assert wizard.pose_cards["rock"].enabled
    wizard.reject()


def test_a_gesture_on_a_default_pose_can_still_be_edited(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "Mine", [], 0.0, pose_id="thumbs_up"))
    wizard = GestureWizard(env.window, env.state, env.controller, gesture_id="custom:a")
    assert wizard.pose_cards["thumbs_up"].enabled
    wizard.reject()
    assert gesture_summary(env.state, "open_app") == 'Your "Mine" gesture uses this pose instead'


def test_a_pose_in_use_leaves_the_add_list(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "My sign", [], 0.0, pose_id="rock"))
    env.state.save()
    # Old rows are deleted later, by the event loop. Tests have to ask for that explicitly.
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    pictures = [w.pose_id for w in env.window.gestures.findChildren(PoseStill)]
    assert pictures.count("rock") == 1  # Its picture in "Your gestures", and not in the add list.
    assert pictures.count("three") == 1


def test_every_ready_pose_has_a_picture(app):
    assert set(READY_POSE_PICTURES) == set(READY_POSES)
    for pose_id in READY_POSES:
        assert len(distinct_colours(render(PoseStill(pose_id), 64, 64))) > 1


def test_welcome_guide_can_be_dismissed_and_brought_back(env):
    home = env.window.dashboard
    env.window.show()
    assert home.welcome.isVisible()
    home.dismiss_welcome()
    assert not home.welcome.isVisible() and not saved(env).general.show_welcome
    env.window.settings.show_welcome()
    assert home.welcome.isVisible() and saved(env).general.show_welcome
    assert env.window.stack.currentWidget() is home


def test_demo_button_opens_the_right_demo(env, monkeypatch):
    opened = []
    monkeypatch.setattr(DemoDialog, "exec", lambda self: opened.append(self.demo.scene))
    env.window.gestures.show_demo("volume_mode")
    assert opened == [SCENES["volume_mode"]]


def test_clicking_a_gesture_picture_opens_its_demo(env, monkeypatch):
    opened = []
    monkeypatch.setattr(env.window.gestures, "show_demo", opened.append)
    env.window.show()
    picture = next(w for w in env.window.gestures.findChildren(GestureStill) if w.scene is SCENES["peace"])
    QTest.mouseClick(picture, Qt.LeftButton)
    assert opened == ["peace"]


def test_recording_stores_a_picture_of_the_hand(env):
    prefix = next(k for k in FIXTURES.files if "|assets_thumb_up|raw|0|" in k).rsplit("|", 1)[0]
    points = FIXTURES[f"{prefix}|image"][:, :2].astype(float)
    dialog = make_recording_dialog(env)
    dialog.begin()
    dialog.advance(dialog.PREPARE_S + 0.1)
    sampler = env.controller.engine.sampler
    for features, label, ratio in thumb_samples():
        sampler.features.append(features)
        sampler.labels.append(label)
        sampler.pinch_ratios.append(ratio)
        sampler.landmarks.append(points)
    dialog.advance(dialog.MEASURE_S + 0.1)
    assert dialog.result() == QDialog.Accepted
    assert len(dialog.outline) == 21
    flat = np.array(dialog.outline)
    assert flat.min() >= -0.001 and flat.max() <= 1.001
    env.controller.engine = None


def test_gestures_recorded_before_pictures_still_load_and_draw(app, tmp_path):
    path = tmp_path / "old.json"
    path.write_text(
        '{"custom": [{"id": "custom:a", "name": "Old", "template": [0.1], "threshold": 0.9}]}', encoding="utf-8"
    )
    profile, warning = load_profile(path)
    assert warning is None and profile.custom[0].outline == []
    assert len(distinct_colours(render(OutlineStill([]), 64, 64))) > 1


def test_outline_keeps_the_hand_in_the_box_and_in_proportion():
    tall = np.array([[0.5, y / 20] for y in range(21)])
    outline = np.array(build_outline([tall, tall]))
    assert outline[:, 1].min() == 0.0 and outline[:, 1].max() == 1.0
    assert outline[:, 0].min() == outline[:, 0].max() == 0.5
    assert build_outline([]) == []


def test_record_dialog_gives_tips(env):
    dialog = RecordPoseDialog(env.controller, env.window)
    assert RECORDING_TIPS in page_texts(dialog)
    dialog.reject()


def test_recording_a_ready_pose_you_already_use_is_refused(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "My sign", [], 0.0, pose_id="thumbs_up"))
    dialog = make_recording_dialog(env)
    dialog.begin()
    dialog.advance(dialog.PREPARE_S + 0.1)
    sampler = env.controller.engine.sampler
    for features, label, ratio in thumb_samples():
        sampler.features.append(features)
        sampler.labels.append(label)
        sampler.pinch_ratios.append(ratio)
        sampler.ready_poses.append("thumbs_up")
    dialog.advance(dialog.MEASURE_S + 0.1)
    assert 'already used by your "My sign"' in dialog.message.text()
    dialog.reject()
    env.controller.engine = None


# Choosing what a gesture does


@pytest.mark.parametrize(
    "steps",
    [
        [Step(SYSTEM, "mute")],
        [Step(URL, "https://example.com")],
        [Step(TEXT, "  spaces kept  ")],
        [Step(HOTKEY, "ctrl+shift+s")],
        [Step(APP, r"C:\Some Folder\app.exe")],
    ],
)
def test_action_picker_round_trips_a_single_action(app, steps):
    picker = ActionPicker(steps)
    assert not picker.is_advanced
    assert picker.steps() == steps
    assert picker.first_error() is None


def test_action_picker_opens_in_step_list_mode_for_several_steps(app):
    steps = [Step(HOTKEY, "ctrl+t"), Step(WAIT, "500"), Step(TEXT, "hi")]
    picker = ActionPicker(steps)
    assert picker.is_advanced and picker.steps() == steps


def test_action_picker_switches_to_several_steps_keeping_your_choice(app):
    picker = ActionPicker([Step(SYSTEM, "mute")])
    picker.toggle_button.click()
    assert picker.is_advanced and picker.steps() == [Step(SYSTEM, "mute")]
    picker.advanced.add_step(Step(WAIT, "100"))
    assert len(picker.steps()) == 2


def test_action_picker_going_back_asks_and_keeps_the_first_step(env):
    picker = ActionPicker([Step(HOTKEY, "ctrl+t"), Step(WAIT, "500")])
    picker.toggle_button.click()
    assert not picker.is_advanced and picker.steps() == [Step(HOTKEY, "ctrl+t")]
    assert any("first step" in text for _, text in env.prompts)


def test_action_picker_going_back_can_be_declined(env, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *_: False)
    steps = [Step(HOTKEY, "ctrl+t"), Step(WAIT, "500")]
    picker = ActionPicker(steps)
    picker.toggle_button.click()
    assert picker.is_advanced and picker.steps() == steps


def test_action_picker_never_selects_a_heading(app):
    for steps in ([], [Step(SYSTEM, "no_such_action")]):
        picker = ActionPicker(steps)
        assert picker.combo.currentData() == f"{SYSTEM}:play_pause"


def test_action_picker_can_offer_do_nothing(app):
    picker = ActionPicker([], allow_nothing=True)
    assert picker.steps() == []
    assert picker.combo.currentText() == "Do nothing"
    assert picker.first_error() is None


def test_action_picker_explains_a_bad_value(app):
    picker = ActionPicker([Step(URL, "youtube")])
    assert "https://" in picker.first_error()
    assert ActionPicker([Step(TEXT, "")]).first_error()


def test_action_picker_shows_a_value_box_only_when_needed(app):
    picker = ActionPicker([Step(SYSTEM, "mute")])
    assert not picker.value_stack.isVisibleTo(picker)
    picker.combo.setCurrentIndex(picker.combo.findData(URL))
    assert picker.value_stack.isVisibleTo(picker) and "browser" in picker.hint.text()


def test_app_list_comes_from_the_start_menu(app, monkeypatch):
    monkeypatch.setattr(
        "gesture_control.app_watch.start_menu_apps", lambda: {"Notepad": r"C:\Start\Notepad.lnk"}
    )
    picker = ActionPicker([Step(SYSTEM, "mute")])
    picker.combo.setCurrentIndex(picker.combo.findData(APP))
    picker.app_combo.setCurrentIndex(picker.app_combo.findData(r"C:\Start\Notepad.lnk"))
    assert picker.steps() == [Step(APP, r"C:\Start\Notepad.lnk")]
    assert picker.first_error() is None


def test_app_choice_is_required(app, monkeypatch):
    monkeypatch.setattr("gesture_control.app_watch.start_menu_apps", lambda: {})
    picker = ActionPicker([Step(SYSTEM, "mute")])
    picker.combo.setCurrentIndex(picker.combo.findData(APP))
    assert picker.first_error() == "Choose an app to open."


def test_more_options_start_hidden(app):
    options = MoreOptions(8, 1.0)
    assert not options.panel.isVisibleTo(options)
    options.toggle_button.click()
    assert options.panel.isVisibleTo(options) and options.toggle_button.text() == "Fewer options"
    options.toggle_button.click()
    assert not options.panel.isVisibleTo(options)


# The add-a-gesture wizard


def test_wizard_adds_a_ready_pose_with_no_recording(env):
    wizard = GestureWizard(env.window, env.state, env.controller)
    assert wizard.step_label.text() == "Step 1 of 3"
    wizard.select_pose("rock")
    wizard.go_next()
    assert wizard.step_label.text() == "Step 2 of 3"
    wizard.picker.set_steps([Step(SYSTEM, "mute")])
    wizard.go_next()
    assert wizard.step_label.text() == "Step 3 of 3" and wizard.next_button.text() == "Save"
    assert wizard.name_edit.text() == "Rock sign"
    wizard.go_next()
    assert wizard.result() == QDialog.Accepted
    gesture = saved(env).custom[0]
    assert gesture.pose_id == "rock" and gesture.name == "Rock sign" and gesture.template == []
    assert gesture.steps == [Step(SYSTEM, "mute")] and gesture.id.startswith("custom:")


def test_wizard_needs_a_pose_before_moving_on(env):
    wizard = GestureWizard(env.window, env.state, env.controller)
    wizard.go_next()
    assert wizard.pages.currentIndex() == 0 and "Pick a pose" in wizard.error.text()
    wizard.reject()


def test_wizard_opened_from_the_list_skips_to_choosing_the_action(env):
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    assert wizard.pages.currentIndex() == 1 and wizard.pose_id == "three"
    wizard.reject()


def test_wizard_checks_the_action_before_moving_on(env):
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.picker.set_steps([Step(URL, "nope")])
    wizard.go_next()
    assert wizard.pages.currentIndex() == 1 and "https://" in wizard.error.text()
    wizard.reject()


def test_wizard_can_go_back_without_losing_anything(env):
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.picker.set_steps([Step(HOTKEY, "ctrl+t")])
    wizard.go_next()
    wizard.go_back()
    wizard.go_back()
    assert wizard.pages.currentIndex() == 0 and wizard.pose_id == "three"
    assert wizard.picker.steps() == [Step(HOTKEY, "ctrl+t")]
    assert not wizard.back_button.isVisibleTo(wizard)
    wizard.reject()


def test_cancelling_the_wizard_adds_nothing(env):
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.go_next()
    wizard.reject()
    assert env.state.profile.custom == []


def test_a_pose_already_in_use_cannot_be_picked_twice(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "Mine", [], 0.0, pose_id="rock"))
    wizard = GestureWizard(env.window, env.state, env.controller)
    assert not wizard.pose_cards["rock"].enabled and wizard.pose_cards["three"].enabled
    wizard.reject()


def fake_record(wizard, monkeypatch, accepted=True):
    class Dialog:
        template, threshold = [0.5] * 13, 0.9
        outline = [[0.5, 0.5]] * 21

        def exec(self):
            return QDialog.Accepted if accepted else QDialog.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr(wizard, "make_record_dialog", Dialog)


def test_wizard_records_your_own_pose(env, monkeypatch):
    wizard = GestureWizard(env.window, env.state, env.controller)
    fake_record(wizard, monkeypatch)
    wizard.select_pose(OWN_POSE)
    wizard.go_next()
    wizard.go_next()
    assert wizard.name_edit.text() == "My gesture 1"
    wizard.go_next()
    gesture = saved(env).custom[0]
    assert gesture.pose_id == "" and gesture.template == [0.5] * 13
    assert gesture.outline == [[0.5, 0.5]] * 21


def test_cancelling_the_recording_leaves_nothing_selected(env, monkeypatch):
    wizard = GestureWizard(env.window, env.state, env.controller)
    fake_record(wizard, monkeypatch, accepted=False)
    wizard.select_pose(OWN_POSE)
    assert wizard.pose_id == ""
    wizard.reject()


def test_wizard_refuses_a_duplicate_name_and_a_blank_one(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "Rock", [], 0.0, pose_id="rock"))
    for name in ("rock", "Fist"):
        wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
        wizard.go_next()
        wizard.name_edit.setText(name)
        wizard.go_next()
        assert "already have" in wizard.error.text()
        wizard.reject()
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.go_next()
    wizard.name_edit.setText("   ")
    wizard.go_next()
    assert "name" in wizard.error.text()
    wizard.reject()
    assert len(env.state.profile.custom) == 1


def test_wizard_asks_for_a_permission_in_plain_words(env):
    env.state.profile.permissions["websites"] = False
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.picker.set_steps([Step(URL, "https://example.com")])
    wizard.go_next()
    wizard.go_next()
    assert any("Open websites" in text for _, text in env.prompts)
    assert saved(env).permissions["websites"]


def test_changing_a_gesture_keeps_its_identity_and_details(env):
    gesture = CustomGesture("custom:a", "Mine", [], 0.0, pose_id="rock", steps=[Step(TEXT, "hi")], hold_frames=12)
    env.state.profile.custom.append(gesture)
    wizard = GestureWizard(env.window, env.state, env.controller, gesture_id="custom:a")
    assert wizard.pose_id == "rock" and wizard.picker.steps() == [Step(TEXT, "hi")]
    wizard.go_next()
    wizard.go_next()
    assert wizard.name_edit.text() == "Mine"
    assert wizard.options.hold_slider.value() == 12
    wizard.go_next()
    assert len(env.state.profile.custom) == 1 and saved(env).custom[0].id == "custom:a"
    assert saved(env).custom[0].hold_frames == 12


def test_switching_a_recorded_gesture_to_a_ready_pose_clears_the_recording(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "Mine", [0.5] * 13, 0.9, outline=[[0.5, 0.5]] * 21))
    wizard = GestureWizard(env.window, env.state, env.controller, gesture_id="custom:a")
    assert wizard.pose_id == OWN_POSE
    wizard.select_pose("rock")
    wizard.go_next()
    wizard.go_next()
    wizard.go_next()
    gesture = saved(env).custom[0]
    assert gesture.pose_id == "rock" and gesture.template == [] and gesture.outline == []


def test_custom_picture_matches_how_the_gesture_was_made(app):
    assert isinstance(custom_picture(CustomGesture("a", "A", [], 0.0, pose_id="rock")), PoseStill)
    assert isinstance(custom_picture(CustomGesture("b", "B", [0.5], 0.9)), OutlineStill)


# Trying a pose with the camera


def panel_with_engine(env, pose_id="rock", template=None):
    panel = TryItPanel(env.controller)
    panel.set_target(pose_id, template, 0.9)
    env.controller.status = EngineController.RUNNING
    env.controller.engine = FakeEngine()
    panel.start()
    assert isinstance(env.controller.engine.sampler, Sampler)
    return panel


def feed(panel, env, poses, features=None):
    sampler = env.controller.engine.sampler
    sampler.ready_poses.extend(poses)
    sampler.features.extend(features or [np.zeros(13)] * len(poses))
    panel.poll()


def test_try_it_confirms_a_held_ready_pose(env):
    panel = panel_with_engine(env)
    feed(panel, env, ["rock"] * 6)
    assert panel.recognised and "Recognised" in panel.status.text()
    panel.stop()
    assert env.controller.engine.sampler is None
    env.controller.engine = None


def test_try_it_explains_a_different_pose(env):
    panel = panel_with_engine(env)
    feed(panel, env, ["rock", "three", None, "rock", "rock", "rock"])
    assert not panel.recognised and "not this pose" in panel.status.text()
    panel.stop()
    env.controller.engine = None


def test_try_it_says_when_it_cannot_see_a_hand(env):
    panel = panel_with_engine(env)
    for _ in range(10):
        panel.poll()
    assert "can't see your hand" in panel.status.text()
    panel.stop()
    env.controller.engine = None


def test_try_it_checks_a_recorded_pose(env):
    template = [0.5] * 13
    panel = panel_with_engine(env, OWN_POSE, template)
    feed(panel, env, [None] * 6, [np.array(template)] * 6)
    assert panel.recognised
    panel.stop()
    env.controller.engine = None


def test_try_it_starts_and_stops_a_silent_camera_when_nothing_is_running(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.controller, "start", lambda **k: calls.append(k))
    monkeypatch.setattr(env.controller, "stop", lambda: calls.append("stop"))
    panel = TryItPanel(env.controller)
    panel.start()
    assert calls == [{"silent": True}]
    panel.stop()
    assert calls[-1] == "stop"


def test_closing_the_wizard_turns_the_camera_off(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.controller, "start", lambda **k: calls.append(k))
    monkeypatch.setattr(env.controller, "stop", lambda: calls.append("stop"))
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.go_next()
    wizard.try_panel.start()
    wizard.reject()
    assert calls == [{"silent": True}, "stop"]


def test_leaving_the_try_page_turns_the_camera_off(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.controller, "start", lambda **k: calls.append(k))
    monkeypatch.setattr(env.controller, "stop", lambda: calls.append("stop"))
    wizard = GestureWizard(env.window, env.state, env.controller, pose_id="three")
    wizard.go_next()
    wizard.try_panel.start()
    wizard.go_back()
    assert calls[-1] == "stop"
    wizard.reject()


def test_opening_the_window_does_not_load_the_slow_libraries():
    code = (
        "import sys; import gesture_control.gui.main_window; "
        "sys.exit(1 if 'mediapipe' in sys.modules else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True).returncode == 0


# Navigation and dashboard


def test_sidebar_switches_pages(env):
    for name, page in (
        ("Gestures", env.window.gestures),
        ("Apps", env.window.apps),
        ("Permissions", env.window.permissions),
        ("Settings", env.window.settings),
        ("Home", env.window.dashboard),
    ):
        QTest.mouseClick(env.window.nav_buttons[name], Qt.LeftButton)
        assert env.window.stack.currentWidget() is page
        assert env.window.nav_buttons[name].isChecked()


def test_start_button_starts_and_stops(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.controller, "start", lambda **k: calls.append(("start", k)))
    monkeypatch.setattr(env.controller, "stop", lambda: calls.append(("stop", {})))
    QTest.mouseClick(env.window.dashboard.start_button, Qt.LeftButton)
    assert calls == [("start", {"practice": False})]

    env.controller.status = EngineController.RUNNING
    env.window.dashboard._on_state(EngineController.RUNNING)
    assert env.window.dashboard.start_button.text() == "Stop"
    QTest.mouseClick(env.window.dashboard.start_button, Qt.LeftButton)
    assert calls[-1][0] == "stop"


def test_practice_mode_is_passed_to_start_and_remembered(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.controller, "start", lambda **k: calls.append(k))
    env.window.dashboard.practice_toggle.setChecked(True)
    QTest.mouseClick(env.window.dashboard.start_button, Qt.LeftButton)
    assert calls == [{"practice": True}]
    assert saved(env).general.practice_mode


def test_buttons_follow_engine_state(env):
    dash = env.window.dashboard
    assert not dash.pause_button.isEnabled() and dash.practice_toggle.isEnabled()
    dash._on_state(EngineController.STARTING)
    assert not dash.start_button.isEnabled() and dash.start_button.text() == "Starting..."
    dash._on_state(EngineController.RUNNING)
    assert dash.pause_button.isEnabled() and not dash.practice_toggle.isEnabled()
    dash._on_state(EngineController.STOPPED)
    assert dash.start_button.isEnabled() and dash.start_button.text() == "Start"


def test_camera_permission_is_required_to_start(env):
    errors = []
    env.controller.failed.connect(errors.append)
    env.state.profile.permissions["camera"] = False
    env.controller.start()
    assert env.controller.status == EngineController.STOPPED
    assert "Camera permission" in errors[0]


def test_connected_count_counts_only_enabled_apps(env):
    profile = env.state.profile
    profile.apps = [ConnectedApp("A", "a.exe"), ConnectedApp("B", "b.exe"), ConnectedApp("C", "c.exe", enabled=False)]
    assert connected_summary(profile, {"a.exe", "c.exe"}) == (2, 1)
    env.state.save()
    assert env.window.dashboard.apps_tile[0].text() == "2"


def test_activity_handler_filters_dry_run_noise(app):
    import logging

    handler = _ActivityHandler()
    received = []
    handler.signals.message.connect(received.append)
    for text in ("[dry-run] volume: 51%", "VOLUME ON"):
        handler.emit(logging.LogRecord("x", logging.INFO, "", 0, text, (), None))
    assert received == ["VOLUME ON"]


def test_profile_changes_reach_a_running_engine(env):
    env.controller.engine = FakeEngine()
    env.state.profile.bindings["fist"].enabled = False
    env.state.save()
    pushed = env.controller.engine.profiles[-1]
    assert not pushed.bindings["fist"].enabled
    # It must be a private copy: the engine reads it on another thread.
    assert pushed is not env.state.profile
    env.controller.engine = None


# Gestures page


def test_toggle_disables_gesture_and_saves(env):
    env.state.profile.bindings["fist"].enabled = True
    env.window.gestures._set_enabled(env.state.profile.bindings["fist"], False)
    assert not saved(env).bindings["fist"].enabled


def test_editing_builtin_changes_steps_timing_and_saves(env):
    dialog = GestureEditor(env.window, env.state, env.controller, "fist")
    dialog.editor.set_steps([Step(HOTKEY, "ctrl+t"), Step(WAIT, "500")])
    dialog.hold_slider.setValue(12)
    dialog.cooldown_spin.setValue(2.5)
    dialog.save()
    assert dialog.result() == QDialog.Accepted
    binding = saved(env).bindings["fist"]
    assert binding.steps == [Step(HOTKEY, "ctrl+t"), Step(WAIT, "500")]
    assert binding.hold_frames == 12 and binding.cooldown_s == 2.5


def test_invalid_step_blocks_saving_with_a_message(env):
    dialog = GestureEditor(env.window, env.state, env.controller, "fist")
    dialog.editor.set_steps([Step(HOTKEY, "ctrl+banana")])
    dialog.save()
    assert "banana" in dialog.error.text()
    assert dialog.result() != QDialog.Accepted
    assert env.state.profile.bindings["fist"].steps == [Step(SYSTEM, "play_pause")]


def test_cancelling_an_edit_changes_nothing(env):
    dialog = GestureEditor(env.window, env.state, env.controller, "fist")
    dialog.editor.set_steps([Step(HOTKEY, "ctrl+t")])
    dialog.reject()
    assert env.state.profile.bindings["fist"].steps == [Step(SYSTEM, "play_pause")]


def test_mode_gesture_has_no_steps_to_edit(env):
    dialog = GestureEditor(env.window, env.state, env.controller, "volume_mode")
    assert dialog.editor is None
    dialog.hold_slider.setValue(15)
    dialog.save()
    assert saved(env).bindings["volume_mode"].hold_frames == 15


def test_step_that_needs_permission_offers_it(env):
    env.state.profile.permissions["websites"] = False
    dialog = GestureEditor(env.window, env.state, env.controller, "fist")
    dialog.editor.set_steps([Step(URL, "https://example.com")])
    dialog.save()
    assert any("Open websites" in text for _, text in env.prompts)
    assert saved(env).permissions["websites"]


def test_declining_a_permission_still_saves_and_shows_a_warning(env, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *_: False)
    dialog = GestureEditor(env.window, env.state, env.controller, "fist")
    dialog.editor.set_steps([Step(TEXT, "hello")])
    dialog.save()
    assert not saved(env).permissions["typing"]
    assert env.window.gestures._missing("fist", env.state.profile.bindings["fist"]) == ["Type text"]


def test_step_list_add_move_remove():
    editor = StepEditor([Step(HOTKEY, "ctrl+a"), Step(WAIT, "100"), Step(TEXT, "x")])
    editor._move(editor.rows[2], -1)
    assert [s.type for s in editor.steps()] == [HOTKEY, TEXT, WAIT]
    editor._move(editor.rows[0], -1)  # Already first: no change.
    assert [s.type for s in editor.steps()] == [HOTKEY, TEXT, WAIT]
    editor._remove(editor.rows[1])
    assert [s.type for s in editor.steps()] == [HOTKEY, WAIT]
    editor.add_step()
    assert len(editor.steps()) == 3


@pytest.mark.parametrize(
    "step",
    [
        Step(SYSTEM, "mute"),
        Step(HOTKEY, "ctrl+shift+s"),
        Step(APP, r"C:\Windows\notepad.exe"),
        Step(URL, "https://example.com"),
        Step(TEXT, "  spaces kept  "),
        Step(WAIT, "750"),
    ],
)
def test_each_step_type_round_trips_through_its_row(step):
    assert StepEditor([step]).steps() == [step]


# Custom gestures, from recording to saving


def make_recording_dialog(env, parent=None):
    dialog = RecordPoseDialog(env.controller, parent or env.window)
    env.controller.status = EngineController.RUNNING
    env.controller.engine = FakeEngine()
    return dialog


def run_capture(dialog, env, features_list):
    """Walk the dialog through its countdown, feeding it samples as the engine would."""
    dialog.begin()
    dialog.advance(dialog.PREPARE_S + 0.1)
    for sample in features_list:
        sampler = env.controller.engine.sampler
        sampler.features.append(sample[0])
        sampler.labels.append(sample[1])
        sampler.pinch_ratios.append(sample[2])
    dialog.advance(dialog.MEASURE_S + 0.1)


def thumb_samples(n=40):
    features, reading = recorded_pose("assets_thumb_up")
    rng = np.random.default_rng(0)
    return [(features + rng.normal(0, 0.02, features.size), reading.gesture, 1.0) for _ in range(n)]


def test_recording_produces_a_template(env):
    dialog = make_recording_dialog(env)
    run_capture(dialog, env, thumb_samples())
    assert dialog.result() == QDialog.Accepted
    assert dialog.template is not None and dialog.threshold > 0
    assert env.controller.engine.sampler is None
    env.controller.engine = None


def test_recording_with_no_hand_explains_itself_and_can_retry(env):
    dialog = make_recording_dialog(env)
    run_capture(dialog, env, [])
    assert dialog.result() != QDialog.Accepted
    assert "visible" in dialog.message.text()
    assert dialog.start_button.isEnabled() and dialog.start_button.text() == "Try again"
    dialog.reject()
    env.controller.engine = None


def test_recording_a_pose_already_in_use_is_refused(env):
    features, reading = recorded_pose("assets_fist")
    assert reading.gesture is Gesture.FIST
    dialog = make_recording_dialog(env)
    run_capture(dialog, env, [(features, reading.gesture, 0.3)] * 40)
    assert "already used by Fist" in dialog.message.text()
    dialog.reject()
    env.controller.engine = None


def test_cancelling_a_recording_clears_the_sampler(env):
    dialog = make_recording_dialog(env)
    dialog.begin()
    dialog.advance(dialog.PREPARE_S + 0.1)
    assert env.controller.engine.sampler is not None
    dialog.reject()
    assert env.controller.engine.sampler is None
    env.controller.engine = None


def test_recording_with_the_engine_off_starts_and_stops_a_silent_one(env, monkeypatch):
    calls = []
    monkeypatch.setattr(env.controller, "start", lambda **k: calls.append(k))
    monkeypatch.setattr(env.controller, "stop", lambda: calls.append("stop"))
    dialog = RecordPoseDialog(env.controller, env.window)
    dialog.begin()
    assert calls == [{"silent": True}]
    dialog.reject()
    assert calls[-1] == "stop"


def test_silent_engine_can_only_get_the_dry_run_controller(env, monkeypatch):
    # A recording must not press keys, even if real mode was asked for by mistake.
    monkeypatch.setattr(EngineController, "_run", lambda self, stop: None)
    env.controller.start(practice=False, silent=True)
    env.controller._thread.join(timeout=2)
    assert env.controller.practice
    engine = env.controller._make_engine(env.state.profile)
    assert isinstance(engine.os, DryRunController)


def test_deleting_a_custom_gesture_removes_it_and_its_app_steps(env):
    profile = env.state.profile
    profile.custom.append(CustomGesture("custom:a", "Rock", [0.0], 0.9))
    profile.apps.append(ConnectedApp("Chrome", "chrome.exe", overrides={"custom:a": [Step(WAIT, "5")], "fist": []}))
    env.window.gestures.delete("custom:a")
    assert saved(env).custom == []
    assert list(saved(env).apps[0].overrides) == ["fist"]


def test_gesture_list_shows_custom_gestures(env):
    env.state.profile.custom.append(CustomGesture("custom:a", "Rock", [0.0], 0.9))
    env.state.save()
    QApplication.processEvents()
    rows = [env.window.gestures.rows.itemAt(i).widget() for i in range(env.window.gestures.rows.count())]
    ids = [r.property("gesture_id") for r in rows if r is not None and r.property("gesture_id")]
    assert "custom:a" in ids and "fist" in ids


# Connected apps


def test_add_and_remove_app(env):
    page = env.window.apps
    assert page.add_app("Chrome.EXE")
    assert saved(env).apps[0].exe == "chrome.exe" and saved(env).apps[0].name == "Chrome"
    assert not page.add_app("chrome.exe")  # Not added twice.
    assert len(saved(env).apps) == 1
    page.remove(env.state.profile.apps[0])
    assert saved(env).apps == []


def test_scope_choice_is_saved_and_warns_when_empty(env):
    page = env.window.apps
    page.scope_connected.setChecked(True)
    assert saved(env).scope == SCOPE_CONNECTED
    assert "No apps are listed" in page.warning.text()


def test_app_toggle_is_saved(env):
    env.window.apps.add_app("chrome.exe")
    env.window.apps._set_enabled(env.state.profile.apps[0], False)
    assert not saved(env).apps[0].enabled


def test_per_app_steps_can_be_set_and_reset(env, monkeypatch):
    app_entry = ConnectedApp("PowerPoint", "powerpnt.exe")
    env.state.profile.apps.append(app_entry)
    dialog = AppOverridesDialog(env.window, env.state, env.controller, app_entry)

    def fake_exec(self):
        self.editor.set_steps([Step(HOTKEY, "right")])
        self.save()
        return self.result()

    monkeypatch.setattr(StepsDialog, "exec", fake_exec)
    dialog.change("swipe_right")
    assert saved(env).apps[0].overrides["swipe_right"] == [Step(HOTKEY, "right")]
    dialog.reset("swipe_right")
    assert saved(env).apps[0].overrides == {}


# Permissions


def test_adding_and_removing_a_permission(env):
    page = env.window.permissions
    page.add("typing")
    assert saved(env).permissions["typing"]
    page.remove("typing")
    assert not saved(env).permissions["typing"]


def test_removing_a_permission_warns_what_it_breaks(env):
    env.window.permissions.remove("volume")
    assert any("Volume pinch" in text for _, text in env.prompts)
    assert not saved(env).permissions["volume"]


def test_declining_the_warning_keeps_the_permission(env, monkeypatch):
    monkeypatch.setattr(ui, "confirm", lambda *_: False)
    env.window.permissions.remove("volume")
    assert env.state.profile.permissions["volume"]


def test_removing_camera_stops_a_running_engine(env, monkeypatch):
    stopped = []
    monkeypatch.setattr(env.controller, "stop", lambda: stopped.append(True))
    env.window.permissions.remove("camera")
    assert stopped == [True]


def test_start_with_windows_creates_and_removes_the_shortcut(env, monkeypatch):
    calls = []
    monkeypatch.setattr(shell_integration, "set_start_with_windows", calls.append)
    env.window.permissions.add("startup")
    env.window.permissions.remove("startup")
    assert calls == [True, False]


def test_failure_to_create_startup_shortcut_leaves_it_off(env, monkeypatch):
    def fail(_):
        raise OSError("nope")

    monkeypatch.setattr(shell_integration, "set_start_with_windows", fail)
    env.window.permissions.add("startup")
    assert not env.state.profile.permissions["startup"]
    assert any("nope" in text for _, text in env.prompts)


# Settings


def test_settings_are_saved(env):
    page = env.window.settings
    page.auto_start.setChecked(True)
    page.close_to_tray.setChecked(False)
    page.camera.setValue(2)
    page.swipe.setValue(30)
    general = saved(env).general
    assert general.start_engine_on_launch and not general.close_to_tray
    assert general.camera_index == 2 and general.swipe_distance == 0.3


def test_reset_keeps_custom_gestures_and_apps(env):
    profile = env.state.profile
    profile.bindings["fist"].steps = [Step(HOTKEY, "ctrl+t")]
    profile.custom.append(CustomGesture("custom:a", "Rock", [0.0], 0.9))
    profile.apps.append(ConnectedApp("Chrome", "chrome.exe"))
    env.window.settings.reset_gestures()
    loaded = saved(env)
    assert loaded.bindings["fist"].steps == [Step(SYSTEM, "play_pause")]
    assert len(loaded.custom) == 1 and len(loaded.apps) == 1


def test_calibration_writes_its_file(env):
    dialog = CalibrationDialog(env.controller, env.window)
    env.controller.status = EngineController.RUNNING
    env.controller.engine = FakeEngine()
    dialog.begin()
    dialog.advance(dialog.PREPARE_S + 0.1)
    env.controller.engine.sampler.pinch_ratios.extend([0.2] * 30)
    dialog.advance(dialog.MEASURE_S + 0.1)
    assert "spread" in dialog.instruction.text()
    dialog.advance(dialog.PREPARE_S + 0.1)
    env.controller.engine.sampler.pinch_ratios.extend([1.4] * 30)
    dialog.advance(dialog.MEASURE_S + 0.1)
    assert dialog.result() == QDialog.Accepted
    assert env.state.calibration_path.exists()
    env.controller.engine = None


# Closing the window


def test_closing_hides_to_tray_and_keeps_engine_running(env, monkeypatch):
    class FakeTray:
        def showMessage(self, *a):
            env.prompts.append(("tray", a[1]))

        def hide(self):
            pass

    stopped = []
    monkeypatch.setattr(env.controller, "shutdown", lambda *a: stopped.append(True))
    env.window.tray = FakeTray()
    env.window.show()
    env.window.close()
    assert not env.window.isVisible()
    assert stopped == []
    assert env.prompts[-1][0] == "tray"
    env.window.tray = None


def test_quit_shuts_the_engine_down(env, monkeypatch):
    stopped = []
    monkeypatch.setattr(env.controller, "shutdown", lambda *a: stopped.append(True))
    env.window.show()
    env.window.quit_app()
    assert stopped == [True]


# The pinned camera


def run_engine(env, status=EngineController.RUNNING):
    env.controller.engine = FakeEngine()
    env.controller._set_status(status)


def live_stats(mode=Mode.IDLE, gesture=Gesture.NONE, tracking=True, position=None, **status):
    return EngineStats(30.0, 11.0, tracking, MapperStatus(mode, gesture, 0.0, **status), position)


@pytest.fixture
def running_env(env):
    env.window.show()
    run_engine(env)
    yield env
    env.controller.engine = None
    env.controller._set_status(EngineController.STOPPED)


def test_camera_is_pinned_only_while_running_and_minimized(running_env):
    env = running_env
    mini = env.window.mini_view
    assert not mini.isVisible()
    env.window.showMinimized()
    assert mini.isVisible()
    assert env.controller.engine.compact_preview
    env.window.showNormal()
    assert not mini.isVisible()
    assert not env.controller.engine.compact_preview


def test_camera_is_pinned_while_in_the_tray(running_env, monkeypatch):
    env = running_env
    env.window.hide()
    assert env.window.mini_view.isVisible()
    env.window.show_window()
    assert not env.window.mini_view.isVisible()


def test_minimizing_before_starting_pins_the_camera_on_start(env):
    env.window.show()
    env.window.showMinimized()
    assert not env.window.mini_view.isVisible()
    run_engine(env, EngineController.STARTING)
    assert env.window.mini_view.isVisible()
    env.controller.engine = None
    env.controller._set_status(EngineController.STOPPED)
    assert not env.window.mini_view.isVisible()


def test_recording_a_pose_never_pins_the_camera(env):
    env.window.hide()
    env.controller.silent = True
    run_engine(env)
    assert not env.window.mini_view.isVisible()
    env.controller.engine = None
    env.controller.silent = False
    env.controller._set_status(EngineController.STOPPED)


def test_settings_turn_the_pinned_camera_off_straight_away(running_env):
    env = running_env
    env.window.hide()
    page = env.window.settings
    page.pin_camera.setChecked(False)
    assert not env.window.mini_view.isVisible()
    assert not saved(env).general.pin_camera
    assert not page.pin_corner.isEnabled()
    page.pin_camera.setChecked(True)
    assert env.window.mini_view.isVisible()
    page.show_preview.setChecked(False)
    assert not env.window.mini_view.isVisible()
    assert not page.pin_camera.isEnabled()


@pytest.mark.parametrize(
    "corner, expected",
    [
        ("bottom_right", QPoint(100 + 800 - 320 - 12, 50 + 600 - 300 - 12)),
        ("bottom_left", QPoint(100 + 12, 50 + 600 - 300 - 12)),
        ("top_right", QPoint(100 + 800 - 320 - 12, 50 + 12)),
        ("top_left", QPoint(100 + 12, 50 + 12)),
        ("nonsense from a hand-edited file", QPoint(100 + 800 - 320 - 12, 50 + 600 - 300 - 12)),
    ],
)
def test_pinned_camera_sits_inside_the_chosen_corner(corner, expected):
    assert corner_position(QRect(100, 50, 800, 600), QSize(320, 300), corner) == expected


def test_changing_the_corner_moves_the_pinned_camera(running_env):
    env = running_env
    env.window.hide()
    mini = env.window.mini_view
    before = mini.pos()
    page = env.window.settings
    page.pin_corner.setCurrentIndex(page.pin_corner.findData("top_left"))
    assert saved(env).general.pin_corner == "top_left"
    assert mini.pos() != before


def test_pinned_camera_lets_input_through_and_stays_on_top(env):
    flags = env.window.mini_view.windowFlags()
    assert flags & Qt.WindowTransparentForInput
    assert flags & Qt.WindowDoesNotAcceptFocus
    assert flags & Qt.WindowStaysOnTopHint
    # Windows would minimize it along with a parent, which is exactly when it's needed.
    assert env.window.mini_view.parent() is None
    assert env.window.mini_view.testAttribute(Qt.WA_ShowWithoutActivating)


def test_pinned_camera_shows_the_gesture_and_where_the_finger_lands(running_env):
    env = running_env
    env.window.hide()
    mini = env.window.mini_view
    image = np.full((480, 640, 3), 90, np.uint8)
    stats = live_stats(Mode.POINTER, Gesture.POINTER, position=(0.25, 0.75))
    env.controller.live_ready.emit(image, stats)
    assert mini.title.text() == "Pointer"
    assert mini.detail.text() == "Moving the mouse"
    assert mini.map.marker[0] == (0.25, 0.75)
    assert not mini.picture.pixmap().isNull()
    image = render(mini, mini.sizeHint().width(), mini.sizeHint().height())
    assert len(distinct_colours(image)) > 3


def test_a_frame_arriving_after_the_window_came_back_is_ignored(running_env):
    env = running_env
    env.controller.live_ready.emit(np.full((480, 640, 3), 90, np.uint8), live_stats())
    assert env.window.mini_view.picture.pixmap().isNull()


def test_unpinning_forgets_the_last_picture(running_env):
    env = running_env
    env.window.hide()
    mini = env.window.mini_view
    env.controller.live_ready.emit(np.full((480, 640, 3), 90, np.uint8), live_stats(position=(0.5, 0.5)))
    assert not mini.picture.pixmap().isNull()
    env.window.show_window()
    assert mini.picture.pixmap().isNull()
    assert mini.map.marker[0] is None


@pytest.mark.parametrize(
    "stats, practice, expected",
    [
        (live_stats(tracking=False), False, ("No hand", "", "")),
        (live_stats(), False, ("Hand seen", "", "")),
        (live_stats(gesture=Gesture.FIST), False, ("Fist", "", "")),
        (live_stats(gesture=Gesture.FIST), True, ("Fist", "Practice mode", "")),
        (live_stats(gesture="open_app"), False, ("Open an app", "", "")),
        (live_stats(Mode.PAUSED, Gesture.FIST), False, ("Paused", "Gestures are ignored for now", "paused")),
        (live_stats(Mode.POINTER, Gesture.POINTER), False, ("Pointer", "Moving the mouse", "active")),
        (live_stats(Mode.POINTER, Gesture.PINCH, mouse_down=True), False, ("Pinch", "Holding the click", "active")),
        (live_stats(Mode.POINTER, Gesture.POINTER), True, ("Pointer", "Practice: Moving the mouse", "active")),
        (live_stats(Mode.VOLUME, Gesture.PINCH, volume=0.456), False, ("Pinch", "Changing the volume: 46%", "active")),
        (live_stats(gesture=Gesture.FIST, flash="PLAY/PAUSE", flash_at=99.5), False, ("Fist", "PLAY/PAUSE", "")),
        (live_stats(gesture=Gesture.FIST, flash="PLAY/PAUSE", flash_at=10.0), False, ("Fist", "", "")),
    ],
)
def test_pinned_camera_caption(env, stats, practice, expected):
    assert caption(stats, env.state.profile, practice, now=100.0) == expected


def test_a_long_gesture_name_is_shortened_to_fit(running_env):
    env = running_env
    env.window.hide()
    env.state.profile.custom.append(CustomGesture("custom:a", "A very long name " * 10, [0.0], 0.9))
    env.controller.live_ready.emit(np.full((480, 640, 3), 90, np.uint8), live_stats(gesture="custom:a"))
    mini = env.window.mini_view
    assert mini.title.text().endswith("…")
    assert mini.sizeHint().width() == 320
