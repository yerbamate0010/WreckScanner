from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from PIL import Image

from core import config
from core.geo import METERS_PER_DEGREE_LAT, lon_meters_per_degree


def validate_crop_m(value: Any) -> float:
    try:
        crop_m = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Nieprawidlowy zoom wycinka mapy.") from exc
    if not math.isfinite(crop_m) or not config.REVIEW_CROP_M_MIN <= crop_m <= config.REVIEW_CROP_M_MAX:
        raise ValueError(
            f"Zoom wycinka mapy musi byc w zakresie {config.REVIEW_CROP_M_MIN:g}-{config.REVIEW_CROP_M_MAX:g} m."
        )
    return crop_m


def load_scan_metadata(data_dir: Path) -> dict[str, Any]:
    metadata_path = data_dir / "metadata.json"
    if not metadata_path.exists():
        raise FileNotFoundError("Brak pobranych danych. Najpierw zeskanuj ten fragment mapy.")
    with metadata_path.open(encoding="utf-8") as f:
        metadata = json.load(f)
    if not isinstance(metadata, dict):
        raise ValueError("Nieprawidlowy format metadata.json.")
    return metadata


def _bbox(metadata: dict[str, Any]) -> dict[str, float]:
    raw = metadata.get("bbox_4326")
    if not isinstance(raw, dict):
        raise ValueError("metadata.json nie zawiera bbox_4326.")
    try:
        bbox = {key: float(raw[key]) for key in ("min_lat", "max_lat", "min_lon", "max_lon")}
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("metadata.json zawiera nieprawidlowy bbox_4326.") from exc
    if bbox["min_lat"] >= bbox["max_lat"] or bbox["min_lon"] >= bbox["max_lon"]:
        raise ValueError("metadata.json zawiera pusty bbox_4326.")
    return bbox


def _point_xy(lat: float, lon: float, bbox: dict[str, float], img_w: int, img_h: int) -> tuple[float, float]:
    if not bbox["min_lat"] <= lat <= bbox["max_lat"] or not bbox["min_lon"] <= lon <= bbox["max_lon"]:
        raise ValueError("Punkt jest poza ostatnio zeskanowanym obszarem. Zeskanuj najpierw ten fragment mapy.")
    x = (lon - bbox["min_lon"]) / (bbox["max_lon"] - bbox["min_lon"]) * img_w
    y = (bbox["max_lat"] - lat) / (bbox["max_lat"] - bbox["min_lat"]) * img_h
    return x, y


def _crop_size_px(crop_m: float, bbox: dict[str, float], img_w: int, img_h: int) -> int:
    center_lat = (bbox["min_lat"] + bbox["max_lat"]) / 2.0
    width_m = (bbox["max_lon"] - bbox["min_lon"]) * lon_meters_per_degree(center_lat)
    height_m = (bbox["max_lat"] - bbox["min_lat"]) * METERS_PER_DEGREE_LAT
    if width_m <= 0 or height_m <= 0:
        raise ValueError("metadata.json zawiera nieprawidlowy rozmiar obszaru.")
    pixels_per_meter = min(img_w / width_m, img_h / height_m)
    return max(1, int(round(crop_m * pixels_per_meter)))


def _crop_bounds(cx: float, cy: float, img_w: int, img_h: int, crop_size: int) -> tuple[int, int, int, int]:
    crop_size = min(int(crop_size), img_w, img_h)
    x1 = int(round(cx - crop_size / 2.0))
    y1 = int(round(cy - crop_size / 2.0))
    x1 = max(0, min(x1, img_w - crop_size))
    y1 = max(0, min(y1, img_h - crop_size))
    return x1, y1, x1 + crop_size, y1 + crop_size


def _safe_crop_label(value: Any) -> str:
    label = str(value or "").strip()
    label = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._-")
    return label[:80] or "crop"


def save_scan_crops(
    lat: float,
    lon: float,
    data_dir: Path,
    output_dir: Path,
    *,
    crop_m: Any = config.REVIEW_CROP_M,
    filename_prefix: str = "",
    jpeg_quality: int = config.REVIEW_JPEG_QUALITY,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    crop_m_f = validate_crop_m(crop_m)
    metadata = load_scan_metadata(data_dir)
    bbox = _bbox(metadata)
    years = metadata.get("years")
    if not isinstance(years, list) or not years:
        raise ValueError("metadata.json nie zawiera rocznikow ortofotomapy.")
    _point_xy(lat, lon, bbox, 1, 1)

    output_dir.mkdir(parents=True, exist_ok=True)
    crops: list[dict[str, str]] = []
    for year in years:
        label = _safe_crop_label(year)
        img_path = data_dir / f"ortofoto_{label}.png"
        if not img_path.exists():
            continue
        with Image.open(img_path) as image:
            img_w, img_h = image.size
            x, y = _point_xy(lat, lon, bbox, img_w, img_h)
            crop_size = _crop_size_px(crop_m_f, bbox, img_w, img_h)
            chunk = image.crop(_crop_bounds(x, y, img_w, img_h, crop_size))
            file_name = f"{filename_prefix}{label}.jpg"
            chunk.convert("RGB").save(output_dir / file_name, "JPEG", quality=jpeg_quality)
        crops.append({"label": label, "file": file_name})

    if not crops:
        raise FileNotFoundError("Brak ortofotomap dla rocznikow z metadata.json. Zeskanuj obszar ponownie.")
    return crops, metadata
