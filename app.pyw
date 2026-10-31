"""Desktop app entry point. The .pyw extension runs it without a console window."""

import sys

from gesture_control.gui.main_window import run_app
from gesture_control.self_test import run_self_test

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(run_self_test(sys.argv[sys.argv.index("--self-test") + 1]))
    sys.exit(run_app(sys.argv))
