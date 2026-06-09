from __future__ import annotations

import hashlib
import io
import json
import math
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PIL import ExifTags, Image, UnidentifiedImageError

from core import config
from core.geo import external_map_links
from core.photo_privacy import (
    apply_review_update,
    ensure_review_fields,
    generate_public_derivatives,
    is_approved,
    migrate_private_original,
    remove_public_derivatives,
    review_status,
    safe_child,
    safe_existing_child,
)
from core.uploads import UploadedFile

FIELD_PHOTO_ID_RE = re.compile(r"photo_\d{8}T\d{6}Z_[a-f0-9]{8}")
EXIF_GPS_IFD = ExifTags.IFD.GPSInfo


def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _now_iso() -> str:
    return _now_utc().isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _safe_text(value: Any, max_len: int = 300) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:max_len]


def _safe_original_name(raw_name: str, ext: str) -> str:
    stem = Path(raw_name or "").stem or "zdjecie"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "zdjecie"
    return f"{stem[:80]}{ext}"


def _validate_photo_id(photo_id: str) -> str:
    if not FIELD_PHOTO_ID_RE.fullmatch(photo_id):
        raise ValueError("Nieprawidłowy identyfikator zdjęcia.")
    return photo_id


def _record_dir_for(photo_id: str, storage_dir: Path) -> Path:
    photo_id = _validate_photo_id(photo_id)
    root = storage_dir.resolve()
    record_dir = (storage_dir / photo_id).resolve()
    if root != record_dir and root not in record_dir.parents:
        raise ValueError("Nieprawidłowa ścieżka zdjęcia.")
    return record_dir


def _rational_to_float(value: Any) -> float:
    if isinstance(value, tuple) and len(value) == 2:
        denominator = float(value[1])
        if denominator == 0:
            raise ValueError("Nieprawidłowa wartość EXIF GPS.")
        number = float(value[0]) / denominator
    else:
        try:
            number = float(value)
        except ZeroDivisionError as exc:
            raise ValueError("Nieprawidłowa wartość EXIF GPS.") from exc
    if not math.isfinite(number):
        raise ValueError("Nieprawidłowa wartość EXIF GPS.")
    return number


def _dms_to_decimal(values: Any, ref: Any) -> float | None:
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        return None
    degrees = _rational_to_float(values[0])
    minutes = _rational_to_float(values[1])
    seconds = _rational_to_float(values[2])
    decimal = degrees + minutes / 60.0 + seconds / 3600.0
    ref_text = str(ref or "").strip().upper()
    if ref_text in {"S", "W"}:
        decimal *= -1
    return decimal


def _gps_ifd(exif: Image.Exif) -> dict[int, Any]:
    try:
        gps = exif.get_ifd(EXIF_GPS_IFD)
    except Exception:
        gps = exif.get(EXIF_GPS_IFD, {})
    return gps if isinstance(gps, dict) else {}


def _extract_gps(exif: Image.Exif) -> tuple[float | None, float | None]:
    gps = _gps_ifd(exif)
    if not gps:
        return None, None
    try:
        lat = _dms_to_decimal(gps.get(2), gps.get(1))
        lon = _dms_to_decimal(gps.get(4), gps.get(3))
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None
    if lat is None or lon is None:
        return None, None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None, None
    return lat, lon


