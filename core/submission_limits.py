from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from core import config
from core.photo_privacy import safe_child


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        try:
            total += item.stat().st_size
        except OSError:
            continue
    return total


def _private_size(record: dict[str, Any], private_dir: Path) -> int:
    try:
        path = safe_child(private_dir, record.get("private_original_file"))
    except (TypeError, ValueError):
        return 0
    try:
        return path.stat().st_size if path.exists() else 0
    except OSError:
        return 0


def _pending_record_owner(record: dict[str, Any]) -> str:
    return str(record.get("submission_owner") or "").strip()


def _is_pending(record: dict[str, Any]) -> bool:
    return str(record.get("public_review_status") or "").strip().lower() == "pending"


def pending_submission_usage(
    *,
    owner: str | None,
    wrecks_dir: Path,
    field_photos_dir: Path,
    private_dir: Path,
) -> dict[str, int]:
    owner = str(owner or "").strip()
    total_bytes = 0
    total_items = 0

    if field_photos_dir.is_dir():
        for record_path in field_photos_dir.glob("*/record.json"):
            try:
                record = _read_json(record_path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict) or not _is_pending(record):
                continue
            if owner and _pending_record_owner(record) != owner:
                continue
            total_items += 1
            total_bytes += _private_size(record, private_dir)

    if wrecks_dir.is_dir():
        for record_path in wrecks_dir.glob("*/record.json"):
            try:
                record = _read_json(record_path)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(record, dict):
                continue
            owner_matches = not owner or _pending_record_owner(record) == owner
            if _is_pending(record) and owner_matches:
                total_items += 1
                total_bytes += _dir_size(record_path.parent)
            for photo in record.get("attached_photos") or []:
                if not isinstance(photo, dict) or not _is_pending(photo):
                    continue
                if owner and _pending_record_owner(photo) != owner:
                    continue
                total_items += 1
                total_bytes += _private_size(photo, private_dir)

    return {
        "bytes": total_bytes,
        "items": total_items,
        "max_bytes": config.PENDING_SUBMISSION_MAX_BYTES,
        "max_items": config.PENDING_SUBMISSION_MAX_ITEMS,
    }


def assert_pending_submission_quota(
    *,
    owner: str,
    additional_bytes: int = 0,
    additional_items: int = 1,
    wrecks_dir: Path,
    field_photos_dir: Path,
    private_dir: Path,
) -> dict[str, int]:
    usage = pending_submission_usage(
        owner=owner,
        wrecks_dir=wrecks_dir,
        field_photos_dir=field_photos_dir,
        private_dir=private_dir,
    )
    next_bytes = usage["bytes"] + max(0, int(additional_bytes or 0))
    next_items = usage["items"] + max(0, int(additional_items or 0))
    if next_bytes > usage["max_bytes"]:
        raise ValueError(
            "Przekroczono limit miejsca dla materiałów oczekujących na moderację "
            f"({usage['max_bytes'] // config.BYTES_PER_MIB} MB)."
        )
    if next_items > usage["max_items"]:
        raise ValueError(f"Przekroczono limit liczby materiałów oczekujących na moderację ({usage['max_items']}).")
    usage["next_bytes"] = next_bytes
    usage["next_items"] = next_items
    return usage
