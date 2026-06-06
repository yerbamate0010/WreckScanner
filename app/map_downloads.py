from __future__ import annotations

import json
import math
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

import numpy as np
import requests
from PIL import Image

from app import config
from core.config import BYTES_PER_GIB, BYTES_PER_MIB, NATIVE_TILE_PX
from core.geo import METERS_PER_DEGREE_LAT, bbox_4326, lon_meters_per_degree
from core.settings_store import load_geotiff_cache_max_bytes
from scripts.download_geoportal_wfs_geotiff import (
    choose_rgb_sheet,
    cleanup_geotiff_cache,
    crop_geotiff_to_png,
    download_tif,
    geotiff_cache_report,
    image_quality_for_path,
    local_tif_path,
    partial_tif_path,
    query_wfs_sheets,
    validate_tif_file,
    wfs_bbox_from_metadata,
)

ProgressCallback = Callable[..., None]
_thread_local = threading.local()


def get_http_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        _thread_local.session = session
    return session


def get_bbox(lat: float, lon: float, width_m: float, height_m: float) -> str:
    return bbox_4326(lat, lon, width_m, height_m)


def calculate_tile_bboxes(
    center_lat: float,
    center_lon: float,
    width_m: float,
    height_m: float,
    tile_size_meters: float = config.WMS_TILE_SIZE_M,
) -> tuple[int, int, list[tuple[int, int, str]]]:
    lat_meters = METERS_PER_DEGREE_LAT
    lon_meters = lon_meters_per_degree(center_lat)

    total_d_lat = height_m / lat_meters
    total_d_lon = width_m / lon_meters

    max_lat = center_lat + total_d_lat / 2.0
    min_lon = center_lon - total_d_lon / 2.0

    cols = math.ceil(width_m / tile_size_meters)
    rows = math.ceil(height_m / tile_size_meters)

    lat_step = total_d_lat / rows
    lon_step = total_d_lon / cols

    tiles = []
    for r in range(rows):
        for c in range(cols):
            tile_min_lat = max_lat - (r + 1) * lat_step
            tile_max_lat = max_lat - r * lat_step
            tile_min_lon = min_lon + c * lon_step
            tile_max_lon = min_lon + (c + 1) * lon_step
            bbox = f"{tile_min_lat:.6f},{tile_min_lon:.6f},{tile_max_lat:.6f},{tile_max_lon:.6f}"
            tiles.append((c, r, bbox))

    return cols, rows, tiles


def cleanup_old_data() -> None:
    """Kasuje stare PNG-i, miniatury i raport, żeby analiza nie pomieszała lokalizacji."""

    data_dir = config.DOWNLOAD_DATA_DIR
    if data_dir.is_dir():
        for path in data_dir.iterdir():
            if path.name.endswith(".png") or path.name == "metadata.json":
                try:
                    path.unlink()
                except OSError:
                    pass
            elif path.name == ".temp" and path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
    if config.ANALYSIS_DIR.is_dir():
        shutil.rmtree(config.ANALYSIS_DIR, ignore_errors=True)


def download_tile_for_year(year: int, c: int, r: int, bbox: str, resolution: int, temp_dir: Path):
    url = f"{config.WMS_UPSTREAM_BASE}/OGC_ortofoto_{year}/MapServer/WMSServer"
    tile_path = temp_dir / f"tile_{year}_{c}_{r}.png"
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": "1",
        "STYLES": "",
        "CRS": "EPSG:4326",
        "BBOX": bbox,
        "WIDTH": str(resolution),
        "HEIGHT": str(resolution),
        "FORMAT": "image/png",
    }

    last_error = None
    for attempt in range(config.TILE_DOWNLOAD_RETRIES + 1):
        try:
            resp = get_http_session().get(url, params=params, timeout=config.WMS_TIMEOUT)
            if resp.status_code == 200 and b"Exception" not in resp.content:
                tile_path.write_bytes(resp.content)
                return year, True, None
            last_error = f"HTTP {resp.status_code}"
        except requests.RequestException as exc:
            last_error = str(exc)

        if attempt < config.TILE_DOWNLOAD_RETRIES:
            time.sleep(config.TILE_DOWNLOAD_RETRY_BACKOFF_SECONDS * (attempt + 1))

    return year, False, last_error or "nieznany błąd WMS"


