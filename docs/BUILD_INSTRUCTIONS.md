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

Once the script finishes, artifacts are in `ui_electron/dist_electron/`:

- **DMG**: `dn-analytics-<version>-arm64-mac.dmg` (e.g. `dn-analytics-1.0.5-arm64-mac.dmg`)
- **Unpacked app**: `mac-arm64/D&N Analytics.app`

*(Version comes from `ui_electron/package.json`.)*

---

## 3. Install to Applications (developers)

If you are building locally and want to run from **Applications** with correct signing and no Gatekeeper issues, use the install script **after** the build:

```bash
./scripts/build_release.sh
./scripts/install_to_applications.sh
open "/Applications/D&N Analytics.app"
```

The script copies the app to `/Applications`, signs the backend executable and then the app bundle (without `--deep`), and clears quarantine. See `docs/TROUBLESHOOTING.md` if the app still won’t open.

---

## 4. How to Share & Open (recipients)

Since this app is **ad-hoc signed** (not notarized by Apple), Gatekeeper may block it on other Macs. See **§5** for the full steps when the DMG is used on another machine.

---

## 5. Running on another Mac (from the DMG)

When you copy the `.dmg` to a different Mac and want to run the app there:

1. **Open the DMG** (double-click it), then drag **D&N Analytics** into **Applications**. Replace the existing app if prompted.

2. **Try opening the app** (double-click or right-click → **Open**).  
   - If it opens, you’re done.

3. **If you see “damaged” or “unidentified developer”**, open **Terminal** and run:
   ```bash
   xattr -cr "/Applications/D&N Analytics.app"
   ```
   Then try opening the app again.

4. **If it still won’t open** (common on Apple Silicon):
   - If that Mac has the **project** (full repo): from the project root run `./scripts/install_to_applications.sh` so it signs and clears quarantine.
   - If you **only have the DMG** (no project): re-sign the app:
     ```bash
     codesign --force --sign - "/Applications/D&N Analytics.app"
     ```
     Then try opening again.

For more help, see `docs/TROUBLESHOOTING.md`.

---

## 6. How to Fully Uninstall

Dragging the app to the Trash removes the application itself; data is stored separately.

To **completely remove** the app and all its data:

1.  **Delete the app**: Drag `D&N Analytics` from Applications to Trash.
2.  **Delete data**: In Finder, press `Cmd + Shift + G`, go to `~/Library/Application Support/`, and delete the folder **`dn-analytics`**.

That folder contains `analytics.db`, `backend.log`, and `logs/errors.jsonl`.
