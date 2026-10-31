# PyInstaller recipe for the desktop app. Run it through scripts/build_exe.py.
from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = Path(SPECPATH).parent

analysis = Analysis(
    [str(ROOT / "app.pyw")],
    pathex=[str(ROOT)],
    # MediaPipe finds its DLL through the mediapipe.tasks.c package, at run time.
    binaries=collect_dynamic_libs("mediapipe"),
    # pynput picks its backend at run time, so the Windows ones are invisible to the analysis.
    hiddenimports=["mediapipe.tasks.c", "pynput.keyboard._win32", "pynput.mouse._win32"],
    excludes=["pytest", "tkinter"],
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="HandGestureControl",
    icon=str(ROOT / "assets" / "icon.ico"),
    console=False,
    upx=False,
)
COLLECT(exe, analysis.binaries, analysis.datas, name="HandGestureControl", upx=False)
