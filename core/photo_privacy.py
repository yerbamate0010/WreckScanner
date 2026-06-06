from __future__ import annotations

import io
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PIL import Image, ImageDraw, ImageOps

from core import config

ReviewStatus = Literal["pending", "approved", "rejected"]

REVIEW_STATUSES = {"pending", "approved", "rejected"}
DEFAULT_REVIEW_STATUS: ReviewStatus = "pending"
PUBLIC_IMAGE_FILE = "public.jpg"
PUBLIC_THUMB_FILE = "public_thumb.jpg"
REDACTION_FILL = (15, 23, 42)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def safe_child(base_dir: Path, relative_path: Any) -> Path:
    rel = str(relative_path or "").replace("\\", "/").strip("/")
    if not rel or rel.startswith("/") or any(part in {"", ".", ".."} for part in rel.split("/")):
        raise ValueError("Nieprawidłowa ścieżka pliku zdjęcia.")
    root = base_dir.resolve()
    path = (base_dir / rel).resolve()
    if root != path and root not in path.parents:
        raise ValueError("Nieprawidłowa ścieżka pliku zdjęcia.")
    return path


def safe_existing_child(base_dir: Path, relative_path: Any) -> Path | None:
    try:
        path = safe_child(base_dir, relative_path)
    except ValueError:
        return None
    return path if path.exists() else None


def review_status(record: dict[str, Any]) -> ReviewStatus:
    value = str(record.get("public_review_status") or DEFAULT_REVIEW_STATUS).strip()
    if value not in REVIEW_STATUSES:
        return DEFAULT_REVIEW_STATUS
    return value  # type: ignore[return-value]


def is_approved(record: dict[str, Any]) -> bool:
    return review_status(record) == "approved"


def ensure_review_fields(record: dict[str, Any]) -> bool:
    changed = False
    if review_status(record) != record.get("public_review_status"):
        record["public_review_status"] = review_status(record)
        changed = True
    try:
        normalized_redactions = normalize_redactions(record.get("redactions") or [])
    except ValueError:
        record["redactions"] = []
        changed = True
    else:
        if normalized_redactions != record.get("redactions"):
            record["redactions"] = normalized_redactions
            changed = True
    if "reviewed_at" not in record:
        record["reviewed_at"] = None
        changed = True
    return changed


def private_original_rel(scope: Literal["field_photos", "wreck_photos"], photo_id: str, ext: str, owner_id: str = "") -> str:
    ext = ext if ext.startswith(".") else f".{ext}"
    if scope == "field_photos":
        return f"field_photos/{photo_id}/original{ext.lower()}"
    return f"wreck_photos/{owner_id}/{photo_id}/original{ext.lower()}"


def migrate_private_original(
    record: dict[str, Any],
    record_dir: Path,
    private_dir: Path,
    *,
    scope: Literal["field_photos", "wreck_photos"],
    photo_id: str,
    owner_id: str = "",
    old_key: str = "original_file",
) -> bool:
    changed = False
    if record.get("private_original_file"):
        if old_key in record:
            record.pop(old_key, None)
            changed = True
        return changed

    source = safe_existing_child(record_dir, record.get(old_key))
    if source:
        rel = private_original_rel(scope, photo_id, source.suffix or ".jpg", owner_id=owner_id)
        destination = safe_child(private_dir, rel)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.move(str(source), destination)
        elif source.exists() and source.resolve() != destination.resolve():
            source.unlink()
        record["private_original_file"] = rel
        changed = True

    if old_key in record:
        record.pop(old_key, None)
        changed = True
    return changed


