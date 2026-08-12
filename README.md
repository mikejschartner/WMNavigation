# WMNavigation

Live Tarkov map companion — player position from screenshots, auto map switch from logs, extract/loot markers.

## Launch

Double-click **WMNavigation.exe** or **Launch WMNavigation.bat**.

## Auto-update

When a new GitHub release is published (`vX.Y.Z` tag), packaged builds
check `mikejschartner/WMNavigation` shortly after launch and **auto-download +
restart** when a newer edition is available. Dev (source) runs still prompt.

Publish a release:

```bash
git tag v0.4.0
git push origin v0.4.0
```

GitHub Actions builds `WMNavigation.exe` and uploads it with `latest.json`.

## v0.4.0

- Marker size slider; markers keep a constant screen size while the map zooms
- Wiki loot/key images (fandom) with tarkov.dev 512px fallback
- Tiny pin under each item hunt marker for the exact spawn
- GitHub release auto-updater
- Highest-value selected item shown per loose-loot spot

## Dev run

```bash
pip install -r requirements.txt
python WMNavigation.py
```

## Build exe

```bash
pip install pyinstaller
pyinstaller build.spec
```

Output: `dist/WMNavigation.exe` — copy to project root for one-click launch.
