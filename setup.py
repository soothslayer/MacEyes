"""py2app build configuration for MacEyes.

Usage:
    pip install py2app
    python setup.py py2app

Or use the build_dmg.sh script which handles everything end-to-end.
"""
from setuptools import setup

APP = ["app.py"]
DATA_FILES = []

OPTIONS = {
    # argv_emulation must be False for rumps menu-bar apps
    "argv_emulation": False,
    "plist": {
        # Hide from Dock — this is a menu bar (agent) app
        "LSUIElement": True,
        "CFBundleName": "MacEyes",
        "CFBundleDisplayName": "MacEyes",
        "CFBundleIdentifier": "com.maceyes.app",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "NSHumanReadableCopyright": "Copyright © 2024 MacEyes",
        # Privacy usage descriptions (macOS requires these for notarisation)
        "NSMicrophoneUsageDescription": (
            "MacEyes uses your microphone to listen for Voice Action commands."
        ),
        "NSAppleEventsUsageDescription": (
            "MacEyes sends Apple Events to control other applications on your behalf."
        ),
        # Launch at login helper key (optional — user can add via System Settings)
        "LSApplicationCategoryType": "public.app-category.utilities",
    },
    "packages": [
        "anthropic",
        "rumps",
        "Quartz",
        "speech_recognition",
        "pyaudio",
        "pyautogui",
        "pynput",
        # anthropic SDK transitive deps that py2app may miss
        "httpx",
        "httpcore",
        "certifi",
        "anyio",
        "sniffio",
        "distro",
        "jiter",
    ],
    "includes": [
        "_cffi_backend",
        "rumps",
    ],
    "excludes": [
        "tkinter",
        "matplotlib",
        "scipy",
        "numpy",
        "PIL",
        "IPython",
        "jupyter",
    ],
    # Bundle all site-packages so transitive deps are included
    "site_packages": True,
}

setup(
    name="MacEyes",
    version="1.0.0",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
