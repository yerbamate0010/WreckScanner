from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np

Status = Literal["present", "missing", "ignored"]


@dataclass(slots=True)
class Detection:
    cx: float
    cy: float
    poly: np.ndarray
    class_id: int
    angle: float
    conf: float
    length_m: float | None = None
    width_m: float | None = None
    aspect: float | None = None
    area_m2: float | None = None
    length_px: float | None = None
    width_px: float | None = None
    area_px: float | None = None


@dataclass(slots=True)
class ImageItem:
    source: str
    label: str
    path: Path
    img: np.ndarray
    img_aligned: np.ndarray | None = None
    alignment: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, float] = field(default_factory=dict)
    dets: list[Detection] = field(default_factory=list)
    crops: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Observation:
    label: str
    status: Status
    reason: str | None = None
    visibility: dict[str, Any] | None = None
    conf: float | None = None
    dist_m: float | None = None
    angle_diff: float | None = None
    color_similarity: float | None = None
    shape_similarity: float | None = None
    match_score: float | None = None
    crop_cx: float | None = None
    crop_cy: float | None = None


@dataclass(slots=True)
class DetectionMatch:
    det: Detection
    color: tuple[float, float, float]
    color_sim: float
    dist_px: float
    angle_diff: float
    shape_sim: float
    match_score: float


@dataclass(slots=True)
class Candidate:
    cx: float
    cy: float
    lat: float | None
    lon: float | None
    score: float
    current_conf: float
    coverage: float
    color_consistency: float
    mean_conf: float
    mean_match: float
    span_score: float
    evidence_factor: float
    labels_present: list[str]
    n_detections: int
    valid_items: int
    ignored_count: int
    clear_missing_count: int
    observations: list[Observation]
    poly: np.ndarray
