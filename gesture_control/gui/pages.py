"""The app's pages."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QMenu,
    QRadioButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from gesture_control import __version__, app_watch, shell_integration
from gesture_control.custom_gestures import READY_POSES
from gesture_control.gui import widgets as ui
from gesture_control.gui.capture_dialog import CalibrationDialog
from gesture_control.gui.editors import GestureEditor, StepsDialog, missing_permissions
from gesture_control.gui.illustrations import (
    GENERAL_TIPS,
    GUIDE_STEPS,
    GestureDemo,
    GestureStill,
    OutlineStill,
    PoseStill,
    custom_picture,
)
from gesture_control.gui.state import AppState, EngineController
from gesture_control.gui.widgets import Page, Toggle, bgr_to_pixmap, button, card, clear_layout, label
from gesture_control.gui.wizard import HOW_TO_ADD, GestureWizard
from gesture_control.profile import (
    BUILTINS,
    PERMISSIONS,
    POSE_BUILTINS,
    SCOPE_ALL,
    SCOPE_CONNECTED,
    ConnectedApp,
    Profile,
    default_bindings,
)
from gesture_control.recognizer import Gesture
from gesture_control.steps import describe_steps

_BUILTIN_LABELS = {
    Gesture.OPEN_PALM: "Open palm",
    Gesture.FIST: "Fist",
    Gesture.PEACE: "Peace sign",
    Gesture.POINTER: "Pointer",
    Gesture.PINCH: "Pinch",
}
_MODE_SUMMARIES = {
    "volume_mode": "Changes the volume",
    "pointer_mode": "Moves the mouse and clicks",
    "click": "Left click where the cursor is",
    "double_click": "Double click where the cursor is",
}
_MOUSE_GESTURES = ("pointer_mode", "click", "double_click")
_LAPTOP_GESTURES = (*_MOUSE_GESTURES, *POSE_BUILTINS)
_HANDS_FREE = (
    "Point to move the cursor. While pointing, pinch to click, or raise your middle finger to double click. "
    "Open your palm to stop pointing, then give a thumbs up or thumbs down to open things."
)


def connected_summary(profile: Profile, running: set[str]) -> tuple[int, int]:
    """(connected apps, how many of those are running now)."""
    connected = [a for a in profile.apps if a.enabled]
    return len(connected), sum(1 for a in connected if a.exe in running)


def gesture_display_name(profile: Profile, gesture) -> str | None:
    """The name to show for what the engine has settled on. None when there's no gesture."""
    name = _BUILTIN_LABELS.get(gesture)
    if name is None and gesture != Gesture.NONE:
        name = profile.gesture_name(gesture)
    return name


