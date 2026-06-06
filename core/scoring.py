from __future__ import annotations

import math
from typing import Any

import numpy as np

from core.config import (
    CLEAR_MISSING_PENALTY,
    DETECTION_FACTOR_BASE,
    DETECTION_FACTOR_RANGE,
    EVIDENCE_FACTOR_BASE,
    EVIDENCE_FACTOR_RANGE,
    EVIDENCE_FULL_OBSERVATION_COUNT,
    LOW_QUALITY_COLOR_SIMILARITY,
    LOW_QUALITY_DETECTION_CONF,
    MATCH_CLOSE_EPS_FACTOR,
    MATCH_MIN_SCORE,
    MATCH_MIN_SHAPE_SIMILARITY,
    MATCH_STRONG_COLOR_EPS_FACTOR,
    MATCH_STRONG_COLOR_SIMILARITY,
    MATCH_WEIGHTS,
    MAX_ANGLE_DIFF_DEG,
    MIN_DETECTIONS,
    SCORE_WEIGHTS,
    SHAPE_LENGTH_WEIGHT,
    SHAPE_WIDTH_WEIGHT,
)
from core.models import Candidate, Detection, DetectionMatch, ImageItem, Observation
from core.vision import aligned_image, dominant_color_hsv, hsv_similarity, local_visibility, parse_year, pixel_to_latlon


def angle_diff_deg(a: float, b: float) -> float:
    diff = abs((a - b) % 180.0)
    return min(diff, 180.0 - diff)


def size_similarity(d1: Detection, d2: Detection) -> float:
    l1, l2 = d1.length_m, d2.length_m
    w1, w2 = d1.width_m, d2.width_m
    if not all(v and v > 0 for v in (l1, l2, w1, w2)):
        return 1.0
    length_sim = min(l1, l2) / max(l1, l2)
    width_sim = min(w1, w2) / max(w1, w2)
    return float(SHAPE_LENGTH_WEIGHT * length_sim + SHAPE_WIDTH_WEIGHT * width_sim)


def best_detection_match(
    ref_det: Detection,
    detections: list[Detection],
    ref_color: tuple[float, float, float],
    img: np.ndarray,
    eps_px: float,
) -> DetectionMatch | None:
    best: DetectionMatch | None = None
    for det in detections:
        dist = math.hypot(det.cx - ref_det.cx, det.cy - ref_det.cy)
        if dist > eps_px:
            continue
        angle_diff = angle_diff_deg(det.angle, ref_det.angle)
        color = dominant_color_hsv(img, det.poly)
        color_sim = hsv_similarity(ref_color, color)
        strong_color_close = (
            dist <= eps_px * MATCH_STRONG_COLOR_EPS_FACTOR and color_sim >= MATCH_STRONG_COLOR_SIMILARITY
        )
        if angle_diff > MAX_ANGLE_DIFF_DEG and not strong_color_close:
            continue
        shape_sim = size_similarity(ref_det, det)
        if shape_sim < MATCH_MIN_SHAPE_SIMILARITY and not strong_color_close:
            continue
        dist_sim = 1.0 - min(1.0, dist / eps_px)
        angle_sim = 1.0 - min(1.0, angle_diff / MAX_ANGLE_DIFF_DEG)
        match_score = (
            MATCH_WEIGHTS["distance"] * dist_sim
            + MATCH_WEIGHTS["angle"] * angle_sim
            + MATCH_WEIGHTS["shape"] * shape_sim
            + MATCH_WEIGHTS["color"] * color_sim
        )
        match = DetectionMatch(
            det=det,
            color=color,
            color_sim=color_sim,
            dist_px=dist,
            angle_diff=angle_diff,
            shape_sim=shape_sim,
            match_score=match_score,
        )
        if best is None or match.match_score > best.match_score:
            best = match
    if best and (best.match_score >= MATCH_MIN_SCORE or best.dist_px <= eps_px * MATCH_CLOSE_EPS_FACTOR):
        return best
    return None


def has_temporal_support(idx: int, items: list[ImageItem], present_indices: set[int]) -> bool:
    current_year = parse_year(str(items[idx].label))
    if current_year is not None:
        has_before = any((parse_year(str(items[i].label)) or 9999) < current_year for i in present_indices)
        has_after = any((parse_year(str(items[i].label)) or -1) > current_year for i in present_indices)
        return has_before and has_after
    return any(i < idx for i in present_indices) and any(i > idx for i in present_indices)


def temporal_span_score(labels: list[str]) -> float:
    years = [parse_year(str(label)) for label in labels]
    years = [year for year in years if year is not None]
    if len(years) < 2:
        return 0.0
    return min(1.0, (max(years) - min(years)) / 5.0)


