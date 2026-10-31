"""Build HandGestureControl.exe and its shortcut in the project folder: python scripts/build_exe.py

The .exe uses the models, assets and user_data folders beside it, so keep them together.
With --package, also make the zip that is attached to a GitHub release.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gesture_control import __version__
from gesture_control.shell_integration import SHORTCUT_NAME, write_shortcut

BUILD = ROOT / "build"
BUNDLE = BUILD / "dist" / "HandGestureControl"
EXE_NAME = "HandGestureControl.exe"
SUPPORT_DIR = "_internal"
REPORT = BUILD / "self_test.txt"
SELF_TEST_TIMEOUT_S = 120
PACKAGE_DOCS = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md", "CHANGELOG.md")


def make_package(exe: Path) -> Path:
    """Everything the app needs, in one folder inside a zip. Your own settings are left out."""
    name = f"HandGestureControl-{__version__}-windows-x64"
    staging = BUILD / "package"
    shutil.rmtree(staging, ignore_errors=True)
    folder = staging / name
    folder.mkdir(parents=True)
    shutil.copy2(exe, folder)
    shutil.copytree(ROOT / SUPPORT_DIR, folder / SUPPORT_DIR)
    shutil.copytree(ROOT / "assets", folder / "assets")
    (folder / "models").mkdir()
    shutil.copy2(ROOT / "models" / "hand_landmarker.task", folder / "models")
    for doc in PACKAGE_DOCS:
        shutil.copy2(ROOT / doc, folder)
    archive = shutil.make_archive(str(BUILD / name), "zip", staging)
    shutil.rmtree(staging)
    return Path(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the desktop app.")
    parser.add_argument("--package", action="store_true", help="also make a zip for a GitHub release")
    args = parser.parse_args()

    if not (ROOT / "models" / "hand_landmarker.task").exists():
        print("The hand model is missing. Run scripts/download_model.py first.", file=sys.stderr)
        return 1

    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller", str(ROOT / "scripts" / "HandGestureControl.spec"),
            "--noconfirm", "--distpath", str(BUILD / "dist"), "--workpath", str(BUILD / "work"),
        ],
        check=True,
    )

    exe = ROOT / EXE_NAME
    # Fails here, leaving the old build in place, if the app is still running.
    exe.unlink(missing_ok=True)
    shutil.rmtree(ROOT / SUPPORT_DIR, ignore_errors=True)
    shutil.move(BUNDLE / EXE_NAME, exe)
    shutil.move(BUNDLE / SUPPORT_DIR, ROOT / SUPPORT_DIR)

    REPORT.unlink(missing_ok=True)
    result = subprocess.run([exe, "--self-test", str(REPORT)], timeout=SELF_TEST_TIMEOUT_S)
    if result.returncode != 0:
        details = REPORT.read_text(encoding="utf-8") if REPORT.exists() else "no report was written"
        print(f"The built app failed its self-test (exit {result.returncode}):\n{details}", file=sys.stderr)
        return 1

    print(f"Built {exe}")
    print(f"Created {write_shortcut(ROOT / SHORTCUT_NAME, exe)}")
    if args.package:
        print(f"Packaged {make_package(exe)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
