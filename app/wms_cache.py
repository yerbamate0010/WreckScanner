from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app import config
from core.config import BYTES_PER_GIB
from core.settings_store import load_app_settings

_cleanup_lock = threading.Lock()
_last_cleanup = 0.0


def strip_proxy_only_params(upstream_path: str) -> str:
    parts = urlsplit(upstream_path)
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() != "enhancementsettings"
        ]
    )
    return urlunsplit(("", "", parts.path, query, parts.fragment))


def enhancement_fingerprint() -> str:
    payload = json.dumps(load_app_settings().get("enhancement", {}), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def tile_cache_key(stripped_upstream_path: str, enhancement_hash: str) -> str:
    payload = json.dumps(
        {
            "upstream": stripped_upstream_path,
            "enhancement": enhancement_hash,
            "format": "enhanced-png-v1",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def tile_cache_path(cache_key: str) -> Path:
    return config.WMS_TILE_CACHE_DIR / cache_key[:2] / f"{cache_key}.png"


def read_tile_cache(cache_path: Path) -> bytes | None:
    try:
        data = cache_path.read_bytes()
    except OSError:
        return None
    try:
        now = time.time()
        os.utime(cache_path, (now, cache_path.stat().st_mtime))
    except OSError:
        pass
    return data


def write_tile_cache(cache_path: Path, data: bytes) -> None:
    if config.WMS_TILE_CACHE_MAX_BYTES <= 0:
        return
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
        tmp_path.write_bytes(data)
        os.replace(tmp_path, cache_path)
    except OSError as exc:
        print(f"⚠️  WMS tile cache write failed for {cache_path}: {exc}")
    finally:
        try:
            if "tmp_path" in locals() and tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass


def cleanup_tile_cache(force: bool = False) -> None:
    global _last_cleanup
    if config.WMS_TILE_CACHE_MAX_BYTES <= 0:
        return
    now = time.time()
    if not force and now - _last_cleanup < config.WMS_TILE_CACHE_CLEANUP_INTERVAL_SECONDS:
        return
    if not _cleanup_lock.acquire(blocking=False):
        return
    try:
        _last_cleanup = now
        if not config.WMS_TILE_CACHE_DIR.is_dir():
            return
        entries = []
        total = 0
        for path in config.WMS_TILE_CACHE_DIR.glob("*/*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            entries.append((stat.st_atime, stat.st_size, path))
        if total <= config.WMS_TILE_CACHE_MAX_BYTES:
            return
        entries.sort()
        removed = 0
        for _, size, path in entries:
            if total <= config.WMS_TILE_CACHE_MAX_BYTES:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        if removed:
            print(f"🧹 WMS tile cache cleanup: removed {removed} tiles, total={total / BYTES_PER_GIB:.2f} GB")
    finally:
        _cleanup_lock.release()


def tile_cache_report() -> dict:
    total = 0
    count = 0
    if config.WMS_TILE_CACHE_DIR.is_dir():
        for path in config.WMS_TILE_CACHE_DIR.glob("*/*.png"):
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            count += 1
    return {
        "dir": str(config.WMS_TILE_CACHE_DIR),
        "tiles": count,
        "total_bytes": total,
        "total_gb": round(total / BYTES_PER_GIB, 2),
        "max_gb": round(config.WMS_TILE_CACHE_MAX_BYTES / BYTES_PER_GIB, 2),
    }
