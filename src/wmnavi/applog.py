"""Small file logger. No per-frame messages."""

from __future__ import annotations

import logging

from .paths import user_data_dir

_configured = False


def get_logger(name: str) -> logging.Logger:
    global _configured
    log = logging.getLogger(name)
    if not _configured:
        _configured = True
        root = logging.getLogger("wmnavi")
        root.setLevel(logging.INFO)
        if not root.handlers:
            path = user_data_dir() / "wmnavi.log"
            try:
                handler = logging.FileHandler(path, encoding="utf-8")
                handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
                root.addHandler(handler)
            except OSError:
                root.addHandler(logging.NullHandler())
            root.propagate = False
    return log
