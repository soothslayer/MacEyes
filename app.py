#!/usr/bin/env python3
"""MacEyes — AI-powered screen description via voice."""

import anthropic
import base64
import json
import os
import subprocess
import tempfile
import threading

import Quartz
import pyautogui
import rumps
import speech_recognition as sr

pyautogui.PAUSE = 0.1
pyautogui.FAILSAFE = False

CONFIG_PATH = os.path.expanduser("~/.config/maceyes/config.json")


def _load_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH) as f:
            return json.load(f)
    return {}


def _save_config(config: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def _make_client() -> anthropic.Anthropic:
    """Build an Anthropic client from saved config or environment variables."""
    config = _load_config()
    auth_type = config.get("auth_type", "api_key")
    if auth_type == "auth_token" and config.get("auth_token"):
        return anthropic.Anthropic(auth_token=config["auth_token"])
    if config.get("api_key"):
        return anthropic.Anthropic(api_key=config["api_key"])
    # Fall back to ANTHROPIC_API_KEY env var (default SDK behaviour)
    return anthropic.Anthropic()


client = _make_client()

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
        self.menu = [
            rumps.MenuItem("Describe Screen", callback=self.on_describe),
            rumps.MenuItem("Describe Active Window", callback=self.on_describe_window),
            rumps.MenuItem("Voice Action", callback=self.on_voice_action),
            rumps.MenuItem("Stop Speaking", callback=self.on_stop),
            None,  # separator
            rumps.MenuItem("Set API Key…", callback=self.on_set_api_key),
            rumps.MenuItem("Set Auth Token…", callback=self.on_set_auth_token),
        ]
        self._busy = False
        self._say_proc: subprocess.Popen | None = None
        self._update_auth_checkmarks()

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
        if self._say_proc and self._say_proc.poll() is None:
            self._say_proc.terminate()

    @rumps.clicked("Set API Key…")
    def on_set_api_key(self, _):
        config = _load_config()
        current = config.get("api_key", "")
        win = rumps.Window(
            message="Enter your Anthropic API key (sk-ant-…):",
            title="MacEyes — Set API Key",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(420, 24),
        )
        response = win.run()
        if response.clicked and response.text.strip():
            config["api_key"] = response.text.strip()
            config["auth_type"] = "api_key"
            _save_config(config)
            global client
            client = _make_client()
            self._update_auth_checkmarks()
            rumps.notification("MacEyes", "Auth", "API key saved and active.")

    @rumps.clicked("Set Auth Token…")
    def on_set_auth_token(self, _):
        config = _load_config()
        current = config.get("auth_token", "")
        win = rumps.Window(
            message="Enter your Anthropic OAuth auth token:",
            title="MacEyes — Set Auth Token",
            default_text=current,
            ok="Save",
            cancel="Cancel",
            dimensions=(420, 24),
        )
        response = win.run()
        if response.clicked and response.text.strip():
            config["auth_token"] = response.text.strip()
            config["auth_type"] = "auth_token"
            _save_config(config)
            global client
            client = _make_client()
            self._update_auth_checkmarks()
            rumps.notification("MacEyes", "Auth", "Auth token saved and active.")

    def _update_auth_checkmarks(self):
        """Show a checkmark next to whichever auth method is currently active."""
        config = _load_config()
        auth_type = config.get("auth_type", "api_key")
        self.menu["Set API Key…"].state = auth_type == "api_key" and bool(config.get("api_key"))
        self.menu["Set Auth Token…"].state = auth_type == "auth_token" and bool(config.get("auth_token"))

    def _run(self, capture_fn=None):
        try:
            _speak_async("Analyzing screen...").wait()
            img = (capture_fn or _capture_screen)()
            desc = _describe(img)
            self._say_proc = _speak_async(desc)
            self._say_proc.wait()
        except Exception as exc:
            rumps.notification("MacEyes", "Error", str(exc))
        finally:
            self._busy = False
            self.title = "👁"

    def _run_voice_action(self):
        try:
            self._say_proc = _speak_async("Listening. Say your command.")
            self._say_proc.wait()

            command = _listen_for_command()

            self._say_proc = _speak_async(f"Got it. {command}. Working on it.")
            self._say_proc.wait()

            result = _run_computer_use(command)

            self._say_proc = _speak_async(result)
            self._say_proc.wait()
        except sr.WaitTimeoutError:
            _speak_async("No speech detected. Please try again.").wait()
        except sr.UnknownValueError:
            _speak_async("Sorry, I couldn't understand that. Please try again.").wait()
        except Exception as exc:
            rumps.notification("MacEyes", "Voice Action Error", str(exc))
        finally:
            self._busy = False
            self.title = "👁"


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


def _run_computer_use(request: str) -> str:
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

    for _ in range(20):  # safety cap on iterations
        response = client.beta.messages.create(
            model="claude-opus-4-6",
            max_tokens=4096,
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


def _capture_screen() -> str:
    """Capture the full screen and return a base64-encoded PNG."""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        # -x: no screenshot sound; -D 1: main display only
        subprocess.run(["screencapture", "-x", "-D", "1", path], check=True)
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
        with open(path, "rb") as f:
            return base64.standard_b64encode(f.read()).decode()
    finally:
        os.unlink(path)


def _describe(image_b64: str) -> str:
    """Send screenshot to Claude via vision and return a spoken description."""
    with client.messages.stream(
        model="claude-opus-4-6",
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
