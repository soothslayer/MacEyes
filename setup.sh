#!/bin/bash
# MacEyes setup
set -e

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Fix SpeechRecognition's bundled flac-mac binary — it's Intel-only and crashes on Apple Silicon.
# Replace it with the native ARM64 flac from Homebrew if available.
FLAC_MAC="venv/lib/$(ls venv/lib)/site-packages/speech_recognition/flac-mac"
if [ -f "$FLAC_MAC" ] && [ "$(file "$FLAC_MAC" | grep -c arm64)" -eq 0 ]; then
    BREW_FLAC="$(brew --prefix 2>/dev/null)/bin/flac"
    if [ -f "$BREW_FLAC" ]; then
        cp "$BREW_FLAC" "$FLAC_MAC"
        echo "Replaced flac-mac with ARM64 binary from Homebrew."
    else
        echo "Warning: flac-mac is Intel-only. Install flac via Homebrew to fix on Apple Silicon:"
        echo "  brew install flac"
    fi
fi

echo ""
echo "Setup complete. Run with:"
echo "  source venv/bin/activate && python app.py"
echo ""
echo "Make sure ANTHROPIC_API_KEY is set in your environment."
