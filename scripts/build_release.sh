#!/bin/bash
set -e # Exit on error

# Directory definitions
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALLER_DIR="$PROJECT_ROOT/installer"
UI_DIR="$PROJECT_ROOT/ui_electron"
DIST_BACKEND="$PROJECT_ROOT/dist-backend"
BUILD_BACKEND="$PROJECT_ROOT/build-backend"

echo "=========================================="
echo "   Analytics App Build & Release Script   "
echo "=========================================="
echo "Project Root: $PROJECT_ROOT"

# 1. Clean previous builds
echo "Cleaning previous builds..."
rm -rf "$DIST_BACKEND"
rm -rf "$BUILD_BACKEND"
rm -rf "$UI_DIR/dist"
rm -rf "$UI_DIR/dist_electron"

# 2. Build Python Backend
echo "Building Python Backend..."
# Ensure we are in the virtualenv
if [ -d "$PROJECT_ROOT/.venv" ]; then
    source "$PROJECT_ROOT/.venv/bin/activate"
else
    echo "Error: Virtual environment not found at .venv"
    exit 1
fi

# Install requirements (ensure everything is up to date)
pip install -r "$PROJECT_ROOT/requirements.txt"

# Run PyInstaller
# We run from INSTALLER_DIR so the spec file finds 'backend_entry.py' easily alongside it
# But we need to make sure the paths in spec file (which assume root is one level up) are respected.
# Actually, my spec file has `project_root = os.path.abspath(os.path.join(os.getcwd(), '..'))`
# So I should run it from `installer` directory.

cd "$INSTALLER_DIR"
pyinstaller backend.spec \
    --distpath "$DIST_BACKEND" \
    --workpath "$BUILD_BACKEND" \
    --noconfirm \
    --clean

echo "Backend build complete. Output in: $DIST_BACKEND"

# 3. Build Frontend & Package Electron
echo "Building Electron App..."
cd "$UI_DIR"

if [ ! -d "node_modules" ]; then
    npm install
fi

# Build Vite App
npm run build

# Package with Electron Builder
echo "Packaging for Mac (arm64)..."
npm run package:mac

echo "=========================================="
echo "   Build Success!                         "
echo "   Artifact: $UI_DIR/dist_electron/*.dmg  "
echo "=========================================="
