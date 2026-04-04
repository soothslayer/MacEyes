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

## Hotkeys

| Hotkey | Action |
|---|---|
| **Cmd+.** | Stop speaking / cancel a running action immediately |

Cmd+. works globally — you don't need the app to be focused. It stops speech and exits the computer-use loop as soon as the current API call completes.

## Notes

- Voice Action uses Claude's computer-use API to take screenshots, click, type, scroll, and run shell commands on your behalf. Review what you're asking it to do — it has full control of your machine.
- Soft tones play every 3 seconds while Claude is working so you know it's still running.
- The computer-use loop is capped at 20 iterations per action.