class DashboardPage(Page):
    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__("Home")
        self.state = state
        self.controller = controller
        self.running_names: set[str] = set()
        self.watcher = app_watch.ForegroundWatcher()

        controls = QHBoxLayout()
        controls.setSpacing(12)
        practice_row = QHBoxLayout()
        practice_row.setSpacing(10)
        self.practice_toggle = Toggle(
            state.profile.general.practice_mode,
            "Recognises gestures and shows what would happen, without touching your computer.",
        )
        self.practice_toggle.toggled.connect(self._set_practice)
        practice_row.addWidget(self.practice_toggle)
        practice_row.addWidget(label("Practice mode"))
        controls.addLayout(practice_row)
        self.pause_button = button("Pause", controller.toggle_pause)
        controls.addWidget(self.pause_button)
        self.start_button = button("Start", self._toggle_engine, "startButton")
        self.start_button.setMinimumWidth(130)
        controls.addWidget(self.start_button)
        self.header.addLayout(controls)

        self.welcome = self._build_welcome()
        self.body.addWidget(self.welcome)
        self.welcome.setVisible(state.profile.general.show_welcome)

        tiles = QHBoxLayout()
        tiles.setSpacing(12)
        self.status_tile = self._tile(tiles, "Status")
        self.gesture_tile = self._tile(tiles, "Gesture I can see")
        self.apps_tile = self._tile(tiles, "Apps connected")
        self.actions_tile = self._tile(tiles, "Gestures used")
        self.actions_tile[0].setText("0")
        self.body.addLayout(tiles)

        main = QHBoxLayout()
        main.setSpacing(12)

        preview_card = card()
        preview_layout = QVBoxLayout(preview_card)
        self.preview = label("Press Start to turn on the camera", "preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(520, 390)
        preview_layout.addWidget(self.preview, 1)
        main.addWidget(preview_card, 3)

        activity_card = card()
        activity_layout = QVBoxLayout(activity_card)
        activity_layout.addWidget(label("App you're using", "section"))
        self.front_app = label("", "muted", wrap=True)
        activity_layout.addWidget(self.front_app)
        activity_layout.addSpacing(8)
        activity_layout.addWidget(label("What just happened", "section"))
        self.activity_list = QListWidget()
        self.activity_list.setFocusPolicy(Qt.NoFocus)
        self.activity_list.setWordWrap(True)
        activity_layout.addWidget(self.activity_list, 1)
        activity_layout.addWidget(label("Ctrl+Alt+G pauses from any window.", "muted"))
        self.guide_button = button("See all gestures")
        activity_layout.addWidget(self.guide_button)
        main.addWidget(activity_card, 2)
        self.body.addLayout(main, 1)

        controller.state_changed.connect(self._on_state)
        controller.frame_ready.connect(self._on_frame)
        controller.stats_ready.connect(self._on_stats)
        state.changed.connect(self.refresh_apps)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_front_app)
        self._timer.start(1000)
        self._poll_counter = 0
        self._on_state(controller.status)
        self.refresh_running()

    def _build_welcome(self) -> QWidget:
        box = card()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 12)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label("Welcome! Here's how to start", "section"))
        steps = (
            "1.  Turn on Practice mode. It lets you try gestures without anything really happening.",
            "2.  Press Start, and hold your hand up where the camera can see it.",
            "3.  Open Gestures to watch how each one is done, and add your own.",
        )
        text.addWidget(label("\n".join(steps), "muted", wrap=True))
        layout.addLayout(text, 1)
        layout.addWidget(button("Got it", self.dismiss_welcome))
        return box

    def dismiss_welcome(self) -> None:
        self.welcome.hide()
        self.state.profile.general.show_welcome = False
        self.state.save()

    def _tile(self, parent_layout, caption: str):
        tile = card()
        layout = QVBoxLayout(tile)
        layout.setSpacing(0)
        value = label("-", "tileValue")
        value.setTextInteractionFlags(Qt.NoTextInteraction)
        sub = label("", "tileLabel")
        layout.addWidget(label(caption, "tileLabel"))
        layout.addWidget(value)
        layout.addWidget(sub)
        parent_layout.addWidget(tile, 1)
        return value, sub

    def _set_practice(self, enabled: bool) -> None:
        self.state.profile.general.practice_mode = enabled
        self.state.save()
        if self.controller.is_active and not self.controller.silent:
            self.add_activity("Practice mode applies the next time you press Start.")

    def _toggle_engine(self) -> None:
        if self.controller.is_active:
            self.controller.stop()
        else:
            self.controller.start(practice=self.state.profile.general.practice_mode)

    def _on_state(self, status: str) -> None:
        c = EngineController
        silent = self.controller.silent
        text = {c.STOPPED: "Start", c.STARTING: "Starting...", c.RUNNING: "Stop", c.STOPPING: "Stopping..."}[status]
        self.start_button.setText(text)
        self.start_button.setEnabled(status in (c.STOPPED, c.RUNNING) and not (silent and status == c.RUNNING))
        self.start_button.setProperty("running", status == c.RUNNING)
        self.start_button.style().polish(self.start_button)
        self.pause_button.setEnabled(status == c.RUNNING and not silent)
        self.practice_toggle.setEnabled(status == c.STOPPED)

        value, sub = self.status_tile
        if status == c.STOPPED:
            value.setText("Off")
            sub.setText("Camera is off")
            self.gesture_tile[0].setText("-")
            self.gesture_tile[1].setText("")
            self.preview.clear()
            self.preview.setText("Press Start to turn on the camera")
            self.pause_button.setText("Pause")
        elif status == c.STARTING:
            value.setText("Starting")
            sub.setText("This takes a few seconds...")
            self.preview.setText("Opening the camera...")
        elif status == c.RUNNING:
            value.setText("Practice" if self.controller.practice else "On")
            sub.setText("")

    def _on_frame(self, image) -> None:
        # Hidden in the tray, minimized or on another page: no need to spend CPU on drawing.
        if not self.preview.isVisible() or self.window().isMinimized():
            return
        self.preview.setPixmap(
            bgr_to_pixmap(image).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _on_stats(self, stats) -> None:
        status = stats.status
        paused = status.mode.value == "PAUSED"
        self.pause_button.setText("Resume" if paused else "Pause")
        value, sub = self.status_tile
        if paused:
            value.setText("Paused")
        else:
            value.setText("Practice" if self.controller.practice else "On")
        sub.setText(f"{stats.fps:.0f} frames per second" if stats.fps else "")

        name = gesture_display_name(self.state.profile, status.gesture)
        self.gesture_tile[0].setText(name or ("Hand seen" if stats.tracking else "-"))
        mode = {"VOLUME": "Changing the volume", "POINTER": "Controlling the mouse"}.get(status.mode.value, "")
        self.gesture_tile[1].setText(mode)
        self.actions_tile[0].setText(str(status.fired_count))

    def add_activity(self, text: str) -> None:
        self.activity_list.insertItem(0, f"{datetime.now():%H:%M:%S}  {text}")
        while self.activity_list.count() > 200:
            self.activity_list.takeItem(self.activity_list.count() - 1)

    def refresh_running(self) -> None:
        try:
            self.running_names = app_watch.running_process_names()
        except Exception:
            self.running_names = set()
        self.refresh_apps()

    def refresh_apps(self) -> None:
        connected, running = connected_summary(self.state.profile, self.running_names)
        self.apps_tile[0].setText(str(connected))
        if self.state.profile.scope == SCOPE_ALL:
            self.apps_tile[1].setText("Gestures work in every app")
        else:
            self.apps_tile[1].setText(f"{running} running. Gestures only work in these")

    def _poll_front_app(self) -> None:
        current = self.watcher.current()
        if self.watcher.is_own_window():
            self.front_app.setText("This app")
        elif current is None:
            self.front_app.setText("Unknown")
        else:
            app = self.state.profile.find_app(current[1])
            blocked = (app is not None and not app.enabled) or (
                app is None and self.state.profile.scope == SCOPE_CONNECTED
            )
            name = app.name if app else app_watch.friendly_name(current[1])
            self.front_app.setText(f"{name}: {'gestures off' if blocked else 'gestures on'}")
        self._poll_counter += 1
        if self._poll_counter % 5 == 0:
            self.refresh_running()


def gesture_summary(state: AppState, gesture_id: str) -> str:
    binding = state.profile.bindings[gesture_id]
    if not binding.enabled:
        return "Turned off"
    if gesture_id in _MODE_SUMMARIES:
        return _MODE_SUMMARIES[gesture_id]
    owner = state.profile.gesture_using_pose(POSE_BUILTINS[gesture_id]) if gesture_id in POSE_BUILTINS else None
    if owner is not None:
        return f'Your "{owner.name}" gesture uses this pose instead'
    return describe_steps(binding.steps)


def _numbered(steps: list[str]) -> QWidget:
    return label("\n".join(f"{number}.  {step}" for number, step in enumerate(steps, start=1)), "muted", wrap=True)


class DemoDialog(QDialog):
    """A large demonstration of one gesture."""

    def __init__(self, parent, state: AppState, gesture_id: str) -> None:
        super().__init__(parent)
        info = BUILTINS[gesture_id]
        self.setWindowTitle(info.name)
        self.setMinimumWidth(680)
        outer = QHBoxLayout(self)
        outer.setSpacing(20)
        self.demo = GestureDemo(gesture_id, side=320)
        outer.addWidget(self.demo, 3)
        side = QVBoxLayout()
        side.setSpacing(10)
        side.addWidget(label(info.name, "title"))
        side.addWidget(label(gesture_summary(state, gesture_id), "accentText", wrap=True))
        side.addWidget(_numbered(GUIDE_STEPS[gesture_id]))
        side.addWidget(label(GENERAL_TIPS, "muted", wrap=True))
        side.addStretch(1)
        side.addWidget(button("Close", self.accept), alignment=Qt.AlignRight)
        outer.addLayout(side, 2)


class GesturesPage(Page):
    """The full list: gestures ready to use, your own, and more you can add."""

    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__("Gestures", "Click a picture to watch how a gesture is done. Use the switch to turn it on or off.")
        self.state = state
        self.controller = controller
        self.header.addWidget(button("+ Add a gesture", lambda: self.add(), "primary"), alignment=Qt.AlignTop)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.body.addLayout(self.rows)
        self.body.addStretch(1)
        state.changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        clear_layout(self.rows)
        profile = self.state.profile
        self.rows.addWidget(label("Use your laptop without a mouse", "section"))
        self.rows.addWidget(label(_HANDS_FREE, "muted", wrap=True))
        for key in _LAPTOP_GESTURES:
            self.rows.addWidget(self._builtin_row(key, BUILTINS[key]))

        self.rows.addSpacing(10)
        self.rows.addWidget(label("Music, volume and desktops", "section"))
        for key, info in BUILTINS.items():
            if key not in _LAPTOP_GESTURES:
                self.rows.addWidget(self._builtin_row(key, info))

        self.rows.addSpacing(10)
        self.rows.addWidget(label("Your gestures", "section"))
        if not profile.custom:
            self.rows.addWidget(label("You haven't added any yet. Choose one below.", "muted"))
        for gesture in profile.custom:
            self.rows.addWidget(self._custom_row(gesture))

        self.rows.addSpacing(10)
        self.rows.addWidget(label("More gestures you can add", "section"))
        self.rows.addWidget(label("Pick one, choose what it does, and you're done. No recording needed.", "muted"))
        grid = QGridLayout()
        grid.setSpacing(8)
        available = [key for key in READY_POSES if profile.pose_taken_by(key) is None]
        for position, key in enumerate(available):
            grid.addWidget(self._library_card(key), *divmod(position, 2))
        grid.addWidget(self._own_pose_card(), *divmod(len(available), 2))
        self.rows.addLayout(grid)

        self.rows.addSpacing(10)
        self.rows.addWidget(self._how_to_card())

    def _how_to_card(self) -> QWidget:
        box = card()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addWidget(label("How to add your own gesture", "section"))
        for number, (title, detail) in enumerate(HOW_TO_ADD, start=1):
            layout.addWidget(label(f"{number}.  {title}. {detail}", "muted", wrap=True))
        layout.addSpacing(4)
        layout.addWidget(label("Good to know", "section"))
        tips = (
            "Choose hand shapes that look clearly different from each other.",
            "If a gesture fires by accident, make it hold longer under More options, or choose another pose.",
            "If it's not recognised, try better light and show your whole hand.",
        )
        for tip in tips:
            layout.addWidget(label(f"-  {tip}", "muted", wrap=True))
        return box

    def _library_card(self, pose_id: str) -> QWidget:
        pose = READY_POSES[pose_id]
        box = card()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        layout.addWidget(PoseStill(pose_id, side=64, on_click=lambda: self.add(pose_id)))
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(pose.name, "rowTitle"))
        text.addWidget(label(pose.how, "muted", wrap=True))
        layout.addLayout(text, 1)
        layout.addWidget(button("Add", lambda: self.add(pose_id)))
        return box

    def _own_pose_card(self) -> QWidget:
        box = card()
        layout = QHBoxLayout(box)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        layout.addWidget(OutlineStill([], side=64))
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label("Any other hand shape", "rowTitle"))
        text.addWidget(label("Record your own by showing it to the camera", "muted", wrap=True))
        layout.addLayout(text, 1)
        layout.addWidget(button("Add", lambda: self.add()))
        return box

    def _row_frame(self) -> tuple[QWidget, QHBoxLayout]:
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(12)
        return row, layout

    def _add_text(self, layout, name: str, how: str, summary: str, missing: list[str]) -> None:
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(name, "rowTitle"))
        text.addWidget(label(how, "muted", wrap=True))
        layout.addLayout(text, 2)
        result = QVBoxLayout()
        result.setSpacing(1)
        result.addWidget(label(summary, "accentText", wrap=True))
        if missing:
            result.addWidget(label("Won't work until you allow: " + ", ".join(missing), "warning", wrap=True))
        layout.addLayout(result, 2)

    def _builtin_row(self, key: str, info) -> QWidget:
        binding = self.state.profile.bindings[key]
        row, layout = self._row_frame()
        row.setProperty("gesture_id", key)
        toggle = Toggle(binding.enabled, "Turn this gesture on or off")
        toggle.toggled.connect(lambda on: self._set_enabled(binding, on))
        layout.addWidget(toggle)
        picture = GestureStill(key, on_click=lambda: self.show_demo(key))
        picture.setToolTip("Watch how to do this gesture")
        layout.addWidget(picture)
        self._add_text(layout, info.name, info.how, gesture_summary(self.state, key), self._missing(key, binding))
        layout.addWidget(button("Watch how", lambda: self.show_demo(key)))
        change = button("Change", lambda: self.edit(key))
        layout.addWidget(change)
        if info.kind == "pointer":
            # Nothing to change, but it keeps its space so the rows still line up.
            policy = change.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            change.setSizePolicy(policy)
            change.hide()
        return row

    def _custom_row(self, gesture) -> QWidget:
        row, layout = self._row_frame()
        row.setProperty("gesture_id", gesture.id)
        toggle = Toggle(gesture.enabled, "Turn this gesture on or off")
        toggle.toggled.connect(lambda on: self._set_enabled(gesture, on))
        layout.addWidget(toggle)
        layout.addWidget(custom_picture(gesture))
        pose = READY_POSES.get(gesture.pose_id)
        how = pose.how if pose else "Your recorded pose"
        summary = "Turned off" if not gesture.enabled else describe_steps(gesture.steps)
        self._add_text(layout, gesture.name, how, summary, self._missing(gesture.id, gesture))
        layout.addWidget(button("Change", lambda: self.edit(gesture.id)))
        layout.addWidget(button("Delete", lambda: self.delete(gesture.id), "danger"))
        return row

    def _missing(self, gesture_id: str, binding) -> list[str]:
        permissions = missing_permissions(self.state, binding.steps)
        mode_needs = "volume" if gesture_id == "volume_mode" else "mouse" if gesture_id in _MOUSE_GESTURES else None
        if mode_needs and not self.state.profile.permissions.get(mode_needs):
            permissions.append(mode_needs)
        return [PERMISSIONS[p].name for p in permissions]

    def _set_enabled(self, binding, on: bool) -> None:
        binding.enabled = on
        self.state.save()

    def add(self, pose_id: str | None = None) -> None:
        ui.run_dialog(GestureWizard(self, self.state, self.controller, pose_id=pose_id))

    def edit(self, gesture_id: str) -> None:
        if gesture_id in BUILTINS:
            ui.run_dialog(GestureEditor(self, self.state, self.controller, gesture_id))
        else:
            ui.run_dialog(GestureWizard(self, self.state, self.controller, gesture_id=gesture_id))

    def show_demo(self, gesture_id: str) -> None:
        ui.run_dialog(DemoDialog(self, self.state, gesture_id))

    def delete(self, gesture_id: str) -> None:
        name = self.state.profile.gesture_name(gesture_id)
        if not ui.confirm(self, "Delete gesture", f'Delete "{name}"? This can\'t be undone.'):
            return
        profile = self.state.profile
        profile.custom = [g for g in profile.custom if g.id != gesture_id]
        for app in profile.apps:
            app.overrides.pop(gesture_id, None)
        self.state.save()


