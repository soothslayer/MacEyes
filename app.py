#!/usr/bin/env python3
"""MacEyes — AI-powered screen description via voice."""

from __future__ import annotations

import anthropic
import base64
import difflib
import json
import math
import os
import plistlib
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback

import Quartz
import pyautogui
import rumps
import speech_recognition as sr
from pynput import keyboard

pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = False

def _get_client() -> anthropic.Anthropic:
    """Return an Anthropic client, preferring the key stored in settings."""
    key = _settings_instance.api_key if "_settings_instance" in globals() else None
    return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()

SYSTEM_PROMPT = (
    "You are an accessibility assistant helping a visually impaired user understand "
    "what is on their screen. Describe the screen concisely and clearly. "
    "Lead with the active application and main content, then mention important UI elements. "
    "Keep it under 3 sentences unless there is a lot to convey."
)

DESCRIBE_PROMPT = (
    "Describe what is currently visible on this screen. "
    "Be concise and natural — you will be read aloud."
)

VOICE_ACTION_SYSTEM = (
    "You are a computer automation assistant. "
    "The user has spoken a task they want performed on their Mac. "
    "Use the tools to observe the screen and take actions to complete the task. "
    "Take a screenshot first to understand the current state. "
    "When done, provide a brief, spoken-friendly summary (1-2 sentences) of what you did."
)

_SETTINGS_PATH = os.path.expanduser("~/.maceyes.json")
_DEFAULT_STOP_HOTKEY = "cmd+."
_DEFAULT_VOICE_ACTION_HOTKEY = "cmd+shift+v"

_VERSION = "1.1.0"
_CHANGELOG = """\
v1.1.0
• Start on Login setting (LaunchAgent)

v1.0.0
• Describe Screen and Describe Active Window
• Voice Action with computer-use automation
• Configurable hotkeys and API key storage
• Tunable Voice Action limits (iterations, tokens)
• Toggleable action model (Haiku / Sonnet)
• Say "Over" when done option
• Processing tones and stop-hotkey feedback\
"""

_LAUNCH_AGENT_LABEL = "com.maceyes.app"
_LAUNCH_AGENT_PLIST = os.path.expanduser(
    f"~/Library/LaunchAgents/{_LAUNCH_AGENT_LABEL}.plist"
)


def _launch_at_login_enabled() -> bool:
    """Return True if the LaunchAgent plist currently exists."""
    return os.path.exists(_LAUNCH_AGENT_PLIST)


def _set_launch_at_login(enabled: bool) -> None:
    """Create or remove the LaunchAgent plist to enable/disable launch at login."""
    if enabled:
        os.makedirs(os.path.dirname(_LAUNCH_AGENT_PLIST), exist_ok=True)
        # Detect whether we're running inside a py2app .app bundle
        if getattr(sys, "frozen", False):
            # py2app sets sys.frozen and packages a standalone executable
            program_args = [sys.executable]
        else:
            program_args = [sys.executable, os.path.abspath(__file__)]
        plist_data = {
            "Label": _LAUNCH_AGENT_LABEL,
            "ProgramArguments": program_args,
            "RunAtLoad": True,
            "KeepAlive": False,
        }
        with open(_LAUNCH_AGENT_PLIST, "wb") as f:
            plistlib.dump(plist_data, f)
        subprocess.run(
            ["launchctl", "load", _LAUNCH_AGENT_PLIST],
            capture_output=True,
        )
    else:
        if os.path.exists(_LAUNCH_AGENT_PLIST):
            subprocess.run(
                ["launchctl", "unload", _LAUNCH_AGENT_PLIST],
                capture_output=True,
            )
            os.remove(_LAUNCH_AGENT_PLIST)


