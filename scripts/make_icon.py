"""Draws assets/icon.ico. Needs Pillow; only used to regenerate the icon.

The hand is a real one: landmarks the model produced from a photo of an open hand.
"""

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SIZE = 1024
SKELETON = (
    (0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
    (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20),
)  # fmt: skip


def main() -> None:
    fixtures = np.load(ROOT / "tests" / "fixtures" / "real_hand_landmarks.npz")
    points = fixtures["OPEN_PALM|assets_right_hands|raw|0|image"][:, :2].astype(float)
    points[:, 0] *= float(fixtures["OPEN_PALM|assets_right_hands|raw|0|aspect"])
    # This photo's hand points downwards.
    if points[12, 1] > points[0, 1]:
        points[:, 1] = -points[:, 1]
    points -= (points.min(axis=0) + points.max(axis=0)) / 2
    points *= SIZE * 0.68 / np.ptp(points, axis=0).max()
    points += SIZE / 2

    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=210, fill=(20, 23, 26, 255))
    for a, b in SKELETON:
        draw.line([tuple(points[a]), tuple(points[b])], fill=(60, 215, 200, 255), width=44)
    for x, y in points:
        draw.ellipse((x - 34, y - 34, x + 34, y + 34), fill=(60, 215, 200, 255))
    for index in (4, 8, 12, 16, 20):
        x, y = points[index]
        draw.ellipse((x - 46, y - 46, x + 46, y + 46), fill=(240, 244, 245, 255))

    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    image.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    image.resize((256, 256), Image.LANCZOS).save(assets / "icon.png")


if __name__ == "__main__":
    main()