class AppOverridesDialog(QDialog):
    """What each gesture does while one particular app is in use."""

    def __init__(self, parent, state: AppState, controller: EngineController, app: ConnectedApp) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller
        self.app = app
        self.setWindowTitle(f"Gestures in {app.name}")
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.addWidget(label(f"Gestures in {app.name}", "title"))
        layout.addWidget(label("Make a gesture do something different while you're using this app. For example, swipe to change slides in PowerPoint.", "muted", wrap=True))
        self.rows = QVBoxLayout()
        layout.addLayout(self.rows)
        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_row.addWidget(button("Done", self.accept, "primary"))
        layout.addLayout(close_row)
        self.refresh()

    def _gesture_ids(self) -> list[str]:
        builtin = [k for k, info in BUILTINS.items() if info.kind == "action"]
        return builtin + [g.id for g in self.state.profile.custom]

    def refresh(self) -> None:
        clear_layout(self.rows)
        for gesture_id in self._gesture_ids():
            row = QHBoxLayout()
            row.addWidget(label(self.state.profile.gesture_name(gesture_id)), 2)
            override = self.app.overrides.get(gesture_id)
            if override is None:
                row.addWidget(label("Same as everywhere else", "muted"), 3)
            else:
                row.addWidget(label(describe_steps(override), "accentText", wrap=True), 3)
            row.addWidget(button("Change", lambda g=gesture_id: self.change(g)))
            reset = button("Reset", lambda g=gesture_id: self.reset(g))
            reset.setEnabled(override is not None)
            row.addWidget(reset)
            self.rows.addLayout(row)

    def change(self, gesture_id: str) -> None:
        name = self.state.profile.gesture_name(gesture_id)
        binding = self.state.profile.bindings.get(gesture_id) or self.state.profile.find_custom(gesture_id)
        current = self.app.overrides.get(gesture_id, list(binding.steps) if binding else [])
        dialog = StepsDialog(self, self.state, self.controller, f"{name} in {self.app.name}", current)
        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            self.app.overrides[gesture_id] = dialog.editor.steps()
        dialog.deleteLater()
        if accepted:
            self.state.save()
            self.refresh()

    def reset(self, gesture_id: str) -> None:
        self.app.overrides.pop(gesture_id, None)
        self.state.save()
        self.refresh()


