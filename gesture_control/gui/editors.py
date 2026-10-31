"""Choosing what a gesture does, in plain language, and editing built-in gestures."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gesture_control import app_watch
from gesture_control.gui import widgets as ui
from gesture_control.gui.illustrations import GUIDE_STEPS, GestureDemo
from gesture_control.gui.state import AppState, EngineController
from gesture_control.gui.widgets import button, label
from gesture_control.profile import BUILTINS, PERMISSIONS
from gesture_control.steps import (
    APP,
    HOTKEY,
    MAX_WAIT_MS,
    STEP_TYPES,
    SYSTEM,
    SYSTEM_ACTIONS,
    TEXT,
    URL,
    WAIT,
    Step,
    StepRunner,
    app_name,
    required_permissions,
    validate_step,
)

_ASSUMED_FPS = 30
_NOTHING = "nothing"

# The menu of things a gesture can do, as (group, entries).
_ACTION_GROUPS = (
    ("Music and video", ("play_pause", "mute", "media_next", "media_previous", "volume_up", "volume_down")),
    ("Windows", ("show_desktop", "switch_app", "desktop_next", "desktop_previous", "close_window", "screenshot")),
    ("Browser tabs", ("tab_next", "tab_previous")),
    ("Text", ("copy", "paste")),
)
_VALUE_HINTS = {
    URL: "Copy the address from the top of your browser and paste it here.",
    APP: "Choose from your installed apps, or find one yourself.",
    TEXT: "This is typed wherever your cursor is when you do the gesture.",
    HOTKEY: "Type the keys joined by +. For example: ctrl+shift+s, win+d or alt+tab.",
}
_CHOOSE_ANOTHER_APP = "__browse__"


def missing_permissions(state: AppState, steps: list[Step]) -> list[str]:
    return sorted(p for p in required_permissions(steps) if not state.profile.permissions.get(p))


def offer_permissions(parent, state: AppState, steps: list[Step]) -> None:
    """If steps need permissions that are off, offer to add them. Saving works either way."""
    missing = missing_permissions(state, steps)
    if not missing:
        return
    names = ", ".join(PERMISSIONS[p].name for p in missing)
    if ui.confirm(parent, "Allow this?", f"This gesture needs your permission for: {names}.\n\nAllow it? If not, the gesture won't work."):
        for permission in missing:
            state.profile.permissions[permission] = True


class StepRow(QWidget):
    """One step: what kind it is, and its value."""

    def __init__(self, step: Step, on_move, on_remove) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.type_combo = QComboBox()
        for key, (name, _permission) in STEP_TYPES.items():
            self.type_combo.addItem(name, key)
        self.type_combo.setFixedWidth(140)
        layout.addWidget(self.type_combo)

        self.system_combo = QComboBox()
        for key, name in SYSTEM_ACTIONS.items():
            self.system_combo.addItem(name, key)
        self.hotkey_edit = QLineEdit(placeholderText="For example: ctrl+shift+s")
        self.app_edit = QLineEdit(placeholderText="Path to the app")
        app_box = QWidget()
        app_layout = QHBoxLayout(app_box)
        app_layout.setContentsMargins(0, 0, 0, 0)
        app_layout.addWidget(self.app_edit, 1)
        app_layout.addWidget(button("Browse", self._browse))
        self.url_edit = QLineEdit(placeholderText="https://example.com")
        self.text_edit = QLineEdit(placeholderText="Text to type")
        self.wait_spin = QSpinBox()
        self.wait_spin.setRange(50, MAX_WAIT_MS)
        self.wait_spin.setSingleStep(100)
        self.wait_spin.setSuffix(" ms")
        self.wait_spin.setValue(500)

        self.stack = QStackedWidget()
        self._pages = {
            SYSTEM: self.system_combo,
            HOTKEY: self.hotkey_edit,
            APP: app_box,
            URL: self.url_edit,
            TEXT: self.text_edit,
            WAIT: self.wait_spin,
        }
        for page in self._pages.values():
            self.stack.addWidget(page)
        layout.addWidget(self.stack, 1)

        layout.addWidget(button("Up", lambda: on_move(self, -1)))
        layout.addWidget(button("Down", lambda: on_move(self, 1)))
        layout.addWidget(button("Remove", lambda: on_remove(self), "danger"))

        self.type_combo.currentIndexChanged.connect(self._show_page)
        self.set_step(step)

    def _show_page(self) -> None:
        self.stack.setCurrentWidget(self._pages[self.type_combo.currentData()])

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app", "", "Apps (*.exe *.lnk *.bat *.cmd);;All files (*)")
        if path:
            self.app_edit.setText(path)

    def set_step(self, step: Step) -> None:
        self.type_combo.setCurrentIndex(max(self.type_combo.findData(step.type), 0))
        self._show_page()
        if step.type == SYSTEM:
            self.system_combo.setCurrentIndex(max(self.system_combo.findData(step.value), 0))
        elif step.type == HOTKEY:
            self.hotkey_edit.setText(step.value)
        elif step.type == APP:
            self.app_edit.setText(step.value)
        elif step.type == URL:
            self.url_edit.setText(step.value)
        elif step.type == TEXT:
            self.text_edit.setText(step.value)
        elif step.type == WAIT and step.value.isdigit():
            self.wait_spin.setValue(int(step.value))

    def step(self) -> Step:
        kind = self.type_combo.currentData()
        value = {
            SYSTEM: lambda: self.system_combo.currentData(),
            HOTKEY: lambda: self.hotkey_edit.text().strip(),
            APP: lambda: self.app_edit.text().strip(),
            URL: lambda: self.url_edit.text().strip(),
            TEXT: lambda: self.text_edit.text(),
            WAIT: lambda: str(self.wait_spin.value()),
        }[kind]()
        return Step(kind, value)


class StepEditor(QWidget):
    """An ordered list of steps that can be added to, reordered and removed from."""

    def __init__(self, steps: list[Step]) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        self._rows_layout = QVBoxLayout()
        self._rows_layout.setSpacing(6)
        outer.addLayout(self._rows_layout)
        add_row = QHBoxLayout()
        add_row.addWidget(button("+ Add another step", self.add_step))
        add_row.addStretch(1)
        outer.addLayout(add_row)
        self.rows: list[StepRow] = []
        for step in steps:
            self.add_step(step)

    def add_step(self, step: Step | None = None) -> None:
        row = StepRow(step or Step(SYSTEM, "play_pause"), self._move, self._remove)
        self.rows.append(row)
        self._rows_layout.addWidget(row)

    def _move(self, row: StepRow, delta: int) -> None:
        index = self.rows.index(row)
        target = index + delta
        if 0 <= target < len(self.rows):
            self.rows.insert(target, self.rows.pop(index))
            self._rows_layout.removeWidget(row)
            self._rows_layout.insertWidget(target, row)

    def _remove(self, row: StepRow) -> None:
        self.rows.remove(row)
        self._rows_layout.removeWidget(row)
        row.deleteLater()

    def steps(self) -> list[Step]:
        return [row.step() for row in self.rows]

    def first_error(self) -> str | None:
        for number, step in enumerate(self.steps(), start=1):
            problem = validate_step(step)
            if problem:
                return f"Step {number}: {problem}"
        return None


class ActionPicker(QWidget):
    """Chooses what a gesture does from a plain menu, with a step list for anyone who wants one."""

    def __init__(self, steps: list[Step], allow_nothing: bool = False) -> None:
        super().__init__()
        self._apps_loaded = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        self.simple = QWidget()
        simple_layout = QVBoxLayout(self.simple)
        simple_layout.setContentsMargins(0, 0, 0, 0)
        simple_layout.setSpacing(6)

        self.combo = QComboBox()
        self.combo.setMaxVisibleItems(24)
        if allow_nothing:
            self.combo.addItem("Do nothing", _NOTHING)
        for group, actions in _ACTION_GROUPS:
            self._add_header(group)
            for action in actions:
                self.combo.addItem(SYSTEM_ACTIONS[action], f"{SYSTEM}:{action}")
        self._add_header("Something else")
        self.combo.addItem("Open a website", URL)
        self.combo.addItem("Open an app", APP)
        self.combo.addItem("Type some text", TEXT)
        self.combo.addItem("Press a keyboard shortcut", HOTKEY)
        simple_layout.addWidget(self.combo)

        self.url_edit = QLineEdit(placeholderText="https://www.youtube.com")
        self.text_edit = QLineEdit(placeholderText="On my way!")
        self.hotkey_edit = QLineEdit(placeholderText="ctrl+shift+s")
        self.app_combo = QComboBox()
        self.app_combo.setMaxVisibleItems(20)
        self.app_combo.activated.connect(self._on_app_chosen)
        self.value_stack = QStackedWidget()
        self._value_pages = {URL: self.url_edit, APP: self.app_combo, TEXT: self.text_edit, HOTKEY: self.hotkey_edit}
        for page in self._value_pages.values():
            self.value_stack.addWidget(page)
        simple_layout.addWidget(self.value_stack)
        self.hint = label("", "muted", wrap=True)
        simple_layout.addWidget(self.hint)
        outer.addWidget(self.simple)

        self.advanced = StepEditor([])
        outer.addWidget(self.advanced)

        toggle_row = QHBoxLayout()
        self.toggle_button = button("Do several things in a row", self._toggle_advanced)
        toggle_row.addWidget(self.toggle_button)
        toggle_row.addStretch(1)
        outer.addLayout(toggle_row)

        self.combo.currentIndexChanged.connect(self._sync_value)
        self.is_advanced = False
        self.set_steps(steps)

    def _add_header(self, text: str) -> None:
        self.combo.addItem(text.upper(), None)
        item = self.combo.model().item(self.combo.count() - 1)
        item.setEnabled(False)

    def _load_apps(self) -> None:
        if self._apps_loaded:
            return
        self._apps_loaded = True
        current = self.app_combo.currentData()
        self.app_combo.clear()
        self.app_combo.addItem("Choose an app", "")
        for name, path in app_watch.start_menu_apps().items():
            self.app_combo.addItem(name, path)
        self.app_combo.addItem("Find another app...", _CHOOSE_ANOTHER_APP)
        if current:
            self._select_app(current)

    def _select_app(self, path: str) -> None:
        index = self.app_combo.findData(path)
        if index < 0:
            self.app_combo.insertItem(1, app_name(path), path)
            index = 1
        self.app_combo.setCurrentIndex(index)

    def _on_app_chosen(self) -> None:
        if self.app_combo.currentData() != _CHOOSE_ANOTHER_APP:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Choose an app", "", "Apps (*.exe *.lnk *.bat *.cmd);;All files (*)")
        if path:
            self._select_app(path)
        else:
            self.app_combo.setCurrentIndex(0)

    def _sync_value(self) -> None:
        kind = self.combo.currentData()
        page = self._value_pages.get(kind)
        self.value_stack.setVisible(page is not None)
        self.hint.setVisible(page is not None)
        if page is not None:
            if kind == APP:
                self._load_apps()
            self.value_stack.setCurrentWidget(page)
            self.hint.setText(_VALUE_HINTS[kind])

    def _show_mode(self, advanced: bool) -> None:
        self.is_advanced = advanced
        self.simple.setVisible(not advanced)
        self.advanced.setVisible(advanced)
        self.toggle_button.setText("Back to one simple action" if advanced else "Do several things in a row")

    def _toggle_advanced(self) -> None:
        if self.is_advanced:
            steps = self.advanced.steps()
            if len(steps) > 1 and not ui.confirm(self, "Keep only the first step?", "Going back to one action keeps only the first step."):
                return
            self.set_steps(steps[:1], force_simple=True)
        else:
            current = self.steps()
            for row in list(self.advanced.rows):
                self.advanced._remove(row)
            for step in current or [Step(SYSTEM, "play_pause")]:
                self.advanced.add_step(step)
            self._show_mode(True)

    def set_steps(self, steps: list[Step], force_simple: bool = False) -> None:
        simple_types = (SYSTEM, URL, APP, TEXT, HOTKEY)
        if len(steps) > 1 or (steps and steps[0].type not in simple_types):
            if not force_simple:
                for row in list(self.advanced.rows):
                    self.advanced._remove(row)
                for step in steps:
                    self.advanced.add_step(step)
                self._show_mode(True)
                return
            steps = [s for s in steps if s.type in simple_types][:1]
        self._show_mode(False)
        # The first real choice: the list also contains headings, which can't be selected.
        first_action = self.combo.findData(f"{SYSTEM}:play_pause")
        if not steps:
            nothing = self.combo.findData(_NOTHING)
            self.combo.setCurrentIndex(nothing if nothing >= 0 else first_action)
        else:
            step = steps[0]
            key = f"{SYSTEM}:{step.value}" if step.type == SYSTEM else step.type
            found = self.combo.findData(key)
            self.combo.setCurrentIndex(found if found >= 0 else first_action)
            if step.type == URL:
                self.url_edit.setText(step.value)
            elif step.type == TEXT:
                self.text_edit.setText(step.value)
            elif step.type == HOTKEY:
                self.hotkey_edit.setText(step.value)
            elif step.type == APP:
                self._load_apps()
                self._select_app(step.value)
        self._sync_value()

    def steps(self) -> list[Step]:
        if self.is_advanced:
            return self.advanced.steps()
        kind = self.combo.currentData()
        if kind == _NOTHING or kind is None:
            return []
        if kind.startswith(f"{SYSTEM}:"):
            return [Step(SYSTEM, kind.split(":", 1)[1])]
        if kind == URL:
            return [Step(URL, self.url_edit.text().strip())]
        if kind == TEXT:
            return [Step(TEXT, self.text_edit.text())]
        if kind == HOTKEY:
            return [Step(HOTKEY, self.hotkey_edit.text().strip())]
        return [Step(APP, self.app_combo.currentData() or "")]

    def first_error(self) -> str | None:
        if self.is_advanced:
            return self.advanced.first_error()
        for step in self.steps():
            problem = validate_step(step)
            if problem:
                return problem
        return None


class StepTester:
    """Runs steps for real after a short delay, so you can switch to the target app first."""

    DELAY_MS = 3000

    def __init__(self, parent, state: AppState, controller: EngineController) -> None:
        self.state = state
        self.controller = controller
        self._timer = QTimer(parent)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fire)
        self._steps: list[Step] = []
        self._runner: StepRunner | None = None
        self.on_result = lambda text: None

    def schedule(self, steps: list[Step]) -> None:
        self._steps = steps
        self._timer.start(self.DELAY_MS)

    def _fire(self) -> None:
        from gesture_control.os_control import DryRunController, SystemController

        practice = self.state.profile.general.practice_mode
        os_ctl = DryRunController() if practice else SystemController()
        self._runner = StepRunner(os_ctl, lambda p: self.state.profile.permissions.get(p, False))
        reason = self._runner.run(self._steps)
        if reason:
            self.on_result(f"Blocked: {reason}")
        else:
            self.on_result("Practice mode: logged only." if practice else "Done.")


class StepsDialog(QDialog):
    """Chooses what a gesture does within one app."""

    def __init__(self, parent, state: AppState, controller: EngineController, title: str, steps: list[Step]) -> None:
        super().__init__(parent)
        self.state = state
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.addWidget(label(title, "section", wrap=True))
        self.editor = ActionPicker(steps, allow_nothing=True)
        layout.addWidget(self.editor)
        self.error = label("", "error", wrap=True)
        layout.addWidget(self.error)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button("Cancel", self.reject))
        buttons.addWidget(button("Save", self.save, "primary"))
        layout.addLayout(buttons)

    def save(self) -> None:
        problem = self.editor.first_error()
        if problem:
            self.error.setText(problem)
            return
        offer_permissions(self, self.state, self.editor.steps())
        self.accept()


class MoreOptions(QWidget):
    """Timing settings, tucked behind a button because the defaults suit most people."""

    def __init__(self, hold_frames: int, cooldown_s: float, show_hold: bool = True, show_cooldown: bool = True) -> None:
        super().__init__()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        toggle_row = QHBoxLayout()
        self.toggle_button = button("More options", self._toggle)
        toggle_row.addWidget(self.toggle_button)
        toggle_row.addStretch(1)
        outer.addLayout(toggle_row)

        self.panel = QWidget()
        form = QFormLayout(self.panel)
        form.setContentsMargins(0, 4, 0, 0)
        form.setHorizontalSpacing(16)
        self.hold_slider = QSlider(Qt.Horizontal)
        self.hold_slider.setRange(2, 30)
        self.hold_slider.setValue(hold_frames)
        self.hold_value = label("", "muted")
        self.hold_value.setFixedWidth(60)
        self.hold_slider.valueChanged.connect(self._sync_hold)
        hold_row = QHBoxLayout()
        hold_row.addWidget(self.hold_slider, 1)
        hold_row.addWidget(self.hold_value)
        if show_hold:
            form.addRow("Hold the pose for", hold_row)
        self.cooldown_spin = QDoubleSpinBox()
        self.cooldown_spin.setRange(0.2, 30.0)
        self.cooldown_spin.setSingleStep(0.1)
        self.cooldown_spin.setSuffix(" s")
        self.cooldown_spin.setValue(cooldown_s)
        if show_cooldown:
            form.addRow("Wait before it can run again", self.cooldown_spin)
        outer.addWidget(self.panel)
        self.panel.hide()
        self._sync_hold()
        self.setVisible(show_hold or show_cooldown)

    def _toggle(self) -> None:
        showing = not self.panel.isVisibleTo(self)
        self.panel.setVisible(showing)
        self.toggle_button.setText("Fewer options" if showing else "More options")

    def _sync_hold(self) -> None:
        self.hold_value.setText(f"{self.hold_slider.value() / _ASSUMED_FPS:.1f} s")


class GestureEditor(QDialog):
    """Changes what a built-in gesture does."""

    def __init__(self, parent, state: AppState, controller: EngineController, gesture_id: str) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller
        self.gesture_id = gesture_id
        self.binding = state.profile.bindings[gesture_id]
        info = BUILTINS[gesture_id]
        self.is_mode = info.kind != "action"
        self.setWindowTitle(info.name)
        self.setMinimumWidth(620)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        heading = QHBoxLayout()
        heading.setSpacing(16)
        heading.addWidget(GestureDemo(gesture_id, side=170), 0)
        text = QVBoxLayout()
        text.addWidget(label(info.name, "title"))
        text.addWidget(label("\n".join(f"{i}.  {step}" for i, step in enumerate(GUIDE_STEPS[gesture_id], 1)), "muted", wrap=True))
        text.addStretch(1)
        heading.addLayout(text, 1)
        layout.addLayout(heading)

        self.editor = None
        if self.is_mode:
            layout.addWidget(label("This gesture has a fixed job, so there's nothing to choose here.", "muted", wrap=True))
        else:
            layout.addWidget(label("What should this gesture do?", "section"))
            self.editor = ActionPicker(list(self.binding.steps))
            layout.addWidget(self.editor)

        has_hold = not gesture_id.startswith("swipe") and info.kind != "pointer"
        self.options = MoreOptions(self.binding.hold_frames, self.binding.cooldown_s, has_hold, not self.is_mode)
        self.hold_slider = self.options.hold_slider
        self.cooldown_spin = self.options.cooldown_spin
        layout.addWidget(self.options)

        self.error = label("", "error", wrap=True)
        layout.addWidget(self.error)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        self.tester = StepTester(self, state, controller)
        self.tester.on_result = self.error.setText
        if self.editor is not None:
            buttons.addWidget(button("Try this action", self._try_steps))
        buttons.addStretch(1)
        buttons.addWidget(button("Cancel", self.reject))
        buttons.addWidget(button("Save", self.save, "primary"))
        layout.addLayout(buttons)

    def _try_steps(self) -> None:
        problem = self.editor.first_error()
        if problem:
            self.error.setText(problem)
            return
        self.error.setText("Running in 3 seconds. Switch to the app you want to test in.")
        self.tester.schedule(self.editor.steps())

    def save(self) -> None:
        if self.editor is not None:
            problem = self.editor.first_error()
            if problem:
                self.error.setText(problem)
                return
            offer_permissions(self, self.state, self.editor.steps())
            self.binding.steps = self.editor.steps()
        self.binding.hold_frames = self.hold_slider.value()
        self.binding.cooldown_s = self.cooldown_spin.value()
        self.state.save()
        self.accept()
