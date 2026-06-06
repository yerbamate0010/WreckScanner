from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))
MPLCONFIGDIR = ROOT_DIR / ".cache" / "matplotlib"
MPLCONFIGDIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(MPLCONFIGDIR))

from core.config import (
    CROPS_DIR_NAME,
    DATA_DIR,
    DEFAULT_CONF,
    DEFAULT_EPS_M,
    DEFAULT_MODEL,
    EXTRA_DIR,
    OUTPUT_DIR,
    OVERLAY_DIR_NAME,
    REVIEW_CROP_M,
    REVIEW_CROP_M_MAX,
    REVIEW_CROP_M_MIN,
)
from core.detection import detect_cars, detect_current_cars, load_detector, optimal_imgsz
from core.diagnostics import write_analysis_run_log
from core.enhancement import enhance_image_items
from core.reporter import clear_generated_files, write_analysis_outputs
from core.runtime import configure_process_encoding
from core.scoring import score_candidates
from core.settings_store import load_enhancement_settings
from core.vision import align_images, aligned_image, image_quality, load_images, load_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=DATA_DIR)
    parser.add_argument("--extra", type=Path, default=EXTRA_DIR)
    parser.add_argument("--out", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--device", choices=("auto", "cpu", "mps"), default="auto")
    parser.add_argument("--conf", type=float, default=DEFAULT_CONF)
    parser.add_argument("--eps", type=float, default=DEFAULT_EPS_M)
    parser.add_argument("--lang", choices=("pl", "en"), default="pl")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Przyspiesz analizę: wykryj pojazdy na najnowszym zdjęciu w jednej skali zamiast wieloskalowo.",
    )
    parser.add_argument(
        "--crop-m", type=float, default=REVIEW_CROP_M, help="Bok miniatury raportu w metrach obrazu źródłowego."
    )
    parser.add_argument(
        "--no-enhance",
        "--no-normalize",
        action="store_true",
        dest="no_enhance",
        help="Wyłącz wspólny filtr ortofoto przed analizą YOLO.",
    )
    return parser.parse_args()


def report_crop_px(crop_m: float, px_per_m: float, img_w: int, img_h: int) -> int:
    if not math.isfinite(crop_m) or not (REVIEW_CROP_M_MIN <= crop_m <= REVIEW_CROP_M_MAX):
        raise ValueError(f"Zoom miniatur raportu musi być w zakresie {REVIEW_CROP_M_MIN:g}-{REVIEW_CROP_M_MAX:g} m.")
    if px_per_m <= 0:
        raise ValueError("Brak poprawnej skali obrazu do przeliczenia miniatur raportu.")
    crop_px = int(round(crop_m * px_per_m))
    return min(max(crop_px, 20), max(1, min(img_w, img_h)))


def main() -> None:
    configure_process_encoding()
    started = time.perf_counter()
    args = parse_args()

    crops_dir = args.out / CROPS_DIR_NAME
    overlay_dir = args.out / OVERLAY_DIR_NAME
    args.out.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    overlay_dir.mkdir(parents=True, exist_ok=True)
    clear_generated_files(crops_dir, overlay_dir)

    items, ref_idx = load_images(args.data, args.extra)
    if not items:
        print("Brak zdjęć.")
        sys.exit(1)

    items = align_images(items, ref_idx)
    ref_item = items[ref_idx]
    print(f"Baza: {len(items)} zdjęć. Referencyjne (najnowsze) to: {ref_item.label}")

    if not args.no_enhance:
        enhance_image_items(items, settings=load_enhancement_settings())

    md = load_metadata(args.data)
    ref_img = aligned_image(ref_item)
    img_h, img_w = ref_img.shape[:2]

    width_m = (md or {}).get("width_meters") or 50
    height_m = (md or {}).get("height_meters") or 50
    px_per_m = ((img_w / width_m) + (img_h / height_m)) / 2.0
    eps_px = max(20, args.eps * px_per_m)

    imgsz = optimal_imgsz(width_m, height_m)
    print(f"Wykrywanie pojazdów (YOLO) obszar {width_m}x{height_m}m -> dopasowano imgsz={imgsz}...")

    try:
        detector = load_detector(args.model, args.device)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"Model: {args.model} | device={detector.device}")
    for item in items:
        img = aligned_image(item)
        item.quality = image_quality(img)
        if item is ref_item and not args.fast:
            item.dets = detect_current_cars(detector, img, base_imgsz=imgsz, conf=args.conf, px_per_m=px_per_m)
        else:
            item.dets = detect_cars(detector, img, conf=args.conf, imgsz=imgsz, px_per_m=px_per_m)
        q = item.quality
        print(f" - {item.label}: {len(item.dets)} pojazdów (ostrość={q['sharpness']:.0f}, jasność={q['mean']:.0f})")

    candidates = score_candidates(items, ref_item, md, img_w, img_h, px_per_m, eps_px)
    try:
        crop_px = report_crop_px(args.crop_m, px_per_m, img_w, img_h)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(2)
    write_analysis_outputs(
        items,
        ref_item,
        candidates,
        md,
        args.out,
        crops_dir,
        overlay_dir,
        img_w,
        img_h,
        eps_px,
        lang=args.lang,
        crop_px=crop_px,
    )
    write_analysis_run_log(
        args.out,
        args=args,
        metadata=md,
        items=items,
        ref_item=ref_item,
        candidates=candidates,
        detector_device=str(detector.device),
        imgsz=imgsz,
        img_w=img_w,
        img_h=img_h,
        px_per_m=px_per_m,
        eps_px=eps_px,
        crop_px=crop_px,
        elapsed_s=time.perf_counter() - started,
    )
    print("✅ Gotowe.")


if __name__ == "__main__":
    main()
