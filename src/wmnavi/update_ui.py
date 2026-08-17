"""Startup update dialog: progress bar, then one clean restart."""

from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QProgressBar,
    QVBoxLayout,
)

from . import __version__
from .paths import is_frozen


class UpdateProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateDialog")
        self.setWindowTitle("WMNavigation")
        self.setModal(True)
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setFixedWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        self.label = QLabel("Checking for updates…")
        self.label.setWordWrap(True)
        self.label.setObjectName("status")
        layout.addWidget(self.label)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        layout.addWidget(self.bar)
        self._allow_close = False

    def set_progress(self, pct: int, text: str):
        self.label.setText(text)
        if pct < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(min(100, max(0, pct)))

    def allow_close(self):
        self._allow_close = True

    def closeEvent(self, event):
        if not self._allow_close:
            event.ignore()
            return
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()
            return
        super().keyPressEvent(event)


class _UpdateBridge(QObject):
    progress = Signal(int, str)
    finished = Signal(object)


def offer_startup_update(app: QApplication) -> bool:
    """Download on a progress dialog before the main window.

    Returns True if this process should exit so the helper can start the new exe.
    """
    if not is_frozen():
        return False

    from .updater import apply_update, check_for_update, resume_pending_update

    if resume_pending_update():
        dlg = UpdateProgressDialog()
        dlg.set_progress(100, "Finishing previous update…")
        dlg.show()
        app.processEvents()
        return True

    settings = QSettings("WMMods", "WMNavigation")
    if not settings.value("auto_update_check", True, type=bool):
        return False

    info = None
    error = None

    def probe():
        nonlocal info, error
        try:
            info = check_for_update()
        except Exception as exc:
            error = exc

    thread = threading.Thread(target=probe, daemon=True, name="wmnavi-update-check")
    thread.start()
    thread.join(8.0)
    if thread.is_alive() or error or not info:
        return False

    remote = str(info.get("version") or "")
    url = str(info.get("downloadUrl") or "")
    if not remote or not url:
        return False

    dlg = UpdateProgressDialog()
    dlg.set_progress(0, f"Downloading v{remote}…")
    dlg.show()
    app.processEvents()

    bridge = _UpdateBridge()
    bridge.progress.connect(dlg.set_progress, Qt.ConnectionType.QueuedConnection)
    outcome: dict[str, object] = {"ok": False, "err": ""}

    def work():
        try:
            apply_update(url, on_progress=lambda pct, text: bridge.progress.emit(pct, text))
            bridge.finished.emit(None)
        except Exception as exc:
            bridge.finished.emit(exc)

    def on_done(exc):
        if exc is None:
            outcome["ok"] = True
            dlg.set_progress(100, f"Restarting with v{remote}…")
        else:
            outcome["err"] = str(exc)
            dlg.set_progress(0, f"Update failed — opening v{__version__}")
            dlg.allow_close()

    bridge.finished.connect(on_done, Qt.ConnectionType.QueuedConnection)
    worker = threading.Thread(target=work, daemon=True, name="wmnavi-update-dl")
    worker.start()
    deadline = time.time() + 600
    while time.time() < deadline:
        app.processEvents()
        if outcome["ok"] or outcome["err"]:
            break
        if not worker.is_alive():
            app.processEvents()
            if not (outcome["ok"] or outcome["err"]):
                outcome["err"] = "Update did not finish"
            break
        worker.join(0.05)

    app.processEvents()
    if outcome["ok"]:
        return True
    dlg.allow_close()
    dlg.close()
    return False
