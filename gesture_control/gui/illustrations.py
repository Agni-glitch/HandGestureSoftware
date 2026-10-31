"""Drawn, animated demonstrations of each gesture, and the words that go with them.

The hand is a right hand with its palm to the camera, as it looks in the mirrored preview, so
the demos move the same way your hand appears to on screen. Everything is in a 100x100 space.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from gesture_control.gui import theme
from gesture_control.recognizer import HAND_CONNECTIONS

Line = tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Pose:
    thumb: Line
    index: Line
    middle: Line
    ring: Line
    pinky: Line


_THUMB_OUT: Line = ((31, 74), (19, 64), (12, 53), (8, 43))
_THUMB_TUCKED: Line = ((31, 74), (38, 69), (47, 65), (56, 63))
_INDEX_UP: Line = ((33, 54), (31, 40), (30, 28), (29, 18))
_MIDDLE_UP: Line = ((45, 53), (45, 37), (44, 24), (44, 12))
_RING_UP: Line = ((57, 54), (58, 39), (59, 27), (60, 17))
_PINKY_UP: Line = ((68, 57), (71, 46), (74, 37), (76, 28))
# Folded towards the palm. Seen from the front, the finger overlaps itself into a stub.
_INDEX_DOWN: Line = ((33, 54), (33, 45), (33, 48), (34, 60))
_MIDDLE_DOWN: Line = ((45, 53), (45, 43), (45, 47), (45, 60))
_RING_DOWN: Line = ((57, 54), (57, 45), (57, 48), (56, 60))
_PINKY_DOWN: Line = ((68, 57), (68, 49), (68, 52), (66, 62))

OPEN = Pose(_THUMB_OUT, _INDEX_UP, _MIDDLE_UP, _RING_UP, _PINKY_UP)
FIST = Pose(_THUMB_TUCKED, _INDEX_DOWN, _MIDDLE_DOWN, _RING_DOWN, _PINKY_DOWN)
PEACE = Pose(
    _THUMB_TUCKED,
    ((33, 54), (29, 40), (26, 28), (23, 17)),
    ((45, 53), (47, 37), (49, 24), (51, 12)),
    _RING_DOWN,
    _PINKY_DOWN,
)
POINTER = Pose(_THUMB_TUCKED, _INDEX_UP, _MIDDLE_DOWN, _RING_DOWN, _PINKY_DOWN)
# Thumb and index fingertips meet, forming a ring. The other fingers stay up.
PINCH_CLOSED = Pose(
    ((31, 74), (16, 69), (6, 58), (5, 43)),
    ((33, 54), (28, 39), (17, 30), (6, 37)),
    _MIDDLE_UP,
    _RING_UP,
    _PINKY_UP,
)
PINCH_WIDE = Pose(_THUMB_OUT, _INDEX_UP, _MIDDLE_UP, _RING_UP, _PINKY_UP)
POINTER_PINCH = Pose(
    ((31, 74), (16, 69), (8, 56), (9, 41)),
    ((33, 54), (29, 38), (19, 30), (10, 35)),
    _MIDDLE_DOWN,
    _RING_DOWN,
    _PINKY_DOWN,
)
# The index finger hasn't moved from POINTER, which is why the cursor stays put.
POINTER_DOUBLE = Pose(_THUMB_TUCKED, _INDEX_UP, _MIDDLE_UP, _RING_DOWN, _PINKY_DOWN)
THUMBS_UP = Pose(((31, 74), (25, 60), (23, 46), (22, 32)), _INDEX_DOWN, _MIDDLE_DOWN, _RING_DOWN, _PINKY_DOWN)
ROCK = Pose(_THUMB_TUCKED, _INDEX_UP, _MIDDLE_DOWN, _RING_DOWN, _PINKY_UP)
THREE = Pose(_THUMB_TUCKED, _INDEX_UP, _MIDDLE_UP, _RING_UP, _PINKY_DOWN)

CALL = Pose(_THUMB_OUT, _INDEX_DOWN, _MIDDLE_DOWN, _RING_DOWN, _PINKY_UP)
PINKY = Pose(_THUMB_TUCKED, _INDEX_DOWN, _MIDDLE_DOWN, _RING_DOWN, _PINKY_UP)

# Pictures for the ready-made poses in custom_gestures.READY_POSES: (pose, rotation).
READY_POSE_PICTURES = {
    "thumbs_up": (THUMBS_UP, 0.0),
    "thumbs_down": (THUMBS_UP, 180.0),
    "rock": (ROCK, 0.0),
    "three": (THREE, 0.0),
    "call": (CALL, 0.0),
    "pinky": (PINKY, 0.0),
}


def _lerp_pose(a: Pose, b: Pose, f: float) -> Pose:
    def line(p: Line, q: Line) -> Line:
        return tuple((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f) for (x0, y0), (x1, y1) in zip(p, q))

    return Pose(*(line(getattr(a, n), getattr(b, n)) for n in ("thumb", "index", "middle", "ring", "pinky")))


@dataclass(frozen=True)
class Key:
    """The picture at a moment in a demo. Values between two keys are blended."""

    t: float
    pose: Pose = OPEN
    caption: str = ""
    dx: float = 0.0
    dy: float = 0.0
    rotation: float = 0.0
    highlight: float = 0.0  # 0 to 1: how strongly the fingers that matter are coloured.
    level: float | None = None  # The volume bar.
    click: float = 0.0  # 0 to 1: the expanding ring of a click.
    fired: float = 0.0  # 0 to 1: the ring that shows an action has run.
    cursor: bool = False


@dataclass(frozen=True)
class Scene:
    keys: tuple[Key, ...]
    highlighted: tuple[str, ...]  # Which fingers to colour.
    still_t: float  # The moment used for a small still picture.
    arrow: str | None = None  # "left" or "right".
    pinch_highlight: tuple[str, ...] = ()  # Which fingers to colour while clicking.

    @property
    def duration(self) -> float:
        return self.keys[-1].t


def _one_shot(pose: Pose, highlighted, name: str, rotation: float = 0.0, dy: float = 0.0) -> Scene:
    return Scene(
        (
            Key(0.0, OPEN, "Start with an open hand"),
            Key(0.7, OPEN, "Start with an open hand"),
            Key(1.2, pose, name, rotation=rotation, dy=dy, highlight=1.0),
            Key(1.5, pose, "Hold for a moment", rotation=rotation, dy=dy, highlight=1.0),
            Key(1.9, pose, "Done: the action runs once", rotation=rotation, dy=dy, highlight=1.0),
            Key(2.1, pose, "Done: the action runs once", rotation=rotation, dy=dy, highlight=1.0, fired=1.0),
            Key(3.0, pose, "Done: the action runs once", rotation=rotation, dy=dy, highlight=1.0, fired=0.0),
            Key(3.5, OPEN, "Done: the action runs once"),
        ),
        tuple(highlighted),
        still_t=1.6,
    )


_FINGERS = ("thumb", "index", "middle", "ring", "pinky")


def _swipe(direction: int, word: str) -> Scene:
    start, end = -20 * direction, 20 * direction
    return Scene(
        (
            Key(0.0, OPEN, "Hold up an open hand", dx=start),
            Key(0.8, OPEN, "Hold up an open hand", dx=start),
            Key(1.2, OPEN, f"Move it quickly to the {word}", dx=end, highlight=1.0),
            Key(1.4, OPEN, "Done: the action runs once", dx=end, highlight=1.0, fired=1.0),
            Key(2.2, OPEN, "Done: the action runs once", dx=end),
            Key(2.8, OPEN, "Then bring it back slowly", dx=start),
            Key(3.2, OPEN, "Then bring it back slowly", dx=start),
        ),
        _FINGERS,
        still_t=1.05,
        arrow=word,
    )


SCENES = {
    "fist": _one_shot(FIST, _FINGERS, "Close all fingers, thumb tucked"),
    "peace": _one_shot(PEACE, ("index", "middle"), "Raise index and middle, fold the rest"),
    "peace_left": _one_shot(PEACE, ("index", "middle"), "Peace sign, leaning to the left", rotation=-25.0),
    "swipe_right": _swipe(1, "right"),
    "swipe_left": _swipe(-1, "left"),
    "volume_mode": Scene(
        (
            Key(0.0, OPEN, "Start with an open hand", level=0.5),
            Key(0.6, OPEN, "Start with an open hand", level=0.5),
            Key(1.1, PINCH_CLOSED, "Touch thumb to index, others up", highlight=1.0, level=0.5),
            Key(1.8, PINCH_CLOSED, "Hold: volume mode turns on", highlight=1.0, level=0.5, fired=0.0),
            Key(2.0, PINCH_CLOSED, "Hold: volume mode turns on", highlight=1.0, level=0.5, fired=1.0),
            Key(2.8, PINCH_WIDE, "Spread until you reach the level", highlight=1.0, level=0.5),
            Key(3.5, _lerp_pose(PINCH_CLOSED, PINCH_WIDE, 0.2), "Close to lower the volume", highlight=1.0, level=0.2),
            Key(4.3, PINCH_WIDE, "Spread to raise it", highlight=1.0, level=0.9),
            Key(4.8, PINCH_WIDE, "Spread to raise it", highlight=1.0, level=0.9),
            Key(5.3, FIST, "Make a fist to finish", level=0.9),
            Key(6.1, FIST, "Make a fist to finish", level=0.9),
        ),
        ("thumb", "index"),
        still_t=1.5,
    ),
    "pointer_mode": Scene(
        (
            Key(0.0, OPEN, "Start with an open hand"),
            Key(0.5, OPEN, "Start with an open hand"),
            Key(1.0, POINTER, "Point with your index finger", highlight=1.0),
            Key(1.6, POINTER, "Hold: air mouse turns on", highlight=1.0, cursor=True),
            Key(2.4, POINTER, "Move your hand to move the cursor", dx=16, dy=-8, highlight=1.0, cursor=True),
            Key(3.2, POINTER, "Move your hand to move the cursor", dx=4, dy=4, highlight=1.0, cursor=True),
            Key(3.7, POINTER, "Move your hand to move the cursor", dx=4, dy=4, highlight=1.0, cursor=True),
            Key(4.0, POINTER_PINCH, "Pinch thumb to index to click", dx=4, dy=4, highlight=1.0, cursor=True),
            Key(4.5, POINTER_PINCH, "Pinch thumb to index to click", dx=4, dy=4, highlight=1.0, cursor=True, click=1.0),
            Key(4.8, POINTER, "Release to let go", dx=4, dy=4, highlight=1.0, cursor=True),
            Key(5.3, POINTER, "Release to let go", dx=4, dy=4, highlight=1.0, cursor=True),
            Key(5.8, OPEN, "Open your palm to finish", dx=4, dy=4),
            Key(6.6, OPEN, "Open your palm to finish", dx=4, dy=4),
        ),
        ("index",),
        still_t=1.4,
        # The thumb only matters while it pinches, so it is only coloured then.
        pinch_highlight=("index", "thumb"),
    ),
    "click": Scene(
        (
            Key(0.0, POINTER, "Point at what you want to click", highlight=1.0, cursor=True),
            Key(0.9, POINTER, "Point at what you want to click", highlight=1.0, cursor=True),
            Key(1.3, POINTER_PINCH, "Pinch thumb to index", highlight=1.0, cursor=True),
            Key(1.6, POINTER_PINCH, "Pinch thumb to index", highlight=1.0, cursor=True, click=1.0),
            Key(2.0, POINTER, "Let go: that's one click", highlight=1.0, cursor=True),
            Key(2.8, POINTER, "Let go: that's one click", highlight=1.0, cursor=True),
        ),
        ("index", "thumb"),
        still_t=1.35,
        pinch_highlight=("index", "thumb"),
    ),
    "double_click": Scene(
        (
            Key(0.0, POINTER, "Point at what you want to open", highlight=1.0, cursor=True),
            Key(0.9, POINTER, "Point at what you want to open", highlight=1.0, cursor=True),
            Key(1.3, POINTER_DOUBLE, "Raise your middle finger too", highlight=1.0, cursor=True),
            Key(1.7, POINTER_DOUBLE, "Raise your middle finger too", highlight=1.0, cursor=True),
            Key(1.95, POINTER_DOUBLE, "Double click!", highlight=1.0, cursor=True, click=1.0),
            Key(2.1, POINTER_DOUBLE, "Double click!", highlight=1.0, cursor=True),
            Key(2.35, POINTER_DOUBLE, "Double click!", highlight=1.0, cursor=True, click=1.0),
            Key(2.8, POINTER_DOUBLE, "Double click!", highlight=1.0, cursor=True),
            Key(3.2, POINTER, "Lower it to carry on pointing", highlight=1.0, cursor=True),
            Key(3.9, POINTER, "Lower it to carry on pointing", highlight=1.0, cursor=True),
        ),
        ("index", "middle"),
        still_t=1.5,
    ),
    "open_app": _one_shot(THUMBS_UP, ("thumb",), "Fist with your thumb pointing up"),
    # Turned about the wrist, then moved back up into the picture.
    "open_website": _one_shot(THUMBS_UP, ("thumb",), "Fist with your thumb pointing down", rotation=180.0, dy=-76.0),
}

# Plain instructions. These describe what the code requires, so keep them in step with it.
GUIDE_STEPS = {
    "fist": ["Close all four fingers.", "Tuck your thumb across them. A thumb sticking out doesn't count.", "Hold briefly. It runs once until you open your hand."],
    "peace": ["Raise your index and middle fingers.", "Fold your ring and little fingers.", "Hold briefly, with your hand upright."],
    "peace_left": ["Make the peace sign.", "Lean your hand to the left, as seen on screen.", "Hold briefly."],
    "swipe_right": ["Hold up an open hand.", "Move it quickly to the right, as seen on screen.", "Keep it level. Bring it back slowly so it doesn't count again."],
    "swipe_left": ["Hold up an open hand.", "Move it quickly to the left, as seen on screen.", "Keep it level. Bring it back slowly so it doesn't count again."],
    "volume_mode": ["Touch thumb to index, like an OK sign, with your other fingers up.", "Hold until the volume bar appears.", "Spread until your fingers reach the current level, then spread or close to change it.", "Make a fist, or lower your hand, to finish."],
    "pointer_mode": ["Point with your index finger, other fingers curled, and hold.", "Move your hand to move the cursor.", "Pinch to click, or raise your middle finger to double click.", "Open your palm, make a fist, or lower your hand to finish."],
    "click": ["Start pointing, and move the cursor to what you want.", "Pinch your thumb to your index finger, then let go.", "To drag, keep pinching and move your hand."],
    "double_click": ["Start pointing, and move the cursor to what you want.", "Raise your middle finger next to your index finger, and hold briefly.", "Lower it again before you double click somewhere else."],
    "open_app": ["Close all four fingers.", "Point your thumb straight up.", "Hold briefly. Press Change to choose which app opens.", "This doesn't work while pointing. Open your palm to stop pointing first."],
    "open_website": ["Close all four fingers.", "Point your thumb straight down.", "Hold briefly. Press Change to choose which website opens.", "This doesn't work while pointing. Open your palm to stop pointing first."],
}
GENERAL_TIPS = "Show your whole hand with your palm towards the camera, about 30 to 80 cm away, in good light."


def frame_at(scene: Scene, t: float) -> Key:
    """The blended picture at time t, which loops."""
    t = t % scene.duration
    keys = scene.keys
    after = next((i for i, key in enumerate(keys) if key.t > t), len(keys) - 1)
    a, b = keys[after - 1], keys[after]
    f = (t - a.t) / (b.t - a.t) if b.t > a.t else 1.0
    f = f * f * (3 - 2 * f)  # Ease in and out.

    def blend(x: float, y: float) -> float:
        return x + (y - x) * f

    level = None
    if a.level is not None and b.level is not None:
        level = blend(a.level, b.level)
    return replace(
        b,
        t=t,
        pose=_lerp_pose(a.pose, b.pose, f),
        dx=blend(a.dx, b.dx),
        dy=blend(a.dy, b.dy),
        rotation=blend(a.rotation, b.rotation),
        highlight=blend(a.highlight, b.highlight),
        level=level,
        click=max(a.click, b.click) * f if b.click >= a.click else a.click * (1 - f),
        fired=f if b.fired > a.fired else a.fired * (1 - f),
        cursor=a.cursor and b.cursor,
    )


_SKIN = QColor("#dde6e8")
_OUTLINE = QColor(theme.CARD)
_ACCENT = QColor(theme.ACCENT)
_FINGER_WIDTH = 10.5
_THUMB_WIDTH = 11.0


def _mix(a: QColor, b: QColor, f: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * f),
        round(a.green() + (b.green() - a.green()) * f),
        round(a.blue() + (b.blue() - a.blue()) * f),
    )


def _draw_digit(p: QPainter, line: Line, width: float, color: QColor) -> None:
    path = QPainterPath(QPointF(*line[0]))
    for point in line[1:]:
        path.lineTo(QPointF(*point))
    for pen_width, pen_color in ((width + 2.2, _OUTLINE), (width, color)):
        p.setPen(QPen(pen_color, pen_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)


def draw_hand(p: QPainter, pose: Pose, highlighted=(), highlight: float = 0.0) -> None:
    """Draws in 100x100 space. The caller sets the painter's transform."""
    palm = QPainterPath()
    palm.moveTo(26, 57)
    palm.cubicTo(32, 49, 68, 49, 75, 56)
    palm.cubicTo(76, 68, 72, 80, 65, 88)
    palm.lineTo(39, 88)
    palm.cubicTo(31, 82, 26, 72, 26, 57)

    # Forearm, then palm; fingers go on top, and the thumb on top of those to fold across them.
    p.setPen(QPen(_OUTLINE, 2.2))
    p.setBrush(_SKIN.darker(112))
    p.drawRoundedRect(QRectF(40, 80, 24, 26), 5, 5)
    p.setBrush(_SKIN)
    p.drawPath(palm)
    for name in ("pinky", "ring", "middle", "index", "thumb"):
        color = _mix(_SKIN, _ACCENT, highlight) if name in highlighted else _SKIN
        _draw_digit(p, getattr(pose, name), _THUMB_WIDTH if name == "thumb" else _FINGER_WIDTH, color)