class AppsPage(Page):
    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__("Apps", "Optional. Gestures already work everywhere. Use this to switch them off in an app, or make them do something different there.")
        self.state = state
        self.controller = controller
        self.running: set[str] = set()

        self.add_running_button = button("+ Add an open app", self.show_add_menu, "primary")
        self.header.addWidget(self.add_running_button, alignment=Qt.AlignTop)
        self.header.addWidget(button("Find an app...", self.browse), alignment=Qt.AlignTop)

        scope_card = card()
        scope_layout = QVBoxLayout(scope_card)
        scope_layout.addWidget(label("Where should gestures work?", "section"))
        self.scope_all = QRadioButton("In every app, except the ones I switch off below")
        self.scope_connected = QRadioButton("Only in the apps listed below")
        group = QButtonGroup(self)
        group.addButton(self.scope_all)
        group.addButton(self.scope_connected)
        scope_layout.addWidget(self.scope_all)
        scope_layout.addWidget(self.scope_connected)
        self.scope_all.setChecked(state.profile.scope == SCOPE_ALL)
        self.scope_connected.setChecked(state.profile.scope == SCOPE_CONNECTED)
        self.scope_all.toggled.connect(self._set_scope)
        self.body.addWidget(scope_card)

        self.count_label = label("", "section")
        self.body.addWidget(self.count_label)
        self.warning = label("", "warning", wrap=True)
        self.body.addWidget(self.warning)
        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.body.addLayout(self.rows)
        self.body.addStretch(1)
        state.changed.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:
        self.running = app_watch.running_process_names()
        self.refresh()
        super().showEvent(event)

    def _set_scope(self) -> None:
        self.state.profile.scope = SCOPE_ALL if self.scope_all.isChecked() else SCOPE_CONNECTED
        self.state.save()

    def refresh(self) -> None:
        clear_layout(self.rows)
        profile = self.state.profile
        connected, running = connected_summary(profile, self.running)
        noun = "app" if connected == 1 else "apps"
        self.count_label.setText(f"{connected} {noun} connected, {running} running now")
        if profile.scope == SCOPE_CONNECTED and connected == 0:
            self.warning.setText("No apps are listed, so gestures will only work in this window.")
            self.warning.show()
        else:
            self.warning.hide()
        if not profile.apps:
            self.rows.addWidget(label("No apps yet, and that's fine. You only need this if you want different behaviour in a particular app.", "muted", wrap=True))
        for app in profile.apps:
            self.rows.addWidget(self._row(app))

    def _row(self, app: ConnectedApp) -> QWidget:
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(12)
        toggle = Toggle(app.enabled, "Turn gestures on or off for this app")
        toggle.toggled.connect(lambda on: self._set_enabled(app, on))
        layout.addWidget(toggle)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(app.name, "rowTitle"))
        details = "Running" if app.exe in self.running else "Not running"
        overrides = len(app.overrides)
        if overrides:
            details += f", {overrides} gesture{'s' if overrides != 1 else ''} changed"
        text.addWidget(label(details, "muted", wrap=True))
        layout.addLayout(text, 1)
        layout.addWidget(button("Change gestures here", lambda: self.customize(app)))
        layout.addWidget(button("Remove", lambda: self.remove(app), "danger"))
        return row

    def _set_enabled(self, app: ConnectedApp, on: bool) -> None:
        app.enabled = on
        self.state.save()

    def customize(self, app: ConnectedApp) -> None:
        ui.run_dialog(AppOverridesDialog(self, self.state, self.controller, app))

    def remove(self, app: ConnectedApp) -> None:
        if ui.confirm(self, "Remove app", f"Remove {app.name}? Any gestures changed for it go back to normal."):
            self.state.profile.apps.remove(app)
            self.state.save()

    def add_app(self, exe: str, name: str | None = None) -> bool:
        exe = exe.lower()
        if self.state.profile.find_app(exe) is not None:
            ui.notify(self, "Already added", f"{app_watch.friendly_name(exe)} is already in your list.")
            return False
        self.state.profile.apps.append(ConnectedApp(name or app_watch.friendly_name(exe), exe))
        self.state.save()
        return True

    def show_add_menu(self) -> None:
        menu = QMenu(self)
        windows = [w for w in app_watch.open_windows() if self.state.profile.find_app(w.exe) is None]
        if not windows:
            menu.addAction("No other apps are open").setEnabled(False)
        for window in windows:
            title = window.title if len(window.title) <= 40 else window.title[:37] + "..."
            menu.addAction(f"{app_watch.friendly_name(window.exe)}  ({title})", lambda w=window: self.add_app(w.exe))
        menu.exec(self.add_running_button.mapToGlobal(self.add_running_button.rect().bottomLeft()))

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app", "", "Apps (*.exe);;All files (*)")
        if path:
            self.add_app(Path(path).name)


