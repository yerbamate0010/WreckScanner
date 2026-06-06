from __future__ import annotations

import html as html_lib
import json
import math
import os
import time
from pathlib import Path
from typing import Any

import cv2

from core.config import (
    DETECTION_CROP_MAX_DIST_M,
    DETECTION_CROP_MIN_MATCH_SCORE,
    LOCAL_CROP_ALIGN_CONTEXT_FACTOR,
    LOCAL_CROP_ALIGN_MAX_SHIFT_FACTOR,
    LOCAL_CROP_ALIGN_MIN_ACCEPTED_SHIFT_PX,
    LOCAL_CROP_ALIGN_MIN_PX,
    LOCAL_CROP_ALIGN_MIN_RESPONSE,
    LOCAL_CROP_ALIGN_REFERENCE_MAX_SHIFT_FACTOR,
    LOCAL_CROP_ALIGN_REFERENCE_MIN_RESPONSE,
    LOCAL_CROP_ALIGN_WEAK_DET_MIN_RESPONSE,
    REVIEW_JPEG_QUALITY,
)
from core.geo import external_map_links, google_maps_embed_url
from core.models import Candidate, ImageItem, Observation
from core.vision import aligned_image, alignment_image, crop_bounds

REPORT_I18N: dict[str, dict[str, str]] = {
    "pl": {
        "title": "Analiza pojazdów do weryfikacji",
        "legend.title": "Mapa obecnych detekcji",
        "legend.note": "Score to heurystyka rankingowa (pokrycie · kolor · rozpiętość). Wysoka wartość = wysoka pozycja na liście, <b>nie</b> potwierdzenie, że pojazd jest nieużytkowany — to wymaga inspekcji manualnej.",
        "legend.diagnostics": "diagnostyka JSON",
        "legend.cropManifest": "kadrowanie JSON",
        "noCars": "Brak pojazdów na najnowszym zdjęciu.",
        "jumpTo": "Skocz do #{n}",
        "showOnMap": "📍 pokaż na mapie",
        "showOnMap.title": "Pokaż tego kandydata na mapie u góry",
        "saveWreck": "Dodaj pojazd",
        "savingWreck": "Zapisuję...",
        "savedWreck": "Pojazd dodany",
        "alreadySavedWreck": "Już zapisany",
        "saveWreckError": "Błąd zapisu pojazdu",
        "saveWreckDisabled": "Dodawanie pinezek z YOLO jest teraz wyłączone.",
        "score.tooltip": "Heurystyka rankingowa łącząca pokrycie, spójność koloru i rozpiętość czasową. Służy do ułożenia listy — nie potwierdza długiego stania.",
        "badge.coverage": "obecne na {n}/{total} wiarygodnych zdjęciach",
        "badge.color": "spójność koloru {pct}%",
        "badge.yolo": "YOLO teraz {val}",
        "badge.span": "rozpiętość {pct}%",
        "candMeta": "Widziane w: {labels} · braki: {missing} · pominięte kadry: {ignored}",
        "cell.match": "trafienie {conf}",
        "cell.skipped": "pominięte",
        "cell.missing": "brak",
        "cell.empty": "brak",
        "cell.satellite": "Satelita",
        "cell.googleAge": "Google · ?",
        "link.streetView": "Street View",
        "link.gmapsSat": "Google Maps satelita",
        "link.appleMaps": "Apple Maps",
        "link.mapillary": "Mapillary",
        "link.geoportal": "Geoportal Krajowy",
    },
    "en": {
        "title": "Vehicles for verification analysis",
        "legend.title": "Map of current detections",
        "legend.note": "Score is a ranking heuristic (coverage · color · span). High value = high position on the list, <b>not</b> confirmation that the vehicle is unused — manual inspection required.",
        "legend.diagnostics": "diagnostics JSON",
        "legend.cropManifest": "crop manifest JSON",
        "noCars": "No vehicles on the latest image.",
        "jumpTo": "Jump to #{n}",
        "showOnMap": "📍 show on map",
        "showOnMap.title": "Show this candidate on the map above",
        "saveWreck": "Add vehicle",
        "savingWreck": "Saving...",
        "savedWreck": "Vehicle added",
        "alreadySavedWreck": "Already saved",
        "saveWreckError": "Vehicle save failed",
        "saveWreckDisabled": "Saving YOLO vehicle pins is currently disabled.",
        "score.tooltip": "Ranking heuristic combining coverage, color consistency and temporal span. Used to order the list — does not confirm long-term parking.",
        "badge.coverage": "present in {n}/{total} reliable images",
        "badge.color": "color consistency {pct}%",
        "badge.yolo": "YOLO now {val}",
        "badge.span": "span {pct}%",
        "candMeta": "Seen in: {labels} · missing: {missing} · skipped frames: {ignored}",
        "cell.match": "match {conf}",
        "cell.skipped": "skipped",
        "cell.missing": "missing",
        "cell.empty": "missing",
        "cell.satellite": "Satellite",
        "cell.googleAge": "Google · ?",
        "link.streetView": "Street View",
        "link.gmapsSat": "Google Maps satellite",
        "link.appleMaps": "Apple Maps",
        "link.mapillary": "Mapillary",
        "link.geoportal": "Polish Geoportal",
    },
}