def build_metadata(
    lat: float,
    lon: float,
    width_m: float,
    height_m: float,
    resolution: int,
    final_width: int,
    final_height: int,
    bbox_full: str,
    results: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    min_lat, min_lon, max_lat, max_lon = [float(x) for x in bbox_full.split(",")]
    return {
        "center_lat": lat,
        "center_lon": lon,
        "width_meters": width_m,
        "height_meters": height_m,
        "resolution_per_tile_px": resolution,
        "image_width_px": final_width,
        "image_height_px": final_height,
        "bbox_4326": {
            "min_lat": min_lat,
            "min_lon": min_lon,
            "max_lat": max_lat,
            "max_lon": max_lon,
        },
        "years": [int(y) for y, result in results.items() if result.get("status") == "ok"],
        "source": "wroclaw_wms_geoportal_wfs_geotiff",
    }


def apply_wfs_geotiff_replacements(
    metadata: dict[str, Any],
    results: dict[int, dict[str, Any]],
    progress: ProgressCallback | None = None,
) -> list[dict[str, Any]]:
    summary = []
    session = get_http_session()
    bbox = wfs_bbox_from_metadata(metadata)

    for index, year in enumerate(config.WFS_GEOTIFF_YEARS, start=1):
        year_report: dict[str, Any] = {
            "year": year,
            "status": "pending",
            "cache": None,
            "pixel_m": None,
            "color": None,
            "acquisition_date": None,
            "file_size_mb": None,
        }
        if progress:
            progress(
                stage="wfs_query",
                message=f"GeoTIFF {year}: sprawdzam arkusze WFS",
                current=index - 1,
                total=len(config.WFS_GEOTIFF_YEARS),
            )

        try:
            sheets = query_wfs_sheets(year, bbox, session, config.WFS_GEOTIFF_TIMEOUT)
            selected = choose_rgb_sheet(sheets)
        except Exception as exc:
            year_report["status"] = "wfs_error"
            year_report["message"] = str(exc)
            summary.append(year_report)
            continue

        if selected is None:
            colors = sorted({(sheet.color or "brak").upper() for sheet in sheets})
            year_report["status"] = "no_rgb_sheet"
            year_report["message"] = f"Brak arkusza RGB. Dostępne kolory: {', '.join(colors) if colors else 'brak'}."
            summary.append(year_report)
            continue

        year_report.update(
            {
                "pixel_m": selected.pixel_m,
                "color": selected.color,
                "acquisition_date": selected.acquisition_date,
                "file_size_mb": selected.file_size_mb,
                "godlo": selected.godlo,
            }
        )

        if selected.pixel_m is None or selected.pixel_m > config.WFS_GEOTIFF_MAX_PIXEL_M:
            year_report["status"] = "skipped_low_resolution"
            year_report["message"] = (
                f"GeoTIFF ma {selected.pixel_m:g} m/pixel, więc zostawiam WMS dla tego roku."
                if selected.pixel_m is not None
                else "GeoTIFF nie ma informacji o rozdzielczości."
            )
            summary.append(year_report)
            continue

        tif_path = local_tif_path(selected, config.WFS_GEOTIFF_CACHE_DIR)
        part_path = partial_tif_path(tif_path)
        cache_limit_bytes = load_geotiff_cache_max_bytes()
        cleanup_before = cleanup_geotiff_cache(
            config.WFS_GEOTIFF_CACHE_DIR,
            cache_limit_bytes,
            keep_paths=[tif_path, part_path],
            stale_part_seconds=config.WFS_GEOTIFF_PART_TTL_SECONDS,
        )
        cache_hit = tif_path.exists() and tif_path.stat().st_size > 0
        partial_bytes = part_path.stat().st_size if part_path.exists() else 0
        estimated_total = int((selected.file_size_mb or 0) * BYTES_PER_MIB)
        estimated_total = max(estimated_total, partial_bytes)
        cache_state = "hit" if cache_hit else "partial" if partial_bytes > 0 else "miss"
        year_report["cache_path"] = str(tif_path)
        year_report["cache_before"] = cache_state
        year_report["cache_limit_gb"] = (
            None if cache_limit_bytes is None else round(cache_limit_bytes / BYTES_PER_GIB, 2)
        )
        if cleanup_before["removed"]:
            year_report["cache_cleanup_before"] = cleanup_before

        if progress:
            sheet_name = selected.godlo or tif_path.name
            if cache_hit:
                cached_bytes = tif_path.stat().st_size
                progress(
                    stage="wfs_cache",
                    message=f"GeoTIFF {year}: używam cache arkusza {sheet_name}",
                    current=cached_bytes,
                    total=cached_bytes,
                    year=year,
                    cache=cache_state,
                    bytes_done=cached_bytes,
                    bytes_total=cached_bytes,
                    cache_report=geotiff_cache_report(config.WFS_GEOTIFF_CACHE_DIR),
                )
            else:
                progress(
                    stage="wfs_download",
                    message=(
                        f"GeoTIFF {year}: sprawdzam wznowienie arkusza {sheet_name}"
                        if partial_bytes > 0
                        else f"GeoTIFF {year}: pobieram nowy arkusz {sheet_name}"
                    ),
                    year=year,
                    cache=cache_state,
                    bytes_done=None,
                    bytes_total=estimated_total or None,
                    cache_report=geotiff_cache_report(config.WFS_GEOTIFF_CACHE_DIR),
                )

        def on_download_progress(done, total, *, resume_from=0, resumed=False, restarted=False):
            effective_total = total or int((selected.file_size_mb or 0) * BYTES_PER_MIB)
            effective_total = max(effective_total, done)
            sheet_name = selected.godlo or tif_path.name
            if resumed:
                message = f"GeoTIFF {year}: wznawiam pobieranie arkusza {sheet_name}"
                cache = "partial"
            elif restarted:
                message = f"GeoTIFF {year}: pobieram od początku arkusz {sheet_name}"
                cache = "miss"
            else:
                message = f"GeoTIFF {year}: pobieram arkusz {sheet_name}"
                cache = "miss"
            if progress:
                progress(
                    stage="wfs_download",
                    message=message,
                    current=done,
                    total=effective_total,
                    year=year,
                    cache=cache,
                    resume_from=resume_from,
                    bytes_done=done,
                    bytes_total=effective_total,
                )

        try:
            tif_download = download_tif(
                selected,
                config.WFS_GEOTIFF_CACHE_DIR,
                session,
                config.WFS_GEOTIFF_TIMEOUT,
                progress=on_download_progress,
            )
            tif_path = tif_download.path
            png_path = config.DOWNLOAD_DATA_DIR / f"ortofoto_{year}.png"
            crop_info = crop_geotiff_to_png(tif_path, png_path, metadata)
            quality = image_quality_for_path(png_path) or {}
            size_kb = png_path.stat().st_size / 1024
            cleanup_after = cleanup_geotiff_cache(
                config.WFS_GEOTIFF_CACHE_DIR,
                cache_limit_bytes,
                keep_paths=[tif_path],
                stale_part_seconds=config.WFS_GEOTIFF_PART_TTL_SECONDS,
            )
        except Exception as exc:
            year_report["status"] = "error"
            year_report["message"] = str(exc)
            summary.append(year_report)
            continue

        year_report.update(
            {
                "status": "replaced",
                "cache": tif_download.cache,
                "downloaded_tif": str(tif_path),
                "output_png": str(png_path),
                "crop": crop_info,
                "quality": quality,
                "download": {
                    "resume_from": tif_download.resume_from,
                    "bytes_done": tif_download.bytes_done,
                    "bytes_total": tif_download.bytes_total,
                },
                "cache_report": geotiff_cache_report(config.WFS_GEOTIFF_CACHE_DIR),
            }
        )
        if cleanup_after["removed"]:
            year_report["cache_cleanup_after"] = cleanup_after
        results[year] = {
            "status": "ok",
            "source": "geoportal_wfs_geotiff",
            "cache": tif_download.cache,
            "pixel_m": selected.pixel_m,
            "size_kb": round(size_kb, 1),
            "std": quality.get("std"),
            "sharpness": quality.get("sharpness"),
            "file": str(png_path),
        }
        summary.append(year_report)

    if progress:
        progress(
            stage="wfs_done",
            message="GeoTIFF: zakończono sprawdzanie cache i wycinek",
            current=len(config.WFS_GEOTIFF_YEARS),
            total=len(config.WFS_GEOTIFF_YEARS),
        )
    return summary


def _format_bytes_gib(value: int | float | None) -> float:
    return round(float(value or 0) / BYTES_PER_GIB, 2)


def _geotiff_bounds_4326(path: Path) -> dict[str, Any] | None:
    try:
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(path) as src:
            bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21) if src.crs else src.bounds
            return {
                "min_lon": bounds[0],
                "min_lat": bounds[1],
                "max_lon": bounds[2],
                "max_lat": bounds[3],
                "width_px": src.width,
                "height_px": src.height,
                "crs": str(src.crs) if src.crs else None,
            }
    except Exception as exc:
        return {"error": str(exc)}