class _Settings:
    """Persists user preferences to ~/.maceyes.json."""

    def __init__(self):
        self._data: dict = {}
        self._load()

    def _load(self):
        try:
            with open(_SETTINGS_PATH) as f:
                self._data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            self._data = {}

    def _save(self):
        with open(_SETTINGS_PATH, "w") as f:
            json.dump(self._data, f, indent=2)

    @property
    def stop_hotkey(self) -> str:
        """Human-readable hotkey string, e.g. 'cmd+.'"""
        return self._data.get("stop_hotkey", _DEFAULT_STOP_HOTKEY)

    @stop_hotkey.setter
    def stop_hotkey(self, value: str):
        self._data["stop_hotkey"] = value
        self._save()

    @property
    def voice_action_hotkey(self) -> str:
        """Human-readable hotkey string, e.g. 'cmd+shift+v'"""
        return self._data.get("voice_action_hotkey", _DEFAULT_VOICE_ACTION_HOTKEY)

    @voice_action_hotkey.setter
    def voice_action_hotkey(self, value: str):
        self._data["voice_action_hotkey"] = value
        self._save()

    @property
    def api_key(self) -> str | None:
        return self._data.get("api_key") or None

    @api_key.setter
    def api_key(self, value: str | None):
        if value:
            self._data["api_key"] = value
        else:
            self._data.pop("api_key", None)
        self._save()

    @property
    def say_over(self) -> bool:
        return self._data.get("say_over", False)

    @say_over.setter
    def say_over(self, value: bool):
        self._data["say_over"] = value
        self._save()

    @property
    def computer_use_max_iterations(self) -> int:
        return self._data.get("computer_use_max_iterations", 20)

    @computer_use_max_iterations.setter
    def computer_use_max_iterations(self, value: int):
        self._data["computer_use_max_iterations"] = value
        self._save()

    @property
    def computer_use_model(self) -> str:
        return self._data.get("computer_use_model", "claude-haiku-4-5-20251001")

    @computer_use_model.setter
    def computer_use_model(self, value: str):
        self._data["computer_use_model"] = value
        self._save()

    @property
    def computer_use_max_tokens(self) -> int:
        return self._data.get("computer_use_max_tokens", 4096)

    @computer_use_max_tokens.setter
    def computer_use_max_tokens(self, value: int):
        self._data["computer_use_max_tokens"] = value
        self._save()


_app_cache: list[str] = []
_app_cache_lock = threading.Lock()


def _refresh_app_list() -> None:
    """Fetch installed .app bundle names via Spotlight and update the cache."""
    try:
        result = subprocess.run(
            ["mdfind", "kMDItemContentType == 'com.apple.application-bundle'"],
            capture_output=True, text=True, timeout=15,
        )
        apps = []
        for path in result.stdout.splitlines():
            name = os.path.basename(path)
            if name.endswith(".app"):
                apps.append(name[:-4])
        with _app_cache_lock:
            global _app_cache
            _app_cache = sorted(set(apps))
        print(f"[MacEyes] App cache refreshed: {len(_app_cache)} apps", file=sys.stderr)
    except Exception as exc:
        print(f"[MacEyes] App cache refresh failed: {exc}", file=sys.stderr)


def _start_app_cache_refresher() -> None:
    """Start a daemon thread that refreshes the app list immediately and every 15 minutes."""
    def _loop():
        while True:
            _refresh_app_list()
            time.sleep(15 * 60)
    threading.Thread(target=_loop, daemon=True, name="app-cache-refresher").start()


