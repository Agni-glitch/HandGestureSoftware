# Contributing

Thanks for your interest in Hand Gesture Control. Bug reports, ideas, documentation fixes and code are all welcome,
and you don't need to be an expert in computer vision to help.

By taking part, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). To report a security problem, follow
the [security policy](SECURITY.md) instead of opening a public issue.

## Good places to start

- **Testing on macOS and Linux.** The code supports both, but it has only been developed and tested on Windows 11.
  Reports of what works and what doesn't are very useful.
- **Testing on other cameras and in other lighting.** Tell us which gestures are misread.
- **Right-click and scrolling gestures.** These are the main things that still need a keyboard or mouse.
- **Documentation.** If something in the README confused you, it will confuse others too.

For anything large, such as a new gesture or a change to how recognition works, please open an issue first, so we can
agree on the approach before you spend time on it.

## Setting up

You need Python 3.10 to 3.14 and a webcam. On Windows:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python scripts\download_model.py
.venv\Scripts\python app.pyw
```

On macOS or Linux, use `.venv/bin/python`. The Practice mode switch on the Home page, or `main.py --dry-run --debug`,
lets you try gestures without anything really happening.

## Before opening a pull request

Run these and make sure they pass. The same checks run automatically on every pull request.

```powershell
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check .
```

The tests don't need a camera. They run the GUI without showing it, and with a temporary profile.

If you changed how the app is packaged, or added a library, also run `scripts\build_exe.py`. Its self-test confirms that
the built app can still load everything. New libraries also need an entry in `THIRD_PARTY_NOTICES.md`, and their
license must allow the project to stay under the MIT License.

## What we look for

- **Tests for changes in behaviour.** A bug fix should come with a test that fails without it.
- **One change per pull request.** Small pull requests are reviewed sooner.
- **Code that matches its surroundings.** Comments explain why something is done, not what it does.
- **Plain words in the app.** The app is for people who aren't programmers: say "Open apps", not "process spawning".
- **A changelog entry.** Add a line under `[Unreleased]` in `CHANGELOG.md` for anything users would notice.

Some rules protect the people using the app, and changes that break them won't be accepted:

- **Nothing leaves the computer.** No telemetry or network connections, other than a website gesture the user set up.
- **Camera frames are never saved.** Recording a pose stores numbers describing the hand, not pictures.
- **Text typed by a gesture stays out of logs.** It may be a password.
- **Every action checks its permission.** New kinds of action need a permission, off by default if they could cause harm.
- **A mouse button or key is never left held down,** whatever goes wrong.

## Where things are

The README has a [map of the code](README.md#architecture). Common changes:

| To change | Look in |
| --- | --- |
| How a hand pose is recognised | `gesture_control/recognizer.py` |
| How long a pose must be held, and swipes | `gesture_control/stability.py` |
| Modes, cooldowns, the cursor and volume | `gesture_control/actions.py` |
| What a gesture can do | `gesture_control/steps.py`, `gesture_control/os_control.py` |
| The default gestures and saved settings | `gesture_control/profile.py` |
| The window and its pages | `gesture_control/gui/` |
| Low-level tuning values | `config.py` |

## Reporting bugs

Please use the bug report form, and include your operating system, how you run the app, and what you expected to happen.
Relevant lines from `user_data/app.log` help a lot. Don't attach your `settings.json` without checking it first, as it
contains any text your gestures type.

## Making a release

For maintainers:

1. Update `__version__` in `gesture_control/__init__.py`.
2. Move the `[Unreleased]` entries in `CHANGELOG.md` under the new version and date.
3. Commit, then tag and push: `git tag v1.2.3`, then `git push origin main v1.2.3`.

The release workflow then runs the tests, builds the zip and publishes it on the Releases page.

## License

Your contributions are licensed under the project's [MIT License](LICENSE).
