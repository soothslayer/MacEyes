#!/bin/bash
# Build MacEyes.app and package it as a DMG (and optionally a PKG installer).
#
# Usage:
#   ./build_dmg.sh           # builds MacEyes.app + MacEyes.dmg
#   ./build_dmg.sh --pkg     # also creates MacEyes.pkg
#
# Requirements:
#   - macOS with Xcode Command Line Tools (xcode-select --install)
#   - Python 3.11+ (brew install python@3.11)
#   - Homebrew flac for Apple Silicon: brew install flac
#
# The finished artefacts land in dist/:
#   dist/MacEyes.app          — standalone app bundle
#   dist/MacEyes.dmg          — drag-to-Applications disk image
#   dist/MacEyes.pkg          — flat installer package (with --pkg flag)
#
# To sign / notarise for distribution outside the App Store, set:
#   DEVELOPER_ID="Developer ID Application: Your Name (TEAMID)"
# before running this script.

set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────────
APP_NAME="MacEyes"
APP_VERSION="1.0.0"
BUNDLE_ID="com.maceyes.app"
PYTHON="${PYTHON:-python3}"
BUILD_PKG=false
DEVELOPER_ID="${DEVELOPER_ID:-}"

# ── Parse args ────────────────────────────────────────────────────────────────
for arg in "$@"; do
  case $arg in
    --pkg) BUILD_PKG=true ;;
    *) echo "Unknown argument: $arg" && exit 1 ;;
  esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
info()  { echo "▶ $*"; }
ok()    { echo "✓ $*"; }
warn()  { echo "⚠ $*" >&2; }
die()   { echo "✗ $*" >&2; exit 1; }

# ── Preflight ─────────────────────────────────────────────────────────────────
info "Checking prerequisites…"
command -v "$PYTHON"   >/dev/null || die "Python not found. Install with: brew install python@3.11"
command -v hdiutil     >/dev/null || die "hdiutil not found (requires macOS)"
[[ "$(uname)" == "Darwin" ]] || die "This script must run on macOS."

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d venv ]]; then
  info "Creating virtual environment…"
  "$PYTHON" -m venv venv
fi
source venv/bin/activate

# ── Install dependencies ──────────────────────────────────────────────────────
info "Installing runtime dependencies…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

info "Installing py2app build tool…"
pip install --quiet py2app

# ── Fix Apple Silicon flac binary ─────────────────────────────────────────────
FLAC_MAC="venv/lib/$(ls venv/lib)/site-packages/speech_recognition/flac-mac"
if [[ -f "$FLAC_MAC" ]] && ! file "$FLAC_MAC" | grep -q arm64; then
  BREW_FLAC="$(brew --prefix 2>/dev/null)/bin/flac"
  if [[ -f "$BREW_FLAC" ]]; then
    cp "$BREW_FLAC" "$FLAC_MAC"
    ok "Replaced Intel flac-mac with ARM64 binary from Homebrew."
  else
    warn "flac-mac is Intel-only and Homebrew flac was not found."
    warn "Voice Action may fail on Apple Silicon. Run: brew install flac"
  fi
fi

# ── Clean previous build ──────────────────────────────────────────────────────
info "Cleaning previous build artefacts…"
rm -rf build dist

# ── Fix rubicon namespace package for py2app ──────────────────────────────────
# rubicon-objc is a PEP 420 namespace package (no __init__.py) which py2app's
# modulegraph cannot locate via imp.find_module.  A stub fixes the lookup.
RUBICON_INIT="venv/lib/$(ls venv/lib)/site-packages/rubicon/__init__.py"
if [[ ! -f "$RUBICON_INIT" ]]; then
  echo "# stub created by build_dmg.sh to make rubicon visible to py2app/modulegraph" > "$RUBICON_INIT"
  ok "Created rubicon stub __init__.py for py2app compatibility."
fi

# ── Build .app bundle ─────────────────────────────────────────────────────────
info "Building ${APP_NAME}.app with py2app (this takes a few minutes)…"
python setup.py py2app 2>&1