def _normalize_app_name(s: str) -> str:
    """Lowercase and strip non-alphanumeric characters for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _resolve_app_names(command: str) -> str:
    """Replace misrecognized app names in the spoken command with known app names.

    Tries every 1-to-4-word n-gram in the command and substitutes the highest-scoring
    match from the installed app cache when similarity exceeds the threshold.
    """
    with _app_cache_lock:
        apps = list(_app_cache)
    if not apps:
        return command

    norm_to_app = {_normalize_app_name(app): app for app in apps}
    words = command.split()

    best_score = 0.80  # minimum similarity to accept a substitution
    best_app: str | None = None
    best_span: tuple[int, int] | None = None

    for n in range(1, 5):
        for i in range(len(words) - n + 1):
            phrase = " ".join(words[i : i + n])
            norm_phrase = _normalize_app_name(phrase)
            for norm_app, original_app in norm_to_app.items():
                score = difflib.SequenceMatcher(None, norm_phrase, norm_app).ratio()
                if score > best_score:
                    best_score = score
                    best_app = original_app
                    best_span = (i, i + n)

    if best_app and best_span:
        start, end = best_span
        resolved = " ".join(words[:start] + [best_app] + words[end:])
        if resolved != command:
            print(
                f"[MacEyes] App name resolved: '{command}' -> '{resolved}' "
                f"(score={best_score:.2f})",
                file=sys.stderr,
            )
        return resolved

    return command


_KEY_ALIASES = {
    "option": "alt",
    "escape": "esc",
    "return": "enter",
    "delete": "backspace",
}


def _hotkey_to_pynput(hotkey: str) -> str:
    """Convert a user-friendly hotkey string to pynput GlobalHotKeys format.

    Modifiers (cmd, ctrl, shift, alt/option) and multi-character key names
    (esc, space, enter, tab, f1-f12, etc.) are wrapped in <>.
    Single printable characters are left bare.

    Examples:
        'cmd+.'        -> '<cmd>+.'
        'ctrl+shift+s' -> '<ctrl>+<shift>+s'
        'cmd+escape'   -> '<cmd>+<esc>'
    """
    modifiers = {"cmd", "ctrl", "shift", "alt"}
    parts = [p.strip().lower() for p in hotkey.split("+")]
    converted = []
    for part in parts:
        part = _KEY_ALIASES.get(part, part)
        if part in modifiers or len(part) > 1:
            converted.append(f"<{part}>")
        else:
            converted.append(part)
    return "+".join(converted)


# Key name mapping from X11/Claude style to pyautogui style
_KEY_MAP = {
    "Return": "enter", "Escape": "esc", "BackSpace": "backspace",
    "Delete": "delete", "Tab": "tab", "space": "space",
    "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Home": "home", "End": "end", "Page_Up": "pageup", "Page_Down": "pagedown",
    "ctrl": "ctrl", "alt": "alt", "shift": "shift",
    "super": "command", "cmd": "command", "meta": "command",
    **{f"F{i}": f"f{i}" for i in range(1, 13)},
}


class MacEyesApp(rumps.App):
    def __init__(self):
        super().__init__("👁", quit_button="Quit MacEyes")
        self._settings = _Settings()

        global _settings_instance
        _settings_instance = self._settings

        settings_menu = rumps.MenuItem("Settings")
        self._stop_hotkey_item = rumps.MenuItem(
            self._stop_hotkey_menu_label(), callback=self.on_set_stop_hotkey
        )
        self._voice_hotkey_item = rumps.MenuItem(
            self._voice_hotkey_menu_label(), callback=self.on_set_voice_hotkey
        )
        self._api_key_item = rumps.MenuItem(
            self._api_key_menu_label(), callback=self.on_set_api_key
        )
        self._say_over_item = rumps.MenuItem(
            self._say_over_menu_label(), callback=self.on_toggle_say_over
        )
        self._computer_use_model_item = rumps.MenuItem(
            self._computer_use_model_menu_label(), callback=self.on_toggle_computer_use_model
        )
        self._max_iterations_item = rumps.MenuItem(
            self._max_iterations_menu_label(), callback=self.on_set_max_iterations
        )
        self._max_tokens_item = rumps.MenuItem(
            self._max_tokens_menu_label(), callback=self.on_set_max_tokens
        )
        self._launch_at_login_item = rumps.MenuItem(
            self._launch_at_login_menu_label(), callback=self.on_toggle_launch_at_login
        )
        settings_menu.add(self._stop_hotkey_item)
        settings_menu.add(self._voice_hotkey_item)
        settings_menu.add(self._api_key_item)
        settings_menu.add(self._say_over_item)
        settings_menu.add(self._computer_use_model_item)
        settings_menu.add(self._max_iterations_item)
        settings_menu.add(self._max_tokens_item)
        settings_menu.add(self._launch_at_login_item)

        self.menu = [
            rumps.MenuItem("Describe Screen", callback=self.on_describe),
            rumps.MenuItem("Describe Active Window", callback=self.on_describe_window),
            rumps.MenuItem("Voice Action", callback=self.on_voice_action),
            rumps.MenuItem("Stop Speaking", callback=self.on_stop),
            None,  # separator
            settings_menu,
            rumps.MenuItem(f"About MacEyes {_VERSION}", callback=self.on_about),
        ]
        self._busy = False
        self._say_proc: subprocess.Popen | None = None
        self._cancel = threading.Event()

        self._hotkey_listener = self._build_hotkey_listener()
        self._hotkey_listener.start()

        _start_app_cache_refresher()

    @rumps.clicked("Describe Screen")
    def on_describe(self, _):
        if self._busy:
            return
        self._busy = True
        self.title = "⏳"
        threading.Thread(target=self._run, args=(_capture_screen,), daemon=True).start()

    @rumps.clicked("Describe Active Window")
    def on_describe_window(self, _):
        if self._busy:
            return
        self._busy = True
        self.title = "⏳"
        threading.Thread(target=self._run, args=(_capture_active_window,), daemon=True).start()

    @rumps.clicked("Voice Action")
    def on_voice_action(self, _):
        if self._busy:
            return
        self._busy = True
        self.title = "🎤"
        threading.Thread(target=self._run_voice_action, daemon=True).start()

    @rumps.clicked("Stop Speaking")
    def on_stop(self, _):
        self._on_stop_hotkey()

    def on_about(self, _):
        rumps.alert(
            title=f"MacEyes {_VERSION}",
            message=_CHANGELOG,
            ok="OK",
        )

    def _on_stop_hotkey(self):
        """Stop speech and cancel any running action. Called from pynput's thread."""
        # TODO: pressing stop while _speak() is mid-utterance (e.g. "Say your command")
        # still crashes intermittently. The root cause appears to be a race between
        # terminating self._say_proc here and _speak() starting the "over" process
        # on the voice-action thread — likely a PyAudio/sr teardown issue. Needs
        # investigation with crash logs to pinpoint the exact fault.
        try:
            self._cancel.set()
            if self._say_proc and self._say_proc.poll() is None:
                self._say_proc.terminate()
            if self._busy:
                # Title update must happen on the main thread — dispatch via NSOperationQueue
                from Foundation import NSOperationQueue
                NSOperationQueue.mainQueue().addOperationWithBlock_(
                    lambda: setattr(self, "title", "🛑")
                )
                _speak_async("Stopping")
        except Exception:
            pass

    def _on_voice_action_hotkey(self):
        """Trigger Voice Action from the global hotkey."""
        self.on_voice_action(None)

    def _build_hotkey_listener(self) -> keyboard.GlobalHotKeys:
        """Build a fresh GlobalHotKeys listener with the current settings."""
        return keyboard.GlobalHotKeys({
            _hotkey_to_pynput(self._settings.stop_hotkey): self._on_stop_hotkey,
            _hotkey_to_pynput(self._settings.voice_action_hotkey): self._on_voice_action_hotkey,
        })

    def _stop_hotkey_menu_label(self) -> str:
        return f"Stop Hotkey: {self._settings.stop_hotkey}"

    def _voice_hotkey_menu_label(self) -> str:
        return f"Voice Action Hotkey: {self._settings.voice_action_hotkey}"

    def _prompt_hotkey(self, title: str, current: str) -> str | None:
        """Show a dialog to enter a new hotkey. Returns the new value or None if cancelled."""
        window = rumps.Window(
            message="Use modifier names: cmd, ctrl, shift, alt\nExample: cmd+shift+v  or  ctrl+shift+x",
            title=title,
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(260, 24),
        )
        response = window.run()
        if not response.clicked:
            return None

        new_hotkey = response.text.strip().lower()
        if not new_hotkey:
            return None

        try:
            keyboard.HotKey.parse(_hotkey_to_pynput(new_hotkey))
        except Exception as exc:
            rumps.alert(title="Invalid Hotkey", message=str(exc))
            return None

        return new_hotkey

    def on_set_stop_hotkey(self, _):
        new_hotkey = self._prompt_hotkey("Set Stop Hotkey", self._settings.stop_hotkey)
        if new_hotkey is None:
            return
        self._hotkey_listener.stop()
        self._settings.stop_hotkey = new_hotkey
        self._hotkey_listener = self._build_hotkey_listener()
        self._hotkey_listener.start()
        self._stop_hotkey_item.title = self._stop_hotkey_menu_label()

    def on_set_voice_hotkey(self, _):
        new_hotkey = self._prompt_hotkey("Set Voice Action Hotkey", self._settings.voice_action_hotkey)
        if new_hotkey is None:
            return
        self._hotkey_listener.stop()
        self._settings.voice_action_hotkey = new_hotkey
        self._hotkey_listener = self._build_hotkey_listener()
        self._hotkey_listener.start()
        self._voice_hotkey_item.title = self._voice_hotkey_menu_label()

    def _api_key_menu_label(self) -> str:
        return "API Key: (set)" if self._settings.api_key else "API Key: (using env)"

    def on_set_api_key(self, _):
        current = self._settings.api_key or ""
        window = rumps.Window(
            message="Paste your Anthropic API key below. Leave blank to clear and fall back to the ANTHROPIC_API_KEY environment variable.",
            title="Set Anthropic API Key",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(380, 24),
        )
        response = window.run()
        if not response.clicked:
            return
        value = response.text.strip()
        self._settings.api_key = value if value else None
        self._api_key_item.title = self._api_key_menu_label()

    def _say_over_menu_label(self) -> str:
        return "Say 'Over' When Done: On" if self._settings.say_over else "Say 'Over' When Done: Off"

    def on_toggle_say_over(self, _):
        self._settings.say_over = not self._settings.say_over
        self._say_over_item.title = self._say_over_menu_label()

    def _computer_use_model_menu_label(self) -> str:
        model = self._settings.computer_use_model
        label = "Sonnet" if "sonnet" in model else "Haiku"
        return f"Action Model: {label}"

    def on_toggle_computer_use_model(self, _):
        if "haiku" in self._settings.computer_use_model:
            self._settings.computer_use_model = "claude-sonnet-4-5"
        else:
            self._settings.computer_use_model = "claude-haiku-4-5-20251001"
        self._computer_use_model_item.title = self._computer_use_model_menu_label()

    def _speak(self, text: str) -> None:
        """Speak text, then say 'over' if that setting is enabled and not cancelled."""
        self._say_proc = _speak_async(text)
        self._say_proc.wait()
        if self._settings.say_over and not self._cancel.is_set():
            self._say_proc = _speak_async("over")
            self._say_proc.wait()
    def _max_iterations_menu_label(self) -> str:
        return f"Voice Action Max Iterations: {self._settings.computer_use_max_iterations}"

    def _max_tokens_menu_label(self) -> str:
        return f"Voice Action Max Tokens: {self._settings.computer_use_max_tokens}"

    def on_set_max_iterations(self, _):
        window = rumps.Window(
            message="Max number of reasoning steps per Voice Action (default: 20). Lower = cheaper, higher = more capable.",
            title="Set Max Iterations",
            default_text=str(self._settings.computer_use_max_iterations),
            ok="Save",
            cancel="Cancel",
            dimensions=(120, 24),
        )
        response = window.run()
        if not response.clicked:
            return
        try:
            value = int(response.text.strip())
            if value < 1:
                raise ValueError
        except ValueError:
            rumps.notification("MacEyes", "Invalid value", "Please enter a positive integer.")
            return
        self._settings.computer_use_max_iterations = value
        self._max_iterations_item.title = self._max_iterations_menu_label()

    def _launch_at_login_menu_label(self) -> str:
        return "Start on Login: On" if _launch_at_login_enabled() else "Start on Login: Off"

    def on_toggle_launch_at_login(self, _):
        enabled = not _launch_at_login_enabled()
        try:
            _set_launch_at_login(enabled)
        except Exception as exc:
            rumps.alert(title="Launch at Login Error", message=str(exc))
            return
        self._launch_at_login_item.title = self._launch_at_login_menu_label()

    def on_set_max_tokens(self, _):
        window = rumps.Window(
            message="Max tokens per Voice Action API call (default: 4096). Lower = cheaper, higher = allows longer responses.",
            title="Set Max Tokens",
            default_text=str(self._settings.computer_use_max_tokens),
            ok="Save",
            cancel="Cancel",
            dimensions=(120, 24),
        )
        response = window.run()
        if not response.clicked:
            return
        try:
            value = int(response.text.strip())
            if value < 1:
                raise ValueError
        except ValueError:
            rumps.notification("MacEyes", "Invalid value", "Please enter a positive integer.")
            return
        self._settings.computer_use_max_tokens = value
        self._max_tokens_item.title = self._max_tokens_menu_label()

    def _run(self, capture_fn=None):
        self._cancel.clear()
        try:
            _speak_async("Analyzing screen...").wait()
            tones = _WorkingTones()
            tones.start()
            try:
                img = (capture_fn or _capture_screen)()
                desc = _describe(img)
            finally:
                tones.stop()
            self._speak(desc)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            rumps.notification("MacEyes", "Error", str(exc))
        finally:
            self._busy = False
            self.title = "👁"

    def _run_voice_action(self):
        self._cancel.clear()
        try:
            self._speak("Listening. Say your command.")

            if self._cancel.is_set():
                return

            command = _listen_for_command()
            command = _resolve_app_names(command)

            self._speak(f"Got it. {command}. Working on it.")

            tones = _WorkingTones()
            tones.start()
            try:
                result = _run_computer_use(command, self._cancel)
            finally:
                tones.stop()

            if self._cancel.is_set():
                return

            self._speak(result)
        except sr.WaitTimeoutError:
            self._speak("No speech detected. Please try again.")
        except sr.UnknownValueError:
            self._speak("Sorry, I couldn't understand that. Please try again.")
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            rumps.notification("MacEyes", "Voice Action Error", str(exc))
        finally:
            self._busy = False
            self.title = "👁"


