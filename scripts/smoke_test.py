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
        floor_test = Path(__file__).resolve().parent / "test_floors.py"
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
        if not hasattr(window, "btn_page_visual") or not hasattr(window, "visual_page"):
            raise RuntimeError("Visual Profiles tab missing")
        if window.page_stack.count() < 2:
            raise RuntimeError("Visual Profiles page missing")
        if not hasattr(window, "chk_auto_join"):
            raise RuntimeError("Auto Join Last Room missing")
        from wmnavi.hotkeys import _KEYS
        from wmnavi.visual.profiles import VisualProfileManager, VisualSettings
        from wmnavi.visual.tone import build_gamma_ramp, identity_ramp

        names = {name for _vk, name in _KEYS}
        if "f10" not in names or "f11" not in names:
            raise RuntimeError("F10/F11 hotkeys missing")
        red, green, blue = build_gamma_ramp(VisualSettings())
        ident = identity_ramp()[0]
        if red != ident or green != ident or blue != ident:
            raise RuntimeError("default visual LUT is not identity")
        mgr = VisualProfileManager()
        mgr.draft.gamma = 4.0
        if mgr.save_draft_to_active() or mgr.profiles[0].settings.gamma != 1.0:
            raise RuntimeError("Default visual profile must stay unmodified")
        mgr.select(1)
        mgr.draft.gamma = 4.0
        if not mgr.save_draft_to_active() or mgr.profiles[1].settings.gamma != 4.0:
            raise RuntimeError("Profile 1 session save failed")
        leaked = [k for k in window.settings.allKeys() if "visual_profile" in str(k).lower()]
        if leaked:
            raise RuntimeError(f"Visual Profiles leaked into QSettings: {leaked}")
        if window.visual_engine.manager.filter_enabled:
            raise RuntimeError("Visual Filter must start OFF")
        if window.visual_engine.manager.active_index != 0:
            raise RuntimeError("Visual Profiles must start on Default")
        mmw = MiniMapWindow(size_px=180)
        center = QGraphicsView.ViewportAnchor.AnchorViewCenter
        if mmw.map_view.transformationAnchor() != center or mmw.map_view.resizeAnchor() != center:
            raise RuntimeError("F7 overlay must rotate around view center")
        mmw.close()
        window.visual_engine.shutdown()
        print(f"SMOKE OK v{__version__} title={title}")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
