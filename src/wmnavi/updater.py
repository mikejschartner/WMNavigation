"""GitHub release auto-updater for WMNavigation.exe."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import requests

from . import __version__
from .paths import is_frozen

GITHUB_OWNER = "mikejschartner"
GITHUB_REPO = "WMNavigation"
LATEST_JSON_URL = (
    f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest/download/latest.json"
)
RELEASES_API = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"


def _parse_version(text: str) -> tuple[int, ...]:
    parts = []
    for chunk in (text or "0").lstrip("vV").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts or [0])


def _headers() -> dict[str, str]:
    return {"User-Agent": f"WMNavigation/{__version__}", "Accept": "application/json"}


def _info_from_release(release: dict) -> dict | None:
    version = (release.get("tag_name") or "").lstrip("vV")
    assets = release.get("assets") or []
    exe = next(
        (a for a in assets if str(a.get("name", "")).lower() == "wmnavigation.exe"),
        None,
    )
    if not exe:
        exe = next(
            (a for a in assets if str(a.get("name", "")).endswith(".exe")),
            None,
        )
    if not version or not exe:
        return None
    return {
        "version": version,
        "downloadUrl": exe.get("browser_download_url"),
        "releaseNotes": release.get("body") or "",
    }


def _fetch_latest_json(timeout: float) -> dict | None:
    try:
        resp = requests.get(LATEST_JSON_URL, timeout=timeout, headers=_headers())
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _fetch_releases_api(timeout: float) -> dict | None:
    try:
        resp = requests.get(RELEASES_API, timeout=timeout, headers=_headers())
        resp.raise_for_status()
        return _info_from_release(resp.json())
    except Exception:
        return None


def check_for_update(timeout: float = 8.0) -> dict | None:
    """Return update info if a newer GitHub release exists.

    Prefers latest.json when present; if missing or incomplete, uses the
    GitHub Releases API and the .exe asset on the latest release.
    """
    info = _fetch_latest_json(timeout)
    api_info = None

    remote = ""
    url = ""
    notes = ""
    if info:
        remote = str(info.get("version") or info.get("versionName") or "")
        url = str(info.get("downloadUrl") or "")
        notes = str(info.get("releaseNotes") or "")

    if not remote or not url:
        api_info = _fetch_releases_api(timeout)
        if not api_info:
            return None
        remote = remote or str(api_info.get("version") or "")
        url = url or str(api_info.get("downloadUrl") or "")
        notes = notes or str(api_info.get("releaseNotes") or "")

    if not remote or not url:
        return None
    if _parse_version(remote) <= _parse_version(__version__):
        return None
    return {
        "version": remote,
        "downloadUrl": url,
        "releaseNotes": notes,
    }


def apply_update(download_url: str) -> Path | None:
    """Download the new exe next to the running app, then replace+restart after exit."""
    if is_frozen():
        target = Path(sys.executable).resolve()
    else:
        target = Path.cwd() / "WMNavigation.exe"

    dest_dir = target.parent
    dest_dir.mkdir(parents=True, exist_ok=True)
    staged = dest_dir / "WMNavigation_new.exe"

    headers = {"User-Agent": f"WMNavigation/{__version__}"}
    resp = requests.get(download_url, timeout=120, headers=headers, stream=True)
    resp.raise_for_status()
    with staged.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=1024 * 256):
            if chunk:
                fh.write(chunk)

    if staged.stat().st_size < 1_000_000:
        staged.unlink(missing_ok=True)
        raise RuntimeError("Downloaded update looks too small")

    bat = dest_dir / "wmnavi_apply_update.bat"
    pid = os.getpid()
    bat.write_text(
        "\r\n".join(
            [
                "@echo off",
                "setlocal",
                f'set "TARGET={target}"',
                f'set "STAGED={staged}"',
                "timeout /t 2 /nobreak >nul",
                ":wait",
                f'tasklist /FI "PID eq {pid}" | find "{pid}" >nul',
                "if not errorlevel 1 (",
                "  timeout /t 1 /nobreak >nul",
                "  goto wait",
                ")",
                "set /a N=0",
                ":retry",
                "set /a N+=1",
                'copy /Y "%STAGED%" "%TARGET%" >nul 2>&1',
                "if not errorlevel 1 goto launch_target",
                "if %N% LSS 15 (",
                "  timeout /t 1 /nobreak >nul",
                "  goto retry",
                ")",
                'start "" "%STAGED%"',
                "goto done",
                ":launch_target",
                'start "" "%TARGET%"',
                'del "%STAGED%" >nul 2>&1',
                ":done",
                'del "%~f0" >nul 2>&1',
                "",
            ]
        ),
        encoding="utf-8",
    )
    subprocess.Popen(
        ["cmd", "/c", str(bat)],
        cwd=str(dest_dir),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
    return bat
