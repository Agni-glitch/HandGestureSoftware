"""The guided way to add or change your own gesture: pick a pose, pick an action, try it."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from gesture_control.custom_gestures import READY_POSES, CustomMatcher
from gesture_control.gui import widgets as ui
from gesture_control.gui.capture_dialog import RecordPoseDialog
from gesture_control.gui.editors import ActionPicker, MoreOptions, offer_permissions
from gesture_control.gui.illustrations import OutlineStill, PoseStill
from gesture_control.gui.state import AppState, EngineController
from gesture_control.gui.widgets import bgr_to_pixmap, button, label
from gesture_control.profile import BUILTINS, CustomGesture, new_custom_id
from gesture_control.steps import SYSTEM, Step

OWN_POSE = "__own__"
_RECOGNISED_FRAMES = 6
_NO_HAND_AFTER_POLLS = 10
_POLL_MS = 100

HOW_TO_ADD = (
    ("Choose a pose", "Pick one from the list, or record your own."),
    ("Choose what it does", "Play music, open a website, open an app and more."),
    ("Try it", "Show the pose to your camera to check that it works."),
)


class PoseCard(QFrame):
    """A selectable pose in the list."""

    def __init__(self, title: str, subtitle: str, picture: QWidget, on_click, disabled_reason: str = "") -> None:
        super().__init__()
        self.setObjectName("poseCard")
        self.on_click = on_click
        self.enabled = not disabled_reason
        if self.enabled:
            self.setCursor(Qt.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(10)
        layout.addWidget(picture)
        text = QVBoxLayout()
        text.setSpacing(1)
        text.addWidget(label(title, "rowTitle"))
        if disabled_reason:
            text.addWidget(label(disabled_reason, "warning", wrap=True))
        else:
            text.addWidget(label(subtitle, "muted", wrap=True))
        text.addStretch(1)
        layout.addLayout(text, 1)
        self.set_selected(False)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:
        if self.enabled and self.rect().contains(event.position().toPoint()):
            self.on_click()
        else:
            super().mouseReleaseEvent(event)


class TryItPanel(QWidget):
    """Shows the camera and says whether it recognises the chosen pose. Nothing runs meanwhile."""

    def __init__(self, controller: EngineController) -> None:
        super().__init__()
        self.controller = controller
        self.pose_id = ""
        self.matcher = CustomMatcher([])
        self.owns_engine = False
        self.active = False
        self.recognised = False
        self._seen_frames = 0
        self._polls_without_hand = 0

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        self.preview = label("Camera", "preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(240, 180)
        layout.addWidget(self.preview)
        side = QVBoxLayout()
        self.status = label("Press the button, then show your pose to the camera.", "subtitle", wrap=True)
        side.addWidget(self.status)
        self.button = button("Try it with my camera", self.toggle)
        side.addWidget(self.button, alignment=Qt.AlignLeft)
        side.addStretch(1)
        layout.addLayout(side, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self.poll)
        controller.state_changed.connect(self._on_state)
        controller.failed.connect(self._on_failed)
        controller.frame_ready.connect(self._show_frame)

    def set_target(self, pose_id: str, template: list[float] | None, threshold: float) -> None:
        self.pose_id = pose_id
        self.matcher = CustomMatcher([("target", template, threshold)] if template else [])

    def toggle(self) -> None:
        if self.active:
            self.stop()
        else:
            self.start()

    def start(self) -> None:
        self.recognised = False
        self._seen_frames = 0
        self._polls_without_hand = 0
        self.active = True
        self.button.setText("Stop camera")
        status = self.controller.status
        if status == EngineController.RUNNING:
            self._begin_sampling()
        elif status == EngineController.STOPPED:
            self.owns_engine = True
            self.status.setText("Starting the camera. This takes a few seconds...")
            self.controller.start(silent=True)
        else:
            self.stop()
            self.status.setText("The camera is busy. Try again in a moment.")

    def _begin_sampling(self) -> None:
        from gesture_control.engine import Sampler

        if self.controller.engine is not None:
            self.controller.engine.sampler = Sampler()
        self.status.setText("Show your pose to the camera and hold it...")
        self._timer.start()

    def _on_state(self, status: str) -> None:
        if status == EngineController.RUNNING and self.active and not self._timer.isActive():
            self._begin_sampling()

    def _on_failed(self, error: str) -> None:
        if self.active:
            self.stop()
            self.status.setText(error)

    def poll(self) -> None:
        engine = self.controller.engine
        sampler = engine.sampler if engine is not None else None
        if sampler is None:
            return
        count = len(sampler.ready_poses)
        if count == self._seen_frames:
            self._polls_without_hand += 1
            if self._polls_without_hand >= _NO_HAND_AFTER_POLLS:
                self.status.setText("I can't see your hand. Move it into view, in good light.")
            return
        self._seen_frames = count
        self._polls_without_hand = 0
        if count < _RECOGNISED_FRAMES:
            return
        if self._last_frames_match(sampler):
            self.recognised = True
            self.status.setObjectName("accentText")
            self.status.setText("Recognised! This pose works.")
        elif not self.recognised:
            self.status.setText("I can see your hand, but not this pose yet. Check the picture and hold it steady.")
        self.status.style().polish(self.status)

    def _last_frames_match(self, sampler) -> bool:
        if self.pose_id and self.pose_id != OWN_POSE:
            return all(pose == self.pose_id for pose in sampler.ready_poses[-_RECOGNISED_FRAMES:])
        return all(self.matcher.match(f) == "target" for f in sampler.features[-_RECOGNISED_FRAMES:])

    def _show_frame(self, image) -> None:
        if self.active:
            self.preview.setPixmap(
                bgr_to_pixmap(image).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

    def stop(self) -> None:
        self._timer.stop()
        self.active = False
        self.button.setText("Try it with my camera")
        self.preview.clear()
        self.preview.setText("Camera")
        if self.controller.engine is not None:
            self.controller.engine.sampler = None
        if self.owns_engine:
            self.owns_engine = False
            self.controller.stop()

    def disconnect_all(self) -> None:
        self.controller.state_changed.disconnect(self._on_state)
        self.controller.failed.disconnect(self._on_failed)
        self.controller.frame_ready.disconnect(self._show_frame)


class GestureWizard(QDialog):
    """Adds a gesture, or changes an existing one, in three steps."""

    def __init__(
        self,
        parent,
        state: AppState,
        controller: EngineController,
        gesture_id: str | None = None,
        pose_id: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.state = state
        self.controller = controller
        self.gesture = state.profile.find_custom(gesture_id) if gesture_id else None
        self.gesture_id = gesture_id
        self.pose_id = ""
        self.template = list(self.gesture.template) if self.gesture else None
        self.threshold = self.gesture.threshold if self.gesture else 0.0
        self.outline = list(self.gesture.outline) if self.gesture else []
        self.setWindowTitle("Change gesture" if self.gesture else "Add a gesture")
        self.setMinimumWidth(680)

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        self.step_label = label("", "muted")
        layout.addWidget(self.step_label)
        self.title = label("", "title")
        layout.addWidget(self.title)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self.pages.addWidget(self._build_pose_page())
        self.pages.addWidget(self._build_action_page())
        self.pages.addWidget(self._build_finish_page())

        self.error = label("", "error", wrap=True)
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addWidget(button("Cancel", self.reject))
        buttons.addStretch(1)
        self.back_button = button("Back", self.go_back)
        buttons.addWidget(self.back_button)
        self.next_button = button("Next", self.go_next, "primary")
        buttons.addWidget(self.next_button)
        layout.addLayout(buttons)

        # Pre-select from what we were given, and skip ahead if the pose is already decided.
        if self.gesture is not None:
            self.select_pose(self.gesture.pose_id or OWN_POSE, record_if_own=False)
        elif pose_id:
            self.select_pose(pose_id, record_if_own=False)
        self.show_page(1 if pose_id else 0)

    # Page 1: the pose

    def _build_pose_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Pick the hand shape you'd like to use. These work straight away, with no recording.", "muted", wrap=True))
        grid = QGridLayout()
        grid.setSpacing(8)
        self.pose_cards: dict[str, PoseCard] = {}
        for position, (key, pose) in enumerate(READY_POSES.items()):
            used_by = self.state.profile.pose_taken_by(key, self.gesture_id)
            reason = f'Used by your "{used_by}" gesture' if used_by else ""
            card = PoseCard(pose.name, pose.how, PoseStill(key, side=60), lambda k=key: self.select_pose(k), reason)
            self.pose_cards[key] = card
            grid.addWidget(card, *divmod(position, 2))
        layout.addLayout(grid)
        layout.addSpacing(4)
        layout.addWidget(label("Or use any other hand shape:", "muted"))
        self.own_picture = OutlineStill(self.outline, side=60)
        own = PoseCard("Record my own pose", "Show the camera any hand shape for 3 seconds.", self.own_picture, lambda: self.select_pose(OWN_POSE))
        self.pose_cards[OWN_POSE] = own
        layout.addWidget(own)
        layout.addStretch(1)
        return page

    def make_record_dialog(self) -> RecordPoseDialog:
        return RecordPoseDialog(self.controller, self, editing_id=self.gesture_id)

    def select_pose(self, pose_id: str, record_if_own: bool = True) -> None:
        if pose_id == OWN_POSE and record_if_own:
            dialog = self.make_record_dialog()
            if ui.run_dialog(dialog) != QDialog.Accepted:
                return
            self.template, self.threshold, self.outline = dialog.template, dialog.threshold, dialog.outline
            self.own_picture.outline = self.outline
            self.own_picture.update()
        self.pose_id = pose_id
        for key, card in self.pose_cards.items():
            card.set_selected(key == pose_id)
        self.error.setText("")

    # Page 2: the action

    def _build_action_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(label("Choose from the list. You can change this later.", "muted", wrap=True))
        self.picker = ActionPicker(list(self.gesture.steps) if self.gesture else [Step(SYSTEM, "play_pause")])
        layout.addWidget(self.picker)
        layout.addStretch(1)
        return page

    # Page 3: name and try it

    def _build_finish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(label("Name", "section"))
        self.name_edit = QLineEdit(placeholderText="For example: Thumbs up")
        layout.addWidget(self.name_edit)
        layout.addWidget(label("Try it (optional)", "section"))
        self.try_panel = TryItPanel(self.controller)
        layout.addWidget(self.try_panel)
        self.options = MoreOptions(
            self.gesture.hold_frames if self.gesture else 8,
            self.gesture.cooldown_s if self.gesture else 1.0,
        )
        layout.addWidget(self.options)
        layout.addStretch(1)
        return page

    # Moving between pages

    def show_page(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        self.error.setText("")
        titles = ("Choose a hand pose", "What should it do?", "Name it and try it")
        self.step_label.setText(f"Step {index + 1} of 3")
        self.title.setText(titles[index])
        self.back_button.setVisible(index > 0)
        self.next_button.setText("Save" if index == 2 else "Next")
        if index != 2:
            self.try_panel.stop()
        else:
            self.try_panel.set_target(self.pose_id, self.template, self.threshold)
            if not self.name_edit.text():
                self.name_edit.setText(self._suggested_name())

    def _suggested_name(self) -> str:
        if self.gesture is not None:
            return self.gesture.name
        if self.pose_id in READY_POSES:
            return READY_POSES[self.pose_id].name
        taken = {g.name.lower() for g in self.state.profile.custom}
        number = 1
        while f"my gesture {number}" in taken:
            number += 1
        return f"My gesture {number}"

    def go_back(self) -> None:
        self.show_page(self.pages.currentIndex() - 1)

    def go_next(self) -> None:
        index = self.pages.currentIndex()
        if index == 0:
            if not self.pose_id:
                self.error.setText("Pick a pose first.")
                return
            if self.pose_id == OWN_POSE and not self.template:
                self.error.setText("Record your pose first.")
                return
        elif index == 1:
            problem = self.picker.first_error()
            if problem:
                self.error.setText(problem)
                return
        if index < 2:
            self.show_page(index + 1)
        else:
            self.save()

    def save(self) -> None:
        name = self.name_edit.text().strip()
        if not name:
            self.error.setText("Give this gesture a name.")
            return
        duplicate = any(g.name.lower() == name.lower() and g.id != self.gesture_id for g in self.state.profile.custom)
        if duplicate or name.lower() in {b.name.lower() for b in BUILTINS.values()}:
            self.error.setText(f'You already have a gesture called "{name}".')
            return
        steps = self.picker.steps()
        offer_permissions(self, self.state, steps)
        target = self.gesture
        if target is None:
            target = CustomGesture(new_custom_id(), name, [], 0.0)
            self.state.profile.custom.append(target)
        target.name = name
        target.steps = steps
        target.hold_frames = self.options.hold_slider.value()
        target.cooldown_s = self.options.cooldown_spin.value()
        if self.pose_id == OWN_POSE:
            target.pose_id = ""
            target.template, target.threshold, target.outline = self.template, self.threshold, self.outline
        else:
            target.pose_id = self.pose_id
            target.template, target.threshold, target.outline = [], 0.0, []
        self.state.save()
        self.accept()

    def done(self, result: int) -> None:
        self.try_panel.stop()
        self.try_panel.disconnect_all()
        super().done(result)
