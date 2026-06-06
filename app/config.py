from __future__ import annotations

import os
import secrets
from pathlib import Path

from core.config import BYTES_PER_GIB, DATA_DIR, WRECKS_DIR
from core.config import OUTPUT_DIR as ANALYSIS_DIR

ROOT_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT_DIR / "web"
ANALYZE_SCRIPT = ROOT_DIR / "analyze.py"

DOWNLOAD_DATA_DIR = DATA_DIR
DOWNLOAD_DATA_DIR_NAME = DOWNLOAD_DATA_DIR.as_posix()
ANALYSIS_DIR_NAME = ANALYSIS_DIR.as_posix()
WRECKS_ROUTE = WRECKS_DIR.as_posix()

# Lokalny port aplikacji. Zmiana wymaga aktualizacji instrukcji uruchomienia
# albo reverse proxy, ale nie zmienia samego API HTTP.
PORT = 8000

WMS_UPSTREAM_BASE = "https://gis1.um.wroc.pl/arcgis/services/ogc"
WMS_YEARS = [2020, 2021, 2022, 2023, 2024, 2025]
WMS_TILE_SIZE_M = 50
MAX_LAYER_DOWNLOAD_WORKERS = min(6, len(WMS_YEARS))
TILE_DOWNLOAD_RETRIES = 2
TILE_DOWNLOAD_RETRY_BACKOFF_SECONDS = 0.25
WMS_TIMEOUT = (5, 30)
WMS_STITCH_MAX_DIM_PX = 25_000

# Próg std pikseli odróżniający realną ortofoto od pustej odpowiedzi WMS.
# Puste kafle są prawie jednolite; podniesienie progu oznacza agresywniejsze
# odrzucanie roczników, obniżenie może przepuścić białe/szare obrazy.
BLANK_IMAGE_STD_THRESHOLD = 10.0

# Cache tile'i po wspólnym filtrze ortofoto. Większy limit przyspiesza mapę,
# ale rośnie zużycie dysku pod `.cache/wms_tiles`.
WMS_TILE_CACHE_DIR = ROOT_DIR / ".cache" / "wms_tiles"
WMS_TILE_CACHE_MAX_BYTES = int(float(os.environ.get("WRECKSCANNER_WMS_TILE_CACHE_GB", "60")) * BYTES_PER_GIB)
WMS_TILE_CACHE_CLEANUP_INTERVAL_SECONDS = 60
WMS_TILE_CACHE_CONTROL = "public, max-age=86400"

CADASTRAL_WMS_URL = "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
CADASTRAL_WMS_FALLBACK_URL = "https://integracja01.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow"
CADASTRAL_WMS_TIMEOUT = (10, 30)

PHOTO_RETENTION_AUTORUN_ENABLED = os.environ.get("WRECKSCANNER_PHOTO_RETENTION_AUTORUN", "1").strip() not in {
    "0",
    "false",
    "False",
    "no",
}
PHOTO_RETENTION_STARTUP_DELAY_SECONDS = 5
PHOTO_RETENTION_INTERVAL_SECONDS = 24 * 60 * 60

# GeoTIFF WFS zastępuje wybrane roczniki WMS lepszą ortofotą, jeżeli arkusz RGB
# ma wystarczająco mały piksel. Niższy max_pixel_m wymaga ostrzejszych danych.
WFS_GEOTIFF_YEARS = [2024, 2025]
WFS_GEOTIFF_CACHE_DIR = DOWNLOAD_DATA_DIR / "wfs_geotiff_cache" / "raw_geotiff"
WFS_GEOTIFF_TIMEOUT = 60.0
WFS_GEOTIFF_MAX_PIXEL_M = 0.10
WFS_GEOTIFF_PART_TTL_SECONDS = 24 * 60 * 60
WROCLAW_ESTIMATE_BBOX_4326 = (50.96, 16.78, 51.23, 17.20)

ADMIN_PASSWORD_FILE = ROOT_DIR / ".admin_password"
ADMIN_COOKIE_NAME = "wreckscanner_admin"
ADMIN_SESSION_SECONDS = 12 * 60 * 60
ADMIN_SESSION_CLOCK_SKEW_SECONDS = 60
ADMIN_SESSION_SECRET = os.environ.get("WRECKSCANNER_ADMIN_SESSION_SECRET") or secrets.token_urlsafe(32)
ADMIN_COOKIE_SECURE = os.environ.get("WRECKSCANNER_ADMIN_COOKIE_SECURE", "1").strip().lower() not in {
    "0",
    "false",
    "no",
}
CORS_ALLOWED_ORIGINS = tuple(
    origin.strip()
    for origin in os.environ.get("WRECKSCANNER_CORS_ALLOWED_ORIGINS", "https://wreckscanner.pl").split(",")
    if origin.strip()
)

# Pojedynczy slot analizy chroni Raspberry Pi przed równoległym YOLO i dużymi
# pobraniami. Zwiększenie max scan size szybko zwiększa pamięć i czas analizy.
MIN_SCAN_SIZE_M = 1.0
MAX_SCAN_SIZE_M = 50.0
PIPELINE_TTL_SECONDS = 30 * 60
MAX_LOAD_PER_CPU = 2.5
MIN_AVAILABLE_MEMORY_MB = 450

INSPECT_CROP_PX = 300
INSPECT_JPEG_QUALITY = 95

ANALYZE_TIMEOUT_SECONDS = 20 * 60
ANALYZE_STDOUT_TAIL_CHARS = 4000
ANALYZE_STDERR_TAIL_CHARS = 2000
ANALYZE_MAX_CANDIDATES = 500
