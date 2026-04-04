#!/bin/bash
# MacEyes setup
set -e

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

echo ""
echo "Setup complete. Run with:"
echo "  source venv/bin/activate && python app.py"
echo ""
echo "Make sure ANTHROPIC_API_KEY is set in your environment."
