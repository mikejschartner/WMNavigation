"""Fail the build if the app cannot import or open a window.

Run before tagging. Publish only after this prints SMOKE OK.
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
        floor_test = Path(__file__).resolve().parent / "test_floors.py"
        ping_test = Path(__file__).resolve().parent / "test_ping.py"
        try:
            runpy.run_path(str(math_test), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"tracking math tests failed: {exc.code}") from exc
        try:
            runpy.run_path(str(floor_test), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"floor select tests failed: {exc.code}") from exc
        try:
            runpy.run_path(str(ping_test), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (0, None):
                raise RuntimeError(f"ping tests failed: {exc.code}") from exc
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
        if not hasattr(window, "btn_ping_system") or not hasattr(window, "btn_prediction"):
            raise RuntimeError("Ping System and Prediction buttons missing")
        if window.btn_ping_system is window.btn_prediction:
            raise RuntimeError("Ping System and Prediction must be separate buttons")
        pred_was = window.btn_prediction.isChecked()
        ping_was = window.btn_ping_system.isChecked()
        window.btn_ping_system.setChecked(True)
        if window.btn_prediction.isChecked() and not pred_was:
            raise RuntimeError("Ping System must not turn Prediction on")
        window.btn_prediction.setChecked(True)
        if not window.btn_ping_system.isChecked():
            raise RuntimeError("Prediction must not turn Ping System off")
        window.btn_ping_system.setChecked(False)
        if not window.btn_prediction.isChecked():
            raise RuntimeError("turning Ping System off must not turn Prediction off")
        window.btn_ping_system.setChecked(ping_was)
        window.btn_prediction.setChecked(pred_was)
        if hasattr(window, "btn_ai_prediction") or hasattr(window, "btn_audio_indicator"):
            raise RuntimeError("AI Prediction / Audio Indicator should be removed")
        if hasattr(window, "btn_page_visual") or hasattr(window, "visual_page") or hasattr(window, "visual_engine"):
            raise RuntimeError("Visual Profiles must be removed")
        if hasattr(window, "page_stack"):
            raise RuntimeError("Map/Visual page stack must be removed")
        if window.splitter.childrenCollapsible():
            raise RuntimeError("sidebar splitter must not be collapsible")
        from PySide6.QtWidgets import QScrollArea

        from wmnavi.marker_icons import get_quest_marker
        from wmnavi.paths import app_root
        from wmnavi.updater import apply_update, check_for_update, resume_pending_update

        if not callable(apply_update) or not callable(check_for_update) or not callable(resume_pending_update):
            raise RuntimeError("updater exports missing")

        if window.findChild(QScrollArea, "sidebarScroll") is None:
            raise RuntimeError("sidebarScroll missing")
        if not hasattr(window, "btn_settings") or not hasattr(window, "settings_widget"):
            raise RuntimeError("Settings gear/dialog missing")
        if not hasattr(window, "btn_sync_quests"):
            raise RuntimeError("Sync quests button missing")
        if not hasattr(window.layer_sidebar, "apply_preset"):
            raise RuntimeError("layer sidebar apply_preset missing")
        from wmnavi.brand import app_icon, icon_ico_path, icon_png_path
        from wmnavi.splash import SplashScreen
        from wmnavi.theme import BG
        from wmnavi.window_glow import glow_supported

        if BG.lower() != "#050508":
            raise RuntimeError("black-heavy theme missing")
        if not icon_png_path().exists() or not icon_ico_path().exists():
            raise RuntimeError("app logo missing")
        icon = app_icon()
        if icon.isNull():
            raise RuntimeError("app icon failed")
        splash = SplashScreen(min_ms=0)
        splash.close()
        if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen" and glow_supported():
            raise RuntimeError("window glow should stay off during offscreen smoke")
        if not hasattr(window, "chk_window_glow"):
            raise RuntimeError("window glow setting missing")
        if not hasattr(window, "_window_glow"):
            raise RuntimeError("window glow hook missing")

        traders = app_root() / "assets" / "traders"
        needed = [
            "prapor.jpg",
            "therapist.jpg",
            "fence.jpg",
            "skier.jpg",
            "peacekeeper.jpg",
            "mechanic.jpg",
            "ragman.jpg",
            "jaeger.jpg",
            "lightkeeper.jpg",
            "ref.jpg",
            "btr-driver.png",
        ]
        missing = [name for name in needed if not (traders / name).exists()]
        if missing:
            raise RuntimeError(f"trader portraits missing: {missing}")
        pix = get_quest_marker(22, trader="Prapor")
        if pix.isNull() or pix.width() < 8:
            raise RuntimeError("quest trader marker failed")
        from wmnavi.update_ui import UpdateProgressDialog

        dlg = UpdateProgressDialog()
        dlg.set_progress(40, "Downloading update… 40%")
        if dlg.bar.value() != 40:
            raise RuntimeError("update progress bar failed")
        dlg.allow_close()
        dlg.close()
        rect = mv.scene.sceneRect()
        if rect.width() < 40 or rect.height() < 40:
            raise RuntimeError(f"map scene empty: {rect}")
        from PySide6.QtWidgets import QGraphicsView

        from wmnavi.map_view import MapView
        from wmnavi.minimap import MiniMapWindow

        overlay = MapView()
        overlay.set_prefer_raster_art(True)
        overlay.load_svg(
            mv._svg_source,
            mv.map_rotation,
            mv.map_bounds,
            mv.map_transform,
            map_meta=mv._map_meta,
            map_slug=mv._map_slug,
        )
        art = overlay.map_item or overlay._tile_item
        if art is None:
            raise RuntimeError("F7 overlay map graphic missing")
        art_rect = art.sceneBoundingRect()
        if art_rect.width() < rect.width() * 0.5 or art_rect.height() < rect.height() * 0.5:
            raise RuntimeError(f"F7 overlay map misplaced: art={art_rect} scene={rect}")
        if not hasattr(window, "chk_auto_join"):
            raise RuntimeError("Auto Join Last Room missing")
        from wmnavi.hotkeys import _KEYS

        names = {name for _vk, name in _KEYS}
        if "f10" in names or "f11" in names:
            raise RuntimeError("F10/F11 visual hotkeys should be removed")
        leaked = [k for k in window.settings.allKeys() if "visual_profile" in str(k).lower()]
        if leaked:
            raise RuntimeError(f"Visual Profiles leaked into QSettings: {leaked}")
        from wmnavi.geometry_import import unity_typetree_error

        tpk_err = unity_typetree_error()
        if tpk_err:
            raise RuntimeError(tpk_err)
        mmw = MiniMapWindow(size_px=180)
        center = QGraphicsView.ViewportAnchor.AnchorViewCenter
        if mmw.map_view.transformationAnchor() != center or mmw.map_view.resizeAnchor() != center:
            raise RuntimeError("F7 overlay must rotate around view center")
        mmw.close()
        print(f"SMOKE OK v{__version__} title={title}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