def _tr(lang: str, key: str, **kwargs: Any) -> str:
    """Zwróć przetłumaczony string. Fallback: pl → klucz."""
    dict_lang = REPORT_I18N.get(lang) or REPORT_I18N["pl"]
    val = dict_lang.get(key) or REPORT_I18N["pl"].get(key) or key
    if kwargs:
        for k, v in kwargs.items():
            val = val.replace("{" + k + "}", str(v))
    return val


def clear_generated_files(*dirs: Path) -> None:
    for directory in dirs:
        if not directory.is_dir():
            continue
        for name in os.listdir(directory):
            if name.lower().endswith((".jpg", ".jpeg", ".png")):
                try:
                    (directory / name).unlink()
                except OSError:
                    pass


def _versioned_asset_url(path: str, asset_version: str) -> str:
    separator = "&" if "?" in path else "?"
    return html_lib.escape(f"{path}{separator}v={asset_version}", quote=True)


def local_aligned_crop_center(
    ref_img,
    img,
    candidate: Candidate,
    img_w: int,
    img_h: int,
    crop_px: int,
    min_response: float = LOCAL_CROP_ALIGN_MIN_RESPONSE,
    max_shift_factor: float = LOCAL_CROP_ALIGN_MAX_SHIFT_FACTOR,
) -> tuple[float, float, dict[str, Any] | None]:
    context_px = int(max(LOCAL_CROP_ALIGN_MIN_PX, round(crop_px * LOCAL_CROP_ALIGN_CONTEXT_FACTOR)))
    context_px = min(context_px, max(1, min(img_w, img_h)))
    x1, y1, x2, y2 = crop_bounds(candidate.cx, candidate.cy, img_w, img_h, context_px)
    ref_patch = ref_img[y1:y2, x1:x2]
    patch = img[y1:y2, x1:x2]
    if ref_patch.shape[:2] != patch.shape[:2] or min(ref_patch.shape[:2]) < 64:
        return candidate.cx, candidate.cy, None

    size = (ref_patch.shape[1], ref_patch.shape[0])
    try:
        ref_grad = alignment_image(ref_patch, size)
        patch_grad = alignment_image(patch, size)
        window = cv2.createHanningWindow(size, cv2.CV_32F)
        (dx, dy), response = cv2.phaseCorrelate(patch_grad, ref_grad, window)
    except cv2.error:
        return candidate.cx, candidate.cy, None

    shift = math.hypot(dx, dy)
    max_shift = max(LOCAL_CROP_ALIGN_MIN_ACCEPTED_SHIFT_PX, crop_px * max_shift_factor)
    info = {
        "method": "local_phase",
        "dx": round(float(dx), 2),
        "dy": round(float(dy), 2),
        "response": round(float(response), 4),
        "context_px": context_px,
        "min_response": round(float(min_response), 4),
        "max_shift": round(float(max_shift), 2),
    }
    if response < min_response or shift > max_shift:
        return candidate.cx, candidate.cy, {**info, "accepted": False}

    return candidate.cx - dx, candidate.cy - dy, {**info, "accepted": True}


def weak_detection_crop(obs: Observation | None) -> bool:
    if obs is None or obs.crop_cx is None or obs.crop_cy is None:
        return False
    if obs.dist_m is not None and obs.dist_m > DETECTION_CROP_MAX_DIST_M:
        return True
    if obs.match_score is not None and obs.match_score < DETECTION_CROP_MIN_MATCH_SCORE:
        return True
    return False


