"""Your saved setup: permissions, gesture bindings, custom gestures and connected apps."""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from config import ROOT, ActionConfig
from gesture_control.steps import APP, SYSTEM, URL, Step

log = logging.getLogger(__name__)

PROFILE_PATH = ROOT / "user_data" / "settings.json"

SCOPE_ALL = "all"
SCOPE_CONNECTED = "connected"


@dataclass(frozen=True)
class PermissionInfo:
    name: str
    description: str
    default: bool


# Anything a ready-to-use gesture needs starts on. The rest start off and are offered the
# first time a step needs them.
PERMISSIONS = {
    "camera": PermissionInfo("Camera", "See your hand through the webcam. Frames are never saved.", True),
    "keyboard": PermissionInfo("Keyboard shortcuts", "Press media keys and shortcuts.", True),
    "mouse": PermissionInfo("Mouse control", "Move the cursor and click in air mouse mode.", True),
    "volume": PermissionInfo("System volume", "Change the volume with the pinch gesture.", True),
    "open_apps": PermissionInfo("Open apps", "Launch the apps that your gestures are set to open.", True),
    "websites": PermissionInfo("Open websites", "Open the web addresses that your gestures are set to open.", True),
    "typing": PermissionInfo("Type text", "Type text that you write in a step.", False),
    "startup": PermissionInfo("Start with Windows", "Open this app when you sign in.", False),
}


@dataclass(frozen=True)
class BuiltinInfo:
    name: str
    how: str
    # "action" runs steps once; "mode" stays on until you leave it; "pointer" only works in air mouse.
    kind: str
    pose: str = ""  # The ready-made pose this gesture uses, if it has one.


BUILTINS = {
    "pointer_mode": BuiltinInfo("Pointer (air mouse)", "Point with your index finger and hold. Open your palm to finish", "mode"),
    "click": BuiltinInfo("Click", "While pointing, pinch thumb to index. Pinch and move to drag", "pointer"),
    "double_click": BuiltinInfo("Double click", "While pointing, raise your middle finger too", "pointer"),
    "open_app": BuiltinInfo("Open an app", "Make a fist with your thumb pointing up", "action", "thumbs_up"),
    "open_website": BuiltinInfo("Open a website", "Make a fist with your thumb pointing down", "action", "thumbs_down"),
    "fist": BuiltinInfo("Fist", "Close all fingers, thumb tucked", "action"),
    "peace": BuiltinInfo("Peace sign", "Index and middle fingers up", "action"),
    "peace_left": BuiltinInfo("Peace sign, leaning left", "Index and middle up, tilted left", "action"),
    "swipe_right": BuiltinInfo("Swipe right", "Move an open hand quickly to the right", "action"),
    "swipe_left": BuiltinInfo("Swipe left", "Move an open hand quickly to the left", "action"),
    "volume_mode": BuiltinInfo("Volume pinch", "Thumb on index, other fingers up. Fist to finish", "mode"),
}
POSE_BUILTINS = {key: info.pose for key, info in BUILTINS.items() if info.pose}
_SWIPES = ("swipe_right", "swipe_left")
_DEFAULT_HOLD = {"volume_mode": 8, "pointer_mode": 8}
_DEFAULT_APPS = {"win32": "explorer.exe", "darwin": "/System/Library/CoreServices/Finder.app"}
DEFAULT_APP = _DEFAULT_APPS.get(sys.platform, str(Path.home()))
DEFAULT_WEBSITE = "https://www.google.com"
_OPEN_COOLDOWN_S = 3.0  # Opening the same thing twice is more annoying than repeating a key.


@dataclass
class Binding:
    enabled: bool = True
    steps: list[Step] = field(default_factory=list)
    hold_frames: int = 6
    cooldown_s: float = 1.0


@dataclass
class CustomGesture:
    id: str
    name: str
    template: list[float]
    threshold: float
    enabled: bool = True
    steps: list[Step] = field(default_factory=list)
    hold_frames: int = 8
    cooldown_s: float = 1.0
    # Landmarks for drawing the recorded hand. Empty for gestures recorded before this existed.
    outline: list[list[float]] = field(default_factory=list)
    # Set for a pose from the ready-made list, which has no recording.
    pose_id: str = ""