def _draw_arrow(p: QPainter, direction: str) -> None:
    sign = 1 if direction == "right" else -1
    y = 7.0
    tail, head = 50 - 24 * sign, 50 + 24 * sign
    p.setPen(QPen(_ACCENT, 3.0, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(tail, y), QPointF(head, y))
    p.setPen(Qt.NoPen)
    p.setBrush(_ACCENT)
    p.drawPolygon(QPolygonF([QPointF(head + 6 * sign, y), QPointF(head - 2 * sign, y - 5), QPointF(head - 2 * sign, y + 5)]))


def _draw_level(p: QPainter, level: float) -> None:
    x, top, height, width = 90.0, 18.0, 62.0, 7.0
    p.setPen(QPen(QColor(theme.MUTED), 0.8))
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(x, top, width, height), 2, 2)
    p.setPen(Qt.NoPen)
    p.setBrush(_ACCENT)
    filled = height * max(min(level, 1.0), 0.0)
    p.drawRoundedRect(QRectF(x + 1, top + height - filled, width - 2, filled), 1.5, 1.5)


def _draw_cursor(p: QPainter, x: float, y: float) -> None:
    points = [(0, 0), (0, 11), (2.8, 8.4), (5, 13), (7, 12), (4.8, 7.6), (8.4, 7.6)]
    p.setPen(QPen(_OUTLINE, 0.8))
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([QPointF(x + a, y + b) for a, b in points]))


