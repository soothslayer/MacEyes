#!/usr/bin/env python3
"""MacEyes — AI-powered screen description via voice."""

import anthropic
import base64
import os
import subprocess
import tempfile
import threading

import Quartz
import rumps

client = anthropic.Anthropic()

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


class MacEyesApp(rumps.App):
    def __init__(self):
        super().__init__("👁", quit_button="Quit MacEyes")
        self.menu = [
            rumps.MenuItem("Describe Screen", callback=self.on_describe),
            rumps.MenuItem("Describe Active Window", callback=self.on_describe_window),
            rumps.MenuItem("Stop Speaking", callback=self.on_stop),
        ]
        self._busy = False
        self._say_proc: subprocess.Popen | None = None

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

    @rumps.clicked("Stop Speaking")
    def on_stop(self, _):
        if self._say_proc and self._say_proc.poll() is None:
            self._say_proc.terminate()

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
