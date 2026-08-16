"""Fail the build if the app cannot import or open a window.

Run before tagging. GitHub Actions runs this before publishing the exe.
"""

from __future__ import annotations

import os
import pkgutil
import runpy
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _import_all() -> None:
    import wmnavi

    failed: list[str] = []
    for module in pkgutil.walk_packages(wmnavi.__path__, wmnavi.__name__ + "."):
        try:
            __import__(module.name)
        except Exception as exc:
            failed.append(f"{module.name}: {exc}")
    if failed:
        raise RuntimeError("import failed:\n" + "\n".join(failed))


def main() -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from wmnavi import __version__
    from wmnavi.app import MainWindow

    try:
        _import_all()
        app = QApplication.instance() or QApplication(sys.argv)
        app.setApplicationName("WMNavigation-smoke")
        math_test = Path(__file__).resolve().parent / "test_tracking_math.py"
        try:
            runpy.run_path(str(math_test), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"tracking math tests failed: {exc.code}") from exc
        window = MainWindow()
        window.show()
        QTimer.singleShot(400, app.quit)
        app.exec()
        title = window.windowTitle()
        if __version__ not in title:
            raise RuntimeError(f"window title {title!r} missing version {__version__}")
        mv = window.map_view
        has_art = mv.map_item is not None or mv._tile_item is not None
        if not has_art:
            raise RuntimeError("map graphic missing after MainWindow load")
        if not hasattr(window, "btn_loot_value"):
            raise RuntimeError("Loot Value button missing")
        if hasattr(window, "btn_ai_prediction") or hasattr(window, "btn_audio_indicator"):
            raise RuntimeError("AI Prediction / Audio Indicator should be removed")
        if window.splitter.childrenCollapsible():
            raise RuntimeError("sidebar splitter must not be collapsible")
        rect = mv.scene.sceneRect()
        if rect.width() < 40 or rect.height() < 40:
            raise RuntimeError(f"map scene empty: {rect}")
        print(f"SMOKE OK v{__version__} title={title}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