def _draw_ring(p: QPainter, x: float, y: float, progress: float, radius: float) -> None:
    color = QColor(_ACCENT)
    color.setAlphaF(max(1.0 - progress * 0.75, 0.0))
    p.setPen(QPen(color, 1.8))
    p.setBrush(Qt.NoBrush)
    r = radius * (0.35 + 0.65 * progress)
    p.drawEllipse(QPointF(x, y), r, r)


def draw_frame(p: QPainter, scene: Scene, frame: Key) -> None:
    """Draws one frame in 100x100 space, including its overlays."""
    p.save()
    p.translate(50, 50)
    p.scale(0.82, 0.82)
    p.translate(-50, -50)
    p.translate(frame.dx, frame.dy)
    p.translate(50, 88)
    p.rotate(frame.rotation)
    p.translate(-50, -88)
    highlighted = scene.pinch_highlight if frame.caption.startswith("Pinch") else scene.highlighted
    draw_hand(p, frame.pose, highlighted, frame.highlight)
    p.restore()

    # Fingertip position on screen, to place the cursor and the click ring.
    tip_x = 50 + (frame.pose.index[3][0] - 50 + frame.dx) * 0.82
    tip_y = 50 + (frame.pose.index[3][1] - 50 + frame.dy) * 0.82
    if scene.arrow:
        _draw_arrow(p, scene.arrow)
    if frame.level is not None:
        _draw_level(p, frame.level)
    if frame.click > 0.01:
        _draw_ring(p, tip_x, tip_y, frame.click, 10)
    if frame.cursor:
        _draw_cursor(p, tip_x + 5, tip_y - 7)
    if frame.fired > 0.01:
        _draw_ring(p, 50 + frame.dx * 0.82, 50, frame.fired, 46)


