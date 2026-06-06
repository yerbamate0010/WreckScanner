from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from core.config import (
    ALIGN_ALREADY_ALIGNED_SHIFT_PX,
    ALIGN_MAX_DIM,
    ALIGN_MAX_SHIFT_IMAGE_FRACTION,
    ALIGN_MAX_SHIFT_MIN_PX,
    ALIGN_MIN_PHASE_RESPONSE,
    ALIGN_MIN_WORKING_DIM_PX,
    COLOR_HUE_WEIGHT,
    COLOR_LOW_SATURATION_HUE_CUTOFF,
    COLOR_NORMALIZATION_CLAHE_CLIP_LIMIT,
    COLOR_NORMALIZATION_CLAHE_GRID_SIZE,
    COLOR_SAT_WEIGHT,
    COLOR_VAL_WEIGHT,
    GLOBAL_BLUR_SHARPNESS,
    INNER_POLY_SCALE,
    LOCAL_BLUR_SHARPNESS,
    LOCAL_BLUR_STD,
    LOCAL_TOO_DARK_MEAN,
    LOCAL_TOO_DARK_STD,
    LOCAL_VISIBILITY_MIN_PAD_PX,
    LOCAL_VISIBILITY_PAD_FACTOR,
    QUALITY_SAMPLE_SIZE_PX,
    TREE_COVER_THRESHOLD,
    TREE_EXG_THRESHOLD,
    UNLIMITED_SHARPNESS_SENTINEL,
)
from core.models import ImageItem


def parse_year(value: str) -> int | None:
    match = re.search(r"(20\d{2})", value)
    return int(match.group(1)) if match else None


def load_images(data_dir: Path, extra_dir: Path | None = None) -> tuple[list[ImageItem], int]:
    items: list[ImageItem] = []
    for path in sorted(data_dir.glob("ortofoto_*.png")):
        year = parse_year(path.stem)
        img = cv2.imread(str(path))
        if img is None:
            continue
        items.append(ImageItem(source="wroclaw", label=str(year) if year else path.stem, path=path, img=img))

    if not items:
        return [], 0

    ref_idx = len(items) - 1

    if extra_dir and extra_dir.is_dir():
        for path in sorted(extra_dir.glob("*.png")):
            img = cv2.imread(str(path))
            if img is None:
                continue
            items.append(ImageItem(source="geoportal_krajowy", label=path.stem, path=path, img=img))

    return items, ref_idx