def _clamped_point(raw_point: Any) -> dict[str, float]:
    if not isinstance(raw_point, dict):
        raise ValueError("Punkt redakcji musi być obiektem.")
    try:
        x = float(raw_point.get("x"))
        y = float(raw_point.get("y"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Nieprawidłowe współrzędne punktu redakcji.") from exc
    return {
        "x": round(min(max(x, 0.0), 1.0), 6),
        "y": round(min(max(y, 0.0), 1.0), 6),
    }


def _polygon_area(points: list[dict[str, float]]) -> float:
    area = 0.0
    for idx, point in enumerate(points):
        next_point = points[(idx + 1) % len(points)]
        area += point["x"] * next_point["y"] - next_point["x"] * point["y"]
    return abs(area) / 2.0


def _rect_to_points(item: dict[str, Any]) -> list[dict[str, float]]:
    try:
        x = float(item.get("x"))
        y = float(item.get("y"))
        width = float(item.get("width"))
        height = float(item.get("height"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Nieprawidłowe współrzędne redakcji.") from exc
    x = min(max(x, 0.0), 1.0)
    y = min(max(y, 0.0), 1.0)
    width = min(max(width, 0.0), 1.0 - x)
    height = min(max(height, 0.0), 1.0 - y)
    if width <= 0 or height <= 0:
        return []
    return [
        {"x": round(x, 6), "y": round(y, 6)},
        {"x": round(x + width, 6), "y": round(y, 6)},
        {"x": round(x + width, 6), "y": round(y + height, 6)},
        {"x": round(x, 6), "y": round(y + height, 6)},
    ]


def normalize_redactions(raw_redactions: Any) -> list[dict[str, list[dict[str, float]]]]:
    if not isinstance(raw_redactions, list):
        raise ValueError("Redakcje muszą być listą obszarów.")
    normalized: list[dict[str, list[dict[str, float]]]] = []
    for item in raw_redactions:
        if not isinstance(item, dict):
            raise ValueError("Każda redakcja musi być obiektem.")
        raw_points = item.get("points")
        if isinstance(raw_points, list):
            points = [_clamped_point(point) for point in raw_points]
        else:
            points = _rect_to_points(item)
        if len(points) < 3 or _polygon_area(points) < 0.000025:
            continue
        normalized.append({"points": points})
    return normalized


def _rgb_without_exif(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return image.convert("RGB")


def _jpeg_bytes(image: Image.Image, *, quality: int) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def apply_redactions(image: Image.Image, redactions: list[dict[str, list[dict[str, float]]]]) -> Image.Image:
    image = image.copy()
    draw = ImageDraw.Draw(image)
    width, height = image.size
    for item in redactions:
        points = [
            (int(round(point["x"] * width)), int(round(point["y"] * height)))
            for point in item.get("points", [])
        ]
        if len(points) >= 3:
            draw.polygon(points, fill=REDACTION_FILL)
    return image


def generate_public_derivatives(
    record: dict[str, Any],
    record_dir: Path,
    private_dir: Path,
    *,
    thumb_max_edge: int,
    thumb_quality: int,
) -> None:
    private_rel = record.get("private_original_file")
    if not private_rel:
        raise FileNotFoundError("Brak prywatnego oryginału zdjęcia.")
    original_path = safe_child(private_dir, private_rel)
    if not original_path.exists():
        raise FileNotFoundError("Brak prywatnego oryginału zdjęcia.")

    redactions = normalize_redactions(record.get("redactions") or [])
    with Image.open(original_path) as source:
        public_image = apply_redactions(_rgb_without_exif(source), redactions)

    public_path = record_dir / PUBLIC_IMAGE_FILE
    public_thumb_path = record_dir / PUBLIC_THUMB_FILE
    public_path.write_bytes(_jpeg_bytes(public_image, quality=config.PUBLIC_PHOTO_JPEG_QUALITY))

    thumb = public_image.copy()
    thumb.thumbnail((thumb_max_edge, thumb_max_edge), Image.Resampling.LANCZOS)
    public_thumb_path.write_bytes(_jpeg_bytes(thumb, quality=thumb_quality))

    record["public_image_file"] = PUBLIC_IMAGE_FILE
    record["public_thumb_file"] = PUBLIC_THUMB_FILE
    record["public_width"] = public_image.width
    record["public_height"] = public_image.height


def remove_public_derivatives(record: dict[str, Any], record_dir: Path) -> None:
    for key in ("public_image_file", "public_thumb_file"):
        path = safe_existing_child(record_dir, record.get(key))
        if path:
            path.unlink()
    record.pop("public_image_file", None)
    record.pop("public_thumb_file", None)
    record.pop("public_width", None)
    record.pop("public_height", None)


def apply_review_update(
    record: dict[str, Any],
    record_dir: Path,
    private_dir: Path,
    *,
    status: Any,
    redactions: Any,
    thumb_max_edge: int,
    thumb_quality: int,
) -> None:
    status_text = str(status or "").strip()
    if status_text not in REVIEW_STATUSES:
        raise ValueError("Nieprawidłowy status przeglądu zdjęcia.")
    record["redactions"] = normalize_redactions(redactions)
    record["public_review_status"] = status_text
    record["reviewed_at"] = now_iso() if status_text in {"approved", "rejected"} else None
    if status_text == "approved":
        generate_public_derivatives(
            record,
            record_dir,
            private_dir,
            thumb_max_edge=thumb_max_edge,
            thumb_quality=thumb_quality,
        )
    else:
        remove_public_derivatives(record, record_dir)
