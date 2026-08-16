"""Fast lookup index over EXISTING cached Tarkov item icons. No second image library."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
from PySide6.QtGui import QImage, QPixmap

from .icon_cache import _icon_path
from .models import ItemInfo

INDEX_VERSION = 1
HASH_SIZE = 8  # 8x8 differences → 64-bit dHash
RERANK = 32


@dataclass
class IndexEntry:
    item_id: str
    dhash: int
    mean: tuple[float, float, float]
    gray: np.ndarray  # RERANK x RERANK uint8


def dhash64(gray: np.ndarray) -> int:
    import cv2

    small = cv2.resize(gray, (HASH_SIZE + 1, HASH_SIZE), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    h = 0
    for flag in bits.reshape(-1):
        h = (h << 1) | int(bool(flag))
    return h


def _pixmap_to_bgr(pm: QPixmap) -> np.ndarray | None:
    if pm.isNull() or pm.width() < 8 or pm.height() < 8:
        return None
    img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    w, h = img.width(), img.height()
    ptr = img.constBits()
    arr = np.frombuffer(ptr, dtype=np.uint8, count=w * h * 4).reshape((h, w, 4)).copy()
    # RGBA → BGR, drop near-transparent
    bgr = arr[:, :, 2::-1].copy()
    alpha = arr[:, :, 3]
    if np.mean(alpha) < 8:
        return None
    return bgr


def _prepare_gray(bgr: np.ndarray) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return cv2.resize(gray, (RERANK, RERANK), interpolation=cv2.INTER_AREA)


class ItemIconIndex:
    def __init__(self):
        self.entries: list[IndexEntry] = []
        self.ids: list[str] = []
        self.hashes: np.ndarray = np.zeros(0, dtype=np.uint64)
        self.means: np.ndarray = np.zeros((0, 3), dtype=np.float32)
        self.grays: np.ndarray = np.zeros((0, RERANK, RERANK), dtype=np.uint8)
        self.built_at = 0.0

    def __len__(self) -> int:
        return len(self.ids)

    def build(self, catalog: dict[str, ItemInfo], *, max_items: int = 0) -> int:
        """Scan cache/icons for catalog items. Uses files already on disk; no re-download."""
        t0 = time.perf_counter()
        entries: list[IndexEntry] = []
        items = list(catalog.values())
        if max_items:
            items = items[:max_items]
        for item in items:
            path = _icon_path(item.icon_url)
            if not path.exists() or path.stat().st_size < 128:
                continue
            pm = QPixmap(str(path))
            bgr = _pixmap_to_bgr(pm)
            if bgr is None:
                continue
            gray = _prepare_gray(bgr)
            mean = tuple(float(x) for x in np.mean(bgr.reshape(-1, 3), axis=0))
            entries.append(IndexEntry(item.id, dhash64(gray), mean, gray))
        self.entries = entries
        self.ids = [e.item_id for e in entries]
        self.hashes = np.array([e.dhash for e in entries], dtype=np.uint64)
        self.means = np.array([e.mean for e in entries], dtype=np.float32)
        self.grays = np.stack([e.gray for e in entries], axis=0) if entries else np.zeros(
            (0, RERANK, RERANK), dtype=np.uint8
        )
        self.built_at = time.perf_counter() - t0
        return len(self.ids)

    def query(self, bgr: np.ndarray, top_k: int = 8) -> list[tuple[str, float, int]]:
        """Return [(item_id, ncc_score, hamming), ...] best-first."""
        if not self.ids or bgr is None or bgr.size < 64:
            return []
        gray = _prepare_gray(bgr)
        qh = np.uint64(dhash64(gray))
        xor = np.bitwise_xor(self.hashes, qh)
        ham = np.fromiter((int(v).bit_count() for v in xor), dtype=np.int32, count=int(xor.size))
        k = min(int(top_k), len(self.ids))
        idx = np.argpartition(ham, kth=k - 1)[:k]
        idx = idx[np.argsort(ham[idx])]
        qn = gray.astype(np.float32)
        qn = (qn - qn.mean()) / (qn.std() + 1e-6)
        out: list[tuple[str, float, int]] = []
        for i in idx:
            ref = self.grays[int(i)].astype(np.float32)
            ref = (ref - ref.mean()) / (ref.std() + 1e-6)
            ncc = float(np.mean(qn * ref))
            ncc = max(0.0, min(1.0, (ncc + 1.0) * 0.5))
            out.append((self.ids[int(i)], ncc, int(ham[int(i)])))
        out.sort(key=lambda t: (-t[1], t[2]))
        return out


def transform_for_benchmark(bgr: np.ndarray, kind: str) -> np.ndarray:
    import cv2

    img = bgr.copy()
    h, w = img.shape[:2]
    if kind == "scaled":
        img = cv2.resize(img, (max(16, int(w * 0.72)), max(16, int(h * 0.72))))
        img = cv2.resize(img, (w, h))
    elif kind == "dark":
        img = (img.astype(np.float32) * 0.72).astype(np.uint8)
    elif kind == "highlight":
        img = np.clip(img.astype(np.int16) + 28, 0, 255).astype(np.uint8)
    elif kind == "crop":
        m = max(2, int(min(h, w) * 0.08))
        img = img[m : h - m, m : w - m]
        img = cv2.resize(img, (w, h))
    elif kind == "bg":
        canvas = np.full_like(img, (18, 14, 22))
        m = max(2, int(min(h, w) * 0.1))
        inner = cv2.resize(img, (w - 2 * m, h - 2 * m))
        canvas[m : h - m, m : w - m] = inner
        img = canvas
    return img
