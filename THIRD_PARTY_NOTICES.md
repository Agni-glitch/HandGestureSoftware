# Third-party notices

Hand Gesture Control's own code is under the [MIT License](LICENSE). It uses the components below, which keep
their own licenses. The source code in this repository includes none of them: `requirements.txt` installs the
libraries, and `scripts/download_model.py` fetches the model. The Windows release zip does include them.

## Main components

| Component | Version | License | Project |
| --- | --- | --- | --- |
| MediaPipe | 1.2.0 | Apache-2.0 | <https://github.com/google/mediapipe> |
| Hand landmarker model | float16/1 | Apache-2.0, per Google's model card | <https://developers.google.com/edge/mediapipe/solutions/vision/hand_landmarker> |
| OpenCV (contrib build) | 5.0.0.93 | Apache-2.0 | <https://github.com/opencv/opencv-python> |
| FFmpeg, inside the OpenCV build | as built by OpenCV | LGPL-2.1 | <https://ffmpeg.org> |
| PySide6 and Shiboken (Qt for Python) | 6.12.0 | LGPL-3.0-only, GPL-2.0-only or GPL-3.0-only | <https://www.qt.io/qt-for-python> |
| pynput | 1.8.2 | LGPL-3.0 | <https://github.com/moses-palmer/pynput> |
| NumPy | 2.5.4 | BSD-3-Clause, with 0BSD, MIT, Zlib and CC0-1.0 parts | <https://numpy.org> |
| psutil | 7.2.2 | BSD-3-Clause | <https://github.com/giampaolo/psutil> |
| pycaw | 20260927 | MIT | <https://github.com/AndreMiras/pycaw> |
| comtypes | 1.4.17 | MIT | <https://github.com/enthought/comtypes> |
| Python | 3.14 | PSF-2.0 | <https://www.python.org> |

## Installed because MediaPipe needs them

| Component | Version | License |
| --- | --- | --- |
| Matplotlib | 3.11.2 | Matplotlib License (PSF-based) |
| Pillow | 13.0.0 | MIT-CMU |
| sounddevice | 0.5.6 | MIT |
| absl-py | 2.5.1 | Apache-2.0 |
| FlatBuffers | 25.12.19 | Apache-2.0 |
| contourpy | 1.4.0 | BSD-3-Clause |
| kiwisolver | 1.5.1 | BSD-3-Clause |
| FontTools | 4.68.0 | MIT |
| pyparsing | 3.3.3 | MIT |
| cycler | 0.12.1 | BSD-3-Clause |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause |
| python-dateutil | 3.0.0 | Apache-2.0 or BSD-3-Clause |
| six | 1.17.0 | MIT |
| cffi | 2.1.1 | MIT-0 |
| pycparser | 3.12 | BSD-3-Clause |

The release is built with [PyInstaller](https://pyinstaller.org) 6.22.3, whose GPL license has an exception that
allows its bootloader to be included in applications under other licenses.

## The LGPL libraries in the release zip

Qt, Shiboken, pynput and FFmpeg are under the LGPL, which gives you the right to replace them with your own
versions. They stay as separate files in the `_internal` folder, so you can swap them there, or run the app from
source with versions of your choosing. Their source code is available from the project links above.
