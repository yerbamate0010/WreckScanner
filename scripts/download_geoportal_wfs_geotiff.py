"""
Eksperymentalny downloader WFS -> GeoTIFF -> crop PNG.

WFS Geoportalu Krajowego udostepnia skorowidze arkuszy ortofotomapy, a nie
sam raster. Ten skrypt znajduje arkusze RGB dla biezacego bboxa z metadata.json,
pobiera wskazane GeoTIFF-y i wycina z nich dokladnie ten sam obszar.

Uzycie:
    python3 scripts/download_geoportal_wfs_geotiff.py
    python3 scripts/download_geoportal_wfs_geotiff.py --years 2024 2025
    python3 scripts/download_geoportal_wfs_geotiff.py --list-only
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import cv2
import numpy as np
import requests
from defusedxml import ElementTree as ET
from PIL import Image, UnidentifiedImageError

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.config import DATA_DIR
from core.runtime import configure_process_encoding
from core.vision import image_quality

WFS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WFS/Skorowidze"
GUGIK_NS = "http://www.gugik.gov.pl"
WFS_NS = "http://www.opengis.net/wfs/2.0"
GML_NS = "http://www.opengis.net/gml/3.2"
NS = {"gugik": GUGIK_NS, "wfs": WFS_NS, "gml": GML_NS}


@dataclass(frozen=True)
class OrthoSheet:
    year: int
    feature_id: str
    godlo: str | None
    pixel_m: float | None
    color: str | None
    source: str | None
    layout: str | None
    archive_module: str | None
    report_id: str | None
    acquisition_date: str | None
    pzgik_date: str | None
    filled: str | None
    download_url: str | None
    file_size_mb: float | None


@dataclass(frozen=True)
class TifDownloadResult:
    path: Path
    cache: str
    resume_from: int
    bytes_done: int
    bytes_total: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pobierz surowe GeoTIFF-y ORTO przez WFS i wytnij biezacy bbox.")
    parser.add_argument("--data", type=Path, default=DATA_DIR, help="Katalog z metadata.json i obecnymi WMS PNG.")
    parser.add_argument("--out", type=Path, default=DATA_DIR / "wfs_geotiff_spike", help="Katalog wynikow spike'a.")
    parser.add_argument(
        "--years", type=int, nargs="+", default=[2024, 2025], help="Lata skorowidzow WFS do sprawdzenia."
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="Timeout HTTP w sekundach.")
    parser.add_argument(
        "--list-only", action="store_true", help="Tylko odpytaj WFS i zapisz manifest, bez pobierania GeoTIFF."
    )
    return parser.parse_args()


def require_rasterio() -> tuple[Any, Any, Any]:
    try:
        import rasterio
        from rasterio.enums import Resampling
        from rasterio.warp import transform_bounds
        from rasterio.windows import from_bounds
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Brak zaleznosci rasterio. Zainstaluj wymagania projektu: python3 -m pip install -r requirements.txt"
        ) from exc
    return rasterio, Resampling, transform_bounds, from_bounds


def load_metadata(data_dir: Path) -> dict[str, Any]:
    path = data_dir / "metadata.json"
    if not path.exists():
        raise FileNotFoundError(f"Brak {path}. Najpierw pobierz obszar przez UI.")
    with path.open(encoding="utf-8") as f:
        md = json.load(f)
    required = (
        "center_lat",
        "center_lon",
        "width_meters",
        "height_meters",
        "image_width_px",
        "image_height_px",
        "bbox_4326",
    )
    missing = [key for key in required if key not in md]
    if missing:
        raise ValueError(f"metadata.json nie zawiera wymaganych pol: {', '.join(missing)}")
    return md


def wfs_bbox_from_metadata(md: dict[str, Any]) -> str:
    bb = md["bbox_4326"]
    # Geoportal WFS dla EPSG:4326 oczekuje osi lat,lon.
    return f"{bb['min_lat']},{bb['min_lon']},{bb['max_lat']},{bb['max_lon']},EPSG:4326"


def text_at(node: ET.Element, path: str) -> str | None:
    found = node.find(path, NS)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def time_at(node: ET.Element, path: str) -> str | None:
    found = node.find(path, NS)
    if found is None:
        return None
    return text_at(found, "gml:timePosition")


def float_or_none(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except ValueError:
        return None
    return out if math.isfinite(out) else None


def parse_date(value: str | None) -> date:
    if not value:
        return date.min
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return date.min


def parse_sheet(feature: ET.Element, year: int) -> OrthoSheet:
    return OrthoSheet(
        year=year,
        feature_id=feature.attrib.get(f"{{{GML_NS}}}id", ""),
        godlo=text_at(feature, "gugik:godlo"),
        pixel_m=float_or_none(text_at(feature, "gugik:piksel")),
        color=text_at(feature, "gugik:kolor"),
        source=text_at(feature, "gugik:zrodlo_danych"),
        layout=text_at(feature, "gugik:uklad_xy"),
        archive_module=text_at(feature, "gugik:modul_archiwizacji"),
        report_id=text_at(feature, "gugik:nr_zglosz"),
        acquisition_date=time_at(feature, "gugik:akt_data"),
        pzgik_date=time_at(feature, "gugik:dt_pzgik"),
        filled=text_at(feature, "gugik:czy_ark_wypelniony"),
        download_url=text_at(feature, "gugik:url_do_pobrania"),
        file_size_mb=float_or_none(text_at(feature, "gugik:wlk_pliku_mb")),
    )


def query_wfs_sheets(year: int, bbox: str, session: requests.Session, timeout: float) -> list[OrthoSheet]:
    params = {
        "SERVICE": "WFS",
        "VERSION": "2.0.0",
        "REQUEST": "GetFeature",
        "TYPENAMES": f"gugik:SkorowidzOrtofomapy{year}",
        "SRSNAME": "EPSG:4326",
        "BBOX": bbox,
    }
    resp = session.get(WFS_URL, params=params, timeout=timeout)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    sheets: list[OrthoSheet] = []
    for member in root.findall("wfs:member", NS):
        feature = next(iter(member), None)
        if feature is not None:
            sheets.append(parse_sheet(feature, year))
    return sheets


def choose_rgb_sheet(sheets: list[OrthoSheet]) -> OrthoSheet | None:
    rgb = [
        sheet
        for sheet in sheets
        if (sheet.color or "").upper() == "RGB"
        and (sheet.filled or "").upper() == "TAK"
        and sheet.download_url
        and sheet.pixel_m is not None
    ]
    if not rgb:
        return None
    return sorted(
        rgb,
        key=lambda sheet: (
            sheet.pixel_m,
            -parse_date(sheet.acquisition_date).toordinal(),
            sheet.download_url or "",
        ),
    )[0]


def local_tif_path(sheet: OrthoSheet, raw_dir: Path) -> Path:
    parsed = urlparse(sheet.download_url or "")
    name = Path(parsed.path).name or f"ortofoto_{sheet.year}_{sheet.feature_id}.tif"
    return raw_dir / name


def partial_tif_path(tif_path: Path) -> Path:
    return tif_path.with_suffix(tif_path.suffix + ".part")


def _response_looks_like_markup(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(1024).lstrip().lower()
    except OSError:
        return False
    return head.startswith((b"<!doctype", b"<html", b"<?xml", b"<wfs:", b"<ows:", b"<serviceexception"))


def validate_tif_file(path: Path) -> None:
    try:
        with path.open("rb") as f:
            head = f.read(16)
    except OSError as exc:
        raise ValueError(f"{path.name}: nie można odczytać pliku GeoTIFF.") from exc
    if _response_looks_like_markup(path):
        raise ValueError(f"{path.name}: serwer zwrócił HTML/XML zamiast GeoTIFF.")
    if not head.startswith((b"II*\x00", b"MM\x00*", b"II+\x00", b"MM\x00+")):
        raise ValueError(f"{path.name}: odpowiedź nie wygląda jak plik TIFF.")

    try:
        import rasterio

        with rasterio.open(path) as src:
            if src.width <= 0 or src.height <= 0:
                raise ValueError("puste wymiary rastra")
        return
    except ModuleNotFoundError:
        pass
    except Exception as raster_exc:
        try:
            with Image.open(path) as image:
                if str(image.format or "").upper() != "TIFF":
                    raise ValueError("format nie jest TIFF")
                image.verify()
            return
        except (OSError, UnidentifiedImageError, ValueError) as pil_exc:
            raise ValueError(f"{path.name}: uszkodzony albo niepełny GeoTIFF ({raster_exc}).") from pil_exc

    try:
        with Image.open(path) as image:
            if str(image.format or "").upper() != "TIFF":
                raise ValueError("format nie jest TIFF")
            image.verify()
    except (OSError, UnidentifiedImageError, ValueError) as exc:
        raise ValueError(f"{path.name}: uszkodzony albo niepełny GeoTIFF.") from exc


def geotiff_cache_report(raw_dir: Path) -> dict[str, Any]:
    if not raw_dir.is_dir():
        return {
            "total_bytes": 0,
            "completed_bytes": 0,
            "partial_bytes": 0,
            "completed_files": 0,
            "partial_files": 0,
        }

    completed = [p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() in {".tif", ".tiff"}]
    partials = [p for p in raw_dir.iterdir() if p.is_file() and p.name.lower().endswith((".tif.part", ".tiff.part"))]
    completed_bytes = sum(p.stat().st_size for p in completed)
    partial_bytes = sum(p.stat().st_size for p in partials)
    return {
        "total_bytes": completed_bytes + partial_bytes,
        "completed_bytes": completed_bytes,
        "partial_bytes": partial_bytes,
        "completed_files": len(completed),
        "partial_files": len(partials),
    }


def cleanup_geotiff_cache(
    raw_dir: Path,
    max_bytes: int | None,
    *,
    keep_paths: list[Path] | None = None,
    stale_part_seconds: int = 24 * 60 * 60,
) -> dict[str, Any]:
    raw_dir.mkdir(parents=True, exist_ok=True)
    keep = {path.resolve() for path in (keep_paths or [])}
    before = geotiff_cache_report(raw_dir)
    removed: list[dict[str, Any]] = []
    now = time.time()

    for part in sorted(raw_dir.iterdir()):
        if not part.is_file() or not part.name.lower().endswith((".tif.part", ".tiff.part")):
            continue
        if part.resolve() in keep:
            continue
        target = Path(str(part)[:-5])
        age = now - part.stat().st_mtime
        if target.exists() or age >= stale_part_seconds:
            size = part.stat().st_size
            part.unlink()
            removed.append({"file": str(part), "size_bytes": size, "reason": "stale_partial"})

    if max_bytes is not None:
        total = geotiff_cache_report(raw_dir)["total_bytes"]
        completed = [
            p
            for p in raw_dir.iterdir()
            if p.is_file() and p.suffix.lower() in {".tif", ".tiff"} and p.resolve() not in keep
        ]
        completed.sort(key=lambda path: path.stat().st_mtime)

        for path in completed:
            if total <= max_bytes:
                break
            size = path.stat().st_size
            path.unlink()
            total -= size
            removed.append({"file": str(path), "size_bytes": size, "reason": "cache_limit"})

    return {
        "max_bytes": max_bytes,
        "before": before,
        "after": geotiff_cache_report(raw_dir),
        "removed": removed,
    }


def download_tif(
    sheet: OrthoSheet, raw_dir: Path, session: requests.Session, timeout: float, progress=None
) -> TifDownloadResult:
    if not sheet.download_url:
        raise ValueError(f"Rok {sheet.year}: brak url_do_pobrania.")
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = local_tif_path(sheet, raw_dir)
    if out_path.exists() and out_path.stat().st_size > 0:
        try:
            validate_tif_file(out_path)
        except ValueError:
            out_path.unlink(missing_ok=True)
        else:
            os.utime(out_path, None)
            size = out_path.stat().st_size
            return TifDownloadResult(out_path, "hit", 0, size, size)

    if out_path.exists() and out_path.stat().st_size <= 0:
        out_path.unlink(missing_ok=True)

    part_path = partial_tif_path(out_path)
    restarted = False
    final_cache = "downloaded"
    final_resume_from = 0
    final_written = 0
    final_total = 0
    for _attempt in range(2):
        resume_from = part_path.stat().st_size if part_path.exists() else 0
        headers = {"Range": f"bytes={resume_from}-"} if resume_from > 0 else None
        with session.get(sheet.download_url, headers=headers, stream=True, timeout=timeout) as resp:
            if resume_from > 0 and resp.status_code == 416:
                part_path.unlink(missing_ok=True)
                restarted = True
                continue
            resp.raise_for_status()

            resume_supported = resume_from > 0 and resp.status_code == 206
            mode = "ab" if resume_supported else "wb"
            written = resume_from if resume_supported else 0
            response_bytes = int(resp.headers.get("Content-Length") or 0)
            total = written + response_bytes if resume_supported and response_bytes else response_bytes
            final_cache = (
                "resumed" if resume_supported else "restarted" if restarted or resume_from > 0 else "downloaded"
            )
            final_resume_from = resume_from if resume_supported else 0

            with part_path.open(mode) as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        final_written = written
                        final_total = max(total, written)
                        if progress:
                            progress(
                                written,
                                total,
                                resume_from=resume_from,
                                resumed=resume_supported,
                                restarted=final_cache == "restarted",
                            )
            break

    try:
        validate_tif_file(part_path)
    except ValueError:
        part_path.unlink(missing_ok=True)
        raise
    part_path.replace(out_path)
    os.utime(out_path, None)
    size = out_path.stat().st_size
    return TifDownloadResult(out_path, final_cache, final_resume_from, final_written or size, final_total or size)


def metadata_bounds_lonlat(md: dict[str, Any]) -> tuple[float, float, float, float]:
    bb = md["bbox_4326"]
    return float(bb["min_lon"]), float(bb["min_lat"]), float(bb["max_lon"]), float(bb["max_lat"])


def crop_geotiff_to_png(tif_path: Path, png_path: Path, md: dict[str, Any]) -> dict[str, Any]:
    rasterio, Resampling, transform_bounds, from_bounds = require_rasterio()
    min_lon, min_lat, max_lon, max_lat = metadata_bounds_lonlat(md)
    target_width = int(md["image_width_px"])
    target_height = int(md["image_height_px"])
    with rasterio.open(tif_path) as src:
        if src.crs is None:
            raise ValueError(f"{tif_path.name}: GeoTIFF nie ma CRS.")
        src_bounds = transform_bounds("EPSG:4326", src.crs, min_lon, min_lat, max_lon, max_lat, densify_pts=21)
        window = from_bounds(*src_bounds, transform=src.transform)
        window = window.round_offsets().round_lengths()
        data = src.read(
            window=window,
            out_shape=(src.count, target_height, target_width),
            resampling=Resampling.bilinear,
        )
        if data.size == 0 or data.shape[1] == 0 or data.shape[2] == 0:
            raise ValueError(f"{tif_path.name}: crop jest pusty.")
        if data.shape[0] < 3:
            raise ValueError(f"{tif_path.name}: oczekiwano co najmniej 3 kanalow RGB, jest {data.shape[0]}.")

        rgb = np.stack([data[0], data[1], data[2]], axis=-1)
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        png_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(png_path), bgr):
            raise OSError(f"Nie udalo sie zapisac {png_path}.")

        return {
            "source_crs": str(src.crs),
            "source_width_px": src.width,
            "source_height_px": src.height,
            "native_crop_width_px": int(window.width),
            "native_crop_height_px": int(window.height),
            "output_width_px": int(rgb.shape[1]),
            "output_height_px": int(rgb.shape[0]),
            "source_bounds": [float(v) for v in src.bounds],
            "crop_bounds_source_crs": [float(v) for v in src_bounds],
        }


def image_quality_for_path(path: Path) -> dict[str, Any] | None:
    img = cv2.imread(str(path))
    if img is None:
        return None
    q = image_quality(img)
    return {
        "width_px": int(img.shape[1]),
        "height_px": int(img.shape[0]),
        "mean": round(q["mean"], 3),
        "std": round(q["std"], 3),
        "sharpness": round(q["sharpness"], 3),
    }


def write_spike_metadata(out_dir: Path, md: dict[str, Any], saved_years: list[int]) -> None:
    metadata = {
        "center_lat": md["center_lat"],
        "center_lon": md["center_lon"],
        "width_meters": md["width_meters"],
        "height_meters": md["height_meters"],
        "image_width_px": md["image_width_px"],
        "image_height_px": md["image_height_px"],
        "bbox_4326": md["bbox_4326"],
        "years": saved_years,
        "source": "geoportal_wfs_geotiff_spike",
    }
    with (out_dir / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)


def write_reports(out_dir: Path, report: dict[str, Any]) -> None:
    with (out_dir / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    lines = [
        "# WFS GeoTIFF Spike",
        "",
        f"- bbox: `{report['bbox_4326']}`",
        f"- output: `{out_dir}`",
        "",
        "| year | status | pixel_m | color | akt_data | file_mb | WFS sharpness | WMS sharpness | notes |",
        "| --- | --- | ---: | --- | --- | ---: | ---: | ---: | --- |",
    ]
    for year in report["years"]:
        selected = year.get("selected_sheet") or {}
        wfs_q = (year.get("quality") or {}).get("wfs_geotiff") or {}
        wms_q = (year.get("quality") or {}).get("wms_current") or {}
        notes = year.get("message") or ""
        lines.append(
            "| {year} | {status} | {pixel} | {color} | {date} | {size} | {wfs} | {wms} | {notes} |".format(
                year=year["year"],
                status=year["status"],
                pixel=selected.get("pixel_m", ""),
                color=selected.get("color", ""),
                date=selected.get("acquisition_date", ""),
                size=selected.get("file_size_mb", ""),
                wfs=wfs_q.get("sharpness", ""),
                wms=wms_q.get("sharpness", ""),
                notes=notes.replace("|", "/"),
            )
        )
    (out_dir / "quality_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    configure_process_encoding()
    args = parse_args()
    md = load_metadata(args.data)
    bbox = wfs_bbox_from_metadata(md)
    out_dir = args.out
    raw_dir = out_dir / "raw_geotiff"
    out_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    report: dict[str, Any] = {
        "source": "geoportal_wfs_geotiff_spike",
        "wfs_url": WFS_URL,
        "bbox_4326": bbox,
        "metadata_source": str(args.data / "metadata.json"),
        "years": [],
    }
    saved_years: list[int] = []

    for year in args.years:
        print(f"🔎 WFS {year}: szukam arkuszy RGB dla bboxa...")
        year_report: dict[str, Any] = {"year": year}
        try:
            sheets = query_wfs_sheets(year, bbox, session, args.timeout)
        except Exception as exc:
            year_report["status"] = "wfs_error"
            year_report["message"] = str(exc)
            report["years"].append(year_report)
            print(f"   ❌ {year}: blad WFS: {exc}")
            continue
        year_report["sheets"] = [asdict(sheet) for sheet in sheets]
        selected = choose_rgb_sheet(sheets)
        if selected is None:
            colors = sorted({(sheet.color or "brak").upper() for sheet in sheets})
            year_report["status"] = "no_rgb_sheet"
            year_report["message"] = (
                f"Brak wypelnionego arkusza RGB z url_do_pobrania. Dostepne kolory: {', '.join(colors) if colors else 'brak'}."
            )
            report["years"].append(year_report)
            print(f"   ⏭  {year}: {year_report['message']}")
            continue

        year_report["selected_sheet"] = asdict(selected)
        png_path = out_dir / f"ortofoto_{year}.png"
        tif_path = local_tif_path(selected, raw_dir)
        try:
            if args.list_only:
                year_report["status"] = "selected"
                year_report["message"] = "Tryb list-only: GeoTIFF nie zostal pobrany."
                print(f"   ✅ {year}: wybrano {selected.godlo} RGB {selected.pixel_m}m, {selected.acquisition_date}")
            else:
                print(f"   ⬇️  {year}: pobieram {selected.download_url}")
                tif_download = download_tif(selected, raw_dir, session, args.timeout)
                tif_path = tif_download.path
                crop_info = crop_geotiff_to_png(tif_path, png_path, md)
                wfs_quality = image_quality_for_path(png_path)
                wms_quality = image_quality_for_path(args.data / f"ortofoto_{year}.png")
                year_report["status"] = "ok"
                year_report["cache"] = tif_download.cache
                year_report["downloaded_tif"] = str(tif_path)
                year_report["output_png"] = str(png_path)
                year_report["crop"] = crop_info
                year_report["download"] = {
                    "resume_from": tif_download.resume_from,
                    "bytes_done": tif_download.bytes_done,
                    "bytes_total": tif_download.bytes_total,
                }
                year_report["quality"] = {
                    "wfs_geotiff": wfs_quality,
                    "wms_current": wms_quality,
                }
                saved_years.append(year)
                print(
                    f"   ✅ {year}: PNG {wfs_quality['width_px']}x{wfs_quality['height_px']} sharpness={wfs_quality['sharpness']}"
                )
        except Exception as exc:
            year_report["status"] = "error"
            year_report["message"] = str(exc)
            print(f"   ❌ {year}: {exc}")
        report["years"].append(year_report)

    if saved_years:
        write_spike_metadata(out_dir, md, saved_years)
    write_reports(out_dir, report)
    print(f"\n📄 Raport: {out_dir / 'quality_report.md'}")
    if saved_years:
        print(f"✅ Gotowe. PNG-i kompatybilne z analyze.py sa w {out_dir}")
    elif args.list_only:
        print("✅ Gotowe. Tryb list-only zapisal wybor arkuszy bez pobierania GeoTIFF.")
    else:
        print("⚠️  Nie zapisano zadnych PNG z GeoTIFF.")


if __name__ == "__main__":
    main()