class PermissionsPage(Page):
    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__("Permissions", "What this app is allowed to do. It asks when a gesture needs something new, so you rarely need this page.")
        self.state = state
        self.controller = controller

        privacy = card()
        privacy_layout = QHBoxLayout(privacy)
        privacy_layout.setContentsMargins(14, 10, 14, 10)
        text = QVBoxLayout()
        text.addWidget(label("Windows camera access", "rowTitle"))
        self.privacy_label = label("", "muted", wrap=True)
        text.addWidget(self.privacy_label)
        privacy_layout.addLayout(text, 1)
        privacy_layout.addWidget(button("Open Windows settings", shell_integration.open_camera_privacy_settings))
        self.body.addWidget(privacy)

        self.rows = QVBoxLayout()
        self.rows.setSpacing(8)
        self.body.addLayout(self.rows)
        self.body.addStretch(1)
        state.changed.connect(self.refresh)
        self.refresh()

    def showEvent(self, event) -> None:
        status = shell_integration.camera_privacy_status()
        descriptions = {
            "Allowed": "Windows allows desktop apps to use the camera.",
            "Blocked": "Windows is blocking the camera. Allow it in Windows settings, or the app can't see your hand.",
            "Unknown": "Couldn't read this Windows setting.",
        }
        self.privacy_label.setText(descriptions[status])
        super().showEvent(event)

    def refresh(self) -> None:
        clear_layout(self.rows)
        permissions = self.state.profile.permissions
        granted = [k for k in PERMISSIONS if permissions.get(k)]
        available = [k for k in PERMISSIONS if not permissions.get(k)]
        self.rows.addWidget(label(f"Allowed ({len(granted)})", "section"))
        for key in granted:
            self.rows.addWidget(self._row(key, True))
        self.rows.addSpacing(10)
        self.rows.addWidget(label(f"Not allowed ({len(available)})", "section"))
        if not available:
            self.rows.addWidget(label("Everything is allowed.", "muted"))
        for key in available:
            self.rows.addWidget(self._row(key, False))

    def _row(self, key: str, granted: bool) -> QWidget:
        info = PERMISSIONS[key]
        row = card()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(14, 10, 14, 10)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(info.name, "rowTitle"))
        text.addWidget(label(info.description, "muted", wrap=True))
        layout.addLayout(text, 1)
        if granted:
            layout.addWidget(button("Remove", lambda: self.remove(key), "danger"))
        else:
            layout.addWidget(button("Allow", lambda: self.add(key), "primary"))
        return row

    def _apply_startup(self, enabled: bool) -> bool:
        try:
            shell_integration.set_start_with_windows(enabled)
            return True
        except (OSError, ValueError) as exc:
            ui.show_error(self, "Start with Windows", f"Couldn't change this: {exc}")
            return False

    def add(self, key: str) -> None:
        if key == "startup" and not self._apply_startup(True):
            return
        self.state.profile.permissions[key] = True
        self.state.save()

    def remove(self, key: str) -> None:
        affected = self.state.profile.steps_using(key)
        if affected:
            names = ", ".join(affected)
            text = f"Without {PERMISSIONS[key].name}, these will stop working: {names}.\n\nRemove it anyway?"
            if not ui.confirm(self, "Remove permission", text):
                return
        if key == "startup" and not self._apply_startup(False):
            return
        self.state.profile.permissions[key] = False
        self.state.save()
        if key == "camera":
            self.controller.stop()