def draw_outline(p: QPainter, outline: list[list[float]]) -> None:
    """Draws a recorded hand, given landmarks fitted to a 0 to 1 box, in 100x100 space."""
    points = [QPointF(10 + x * 80, 10 + y * 80) for x, y in outline]
    p.setPen(QPen(_ACCENT, 3.0, Qt.SolidLine, Qt.RoundCap))
    for a, b in HAND_CONNECTIONS:
        p.drawLine(points[a], points[b])
    p.setPen(Qt.NoPen)
    p.setBrush(_SKIN)
    for point in points:
        p.drawEllipse(point, 2.2, 2.2)


class _Canvas(QWidget):
    """Maps a 100x100 drawing space to the centre of the widget."""

    # Stills sit on whatever is behind them, including a highlighted card.
    _FILL_BACKGROUND = False

    def __init__(self, side: int, caption_space: int = 0, on_click=None) -> None:
        super().__init__()
        self._side = side
        self._caption_space = caption_space
        self._on_click = on_click
        self.setFixedHeight(side + caption_space)
        self.setMinimumWidth(side)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if on_click is not None:
            self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:
        return QSize(self._side, self._side + self._caption_space)

    def mouseReleaseEvent(self, event) -> None:
        if self._on_click is not None and self.rect().contains(event.position().toPoint()):
            self._on_click()
        else:
            super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._FILL_BACKGROUND:
            p.fillRect(self.rect(), QColor(theme.CARD))
        p.save()
        p.translate((self.width() - self._side) / 2, 0)
        p.scale(self._side / 100, self._side / 100)
        self.draw(p)
        p.restore()
        self.draw_caption(p)

    def draw(self, p: QPainter) -> None:
        raise NotImplementedError

    def draw_caption(self, p: QPainter) -> None:
        pass