class _WorkingTones:
    """Plays soft repeating tones in the background while Claude is working.

    A short sine-wave pulse (220 Hz, ~120 ms) fires every 3 seconds at low
    volume so the user knows processing is still in progress.
    """

    _SAMPLE_RATE = 44100
    _FREQ = 220          # Hz — low A, unobtrusive
    _DURATION = 0.12     # seconds per pulse
    _INTERVAL = 3.0      # seconds between pulses
    _VOLUME = 0.08       # 0.0–1.0

    def __init__(self):
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)

    def _loop(self):
        try:
            import pyaudio
            pa = pyaudio.PyAudio()
            stream = pa.open(
                format=pyaudio.paInt16,
                channels=1,
                rate=self._SAMPLE_RATE,
                output=True,
            )
            try:
                while not self._stop.wait(timeout=self._INTERVAL):
                    stream.write(self._make_pulse())
            finally:
                stream.stop_stream()
                stream.close()
                pa.terminate()
        except Exception:
            pass  # never let tone errors surface to the user

    def _make_pulse(self) -> bytes:
        n = int(self._SAMPLE_RATE * self._DURATION)
        frames = []
        for i in range(n):
            # sine wave with a simple linear fade-in/out envelope
            t = i / self._SAMPLE_RATE
            envelope = min(i, n - i) / (n * 0.15)
            envelope = min(envelope, 1.0)
            sample = self._VOLUME * envelope * math.sin(2 * math.pi * self._FREQ * t)
            frames.append(struct.pack("<h", int(sample * 32767)))
        return b"".join(frames)


