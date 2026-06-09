from __future__ import annotations

import hashlib
import html
import io
import json
import math
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from PIL import Image, UnidentifiedImageError

from core import config
from core.geo import external_map_links, meters_between
from core.json_io import write_json_atomic
from core.map_crops import save_scan_crops
from core.photo_privacy import (
    REVIEW_STATUSES,
    ensure_review_fields,
    generate_public_derivatives,
    is_approved,
    migrate_private_original,
    normalize_redactions,
    safe_child,
    safe_existing_child,
)
from core.photo_privacy import (
    now_iso as privacy_now_iso,
)
from core.uploads import UploadedFile
from core.wreck_photo_transfers import move_field_photo_to_wreck, prepare_field_photo_attachment


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, payload: Any) -> None:
    write_json_atomic(path, payload)


def _private_original_rel(wreck_id: str, photo_id: str, ext: str) -> str:
    ext = ext if ext.startswith(".") else f".{ext}"
    return f"wreck_photos/{wreck_id}/{photo_id}/original{ext.lower()}"


def _safe_original_name(raw_name: str, ext: str) -> str:
    stem = Path(raw_name or "").stem or "zdjecie"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "zdjecie"
    return f"{stem[:80]}{ext}"


def _meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    return meters_between(lat1, lon1, lat2, lon2)


def _wreck_id(lat: float, lon: float) -> str:
    return f"wreck_{int(round(lat * 1_000_000))}_{int(round(lon * 1_000_000))}"


def _validate_coordinates(lat: Any, lon: Any) -> tuple[float, float]:
    try:
        lat_f = float(lat)
        lon_f = float(lon)
    except (TypeError, ValueError) as exc:
        raise ValueError("Podaj prawidłowe współrzędne pojazdu.") from exc
    if not math.isfinite(lat_f) or not math.isfinite(lon_f):
        raise ValueError("Podaj prawidłowe współrzędne pojazdu.")
    if not -90 <= lat_f <= 90 or not -180 <= lon_f <= 180:
        raise ValueError("Współrzędne pojazdu są poza dozwolonym zakresem.")
    return lat_f, lon_f


def _validate_wreck_id(wreck_id: str) -> str:
    if not re.fullmatch(r"wreck_-?\d+_-?\d+", wreck_id):
        raise ValueError("Nieprawidłowy identyfikator sprawy pojazdu.")
    return wreck_id


def _record_dir_for(wreck_id: str, wrecks_dir: Path) -> Path:
    wreck_id = _validate_wreck_id(wreck_id)
    root = wrecks_dir.resolve()
    record_dir = (wrecks_dir / wreck_id).resolve()
    if root != record_dir and root not in record_dir.parents:
        raise ValueError("Nieprawidłowa ścieżka sprawy pojazdu.")
    if not (record_dir / "record.json").exists():
        raise FileNotFoundError("Nie znaleziono zapisanej sprawy pojazdu.")
    return record_dir


