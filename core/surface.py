from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_TIMEOUT = (10, 35)
SURFACE_CACHE_TTL_SECONDS = 10 * 60
SURFACE_STALE_CACHE_TTL_SECONDS = 24 * 60 * 60
SURFACE_ERROR_CACHE_TTL_SECONDS = 5 * 60
SURFACE_CACHE_DIR = Path(".cache") / "surface_features"
MAX_BBOX_LAT_SPAN = 0.35
MAX_BBOX_LON_SPAN = 0.55
OVERPASS_COOLDOWN_SECONDS = 120
_overpass_cooldown_until = 0.0
_overpass_cooldown_error = ""


def _bbox_key(bbox: tuple[float, float, float, float]) -> str:
    return "_".join(f"{value:.5f}" for value in bbox)


def _cache_path(bbox: tuple[float, float, float, float]) -> Path:
    return SURFACE_CACHE_DIR / f"{_bbox_key(bbox)}.json"


def _error_cache_path(bbox: tuple[float, float, float, float]) -> Path:
    return SURFACE_CACHE_DIR / f"{_bbox_key(bbox)}.error.json"


def _read_cache(path: Path, *, max_age_seconds: int) -> dict[str, Any] | None:
    try:
        if time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _feature_count(payload: dict[str, Any] | None) -> int:
    if not isinstance(payload, dict):
        return 0
    features = payload.get("features")
    return len(features) if isinstance(features, list) else 0


def _payload_bbox(payload: dict[str, Any]) -> tuple[float, float, float, float] | None:
    raw = payload.get("bbox")
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    try:
        min_lon, min_lat, max_lon, max_lat = [float(value) for value in raw]
    except (TypeError, ValueError):
        return None
    return min_lat, min_lon, max_lat, max_lon


def _bbox_area(bbox: tuple[float, float, float, float]) -> float:
    min_lat, min_lon, max_lat, max_lon = bbox
    return max(0.0, max_lat - min_lat) * max(0.0, max_lon - min_lon)


def _bbox_contains(container: tuple[float, float, float, float], bbox: tuple[float, float, float, float]) -> bool:
    min_lat, min_lon, max_lat, max_lon = container
    q_min_lat, q_min_lon, q_max_lat, q_max_lon = bbox
    return min_lat <= q_min_lat and min_lon <= q_min_lon and max_lat >= q_max_lat and max_lon >= q_max_lon


def _bbox_overlaps(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> bool:
    a_min_lat, a_min_lon, a_max_lat, a_max_lon = first
    b_min_lat, b_min_lon, b_max_lat, b_max_lon = second
    return not (a_max_lat < b_min_lat or b_max_lat < a_min_lat or a_max_lon < b_min_lon or b_max_lon < a_min_lon)


def _nearby_feature_cache(bbox: tuple[float, float, float, float]) -> dict[str, Any] | None:
    if not SURFACE_CACHE_DIR.is_dir():
        return None
    candidates: list[tuple[int, float, float, dict[str, Any]]] = []
    for path in SURFACE_CACHE_DIR.glob("*.json"):
        if path.name.endswith(".error.json"):
            continue
        payload = _read_cache(path, max_age_seconds=SURFACE_STALE_CACHE_TTL_SECONDS)
        count = _feature_count(payload)
        if not payload or count <= 0:
            continue
        payload_bbox = _payload_bbox(payload)
        if not payload_bbox or not _bbox_overlaps(payload_bbox, bbox):
            continue
        contains_rank = 0 if _bbox_contains(payload_bbox, bbox) else 1
        try:
            modified = path.stat().st_mtime
        except OSError:
            modified = 0.0
        candidates.append((contains_rank, _bbox_area(payload_bbox), -modified, payload))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    selected = dict(candidates[0][3])
    selected["cache"] = "nearby"
    selected["warning"] = "Używam najbliższego cache warstwy nawierzchni z obiektami."
    return selected


def _write_cache(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
    except OSError:
        pass


def _empty_geojson(bbox: tuple[float, float, float, float], *, error: str = "", cache: str = "error") -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [],
        "bbox": [bbox[1], bbox[0], bbox[3], bbox[2]],
        "sources": ["OSM/Overpass"],
        "cache": cache,
        "error": error,
    }


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    try:
        parts = [float(part) for part in str(raw or "").split(",")]
    except ValueError as exc:
        raise ValueError("Podaj bbox jako minLat,minLon,maxLat,maxLon.") from exc
    if len(parts) != 4:
        raise ValueError("Podaj bbox jako minLat,minLon,maxLat,maxLon.")
    min_lat, min_lon, max_lat, max_lon = parts
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon
    if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90 and -180 <= min_lon <= 180 and -180 <= max_lon <= 180):
        raise ValueError("BBox warstwy nawierzchni jest poza zakresem.")
    if max_lat - min_lat > MAX_BBOX_LAT_SPAN or max_lon - min_lon > MAX_BBOX_LON_SPAN:
        raise ValueError("Przybliż mapę, żeby pobrać warstwę nawierzchni.")
    return min_lat, min_lon, max_lat, max_lon