def load_metadata(data_dir: Path) -> dict[str, Any] | None:
    path = data_dir / "metadata.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def alignment_image(img: np.ndarray, target_size: tuple[int, int]) -> np.ndarray:
    small = cv2.resize(img, target_size, interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = cv2.magnitude(gx, gy)
    grad -= float(grad.mean())
    return grad


def estimate_translation(src: np.ndarray, ref: np.ndarray, max_dim: int = ALIGN_MAX_DIM) -> tuple[float, float, float]:
    h_ref, w_ref = ref.shape[:2]
    scale = min(1.0, max_dim / float(max(w_ref, h_ref)))
    target_size = (
        max(ALIGN_MIN_WORKING_DIM_PX, int(round(w_ref * scale))),
        max(ALIGN_MIN_WORKING_DIM_PX, int(round(h_ref * scale))),
    )
    src_grad = alignment_image(src, target_size)
    ref_grad = alignment_image(ref, target_size)
    window = cv2.createHanningWindow(target_size, cv2.CV_32F)
    (dx_small, dy_small), response = cv2.phaseCorrelate(src_grad, ref_grad, window)
    return dx_small / scale, dy_small / scale, float(response)


def align_images(items: list[ImageItem], ref_idx: int) -> list[ImageItem]:
    if not items:
        return items
    ref = items[ref_idx]
    h_ref, w_ref = ref.img.shape[:2]
    max_shift_px = max(ALIGN_MAX_SHIFT_MIN_PX, min(w_ref, h_ref) * ALIGN_MAX_SHIFT_IMAGE_FRACTION)

    for item in items:
        src = item.img
        if src.shape[:2] != (h_ref, w_ref):
            src = cv2.resize(src, (w_ref, h_ref), interpolation=cv2.INTER_AREA)

        if item is ref:
            item.img_aligned = src
            item.alignment = {"method": "reference", "dx": 0.0, "dy": 0.0, "response": 1.0}
            continue

        try:
            dx, dy, response = estimate_translation(src, ref.img)
        except Exception as exc:
            item.img_aligned = src
            item.alignment = {"method": "none", "error": str(exc), "dx": 0.0, "dy": 0.0, "response": 0.0}
            print(f"⚠️  {item.label}: błąd wyrównania ({exc}) — bez wyrównania")
            continue

        shift_len = math.hypot(dx, dy)
        if response >= ALIGN_MIN_PHASE_RESPONSE and shift_len <= max_shift_px:
            if shift_len < ALIGN_ALREADY_ALIGNED_SHIFT_PX:
                item.img_aligned = src
                method = "already_aligned"
            else:
                matrix = np.float32([[1.0, 0.0, dx], [0.0, 1.0, dy]])
                item.img_aligned = cv2.warpAffine(
                    src,
                    matrix,
                    (w_ref, h_ref),
                    flags=cv2.INTER_LINEAR,
                    borderMode=cv2.BORDER_REPLICATE,
                )
                method = "phase_translation"
            item.alignment = {"method": method, "dx": dx, "dy": dy, "response": response}
            print(f"🔧 {item.label}: align translacją dx={dx:.1f}px dy={dy:.1f}px (phase={response:.3f})")
        else:
            item.img_aligned = src
            item.alignment = {"method": "none", "dx": dx, "dy": dy, "response": response}
            print(
                f"⚠️  {item.label}: align niewiarygodny dx={dx:.1f}px dy={dy:.1f}px phase={response:.3f} — bez wyrównania"
            )

    return items


def poly_np(poly: np.ndarray) -> np.ndarray:
    return np.array(poly, dtype=np.float32)


def shrink_poly(poly: np.ndarray, scale: float = INNER_POLY_SCALE) -> np.ndarray:
    pts = poly_np(poly)
    center = pts.mean(axis=0)
    return center + (pts - center) * scale


def tree_cover_fraction(img: np.ndarray, poly: np.ndarray) -> float:
    mask = np.zeros(img.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(poly, dtype=np.int32)], 255)
    pixels = img[mask > 0]
    if len(pixels) == 0:
        return 0.0
    pixels_f = pixels.astype(np.float32)
    b = pixels_f[:, 0]
    g = pixels_f[:, 1]
    r = pixels_f[:, 2]
    exg = 2.0 * g - r - b
    return float(np.mean(exg > TREE_EXG_THRESHOLD))


def is_covered_by_trees(img: np.ndarray, poly: np.ndarray) -> bool:
    return tree_cover_fraction(img, poly) > TREE_COVER_THRESHOLD


