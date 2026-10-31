# Changelog

Notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-31

First public release.

### Added

- Desktop app with Home, Gestures, Apps, Permissions and Settings pages, and a system tray icon.
- Built-in gestures for the air mouse, click, drag, double click, media, volume, virtual desktops, and opening apps
  and websites.
- Your own gestures, from a list of poses or recorded with your camera, which can run several steps in a row.
- Per-app rules, a permission for each kind of action, and a Practice mode where nothing really happens.
- Windows `HandGestureControl.exe`, built by `scripts/build_exe.py`, with a self-test of the built app.
- The version number in Settings, and `main.py --version`.
- MIT license, contribution guidelines, security policy, and automated tests and release builds.

### Fixed

- Text typed by a gesture no longer appears in the activity feed or `user_data/app.log`, as it may be private.
- A gesture with one invalid step, such as an unknown key in a hand-edited settings file, no longer stops gesture
  control. It is reported as blocked instead.
- A damaged `calibration.json` no longer stops the app from starting gesture control. It is ignored with a warning.