def score_candidates(
    items: list[ImageItem],
    ref_item: ImageItem,
    md: dict[str, Any] | None,
    img_w: int,
    img_h: int,
    px_per_m: float,
    eps_px: float,
) -> list[Candidate]:
    print(f"Kadrowanie historii na podstawie {len(ref_item.dets)} pojazdów z najnowszego zdjęcia...")
    candidates: list[Candidate] = []
    ref_img = aligned_image(ref_item)

    for ref_det in ref_item.dets:
        cx, cy = ref_det.cx, ref_det.cy
        ref_color = dominant_color_hsv(ref_img, ref_det.poly)

        presence: list[str] = []
        colors: list[tuple[float, float, float]] = []
        confs: list[float] = []
        match_scores: list[float] = []
        observations: list[Observation] = []
        weak_color_evidence: dict[int, tuple[tuple[float, float, float], float]] = {}
        present_indices: set[int] = set()
        ignored_count = 0
        clear_missing_count = 0

        for item_idx, item in enumerate(items):
            img = aligned_image(item)
            match = best_detection_match(ref_det, item.dets, ref_color, img, eps_px)
            if match:
                det = match.det
                visibility = local_visibility(img, det.poly, item.quality)
                if item is not ref_item and not visibility["usable"] and det.conf < LOW_QUALITY_DETECTION_CONF:
                    if visibility["reason"] in {"rozmyty kadr", "ciemny kadr"}:
                        weak_color_evidence[item_idx] = (match.color, match.color_sim)
                    ignored_count += 1
                    observations.append(
                        Observation(
                            label=item.label,
                            status="ignored",
                            reason=f"{visibility['reason']}, niska pewnosc",
                            visibility=visibility,
                            crop_cx=det.cx,
                            crop_cy=det.cy,
                        )
                    )
                    continue
                presence.append(item.label)
                present_indices.add(item_idx)
                colors.append(match.color)
                confs.append(det.conf)
                match_scores.append(match.match_score)
                observations.append(
                    Observation(
                        label=item.label,
                        status="present",
                        conf=det.conf,
                        dist_m=match.dist_px / px_per_m,
                        angle_diff=match.angle_diff,
                        shape_similarity=match.shape_sim,
                        color_similarity=match.color_sim,
                        match_score=match.match_score,
                        crop_cx=det.cx,
                        crop_cy=det.cy,
                    )
                )
            else:
                visibility = local_visibility(img, ref_det.poly, item.quality)
                if visibility["usable"]:
                    clear_missing_count += 1
                    observations.append(
                        Observation(label=item.label, status="missing", reason="brak pojazdu", visibility=visibility)
                    )
                else:
                    if visibility["reason"] in {"rozmyty kadr", "ciemny kadr"}:
                        weak_color = dominant_color_hsv(img, ref_det.poly)
                        weak_color_evidence[item_idx] = (weak_color, hsv_similarity(ref_color, weak_color))
                    ignored_count += 1
                    observations.append(
                        Observation(
                            label=item.label,
                            status="ignored",
                            reason=visibility["reason"],
                            visibility=visibility,
                        )
                    )

        for item_idx, (weak_color, color_sim) in weak_color_evidence.items():
            if color_sim < LOW_QUALITY_COLOR_SIMILARITY:
                continue
            if not has_temporal_support(item_idx, items, present_indices):
                continue
            obs = observations[item_idx]
            if obs.status != "ignored":
                continue
            obs.status = "present"
            obs.reason = "kolor + interpolacja"
            obs.color_similarity = color_sim
            obs.match_score = color_sim
            present_indices.add(item_idx)
            colors.append(weak_color)
            match_scores.append(color_sim)
            ignored_count = max(0, ignored_count - 1)

        presence = [item.label for item_idx, item in enumerate(items) if observations[item_idx].status == "present"]

        valid_items = len(items) - ignored_count
        coverage = len(presence) / valid_items if valid_items > 0 else 0.0

        sims: list[float] = []
        for i in range(len(colors)):
            for j in range(i + 1, len(colors)):
                sims.append(hsv_similarity(colors[i], colors[j]))
        color_consistency = float(np.mean(sims)) if sims else 0.0
        mean_conf = float(np.mean(confs)) if confs else 0.0
        mean_match = float(np.mean(match_scores)) if match_scores else 0.0
        span_score = temporal_span_score(presence)

        score = (
            SCORE_WEIGHTS["coverage"] * coverage
            + SCORE_WEIGHTS["color_consistency"] * color_consistency
            + SCORE_WEIGHTS["mean_conf"] * mean_conf
            + SCORE_WEIGHTS["span"] * span_score
        )
        evidence_factor = min(1.0, valid_items / EVIDENCE_FULL_OBSERVATION_COUNT)
        detection_factor = min(1.0, len(presence) / float(MIN_DETECTIONS))
        score *= EVIDENCE_FACTOR_BASE + EVIDENCE_FACTOR_RANGE * evidence_factor
        score *= DETECTION_FACTOR_BASE + DETECTION_FACTOR_RANGE * detection_factor
        if valid_items > 0:
            score *= 1.0 - CLEAR_MISSING_PENALTY * (clear_missing_count / valid_items)

        lat, lon = None, None
        if md:
            lat, lon = pixel_to_latlon(cx, cy, md, img_w, img_h)

        candidates.append(
            Candidate(
                cx=cx,
                cy=cy,
                lat=lat,
                lon=lon,
                score=score,
                current_conf=float(ref_det.conf),
                coverage=coverage,
                color_consistency=color_consistency,
                mean_conf=mean_conf,
                mean_match=mean_match,
                span_score=span_score,
                evidence_factor=evidence_factor,
                labels_present=presence,
                n_detections=len(presence),
                valid_items=valid_items,
                ignored_count=ignored_count,
                clear_missing_count=clear_missing_count,
                observations=observations,
                poly=ref_det.poly,
            )
        )

    candidates.sort(key=lambda item: (-item.score, -item.current_conf))
    return candidates
