from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from PIL import Image

from core import config
from core.json_io import write_json_atomic
from core.photo_privacy import is_approved, safe_child


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso(now: datetime | None = None) -> str:
    current = now or _now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    write_json_atomic(path, payload)


def _parse_reviewed_at(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retention_due(record: dict[str, Any], now: datetime, retention_days: int) -> bool:
    reviewed_at = _parse_reviewed_at(record.get("reviewed_at"))
    if not reviewed_at:
        return False
    return now.astimezone(timezone.utc) - reviewed_at >= timedelta(days=retention_days)


def _retained_private_rel(private_rel: Any) -> str | None:
    rel = str(private_rel or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or any(part in {"", ".", ".."} for part in rel.split("/")):
        return None
    return (Path(rel).parent / "retained_public.jpg").as_posix()


def _public_image_info(path: Path) -> tuple[int, int, int]:
    with Image.open(path) as image:
        width, height = image.size
    return width, height, path.stat().st_size


def _replace_private_original_with_public_copy(
    record: dict[str, Any],
    *,
    record_dir: Path,
    private_photos_dir: Path,
    public_image_file: Any,
    now: datetime,
    dry_run: bool,
) -> dict[str, Any]:
    private_rel = record.get("private_original_file")
    retained_rel = _retained_private_rel(private_rel)
    if not retained_rel:
        return {"action": "skipped", "reason": "unsafe_private_original_path"}

    public_path = safe_child(record_dir, public_image_file)
    if not public_path.exists():
        return {"action": "skipped", "reason": "public_image_missing"}

    try:
        old_private_path = safe_child(private_photos_dir, private_rel)
        retained_private_path = safe_child(private_photos_dir, retained_rel)
    except ValueError:
        return {"action": "skipped", "reason": "unsafe_private_original_path"}

    width, height, size_bytes = _public_image_info(public_path)
    if not dry_run:
        retained_private_path.parent.mkdir(parents=True, exist_ok=True)
        retained_private_path.write_bytes(public_path.read_bytes())
        if old_private_path.exists() and old_private_path.resolve() != retained_private_path.resolve():
            old_private_path.unlink()
        record["private_original_file"] = retained_rel
        record["private_original_replaced_at"] = _now_iso(now)
        record["private_original_retention_action"] = "replaced_with_public_copy"
        record.pop("private_original_deleted_at", None)
        record["content_type"] = "image/jpeg"
        record["format"] = "JPEG"
        record["size_bytes"] = size_bytes
        record["image_width"] = width
        record["image_height"] = height

    return {
        "action": "replaced",
        "private_original_file": retained_rel,
        "public_image_file": str(public_image_file or ""),
    }


def _delete_rejected_private_original(
    record: dict[str, Any],
    *,
    private_photos_dir: Path,
    now: datetime,
    dry_run: bool,
) -> dict[str, Any]:
    private_rel = record.get("private_original_file")
    if not private_rel:
        return {"action": "skipped", "reason": "private_original_missing"}
    try:
        private_path = safe_child(private_photos_dir, private_rel)
    except ValueError:
        return {"action": "skipped", "reason": "unsafe_private_original_path"}
    if not dry_run:
        if private_path.exists():
            private_path.unlink()
        record.pop("private_original_file", None)
        record["private_original_deleted_at"] = _now_iso(now)
        record["private_original_retention_action"] = "deleted_rejected_original"
        record.pop("private_original_replaced_at", None)
    return {"action": "deleted", "private_original_file": str(private_rel or "")}


def _retire_record_private_original(
    record: dict[str, Any],
    *,
    record_dir: Path,
    private_photos_dir: Path,
    public_image_file: Any,
    now: datetime,
    retention_days: int,
    dry_run: bool,
) -> dict[str, Any]:
    status = str(record.get("public_review_status") or "").strip()
    if not _retention_due(record, now, retention_days):
        return {"action": "skipped", "reason": "not_due"}
    if record.get("private_original_replaced_at") or record.get("private_original_deleted_at"):
        return {"action": "skipped", "reason": "already_retired"}
    if is_approved(record):
        return _replace_private_original_with_public_copy(
            record,
            record_dir=record_dir,
            private_photos_dir=private_photos_dir,
            public_image_file=public_image_file,
            now=now,
            dry_run=dry_run,
        )
    if status == "rejected":
        return _delete_rejected_private_original(
            record, private_photos_dir=private_photos_dir, now=now, dry_run=dry_run
        )
    return {"action": "skipped", "reason": "not_final"}


def _summary() -> dict[str, int]:
    return {"scanned": 0, "replaced": 0, "deleted": 0, "skipped": 0}


def _count(summary: dict[str, int], action: str) -> None:
    summary["scanned"] += 1
    if action in {"replaced", "deleted"}:
        summary[action] += 1
    else:
        summary["skipped"] += 1


def retire_private_originals(
    *,
    field_photos_dir: Path = config.FIELD_PHOTOS_DIR,
    wrecks_dir: Path = config.WRECKS_DIR,
    private_photos_dir: Path = config.PRIVATE_PHOTOS_DIR,
    retention_days: int = config.PRIVATE_ORIGINAL_RETENTION_DAYS,
    now: datetime | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    current = now or _now_utc()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    report: dict[str, Any] = {
        "status": "ok",
        "dry_run": dry_run,
        "retention_days": retention_days,
        "generated_at": _now_iso(current),
        "field_photos": _summary(),
        "wreck_photos": _summary(),
        "items": [],
    }

    for record_path in sorted(field_photos_dir.glob("*/record.json")):
        try:
            record = _read_json(record_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        result = _retire_record_private_original(
            record,
            record_dir=record_path.parent,
            private_photos_dir=private_photos_dir,
            public_image_file=record.get("public_image_file"),
            now=current,
            retention_days=retention_days,
            dry_run=dry_run,
        )
        action = str(result.get("action") or "skipped")
        _count(report["field_photos"], action)
        if action in {"replaced", "deleted"} and not dry_run:
            _write_json(record_path, record)
        if action in {"replaced", "deleted"}:
            report["items"].append({"scope": "field", "id": record.get("id"), **result})

    for record_path in sorted(wrecks_dir.glob("*/record.json")):
        try:
            record = _read_json(record_path)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        record_changed = False
        record_dir = record_path.parent
        wreck_id = str(record.get("id") or record_dir.name)
        attached = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
        for photo in attached:
            if not isinstance(photo, dict):
                continue
            photo_id = str(photo.get("id") or "")
            result = _retire_record_private_original(
                photo,
                record_dir=record_dir,
                private_photos_dir=private_photos_dir,
                public_image_file=photo.get("public_image_file"),
                now=current,
                retention_days=retention_days,
                dry_run=dry_run,
            )
            action = str(result.get("action") or "skipped")
            _count(report["wreck_photos"], action)
            if action in {"replaced", "deleted"}:
                record_changed = True
                report["items"].append({"scope": "wreck", "wreck_id": wreck_id, "id": photo_id, **result})
                if not dry_run and photo_id:
                    _write_json(record_dir / "photos" / photo_id / "record.json", photo)
        if record_changed and not dry_run:
            record["updated_at"] = _now_iso(current)
            _write_json(record_path, record)

    return report
