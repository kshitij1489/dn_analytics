# Troubleshooting

## "App cannot be opened" on macOS

If you receive the error **"D&N Analytics" is damaged and can't be opened** or **"D&N Analytics" cannot be opened because it is from an unidentified developer**, this is due to macOS security settings (Gatekeeper) for apps distributed outside the App Store.

### Solution

The recipient needs to remove the "quarantine" attribute from the downloaded app. Ask them to follow these steps:

1.  Move the app to the **Applications** folder (if not already there).
2.  Open **Terminal** and run:

    ```bash
    xattr -cr "/Applications/D&N Analytics.app"
    ```

    *(No `sudo` needed; `-cr` clears quarantine.)*

3.  Try opening the app again.

### Re-sign the app (if it still won’t open)

- **If you have the project source** (e.g. you’re the developer): from the project root, run `./scripts/install_to_applications.sh`. It copies the app to Applications, signs the backend and app correctly (without `--deep`), and clears quarantine.
- **If you only have the .app**: from Terminal, try re-signing the app only:
  ```bash
  codesign --force --sign - "/Applications/D&N Analytics.app"
  ```
  Avoid using `--deep`; it can break nested executables (like the bundled backend).

### Right-click Open
Right-click (or Control-click) the app > **Open** can sometimes bypass "Unidentified Developer" once; it may not fix "Damaged" on newer macOS.

---

## App opens but window never appears

The backend may be failing to start (e.g. read-only filesystem in the app bundle). Check the backend log:

```bash
cat ~/Library/Application\ Support/dn-analytics/backend.log
```

Look for `Spawn error:` or Python tracebacks. Common fix: rebuild and reinstall with `./scripts/install_to_applications.sh` so the backend gets a writable log path.

---

## After Installing an Update

If you installed an update via the in-app updater, Gatekeeper may block the new version. Clear quarantine (and re-sign if you have the project):

```bash
xattr -cr "/Applications/D&N Analytics.app"
# If you have the project: ./scripts/install_to_applications.sh
```
