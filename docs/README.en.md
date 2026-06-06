# WreckScanner

<div align="center">

### 🌐 [🇵🇱 Polski](../README.md) &nbsp;·&nbsp; [**🇬🇧 English**](README.en.md)

</div>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

WreckScanner helps document **unused or lingering vehicles in public space**. It compares Wrocław orthophotos from 2020-2025, presents candidates for manual verification, and manages vehicle cases with field photos, reports, and privacy controls.

The app output is supporting material for verification, not a legal determination of vehicle status.

Video demo: [youtube.com/watch?v=LxChEHNJ2Jg](https://www.youtube.com/watch?v=LxChEHNJ2Jg)

---

## Table of contents

- [Quick start](#quick-start)
- [What is in v1](#what-is-in-v1)
- [How the score works](#how-the-score-works)
- [Candidate verification](#candidate-verification)
- [Map and layers](#map-and-layers)
- [Administrator panel](#administrator-panel)
- [Vehicle cases](#vehicle-cases)
- [Field photos](#field-photos)
- [Privacy and reports](#privacy-and-reports)
- [Diagnostics](#diagnostics)
- [Backup](#backup)
- [CLI](#cli)
- [Local checks](#local-checks)
- [Requirements](#requirements)
- [Data sources](#data-sources)
- [Local artifacts](#local-artifacts)
- [Roadmap](#roadmap)
- [License](#license)

---

## Quick start

```bash
pip install -r requirements.txt
python3 server.py
```

Open [http://localhost:8000](http://localhost:8000), pick a location on the map, and click **Scan area**. Download + analysis takes 1–3 minutes.

> The YOLO model must be available at `weights/yolo11s-obb.pt` or `weights/yolo11m-obb.pt` — switchable in settings.
> The first GeoTIFF use for a given sheet may download a large source file into cache. Later scans of that sheet reuse the local cache.

## What is in v1

- YOLO OBB scanning of a small map area and comparison of detections on 2020-2025 orthophotos.
- Interactive Leaflet map with Wrocław base imagery, Geoportal `STND` preview, scan crosshair, and result pins.
- Vehicle cases for manually verified places, with reports, verification links, and ZIP/PDF package generation.
- Administrator-managed field photos with approval queue, permanent redaction, and public EXIF-free copies.
- Public mode uses only approved anonymized photos. Originals and working data are available only to administrators.
- KIEG/EGiB cadastral parcel overlay and point identify popup.
- `/privacy` and `/report` pages for processing information and removal, correction, or anonymization requests.

## How the score works

For every vehicle tracked across multiple time periods:

| Component | Weight | What it measures |
|---|---:|---|
| **Temporal coverage** (visibility-adjusted) | 50% | How many yearly captures show the car in the same spot. A year covered by tree canopy doesn't count as "missing". |
| **Color consistency** (HSV) | 25% | Whether it's *the same* car — not different vehicles in the same parking slot. |
| **Mean YOLO confidence** | 15% | How sure the detector was. |
| **Temporal span** | 10% | Bonus when the car is visible from the first capture to the last. |

**Visibility** is computed from ExG (2G−R−B) around the vehicle. If the car is 50% under tree leaves, missing detections aren't treated as evidence of absence.

## Candidate verification

Each candidate in the report comes with **6 one-click links**:

- 🚶 **Google Street View** — street-level view, license plate, body damage
- 🛰️ **Google Maps** / **Apple Maps** — current satellite views
- 📸 **Mapillary** — historical street-level imagery with dates
- 🇵🇱 **Geoportal Krajowy** — Poland-wide archive
- 📄 **Full report** — yearly thumbnails, metrics, and score

## Map and layers

The main map shows the current orthophoto base layer, scan crosshair, and marker layers. The bottom slider changes only the visible Leaflet imagery: Wrocław years `2020`-`2025` plus the Polish Geoportal `STND` preview. Changing this preview source does not change YOLO scanning, downloaded analysis years, or generated reports.

Available view layers:

- vehicle cases,
- field photos of vehicles,
- infrastructure photos,
- smoke exposure photos,
- KIEG/EGiB parcel boundaries and parcel numbers.

The map context menu can set the scan center, copy a shareable place link, show or hide the crosshair, and identify the cadastral parcel at the clicked point. The parcel popup shows number, identifier, district, municipality, county, voivodeship, area, and land-use type when KIEG returns it.

## Administrator panel

The administrator panel groups tools that no longer fit into the settings modal:

- adding field photos,
- photo review and anonymization queue,
- privacy request queue,
- public layer visibility for signed-out users,
- private original photo retention.

The settings panel still controls:

- **YOLO model** — `yolo11s-obb.pt` for speed or `yolo11m-obb.pt` for accuracy.
- **Detection sensitivity** — lower threshold gives more candidates, higher threshold gives a shorter list.
- **Report thumbnail zoom** — 5 m, 7.5 m, 10 m, 15 m, or 20 m. This affects evidence thumbnails, not the downloaded orthophoto scale.
- **Orthophoto filter** — the shared enhancement filter used in the map preview and before YOLO. Settings are stored in `settings.json`.
- **GeoTIFF cache limit** — `4 GB` by default, with a no-limit option.

The **Defaults** button restores baseline filter parameters.

## Vehicle cases

A vehicle case is a manually saved report for verification, not an automatic algorithmic decision.

- Saving from the map or report creates `zidentyfikowane_wraki/<wreck_id>/`.
- Each folder contains `record.json`, a local `index.html`, and evidence packages with yearly thumbnails, a candidate snapshot, and analysis metadata.
- After server restart, `GET /api/wrecks` loads saved records and draws their pins on the main map.
- A compact map legend can show and hide vehicle-case pins and field-photo pins independently.
- Saving another candidate within a few meters updates the existing record instead of creating a duplicate.
- A vehicle-case pin has a delete action, which removes the local evidence folder and refreshes the map layer.
- When the model misses a vehicle, clicking inside the downloaded area opens **Manual location inspection** with historical thumbnails. The save action creates a manual `zidentyfikowane_wraki/<wreck_id>/` folder at the clicked coordinates and marks it as `manual_inspection`.
- The report generator creates a ZIP with a separate `zgloszenie.txt` file and `raport.html`, which includes a **Treść zgłoszenia** section with the recipient, subject, and email draft. A companion `PDF` with the summary, photos, and report text is generated next to the ZIP. The public vehicle-case `index.html` does not store reporter details.
- The administrator can add on-site photos directly to a vehicle case from its pin or from that case's public `index.html`. Photos have a private original and a public copy without EXIF. Only approved anonymized public copies are publicly visible.

The `zidentyfikowane_wraki/` directory is ignored by git.

## Field photos

Field photos are handled as a separate map layer. After logging in, the administrator can add and delete photos; uploads are independent from vehicle cases and do not mix with YOLO analysis results.

- JPG, PNG, and WebP are accepted, up to `10 MB` per photo and `25` photos per upload batch.
- The backend stores the private original outside the public route and keeps the photo record in `zdjecia_terenowe/<photo_id>/`.
- Every photo starts with `pending` review status. The public copy `public.jpg` and thumbnail `public_thumb.jpg` are published only after administrator approval.
- Public copies are always stored without EXIF and may contain permanently burned-in redactions, such as masked license plates or identifiers.
- The public API returns only `public_image` and `public_thumb`; it does not return private original paths or URLs.
- The app reads GPS from EXIF. If a photo has no GPS, it stores the current map point as fallback and marks the coordinate source in metadata.
- In the upload modal, the administrator can enable **Ignore EXIF GPS and use the map point** when phone GPS is less accurate than the manually selected map center.
- The administrator can move field-photo pins manually. Moving a grouped pin moves every photo in that group and stores `manual` as the coordinate source.
- The public field-photo layer shows only approved anonymized copies. Originals are available only through administrator endpoints.

The `zdjecia_terenowe/` directory is a local backend store and should not be committed.

## Privacy and reports

The app separates private originals from public copies:

- every photo has a private original and a public copy,
- the public copy is always stored without EXIF,
- the public copy is not published until `public_review_status` is `approved`,
- redactions are burned into file pixels, not rendered as an HTML/CSS overlay,
- public thumbnails are generated from the already anonymized public copy,
- the public API never returns `original_path`, `original_url`, or private file paths.

In the photo queue, the administrator can draw, move, resize, and rotate redaction rectangles. After saving, the backend generates a new public copy and thumbnail. Original retention can replace an old private original with an anonymized version.

Public reports and photo downloads use approved public copies and photos uploaded by the user for that specific report. The administrator flow may use working data, but public endpoints remain limited to anonymized resources.

The `/privacy` page describes processing purpose, data scope, retention, recipients, and individual rights. The `/report` page stores removal, correction, or anonymization requests in the administrator queue.

## Diagnostics

Each analysis writes `analiza/run_log.json`. This is the primary file for debugging result quality.

It includes:

- model, sensitivity, thumbnail zoom, and orthophoto filter settings,
- image dimensions, px/m scale, `imgsz`, `eps_px`, and analysis duration,
- per-year image quality: `sharpness`, `mean`, `std`,
- WFS/GeoTIFF details: year, pixel size, RGB/CIR, acquisition date, cache hit/download, file size,
- per-year detection counts and top candidates with observations.

The HTML report links to **diagnostics JSON**, and `/api/analyze` returns `diagnostics_url`.

## CLI

```bash
python3 analyze.py                              # defaults
python3 analyze.py --conf 0.18 --eps 2.5        # more sensitive
python3 analyze.py --conf 0.35 --eps 1.5        # strict, hard cases only
python3 analyze.py --fast                       # faster, one scale for the latest image
python3 analyze.py --crop-m 7.5                 # report thumbnail zoom
```

- `--conf` — YOLO confidence threshold (history pass; the current frame uses a more sensitive multi-scale pass)
- `--eps` — "same location" tolerance in **meters** (1.5–3.0)
- `--model` — `weights/yolo11s-obb.pt` (default) or `weights/yolo11m-obb.pt` (slower, more accurate)
- `--fast` — skips multi-scale detection on the latest image; faster, but may find fewer candidates
- `--crop-m` — evidence thumbnail size in the report, default 7.5 m
- `--no-enhance` — disables the shared orthophoto filter before YOLO

## Local checks

Before committing, run the basic local checks:

```bash
pip install -r requirements-dev.txt
scripts/check.sh
```

The script prefers `.venv/bin/python` when available, compiles Python modules, runs Ruff lint/format, runs unit tests, and checks whitespace with `git diff --check`.

## Requirements

- Python 3.10+
- ~3 GB disk space (PyTorch + YOLO models)
- GPU optional (~10× speedup), CPU works just fine

## Data sources

| Source | Years | Frequency |
|---|---|---|
| **Wrocław Geoportal** (primary) | 2020–2025 | 1 flight / year |
| **Polish Geoportal** | 2010–present | 1–3 flights / year |
| **Mapillary** | 2014–present | crowdsourced |
| **Street View / Apple Maps** | varies | manual verification |

**WMS endpoints:**
- `https://gis1.um.wroc.pl/arcgis/services/ogc/OGC_ortofoto_{year}/MapServer/WMSServer`
- `https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution`
- `https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/Archiwalne`

`StandardResolution` is used only as a public preview source in the bottom slider. `HighResolution` is not listed separately because it duplicated the Wrocław 2024 base imagery for the tested area. Geoportal TrueOrtho is not listed because, in the current Leaflet/EPSG:3857 map setup, it returned blank tiles for the tested points.

**WFS → GeoTIFF cache:**

The **Scan area** button automatically checks the Polish Geoportal WFS index for 2024/2025. If the sheet is RGB and has better source resolution (`<= 0.10 m/pixel`), the app downloads the raw GeoTIFF once into cache:

- `dane_dla_AI/wfs_geotiff_cache/raw_geotiff/`

Further scans over the same sheet do not download it again; they crop the requested area locally. Large sheets can be around 1 GB, so the first download may take a while.

Set the GeoTIFF cache limit in **Settings → GeoTIFF cache**. The default is `4 GB`. When the limit is exceeded, the app deletes the oldest complete TIFF sheets while keeping the sheet currently in use. Interrupted `.part` downloads are resumed on the next scan.

The manual diagnostic spike is still available:

```bash
python3 scripts/download_geoportal_wfs_geotiff.py --list-only
python3 scripts/download_geoportal_wfs_geotiff.py --years 2025
```

## Local artifacts

These directories/files are local and ignored by git:

- `dane_dla_AI/` — downloaded orthophotos, area metadata, and GeoTIFF cache.
- `analiza/` — report, thumbnails, overlay, `candidates.json`, `run_log.json`.
- `zidentyfikowane_wraki/` — manually saved vehicle cases.
- `zdjecia_terenowe/` — local field photo records and anonymized public copies.
- `prywatne_zdjecia/` — private photo originals used by administrator-only flows.
- `prywatne_zgloszenia/` — private report packages and working report artifacts.
- `zgloszenia_prywatnosci/` — privacy request queue.
- `settings.json` — local orthophoto filter settings.
- `.cache/` — Matplotlib cache and other local helper files.
- `.backups/` — local encrypted restic backup repository. See [BACKUP.md](BACKUP.md).

## Roadmap

Current ideas are tracked in [todo.md](todo.md). The nearest larger directions:

- more precise terrain classification in the parcel popup, such as road, sidewalk, parking, greenery, and surface material,
- gradual modularization of the large frontend files,
- further refinement of marker display settings and the map legend.

## License

[MIT](../LICENSE) — use, modify, and distribute freely, just keep the copyright notice.

Orthophoto data comes from Wrocław municipality and the Polish Geoportal — check their terms of use separately.
