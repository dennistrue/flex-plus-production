# Flex Plus Production Bundle

This folder mirrors the Main Hub production workflow but is tailored for Flex Plus differences:

- Fixed SoftAP password (`12345678`) unless you override specific units via `bin/passwords.csv`.
- Factory SSID/serials use the pattern `Flex<batch>-<YY><MM><serial>` to keep Flex-specific labeling.
- macOS and Windows flashers (`flash_flex_plus.sh` / `flash_flex_plus.ps1`) burn flash encryption keys, write encrypted bundles, and optionally push SSIDs over Wi-Fi.
- The browser GUI (`flash_gui.py`) launches from the `Run Flex Plus GUI` scripts so operators never touch the CLI.

## Directory layout

```
flex-plus-production/
├── Run Flex Plus GUI.command      # macOS launcher
├── RunFlexPlusGUI.bat             # Windows launcher
├── bin/
│   ├── flash_flex_plus.sh         # macOS flashing helper
│   ├── flash_flex_plus.ps1        # Windows flashing helper
│   ├── flash_gui.py               # Browser UI that shells out to the helpers
│   ├── release/                   # Latest bundle from ../scripts/build_output.sh
│   ├── logs/                      # `flash_log.csv` accumulates here
│   ├── tools/                     # Populated with esptool + gen_factory_payload.py
│   ├── keys/                      # Place `flash_encryption_key.bin` here (gitignored)
│   └── passwords.csv.example      # Optional per-unit password overrides
└── .gitignore                     # Keeps secrets/logs out of git
```

## Updating the bundle

1. Make sure `keys/flash_encryption_key.bin` exists locally (generated via `espsecure.py generate_flash_encryption_key`).
2. From the repo root run `./scripts/build_output.sh`. The script builds firmware/SPIFFS, encrypts every artifact, copies the manifest plus generators into `bin/`, and refreshes the vendored esptool binaries for macOS (Intel + Apple) and Windows.
3. Commit & push this folder (or the eventual dedicated `flex-plus-production` subrepo) so operator machines can pull the newest release.

For a clean rebuild plus log capture run `./scripts/clean_build_release.sh`; logs land in `../release_logs/`.

## Password handling

Flex Plus uses a static SoftAP password. If you need overrides for certain batches, copy `bin/passwords.csv.example` to `bin/passwords.csv` and populate rows with `batch,serial,password`. The GUI falls back to `12345678` whenever an entry is missing or the CSV is absent.

## Operator workflow

1. Double-click `Run Flex Plus GUI.command` (macOS) or `RunFlexPlusGUI.bat` (Windows).
2. Enter the batch/year/month/serial; the GUI computes the Flex SSID/serial automatically.
3. Click *Flash* to kick off `flash_flex_plus.(sh|ps1)`.
4. The shell scripts pull the latest commit, ensure flash-encryption keys/efuses are in place, flash the encrypted bundle, (optionally) provision SSIDs by joining the Flex AP, and log the outcome to `bin/logs/flash_log.csv`.

## Installer wrappers

End users can run `installer/install_flex_plus.command` (macOS) or `installer/install_flex_plus.bat` from the parent repo. Those scripts clone the public production repo onto a workstation and immediately launch the GUI so stations stay up-to-date via `git pull`.