def image_quality(img: np.ndarray) -> dict[str, float]:
    small = cv2.resize(img, (QUALITY_SAMPLE_SIZE_PX, QUALITY_SAMPLE_SIZE_PX), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return {
        "mean": float(gray.mean()),
        "std": float(gray.std()),
        "sharpness": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    }


def local_visibility(
    img: np.ndarray, poly: np.ndarray, global_quality: dict[str, float] | None = None
) -> dict[str, Any]:
    pts = poly_np(poly)
    x, y, w, h = cv2.boundingRect(pts.astype(np.int32))
    pad = int(max(w, h, LOCAL_VISIBILITY_MIN_PAD_PX) * LOCAL_VISIBILITY_PAD_FACTOR)
    x1 = max(0, x - pad)
    y1 = max(0, y - pad)
    x2 = min(img.shape[1], x + w + pad)
    y2 = min(img.shape[0], y + h + pad)
    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        return {"usable": False, "reason": "poza kadrem", "mean": 0.0, "std": 0.0, "sharpness": 0.0, "tree": 0.0}

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mean = float(gray.mean())
    std = float(gray.std())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    local_poly = pts.copy()
    local_poly[:, 0] -= x1
    local_poly[:, 1] -= y1
    tree = tree_cover_fraction(crop, local_poly)

    global_sharpness = (global_quality or {}).get("sharpness", UNLIMITED_SHARPNESS_SENTINEL)
    too_dark = mean < LOCAL_TOO_DARK_MEAN and std < LOCAL_TOO_DARK_STD
    too_blurry = global_sharpness < GLOBAL_BLUR_SHARPNESS and sharpness < LOCAL_BLUR_SHARPNESS and std < LOCAL_BLUR_STD
    if tree > TREE_COVER_THRESHOLD:
        reason = "drzewa"
    elif too_dark:
        reason = "ciemny kadr"
    elif too_blurry:
        reason = "rozmyty kadr"
    else:
        reason = None

    return {
        "usable": reason is None,
        "reason": reason,
        "mean": mean,
        "std": std,
        "sharpness": sharpness,
        "tree": tree,
    }


def dominant_color_hsv(img: np.ndarray, poly: np.ndarray) -> tuple[float, float, float]:
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    lightness, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(
        clipLimit=COLOR_NORMALIZATION_CLAHE_CLIP_LIMIT,
        tileGridSize=(COLOR_NORMALIZATION_CLAHE_GRID_SIZE, COLOR_NORMALIZATION_CLAHE_GRID_SIZE),
    )
    cl = clahe.apply(lightness)
    lab_clahe = cv2.merge((cl, a, b))
    img_normalized = cv2.cvtColor(lab_clahe, cv2.COLOR_LAB2BGR)

    inner_poly = shrink_poly(poly)
    mask = np.zeros(img_normalized.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(inner_poly, dtype=np.int32)], 255)
    pixels = img_normalized[mask > 0]
    if len(pixels) == 0:
        return 0.0, 0.0, 0.0
    hsv = cv2.cvtColor(pixels.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    return tuple(float(v) for v in np.median(hsv, axis=0))


def hsv_similarity(c1: tuple[float, float, float], c2: tuple[float, float, float]) -> float:
    h1, s1, v1 = c1
    h2, s2, v2 = c2
    dh = min(abs(h1 - h2), 180 - abs(h1 - h2)) / 90.0
    ds = abs(s1 - s2) / 255.0
    dv = abs(v1 - v2) / 255.0
    hue_weight = COLOR_HUE_WEIGHT * min(1.0, ((s1 + s2) / 2.0) / COLOR_LOW_SATURATION_HUE_CUTOFF)
    dist = hue_weight * dh + COLOR_SAT_WEIGHT * ds + COLOR_VAL_WEIGHT * dv
    return float(max(0.0, 1.0 - dist))


def crop_bounds(cx: float, cy: float, img_w: int, img_h: int, crop_size: int) -> tuple[int, int, int, int]:
    crop_size = min(int(crop_size), img_w, img_h)
    x1 = int(round(cx - crop_size / 2.0))
    y1 = int(round(cy - crop_size / 2.0))
    x1 = max(0, min(x1, img_w - crop_size))
    y1 = max(0, min(y1, img_h - crop_size))
    return x1, y1, x1 + crop_size, y1 + crop_size


def pixel_to_latlon(cx: float, cy: float, md: dict[str, Any], img_w: int, img_h: int) -> tuple[float, float]:
    bb = md["bbox_4326"]
    lon = bb["min_lon"] + (cx / img_w) * (bb["max_lon"] - bb["min_lon"])
    lat = bb["max_lat"] - (cy / img_h) * (bb["max_lat"] - bb["min_lat"])
    return float(lat), float(lon)


def aligned_image(item: ImageItem) -> np.ndarray:
    if item.img_aligned is None:
        return item.img
    return item.img_aligned
