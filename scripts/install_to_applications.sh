#!/bin/bash
# Install D&N Analytics to /Applications and sign for local use (ad-hoc).
# Run after: ./scripts/build_release.sh

set -e
APP_NAME="D&N Analytics"
SOURCE_APP="ui_electron/dist_electron/mac-arm64/${APP_NAME}.app"
DEST_APP="/Applications/${APP_NAME}.app"
ENTITLEMENTS="ui_electron/build/entitlements.mac.plist"
BACKEND_BINARY="${DEST_APP}/Contents/Resources/analytics-backend/analytics-backend"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/.."

if [ ! -d "$SOURCE_APP" ]; then
  echo "Error: Built app not found at $SOURCE_APP"
  echo "Run ./scripts/build_release.sh first."
  exit 1
fi

echo "Removing old app..."
rm -rf "$DEST_APP"

echo "Copying app to /Applications..."
cp -R "$SOURCE_APP" /Applications/

echo "Ensuring backend is executable..."
chmod +x "$BACKEND_BINARY"

echo "Signing backend executable (with entitlements)..."
codesign --force --sign - --entitlements "$ENTITLEMENTS" "$BACKEND_BINARY"

echo "Signing app bundle (do NOT use --deep; preserves backend signature)..."
codesign --force --sign - --entitlements "$ENTITLEMENTS" "$DEST_APP"

echo "Clearing quarantine (avoids 'damaged' / Gatekeeper)..."
xattr -cr "$DEST_APP"

echo "Done. Open with: open \"$DEST_APP\""