def _cached_geotiff_items(raw_dir: Path) -> list[dict[str, Any]]:
    if not raw_dir.is_dir():
        return []
    items: list[dict[str, Any]] = []
    paths = sorted(
        [
            path
            for path in raw_dir.iterdir()
            if path.is_file()
            and (path.suffix.lower() in {".tif", ".tiff"} or path.name.lower().endswith((".tif.part", ".tiff.part")))
        ]
    )
    for path in paths:
        is_partial = path.name.lower().endswith((".tif.part", ".tiff.part"))
        try:
            stat = path.stat()
        except OSError:
            continue
        item: dict[str, Any] = {
            "file": path.name,
            "path": str(path),
            "status": "partial" if is_partial else "complete",
            "size_bytes": stat.st_size,
            "size_gb": _format_bytes_gib(stat.st_size),
            "modified_at": int(stat.st_mtime),
        }
        if not is_partial:
            try:
                validate_tif_file(path)
            except ValueError as exc:
                item["status"] = "invalid"
                item["error"] = str(exc)
            item["bounds_4326"] = _geotiff_bounds_4326(path)
        items.append(item)
    return items


def _estimate_wroclaw_geotiff_cache() -> dict[str, Any]:
    min_lat, min_lon, max_lat, max_lon = config.WROCLAW_ESTIMATE_BBOX_4326
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon},EPSG:4326"
    session = get_http_session()
    by_file: dict[str, dict[str, Any]] = {}
    years: list[dict[str, Any]] = []
    for year in config.WFS_GEOTIFF_YEARS:
        year_report: dict[str, Any] = {"year": year, "status": "ok", "sheets": 0, "total_mb": 0.0}
        try:
            sheets = query_wfs_sheets(year, bbox, session, config.WFS_GEOTIFF_TIMEOUT)
        except Exception as exc:
            year_report.update({"status": "error", "error": str(exc)})
            years.append(year_report)
            continue

        selected = [
            sheet
            for sheet in sheets
            if (sheet.color or "").upper() == "RGB"
            and (sheet.filled or "").upper() == "TAK"
            and sheet.download_url
            and sheet.file_size_mb is not None
        ]
        seen = set()
        for sheet in selected:
            key = local_tif_path(sheet, config.WFS_GEOTIFF_CACHE_DIR).name
            if key in seen:
                continue
            seen.add(key)
            size_mb = float(sheet.file_size_mb or 0)
            year_report["sheets"] += 1
            year_report["total_mb"] += size_mb
            by_file.setdefault(
                key,
                {
                    "file": key,
                    "years": [],
                    "size_mb": size_mb,
                    "godlo": sheet.godlo,
                    "pixel_m": sheet.pixel_m,
                },
            )["years"].append(year)
        year_report["total_gb"] = round(year_report["total_mb"] / 1024, 2)
        years.append(year_report)

    total_mb = sum(float(item.get("size_mb") or 0) for item in by_file.values())
    return {
        "bbox_4326": {
            "min_lat": min_lat,
            "min_lon": min_lon,
            "max_lat": max_lat,
            "max_lon": max_lon,
        },
        "years": years,
        "unique_files": len(by_file),
        "total_mb": round(total_mb, 1),
        "total_gb": round(total_mb / 1024, 2),
        "files": sorted(by_file.values(), key=lambda item: str(item.get("file"))),
    }


