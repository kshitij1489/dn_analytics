# How to Build and Share the D&N Analytics Dashboard

## 1. Regenerate the .dmg (One-Click Build)

To rebuild the application from scratch (e.g., after making code changes), simply run the release script from the project root:

```bash
./scripts/build_release.sh
```

This script will automatically:
1.  **Clean** previous build artifacts.
2.  **Build the Python Backend** into a standalone executable (using PyInstaller).
3.  **Build the React Frontend** (using Vite).
4.  **Package everything** into a `.dmg` file (using Electron Builder).

**Time required**: ~2-3 minutes.

---

## 2. Locate the Output

Once the script finishes, your new `.dmg` file will be here:

```
ui_electron/dist_electron/D&N Analytics-0.0.0-arm64.dmg
```

*(The version number `0.0.0` comes from `ui_electron/package.json`)*

---

## 3. How to Share & Open (Important!)

Since this app is **ad-hoc signed** (not notarized by Apple), Gatekeeper will likely block it on other Macs, especially newer M1/M2/M3 models.

**Instructions for the recipient:**

1.  Drag the app to the **Applications** folder.
2.  **Try Right-Clicking**:
    *   Right-click (Control-click) the app icon > Select **Open** > Click **Open** in the dialog.
    *   *If this works, you are done.*

3.  **If "Right-Click" fails (or app says "Damaged"):**
    Run the following commands in **Terminal**:

    ```bash
    # 1. Remove Quarantine (Fixes "Damaged" or "Unidentified Developer")
    sudo xattr -r -d com.apple.quarantine /Applications/D\&N\ Analytics.app

    # 2. Re-sign locally (Required if it still fails on M1/M2/M3 Macs)
    codesign --force --deep --sign - /Applications/D\&N\ Analytics.app
    ```

    After running these, the app will open normally.

---

## 4. How to Fully Uninstall

Dragging the app to the Trash removes the application itself, but the data is stored separately to ensure it persists across updates.

To **completely remove** the app and all its data:

1.  **Delete the App**: Drag `D&N Analytics` from Applications to Bin/Trash.
2.  **Delete Data**:
    *   Open Finder.
    *   Press `Cmd + Shift + G` (Go to Folder).
    *   Type `~/Library/Application Support/` and press Enter.
    *   Find and delete the folder named **`D&N Analytics`**.

This folder contains the `analytics.db` database and application logs.