def _listen_for_command() -> str:
    """Record from the microphone and return transcribed text."""
    recognizer = sr.Recognizer()
    with sr.Microphone() as source:
        recognizer.adjust_for_ambient_noise(source, duration=0.5)
        audio = recognizer.listen(source, timeout=10, phrase_time_limit=15)
    return recognizer.recognize_google(audio)


def _get_screen_size() -> tuple[int, int]:
    """Return the main display resolution as (width, height)."""
    display_id = Quartz.CGMainDisplayID()
    return (
        Quartz.CGDisplayPixelsWide(display_id),
        Quartz.CGDisplayPixelsHigh(display_id),
    )


def _execute_computer_tool(action: str, params: dict) -> list | str:
    """Execute a computer_20250124 tool action and return content for the tool result."""
    if action == "screenshot":
        img = _capture_screen()
        return [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img}}]

    if action in ("left_click", "right_click", "middle_click", "double_click"):
        x, y = params["coordinate"]
        btn = {"left_click": "left", "right_click": "right",
               "middle_click": "middle", "double_click": "left"}[action]
        pyautogui.click(x, y, button=btn, clicks=2 if action == "double_click" else 1)
        return "OK"

    if action == "left_click_drag":
        sx, sy = params["start_coordinate"]
        ex, ey = params["coordinate"]
        pyautogui.mouseDown(sx, sy, button="left")
        pyautogui.moveTo(ex, ey, duration=0.3)
        pyautogui.mouseUp(button="left")
        return "OK"

    if action == "mouse_move":
        x, y = params["coordinate"]
        pyautogui.moveTo(x, y)
        return "OK"

    if action == "type":
        text = params.get("text", "")
        # Use pbpaste/pbcopy trick for reliable unicode input on macOS
        proc = subprocess.run(
            ["osascript", "-e", f'tell application "System Events" to keystroke "{text}"'],
            capture_output=True,
        )
        if proc.returncode != 0:
            # Fallback for text without special chars
            pyautogui.write(text, interval=0.02)
        return "OK"

    if action == "key":
        raw = params.get("text", "")
        keys = [_KEY_MAP.get(k, k.lower()) for k in raw.split("+")]
        pyautogui.hotkey(*keys)
        return "OK"

    if action == "scroll":
        x, y = params["coordinate"]
        direction = params.get("direction", "down")
        amount = int(params.get("amount", 3))
        # pyautogui scroll: positive = up, negative = down
        clicks = amount if direction == "up" else -amount
        if direction in ("left", "right"):
            pyautogui.hscroll(clicks if direction == "right" else -clicks, x=x, y=y)
        else:
            pyautogui.scroll(clicks, x=x, y=y)
        return "OK"

    if action == "cursor_position":
        x, y = pyautogui.position()
        return f"X={x},Y={y}"

    return f"Unknown action: {action}"