def _overpass_query(bbox: tuple[float, float, float, float]) -> str:
    min_lat, min_lon, max_lat, max_lon = bbox
    box = f"{min_lat:.7f},{min_lon:.7f},{max_lat:.7f},{max_lon:.7f}"
    return f"""
[out:json][timeout:25];
(
  way["highway"]({box});
  way["amenity"="parking"]({box});
  way["surface"]({box});
  way["kerb"]({box});
  node["kerb"]({box});
);
out body geom;
"""


def _feature_kind(tags: dict[str, Any]) -> str:
    highway = str(tags.get("highway") or "")
    if highway in {"footway", "path", "pedestrian", "steps", "cycleway"}:
        return "sidewalk"
    if tags.get("amenity") == "parking":
        return "parking"
    if tags.get("kerb"):
        return "kerb"
    if highway:
        return "road"
    return "surface"


def _line_feature(element: dict[str, Any]) -> dict[str, Any] | None:
    geometry = element.get("geometry")
    if not isinstance(geometry, list) or len(geometry) < 2:
        return None
    coords = []
    for point in geometry:
        try:
            coords.append([float(point["lon"]), float(point["lat"])])
        except (KeyError, TypeError, ValueError):
            continue
    if len(coords) < 2:
        return None
    tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {
            "id": element.get("id"),
            "kind": _feature_kind(tags),
            "name": tags.get("name") or "",
            "highway": tags.get("highway") or "",
            "surface": tags.get("surface") or "",
            "kerb": tags.get("kerb") or "",
            "source": "OSM/Overpass",
        },
    }


def _point_feature(element: dict[str, Any]) -> dict[str, Any] | None:
    try:
        lon = float(element["lon"])
        lat = float(element["lat"])
    except (KeyError, TypeError, ValueError):
        return None
    tags = element.get("tags") if isinstance(element.get("tags"), dict) else {}
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "id": element.get("id"),
            "kind": _feature_kind(tags),
            "kerb": tags.get("kerb") or "",
            "surface": tags.get("surface") or "",
            "source": "OSM/Overpass",
        },
    }


def _geojson_from_overpass(payload: dict[str, Any], bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    features = []
    for element in payload.get("elements") or []:
        if not isinstance(element, dict):
            continue
        feature = _line_feature(element) if element.get("type") == "way" else _point_feature(element)
        if feature:
            features.append(feature)
    return {
        "type": "FeatureCollection",
        "features": features,
        "bbox": [bbox[1], bbox[0], bbox[3], bbox[2]],
        "sources": ["OSM/Overpass", "KIEG/BDOT context in parcel layer"],
    }


def surface_features_geojson(bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    global _overpass_cooldown_error, _overpass_cooldown_until

    path = _cache_path(bbox)
    cached = _read_cache(path, max_age_seconds=SURFACE_CACHE_TTL_SECONDS)
    if cached and _feature_count(cached) > 0:
        cached["cache"] = "hit"
        return cached

    error_path = _error_cache_path(bbox)
    cached_error = _read_cache(error_path, max_age_seconds=SURFACE_ERROR_CACHE_TTL_SECONDS)
    if cached_error:
        return cached_error

    if time.time() < _overpass_cooldown_until:
        nearby = _nearby_feature_cache(bbox)
        if nearby:
            return nearby
        return _empty_geojson(
            bbox,
            error=_overpass_cooldown_error or "Overpass jest chwilowo limitowany.",
            cache="cooldown",
        )

    last_error = ""
    query = _overpass_query(bbox)
    for url in OVERPASS_URLS:
        try:
            response = requests.post(url, data={"data": query}, timeout=OVERPASS_TIMEOUT)
            if response.status_code in {429, 502, 503, 504}:
                last_error = f"{url}: HTTP {response.status_code}"
                if response.status_code == 429:
                    _overpass_cooldown_until = time.time() + OVERPASS_COOLDOWN_SECONDS
                    _overpass_cooldown_error = last_error
                    break
                continue
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            last_error = f"{url}: {exc}"
            continue
        geojson = _geojson_from_overpass(payload, bbox)
        geojson["cache"] = "miss"
        if _feature_count(geojson) > 0:
            _write_cache(path, geojson)
            return geojson
        nearby = _nearby_feature_cache(bbox)
        if nearby:
            return nearby
        error_geojson = _empty_geojson(
            bbox,
            error="Overpass zwrócił pustą warstwę nawierzchni dla tego widoku.",
            cache="empty",
        )
        _write_cache(error_path, error_geojson)
        return error_geojson

    stale = _read_cache(path, max_age_seconds=SURFACE_STALE_CACHE_TTL_SECONDS)
    if stale and _feature_count(stale) > 0:
        stale["cache"] = "stale"
        stale["warning"] = last_error or "Nie udało się odświeżyć warstwy nawierzchni."
        return stale
    nearby = _nearby_feature_cache(bbox)
    if nearby:
        return nearby

    error_geojson = _empty_geojson(
        bbox,
        error=last_error or "Overpass jest chwilowo niedostępny albo limituje zapytania.",
        cache="error",
    )
    _write_cache(error_path, error_geojson)
    return error_geojson
