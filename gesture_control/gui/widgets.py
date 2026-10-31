"""Small reusable pieces of UI."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from gesture_control.gui import theme


class Toggle(QCheckBox):
    """An on/off switch."""

    _W, _H = 38, 20

    def __init__(self, checked: bool = False, tooltip: str = "") -> None:
        super().__init__()
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tooltip)

    def sizeHint(self) -> QSize:
        return QSize(self._W, self._H)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        on = self.isChecked()
        track = QColor(theme.ACCENT if on else theme.BORDER)
        if not self.isEnabled():
            track.setAlpha(110)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, self._W, self._H), self._H / 2, self._H / 2)
        p.setBrush(QColor("#06201d" if on else theme.MUTED))
        x = self._W - self._H + 3 if on else 3
        p.drawEllipse(QRectF(x, 3, self._H - 6, self._H - 6))


def label(text: str = "", name: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    widget.setTextFormat(Qt.PlainText)
    return widget


def button(text: str, handler=None, name: str | None = None) -> QPushButton:
    widget = QPushButton(text)
    widget.setCursor(Qt.PointingHandCursor)
    if name:
        widget.setObjectName(name)
    if handler is not None:
        widget.clicked.connect(lambda _checked=False: handler())
    return widget


def card() -> QFrame:
    frame = QFrame()
    frame.setObjectName("card")
    return frame


def clear_layout(layout) -> None:
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


class Page(QWidget):
    """A scrolling page with a title. Subclasses add content to self.body."""

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("page")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("page")
        scroll.setWidget(content)
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(28, 24, 28, 24)
        self.body.setSpacing(12)

        self.header = QHBoxLayout()
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "title"))
        if subtitle:
            text.addWidget(label(subtitle, "subtitle", wrap=True))
        self.header.addLayout(text, 1)
        self.body.addLayout(self.header)


def bgr_to_pixmap(image) -> QPixmap:
    height, width = image.shape[:2]
    qimage = QImage(image.data, width, height, 3 * width, QImage.Format_BGR888)
    return QPixmap.fromImage(qimage)  # This copies, so the array can be released.


def run_dialog(dialog) -> int:
    """Show a dialog until it closes, then free it. Its Python attributes can still be read."""
    result = dialog.exec()
    dialog.deleteLater()
    return result


# These are functions so that tests can replace them: a real dialog would wait forever.
def confirm(parent, title: str, text: str) -> bool:
    box = QMessageBox(QMessageBox.Question, title, text, QMessageBox.Yes | QMessageBox.Cancel, parent)
    box.setDefaultButton(QMessageBox.Cancel)
    return box.exec() == QMessageBox.Yes


def notify(parent, title: str, text: str) -> None:
    QMessageBox(QMessageBox.Information, title, text, QMessageBox.Ok, parent).exec()


def show_error(parent, title: str, text: str) -> None:
    QMessageBox(QMessageBox.Warning, title, text, QMessageBox.Ok, parent).exec()
