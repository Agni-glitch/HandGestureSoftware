"""Watches your hand for a few seconds: used to record custom poses and calibrate the pinch."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QHBoxLayout, QProgressBar, QVBoxLayout

from gesture_control.calibrate import compute_calibration
from gesture_control.custom_gestures import RecordingError, build_outline, build_template
from gesture_control.gui.state import EngineController
from gesture_control.gui.widgets import bgr_to_pixmap, button, label

RECORDING_TIPS = (
    "Tips: keep your whole hand in view and hold still. Use the hand you'll normally use. "
    "Choose a pose that looks clearly different from your other gestures."
)


class CaptureDialog(QDialog):
    PREPARE_S = 2.0
    MEASURE_S = 3.0
    _TICK_MS = 50

    def __init__(self, controller: EngineController, parent, title: str, phases: list[str]) -> None:
        super().__init__(parent)
        self.controller = controller
        self.phases = phases
        self.samplers = []
        self.owns_engine = False
        self._phase = -1
        self._elapsed = 0.0
        self._measuring = False
        self.setWindowTitle(title)
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        self.instruction = label(phases[0], "section", wrap=True)
        layout.addWidget(self.instruction)
        self.preview = label("Camera preview appears here", "preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setFixedSize(480, 360)
        layout.addWidget(self.preview, alignment=Qt.AlignCenter)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.message = label("Press Start when you're ready.", "subtitle", wrap=True)
        layout.addWidget(self.message)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(button("Cancel", self.reject))
        self.start_button = button("Start", self.begin, "primary")
        buttons.addWidget(self.start_button)
        layout.addLayout(buttons)

        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_MS)
        self._timer.timeout.connect(lambda: self.advance(self._TICK_MS / 1000))
        controller.frame_ready.connect(self._show_frame)
        controller.failed.connect(self._on_failed)
        controller.state_changed.connect(self._on_state)

    def begin(self) -> None:
        self.samplers = []
        self.start_button.setEnabled(False)
        status = self.controller.status
        if status == EngineController.RUNNING:
            self._begin_phase(0)
        elif status == EngineController.STOPPED:
            self.owns_engine = True
            self.message.setText("Starting the camera...")
            self.controller.start(silent=True)
        else:
            self.start_button.setEnabled(True)
            self.message.setText("The camera is busy. Try again in a moment.")

    def _on_state(self, status: str) -> None:
        if status == EngineController.RUNNING and self._phase < 0 and not self.start_button.isEnabled():
            self._begin_phase(0)

    def _on_failed(self, error: str) -> None:
        self._stop_timer()
        self.message.setText(error)
        self.message.setObjectName("error")
        self.message.style().polish(self.message)
        self.start_button.setEnabled(True)
        self._phase = -1

    def _begin_phase(self, index: int) -> None:
        self._phase = index
        self._elapsed = 0.0
        self._measuring = False
        self.progress.setValue(0)
        self.instruction.setText(self.phases[index])
        self._timer.start()

    def advance(self, seconds: float) -> None:
        """Move the countdown forward. The timer calls this; tests can call it directly."""
        self._elapsed += seconds
        if not self._measuring:
            self.message.setText(f"Get ready... {max(self.PREPARE_S - self._elapsed, 0):.1f}s")
            if self._elapsed >= self.PREPARE_S:
                self._measuring = True
                self._elapsed = 0.0
                self._start_sampling()
            return
        self.message.setText("Hold it steady...")
        self.progress.setValue(int(min(self._elapsed / self.MEASURE_S, 1.0) * 100))
        if self._elapsed >= self.MEASURE_S:
            self._finish_phase()

    def _start_sampling(self) -> None:
        from gesture_control.engine import Sampler

        if self.controller.engine is not None:
            self.controller.engine.sampler = Sampler()

    def _finish_phase(self) -> None:
        engine = self.controller.engine
        if engine is None:
            return
        self.samplers.append(engine.sampler)
        engine.sampler = None
        if self._phase + 1 < len(self.phases):
            self._begin_phase(self._phase + 1)
            return
        self._stop_timer()
        self._phase = -1
        try:
            self.process_samples(self.samplers)
        except (RecordingError, ValueError) as exc:
            self.message.setText(str(exc))
            self.message.setObjectName("error")
            self.message.style().polish(self.message)
            self.start_button.setText("Try again")
            self.start_button.setEnabled(True)
            return
        self.accept()

    def process_samples(self, samplers: list) -> None:
        raise NotImplementedError

    def _stop_timer(self) -> None:
        self._timer.stop()

    def _show_frame(self, image) -> None:
        self.preview.setPixmap(
            bgr_to_pixmap(image).scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def done(self, result: int) -> None:
        self._stop_timer()
        for signal, slot in (
            (self.controller.frame_ready, self._show_frame),
            (self.controller.failed, self._on_failed),
            (self.controller.state_changed, self._on_state),
        ):
            signal.disconnect(slot)
        if self.controller.engine is not None:
            self.controller.engine.sampler = None
        if self.owns_engine:
            self.controller.stop()
        super().done(result)


class RecordPoseDialog(CaptureDialog):
    """On success, template and threshold hold the recorded pose."""

    def __init__(self, controller, parent, editing_id: str | None = None) -> None:
        super().__init__(
            controller,
            parent,
            "Record a custom gesture",
            ["Show the pose with the hand you'll use, and hold it steady"],
        )
        self.editing_id = editing_id
        self.template: list[float] | None = None
        self.threshold = 0.0
        self.outline: list[list[float]] = []

        self.layout().insertWidget(1, label(RECORDING_TIPS, "muted", wrap=True))

    def process_samples(self, samplers: list) -> None:
        import numpy as np

        profile = self.controller.state.profile
        sampler = samplers[0]
        shown = [pose for pose in sampler.ready_poses if pose]
        if len(shown) > len(sampler.ready_poses) / 2:
            used_by = profile.pose_taken_by(max(set(shown), key=shown.count), self.editing_id)
            if used_by is not None:
                raise RecordingError(f'That pose is already used by your "{used_by}" gesture. Try a different pose.')
        others = [
            (g.name, np.array(g.template), g.threshold)
            for g in profile.custom
            if g.id != self.editing_id and len(g.template) > 0
        ]
        self.template, self.threshold = build_template(
            sampler.features, sampler.labels, profile.builtin_enabled, others
        )
        self.outline = build_outline(sampler.landmarks)


class CalibrationDialog(CaptureDialog):
    def __init__(self, controller, parent) -> None:
        super().__init__(
            controller,
            parent,
            "Calibrate your pinch",
            [
                "Touch your thumb and index fingertips together, other fingers up",
                "Now spread your thumb and index finger as wide as is comfortable",
            ],
        )

    def process_samples(self, samplers: list) -> None:
        touch, spread = samplers
        if len(touch.pinch_ratios) < 20 or len(spread.pinch_ratios) < 20:
            raise ValueError("Your hand wasn't visible for long enough. Keep it in view and try again.")
        values = compute_calibration(touch.pinch_ratios, spread.pinch_ratios)
        self.controller.state.calibration_path.write_text(
            json.dumps(values, indent=2) + "\n", encoding="utf-8"
        )
