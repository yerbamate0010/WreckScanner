from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from ultralytics import YOLO

from core.config import (
    CAR_CLASSES,
    CURRENT_CONF_MAX,
    CURRENT_DETECTION_SCALES,
    CURRENT_MERGE_EPS_M,
    CURRENT_MERGE_EPS_MIN_PX,
    DEFAULT_CONF,
    DEFAULT_DETECTION_IMGSZ,
    IMG_SIZE_MAX,
    IMG_SIZE_MIN,
    IMG_SIZE_STRIDE,
    OPTIMAL_CAR_PIXELS_PER_METER,
    YOLO_MAX_DET,
)
from core.models import Detection
from core.vehicle_size import looks_like_vehicle_size


def resolve_device(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@dataclass(slots=True)
class YoloDetector:
    model: YOLO
    device: str

    def predict(self, img: np.ndarray, conf: float, imgsz: int) -> list[Any]:
        try:
            return self.model.predict(
                img,
                conf=conf,
                imgsz=imgsz,
                max_det=YOLO_MAX_DET,
                verbose=False,
                device=self.device,
            )
        except Exception as exc:
            if self.device == "mps":
                print(f"⚠️  MPS nie obsłużył predykcji YOLO ({exc}); przełączam na CPU.")
                self.device = "cpu"
                return self.model.predict(
                    img,
                    conf=conf,
                    imgsz=imgsz,
                    max_det=YOLO_MAX_DET,
                    verbose=False,
                    device=self.device,
                )
            raise


def load_detector(model_path: Path | str, device: str) -> YoloDetector:
    path = Path(model_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Brak pliku modelu YOLO: {path}. Umieść model w katalogu projektu albo wybierz dostępny model."
        )
    return YoloDetector(model=YOLO(str(path)), device=resolve_device(device))


def rect_features(poly: np.ndarray, px_per_m: float | None = None) -> dict[str, float] | None:
    pts = np.array(poly, dtype=np.float32)
    rect = cv2.minAreaRect(pts)
    w, h = rect[1]
    angle = float(rect[2])
    if w <= 0 or h <= 0:
        return None
    if w < h:
        angle += 90.0
    angle = angle % 180.0
    length_px = float(max(w, h))
    width_px = float(min(w, h))
    area_px = float(cv2.contourArea(pts))
    out = {
        "angle": angle,
        "length_px": length_px,
        "width_px": width_px,
        "area_px": area_px,
        "aspect": length_px / max(width_px, 1.0),
    }
    if px_per_m:
        out["length_m"] = length_px / px_per_m
        out["width_m"] = width_px / px_per_m
        out["area_m2"] = area_px / (px_per_m * px_per_m)
    return out


def detect_cars(
    detector: YoloDetector,
    img: np.ndarray,
    conf: float = DEFAULT_CONF,
    imgsz: int = DEFAULT_DETECTION_IMGSZ,
    px_per_m: float | None = None,
) -> list[Detection]:
    results = detector.predict(img, conf=conf, imgsz=imgsz)
    out: list[Detection] = []
    for result in results:
        if result.obb is None or len(result.obb) == 0:
            continue
        polys = result.obb.xyxyxyxy.cpu().numpy()
        classes = result.obb.cls.cpu().numpy().astype(int)
        confs = result.obb.conf.cpu().numpy()
        for poly, class_id, det_conf in zip(polys, classes, confs):
            class_id = int(class_id)
            if class_id not in CAR_CLASSES:
                continue
            poly_arr = np.array(poly, dtype=np.float32)
            feat = rect_features(poly_arr, px_per_m=px_per_m)
            if not feat or not looks_like_vehicle_size(feat, class_id):
                continue
            out.append(
                Detection(
                    cx=float(poly_arr[:, 0].mean()),
                    cy=float(poly_arr[:, 1].mean()),
                    poly=poly_arr,
                    class_id=class_id,
                    angle=float(feat["angle"]),
                    conf=float(det_conf),
                    length_m=feat.get("length_m"),
                    width_m=feat.get("width_m"),
                    aspect=feat.get("aspect"),
                    area_m2=feat.get("area_m2"),
                    length_px=feat.get("length_px"),
                    width_px=feat.get("width_px"),
                    area_px=feat.get("area_px"),
                )
            )
    return out


def merge_detections(detections: list[Detection], eps_px: float) -> list[Detection]:
    merged: list[Detection] = []
    for det in sorted(detections, key=lambda d: d.conf, reverse=True):
        duplicate = False
        for kept in merged:
            if math.hypot(det.cx - kept.cx, det.cy - kept.cy) <= eps_px:
                duplicate = True
                break
        if not duplicate:
            merged.append(det)
    return merged


def detect_current_cars(
    detector: YoloDetector,
    img: np.ndarray,
    base_imgsz: int,
    conf: float,
    px_per_m: float,
) -> list[Detection]:
    sizes = sorted(
        {
            max(IMG_SIZE_MIN, int(round((base_imgsz * scale) / IMG_SIZE_STRIDE) * IMG_SIZE_STRIDE))
            for scale in CURRENT_DETECTION_SCALES
        }
    )
    detections: list[Detection] = []
    current_conf = min(conf, CURRENT_CONF_MAX)
    for imgsz in sizes:
        detections.extend(detect_cars(detector, img, conf=current_conf, imgsz=imgsz, px_per_m=px_per_m))
    return merge_detections(detections, eps_px=max(CURRENT_MERGE_EPS_MIN_PX, CURRENT_MERGE_EPS_M * px_per_m))


def optimal_imgsz(width_m: float, height_m: float) -> int:
    imgsz = int(max(width_m, height_m) * OPTIMAL_CAR_PIXELS_PER_METER)
    imgsz = int(math.ceil(imgsz / IMG_SIZE_STRIDE) * IMG_SIZE_STRIDE)
    return max(IMG_SIZE_MIN, min(imgsz, IMG_SIZE_MAX))