def save_candidate_crops(
    items: list[ImageItem],
    ref_item: ImageItem,
    candidates: list[Candidate],
    crops_dir: Path,
    manifest_path: Path,
    img_w: int,
    img_h: int,
    crop_px: int,
) -> None:
    ref_img = aligned_image(ref_item)
    for item in items:
        item.crops = []
    manifest: dict[str, Any] = {
        "crop_px": crop_px,
        "items": [item.label for item in items],
        "candidates": [],
    }
    for i, candidate in enumerate(candidates):
        manifest_candidate = {
            "rank": i + 1,
            "lat": candidate.lat,
            "lon": candidate.lon,
            "base_center": {"x": round(float(candidate.cx), 2), "y": round(float(candidate.cy), 2)},
            "crops": [],
        }
        for item_idx, item in enumerate(items):
            img = aligned_image(item)
            obs = candidate.observations[item_idx] if item_idx < len(candidate.observations) else None
            crop_source = (
                "matched_detection" if obs and obs.crop_cx is not None and obs.crop_cy is not None else "reference"
            )
            local_align = None
            if crop_source == "matched_detection":
                crop_cx = obs.crop_cx
                crop_cy = obs.crop_cy
                if weak_detection_crop(obs):
                    local_cx, local_cy, local_align = local_aligned_crop_center(
                        ref_img,
                        img,
                        candidate,
                        img_w,
                        img_h,
                        crop_px,
                        min_response=LOCAL_CROP_ALIGN_WEAK_DET_MIN_RESPONSE,
                    )
                    if local_align and local_align.get("accepted"):
                        local_align["replaced_detection"] = {
                            "center_x": round(float(crop_cx), 2),
                            "center_y": round(float(crop_cy), 2),
                            "dist_m": obs.dist_m,
                            "match_score": obs.match_score,
                        }
                        crop_cx = local_cx
                        crop_cy = local_cy
                        crop_source = "weak_detection_local_alignment"
            else:
                crop_cx, crop_cy, local_align = local_aligned_crop_center(
                    ref_img,
                    img,
                    candidate,
                    img_w,
                    img_h,
                    crop_px,
                    min_response=LOCAL_CROP_ALIGN_REFERENCE_MIN_RESPONSE,
                    max_shift_factor=LOCAL_CROP_ALIGN_REFERENCE_MAX_SHIFT_FACTOR,
                )
                if local_align and local_align.get("accepted"):
                    crop_source = "local_alignment"
            x1, y1, x2, y2 = crop_bounds(crop_cx, crop_cy, img_w, img_h, crop_px)
            chunk = img[y1:y2, x1:x2]
            if chunk.size == 0:
                item.crops.append({"file": None})
            else:
                name = f"cand_{i:03d}_{item.label}.jpg"
                path = crops_dir / name
                cv2.imwrite(str(path), chunk, [cv2.IMWRITE_JPEG_QUALITY, REVIEW_JPEG_QUALITY])
                item.crops.append(
                    {
                        "file": f"crops/{name}",
                        "x1": x1,
                        "y1": y1,
                        "x2": x2,
                        "y2": y2,
                        "center_x": round(float(crop_cx), 2),
                        "center_y": round(float(crop_cy), 2),
                        "center_source": crop_source,
                        "local_alignment": local_align,
                    }
                )
            crop_entry = item.crops[-1] if item.crops else {"file": None}
            manifest_candidate["crops"].append(
                {
                    "label": item.label,
                    "status": obs.status if obs else None,
                    "conf": round(obs.conf, 3) if obs and obs.conf is not None else None,
                    "dist_m": round(obs.dist_m, 2) if obs and obs.dist_m is not None else None,
                    "match_score": round(obs.match_score, 3) if obs and obs.match_score is not None else None,
                    "detection_center": (
                        {"x": round(obs.crop_cx, 2), "y": round(obs.crop_cy, 2)}
                        if obs and obs.crop_cx is not None and obs.crop_cy is not None
                        else None
                    ),
                    "final_center": (
                        {"x": crop_entry.get("center_x"), "y": crop_entry.get("center_y")}
                        if crop_entry.get("file")
                        else None
                    ),
                    "center_source": crop_entry.get("center_source"),
                    "local_alignment": crop_entry.get("local_alignment"),
                    "file": crop_entry.get("file"),
                }
            )
        manifest["candidates"].append(manifest_candidate)
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)


