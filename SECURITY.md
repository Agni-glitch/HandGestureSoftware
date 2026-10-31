# Security policy

Hand Gesture Control uses your camera and can move the mouse, press keys and open programs, so security reports are
taken seriously.

## Supported versions

Fixes are made for the latest release only.

## Reporting a problem

**Please don't open a public issue.** Report it privately using [GitHub's security advisory form](../../security/advisories/new).

Include what the problem is, how to reproduce it, and what someone could do with it. You can expect a first reply within
7 days. This is a volunteer project, so a fix may take longer, but you'll be kept informed and credited if you wish.

## What counts

Examples of things to report:

- Camera pictures being saved or leaving the computer
- Any network connection other than a website gesture you set up, or the model download
- A way for a website or another computer to trigger actions or change settings
- Text typed by a gesture appearing in a log
- An action running without its permission
- A model download that isn't verified against its pinned hash

## What the app does and doesn't protect against

Knowing these limits should help you use the app safely:

- **Your settings aren't encrypted.** `user_data/settings.json` holds your gestures, including any text they type, in
  plain text. Don't make a gesture that types a password on a computer you share, and don't post that file publicly.
- **Other programs running as you are trusted.** A program with access to your account could edit your settings, for
  example to change what a gesture opens. Keeping malware off a computer is outside what this app can do.
- **Anyone your camera can see can make gestures.** Pause with `Ctrl+Alt+G` or stop the app when you step away.
- **Release builds aren't code-signed,** so Windows SmartScreen may warn about them. Only download them from this
  project's Releases page.