@dataclass
class ConnectedApp:
    name: str
    exe: str  # Process name in lower case, such as "chrome.exe".
    enabled: bool = True
    # Gesture id -> steps used while this app is in front. Empty steps: do nothing here.
    overrides: dict[str, list[Step]] = field(default_factory=dict)


@dataclass
class GeneralSettings:
    camera_index: int = 0
    show_preview: bool = True
    pin_camera: bool = True
    pin_corner: str = "bottom_right"
    start_engine_on_launch: bool = False
    close_to_tray: bool = True
    practice_mode: bool = False
    swipe_distance: float = 0.22
    show_welcome: bool = True


def default_bindings(actions: ActionConfig | None = None) -> dict[str, Binding]:
    actions = actions or ActionConfig()
    if actions.swipe_action == "tabs":
        forward, back = "tab_next", "tab_previous"
    else:
        forward, back = "media_next", "media_previous"
    steps = {
        "fist": actions.fist_action,
        "peace": "desktop_next",
        "peace_left": "desktop_previous",
        "swipe_right": forward,
        "swipe_left": back,
    }
    bindings = {key: Binding(steps=[Step(SYSTEM, value)]) for key, value in steps.items()}
    for key, hold in _DEFAULT_HOLD.items():
        bindings[key] = Binding(hold_frames=hold)
    bindings["click"] = Binding()
    bindings["double_click"] = Binding()
    bindings["open_app"] = Binding(steps=[Step(APP, DEFAULT_APP)], hold_frames=8, cooldown_s=_OPEN_COOLDOWN_S)
    bindings["open_website"] = Binding(steps=[Step(URL, DEFAULT_WEBSITE)], hold_frames=8, cooldown_s=_OPEN_COOLDOWN_S)
    return bindings


@dataclass
class Profile:
    permissions: dict[str, bool] = field(
        default_factory=lambda: {key: info.default for key, info in PERMISSIONS.items()}
    )
    bindings: dict[str, Binding] = field(default_factory=default_bindings)
    custom: list[CustomGesture] = field(default_factory=list)
    apps: list[ConnectedApp] = field(default_factory=list)
    scope: str = SCOPE_ALL
    general: GeneralSettings = field(default_factory=GeneralSettings)

    def gesture_name(self, gesture_id: str) -> str:
        if gesture_id in BUILTINS:
            return BUILTINS[gesture_id].name
        for gesture in self.custom:
            if gesture.id == gesture_id:
                return gesture.name
        return gesture_id

    def find_custom(self, gesture_id: str) -> CustomGesture | None:
        return next((g for g in self.custom if g.id == gesture_id), None)

    def gesture_using_pose(self, pose_id: str, ignore_id: str | None = None) -> CustomGesture | None:
        """The custom gesture already using a ready-made pose, if any."""
        return next((g for g in self.custom if g.pose_id == pose_id and g.id != ignore_id), None)

    def pose_taken_by(self, pose_id: str, ignore_id: str | None = None) -> str | None:
        """The name of the gesture a new one would clash with by using this pose, if any."""
        custom = self.gesture_using_pose(pose_id, ignore_id)
        if custom is not None:
            return custom.name
        # A gesture already on this pose, from before the pose had a built-in use, may keep it.
        mine = self.find_custom(ignore_id) if ignore_id else None
        if mine is not None and mine.pose_id == pose_id:
            return None
        return next((BUILTINS[key].name for key, pose in POSE_BUILTINS.items() if pose == pose_id), None)

    def find_app(self, exe: str | None) -> ConnectedApp | None:
        return next((a for a in self.apps if a.exe == exe), None) if exe else None

    def hold_frames(self) -> dict[str, int]:
        """Frames to hold each stabilizer label before it counts."""
        peace = min(self.bindings["peace"].hold_frames, self.bindings["peace_left"].hold_frames)
        holds = {
            "FIST": self.bindings["fist"].hold_frames,
            "PEACE": peace,
            "PINCH": self.bindings["volume_mode"].hold_frames,
            "POINTER": self.bindings["pointer_mode"].hold_frames,
            "OPEN_PALM": 6,
        }
        holds.update({key: self.bindings[key].hold_frames for key in POSE_BUILTINS})
        holds.update({g.id: g.hold_frames for g in self.custom})
        return holds

    def builtin_enabled(self, label: str) -> bool:
        """Whether a recognizer label is in use. A label not in use can go to a custom gesture."""
        ids = {
            "FIST": ("fist",),
            "PEACE": ("peace", "peace_left"),
            "PINCH": ("volume_mode",),
            "POINTER": ("pointer_mode",),
            "OPEN_PALM": _SWIPES,
        }.get(label, ())
        return any(self.bindings[i].enabled for i in ids)

    def steps_using(self, permission: str) -> list[str]:
        """Names of gestures that would stop working without this permission."""
        from gesture_control.steps import required_permissions

        names = []
        for key, binding in self.bindings.items():
            if binding.enabled and permission in required_permissions(binding.steps):
                names.append(BUILTINS[key].name)
        if permission == "volume" and self.bindings["volume_mode"].enabled:
            names.append(BUILTINS["volume_mode"].name)
        if permission == "mouse" and self.bindings["pointer_mode"].enabled:
            names.append(BUILTINS["pointer_mode"].name)
        for gesture in self.custom:
            if gesture.enabled and permission in required_permissions(gesture.steps):
                names.append(gesture.name)
        return names


