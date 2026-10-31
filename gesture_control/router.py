"""Decides what a gesture does right now, given the front app and your permissions."""

from __future__ import annotations

from gesture_control.app_watch import ForegroundWatcher
from gesture_control.profile import BUILTINS, PERMISSIONS, SCOPE_CONNECTED, Profile
from gesture_control.steps import Step, StepRunner, describe_outcome

_MODE_PERMISSION = {"volume_mode": "volume", "pointer_mode": "mouse"}


class ProfileRouter:
    def __init__(self, profile: Profile, os_ctl, watcher: ForegroundWatcher | None = None) -> None:
        self.profile = profile
        self.watcher = watcher or ForegroundWatcher()
        self.runner = StepRunner(os_ctl, self.has_permission)

    def has_permission(self, permission: str) -> bool:
        return self.profile.permissions.get(permission, False)

    def _binding(self, gesture_id: str):
        if gesture_id in BUILTINS:
            return self.profile.bindings[gesture_id]
        return self.profile.find_custom(gesture_id)

    def is_enabled(self, gesture_id: str) -> bool:
        binding = self._binding(gesture_id)
        return binding is not None and binding.enabled

    def cooldown(self, gesture_id: str) -> float:
        binding = self._binding(gesture_id)
        return binding.cooldown_s if binding else 0.0

    def scope_block(self) -> str | None:
        """Why gestures may not act on the front app right now, or None if they may."""
        # This app's own window always responds, so gestures can be tested from it.
        if self.watcher.is_own_window():
            return None
        current = self.watcher.current()
        if current is None:
            return None
        exe = current[1]
        app = self.profile.find_app(exe)
        if app is not None and not app.enabled:
            return f"gestures are off in {app.name}"
        if app is None and self.profile.scope == SCOPE_CONNECTED:
            return f"{exe} is not connected"
        return None

    def mode_block(self, gesture_id: str) -> str | None:
        permission = _MODE_PERMISSION[gesture_id]
        if not self.has_permission(permission):
            return "needs permission: " + PERMISSIONS[permission].name
        return self.scope_block()

    def steps_for(self, gesture_id: str) -> list[Step]:
        current = self.watcher.current()
        app = self.profile.find_app(current[1]) if current else None
        if app is not None and gesture_id in app.overrides:
            return app.overrides[gesture_id]
        binding = self._binding(gesture_id)
        return binding.steps if binding else []

    def fire(self, gesture_id: str) -> tuple[bool, str]:
        """Run the gesture's steps. Returns (whether they ran, text describing the outcome)."""
        reason = self.scope_block()
        if reason is not None:
            return False, reason
        steps = self.steps_for(gesture_id)
        if not steps:
            return False, "nothing assigned"
        reason = self.runner.run(steps)
        if reason is not None:
            return False, reason
        return True, describe_outcome(steps)

    def cancel(self) -> None:
        self.runner.cancel()
