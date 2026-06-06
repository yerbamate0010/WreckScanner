from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import config
from core.field_photos import FIELD_PHOTO_ID_RE
from core.photo_privacy import ensure_review_fields, migrate_private_original, safe_child


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _field_photo_record_dir(photo_id: Any, field_photos_dir: Path) -> Path:
    photo_id_text = str(photo_id or "").strip()
    if not FIELD_PHOTO_ID_RE.fullmatch(photo_id_text):
        raise ValueError("Nieprawidłowy identyfikator zdjęcia terenowego.")
    root = field_photos_dir.resolve()
    record_dir = (field_photos_dir / photo_id_text).resolve()
    if root != record_dir and root not in record_dir.parents:
        raise ValueError("Nieprawidłowa ścieżka zdjęcia terenowego.")
    if not (record_dir / "record.json").exists():
        raise FileNotFoundError("Nie znaleziono zdjęcia terenowego.")
    return record_dir


def _private_photo_file(file_name: Any) -> Path:
    path = safe_child(config.PRIVATE_PHOTOS_DIR, file_name)
    if not path.exists():
        raise FileNotFoundError("Nie znaleziono prywatnego oryginału zdjęcia terenowego.")
    return path


def prepare_field_photo_attachment(
    photo_id: Any, field_photos_dir: Path, wreck_record_dir: Path
) -> tuple[Path, dict[str, Any]]:
    photo_id_text = str(photo_id or "").strip()
    field_record_dir = _field_photo_record_dir(photo_id_text, field_photos_dir)
    field_record = _read_json(field_record_dir / "record.json")
    if not isinstance(field_record, dict):
        raise ValueError("Nieprawidłowy format record.json zdjęcia terenowego.")
    if str(field_record.get("id") or "").strip() != photo_id_text:
        raise ValueError("Nieprawidłowy format record.json zdjęcia terenowego.")
    issue_type = str(field_record.get("issue_type") or config.DEFAULT_FIELD_PHOTO_ISSUE_TYPE).strip()
    if issue_type != config.DEFAULT_FIELD_PHOTO_ISSUE_TYPE:
        raise ValueError("Do teczki pojazdu można przenieść tylko zdjęcia zalegających pojazdów.")
    changed = ensure_review_fields(field_record)
    changed = (
        migrate_private_original(
            field_record,
            field_record_dir,
            config.PRIVATE_PHOTOS_DIR,
            scope="field_photos",
            photo_id=photo_id_text,
        )
        or changed
    )
    for legacy_key in ("thumbnail_file", "original_url", "original_path"):
        if legacy_key in field_record:
            field_record.pop(legacy_key, None)
            changed = True
    if changed:
        _write_json(field_record_dir / "record.json", field_record)
    _private_photo_file(field_record.get("private_original_file"))
    destination = wreck_record_dir / "photos" / photo_id_text
    if destination.exists():
        raise ValueError("To zdjęcie jest już w teczce pojazdu.")
    return field_record_dir, field_record


def move_field_photo_to_wreck(
    field_record: dict[str, Any], field_record_dir: Path, wreck_record_dir: Path
) -> dict[str, Any]:
    photo_id = str(field_record.get("id") or "")
    if not FIELD_PHOTO_ID_RE.fullmatch(photo_id):
        raise ValueError("Nieprawidłowy format record.json zdjęcia terenowego.")
    ensure_review_fields(field_record)
    source_original = _private_photo_file(field_record.get("private_original_file"))
    ext = source_original.suffix.lower() or ".jpg"
    photo_dir = wreck_record_dir / "photos" / photo_id
    if photo_dir.exists():
        raise ValueError("To zdjęcie jest już w teczce pojazdu.")
    photo_dir.mkdir(parents=True, exist_ok=False)
    private_original_file = f"wreck_photos/{wreck_record_dir.name}/{photo_id}/original{ext}"
    destination_original = safe_child(config.PRIVATE_PHOTOS_DIR, private_original_file)
    destination_original.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_original), destination_original)
    attached = {
        "id": photo_id,
        "created_at": _now_iso(),
        "original_filename": str(field_record.get("original_filename") or f"zdjecie{ext}"),
        "content_type": field_record.get("content_type"),
        "format": field_record.get("format"),
        "size_bytes": field_record.get("size_bytes"),
        "image_width": field_record.get("image_width"),
        "image_height": field_record.get("image_height"),
        "issue_type": config.DEFAULT_FIELD_PHOTO_ISSUE_TYPE,
        "captured_at": field_record.get("captured_at"),
        "field_photo_created_at": field_record.get("created_at"),
        "field_photo_lat": field_record.get("lat"),
        "field_photo_lon": field_record.get("lon"),
        "source": "field_photo",
        "private_original_file": private_original_file,
        "public_review_status": field_record.get("public_review_status") or "pending",
        "redactions": field_record.get("redactions") or [],
        "reviewed_at": field_record.get("reviewed_at"),
    }
    _write_json(photo_dir / "record.json", attached)
    shutil.rmtree(field_record_dir)
    return attached
