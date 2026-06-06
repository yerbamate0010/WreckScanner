from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from core.models import Candidate, ImageItem
from core.reporter import candidates_to_json
from core.settings_store import load_app_settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        value = float(value)
    if isinstance(value, float):
        return round(value, 4) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _items_summary(items: list[ImageItem], ref_item: ImageItem) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for item in items:
        summary.append(
            {
                "label": item.label,
                "source": item.source,
                "file": item.path,
                "is_reference": item is ref_item,
                "quality": item.quality,
                "detections": len(item.dets),
                "alignment": item.alignment,
            }
        )
    return _jsonable(summary)


def write_analysis_run_log(
    output_path: Path,
    *,
    args: Any,
    metadata: dict[str, Any] | None,
    items: list[ImageItem],
    ref_item: ImageItem,
    candidates: list[Candidate],
    detector_device: str,
    imgsz: int,
    img_w: int,
    img_h: int,
    px_per_m: float,
    eps_px: float,
    crop_px: int,
    elapsed_s: float,
) -> None:
    diagnostics = {
        "generated_at": _now_iso(),
        "status": "ok",
        "inputs": {
            "data_dir": args.data,
            "extra_dir": args.extra,
            "output_dir": args.out,
            "model": args.model,
            "device_requested": args.device,
            "device_used": detector_device,
            "confidence": args.conf,
            "eps_m": args.eps,
            "crop_m": args.crop_m,
            "fast_mode": args.fast,
            "enhancement_enabled": not args.no_enhance,
            "app_settings": load_app_settings(),
        },
        "scale": {
            "image_width_px": img_w,
            "image_height_px": img_h,
            "px_per_m": px_per_m,
            "eps_px": eps_px,
            "crop_px": crop_px,
            "imgsz": imgsz,
        },
        "timing": {
            "analysis_seconds": elapsed_s,
        },
        "imagery": {
            "metadata": metadata or {},
            "images": _items_summary(items, ref_item),
        },
        "results": {
            "candidate_count": len(candidates),
            "top_candidates": candidates_to_json(candidates[:20]),
        },
        "artifacts": {
            "report": output_path / "report.html",
            "candidates": output_path / "candidates.json",
            "overlay": output_path / "overlays" / "scored_overlay.jpg",
        },
    }

    output_path.mkdir(parents=True, exist_ok=True)
    with (output_path / "run_log.json").open("w", encoding="utf-8") as f:
        json.dump(_jsonable(diagnostics), f, indent=2, ensure_ascii=False)
        f.write("\n")