class GestureStill(_Canvas):
    """A small still picture of a built-in gesture."""

    def __init__(self, gesture_id: str, side: int = 64, on_click=None) -> None:
        super().__init__(side, on_click=on_click)
        self.setFixedWidth(side)
        self.scene = SCENES[gesture_id]
        self.frame = frame_at(self.scene, self.scene.still_t)

    def draw(self, p: QPainter) -> None:
        draw_frame(p, self.scene, self.frame)


class PoseStill(_Canvas):
    """A small still picture of a ready-made pose."""

    def __init__(self, pose_id: str, side: int = 64, on_click=None) -> None:
        super().__init__(side, on_click=on_click)
        self.setFixedWidth(side)
        self.pose_id = pose_id
        self.pose, self.rotation = READY_POSE_PICTURES[pose_id]

    def draw(self, p: QPainter) -> None:
        p.translate(50, 50)
        p.rotate(self.rotation)
        p.scale(0.82, 0.82)
        p.translate(-50, -50)
        draw_hand(p, self.pose)


class OutlineStill(_Canvas):
    """Your own recorded hand. Recordings older than this feature don't have an outline."""

    def __init__(self, outline: list[list[float]], side: int = 64, on_click=None) -> None:
        super().__init__(side, on_click=on_click)
        self.setFixedWidth(side)
        self.outline = outline

    def draw(self, p: QPainter) -> None:
        if len(self.outline) == 21:
            draw_outline(p, self.outline)
            return
        p.save()
        p.setOpacity(0.3)
        p.translate(50, 50)
        p.scale(0.82, 0.82)
        p.translate(-50, -50)
        draw_hand(p, OPEN)
        p.restore()
        p.setPen(QColor(theme.TEXT))
        font = QFont(p.font())
        font.setPixelSize(40)
        p.setFont(font)
        p.drawText(QRectF(0, 0, 100, 100), Qt.AlignCenter, "?")