def new_custom_id() -> str:
    return "custom:" + uuid.uuid4().hex[:10]


def _steps_from(data: list) -> list[Step]:
    return [Step(str(s["type"]), str(s.get("value", ""))) for s in data]


def profile_from_dict(data: dict) -> Profile:
    profile = Profile()
    for key, value in data.get("permissions", {}).items():
        if key in PERMISSIONS:
            profile.permissions[key] = bool(value)
    for key, value in data.get("bindings", {}).items():
        if key in BUILTINS:
            profile.bindings[key] = Binding(
                enabled=bool(value.get("enabled", True)),
                steps=_steps_from(value.get("steps", [])),
                hold_frames=int(value.get("hold_frames", 6)),
                cooldown_s=float(value.get("cooldown_s", 1.0)),
            )
    profile.custom = [
        CustomGesture(
            id=str(g["id"]),
            name=str(g["name"]),
            template=[float(v) for v in g["template"]],
            threshold=float(g["threshold"]),
            enabled=bool(g.get("enabled", True)),
            steps=_steps_from(g.get("steps", [])),
            hold_frames=int(g.get("hold_frames", 8)),
            cooldown_s=float(g.get("cooldown_s", 1.0)),
            outline=[[float(x), float(y)] for x, y in g.get("outline", [])],
            pose_id=str(g.get("pose_id", "")),
        )
        for g in data.get("custom", [])
    ]
    # Settings from before a pose had a built-in use: your own gesture on that pose keeps it.
    for key, pose_id in POSE_BUILTINS.items():
        if key not in data.get("bindings", {}) and profile.gesture_using_pose(pose_id) is not None:
            profile.bindings[key].enabled = False
    profile.apps = [
        ConnectedApp(
            name=str(a["name"]),
            exe=str(a["exe"]).lower(),
            enabled=bool(a.get("enabled", True)),
            overrides={k: _steps_from(v) for k, v in a.get("overrides", {}).items()},
        )
        for a in data.get("apps", [])
    ]
    if data.get("scope") in (SCOPE_ALL, SCOPE_CONNECTED):
        profile.scope = data["scope"]
    known = GeneralSettings.__dataclass_fields__
    profile.general = GeneralSettings(
        **{k: v for k, v in data.get("general", {}).items() if k in known}
    )
    return profile


def load_profile(path: Path = PROFILE_PATH) -> tuple[Profile, str | None]:
    """Returns the profile and a warning if the saved one was unreadable and set aside."""
    if not path.exists():
        return Profile(), None
    try:
        return profile_from_dict(json.loads(path.read_text(encoding="utf-8"))), None
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        backup = path.with_name(path.stem + ".broken" + path.suffix)
        os.replace(path, backup)
        log.warning("Could not read settings: %s", exc)
        return Profile(), f"Your settings could not be read, so defaults were loaded. The old file is at {backup}."


def save_profile(profile: Profile, path: Path = PROFILE_PATH) -> None:
    """Writes via a temporary file, so a crash mid-save can't destroy your settings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(profile), indent=2), encoding="utf-8")
    os.replace(tmp, path)