class SettingsPage(Page):
    SWIPE_RANGE = (10, 45)  # Hundredths of the frame width.
    # Here rather than imported from mini_view, which needs this module.
    PIN_CORNERS = (
        ("bottom_right", "Bottom right"),
        ("bottom_left", "Bottom left"),
        ("top_right", "Top right"),
        ("top_left", "Top left"),
    )

    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__("Settings")
        self.state = state
        self.controller = controller
        general = state.profile.general

        behaviour = card()
        form = QFormLayout(behaviour)
        form.setContentsMargins(16, 14, 16, 14)
        form.setVerticalSpacing(12)
        form.setHorizontalSpacing(24)
        self.auto_start = Toggle(general.start_engine_on_launch, "Turn the camera on as soon as the app opens.")
        self.auto_start.toggled.connect(lambda on: self._set("start_engine_on_launch", on))
        form.addRow("Start automatically when the app opens", self.auto_start)
        self.close_to_tray = Toggle(general.close_to_tray, "Keep running from the small icon by the clock when you close the window.")
        self.close_to_tray.toggled.connect(lambda on: self._set("close_to_tray", on))
        form.addRow("Keep running after I close the window", self.close_to_tray)
        self.show_preview = Toggle(general.show_preview, "Turning this off saves some computer power.")
        self.show_preview.toggled.connect(lambda on: self._set("show_preview", on))
        form.addRow("Show the camera picture", self.show_preview)
        self.pin_camera = Toggle(
            general.pin_camera,
            "While the app is running and minimized, or in the tray, show the camera in a corner of the screen.",
        )
        self.pin_camera.toggled.connect(lambda on: self._set("pin_camera", on))
        form.addRow("Pin the camera to a corner when minimized", self.pin_camera)
        self.pin_corner = QComboBox()
        for key, name in self.PIN_CORNERS:
            self.pin_corner.addItem(name, key)
        self.pin_corner.setCurrentIndex(max(self.pin_corner.findData(general.pin_corner), 0))
        self.pin_corner.currentIndexChanged.connect(lambda _: self._set("pin_corner", self.pin_corner.currentData()))
        form.addRow("Which corner", self.pin_corner)
        self.show_preview.toggled.connect(lambda _: self._sync_pin_controls())
        self.pin_camera.toggled.connect(lambda _: self._sync_pin_controls())
        self._sync_pin_controls()
        self.camera = QSpinBox()
        self.camera.setRange(0, 9)
        self.camera.setValue(general.camera_index)
        self.camera.setToolTip("Only change this if you have more than one camera. Applies the next time you press Start.")
        self.camera.valueChanged.connect(lambda v: self._set("camera_index", v))
        form.addRow("Which camera (if you have more than one)", self.camera)
        self.body.addWidget(behaviour)

        tuning = card()
        tuning_layout = QVBoxLayout(tuning)
        tuning_layout.setContentsMargins(16, 14, 16, 14)
        tuning_layout.addWidget(label("How far to move your hand for a swipe", "rowTitle"))
        tuning_layout.addWidget(label("Shorter is easier, but swipes may happen by accident.", "muted", wrap=True))
        slider_row = QHBoxLayout()
        self.swipe = QSlider(Qt.Horizontal)
        self.swipe.setRange(*self.SWIPE_RANGE)
        self.swipe.setValue(round(general.swipe_distance * 100))
        self.swipe_value = label(f"{self.swipe.value()}% of the picture", "muted")
        self.swipe_value.setFixedWidth(120)
        self.swipe.valueChanged.connect(self._swipe_changed)
        slider_row.addWidget(self.swipe, 1)
        slider_row.addWidget(self.swipe_value)
        tuning_layout.addLayout(slider_row)
        tuning_layout.addSpacing(6)
        tuning_layout.addWidget(label("Fit the pinch to my hand", "rowTitle"))
        calibrate_row = QHBoxLayout()
        calibrate_row.addWidget(label("Try this if volume control or clicking feels unreliable.", "muted", wrap=True), 1)
        calibrate_row.addWidget(button("Fit to my hand", self.calibrate))
        tuning_layout.addLayout(calibrate_row)
        self.body.addWidget(tuning)

        system = card()
        system_layout = QVBoxLayout(system)
        system_layout.setContentsMargins(16, 14, 16, 14)
        desktop_row = QHBoxLayout()
        text = QVBoxLayout()
        text.addWidget(label("Desktop icon", "rowTitle"))
        self.desktop_status = label("Add it again if the icon has gone missing.", "muted", wrap=True)
        text.addWidget(self.desktop_status)
        desktop_row.addLayout(text, 1)
        desktop_row.addWidget(button("Add to Desktop", self.add_to_desktop))
        system_layout.addLayout(desktop_row)
        system_layout.addSpacing(6)
        welcome_row = QHBoxLayout()
        text = QVBoxLayout()
        text.addWidget(label("Welcome guide", "rowTitle"))
        text.addWidget(label("Show the getting-started guide on Home again.", "muted", wrap=True))
        welcome_row.addLayout(text, 1)
        welcome_row.addWidget(button("Show it", self.show_welcome))
        system_layout.addLayout(welcome_row)
        system_layout.addSpacing(6)
        reset_row = QHBoxLayout()
        text = QVBoxLayout()
        text.addWidget(label("Reset the ready-to-use gestures", "rowTitle"))
        text.addWidget(label("Puts them back as they first were. Your own gestures and apps are kept.", "muted", wrap=True))
        reset_row.addLayout(text, 1)
        reset_row.addWidget(button("Reset", self.reset_gestures, "danger"))
        system_layout.addLayout(reset_row)
        system_layout.addSpacing(6)
        system_layout.addWidget(label(f"Hand Gesture Control {__version__}", "rowTitle"))
        system_layout.addWidget(label("Free and open source, under the MIT License.", "muted", wrap=True))
        self.body.addWidget(system)
        self.body.addStretch(1)
        self.on_show_welcome = lambda: None

    def _set(self, attr: str, value) -> None:
        setattr(self.state.profile.general, attr, value)
        self.state.save()

    def _sync_pin_controls(self) -> None:
        # With the camera picture off, there's no picture to pin.
        preview = self.show_preview.isChecked()
        self.pin_camera.setEnabled(preview)
        self.pin_corner.setEnabled(preview and self.pin_camera.isChecked())

    def _swipe_changed(self, value: int) -> None:
        self.swipe_value.setText(f"{value}% of the picture")
        self._set("swipe_distance", value / 100)

    def calibrate(self) -> None:
        if ui.run_dialog(CalibrationDialog(self.controller, self)) == QDialog.Accepted:
            ui.notify(self, "All done", "Saved. This applies the next time you press Start.")

    def add_to_desktop(self) -> None:
        try:
            path = shell_integration.create_shortcut("Desktop")
        except (OSError, ValueError) as exc:
            ui.show_error(self, "Desktop icon", f"Couldn't create it: {exc}")
            return
        self.desktop_status.setText(f"Created at {path}")

    def show_welcome(self) -> None:
        self._set("show_welcome", True)
        self.on_show_welcome()

    def reset_gestures(self) -> None:
        if ui.confirm(self, "Reset gestures", "Put the ready-to-use gestures back as they first were?"):
            self.state.profile.bindings = default_bindings()
            self.state.save()