APP_PATH="dist/${APP_NAME}.app"
[[ -d "$APP_PATH" ]] || die "py2app finished but ${APP_PATH} was not created."
ok "Built ${APP_PATH}"

# ── Fix flac-mac inside the .app bundle ───────────────────────────────────────
BUNDLE_FLAC="${APP_PATH}/Contents/Resources/lib/python$(python -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')/speech_recognition/flac-mac"
if [[ -f "$BUNDLE_FLAC" ]] && ! file "$BUNDLE_FLAC" | grep -q arm64; then
  BREW_FLAC="$(brew --prefix 2>/dev/null)/bin/flac"
  if [[ -f "$BREW_FLAC" ]]; then
    cp "$BREW_FLAC" "$BUNDLE_FLAC"
    ok "Replaced bundled Intel flac-mac with ARM64 binary."
  else
    warn "Bundled flac-mac is Intel-only. Voice Action may fail on Apple Silicon."
  fi
fi

# ── Code-sign (optional) ──────────────────────────────────────────────────────
if [[ -n "$DEVELOPER_ID" ]]; then
  info "Signing ${APP_NAME}.app as '${DEVELOPER_ID}'…"
  codesign --deep --force --options runtime \
    --sign "$DEVELOPER_ID" \
    "$APP_PATH"
  ok "Signed."
else
  warn "DEVELOPER_ID not set — app bundle is unsigned."
  warn "Users may need to right-click → Open the first time, or run:"
  warn "  xattr -dr com.apple.quarantine /Applications/${APP_NAME}.app"
fi

# ── Build DMG ─────────────────────────────────────────────────────────────────
DMG_PATH="dist/${APP_NAME}.dmg"
DMG_STAGING="dist/dmg_staging"
info "Creating ${DMG_PATH}…"

rm -rf "$DMG_STAGING"
mkdir -p "$DMG_STAGING"
cp -R "$APP_PATH" "$DMG_STAGING/"
# Symlink to /Applications so the user can drag-and-drop
ln -s /Applications "$DMG_STAGING/Applications"

hdiutil create \
  -volname "${APP_NAME} ${APP_VERSION}" \
  -srcfolder "$DMG_STAGING" \
  -ov \
  -format UDZO \
  -imagekey zlib-level=9 \
  "$DMG_PATH"

rm -rf "$DMG_STAGING"
ok "Created ${DMG_PATH}"

# ── Build PKG (optional) ──────────────────────────────────────────────────────
if $BUILD_PKG; then
  PKG_ROOT="dist/pkg_root"
  PKG_PATH="dist/${APP_NAME}.pkg"

  info "Creating ${PKG_PATH}…"
  mkdir -p "${PKG_ROOT}/Applications"
  cp -R "$APP_PATH" "${PKG_ROOT}/Applications/"

  pkgbuild \
    --root "$PKG_ROOT" \
    --identifier "$BUNDLE_ID" \
    --version "$APP_VERSION" \
    --install-location "/" \
    "$PKG_PATH"

  rm -rf "$PKG_ROOT"

  if [[ -n "$DEVELOPER_ID" ]]; then
    SIGN_ID="${DEVELOPER_ID/Application/Installer}"
    productsign --sign "$SIGN_ID" "$PKG_PATH" "${PKG_PATH%.pkg}-signed.pkg" \
      && mv "${PKG_PATH%.pkg}-signed.pkg" "$PKG_PATH" \
      && ok "Signed ${PKG_PATH}."
  fi

  ok "Created ${PKG_PATH}"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "────────────────────────────────────────────"
echo " Build complete!"
echo ""
echo " Artefacts in dist/:"
ls -lh dist/*.app dist/*.dmg dist/*.pkg 2>/dev/null | awk '{print "   "$NF, $5}'
echo ""
echo " To install: open dist/${APP_NAME}.dmg and drag to Applications."
if $BUILD_PKG; then
  echo " Or double-click dist/${APP_NAME}.pkg to install via Installer.app."
fi
echo ""
echo " First launch: the app will appear as 👁 in the menu bar."
echo " Open Settings → API Key to enter your Anthropic API key."
echo "────────────────────────────────────────────"
