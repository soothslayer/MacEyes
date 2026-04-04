# MacEyes

AI-powered screen description and voice-controlled computer automation for macOS. Runs as a menu bar app.

## Requirements

- macOS 13+
- Python 3.11+
- [Homebrew](https://brew.sh) (recommended)
- An `ANTHROPIC_API_KEY` from [console.anthropic.com](https://console.anthropic.com)

## Setup

```bash
export ANTHROPIC_API_KEY=your_key_here
./setup.sh
```

> **Apple Silicon:** `setup.sh` automatically replaces the Intel-only `flac-mac` binary bundled with SpeechRecognition. If you skip `setup.sh` and install manually, run `brew install flac` first or you'll get an `[Errno 86] Bad CPU type` error when using Voice Action.

## Running

```bash
source venv/bin/activate && python app.py
```

The app appears as `👁` in the menu bar.

## Permissions

macOS will prompt for the following permissions on first use — the app won't work without them:

| Permission | Required for |
|---|---|
| **Microphone** | Voice Action (recording your command) |
| **Screen Recording** | All screen capture features |
| **Accessibility** | Global hotkey (Cmd+.) and simulating keyboard/mouse actions |

Grant them in **System Settings → Privacy & Security**.

## Features

| Menu Item | What it does |
|---|---|
| Describe Screen | Takes a screenshot and reads aloud what's on screen |
| Describe Active Window | Same, but cropped to the frontmost window |
| Voice Action | Listens for a spoken command, then uses Claude to execute it |
| Stop Speaking | Stops the current speech output |
| Settings → Stop Hotkey | Change the global stop hotkey |
| Settings → Voice Action Hotkey | Change the global Voice Action trigger hotkey |

## Hotkeys

| Hotkey | Action |
|---|---|
| **Cmd+.** *(default)* | Stop speaking / cancel a running action immediately |
| **Cmd+Shift+V** *(default)* | Trigger Voice Action |

Both hotkeys work globally — you don't need the app to be focused. The stop hotkey exits the computer-use loop as soon as the current API call completes.

To change either: **Settings → Stop Hotkey** or **Settings → Voice Action Hotkey** in the menu bar. Use modifier names `cmd`, `ctrl`, `shift`, `alt` separated by `+`, followed by the key. Examples: `cmd+.`, `ctrl+shift+x`, `cmd+escape`. Settings are saved to `~/.maceyes.json`.

## Notes

- Voice Action uses Claude's computer-use API to take screenshots, click, type, scroll, and run shell commands on your behalf. Review what you're asking it to do — it has full control of your machine.
- Soft tones play every 3 seconds while Claude is working so you know it's still running.
- The computer-use loop is capped at 20 iterations per action.