def geotiff_admin_cache_report(include_estimate: bool = True) -> dict[str, Any]:
    raw_dir = config.WFS_GEOTIFF_CACHE_DIR
    report = geotiff_cache_report(raw_dir)
    items = _cached_geotiff_items(raw_dir)
    complete = [item for item in items if item["status"] == "complete"]
    partial = [item for item in items if item["status"] == "partial"]
    invalid = [item for item in items if item["status"] == "invalid"]
    payload: dict[str, Any] = {
        "status": "ok",
        "dir": str(raw_dir),
        "summary": {
            **report,
            "total_gb": _format_bytes_gib(report["total_bytes"]),
            "completed_gb": _format_bytes_gib(report["completed_bytes"]),
            "partial_gb": _format_bytes_gib(report["partial_bytes"]),
            "invalid_files": len(invalid),
        },
        "items": items,
        "coverage": [item for item in complete if isinstance(item.get("bounds_4326"), dict)],
        "partials": partial,
        "invalid": invalid,
    }
    if include_estimate:
        payload["estimate"] = _estimate_wroclaw_geotiff_cache()
    return payload


def download_maps(
    lat: float,
    lon: float,
    width_m: float = config.MAX_SCAN_SIZE_M,
    height_m: float = config.MAX_SCAN_SIZE_M,
    progress: ProgressCallback | None = None,
) -> tuple[dict[int, dict[str, Any]], str, list[dict[str, Any]]]:
    """Download orthophoto tiles for all configured years and stitch them at native WMS density."""

    cleanup_old_data()
    config.DOWNLOAD_DATA_DIR.mkdir(parents=True, exist_ok=True)
    temp_dir = config.DOWNLOAD_DATA_DIR / ".temp"
    temp_dir.mkdir(parents=True, exist_ok=True)

    cols, rows, tiles = calculate_tile_bboxes(lat, lon, width_m, height_m, config.WMS_TILE_SIZE_M)
    resolution = NATIVE_TILE_PX
    final_width = cols * resolution
    final_height = rows * resolution

    if final_width > config.WMS_STITCH_MAX_DIM_PX or final_height > config.WMS_STITCH_MAX_DIM_PX:
        raise ValueError(
            f"Żądany obraz ({final_width}x{final_height}) jest zbyt duży do wygenerowania w pamięci. Zmniejsz rozmiar kadru."
        )

    results: dict[int, dict[str, Any]] = {}
    bbox_full = get_bbox(lat, lon, width_m, height_m)
    success_counts = {year: 0 for year in config.WMS_YEARS}
    completed_tiles = 0
    total_tiles = len(tiles) * len(config.WMS_YEARS)

    with ThreadPoolExecutor(max_workers=config.MAX_LAYER_DOWNLOAD_WORKERS) as executor:
        for c, r, bbox in tiles:
            futures = [
                executor.submit(download_tile_for_year, year, c, r, bbox, resolution, temp_dir)
                for year in config.WMS_YEARS
            ]
            for future in as_completed(futures):
                year, ok, error = future.result()
                completed_tiles += 1
                if progress:
                    progress(
                        stage="wms",
                        message=f"WMS: pobieram kafelki {completed_tiles}/{total_tiles}",
                        current=completed_tiles,
                        total=total_tiles,
                    )
                if ok:
                    success_counts[year] += 1
                else:
                    print(f"Błąd WMS dla kafelka {year} {c},{r}: {error}")

    for year in config.WMS_YEARS:
        success_count = success_counts[year]
        if success_count > 0:
            final_img = Image.new("RGB", (final_width, final_height))
            for c, r, _bbox in tiles:
                tile_path = temp_dir / f"tile_{year}_{c}_{r}.png"
                if not tile_path.exists():
                    continue
                with Image.open(tile_path) as tile_img:
                    final_img.paste(tile_img, (c * resolution, r * resolution))

            std = float(np.asarray(final_img).std())
            if std < config.BLANK_IMAGE_STD_THRESHOLD:
                results[year] = {
                    "status": "missing",
                    "detail": f"WMS zwrócił pusty obraz dla {year} (std={std:.1f}) — brak ortofoto dla tego roku",
                }
                print(f"   ⏭  {year}: brak ortofoto (std={std:.1f})")
            else:
                filepath = config.DOWNLOAD_DATA_DIR / f"ortofoto_{year}.png"
                final_img.save(filepath)
                kb = filepath.stat().st_size / 1024
                results[year] = {"status": "ok", "size_kb": round(kb, 1), "file": str(filepath), "std": round(std, 1)}
        else:
            results[year] = {"status": "error", "detail": "Nie udało się pobrać żadnych kafelków WMS dla tego roku."}

    metadata = build_metadata(lat, lon, width_m, height_m, resolution, final_width, final_height, bbox_full, results)
    wfs_summary = apply_wfs_geotiff_replacements(metadata, results, progress=progress)
    metadata["years"] = [int(y) for y, result in results.items() if result.get("status") == "ok"]
    metadata["wfs_geotiff"] = wfs_summary

    with (config.DOWNLOAD_DATA_DIR / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    return results, bbox_full, wfs_summary