def _execute_bash_tool(command: str) -> str:
    """Run a bash command and return combined stdout+stderr (truncated)."""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        output = (result.stdout + result.stderr).strip()
        return output[:4000] if output else "(no output)"
    except subprocess.TimeoutExpired:
        return "Command timed out after 30 seconds."


def _run_computer_use(request: str, cancel: threading.Event | None = None) -> str:
    """Drive a Claude computer-use loop to fulfil the user's spoken request."""
    w, h = _get_screen_size()
    tools = [
        {
            "type": "computer_20250124",
            "name": "computer",
            "display_width_px": w,
            "display_height_px": h,
        },
        {"type": "bash_20250124", "name": "bash"},
    ]
    messages = [{"role": "user", "content": request}]

    max_iterations = _settings_instance.computer_use_max_iterations if "_settings_instance" in globals() else 20
    max_tokens = _settings_instance.computer_use_max_tokens if "_settings_instance" in globals() else 4096
    for _ in range(max_iterations):
        if cancel and cancel.is_set():
            return "Cancelled."
        response = _get_client().beta.messages.create(
            model=_settings_instance.computer_use_model if "_settings_instance" in globals() else "claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            system=VOICE_ACTION_SYSTEM,
            tools=tools,
            messages=messages,
            betas=["computer-use-2025-01-24"],
        )

        # Append assistant turn (keep raw content blocks for the API)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            return next(
                (b.text for b in response.content if hasattr(b, "text") and b.text),
                "Task completed.",
            )

        # Execute all tool calls and collect results
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == "computer":
                content = _execute_computer_tool(block.input.get("action", ""), block.input)
            elif block.name == "bash":
                content = _execute_bash_tool(block.input.get("command", ""))
            else:
                content = f"Unknown tool: {block.name}"

            if isinstance(content, str):
                content = [{"type": "text", "text": content}]

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content,
            })

        messages.append({"role": "user", "content": tool_results})

    return "Task loop reached maximum iterations. Stopping."