def _format_exif_datetime(value: Any) -> str | None:
    text = _safe_text(value, 80)
    match = re.fullmatch(r"(\d{4}):(\d{2}):(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text)
    if not match:
        return text or None
    year, month, day, hour, minute, second = match.groups()
    return f"{year}-{month}-{day}T{hour}:{minute}:{second}"


def _limited_exif(exif: Image.Exif) -> dict[str, str]:
    labels = {
        271: "make",
        272: "model",
        306: "datetime",
        36867: "datetime_original",
        36868: "datetime_digitized",
    }
    values: dict[str, str] = {}
    for tag, label in labels.items():
        value = _safe_text(exif.get(tag), 200)
        if value:
            values[label] = value
    return values


def _captured_at(exif: Image.Exif) -> str | None:
    return _format_exif_datetime(exif.get(36867) or exif.get(306))


def _fallback_coord(value: Any, label: str) -> float:
    try:
        coord = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Nieprawidłowa wartość {label}.") from exc
    if not math.isfinite(coord):
        raise ValueError(f"Nieprawidłowa wartość {label}.")
    return coord


def _coordinates_from(
    exif: Image.Exif,
    fallback_lat: Any = None,
    fallback_lon: Any = None,
    *,
    ignore_exif_gps: bool = False,
) -> tuple[float, float, Literal["exif", "map"]]:
    if not ignore_exif_gps:
        exif_lat, exif_lon = _extract_gps(exif)
        if exif_lat is not None and exif_lon is not None:
            return exif_lat, exif_lon, "exif"
    if (
        fallback_lat is None
        or fallback_lon is None
        or str(fallback_lat).strip() == ""
        or str(fallback_lon).strip() == ""
    ):
        raise ValueError("Zdjęcie nie ma współrzędnych GPS w EXIF. Wskaż punkt na mapie i spróbuj ponownie.")
    lat = _fallback_coord(fallback_lat, "fallback_lat")
    lon = _fallback_coord(fallback_lon, "fallback_lon")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Nieprawidłowe współrzędne fallback z mapy.")
    return lat, lon, "map"


def _validated_lat_lon(lat_value: Any, lon_value: Any) -> tuple[float, float]:
    lat = _fallback_coord(lat_value, "lat")
    lon = _fallback_coord(lon_value, "lon")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise ValueError("Nieprawidłowe współrzędne zdjęcia terenowego.")
    return lat, lon


def _issue_type(value: Any) -> str:
    issue_type = str(value or config.DEFAULT_FIELD_PHOTO_ISSUE_TYPE).strip()
    if issue_type not in config.FIELD_PHOTO_ISSUE_TYPES:
        raise ValueError("Nieprawidłowy typ pinezki terenowej.")
    return issue_type


def _links(lat: float, lon: float) -> dict[str, str]:
    return external_map_links(lat, lon)


def _photo_id(upload: UploadedFile) -> str:
    stamp = _now_utc().strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(
        f"{upload.filename}:{len(upload.data)}:{secrets.token_urlsafe(12)}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:8]
    return f"photo_{stamp}_{digest}"


def _private_dir(private_dir: Path | None) -> Path:
    return private_dir or config.PRIVATE_PHOTOS_DIR


def _migrate_field_record(record_dir: Path, record: dict[str, Any], private_dir: Path) -> bool:
    changed = ensure_review_fields(record)
    photo_id = str(record.get("id") or "")
    if photo_id:
        changed = (
            migrate_private_original(
                record,
                record_dir,
                private_dir,
                scope="field_photos",
                photo_id=photo_id,
            )
            or changed
        )
    for legacy_key in ("thumbnail_file", "original_url", "original_path"):
        if legacy_key in record:
            record.pop(legacy_key, None)
            changed = True
    if not is_approved(record):
        remove_public_derivatives(record, record_dir)
    elif not safe_existing_child(record_dir, record.get("public_image_file")) or not safe_existing_child(
        record_dir, record.get("public_thumb_file")
    ):
        generate_public_derivatives(
            record,
            record_dir,
            private_dir,
            thumb_max_edge=config.FIELD_PHOTO_THUMBNAIL_MAX_EDGE_PX,
            thumb_quality=config.FIELD_PHOTO_THUMBNAIL_JPEG_QUALITY,
        )
        changed = True
    return changed


def _load_field_record(record_dir: Path, private_dir: Path) -> dict[str, Any]:
    record_path = record_dir / "record.json"
    if not record_path.exists():
        raise FileNotFoundError("Nie znaleziono zdjęcia terenowego.")
    record = _read_json(record_path)
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    if _migrate_field_record(record_dir, record, private_dir):
        _write_json(record_path, record)
    return record


def _public_file_url(photo_id: str, asset: Literal["public-image", "public-thumb"]) -> str:
    return f"/api/field-photos/{photo_id}/{asset}"


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    photo_id = str(record["id"])
    summary = {
        "id": photo_id,
        "created_at": record.get("created_at"),
        "format": record.get("format"),
        "public_width": record.get("public_width"),
        "public_height": record.get("public_height"),
        "public_review_status": review_status(record),
        "reviewed_at": record.get("reviewed_at"),
        "issue_type": _issue_type(record.get("issue_type")),
        "lat": record.get("lat"),
        "lon": record.get("lon"),
        "coordinate_source": record.get("coordinate_source"),
        "position_updated_at": record.get("position_updated_at"),
        "captured_at": record.get("captured_at"),
        "links": record.get("links") or {},
    }
    if is_approved(record):
        summary["public_image"] = _public_file_url(photo_id, "public-image")
        summary["public_thumb"] = _public_file_url(photo_id, "public-thumb")
    return summary


def list_field_photos(storage_dir: Path, *, private_dir: Path | None = None) -> list[dict[str, Any]]:
    private_root = _private_dir(private_dir)
    if not storage_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(storage_dir.glob("*/record.json")):
        try:
            record = _load_field_record(path.parent, private_root)
        except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError):
            continue
        if isinstance(record, dict) and record.get("id") and review_status(record) != "rejected":
            records.append(_summary(record))
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def list_field_photo_review_items(storage_dir: Path, *, private_dir: Path | None = None) -> list[dict[str, Any]]:
    private_root = _private_dir(private_dir)
    if not storage_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(storage_dir.glob("*/record.json")):
        try:
            record = _load_field_record(path.parent, private_root)
        except (OSError, json.JSONDecodeError, ValueError, FileNotFoundError):
            continue
        if not isinstance(record, dict) or not record.get("id"):
            continue
        photo_id = str(record["id"])
        records.append(
            {
                "scope": "field",
                "id": f"field:{photo_id}",
                "photo_id": photo_id,
                "created_at": record.get("created_at"),
                "original_filename": record.get("original_filename"),
                "issue_type": _issue_type(record.get("issue_type")),
                "lat": record.get("lat"),
                "lon": record.get("lon"),
                "captured_at": record.get("captured_at"),
                "public_review_status": record.get("public_review_status"),
                "reviewed_at": record.get("reviewed_at"),
                "redactions": record.get("redactions") or [],
                "original_image": f"/api/admin/photos/field/{photo_id}/original",
                "public_image": _public_file_url(photo_id, "public-image") if is_approved(record) else None,
                "public_thumb": _public_file_url(photo_id, "public-thumb") if is_approved(record) else None,
            }
        )
    return sorted(records, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def save_field_photo(
    upload: UploadedFile,
    storage_dir: Path,
    *,
    fallback_lat: Any = None,
    fallback_lon: Any = None,
    ignore_exif_gps: bool = False,
    issue_type: Any = None,
    private_dir: Path | None = None,
    submission_owner: str | None = None,
) -> dict[str, Any]:
    private_root = _private_dir(private_dir)
    if upload.field_name != "photo":
        raise ValueError("Nie znaleziono pola pliku 'photo'.")
    size = len(upload.data)
    if size <= 0:
        raise ValueError("Dodaj zdjęcie do uploadu.")
    if size > config.MAX_FIELD_PHOTO_BYTES:
        raise ValueError("Zdjęcie przekracza limit 10 MB.")
    issue_type_text = _issue_type(issue_type)

    try:
        with Image.open(io.BytesIO(upload.data)) as image:
            image_format = str(image.format or "").upper()
            if image_format not in config.ALLOWED_UPLOAD_IMAGE_FORMATS:
                raise ValueError("Dozwolone są tylko zdjęcia JPG, PNG albo WebP.")
            exif = image.getexif()
            lat, lon, coordinate_source = _coordinates_from(
                exif,
                fallback_lat,
                fallback_lon,
                ignore_exif_gps=ignore_exif_gps,
            )
            width, height = image.size
    except UnidentifiedImageError as exc:
        raise ValueError("Plik nie jest obsługiwanym zdjęciem.") from exc

    ext, content_type = config.ALLOWED_UPLOAD_IMAGE_FORMATS[image_format]
    photo_id = _photo_id(upload)
    record_dir = storage_dir / photo_id
    record_dir.mkdir(parents=True, exist_ok=False)

    private_original_file = f"field_photos/{photo_id}/original{ext}"
    record = {
        "id": photo_id,
        "created_at": _now_iso(),
        "original_filename": _safe_original_name(upload.filename, ext),
        "content_type": content_type,
        "format": image_format,
        "size_bytes": size,
        "image_width": width,
        "image_height": height,
        "issue_type": issue_type_text,
        "lat": lat,
        "lon": lon,
        "coordinate_source": coordinate_source,
        "captured_at": _captured_at(exif),
        "exif": _limited_exif(exif),
        "private_original_file": private_original_file,
        "public_review_status": "pending",
        "redactions": [],
        "reviewed_at": None,
        "submission_owner": submission_owner,
        "links": _links(lat, lon),
    }
    original_path = safe_child(private_root, private_original_file)
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(upload.data)
    _write_json(record_dir / "record.json", record)
    return {"status": "ok", "photo": _summary(record)}


def update_field_photo_location(
    photo_id: str,
    storage_dir: Path,
    *,
    lat: Any,
    lon: Any,
    private_dir: Path | None = None,
) -> dict[str, Any]:
    record_dir = _record_dir_for(photo_id, storage_dir)
    record_path = record_dir / "record.json"
    if not record_path.exists():
        raise FileNotFoundError("Nie znaleziono zdjęcia terenowego.")
    record = _load_field_record(record_dir, _private_dir(private_dir))
    lat_float, lon_float = _validated_lat_lon(lat, lon)
    record["lat"] = lat_float
    record["lon"] = lon_float
    record["coordinate_source"] = "manual"
    record["position_updated_at"] = _now_iso()
    record["links"] = _links(lat_float, lon_float)
    _write_json(record_path, record)
    return {"status": "ok", "photo": _summary(record)}


def delete_field_photo(photo_id: str, storage_dir: Path, *, private_dir: Path | None = None) -> dict[str, Any]:
    record_dir = _record_dir_for(photo_id, storage_dir)
    if not record_dir.exists():
        raise FileNotFoundError("Nie znaleziono zdjęcia terenowego.")
    if not (record_dir / "record.json").exists():
        raise ValueError("Katalog nie wygląda jak rekord zdjęcia terenowego.")
    try:
        record = _load_field_record(record_dir, _private_dir(private_dir))
        original = safe_child(_private_dir(private_dir), record.get("private_original_file"))
        if original.exists():
            original.unlink()
    except (FileNotFoundError, ValueError):
        pass
    shutil.rmtree(record_dir)
    return {"status": "ok", "deleted": photo_id}


def field_photo_asset(
    photo_id: str,
    storage_dir: Path,
    asset: Literal["public-image", "public-thumb", "original"],
    *,
    private_dir: Path | None = None,
) -> tuple[Path, str]:
    record_dir = _record_dir_for(photo_id, storage_dir)
    private_root = _private_dir(private_dir)
    record = _load_field_record(record_dir, private_root)
    if asset == "public-thumb":
        if not is_approved(record):
            raise FileNotFoundError("Nie znaleziono publicznej miniatury zdjęcia.")
        file_name = str(record.get("public_thumb_file") or "")
        content_type = "image/jpeg"
        path = safe_child(record_dir, file_name)
    elif asset == "public-image":
        if not is_approved(record):
            raise FileNotFoundError("Nie znaleziono publicznej kopii zdjęcia.")
        file_name = str(record.get("public_image_file") or "")
        content_type = "image/jpeg"
        path = safe_child(record_dir, file_name)
    else:
        file_name = str(record.get("private_original_file") or "")
        content_type = str(record.get("content_type") or "application/octet-stream")
        path = safe_child(private_root, file_name)
    if not path.exists():
        raise FileNotFoundError("Nie znaleziono pliku zdjęcia terenowego.")
    return path, content_type


def review_field_photo(
    photo_id: str,
    storage_dir: Path,
    *,
    status: Any,
    redactions: Any,
    private_dir: Path | None = None,
) -> dict[str, Any]:
    record_dir = _record_dir_for(photo_id, storage_dir)
    record = _load_field_record(record_dir, _private_dir(private_dir))
    apply_review_update(
        record,
        record_dir,
        _private_dir(private_dir),
        status=status,
        redactions=redactions,
        thumb_max_edge=config.FIELD_PHOTO_THUMBNAIL_MAX_EDGE_PX,
        thumb_quality=config.FIELD_PHOTO_THUMBNAIL_JPEG_QUALITY,
    )
    _write_json(record_dir / "record.json", record)
    return {"status": "ok", "photo": _summary(record) if is_approved(record) else {"id": record["id"]}}
