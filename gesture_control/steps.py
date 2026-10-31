"""What a gesture can do: an ordered list of steps, each checked against your permissions."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

from gesture_control.os_control import KEY_ALIASES, is_valid_key

log = logging.getLogger(__name__)

MAX_WAIT_MS = 30000

SYSTEM = "system"
HOTKEY = "hotkey"
TEXT = "text"
APP = "app"
URL = "url"
WAIT = "wait"

# Display label and the permission each step type needs (None: no permission needed).
STEP_TYPES = {
    SYSTEM: ("Built-in action", "keyboard"),
    HOTKEY: ("Press shortcut", "keyboard"),
    APP: ("Open app", "open_apps"),
    URL: ("Open website", "websites"),
    TEXT: ("Type text", "typing"),
    WAIT: ("Wait", None),
}

SYSTEM_ACTIONS = {
    "play_pause": "Play / pause",
    "mute": "Mute / unmute",
    "media_next": "Next track",
    "media_previous": "Previous track",
    "volume_up": "Volume up",
    "volume_down": "Volume down",
    "desktop_next": "Next desktop",
    "desktop_previous": "Previous desktop",
    "tab_next": "Next browser tab",
    "tab_previous": "Previous browser tab",
    "show_desktop": "Show desktop",
    "switch_app": "Switch app",
    "screenshot": "Take screenshot",
    "copy": "Copy",
    "paste": "Paste",
    "close_window": "Close window",
}


@dataclass
class Step:
    type: str
    value: str = ""


_APP_NAMES = {"explorer.exe": "File Explorer"}


def app_name(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower() in _APP_NAMES:
        return _APP_NAMES[name.lower()]
    return name.rsplit(".", 1)[0] if name.lower().endswith((".exe", ".lnk")) else name


def parse_chord(text: str) -> list[str]:
    """Turn 'Ctrl + Shift + S' into key names. Raises ValueError if it is not a valid shortcut."""
    keys = []
    for part in text.split("+"):
        name = part.strip().lower().replace(" ", "_")
        name = KEY_ALIASES.get(name, name)
        if not name:
            raise ValueError("Shortcut has an empty key. Example: ctrl+shift+s")
        if not is_valid_key(name):
            raise ValueError(f"Unknown key '{part.strip()}'. Example: ctrl+shift+s")
        keys.append(name)
    if not keys:
        raise ValueError("Enter a shortcut. Example: ctrl+shift+s")
    return keys


def validate_step(step: Step) -> str | None:
    """A message describing what is wrong with the step, or None if it is usable."""
    value = step.value.strip()
    if step.type == SYSTEM:
        return None if value in SYSTEM_ACTIONS else "Choose a built-in action."
    if step.type == HOTKEY:
        try:
            parse_chord(value)
        except ValueError as exc:
            return str(exc)
        return None
    if step.type == APP:
        return None if value else "Choose an app to open."
    if step.type == URL:
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            return "Enter a full web address, starting with https://"
        return None
    if step.type == TEXT:
        return None if step.value else "Enter the text to type."
    if step.type == WAIT:
        if not value.isdigit() or not 0 < int(value) <= MAX_WAIT_MS:
            return f"Wait time must be 1 to {MAX_WAIT_MS} milliseconds."
        return None
    return f"Unknown step type '{step.type}'."


def describe_step(step: Step) -> str:
    if step.type == SYSTEM:
        return SYSTEM_ACTIONS.get(step.value, step.value)
    if step.type == HOTKEY:
        return "+".join(k.capitalize() for k in step.value.replace(" ", "").split("+"))
    if step.type == APP:
        return "Open " + app_name(step.value)
    if step.type == URL:
        return "Open " + (urlparse(step.value).netloc or step.value)
    if step.type == TEXT:
        preview = step.value if len(step.value) <= 18 else step.value[:15] + "..."
        return f'Type "{preview}"'
    if step.type == WAIT:
        return f"Wait {int(step.value) / 1000:g}s" if step.value.isdigit() else "Wait"
    return step.type


def describe_steps(steps: list[Step]) -> str:
    return "  >  ".join(describe_step(s) for s in steps) if steps else "Does nothing"


def describe_outcome(steps: list[Step]) -> str:
    """For the activity feed and the log file, which must not hold what was typed: it may be private."""
    return "  >  ".join("Type text" if s.type == TEXT else describe_step(s) for s in steps)


def required_permissions(steps: list[Step]) -> set[str]:
    needed = {STEP_TYPES[s.type][1] for s in steps if s.type in STEP_TYPES}
    needed.discard(None)
    return needed


class StepRunner:
    """Runs step lists. A lone instant step runs inline; anything longer gets a worker thread."""

    _INSTANT = (SYSTEM, HOTKEY)

    def __init__(self, os_ctl, is_allowed: Callable[[str], bool]) -> None:
        self.os = os_ctl
        self.is_allowed = is_allowed
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None

    def run(self, steps: list[Step]) -> str | None:
        """Start the steps. Returns why they were refused, or None if they started."""
        # All or nothing: half a sequence could leave things in a surprising state.
        missing = sorted(p for p in required_permissions(steps) if not self.is_allowed(p))
        if missing:
            return "needs permission: " + ", ".join(missing)
        if len(steps) == 1 and steps[0].type in self._INSTANT:
            # This runs on the engine thread, where one bad step must not stop gesture control.
            try:
                self._run_step(steps[0])
            except Exception as exc:
                log.warning("Step failed (%s): %s", describe_outcome(steps), exc)
                return "the step failed, see the log"
            return None
        if self._worker is not None and self._worker.is_alive():
            return "another sequence is still running"
        self._cancel.clear()
        self._worker = threading.Thread(target=self._run_all, args=(steps,), daemon=True)
        self._worker.start()
        return None

    def cancel(self) -> None:
        self._cancel.set()

    def _run_all(self, steps: list[Step]) -> None:
        for step in steps:
            if self._cancel.is_set():
                return
            try:
                self._run_step(step)
            except Exception as exc:
                log.warning("Step failed (%s): %s", describe_outcome([step]), exc)
                return

    def _run_step(self, step: Step) -> None:
        if step.type == SYSTEM:
            self.os.trigger(step.value)
        elif step.type == HOTKEY:
            self.os.press_keys(parse_chord(step.value))
        elif step.type == TEXT:
            self.os.type_text(step.value)
        elif step.type == APP:
            self.os.open_app(step.value)
        elif step.type == URL:
            # Settings files can be edited by hand, so this is checked again at run time.
            if validate_step(step) is None:
                self.os.open_url(step.value)
        elif step.type == WAIT:
            self._cancel.wait(min(int(step.value), MAX_WAIT_MS) / 1000.0)
