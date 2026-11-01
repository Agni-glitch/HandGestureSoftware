"""The main window, the system tray icon and app startup."""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from config import ROOT
from gesture_control.gui import theme
from gesture_control.gui import widgets as ui
from gesture_control.gui.mini_view import MiniCameraView
from gesture_control.gui.pages import (
    AppsPage,
    DashboardPage,
    GesturesPage,
    PermissionsPage,
    SettingsPage,
)
from gesture_control.gui.state import AppState, EngineController
from gesture_control.gui.widgets import label
from gesture_control.shell_integration import ICON_PATH

log = logging.getLogger("gesture_control")

_INSTANCE_NAME = "HandGestureControl-instance"
_LOG_PATH = ROOT / "user_data" / "app.log"
# The practice-mode stand-in logs every volume step; the feed only needs what the gesture did.
_ACTIVITY_SKIP_PREFIX = "[dry-run]"


class _ActivitySignals(QObject):
    message = Signal(str)


class _ActivityHandler(logging.Handler):
    """Passes log messages to the UI. Logging can happen on any thread; the signal is safe."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.signals = _ActivitySignals()

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if not text.startswith(_ACTIVITY_SKIP_PREFIX):
            self.signals.message.emit(text)


class MainWindow(QMainWindow):
    def __init__(self, state: AppState, controller: EngineController) -> None:
        super().__init__()
        self.state = state
        self.controller = controller
        self.really_quit = False
        self.tray: QSystemTrayIcon | None = None
        self._tray_tip_shown = False
        self.setWindowTitle("Hand Gesture Control")
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1180, 780)
        self.setMinimumSize(980, 640)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 20, 12, 16)
        side.setSpacing(4)
        side.addWidget(label("Hand Gesture Control", "brand"))
        side.addSpacing(18)
        self.stack = QStackedWidget()
        self.dashboard = DashboardPage(state, controller)
        self.gestures = GesturesPage(state, controller)
        self.apps = AppsPage(state, controller)
        self.permissions = PermissionsPage(state, controller)
        self.settings = SettingsPage(state, controller)
        self._nav = QButtonGroup(self)
        self.nav_buttons = {}
        for name, page in (
            ("Home", self.dashboard),
            ("Gestures", self.gestures),
            ("Apps", self.apps),
            ("Permissions", self.permissions),
            ("Settings", self.settings),
        ):
            self.stack.addWidget(page)
            nav = QPushButton(name)
            nav.setObjectName("navButton")
            nav.setCheckable(True)
            nav.setCursor(Qt.PointingHandCursor)
            nav.clicked.connect(lambda _=False, p=page: self.stack.setCurrentWidget(p))
            self._nav.addButton(nav)
            side.addWidget(nav)
            self.nav_buttons[name] = nav
        self.nav_buttons["Home"].setChecked(True)
        self.dashboard.guide_button.clicked.connect(self.nav_buttons["Gestures"].click)
        self.settings.on_show_welcome = self._show_welcome
        side.addStretch(1)
        self.sidebar_status = label("", "sidebarStatus", wrap=True)
        side.addWidget(self.sidebar_status)

        outer.addWidget(sidebar)
        outer.addWidget(self.stack, 1)

        self.mini_view = MiniCameraView(state, controller)
        controller.state_changed.connect(self._on_state)
        controller.failed.connect(self._on_failed)
        state.changed.connect(self._sync_mini_view)
        self._on_state(controller.status)

    def _sync_mini_view(self) -> None:
        general = self.state.profile.general
        out_of_sight = not self.isVisible() or self.isMinimized()
        running = self.controller.is_active and not self.controller.silent
        wanted = out_of_sight and running and general.show_preview and general.pin_camera
        self.controller.set_compact_preview(wanted)
        if wanted:
            self.mini_view.pin()
        else:
            self.mini_view.unpin()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._sync_mini_view()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_mini_view()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._sync_mini_view()

    def _show_welcome(self) -> None:
        self.dashboard.welcome.show()
        self.nav_buttons["Home"].click()

    def _on_state(self, status: str) -> None:
        c = EngineController
        if status == c.RUNNING and self.controller.practice:
            text = "Practice mode: nothing on your computer is touched."
        else:
            text = {
                c.STOPPED: "Off. The camera isn't on.",
                c.STARTING: "Starting...",
                c.RUNNING: "Running. The camera is on.",
                c.STOPPING: "Stopping...",
            }[status]
        self.sidebar_status.setText(text)
        self._sync_tray()
        self._sync_mini_view()

    def _on_failed(self, error: str) -> None:
        self.dashboard.add_activity(f"Error: {error}")
        if self.isVisible():
            ui.show_error(self, "Gesture control stopped", error)
        elif self.tray is not None:
            self.tray.showMessage("Gesture control stopped", error, QSystemTrayIcon.Warning)

    def setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(QIcon(str(ICON_PATH)), self)
        self.tray.setToolTip("Hand Gesture Control")
        menu = QMenu()
        menu.addAction("Open", self.show_window)
        self.tray_start = QAction("Start", self)
        self.tray_start.triggered.connect(self.dashboard._toggle_engine)
        menu.addAction(self.tray_start)
        self.tray_pause = QAction("Pause or resume", self)
        self.tray_pause.triggered.connect(self.controller.toggle_pause)
        menu.addAction(self.tray_pause)
        menu.addSeparator()
        menu.addAction("Quit", self.quit_app)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._sync_tray()

    def _sync_tray(self) -> None:
        if self.tray is None:
            return
        active = self.controller.is_active
        self.tray_start.setText("Stop" if active else "Start")
        self.tray_pause.setEnabled(self.controller.status == EngineController.RUNNING)
        state = "running, camera on" if active else "stopped"
        self.tray.setToolTip(f"Hand Gesture Control: {state}")

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        keep_running = self.state.profile.general.close_to_tray and self.tray is not None
        if keep_running and not self.really_quit:
            event.ignore()
            self.hide()
            if not self._tray_tip_shown:
                self._tray_tip_shown = True
                status = "still running" if self.controller.is_active else "in the tray"
                self.tray.showMessage(
                    "Hand Gesture Control",
                    f"It's {status}. Right-click the tray icon to stop it or quit.",
                    QSystemTrayIcon.Information,
                )
            return
        self.mini_view.unpin()
        self.controller.shutdown()
        if self.tray is not None:
            self.tray.hide()
        event.accept()
        QApplication.quit()

    def quit_app(self) -> None:
        self.really_quit = True
        self.close()


def _setup_logging() -> _ActivityHandler:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.INFO)
    file_handler = logging.handlers.RotatingFileHandler(
        _LOG_PATH, maxBytes=500_000, backupCount=1, encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(file_handler)
    activity = _ActivityHandler()
    log.addHandler(activity)
    log.propagate = False
    return activity


def _claim_single_instance(app: QApplication) -> QLocalServer | None:
    """Returns a server if this is the first copy. Otherwise wakes that copy and returns None."""
    socket = QLocalSocket()
    socket.connectToServer(_INSTANCE_NAME)
    if socket.waitForConnected(300):
        socket.write(b"show")
        socket.waitForBytesWritten(300)
        socket.disconnectFromServer()
        return None
    QLocalServer.removeServer(_INSTANCE_NAME)
    server = QLocalServer(app)
    server.listen(_INSTANCE_NAME)
    return server


def run_app(argv: list[str]) -> int:
    # OpenCV's Linux builds point Qt at their own plugins, which would break this app.
    os.environ.pop("QT_QPA_PLATFORM_PLUGIN_PATH", None)
    if sys.platform == "win32":
        # Makes Windows show this app's icon on the taskbar, not Python's.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("HandGestureControl.App")

    app = QApplication(argv)
    app.setApplicationName("Hand Gesture Control")
    app.setQuitOnLastWindowClosed(False)
    server = _claim_single_instance(app)
    if server is None:
        return 0
    app.setStyleSheet(theme.STYLE)
    activity = _setup_logging()

    state = AppState()
    controller = EngineController(state)
    window = MainWindow(state, controller)
    # This must connect to a method of a QObject, not a lambda: messages come from the engine
    # thread, and only a QObject's slot is safely moved over to the UI thread by Qt.
    activity.signals.message.connect(window.dashboard.add_activity)
    window.setup_tray()
    server.newConnection.connect(window.show_window)

    def report_crash(kind, value, traceback) -> None:
        log.error("Unexpected error", exc_info=(kind, value, traceback))
        ui.show_error(window, "Something went wrong", f"{value}\n\nDetails are in {_LOG_PATH}")

    sys.excepthook = report_crash

    if "--minimized" not in argv:
        window.show()
    if state.load_warning:
        window.dashboard.add_activity(state.load_warning)
        if window.isVisible():
            ui.notify(window, "Settings", state.load_warning)
    if state.profile.general.start_engine_on_launch:
        controller.start(practice=state.profile.general.practice_mode)
    return app.exec()