def observation_to_json(obs: Observation) -> dict[str, Any]:
    return {
        "label": obs.label,
        "status": obs.status,
        "reason": obs.reason,
        "conf": round(obs.conf, 3) if obs.conf is not None else None,
        "dist_m": round(obs.dist_m, 2) if obs.dist_m is not None else None,
        "angle_diff": round(obs.angle_diff, 1) if obs.angle_diff is not None else None,
        "color_similarity": round(obs.color_similarity, 3) if obs.color_similarity is not None else None,
        "shape_similarity": round(obs.shape_similarity, 3) if obs.shape_similarity is not None else None,
        "match_score": round(obs.match_score, 3) if obs.match_score is not None else None,
        "crop_cx": round(obs.crop_cx, 2) if obs.crop_cx is not None else None,
        "crop_cy": round(obs.crop_cy, 2) if obs.crop_cy is not None else None,
    }


def candidates_to_json(candidates: list[Candidate]) -> list[dict[str, Any]]:
    out_json: list[dict[str, Any]] = []
    for i, candidate in enumerate(candidates):
        out_json.append(
            {
                "rank": i + 1,
                "score": round(candidate.score, 4),
                "current_conf": round(candidate.current_conf, 4),
                "coverage": round(candidate.coverage, 3),
                "color_consistency": round(candidate.color_consistency, 3),
                "mean_conf": round(candidate.mean_conf, 3),
                "mean_match": round(candidate.mean_match, 3),
                "span_score": round(candidate.span_score, 3),
                "evidence_factor": round(candidate.evidence_factor, 3),
                "n_detections": candidate.n_detections,
                "valid_items": candidate.valid_items,
                "ignored_count": candidate.ignored_count,
                "clear_missing_count": candidate.clear_missing_count,
                "labels_present": candidate.labels_present,
                "observations": [observation_to_json(obs) for obs in candidate.observations],
                "lat": candidate.lat,
                "lon": candidate.lon,
            }
        )
    return out_json