def _evidence_id(candidate: dict[str, Any], metadata: dict[str, Any]) -> str:
    payload = {
        "lat": candidate.get("lat"),
        "lon": candidate.get("lon"),
        "score": candidate.get("score"),
        "rank": candidate.get("rank"),
        "labels_present": candidate.get("labels_present"),
        "bbox_4326": metadata.get("bbox_4326"),
        "years": metadata.get("years"),
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()[:14]


def _manual_evidence_id(lat: float, lon: float, created_at: str) -> str:
    payload = f"{lat:.8f}:{lon:.8f}:{created_at}:{secrets.token_urlsafe(8)}"
    digest = hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()[:14]
    return f"manual_{digest}"


def _links(lat: float, lon: float) -> dict[str, str]:
    return external_map_links(lat, lon)


def _wreck_public_file_url(record_id: str, relative_path: Any) -> str | None:
    rel = str(relative_path or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or any(part in {"", ".", ".."} for part in rel.split("/")):
        return None
    return f"/zidentyfikowane_wraki/{quote(record_id, safe='')}/{quote(rel, safe='/._-')}"


def _candidate_by_rank(candidates: list[dict[str, Any]], rank: int) -> dict[str, Any]:
    for candidate in candidates:
        if int(candidate.get("rank") or 0) == rank:
            return candidate
    raise ValueError(f"Brak kandydata o numerze #{rank}. Uruchom aktualną analizę ponownie.")


def _photo_dir(record_dir: Path, photo_id: str) -> Path:
    return record_dir / "photos" / photo_id


def _approved_attached_photos(record: dict[str, Any]) -> list[dict[str, Any]]:
    photos = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
    return [photo for photo in photos if isinstance(photo, dict) and is_approved(photo)]


def _generate_wreck_public_derivatives(photo: dict[str, Any], record_dir: Path) -> None:
    photo_id = str(photo.get("id") or "")
    if not photo_id:
        raise ValueError("Nieprawidłowy identyfikator zdjęcia.")
    photo_record_dir = _photo_dir(record_dir, photo_id)
    local_photo = dict(photo)
    public_image_rel = str(photo.get("public_image_file") or "")
    public_thumb_rel = str(photo.get("public_thumb_file") or "")
    if public_image_rel.startswith(f"photos/{photo_id}/"):
        local_photo["public_image_file"] = Path(public_image_rel).name
    if public_thumb_rel.startswith(f"photos/{photo_id}/"):
        local_photo["public_thumb_file"] = Path(public_thumb_rel).name
    generate_public_derivatives(
        local_photo,
        photo_record_dir,
        config.PRIVATE_PHOTOS_DIR,
        thumb_max_edge=config.WRECK_PHOTO_THUMB_MAX_EDGE_PX,
        thumb_quality=config.WRECK_PHOTO_THUMB_QUALITY,
    )
    photo["public_image_file"] = f"photos/{photo_id}/{local_photo['public_image_file']}"
    photo["public_thumb_file"] = f"photos/{photo_id}/{local_photo['public_thumb_file']}"
    photo["public_width"] = local_photo.get("public_width")
    photo["public_height"] = local_photo.get("public_height")


def _remove_wreck_public_derivatives(photo: dict[str, Any], record_dir: Path) -> None:
    for key in ("public_image_file", "public_thumb_file"):
        path = safe_existing_child(record_dir, photo.get(key))
        if path:
            path.unlink()
    for key in ("public_image_file", "public_thumb_file", "public_width", "public_height"):
        photo.pop(key, None)


def _migrate_attached_photo(record_dir: Path, record: dict[str, Any], photo: dict[str, Any]) -> bool:
    changed = ensure_review_fields(photo)
    wreck_id = str(record.get("id") or record_dir.name)
    photo_id = str(photo.get("id") or "")
    if not photo_id:
        return changed
    photo_record_dir = _photo_dir(record_dir, photo_id)
    photo_record_dir.mkdir(parents=True, exist_ok=True)
    changed = (
        migrate_private_original(
            photo,
            record_dir,
            config.PRIVATE_PHOTOS_DIR,
            scope="wreck_photos",
            photo_id=photo_id,
            owner_id=wreck_id,
        )
        or changed
    )
    for legacy_key in ("thumb_file", "thumbnail_file", "original_url", "original_path"):
        if legacy_key in photo:
            photo.pop(legacy_key, None)
            changed = True
    if not is_approved(photo):
        _remove_wreck_public_derivatives(photo, record_dir)
    elif not safe_existing_child(record_dir, photo.get("public_image_file")) or not safe_existing_child(
        record_dir, photo.get("public_thumb_file")
    ):
        _generate_wreck_public_derivatives(photo, record_dir)
        changed = True
    _write_json(photo_record_dir / "record.json", photo)
    return changed


def _migrate_wreck_record(record_dir: Path, record: dict[str, Any]) -> bool:
    changed = False
    if "public_review_status" not in record:
        record["public_review_status"] = "approved"
        changed = True
    changed = ensure_review_fields(record) or changed
    if "submission_owner" not in record:
        record["submission_owner"] = None
        changed = True
    if is_approved(record) and record.get("reviewed_at") is None:
        record["reviewed_at"] = record.get("created_at") or _now_iso()
        changed = True
    attached = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
    for photo in attached:
        if isinstance(photo, dict):
            changed = _migrate_attached_photo(record_dir, record, photo) or changed
    if attached and record.get("attached_photos") is not attached:
        record["attached_photos"] = attached
        changed = True
    return changed


def _load_records(wrecks_dir: Path) -> list[dict[str, Any]]:
    if not wrecks_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(wrecks_dir.glob("*/record.json")):
        try:
            record = _read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(record, dict) and record.get("id"):
            if _migrate_wreck_record(path.parent, record):
                _write_json(path, record)
            records.append(record)
    return records


def _find_existing_record(wrecks_dir: Path, lat: float, lon: float) -> tuple[dict[str, Any] | None, float | None]:
    best: dict[str, Any] | None = None
    best_dist: float | None = None
    for record in _load_records(wrecks_dir):
        try:
            dist = _meters_between(lat, lon, float(record["lat"]), float(record["lon"]))
        except (KeyError, TypeError, ValueError):
            continue
        if dist <= config.WRECK_DEDUPE_M and (best_dist is None or dist < best_dist):
            best = record
            best_dist = dist
    return best, best_dist


def _crop_year_from_name(path: Path) -> str:
    match = re.match(r"cand_\d+_(.+)\.jpe?g$", path.name, flags=re.IGNORECASE)
    return match.group(1) if match else path.stem


def _copy_candidate_crops(rank: int, analysis_dir: Path, evidence_dir: Path) -> list[dict[str, str]]:
    src_dir = analysis_dir / "crops"
    prefix = f"cand_{rank - 1:03d}_"
    copied: list[dict[str, str]] = []
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for src in sorted(src_dir.glob(f"{prefix}*.jpg")):
        year = _crop_year_from_name(src)
        dst_name = f"{year}.jpg"
        dst = evidence_dir / dst_name
        shutil.copy2(src, dst)
        copied.append({"label": year, "file": dst_name})
    if not copied:
        raise FileNotFoundError(f"Brak miniatur dla kandydata #{rank} w {src_dir}.")
    return copied


def _save_manual_evidence(
    *,
    lat: float,
    lon: float,
    data_dir: Path,
    record_dir: Path,
    created_at: str,
    crop_m: Any,
    links: dict[str, str],
) -> dict[str, Any]:
    evidence_id = _manual_evidence_id(lat, lon, created_at)
    evidence_rel = f"evidence/{evidence_id}"
    evidence_dir = record_dir / evidence_rel
    crops, metadata = save_scan_crops(lat, lon, data_dir, evidence_dir, crop_m=crop_m)
    labels = [crop["label"] for crop in crops]
    evidence = {
        "id": evidence_id,
        "created_at": created_at,
        "rank": None,
        "score": None,
        "lat": lat,
        "lon": lon,
        "labels_present": labels,
        "path": evidence_rel,
        "crops": crops,
        "links": links,
        "source": "manual_inspection",
        "crop_m": float(crop_m),
    }
    _write_json(evidence_dir / "links.json", links)
    _write_json(evidence_dir / "metadata.json", metadata)
    _write_json(
        evidence_dir / "manual_inspection.json",
        {
            "source": "manual_inspection",
            "created_at": created_at,
            "lat": lat,
            "lon": lon,
            "links": links,
            "crop_m": float(crop_m),
            "labels_present": labels,
        },
    )
    return evidence


def _first_last_year(labels: list[Any]) -> tuple[int | None, int | None]:
    years = sorted(int(label) for label in labels if str(label).isdigit())
    if not years:
        return None, None
    return years[0], years[-1]


def _evidence_previews(record: dict[str, Any]) -> list[dict[str, str]]:
    record_id = str(record.get("id") or "")
    previews: list[dict[str, str]] = []

    latest = record.get("latest_evidence") if isinstance(record.get("latest_evidence"), dict) else {}
    evidence_path = str(latest.get("path") or "").strip("/")
    crops = latest.get("crops") if isinstance(latest.get("crops"), list) else []
    for crop in crops:
        if not isinstance(crop, dict):
            continue
        file_name = str(crop.get("file") or "").strip("/")
        if not file_name:
            continue
        url = _wreck_public_file_url(record_id, f"{evidence_path}/{file_name}")
        if not url:
            continue
        label = str(crop.get("label") or "evidence")
        previews.append(
            {
                "source": "evidence",
                "label": label,
                "public_image": url,
                "public_thumb": url,
            }
        )
        if len(previews) >= config.WRECK_POPUP_PREVIEW_MAX_IMAGES:
            return previews
    return previews


def _field_photo_previews(record: dict[str, Any]) -> list[dict[str, str]]:
    record_id = str(record.get("id") or "")
    previews: list[dict[str, str]] = []

    for photo in _approved_attached_photos(record):
        thumb_url = _wreck_public_file_url(record_id, photo.get("public_thumb_file"))
        public_url = _wreck_public_file_url(record_id, photo.get("public_image_file")) or thumb_url
        if not thumb_url:
            continue
        label = str(photo.get("original_filename") or "photo")
        previews.append(
            {
                "source": "attached",
                "label": label,
                "public_image": public_url or thumb_url,
                "public_thumb": thumb_url,
            }
        )
        if len(previews) >= config.WRECK_POPUP_PREVIEW_MAX_IMAGES:
            break
    return previews


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    latest = record.get("latest_evidence") or {}
    all_attached_photos = record.get("attached_photos") if isinstance(record.get("attached_photos"), list) else []
    attached_photos = _approved_attached_photos(record)
    return {
        "id": record["id"],
        "status": record.get("status", "confirmed"),
        "public_review_status": record.get("public_review_status"),
        "reviewed_at": record.get("reviewed_at"),
        "lat": record.get("lat"),
        "lon": record.get("lon"),
        "best_score": record.get("best_score"),
        "labels_present": record.get("labels_present") or [],
        "first_seen_year": record.get("first_seen_year"),
        "last_seen_year": record.get("last_seen_year"),
        "evidence_count": len(record.get("evidences") or []),
        "updated_at": record.get("updated_at"),
        "latest_evidence_id": latest.get("id"),
        "photo_count": len(attached_photos),
        "review_photo_count": len(all_attached_photos),
        "evidence_previews": _evidence_previews(record),
        "field_photo_previews": _field_photo_previews(record),
        "folder_url": f"/zidentyfikowane_wraki/{record['id']}/index.html",
        "links": record.get("links") or {},
    }


def list_wrecks(wrecks_dir: Path, *, include_pending: bool = False) -> list[dict[str, Any]]:
    return [_summary(record) for record in _load_records(wrecks_dir) if include_pending or is_approved(record)]


def list_wreck_review_items(wrecks_dir: Path, *, status: str = "pending") -> list[dict[str, Any]]:
    items = []
    for record in _load_records(wrecks_dir):
        review_status = str(record.get("public_review_status") or "pending")
        if status != "all" and review_status != status:
            continue
        items.append(
            {
                "id": record.get("id"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "public_review_status": review_status,
                "reviewed_at": record.get("reviewed_at"),
                "lat": record.get("lat"),
                "lon": record.get("lon"),
                "source": record.get("source") or record.get("status"),
                "photo_count": len(record.get("attached_photos") or []),
                "evidence_count": len(record.get("evidences") or []),
                "links": record.get("links") or {},
                "folder_url": f"/zidentyfikowane_wraki/{record['id']}/index.html",
            }
        )
    return sorted(items, key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)


def review_wreck(
    wreck_id: str,
    wrecks_dir: Path,
    *,
    status: Any,
) -> dict[str, Any]:
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    status_text = str(status or "").strip()
    if status_text not in REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status przeglądu sprawy.")
    _migrate_wreck_record(record_dir, record)
    record["public_review_status"] = status_text
    record["reviewed_at"] = privacy_now_iso() if status_text in {"approved", "rejected"} else None
    record["updated_at"] = _now_iso()
    _write_json(record_dir / "record.json", record)
    _render_record_html(record, record_dir)
    return {"status": "ok", "wreck": _summary(record)}


def wreck_is_public(wreck_id: str, wrecks_dir: Path) -> bool:
    try:
        record_dir = _record_dir_for(wreck_id, wrecks_dir)
        record = _read_json(record_dir / "record.json")
    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict):
        return False
    if _migrate_wreck_record(record_dir, record):
        _write_json(record_dir / "record.json", record)
    return is_approved(record)


def list_wreck_photo_review_items(wrecks_dir: Path) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for record in _load_records(wrecks_dir):
        wreck_id = str(record.get("id") or "")
        for photo in record.get("attached_photos") or []:
            if not isinstance(photo, dict):
                continue
            photo_id = str(photo.get("id") or "")
            if not photo_id:
                continue
            public_image = (
                _wreck_public_file_url(wreck_id, photo.get("public_image_file")) if is_approved(photo) else None
            )
            public_thumb = (
                _wreck_public_file_url(wreck_id, photo.get("public_thumb_file")) if is_approved(photo) else None
            )
            items.append(
                {
                    "scope": "wreck",
                    "id": f"wreck:{wreck_id}:{photo_id}",
                    "wreck_id": wreck_id,
                    "photo_id": photo_id,
                    "created_at": photo.get("created_at"),
                    "original_filename": photo.get("original_filename"),
                    "public_review_status": photo.get("public_review_status"),
                    "reviewed_at": photo.get("reviewed_at"),
                    "redactions": photo.get("redactions") or [],
                    "original_image": f"/api/admin/photos/wreck/{quote(wreck_id, safe='')}/{quote(photo_id, safe='')}/original",
                    "public_image": public_image,
                    "public_thumb": public_thumb,
                }
            )
    return sorted(items, key=lambda item: str(item.get("created_at") or ""), reverse=True)


def _attached_photo_by_id(record: dict[str, Any], photo_id: str) -> dict[str, Any]:
    for photo in record.get("attached_photos") or []:
        if isinstance(photo, dict) and str(photo.get("id") or "") == photo_id:
            return photo
    raise FileNotFoundError("Nie znaleziono zdjęcia w sprawie pojazdu.")


def review_wreck_photo(
    wreck_id: str,
    photo_id: str,
    wrecks_dir: Path,
    *,
    status: Any,
    redactions: Any,
) -> dict[str, Any]:
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    _migrate_wreck_record(record_dir, record)
    photo = _attached_photo_by_id(record, photo_id)
    status_text = str(status or "").strip()
    if status_text not in REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status przeglądu zdjęcia.")
    photo["redactions"] = normalize_redactions(redactions)
    photo["public_review_status"] = status_text
    photo["reviewed_at"] = privacy_now_iso() if status_text in {"approved", "rejected"} else None
    if status_text == "approved":
        _generate_wreck_public_derivatives(photo, record_dir)
    else:
        _remove_wreck_public_derivatives(photo, record_dir)
    _write_json(_photo_dir(record_dir, photo_id) / "record.json", photo)
    record["updated_at"] = _now_iso()
    _write_json(record_dir / "record.json", record)
    _render_record_html(record, record_dir)
    return {"status": "ok", "photo": photo, "wreck": _summary(record)}


def delete_wreck_photo(wreck_id: str, photo_id: str, wrecks_dir: Path) -> dict[str, Any]:
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    _migrate_wreck_record(record_dir, record)
    photo = _attached_photo_by_id(record, photo_id)
    private_original = safe_child(config.PRIVATE_PHOTOS_DIR, photo.get("private_original_file"))
    _remove_wreck_public_derivatives(photo, record_dir)
    if private_original.exists():
        private_original.unlink()
    photo_dir = _photo_dir(record_dir, photo_id)
    if photo_dir.exists():
        shutil.rmtree(photo_dir)
    record["attached_photos"] = [
        item
        for item in (record.get("attached_photos") or [])
        if not (isinstance(item, dict) and str(item.get("id") or "") == photo_id)
    ]
    record["updated_at"] = _now_iso()
    _write_json(record_dir / "record.json", record)
    _render_record_html(record, record_dir)
    return {"status": "ok", "deleted": photo_id, "wreck": _summary(record)}


def wreck_photo_original_asset(wreck_id: str, photo_id: str, wrecks_dir: Path) -> tuple[Path, str]:
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    if _migrate_wreck_record(record_dir, record):
        _write_json(record_dir / "record.json", record)
    photo = _attached_photo_by_id(record, photo_id)
    path = safe_child(config.PRIVATE_PHOTOS_DIR, photo.get("private_original_file"))
    if not path.exists():
        raise FileNotFoundError("Nie znaleziono prywatnego oryginału zdjęcia.")
    return path, str(photo.get("content_type") or "application/octet-stream")


def public_wreck_asset(wreck_id: str, relative_path: str, wrecks_dir: Path) -> tuple[Path, str]:
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    rel = str(relative_path or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise FileNotFoundError("Nie znaleziono publicznego pliku sprawy pojazdu.")

    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    if _migrate_wreck_record(record_dir, record):
        _write_json(record_dir / "record.json", record)

    suffix = Path(rel).suffix.lower()
    if rel.startswith("evidence/") and suffix in {".jpg", ".jpeg", ".png", ".webp"}:
        path = safe_child(record_dir, rel)
        if not path.exists():
            raise FileNotFoundError("Nie znaleziono publicznego pliku sprawy pojazdu.")
        return path, "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix.removeprefix('.')}"

    if rel.startswith("photos/"):
        for photo in _approved_attached_photos(record):
            if rel in {str(photo.get("public_image_file") or ""), str(photo.get("public_thumb_file") or "")}:
                path = safe_child(record_dir, rel)
                if not path.exists():
                    raise FileNotFoundError("Nie znaleziono publicznego pliku sprawy pojazdu.")
                return path, "image/jpeg"

    raise FileNotFoundError("Nie znaleziono publicznego pliku sprawy pojazdu.")


def delete_wreck(wreck_id: str, wrecks_dir: Path) -> dict[str, Any]:
    wreck_id = _validate_wreck_id(wreck_id)
    root = wrecks_dir.resolve()
    record_dir = (wrecks_dir / wreck_id).resolve()
    if root != record_dir and root not in record_dir.parents:
        raise ValueError("Nieprawidłowa ścieżka sprawy pojazdu.")
    if not record_dir.exists():
        raise FileNotFoundError("Nie znaleziono zapisanej sprawy pojazdu.")
    if not (record_dir / "record.json").exists():
        raise ValueError("Katalog nie wygląda jak sprawa pojazdu.")
    shutil.rmtree(record_dir)
    return {"status": "ok", "deleted": wreck_id}


def _wreck_photo_id(upload: UploadedFile) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    digest = hashlib.sha1(
        f"{upload.filename}:{len(upload.data)}:{secrets.token_urlsafe(12)}".encode(),
        usedforsecurity=False,
    ).hexdigest()[:8]
    return f"photo_{stamp}_{digest}"


def _save_attached_photo(
    upload: UploadedFile, record_dir: Path, *, submission_owner: str | None = None
) -> dict[str, Any]:
    if upload.field_name not in {"photos", "photos[]", "photo"}:
        raise ValueError("Nie znaleziono pola pliku 'photos[]'.")
    size = len(upload.data)
    if size <= 0:
        raise ValueError("Dodaj zdjęcie do uploadu.")
    if size > config.MAX_WRECK_PHOTO_BYTES:
        raise ValueError("Zdjęcie przekracza limit 10 MB.")
    try:
        with Image.open(io.BytesIO(upload.data)) as image:
            image_format = str(image.format or "").upper()
            if image_format not in config.ALLOWED_UPLOAD_IMAGE_FORMATS:
                raise ValueError("Dozwolone są tylko zdjęcia JPG, PNG albo WebP.")
            width, height = image.size
    except UnidentifiedImageError as exc:
        raise ValueError("Plik nie jest obsługiwanym zdjęciem.") from exc

    ext, content_type = config.ALLOWED_UPLOAD_IMAGE_FORMATS[image_format]
    photo_id = _wreck_photo_id(upload)
    photo_dir = record_dir / "photos" / photo_id
    photo_dir.mkdir(parents=True, exist_ok=False)
    private_original_file = _private_original_rel(record_dir.name, photo_id, ext)
    original_path = safe_child(config.PRIVATE_PHOTOS_DIR, private_original_file)
    original_path.parent.mkdir(parents=True, exist_ok=True)
    original_path.write_bytes(upload.data)
    photo = {
        "id": photo_id,
        "created_at": _now_iso(),
        "original_filename": _safe_original_name(upload.filename, ext),
        "content_type": content_type,
        "format": image_format,
        "size_bytes": size,
        "image_width": width,
        "image_height": height,
        "private_original_file": private_original_file,
        "public_review_status": "pending",
        "redactions": [],
        "reviewed_at": None,
        "submission_owner": submission_owner,
    }
    _write_json(photo_dir / "record.json", photo)
    return photo


def attach_field_photos_to_wreck(
    wreck_id: str,
    photo_ids: list[Any],
    field_photos_dir: Path,
    wrecks_dir: Path,
) -> dict[str, Any]:
    if not photo_ids:
        raise ValueError("Wybierz co najmniej jedno zdjęcie terenowe.")
    unique_photo_ids = list(dict.fromkeys(str(photo_id or "").strip() for photo_id in photo_ids))
    if len(unique_photo_ids) > config.MAX_WRECK_PHOTOS_PER_UPLOAD:
        raise ValueError(f"Możesz dodać maksymalnie {config.MAX_WRECK_PHOTOS_PER_UPLOAD} zdjęć naraz.")

    wreck_record_dir = _record_dir_for(wreck_id, wrecks_dir)
    wreck_record = _read_json(wreck_record_dir / "record.json")
    if not isinstance(wreck_record, dict):
        raise ValueError("Nieprawidłowy format record.json.")

    field_records: list[tuple[Path, dict[str, Any]]] = []
    for photo_id in unique_photo_ids:
        field_records.append(prepare_field_photo_attachment(photo_id, field_photos_dir, wreck_record_dir))

    attached = wreck_record.get("attached_photos")
    if not isinstance(attached, list):
        attached = []
    moved = []
    for field_record_dir, field_record in field_records:
        moved_photo = move_field_photo_to_wreck(field_record, field_record_dir, wreck_record_dir)
        _migrate_attached_photo(wreck_record_dir, wreck_record, moved_photo)
        moved.append(moved_photo)
        attached.append(moved_photo)
        wreck_record["attached_photos"] = attached
        wreck_record["updated_at"] = _now_iso()
        _write_json(wreck_record_dir / "record.json", wreck_record)
    _render_record_html(wreck_record, wreck_record_dir)
    return {
        "status": "ok",
        "wreck_id": wreck_id,
        "photos": moved,
        "attached_count": len(moved),
        "photo_count": len(attached),
        "removed_field_photo_ids": unique_photo_ids,
        "wreck": _summary(wreck_record),
    }


def attach_wreck_photos(wreck_id: str, uploads: list[UploadedFile], wrecks_dir: Path) -> dict[str, Any]:
    return attach_wreck_photos_for_submission(wreck_id, uploads, wrecks_dir, submission_owner=None)


def attach_wreck_photos_for_submission(
    wreck_id: str,
    uploads: list[UploadedFile],
    wrecks_dir: Path,
    *,
    submission_owner: str | None,
) -> dict[str, Any]:
    photo_uploads = [upload for upload in uploads if upload.filename or upload.data]
    if not photo_uploads:
        raise ValueError("Wybierz co najmniej jedno zdjęcie.")
    if len(photo_uploads) > config.MAX_WRECK_PHOTOS_PER_UPLOAD:
        raise ValueError(f"Możesz dodać maksymalnie {config.MAX_WRECK_PHOTOS_PER_UPLOAD} zdjęć naraz.")

    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")

    attached = record.get("attached_photos")
    if not isinstance(attached, list):
        attached = []
    saved = [_save_attached_photo(upload, record_dir, submission_owner=submission_owner) for upload in photo_uploads]
    attached.extend(saved)
    record["attached_photos"] = attached
    record["updated_at"] = _now_iso()
    _write_json(record_dir / "record.json", record)
    _render_record_html(record, record_dir)
    return {"status": "ok", "photos": saved, "photo_count": len(attached), "wreck": _summary(record)}


def _compact_years(labels: list[Any]) -> str:
    years = sorted(int(label) for label in labels if str(label).isdigit())
    if not years:
        return "brak danych"
    if len(years) >= 3:
        return f"{years[0]}-{years[-1]} ({len(years)} lat)"
    return ", ".join(str(year) for year in years)


def _compact_datetime(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "brak danych"
    return text.replace("T", " ").removesuffix("Z")


def _attached_photo_sections(record: dict[str, Any]) -> str:
    photos = _approved_attached_photos(record)
    if not photos:
        return ""
    cards: list[str] = []
    for photo in photos:
        public_image = html.escape(str(photo.get("public_image_file") or ""))
        public_thumb = html.escape(str(photo.get("public_thumb_file") or photo.get("public_image_file") or ""))
        if not public_image or not public_thumb:
            continue
        name = html.escape(str(photo.get("original_filename") or "zdjęcie"))
        created_at = html.escape(_compact_datetime(photo.get("captured_at") or photo.get("created_at")))
        cards.append(
            f"""
            <figure class="photo-card">
              <a href="{public_image}" target="_blank" rel="noopener"><img src="{public_thumb}" loading="lazy" alt=""></a>
              <figcaption>{name}<span>{created_at}</span><a href="{public_image}" download>Pobierz zdjęcie</a></figcaption>
            </figure>
            """
        )
    if not cards:
        return ""
    return f"""
    <section class="evidence attached-photos" id="zdjecia-z-miejsca">
      <h2>Zdjęcia z miejsca</h2>
      <div class="grid photo-grid">{"".join(cards)}</div>
    </section>
    """


def _photo_upload_section(wreck_id: str) -> str:
    safe_id = html.escape(wreck_id)
    accept_types = ",".join(sorted(config.ALLOWED_UPLOAD_IMAGE_FORMATS))
    allowed_types = json.dumps(sorted(config.ALLOWED_UPLOAD_IMAGE_FORMATS))
    max_mb = config.MAX_WRECK_PHOTO_BYTES // config.BYTES_PER_MIB
    return f"""
    <section class="evidence photo-upload" data-report-photo-upload>
      <h2>Dodaj zdjęcia do sprawy</h2>
      <form id="wreck-photo-form">
        <input type="file" id="wreck-photo-files" name="photos[]" accept="{accept_types}" multiple required>
        <p>JPG, PNG albo WebP, maks. {max_mb} MB każde, do {config.MAX_WRECK_PHOTOS_PER_UPLOAD} zdjęć naraz.</p>
        <button type="submit">Dodaj zdjęcia</button>
      </form>
      <p id="wreck-photo-status">Zdjęcia dodane publicznie trafią do weryfikacji administratora.</p>
    </section>
    <script data-report-photo-upload-script>
    (() => {{
      const wreckId = {json.dumps(wreck_id)};
      const form = document.getElementById('wreck-photo-form');
      const input = document.getElementById('wreck-photo-files');
      const submit = form?.querySelector('button[type="submit"]');
      const status = document.getElementById('wreck-photo-status');
      const maxBytes = {config.MAX_WRECK_PHOTO_BYTES};
      const maxFiles = {config.MAX_WRECK_PHOTOS_PER_UPLOAD};
      const allowed = new Set({allowed_types});
      form?.addEventListener('submit', async event => {{
        event.preventDefault();
        const files = Array.from(input?.files || []);
        if (!files.length) return;
        if (files.length > maxFiles) {{
          if (status) status.textContent = `Wybierz maksymalnie ${{maxFiles}} zdjęć naraz.`;
          return;
        }}
        for (const file of files) {{
          if (file.size > maxBytes || (file.type && !allowed.has(file.type))) {{
            if (status) status.textContent = 'Dozwolone są tylko zdjęcia JPG, PNG albo WebP do {max_mb} MB.';
            return;
          }}
        }}
        if (status) status.textContent = 'Dodaję zdjęcia...';
        const data = new FormData(form);
        const resp = await fetch(`/api/wrecks/${{encodeURIComponent(wreckId)}}/photos`, {{ method: 'POST', body: data }});
        const payload = await resp.json().catch(() => ({{}}));
        if (!resp.ok || payload.status !== 'ok') {{
          if (status) status.textContent = payload.error || 'Nie udało się dodać zdjęć.';
          return;
        }}
        location.reload();
      }});
    }})();
    </script>
    <!-- Upload endpoint: /api/wrecks/{safe_id}/photos -->
    """


def _render_record_html(record: dict[str, Any], record_dir: Path) -> None:
    if _migrate_wreck_record(record_dir, record):
        _write_json(record_dir / "record.json", record)
    title = f"Sprawa pojazdu {record['id']}"
    latest = record.get("latest_evidence") or {}
    attached_photos = _approved_attached_photos(record)
    evidence_sections: list[str] = []
    for evidence in reversed(record.get("evidences") or []):
        crop_cards: list[str] = []
        raw_evidence_path = str(evidence["path"])
        evidence_path = html.escape(raw_evidence_path)
        evidence_labels = ", ".join(str(label) for label in evidence.get("labels_present") or [])
        for crop in evidence.get("crops") or []:
            label = html.escape(str(crop.get("label", "")))
            file_name = html.escape(str(crop.get("file", "")))
            crop_cards.append(
                f'<figure><img src="{evidence_path}/{file_name}" loading="lazy"><figcaption>{label}</figcaption></figure>'
            )
        metadata_links = []
        evidence_dir = record_dir / raw_evidence_path
        for file_name in ("candidate.json", "manual_inspection.json", "metadata.json", "links.json"):
            if (evidence_dir / file_name).exists():
                safe_file_name = html.escape(file_name)
                metadata_links.append(f'<a href="{evidence_path}/{safe_file_name}">{safe_file_name}</a>')
        metadata_links_html = f"<p>{' · '.join(metadata_links)}</p>" if metadata_links else ""
        evidence_meta = ["punkt ręczny" if evidence.get("rank") is None else f"Rank #{evidence.get('rank')}"]
        if evidence.get("score") is not None:
            evidence_meta.append(f"score {(float(evidence.get('score') or 0) * 100):.0f}%")
        evidence_meta.append(f"lata: {html.escape(evidence_labels)}")
        evidence_sections.append(
            f"""
            <section class="evidence">
              <h2>Dowód {html.escape(evidence["id"])} · {html.escape(evidence["created_at"])}</h2>
              <p>{" · ".join(evidence_meta)}</p>
              <div class="grid">{"".join(crop_cards)}</div>
              {metadata_links_html}
            </section>
            """
        )

    links = record.get("links") or {}
    links_html = " · ".join(
        f'<a href="{html.escape(str(url))}" target="_blank" rel="noopener">{html.escape(name.replace("_", " "))}</a>'
        for name, url in links.items()
    )
    record_labels = _compact_years(record.get("labels_present") or [])
    status = html.escape(str(record.get("status", "confirmed")))
    score = float(record.get("best_score") or 0) * 100
    evidence_count = len(record.get("evidences") or [])
    photo_count = len(attached_photos)
    html_body = f"""<!doctype html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; --bg:#0b0f19; --card:#111827; --bdr:#1f2937; --txt:#e5e7eb; --mut:#94a3b8; --acc:#10b981; }}
    body {{ margin:0; background:var(--bg); color:var(--txt); font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
    main {{ max-width:1280px; margin:0 auto; padding:28px; }}
    .hero,.evidence {{ background:var(--card); border:1px solid var(--bdr); border-radius:12px; padding:18px; margin-bottom:18px; }}
    h1,h2 {{ margin:0 0 10px; }}
    p {{ color:var(--mut); line-height:1.5; overflow-wrap:anywhere; }}
    a {{ color:#93c5fd; text-decoration:none; }}
    a:hover {{ text-decoration:underline; }}
    .score {{ color:var(--acc); font-weight:800; }}
    .hero {{ padding:14px 16px; }}
    .hero-head {{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; }}
    .hero h1 {{ font-size:clamp(20px,3vw,30px); margin:0; overflow-wrap:anywhere; }}
    .status-pill {{ border:1px solid rgba(16,185,129,.35); border-radius:999px; padding:4px 9px; color:#bbf7d0; background:rgba(16,185,129,.08); font-size:12px; font-weight:800; white-space:nowrap; }}
    .metric-strip {{ display:flex; flex-wrap:wrap; gap:6px; margin:0 0 8px; }}
    .metric {{ border:1px solid rgba(148,163,184,.16); border-radius:999px; padding:5px 9px; background:#0f172a; color:#cbd5e1; font-size:12px; line-height:1.2; }}
    .metric b {{ color:#94a3b8; font-weight:700; margin-right:4px; }}
    .link-strip {{ display:flex; flex-wrap:wrap; gap:6px 10px; font-size:12px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }}
    figure {{ margin:0; border:1px solid var(--bdr); border-radius:8px; overflow:hidden; background:#0f172a; }}
    img {{ width:100%; aspect-ratio:1; object-fit:cover; display:block; }}
    figcaption {{ padding:8px; color:var(--mut); font-size:12px; text-align:center; }}
    figcaption span {{ display:block; margin-top:3px; color:#64748b; font-size:11px; }}
    .photo-grid {{ grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); }}
    .photo-upload form {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px; }}
    .photo-upload input {{ max-width:360px; color:var(--mut); }}
    .photo-upload button {{ border:0; border-radius:8px; padding:9px 12px; background:#2563eb; color:white; font-weight:800; cursor:pointer; }}
    .photo-upload p {{ margin:4px 0 0; font-size:12px; }}
    .report-mail-draft pre {{ white-space:pre-wrap; overflow-wrap:anywhere; word-break:break-word; max-width:100%; box-sizing:border-box; }}
  </style>
</head>
<body>
<main>
  <section class="hero">
    <div class="hero-head">
      <h1>{html.escape(title)}</h1>
      <span class="status-pill">{status}</span>
    </div>
    <div class="metric-strip">
      <span class="metric"><b>GPS</b>{record["lat"]:.6f}, {record["lon"]:.6f}</span>
      <span class="metric"><b>score</b><span class="score">{score:.0f}%</span></span>
      <span class="metric"><b>widziane</b>{html.escape(record_labels)}</span>
      <span class="metric"><b>dowody</b>{evidence_count}</span>
      <span class="metric"><b>zdjęcia</b>{photo_count}</span>
      <span class="metric"><b>ostatni dowód</b>{html.escape(_compact_datetime(latest.get("created_at")))}</span>
    </div>
    <nav class="link-strip">{links_html}</nav>
  </section>
  {_attached_photo_sections(record)}
  {"".join(evidence_sections)}
  {_photo_upload_section(record["id"])}
</main>
</body>
</html>
"""
    (record_dir / "index.html").write_text(html_body, encoding="utf-8")


def render_wreck_record_html(record: dict[str, Any], record_dir: Path) -> None:
    _render_record_html(record, record_dir)


def refresh_wreck_report(wreck_id: str, wrecks_dir: Path) -> Path:
    record_dir = _record_dir_for(wreck_id, wrecks_dir)
    record = _read_json(record_dir / "record.json")
    if not isinstance(record, dict):
        raise ValueError("Nieprawidłowy format record.json.")
    _render_record_html(record, record_dir)
    return record_dir / "index.html"


def save_manual_wreck(
    lat: Any,
    lon: Any,
    data_dir: Path,
    wrecks_dir: Path,
    *,
    crop_m: Any = config.REVIEW_CROP_M,
    public_review_status: str = "approved",
    submission_owner: str | None = None,
) -> dict[str, Any]:
    lat_f, lon_f = _validate_coordinates(lat, lon)
    if public_review_status not in REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status przeglądu sprawy.")
    existing, distance_m = _find_existing_record(wrecks_dir, lat_f, lon_f)
    if existing:
        if not is_approved(existing) and public_review_status == "approved":
            existing["public_review_status"] = "approved"
            existing["reviewed_at"] = privacy_now_iso()
        record_dir = wrecks_dir / existing["id"]
        _render_record_html(existing, record_dir)
        return {
            "status": "ok",
            "created": False,
            "evidence_created": False,
            "dedupe_distance_m": round(distance_m, 2) if distance_m is not None else None,
            "wreck": _summary(existing),
        }

    created_at = _now_iso()
    links = _links(lat_f, lon_f)
    wreck_id = _wreck_id(lat_f, lon_f)
    record_dir = wrecks_dir / wreck_id
    evidence = _save_manual_evidence(
        lat=lat_f,
        lon=lon_f,
        data_dir=data_dir,
        record_dir=record_dir,
        created_at=created_at,
        crop_m=crop_m,
        links=links,
    )
    labels = [str(label) for label in evidence.get("labels_present") or []]
    first_seen, last_seen = _first_last_year(labels)
    record = {
        "id": wreck_id,
        "status": "manual",
        "lat": lat_f,
        "lon": lon_f,
        "created_at": created_at,
        "updated_at": created_at,
        "best_score": 0.0,
        "labels_present": labels,
        "first_seen_year": first_seen,
        "last_seen_year": last_seen,
        "latest_evidence": evidence,
        "links": links,
        "evidences": [evidence],
        "source": "manual_inspection",
        "public_review_status": public_review_status,
        "reviewed_at": privacy_now_iso() if public_review_status == "approved" else None,
        "reviewed_by": "admin" if public_review_status == "approved" else None,
        "submission_owner": submission_owner,
    }

    _write_json(record_dir / "record.json", record)
    _render_record_html(record, record_dir)

    return {
        "status": "ok",
        "created": True,
        "evidence_created": True,
        "dedupe_distance_m": None,
        "wreck": _summary(record),
    }


def save_wreck_from_rank(
    rank: int,
    analysis_dir: Path,
    data_dir: Path,
    wrecks_dir: Path,
    *,
    public_review_status: str = "approved",
    submission_owner: str | None = None,
) -> dict[str, Any]:
    if public_review_status not in REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status przeglądu sprawy.")
    candidates_path = analysis_dir / "candidates.json"
    metadata_path = data_dir / "metadata.json"
    if not candidates_path.exists():
        raise FileNotFoundError("Brak aktualnych kandydatów. Najpierw uruchom analizę.")
    if not metadata_path.exists():
        raise FileNotFoundError("Brak metadata.json. Najpierw pobierz i przeanalizuj obszar.")

    candidates = _read_json(candidates_path)
    metadata = _read_json(metadata_path)
    if not isinstance(candidates, list):
        raise ValueError("Nieprawidłowy format candidates.json.")

    candidate = _candidate_by_rank(candidates, rank)
    lat = candidate.get("lat")
    lon = candidate.get("lon")
    if lat is None or lon is None:
        raise ValueError(f"Kandydat #{rank} nie ma współrzędnych GPS.")
    lat = float(lat)
    lon = float(lon)

    existing, distance_m = _find_existing_record(wrecks_dir, lat, lon)
    created_record = existing is None
    if existing:
        record = existing
        wreck_id = record["id"]
        if not is_approved(record) and public_review_status == "approved":
            record["public_review_status"] = "approved"
            record["reviewed_at"] = privacy_now_iso()
    else:
        wreck_id = _wreck_id(lat, lon)
        record = {
            "id": wreck_id,
            "status": "confirmed",
            "lat": lat,
            "lon": lon,
            "created_at": _now_iso(),
            "updated_at": None,
            "best_score": 0.0,
            "labels_present": [],
            "first_seen_year": None,
            "last_seen_year": None,
            "latest_evidence": None,
            "links": _links(lat, lon),
            "evidences": [],
            "public_review_status": public_review_status,
            "reviewed_at": privacy_now_iso() if public_review_status == "approved" else None,
            "reviewed_by": "admin" if public_review_status == "approved" else None,
            "submission_owner": submission_owner,
        }

    record_dir = wrecks_dir / wreck_id
    evidence_id = _evidence_id(candidate, metadata)
    evidence_rel = f"evidence/{evidence_id}"
    evidence_dir = record_dir / evidence_rel
    evidence_exists = any(item.get("id") == evidence_id for item in record.get("evidences") or [])
    evidence_created = not evidence_exists

    if evidence_created:
        crops = _copy_candidate_crops(rank, analysis_dir, evidence_dir)
        links = _links(lat, lon)
        _write_json(evidence_dir / "candidate.json", candidate)
        _write_json(evidence_dir / "metadata.json", metadata)
        _write_json(evidence_dir / "links.json", links)
        evidence = {
            "id": evidence_id,
            "created_at": _now_iso(),
            "rank": rank,
            "score": candidate.get("score"),
            "lat": lat,
            "lon": lon,
            "labels_present": candidate.get("labels_present") or [],
            "path": evidence_rel,
            "crops": crops,
            "links": links,
        }
        record.setdefault("evidences", []).append(evidence)

    best_score = max(float(record.get("best_score") or 0.0), float(candidate.get("score") or 0.0))
    record["best_score"] = best_score
    if float(candidate.get("score") or 0.0) >= best_score:
        record["lat"] = lat
        record["lon"] = lon
        record["links"] = _links(lat, lon)
    labels = sorted({str(label) for item in record.get("evidences") or [] for label in item.get("labels_present", [])})
    first_seen, last_seen = _first_last_year(labels)
    record["labels_present"] = labels
    record["first_seen_year"] = first_seen
    record["last_seen_year"] = last_seen
    record["latest_evidence"] = (record.get("evidences") or [])[-1] if record.get("evidences") else None
    record["updated_at"] = _now_iso()

    _write_json(record_dir / "record.json", record)
    _render_record_html(record, record_dir)

    return {
        "status": "ok",
        "created": created_record,
        "evidence_created": evidence_created,
        "dedupe_distance_m": round(distance_m, 2) if distance_m is not None else None,
        "wreck": _summary(record),
    }
