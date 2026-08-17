"""GitHub release auto-updater for WMNavigation.exe."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import requests

from . import __version__
from .paths import is_frozen, user_data_dir

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


def check_for_update(timeout: float = 6.0) -> dict | None:
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


def current_exe() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve()
    return Path.cwd() / "WMNavigation.exe"


def cleanup_old_binaries(target: Path | None = None) -> None:
    exe = target or current_exe()
    old = exe.parent / (exe.name + ".old")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        pass
    purge_legacy_helpers()


def purge_legacy_helpers() -> None:
    """Old updaters used cmd+find; Windows Terminal opens that as a stuck blank tab."""
    folders = [
        user_data_dir() / "update",
        current_exe().parent,
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
    ]
    for folder in folders:
        for name in ("wmnavi_apply_update.bat", "WMNavigation_new.exe"):
            try:
                (folder / name).unlink(missing_ok=True)
            except OSError:
                pass


def _vbs_escape(path: str) -> str:
    return path.replace('"', '""')


def _write_apply_script(pid: int, staged: Path, target: Path) -> Path:
    """Hidden wscript helper: wait for this PID, replace exe, start once. No cmd windows."""
    update_dir = user_data_dir() / "update"
    update_dir.mkdir(parents=True, exist_ok=True)
    script = update_dir / f"apply_{pid}.vbs"
    old = str(target.parent / (target.name + ".old"))
    script.write_text(
        "\r\n".join(
            [
                "Option Explicit",
                f"Dim pid: pid = {int(pid)}",
                f'Dim staged: staged = "{_vbs_escape(str(staged))}"',
                f'Dim target: target = "{_vbs_escape(str(target))}"',
                f'Dim oldp: oldp = "{_vbs_escape(old)}"',
                "Dim sh, fso, n, t, launch",
                'Set sh = CreateObject("WScript.Shell")',
                'Set fso = CreateObject("Scripting.FileSystemObject")',
                "t = 0",
                "Do While ProcessAlive(pid) And t < 1200",
                "  WScript.Sleep 250",
                "  t = t + 1",
                "Loop",
                "WScript.Sleep 400",
                "On Error Resume Next",
                "If fso.FileExists(oldp) Then fso.DeleteFile oldp, True",
                "Err.Clear",
                "If fso.FileExists(target) Then fso.MoveFile target, oldp",
                "n = 0",
                "Do",
                "  Err.Clear",
                "  fso.CopyFile staged, target, True",
                "  If Err.Number = 0 Then",
                "    If fso.FileExists(target) Then",
                "      If fso.GetFile(target).Size > 1000000 Then Exit Do",
                "    End If",
                "  End If",
                "  n = n + 1",
                "  WScript.Sleep 300",
                "Loop While n < 50",
                'If fso.FileExists(target) Then launch = target Else launch = staged',
                'sh.Run """" & launch & """", 1, False',
                "If fso.FileExists(oldp) Then fso.DeleteFile oldp, True",
                "If launch = target And fso.FileExists(staged) Then fso.DeleteFile staged, True",
                "If fso.FileExists(WScript.ScriptFullName) Then fso.DeleteFile WScript.ScriptFullName, True",
                "WScript.Quit 0",
                "",
                "Function ProcessAlive(processId)",
                "  On Error Resume Next",
                "  Dim svc, procs",
                '  Set svc = GetObject("winmgmts:\\\\.\\root\\cimv2")',
                '  Set procs = svc.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE ProcessId=" & processId)',
                "  ProcessAlive = (Not procs Is Nothing) And (procs.Count > 0)",
                "End Function",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return script


def _spawn_hidden(args: list[str]) -> None:
    flags = 0
    flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    flags |= int(getattr(subprocess, "DETACHED_PROCESS", 0x00000008))
    flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200))
    subprocess.Popen(
        args,
        cwd=str(user_data_dir() / "update"),
        creationflags=flags,
        close_fds=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _schedule_replace_and_restart(staged: Path) -> Path:
    script = _write_apply_script(os.getpid(), staged, current_exe())
    wscript = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "wscript.exe"
    _spawn_hidden([str(wscript), "//nologo", "//B", str(script)])
    return script


def resume_pending_update() -> bool:
    """Finish a download that already landed but whose cmd/find helper got stuck."""
    purge_legacy_helpers()
    staged = user_data_dir() / "update" / "WMNavigation.exe"
    if not staged.exists() or staged.stat().st_size < 1_000_000:
        return False
    target = current_exe()
    try:
        if target.resolve() == staged.resolve():
            return False
        if target.exists() and target.stat().st_size == staged.stat().st_size:
            staged.unlink(missing_ok=True)
            return False
    except OSError:
        pass
    _schedule_replace_and_restart(staged)
    return True
    """Download the new exe, then schedule a hidden replace+single restart.

    on_progress(pct: int, text: str) — pct is 0-100, or -1 for indeterminate.
    Staging lives in LocalAppData so OneDrive/Desktop locks cannot stall the download.
    """
    target = current_exe()
    update_dir = user_data_dir() / "update"
    update_dir.mkdir(parents=True, exist_ok=True)
    staged = update_dir / "WMNavigation.exe"

    def report(pct: int, text: str):
        if on_progress:
            on_progress(pct, text)

    report(0, "Downloading update…")
    headers = {"User-Agent": f"WMNavigation/{__version__}"}
    resp = requests.get(download_url, timeout=(20, 180), headers=headers, stream=True)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length") or 0)
    got = 0
    last_pct = -1
    with staged.open("wb") as fh:
        for chunk in resp.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            fh.write(chunk)
            got += len(chunk)
            if total > 0:
                pct = min(99, got * 100 // total)
                if pct != last_pct:
                    last_pct = pct
                    report(pct, f"Downloading update… {pct}%")
            elif on_progress and got % (5 * 1024 * 1024) < 256 * 1024:
                report(-1, f"Downloading update… {got // (1024 * 1024)} MB")

    if staged.stat().st_size < 1_000_000:
        staged.unlink(missing_ok=True)
        raise RuntimeError("Downloaded update looks too small")

    report(100, "Download complete · restarting…")
    return _schedule_replace_and_restart(staged)
