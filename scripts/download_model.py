"""Download MediaPipe's hand model. This is the only step that needs a network.

Run once during setup: python scripts/download_model.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
DEST = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if DEST.exists() and sha256(DEST) == SHA256:
        print(f"Model already present and verified: {DEST}")
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_suffix(".download")
    print(f"Downloading {URL}")
    urllib.request.urlretrieve(URL, tmp)

    actual = sha256(tmp)
    if actual != SHA256:
        tmp.unlink()
        print(f"Checksum mismatch: expected {SHA256}, got {actual}", file=sys.stderr)
        return 1
    tmp.replace(DEST)
    print(f"Saved and verified: {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
