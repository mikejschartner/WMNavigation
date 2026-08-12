"""Resolve item icons from the Tarkov wiki (loot + keys) with tarkov.dev fallback."""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests

from .paths import cache_dir

WIKI_API = "https://escapefromtarkov.fandom.com/api.php"
_USER_AGENT = "WMNavigation/0.4.0"
_resolved: dict[str, str] = {}


def _cache_path() -> Path:
    return cache_dir() / "wiki_icon_resolved.json"


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _load_resolved() -> dict[str, str]:
    global _resolved
    if _resolved:
        return _resolved
    path = _cache_path()
    if path.exists():
        try:
            _resolved = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            _resolved = {}
    return _resolved


def _save_resolved():
    _cache_path().write_text(json.dumps(_resolved), encoding="utf-8")


def _wiki_file_url(file_title: str) -> str:
    try:
        resp = requests.get(
            WIKI_API,
            params={
                "action": "query",
                "titles": file_title if file_title.startswith("File:") else f"File:{file_title}",
                "prop": "imageinfo",
                "iiprop": "url",
                "format": "json",
            },
            timeout=12,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        pages = (resp.json().get("query") or {}).get("pages") or {}
        for page in pages.values():
            if page.get("missing") is not None:
                continue
            infos = page.get("imageinfo") or []
            if infos and infos[0].get("url"):
                return infos[0]["url"]
    except Exception:
        return ""
    return ""


def _wiki_search_file(query: str) -> str:
    if not query:
        return ""
    try:
        resp = requests.get(
            WIKI_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srnamespace": 6,
                "srlimit": 5,
                "format": "json",
            },
            timeout=12,
            headers={"User-Agent": _USER_AGENT},
        )
        resp.raise_for_status()
        hits = (resp.json().get("query") or {}).get("search") or []
        qn = _normalize(query)
        for hit in hits:
            title = hit.get("title") or ""
            if _normalize(title.replace("File:", "").rsplit(".", 1)[0]) == qn:
                return _wiki_file_url(title)
        if hits:
            return _wiki_file_url(hits[0]["title"])
    except Exception:
        return ""
    return ""


def wiki_icon_url(name: str, short_name: str = "") -> str:
    global _resolved
    store = _load_resolved()
    for key in (name, short_name):
        if not key:
            continue
        nk = _normalize(key)
        if nk in store:
            return store[nk] or ""
        for guess in (f"{key}.png", f"{key}.PNG", f"{key}.jpg"):
            url = _wiki_file_url(guess)
            if url:
                store[nk] = url
                _resolved = store
                _save_resolved()
                return url
        url = _wiki_search_file(key)
        store[nk] = url or ""
        _resolved = store
        _save_resolved()
        if url:
            return url
    return ""


def best_icon_url_cached(item_id: str, name: str, short_name: str, explicit: str = "") -> str:
    """Fast path for map redraws: cache-only wiki lookup, no network."""
    store = _load_resolved()
    for key in (name, short_name):
        if not key:
            continue
        url = store.get(_normalize(key)) or ""
        if url:
            return url
    if item_id:
        return f"https://assets.tarkov.dev/{item_id}-512.webp"
    if explicit and explicit.startswith("http"):
        return explicit
    return ""


def best_icon_url(item_id: str, name: str, short_name: str, explicit: str = "") -> str:
    """Wiki loot/key art first, then tarkov.dev 512px, then explicit icon link."""
    wiki = wiki_icon_url(name, short_name)
    if wiki:
        return wiki
    if item_id:
        return f"https://assets.tarkov.dev/{item_id}-512.webp"
    if explicit and explicit.startswith("http"):
        return explicit
    return ""
