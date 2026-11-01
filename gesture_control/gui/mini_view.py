"""The camera pinned to a corner of the screen, for while the main window is out of sight."""

from __future__ import annotations

import time

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from config import UIConfig
from gesture_control.gui import theme
from gesture_control.gui.pages import gesture_display_name
from gesture_control.gui.state import AppState, EngineController
from gesture_control.gui.widgets import bgr_to_pixmap, label
from gesture_control.profile import Profile

_PICTURE = QSize(320, 240)
_MAP_WIDTH = 72
_MARGIN_PX = 12
_CAPTION_MARGIN_PX = 10
_CAPTION_SPACING_PX = 8
_TEXT_WIDTH = _PICTURE.width() - _MAP_WIDTH - 2 * _CAPTION_MARGIN_PX - _CAPTION_SPACING_PX
_MARKER_RADIUS = 5.0
_STARTING_TEXT = "Opening the camera..."


def corner_position(area: QRect, size: QSize, corner: str) -> QPoint:
    """Top left of a window of this size in a corner. An unknown corner means bottom right."""
    x = area.left() + _MARGIN_PX if corner.endswith("left") else area.right() + 1 - size.width() - _MARGIN_PX
    y = area.top() + _MARGIN_PX if corner.startswith("top") else area.bottom() + 1 - size.height() - _MARGIN_PX
    return QPoint(x, y)


def caption(stats, profile: Profile, practice: bool, now: float) -> tuple[str, str, str]:
    """(title, detail, tone) describing what your hand is doing right now."""
    status = stats.status
    mode = status.mode.value
    if mode == "PAUSED":
        return "Paused", "Gestures are ignored for now", "paused"

    title = gesture_display_name(profile, status.gesture) or ("Hand seen" if stats.tracking else "No hand")
    if mode == "POINTER":
        detail = "Holding the click" if status.mouse_down else "Moving the mouse"
    elif mode == "VOLUME":
        detail = f"Changing the volume: {round(status.volume * 100)}%"
    else:
        detail = ""
    # What a gesture just did is the most useful thing to see, for as long as the Home page shows it.
    if status.flash is not None and now - status.flash_at <= UIConfig.action_flash_s:
        detail = status.flash
    if practice:
        detail = f"Practice: {detail}" if detail else "Practice mode"
    return title, detail, "active" if mode in ("POINTER", "VOLUME") else ""


class ScreenMap(QWidget):
    """A small picture of the screen, with a dot where your finger lands on it."""

    def __init__(self) -> None:
        super().__init__()
        screen = QGuiApplication.primaryScreen().geometry()
        self.setFixedSize(_MAP_WIDTH, round(_MAP_WIDTH * screen.height() / screen.width()))
        self.marker: tuple[tuple[float, float] | None, str, bool] = (None, theme.MUTED, False)

    def set_marker(self, position: tuple[float, float] | None, colour: str, filled: bool) -> None:
        marker = (position, colour, filled)
        if marker != self.marker:
            self.marker = marker
            self.update()

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(theme.MUTED), 1))
        p.setBrush(QColor(theme.CARD))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 3, 3)
        position, colour, filled = self.marker
        if position is None:
            return
        # Kept fully inside the outline, so a finger at the very edge of the screen is still seen.
        inset = _MARKER_RADIUS + 1
        x = inset + position[0] * (self.width() - 2 * inset)
        y = inset + position[1] * (self.height() - 2 * inset)
        p.setPen(QPen(QColor(colour), 2))
        p.setBrush(QColor(colour) if filled else Qt.NoBrush)
        p.drawEllipse(QRectF(x - _MARKER_RADIUS, y - _MARKER_RADIUS, 2 * _MARKER_RADIUS, 2 * _MARKER_RADIUS))


class MiniCameraView(QWidget):
    """Has no parent, because Windows minimizes a window along with the window that owns it.

    It never takes the keyboard or the mouse: clicks pass through to whatever is underneath, so
    it can't get in the way of the air mouse, and the app in front keeps its per-app gestures.
    """

    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__(
            None,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput,
        )
        self.state = state
        self.controller = controller
        self.setObjectName("miniView")
        self.setWindowTitle("Hand Gesture Control camera")
        self.setAttribute(Qt.WA_StyledBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.picture = QLabel(_STARTING_TEXT)
        self.picture.setObjectName("miniPicture")
        self.picture.setAlignment(Qt.AlignCenter)
        self.picture.setFixedSize(_PICTURE)
        outer.addWidget(self.picture)

        row = QHBoxLayout()
        row.setContentsMargins(_CAPTION_MARGIN_PX, 8, _CAPTION_MARGIN_PX, 8)
        row.setSpacing(_CAPTION_SPACING_PX)
        text = QVBoxLayout()
        text.setSpacing(0)
        self.title = label("Starting", "miniTitle")
        self.title.setFixedWidth(_TEXT_WIDTH)
        self.detail = label("", "miniDetail")
        self.detail.setFixedWidth(_TEXT_WIDTH)
        text.addWidget(self.title)
        text.addWidget(self.detail)
        row.addLayout(text)
        self.map = ScreenMap()
        row.addWidget(self.map, alignment=Qt.AlignVCenter)
        outer.addLayout(row)
        self.setFixedWidth(_PICTURE.width())
        self._tone = ""

        controller.live_ready.connect(self.show_live)
        QGuiApplication.primaryScreen().availableGeometryChanged.connect(lambda _area: self.place())

    def place(self) -> None:
        # The primary screen is the one the air mouse moves on. The available area stops above the taskbar.
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(corner_position(area, self.sizeHint(), self.state.profile.general.pin_corner))

    def pin(self) -> None:
        self.place()
        self.show()

    def unpin(self) -> None:
        self.hide()
        # A picture of you shouldn't stay in memory, or flash up the next time this opens.
        self.picture.clear()
        self.picture.setText(_STARTING_TEXT)
        self._set_caption("Starting", "", "")
        self.map.set_marker(None, theme.MUTED, False)

    def show_live(self, image, stats) -> None:
        # A frame can already be on its way when the window comes back.
        if not self.isVisible():
            return
        ratio = self.devicePixelRatioF()
        pixmap = bgr_to_pixmap(image).scaled(_PICTURE * ratio, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        self.picture.setPixmap(pixmap)

        self._set_caption(*caption(stats, self.state.profile, self.controller.practice, time.monotonic()))
        status = stats.status
        pointing = status.mode.value == "POINTER"
        colour = theme.RED if status.mouse_down else theme.ACCENT if pointing else theme.MUTED
        self.map.set_marker(stats.screen_position, colour, pointing)

    def _set_caption(self, title: str, detail: str, tone: str) -> None:
        for widget, value in ((self.title, title), (self.detail, detail)):
            # A long custom gesture name mustn't run under the map.
            widget.setText(widget.fontMetrics().elidedText(value, Qt.ElideRight, _TEXT_WIDTH))
        if tone != self._tone:
            self._tone = tone
            self.title.setProperty("tone", tone)
            self.title.style().polish(self.title)
