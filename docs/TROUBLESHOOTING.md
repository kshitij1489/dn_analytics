# Troubleshooting

## "App cannot be opened" on macOS

If you receive the error **"D&N Analytics" is damaged and can't be opened** or **"D&N Analytics" cannot be opened because it is from an unidentified developer**, this is due to macOS security settings (Gatekeeper) for apps distributed outside the App Store.

### Solution

The recipient needs to remove the "quarantine" attribute from the downloaded app. Ask them to follow these steps:

1.  Move the app to the **Applications** folder (if not already there).
2.  Open the **Terminal** app.
3.  Copy and paste the following command and press **Enter**:

    ```bash
    sudo xattr -r -d com.apple.quarantine /Applications/D\&N\ Analytics.app
    ```

    *(Note: They may be asked to enter their Mac password. It won't show up while typing, which is normal.)*

4.  Try opening the app again.

### Advanced: Re-sign the App (If the above doesn't work)
On Apple Silicon (M1/M2/M3), if the app still won't open or says "Damaged", the ad-hoc signature might need to be refreshed. Run this command in Terminal **after** running the command above:

```bash
codesign --force --deep --sign - /Applications/D\&N\ Analytics.app
```

### Alternative (Right-Click)
Sometimes, simply **Right-clicking** (or Control-clicking) the app and selecting **Open** initiates a one-time prompt allowing you to open the app. This works for "Unidentified Developer" errors but may not work for "Damaged" errors on newer macOS versions.

---

## After Installing an Update

If you downloaded and installed an update via the in-app updater, the new version may trigger macOS Gatekeeper again. Run the same commands to clear the quarantine flag:

```bash
sudo xattr -r -d com.apple.quarantine /Applications/D\&N\ Analytics.app
codesign --force --deep --sign - /Applications/D\&N\ Analytics.app
```