def save_candidates_json(candidates: list[Candidate], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(candidates_to_json(candidates), f, indent=2, ensure_ascii=False)


def save_overlay(ref_item: ImageItem, candidates: list[Candidate], overlay_path: Path, eps_px: float) -> None:
    overlay = aligned_image(ref_item).copy()
    for candidate in candidates:
        cx, cy = int(candidate.cx), int(candidate.cy)
        color = (0, 255, 0) if candidate.score > 0.8 else (0, 200, 255) if candidate.score > 0.5 else (0, 0, 255)
        # Sam okrąg w promieniu eps_px — numery dostarczają Leaflet (mapa) i overlay-piny (raport).
        cv2.circle(overlay, (cx, cy), int(eps_px), color, 1, lineType=cv2.LINE_AA)
    cv2.imwrite(str(overlay_path), overlay, [cv2.IMWRITE_JPEG_QUALITY, 90])


def render_report(
    items: list[ImageItem],
    candidates: list[Candidate],
    md: dict[str, Any] | None,
    output_path: Path,
    asset_version: str,
    img_w: int = 0,
    img_h: int = 0,
    lang: str = "pl",
) -> None:
    def tr(key: str, **kw: Any) -> str:
        return _tr(lang, key, **kw)

    diagnostics_url = _versioned_asset_url("run_log.json", asset_version)
    crop_manifest_url = _versioned_asset_url("crop_manifest.json", asset_version)
    overlay_url = _versioned_asset_url("overlays/scored_overlay.jpg", asset_version)

    rows_html: list[str] = []
    overlay_pins_html: list[str] = []
    for i, candidate in enumerate(candidates):
        if img_w > 0 and img_h > 0:
            pin_x = (candidate.cx / img_w) * 100
            pin_y = (candidate.cy / img_h) * 100
            pin_color = "#10b981" if candidate.score > 0.7 else "#f59e0b" if candidate.score > 0.55 else "#ef4444"
            overlay_pins_html.append(
                f'<div class="overlay-pin" id="overlay-pin-{i + 1}" '
                f'style="left:{pin_x:.3f}%; top:{pin_y:.3f}%; background:{pin_color};" '
                f'onclick="goToCand({i + 1})" title="{tr("jumpTo", n=i + 1)}">{i + 1}</div>'
            )
        cells: list[str] = []
        observations = candidate.observations
        for item_idx, item in enumerate(items):
            crop = item.crops[i]
            obs = (
                observations[item_idx]
                if item_idx < len(observations)
                else Observation(label=item.label, status="missing")
            )
            status = obs.status
            if status == "present":
                status_text = (
                    tr("cell.match", conf=f"{obs.conf:.2f}")
                    if obs.conf is not None
                    else (obs.reason or tr("cell.match", conf="?"))
                )
            elif status == "ignored":
                status_text = obs.reason or tr("cell.skipped")
            else:
                status_text = tr("cell.missing")
            status_text = html_lib.escape(status_text)
            label = html_lib.escape(str(item.label))
            if crop and crop.get("file"):
                crop_url = _versioned_asset_url(str(crop["file"]), asset_version)
                cells.append(
                    f'<div class="crop-cell"><div class="yr">{label}<span>{status_text}</span></div>'
                    f'<div class="crop center {status}"><img src="{crop_url}" loading="lazy"></div></div>'
                )
            else:
                cells.append(
                    f'<div class="crop-cell"><div class="yr">{label}<span>{status_text}</span></div>'
                    f'<div class="crop empty {status}">{html_lib.escape(tr("cell.empty"))}</div></div>'
                )

        coords_html = ""
        if candidate.lat is not None:
            lat, lon = candidate.lat, candidate.lon
            links = external_map_links(lat, lon)
            verify_links = [
                (tr("link.streetView"), links["street_view"]),
                (tr("link.gmapsSat"), links["google_maps_satellite"]),
                (tr("link.appleMaps"), links["apple_maps"]),
                (tr("link.mapillary"), links["mapillary"]),
                (tr("link.geoportal"), links["geoportal"]),
            ]
            links_html = " · ".join(f'<a href="{url}" target="_blank">{name}</a>' for name, url in verify_links)
            coords_html = f'<div class="coords">📍 {lat:.6f}, {lon:.6f} → {links_html}</div>'

            # Dodatkowy kafel: Google Maps satelita jako pierwsza komórka w rzędzie
            # — przed ortofotomapami, bo Google ma zwykle starsze (i nieznane datą) zdjęcia.
            # Lazy-load, deep-link przez ?q=lat,lon&t=k.
            sat_url = google_maps_embed_url(lat, lon)
            cells.insert(
                0,
                '<div class="crop-cell">'
                f'<div class="yr">{tr("cell.satellite")}<span>{tr("cell.googleAge")}</span></div>'
                f'<div class="crop sat"><iframe loading="lazy" referrerpolicy="no-referrer-when-downgrade" src="{sat_url}"></iframe></div>'
                "</div>",
            )

        rows_html.append(
            f"""
        <div class="cand">
          <div class="cand-head">
            <span class="rank" id="cand-{i + 1}">#{i + 1}</span>
            <a class="locate" onclick="showOn({i + 1}); return false;" href="#overlay-wrap" title="{tr("showOnMap.title")}">{tr("showOnMap")}</a>
            <button type="button" class="save-wreck" onclick="saveWreck({i + 1}, this)">{tr("saveWreck")}</button>
            <span class="score" title="{tr("score.tooltip")}">Score <b>{(candidate.score * 100):.0f}%</b></span>
            <span class="badge cov">{tr("badge.coverage", n=candidate.n_detections, total=candidate.valid_items)}</span>
            <span class="badge col">{tr("badge.color", pct=f"{candidate.color_consistency * 100:.0f}")}</span>
            <span class="badge cnf">{tr("badge.yolo", val=f"{candidate.current_conf:.2f}")}</span>
            <span class="badge span">{tr("badge.span", pct=f"{candidate.span_score * 100:.0f}")}</span>
          </div>
          <div class="cand-meta">{tr("candMeta", labels=", ".join(candidate.labels_present), missing=candidate.clear_missing_count, ignored=candidate.ignored_count)}</div>
          {coords_html}
          <div class="grid-wrap">{"".join(cells)}</div>
        </div>
        """
        )

    html = f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8">
<title>{tr("title")}</title>
<base href="./">
<style>
  :root {{ --bg:#0b0f19; --card:#111827; --bdr:#1f2937; --txt:#e5e7eb; --mut:#94a3b8; --acc:#6366f1; --ok:#10b981; --miss:#ef4444; --skip:#64748b; }}
  body {{ font-family:system-ui,sans-serif; background:var(--bg); color:var(--txt); margin:0; padding:24px; }}
  .cand {{ background:var(--card); border:1px solid var(--bdr); border-radius:14px; padding:16px; margin-bottom:18px; }}
  .cand-head {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-bottom:6px; }}
  .rank {{ font-size:18px; font-weight:700; color:var(--acc); }}
  .score b {{ color:var(--ok); font-size:18px; }}
  .badge {{ font-size:11px; padding:3px 8px; border-radius:8px; background:#1e293b; color:var(--mut); }}
  .badge.cov {{ background:#1e3a8a; color:#bfdbfe; }}
  .badge.col {{ background:#581c87; color:#e9d5ff; }}
  .badge.span {{ background:#064e3b; color:#bbf7d0; }}
  .coords {{ font-size:12px; margin:6px 0 12px; color:var(--mut); }}
  .coords a {{ color:#93c5fd; text-decoration:none; }}
  .grid-wrap {{ display:flex; gap:8px; }}
  .crop-cell {{ display:flex; flex-direction:column; gap:4px; flex:1 1 0; min-width:0; }}
  .crop-cell .yr {{ font-size:11px; color:var(--mut); text-align:center; display:flex; flex-direction:column; gap:2px; }}
  .crop-cell .yr span {{ font-size:10px; color:#cbd5e1; }}
  .crop {{ position:relative; width:100%; aspect-ratio:1/1; background:#0a0f1c; border:1px solid var(--bdr); border-radius:8px; overflow:hidden; }}
  .crop img {{ width:100%; height:100%; object-fit:cover; display:block; }}
  .crop iframe {{ width:100%; height:100%; border:0; display:block; }}
  .crop.sat {{ border-color:#3b82f6; }}
  .crop.center::after {{ content:''; position:absolute; left:50%; top:50%; width:34px; height:34px; transform:translate(-50%,-50%); border:2px solid #f59e0b; border-radius:50%; pointer-events:none; box-shadow:0 0 0 1px rgba(0,0,0,0.5); }}
  .crop.present {{ border-color:var(--ok); }}
  .crop.present::after {{ border-color:var(--ok); }}
  .crop.missing {{ border-color:var(--miss); opacity:.72; }}
  .crop.missing::after {{ border-color:var(--miss); }}
  .crop.ignored {{ border-color:var(--skip); opacity:.58; }}
  .crop.ignored::after {{ border-color:var(--skip); }}
  .legend {{ background:#0d1424; border:1px solid var(--bdr); border-radius:10px; padding:10px 14px; margin-bottom:18px; font-size:13px; color:var(--mut); }}
  .legend a {{ color:#93c5fd; text-decoration:none; }}
  .legend a:hover {{ text-decoration:underline; }}
  .score {{ cursor:help; }}
  .locate {{ font-size:11px; color:#fbbf24; cursor:pointer; text-decoration:none; padding:3px 8px; border:1px solid rgba(251,191,36,0.35); border-radius:8px; background:rgba(251,191,36,0.06); transition:all 0.15s; }}
  .locate:hover {{ background:rgba(251,191,36,0.18); color:#fff; }}
  .save-wreck {{ font-size:11px; color:#bbf7d0; cursor:pointer; padding:4px 9px; border:1px solid rgba(16,185,129,0.35); border-radius:8px; background:rgba(16,185,129,0.10); font-weight:700; }}
  .save-wreck:hover {{ background:rgba(16,185,129,0.22); color:#fff; }}
  .save-wreck:disabled {{ cursor:default; opacity:.78; }}
  .overlay-wrap {{ position:relative; }}
  .overlay-pin {{ position:absolute; width:26px; height:26px; transform:translate(-50%, -50%); border:2px solid #fff; border-radius:50%; cursor:pointer; color:#0b0f19; font-weight:800; font-size:12px; display:flex; align-items:center; justify-content:center; box-shadow:0 2px 6px rgba(0,0,0,0.55); transition:transform 0.15s, box-shadow 0.15s; scroll-margin-top:25vh; line-height:1; }}
  .overlay-pin:hover {{ transform:translate(-50%, -50%) scale(1.18); box-shadow:0 3px 10px rgba(0,0,0,0.7); }}
  .overlay-pin.active {{ animation:pin-pulse 1.2s ease-in-out infinite; box-shadow:0 0 0 4px rgba(251,191,36,0.55), 0 0 18px rgba(251,191,36,0.85); border-color:#fbbf24; }}
  .rank {{ scroll-margin-top:25vh; }}
  @keyframes pin-pulse {{
    0%, 100% {{ transform:translate(-50%, -50%) scale(1); }}
    50%      {{ transform:translate(-50%, -50%) scale(1.18); }}
  }}
</style></head><body>
<div class="legend">
  <b>{tr("legend.title")}</b>
  <div style="font-size:11px; color:#94a3b8; margin-top:4px;">{tr("legend.note")} · <a href="{diagnostics_url}" target="_blank">{tr("legend.diagnostics")}</a> · <a href="{crop_manifest_url}" target="_blank">{tr("legend.cropManifest")}</a></div>
  <div class="overlay-wrap" id="overlay-wrap">
    <img src="{overlay_url}" style="width:100%; height:auto; border-radius:10px; margin-top:8px; display:block;">
    {"".join(overlay_pins_html)}
  </div>
</div>
{"".join(rows_html) if rows_html else f'<div style="padding:40px;">{tr("noCars")}</div>'}
<script>
function showOn(n) {{
    document.querySelectorAll('.overlay-pin.active').forEach(p => p.classList.remove('active'));
    const pin = document.getElementById('overlay-pin-' + n);
    if (!pin) return;
    pin.classList.add('active');
    pin.scrollIntoView({{behavior:'smooth', block:'start'}});
}}
function goToCand(n) {{
    const card = document.getElementById('cand-' + n);
    if (card) card.scrollIntoView({{behavior:'smooth', block:'start'}});
}}
let yoloWreckSavingAllowed = true;
let reportAdminAuthenticated = false;
function applyPublicFeatures(settings) {{
    yoloWreckSavingAllowed = reportAdminAuthenticated || !settings || settings.yolo_wrecks !== false;
    document.querySelectorAll('.save-wreck').forEach(btn => {{
        btn.hidden = !yoloWreckSavingAllowed;
        btn.disabled = !yoloWreckSavingAllowed;
    }});
}}
async function loadPublicFeatures() {{
    let publicFeatures = {{}};
    try {{
        const resp = await fetch('/api/settings', {{ cache: 'no-store' }});
        const data = await resp.json();
        if (resp.ok) publicFeatures = data.public_features || {{}};
    }} catch (_) {{}}
    try {{
        const resp = await fetch('/api/admin/status', {{ cache: 'no-store' }});
        const data = await resp.json();
        reportAdminAuthenticated = resp.ok && data.authenticated === true;
    }} catch (_) {{}}
    applyPublicFeatures(publicFeatures);
}}
async function saveWreck(rank, btn) {{
    if (!yoloWreckSavingAllowed) {{
        alert({json.dumps(tr("saveWreckDisabled"))});
        return;
    }}
    if (btn) {{
        btn.disabled = true;
        btn.textContent = {json.dumps(tr("savingWreck"))};
    }}
    try {{
        const resp = await fetch('/api/wrecks', {{
            method: 'POST',
            headers: {{ 'Content-Type': 'application/json' }},
            body: JSON.stringify({{ rank }})
        }});
        const data = await resp.json();
        if (!resp.ok || data.status !== 'ok') throw new Error(data.error || {json.dumps(tr("saveWreckError"))});
        if (btn) btn.textContent = data.evidence_created ? {json.dumps(tr("savedWreck"))} : {json.dumps(tr("alreadySavedWreck"))};
    }} catch (err) {{
        if (btn) {{
            btn.disabled = false;
            btn.textContent = {json.dumps(tr("saveWreck"))};
        }}
        alert(err.message || {json.dumps(tr("saveWreckError"))});
    }}
}}
loadPublicFeatures();
</script>
</body></html>"""
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    with output_path.open("w", encoding="utf-8") as f:
        f.write(html)


def write_analysis_outputs(
    items: list[ImageItem],
    ref_item: ImageItem,
    candidates: list[Candidate],
    md: dict[str, Any] | None,
    output_dir: Path,
    crops_dir: Path,
    overlay_dir: Path,
    img_w: int,
    img_h: int,
    eps_px: float,
    crop_px: int,
    lang: str = "pl",
) -> None:
    save_candidate_crops(
        items, ref_item, candidates, crops_dir, output_dir / "crop_manifest.json", img_w, img_h, crop_px=crop_px
    )
    save_overlay(ref_item, candidates, overlay_dir / "scored_overlay.jpg", eps_px)
    save_candidates_json(candidates, output_dir / "candidates.json")
    asset_version = str(time.time_ns())
    render_report(items, candidates, md, output_dir / "report.html", asset_version, img_w=img_w, img_h=img_h, lang=lang)
