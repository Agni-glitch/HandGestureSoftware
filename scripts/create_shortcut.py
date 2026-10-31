"""Put a Hand Gesture Control icon on your Desktop: python scripts/create_shortcut.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gesture_control.shell_integration import create_shortcut

if __name__ == "__main__":
    print(f"Created {create_shortcut('Desktop')}")