def _downscale_screenshot(path: str, max_px: int = 1280) -> None:
    """Resize image in-place so its longest side is at most max_px, using sips."""
    subprocess.run(
        ["sips", "-Z", str(max_px), path],
        check=True, capture_output=True,
    )


def _capture_screen() -> str:
    """Capture the full screen and return a base64-encoded PNG."""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        # -x: no screenshot sound; -D 1: main display only
        subprocess.run(["screencapture", "-x", "-D", "1", path], check=True)
        _downscale_screenshot(path)
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode()
    finally:
        os.unlink(path)


def _capture_active_window() -> str:
    """Capture the frontmost window and return a base64-encoded PNG."""
    window_list = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    )
    # Windows are returned front-to-back; pick the first normal-layer window
    window_id = next(
        (w["kCGWindowNumber"] for w in window_list if w.get("kCGWindowLayer") == 0),
        None,
    )
    if window_id is None:
        raise RuntimeError("Could not find an active window")

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(["screencapture", "-x", "-l", str(window_id), path], check=True)
        _downscale_screenshot(path)
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode()
    finally:
        os.unlink(path)


def _describe(image_b64: str) -> str:
    """Send screenshot to Claude via vision and return a spoken description."""
    with _get_client().messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": DESCRIBE_PROMPT},
                ],
            }
        ],
    ) as stream:
        final = stream.get_final_message()
        return next(b.text for b in final.content if b.type == "text")


def _speak_async(text: str) -> subprocess.Popen:
    """Start speaking text with macOS say and return the process."""
    return subprocess.Popen(["say", text])


if __name__ == "__main__":
    MacEyesApp().run()