def custom_picture(gesture, side: int = 64) -> QWidget:
    """The picture of a custom gesture, whichever way it was made."""
    if gesture.pose_id in READY_POSE_PICTURES:
        return PoseStill(gesture.pose_id, side=side)
    return OutlineStill(gesture.outline, side=side)


class GestureDemo(_Canvas):
    """A looping animation with captions. It only runs while it can be seen."""

    _FILL_BACKGROUND = True
    _INTERVAL_MS = 40
    _CAPTION_SPACE = 34

    def __init__(self, gesture_id: str, side: int = 200) -> None:
        super().__init__(side, self._CAPTION_SPACE)
        self.scene = SCENES[gesture_id]
        # Start on the key moment: from an open hand, all the demos would look the same at first.
        self.time = self.scene.still_t
        self.frame = frame_at(self.scene, self.time)
        self._timer = QTimer(self)
        self._timer.setInterval(self._INTERVAL_MS)
        self._timer.timeout.connect(self._tick)

    @property
    def is_playing(self) -> bool:
        return self._timer.isActive()

    def showEvent(self, event) -> None:
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _tick(self) -> None:
        self.time += self._INTERVAL_MS / 1000
        self.frame = frame_at(self.scene, self.time)
        self.update()

    def draw(self, p: QPainter) -> None:
        draw_frame(p, self.scene, self.frame)

    def draw_caption(self, p: QPainter) -> None:
        p.setPen(QColor(theme.TEXT))
        area = QRectF(4, self.height() - self._CAPTION_SPACE, self.width() - 8, self._CAPTION_SPACE)
        p.drawText(area, Qt.AlignCenter | Qt.TextWordWrap, self.frame.caption)
