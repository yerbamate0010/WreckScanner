import errno
import hashlib
import hmac
import http.server
import json
import logging
import math
import os
import secrets
import subprocess  # nosec B404
import sys
import threading
import time
from datetime import datetime, timezone
from http import cookies
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import cv2
import numpy as np

from app import config, map_downloads, pipeline, wms_cache
from core import config as core_config
from core.cadastral import cadastral_feature_info_params, parse_cadastral_feature_info
from core.enhancement import enhance_orthophoto
from core.field_photos import (
    delete_field_photo,
    field_photo_asset,
    list_field_photo_review_items,
    list_field_photos,
    review_field_photo,
    save_field_photo,
    update_field_photo_location,
)
from core.map_crops import save_scan_crops, validate_crop_m
from core.photo_retention import retire_private_originals
from core.privacy_requests import create_privacy_request, list_privacy_requests, update_privacy_request
from core.report_packages import (
    ReportPhotoUpload,
    create_public_report_package,
    create_report_package,
    public_report_package_asset,
    report_package_asset,
)
from core.runtime import configure_process_encoding, subprocess_text_kwargs
from core.settings_store import (
    DEFAULT_PUBLIC_FEATURES,
    DEFAULT_PUBLIC_LAYERS,
    default_app_settings,
    load_app_settings,
    load_enhancement_settings,
    save_app_settings,
)
from core.submission_limits import assert_pending_submission_quota, pending_submission_usage
from core.surface import parse_bbox as parse_surface_bbox
from core.surface import surface_features_geojson
from core.uploads import UploadedFile, parse_multipart_form
from core.wrecks import (
    attach_field_photos_to_wreck,
    attach_wreck_photos,
    attach_wreck_photos_for_submission,
    delete_wreck,
    delete_wreck_photo,
    list_wreck_photo_review_items,
    list_wreck_review_items,
    list_wrecks,
    public_wreck_asset,
    refresh_wreck_report,
    review_wreck,
    review_wreck_photo,
    save_manual_wreck,
    save_wreck_from_rank,
    wreck_is_public,
    wreck_photo_original_asset,
)

configure_process_encoding()

logger = logging.getLogger("wreckscanner.server")

_REQUEST_ID_SAFE_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")

_download_progress_lock = threading.Lock()
_download_progress = {
    "status": "idle",
    "stage": None,
    "message": "",
    "percent": None,
    "updated_at": None,
}
_photo_retention_run_lock = threading.Lock()
_photo_retention_state_lock = threading.Lock()
_photo_retention_scheduler_started = False
_photo_retention_state = {
    "running": False,
    "last_started_at": None,
    "last_finished_at": None,
    "last_source": None,
    "last_report": None,
    "last_error": None,
}

_FIELD_PHOTO_PUBLIC_LAYER_KEYS = {
    "vehicle": "field_photo_vehicle",
    "infrastructure": "field_photo_infrastructure",
    "smoke": "field_photo_smoke",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ReusableHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP server — pozwala obsługiwać równolegle żądania tile'ów
    z WMS proxy bez blokowania głównego API."""

    allow_reuse_address = True
    daemon_threads = True


def _set_download_progress(**payload) -> None:
    with _download_progress_lock:
        _download_progress.clear()
        _download_progress.update(
            {
                "status": "active",
                "stage": None,
                "message": "",
                "percent": None,
                "updated_at": time.time(),
            }
        )
        _download_progress.update(payload)


def _get_download_progress() -> dict:
    with _download_progress_lock:
        return dict(_download_progress)


def _photo_retention_snapshot() -> dict:
    with _photo_retention_state_lock:
        return dict(_photo_retention_state)


def _run_photo_retention(*, dry_run: bool, source: str) -> dict:
    if not _photo_retention_run_lock.acquire(blocking=False):
        raise RuntimeError("Retencja zdjęć już działa.")
    try:
        with _photo_retention_state_lock:
            _photo_retention_state.update(
                {
                    "running": True,
                    "last_started_at": _now_iso(),
                    "last_source": source,
                    "last_error": None,
                }
            )
        report = retire_private_originals(
            field_photos_dir=core_config.FIELD_PHOTOS_DIR,
            wrecks_dir=core_config.WRECKS_DIR,
            private_photos_dir=core_config.PRIVATE_PHOTOS_DIR,
            dry_run=dry_run,
        )
        with _photo_retention_state_lock:
            _photo_retention_state.update(
                {
                    "running": False,
                    "last_finished_at": _now_iso(),
                    "last_report": report,
                }
            )
        return report
    except Exception as exc:
        with _photo_retention_state_lock:
            _photo_retention_state.update(
                {
                    "running": False,
                    "last_finished_at": _now_iso(),
                    "last_error": str(exc),
                }
            )
        raise
    finally:
        _photo_retention_run_lock.release()


def start_photo_retention_scheduler(
    *,
    initial_delay_seconds: float = config.PHOTO_RETENTION_STARTUP_DELAY_SECONDS,
    interval_seconds: float = config.PHOTO_RETENTION_INTERVAL_SECONDS,
) -> bool:
    global _photo_retention_scheduler_started
    if not config.PHOTO_RETENTION_AUTORUN_ENABLED or _photo_retention_scheduler_started:
        return False
    _photo_retention_scheduler_started = True

    def worker() -> None:
        time.sleep(max(0.0, initial_delay_seconds))
        while True:
            try:
                _run_photo_retention(dry_run=False, source="scheduler")
            except Exception as exc:
                logger.exception("Photo retention scheduler failed: %s", exc)
            time.sleep(max(1.0, interval_seconds))

    thread = threading.Thread(target=worker, name="photo-retention", daemon=True)
    thread.start()
    return True


def _admin_password() -> str | None:
    password = os.environ.get("WRECKSCANNER_ADMIN_PASSWORD", "").strip()
    if password:
        return password
    try:
        password = config.ADMIN_PASSWORD_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return password or None


def _admin_enabled() -> bool:
    return _admin_password() is not None


def _admin_signature(payload: str, password: str) -> str:
    key = f"{config.ADMIN_SESSION_SECRET}:{password}".encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def _make_admin_token(password: str) -> str:
    issued_at = str(int(time.time()))
    nonce = secrets.token_urlsafe(16)
    payload = f"{issued_at}:{nonce}"
    return f"{payload}:{_admin_signature(payload, password)}"


def _valid_admin_token(token: str | None) -> bool:
    password = _admin_password()
    if not password or not token:
        return False
    parts = token.split(":")
    if len(parts) != 3:
        return False
    issued_at, nonce, signature = parts
    try:
        issued = int(issued_at)
    except ValueError:
        return False
    now = int(time.time())
    if issued > now + config.ADMIN_SESSION_CLOCK_SKEW_SECONDS or now - issued > config.ADMIN_SESSION_SECONDS:
        return False
    payload = f"{issued_at}:{nonce}"
    expected = _admin_signature(payload, password)
    return hmac.compare_digest(signature, expected)


def _versioned_route_url(route_path: str, version: str) -> str:
    separator = "&" if "?" in route_path else "?"
    return f"{route_path}{separator}v={version}"


def _file_asset_version(path: Path) -> str:
    try:
        return str(path.stat().st_mtime_ns)
    except OSError:
        return str(time.time_ns())


def _cors_response_headers(origin: str | None) -> dict[str, str]:
    origin_text = str(origin or "").strip()
    if not origin_text or origin_text not in config.CORS_ALLOWED_ORIGINS:
        return {}
    return {
        "Access-Control-Allow-Origin": origin_text,
        "Access-Control-Allow-Methods": "GET, POST, PATCH, DELETE, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type, X-Request-ID",
        "Access-Control-Expose-Headers": "X-Request-ID",
        "Vary": "Origin",
    }


class Handler(http.server.SimpleHTTPRequestHandler):
    """Serves static files and handles the download API."""

    def log_message(self, format: str, *args) -> None:
        request_path = self.path.split("?", 1)[0]
        if request_path.startswith("/wms_proxy/") or request_path == "/api/download/progress":
            return
        super().log_message(format, *args)

    def _write_body(self, body: bytes) -> bool:
        try:
            self.wfile.write(body)
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _request_id(self) -> str:
        cached_request_id = getattr(self, "_cached_request_id", None)
        if cached_request_id:
            return cached_request_id

        raw_request_id = str(self.headers.get("X-Request-ID", "")).strip()
        request_id = "".join(
            char for char in raw_request_id[:80] if char in _REQUEST_ID_SAFE_CHARS
        )
        if not request_id:
            request_id = secrets.token_hex(8)
        self._cached_request_id = request_id
        return request_id

    def _log_exception(
        self,
        message: str,
        exc: BaseException,
        *,
        status: int | None = None,
        level: int = logging.ERROR,
    ) -> None:
        request_path = urlsplit(getattr(self, "path", "")).path
        client_address = getattr(self, "client_address", ("-",))
        client_host = client_address[0] if client_address else "-"
        logger.log(
            level,
            "%s request_id=%s method=%s path=%s status=%s client=%s error=%s",
            message,
            self._request_id(),
            getattr(self, "command", "-"),
            request_path,
            status if status is not None else "-",
            client_host,
            exc,
            exc_info=True,
        )

    def _send_json(
        self,
        status: int,
        payload: dict,
        extra_headers: dict[str, str] | None = None,
        *,
        include_body: bool = True,
    ) -> None:
        request_id = self._request_id()
        response_payload = payload
        if "error" in payload:
            response_payload = {
                **payload,
                "request_id": str(payload.get("request_id") or request_id),
            }
            request_id = response_payload["request_id"]
        body = json.dumps(response_payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Request-ID", request_id)
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if include_body:
            self._write_body(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        data = json.loads(body.decode("utf-8")) if body.strip() else {}
        if not isinstance(data, dict):
            raise ValueError("Payload musi być obiektem JSON.")
        return data

    def _read_multipart_form(self, max_body_bytes: int) -> tuple[dict[str, str], list[UploadedFile]]:
        content_type = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            raise ValueError("Brak danych formularza.")
        if length > max_body_bytes:
            raise ValueError("Formularz przekracza limit rozmiaru pakietu.")
        body = self.rfile.read(length)
        return parse_multipart_form(content_type, body, max_body_bytes=max_body_bytes)

    def _report_package_wreck_id(self, request_path: str) -> str | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "wrecks" and parts[3] == "report-package":
            return parts[2]
        return None

    def _public_report_package_wreck_id(self, request_path: str) -> str | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "wrecks" and parts[3] == "public-report-package":
            return parts[2]
        return None

    def _wreck_photo_upload_wreck_id(self, request_path: str) -> str | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "wrecks" and parts[3] == "photos":
            return parts[2]
        return None

    def _wreck_field_photo_attach_wreck_id(self, request_path: str) -> str | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "wrecks"
            and parts[3] == "field-photos"
            and parts[4] == "attach"
        ):
            return parts[2]
        return None

    def _wreck_index_wreck_id(self, request_path: str) -> str | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) == 3 and parts[0] == config.WRECKS_ROUTE and parts[2] == "index.html":
            return parts[1]
        return None

    def _field_photo_asset_route(self, request_path: str) -> tuple[str, str] | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) == 4 and parts[0] == "api" and parts[1] == "field-photos" and parts[3] in {
            "public-image",
            "public-thumb",
        }:
            return parts[2], parts[3]
        return None

    def _admin_photo_original_route(self, request_path: str) -> tuple[str, tuple[str, ...]] | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) >= 5 and parts[0] == "api" and parts[1] == "admin" and parts[2] == "photos":
            if parts[3] == "field" and len(parts) == 6 and parts[5] == "original":
                return "field", (parts[4],)
            if parts[3] == "wreck" and len(parts) == 7 and parts[6] == "original":
                return "wreck", (parts[4], parts[5])
        return None

    def _admin_photo_review_route(self, request_path: str) -> tuple[str, tuple[str, ...]] | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) >= 5 and parts[0] == "api" and parts[1] == "admin" and parts[2] == "photos":
            if parts[3] == "field" and len(parts) == 6 and parts[5] == "review":
                return "field", (parts[4],)
            if parts[3] == "wreck" and len(parts) == 7 and parts[6] == "review":
                return "wreck", (parts[4], parts[5])
        return None

    def _admin_photo_delete_route(self, request_path: str) -> tuple[str, tuple[str, ...]] | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) >= 5 and parts[0] == "api" and parts[1] == "admin" and parts[2] == "photos":
            if parts[3] == "field" and len(parts) == 5:
                return "field", (parts[4],)
            if parts[3] == "wreck" and len(parts) == 6:
                return "wreck", (parts[4], parts[5])
        return None

    def _admin_wreck_review_route(self, request_path: str) -> str | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "admin"
            and parts[2] == "wrecks"
            and parts[4] == "review"
        ):
            return parts[3]
        return None

    def _report_package_asset_route(self, request_path: str) -> tuple[str, str, str] | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) == 5 and parts[0] == "api" and parts[1] == "report-packages" and parts[4] in {"zip", "pdf"}:
            return parts[2], parts[3], parts[4]
        return None

    def _public_report_package_asset_route(self, request_path: str) -> tuple[str, str, str] | None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if (
            len(parts) == 5
            and parts[0] == "api"
            and parts[1] == "public-report-packages"
            and parts[4] in {"zip", "pdf"}
        ):
            return parts[2], parts[3], parts[4]
        return None

    def _admin_token_from_cookie(self) -> str | None:
        raw_cookie = self.headers.get("Cookie", "")
        if not raw_cookie:
            return None
        jar = cookies.SimpleCookie()
        try:
            jar.load(raw_cookie)
        except cookies.CookieError:
            return None
        morsel = jar.get(config.ADMIN_COOKIE_NAME)
        return morsel.value if morsel else None

    def _is_admin(self) -> bool:
        return _valid_admin_token(self._admin_token_from_cookie())

    def _submission_owner(self) -> str:
        ip = str((self.client_address or ["unknown"])[0])
        ua = str(self.headers.get("User-Agent") or "")
        digest = hashlib.sha256(f"{ip}|{ua}".encode()).hexdigest()[:24]
        return f"public:{digest}"

    def _ensure_public_submission_quota(self, *, additional_bytes: int = 0, additional_items: int = 1) -> None:
        if self._is_admin():
            return
        assert_pending_submission_quota(
            owner=self._submission_owner(),
            additional_bytes=additional_bytes,
            additional_items=additional_items,
            wrecks_dir=core_config.WRECKS_DIR,
            field_photos_dir=core_config.FIELD_PHOTOS_DIR,
            private_dir=core_config.PRIVATE_PHOTOS_DIR,
        )

    def _is_local_request_host(self) -> bool:
        raw_host = self.headers.get("Host", "").strip().lower()
        if raw_host.startswith("[") and "]" in raw_host:
            host = raw_host.split("]", 1)[0].strip("[]")
        else:
            host = raw_host.split(":", 1)[0]
        return host in {"localhost", "127.0.0.1", "::1"}

    def _admin_cookie_header(self, value: str, *, max_age: int) -> str:
        attrs = [
            f"{config.ADMIN_COOKIE_NAME}={value}",
            "HttpOnly",
            "SameSite=Lax",
            "Path=/",
            f"Max-Age={max_age}",
        ]
        if config.ADMIN_COOKIE_SECURE and not self._is_local_request_host():
            attrs.insert(2, "Secure")
        return "; ".join(attrs)

    def _require_admin(self) -> bool:
        if self._is_admin():
            return True
        if not _admin_enabled():
            self._send_json(
                503,
                {
                    "error": "Panel administratora nie ma ustawionego hasla. Ustaw WRECKSCANNER_ADMIN_PASSWORD albo plik .admin_password."
                },
            )
            return False
        self._send_json(401, {"error": "Wymagane logowanie administratora."})
        return False

    def _public_layer_settings(self) -> dict[str, bool]:
        raw = load_app_settings().get("public_layers", {})
        if not isinstance(raw, dict):
            return DEFAULT_PUBLIC_LAYERS.copy()
        settings = DEFAULT_PUBLIC_LAYERS.copy()
        for key in settings:
            settings[key] = bool(raw.get(key, settings[key]))
        return settings

    def _public_layer_allowed(self, key: str) -> bool:
        return self._is_admin() or self._public_layer_settings().get(key, True)

    def _public_feature_settings(self) -> dict[str, bool]:
        raw = load_app_settings().get("public_features", {})
        if not isinstance(raw, dict):
            return DEFAULT_PUBLIC_FEATURES.copy()
        settings = DEFAULT_PUBLIC_FEATURES.copy()
        for key in settings:
            settings[key] = bool(raw.get(key, settings[key]))
        return settings

    def _public_feature_allowed(self, key: str) -> bool:
        return self._is_admin() or self._public_feature_settings().get(key, True)

    def _require_public_feature(self, key: str, message: str) -> bool:
        if self._public_feature_allowed(key):
            return True
        self._send_json(403, {"error": message})
        return False

    def _public_field_photo_allowed(self, photo: dict) -> bool:
        if self._is_admin():
            return True
        layer_settings = self._public_layer_settings()
        if str(photo.get("public_review_status") or "approved") == "pending" and not layer_settings.get(
            "field_photo_pending", True
        ):
            return False
        issue_type = str(photo.get("issue_type") or "vehicle")
        key = _FIELD_PHOTO_PUBLIC_LAYER_KEYS.get(issue_type, "field_photo_vehicle")
        return layer_settings.get(key, True)

    def _handle_admin_status(self) -> None:
        pending_usage = pending_submission_usage(
            owner=None,
            wrecks_dir=core_config.WRECKS_DIR,
            field_photos_dir=core_config.FIELD_PHOTOS_DIR,
            private_dir=core_config.PRIVATE_PHOTOS_DIR,
        )
        self._send_json(
            200,
            {
                "status": "ok",
                "admin_enabled": _admin_enabled(),
                "authenticated": self._is_admin(),
                "pending_submissions": pending_usage,
            },
        )

    def _handle_admin_login(self) -> None:
        password = _admin_password()
        if not password:
            self._send_json(
                503,
                {"error": "Brak hasla administratora. Ustaw WRECKSCANNER_ADMIN_PASSWORD albo plik .admin_password."},
            )
            return
        try:
            data = self._read_json_body()
        except Exception as exc:
            self._send_json(400, {"error": str(exc)})
            return
        candidate = str(data.get("password", ""))
        if not hmac.compare_digest(candidate, password):
            self._send_json(401, {"error": "Nieprawidlowe haslo administratora."})
            return
        token = _make_admin_token(password)
        cookie = self._admin_cookie_header(token, max_age=config.ADMIN_SESSION_SECONDS)
        self._send_json(
            200,
            {"status": "ok", "authenticated": True},
            {"Set-Cookie": cookie},
        )

    def _handle_admin_logout(self) -> None:
        cookie = self._admin_cookie_header("", max_age=0)
        self._send_json(200, {"status": "ok", "authenticated": False}, {"Set-Cookie": cookie})

    def _handle_health(self) -> None:
        pressure = pipeline.system_pressure()
        status = "degraded" if pressure["overloaded"] else "ok"
        self._send_json(
            200,
            {
                "status": status,
                "pressure": pressure,
                "pipeline": pipeline.pipeline_snapshot(),
                "wms_tile_cache": wms_cache.tile_cache_report(),
            },
        )

    def _send_file(
        self,
        path: Path,
        content_type: str,
        *,
        cache_control: str = "no-store",
        include_body: bool = True,
    ) -> None:
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise FileNotFoundError("Nie znaleziono pliku.") from exc
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if include_body:
            self._write_body(body)

    def _send_web_file(self, file_name: str, *, include_body: bool = True) -> None:
        path = config.WEB_DIR / file_name
        self._send_file(path, "text/html; charset=utf-8", include_body=include_body)

    def _handle_admin_photos(self) -> None:
        if not self._require_admin():
            return
        query = parse_qs(urlsplit(self.path).query)
        status_filter = (query.get("status") or ["all"])[0]
        scope_filter = (query.get("scope") or ["all"])[0]
        issue_filter = (query.get("issue_type") or ["all"])[0]
        search = str((query.get("q") or [""])[0]).strip().lower()
        exact_photo_ids = {
            item.strip().lower() for raw in query.get("ids", []) for item in str(raw).split(",") if item.strip()
        }
        photos = list_field_photo_review_items(core_config.FIELD_PHOTOS_DIR) + list_wreck_photo_review_items(
            core_config.WRECKS_DIR
        )
        if status_filter in {"pending", "approved", "rejected"}:
            photos = [photo for photo in photos if photo.get("public_review_status") == status_filter]
        if scope_filter in {"field", "wreck"}:
            photos = [photo for photo in photos if photo.get("scope") == scope_filter]
        if issue_filter != "all":
            photos = [photo for photo in photos if photo.get("issue_type") == issue_filter]
        if exact_photo_ids:
            photos = [
                photo
                for photo in photos
                if str(photo.get("photo_id") or "").lower() in exact_photo_ids
                or str(photo.get("id") or "").lower() in exact_photo_ids
            ]
        if search:
            photos = [
                photo
                for photo in photos
                if search
                in " ".join(
                    str(photo.get(key) or "")
                    for key in ("id", "photo_id", "wreck_id", "original_filename", "issue_type")
                ).lower()
            ]
        self._send_json(200, {"status": "ok", "photos": photos})

    def _handle_admin_wrecks(self) -> None:
        if not self._require_admin():
            return
        query = parse_qs(urlsplit(self.path).query)
        status_filter = (query.get("status") or ["pending"])[0]
        try:
            wrecks = list_wreck_review_items(core_config.WRECKS_DIR, status=status_filter)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return
        self._send_json(200, {"status": "ok", "wrecks": wrecks})

    def _handle_admin_geotiff_cache(self) -> None:
        if not self._require_admin():
            return
        include_estimate = (parse_qs(urlsplit(self.path).query).get("estimate") or ["1"])[0] != "0"
        try:
            self._send_json(200, map_downloads.geotiff_admin_cache_report(include_estimate=include_estimate))
        except Exception as exc:
            self._log_exception("Failed to build GeoTIFF cache report", exc, status=500)
            self._send_json(500, {"status": "error", "error": str(exc)})

    def _handle_public_wreck_asset(self, request_path: str, *, include_body: bool = True) -> None:
        parts = [part for part in request_path.strip("/").split("/") if part]
        if len(parts) < 3 or parts[0] != config.WRECKS_ROUTE:
            self._send_json(
                404,
                {"error": "Nie znaleziono publicznego pliku sprawy pojazdu."},
                include_body=include_body,
            )
            return
        wreck_id = parts[1]
        relative_path = "/".join(parts[2:])
        if not self._is_admin() and not wreck_is_public(wreck_id, core_config.WRECKS_DIR):
            self._send_json(
                404,
                {"error": "Nie znaleziono publicznej sprawy pojazdu."},
                include_body=include_body,
            )
            return
        try:
            file_path, content_type = public_wreck_asset(wreck_id, relative_path, core_config.WRECKS_DIR)
            self._send_file(file_path, content_type, cache_control="public, max-age=300", include_body=include_body)
        except FileNotFoundError as e:
            self._send_json(404, {"error": str(e)}, include_body=include_body)
        except Exception as e:
            self._send_json(400, {"error": str(e)}, include_body=include_body)

    def translate_path(self, path: str) -> str:
        request_path = unquote(urlsplit(path).path)
        if request_path == "/":
            return str(config.WEB_DIR / "index.html")

        if request_path.startswith(f"/{config.ANALYSIS_DIR_NAME}/") or request_path.startswith(
            f"/{config.WRECKS_ROUTE}/"
        ):
            base_dir = config.ROOT_DIR
            relative_path = request_path.lstrip("/")
        else:
            base_dir = config.WEB_DIR
            relative_path = request_path.lstrip("/")

        parts = [part for part in relative_path.split("/") if part and part not in {".", ".."}]
        return str(base_dir.joinpath(*parts))

    def end_headers(self):
        for key, value in _cors_response_headers(self.headers.get("Origin")).items():
            self.send_header(key, value)
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_HEAD(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/privacy":
            self._send_web_file("privacy.html", include_body=False)
            return
        if path == "/report":
            self._send_web_file("report.html", include_body=False)
            return
        wreck_index_wreck_id = self._wreck_index_wreck_id(path)
        if wreck_index_wreck_id:
            if not self._is_admin() and not wreck_is_public(wreck_index_wreck_id, core_config.WRECKS_DIR):
                self._send_json(404, {"error": "Nie znaleziono publicznej sprawy pojazdu."}, include_body=False)
                return
            try:
                index_path = refresh_wreck_report(wreck_index_wreck_id, core_config.WRECKS_DIR)
                self._send_file(index_path, "text/html; charset=utf-8", include_body=False)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)}, include_body=False)
            except Exception as e:
                self._send_json(400, {"error": str(e)}, include_body=False)
            return
        if path.startswith(f"/{config.WRECKS_ROUTE}/"):
            self._handle_public_wreck_asset(path, include_body=False)
            return
        super().do_HEAD()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/privacy":
            self._send_web_file("privacy.html")
            return
        if path == "/report":
            self._send_web_file("report.html")
            return
        wreck_index_wreck_id = self._wreck_index_wreck_id(path)
        if wreck_index_wreck_id:
            if not self._is_admin() and not wreck_is_public(wreck_index_wreck_id, core_config.WRECKS_DIR):
                self._send_json(404, {"error": "Nie znaleziono publicznej sprawy pojazdu."})
                return
            try:
                index_path = refresh_wreck_report(wreck_index_wreck_id, core_config.WRECKS_DIR)
                self._send_file(index_path, "text/html; charset=utf-8")
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return
        if path == "/api/health":
            self._handle_health()
            return
        if path == "/api/admin/status":
            self._handle_admin_status()
            return
        if path == "/api/admin/photos":
            self._handle_admin_photos()
            return
        if path == "/api/admin/wrecks":
            self._handle_admin_wrecks()
            return
        if path == "/api/admin/geotiff-cache":
            self._handle_admin_geotiff_cache()
            return
        if path == "/api/admin/privacy-requests":
            if not self._require_admin():
                return
            query = parse_qs(urlsplit(self.path).query)
            status_filter = query.get("status", ["all"])[0]
            try:
                requests = list_privacy_requests(core_config.PRIVACY_REQUESTS_DIR, status=status_filter)
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
                return
            self._send_json(200, {"status": "ok", "requests": requests})
            return
        if path == "/api/admin/photo-retention":
            if not self._require_admin():
                return
            self._send_json(200, {"status": "ok", "retention": _photo_retention_snapshot()})
            return
        if path == "/api/settings":
            self._handle_get_settings()
            return
        if path == "/api/download/progress":
            self._handle_download_progress()
            return
        if path == "/api/cadastral/identify":
            self._handle_cadastral_identify()
            return
        if path == "/api/surface/features":
            query = parse_qs(urlsplit(self.path).query)
            try:
                bbox = parse_surface_bbox((query.get("bbox") or [""])[0])
                self._send_json(200, {"status": "ok", "geojson": surface_features_geojson(bbox)})
            except ValueError as e:
                self._send_json(400, {"status": "error", "error": str(e)})
            except Exception as e:
                self._send_json(
                    502, {"status": "error", "error": str(e), "geojson": {"type": "FeatureCollection", "features": []}}
                )
            return
        if path == "/api/wrecks":
            self._handle_get_wrecks()
            return
        if path == "/api/field-photos":
            photos = list_field_photos(core_config.FIELD_PHOTOS_DIR)
            if not self._is_admin():
                photos = [photo for photo in photos if self._public_field_photo_allowed(photo)]
            self._send_json(200, {"status": "ok", "photos": photos})
            return
        report_package_route = self._report_package_asset_route(path)
        if report_package_route:
            if not self._require_admin():
                return
            wreck_id, package_id, asset = report_package_route
            try:
                file_path, content_type = report_package_asset(wreck_id, package_id, asset)
                self._send_file(file_path, content_type)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return
        public_report_package_route = self._public_report_package_asset_route(path)
        if public_report_package_route:
            wreck_id, package_id, asset = public_report_package_route
            query = parse_qs(urlsplit(self.path).query)
            token = (query.get("token") or [""])[0]
            try:
                file_path, content_type = public_report_package_asset(wreck_id, package_id, asset, token)
                self._send_file(file_path, content_type)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return
        admin_photo_original_route = self._admin_photo_original_route(path)
        if admin_photo_original_route:
            if not self._require_admin():
                return
            scope, ids = admin_photo_original_route
            try:
                if scope == "field":
                    file_path, content_type = field_photo_asset(
                        ids[0], core_config.FIELD_PHOTOS_DIR, "original", private_dir=core_config.PRIVATE_PHOTOS_DIR
                    )
                else:
                    file_path, content_type = wreck_photo_original_asset(ids[0], ids[1], core_config.WRECKS_DIR)
                self._send_file(file_path, content_type)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return
        field_photo_asset_route = self._field_photo_asset_route(path)
        if field_photo_asset_route:
            photo_id, asset = field_photo_asset_route
            try:
                file_path, content_type = field_photo_asset(
                    photo_id,
                    core_config.FIELD_PHOTOS_DIR,
                    asset,  # type: ignore[arg-type]
                    private_dir=core_config.PRIVATE_PHOTOS_DIR,
                )
                self._send_file(file_path, content_type, cache_control="public, max-age=300")
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return
        if self.path.startswith("/wms_proxy/"):
            self._handle_wms_proxy()
            return
        if path.startswith(f"/{config.WRECKS_ROUTE}/"):
            self._handle_public_wreck_asset(path)
            return
        super().do_GET()

    def do_DELETE(self):
        request_path = unquote(urlsplit(self.path).path)
        admin_photo_delete_route = self._admin_photo_delete_route(request_path)
        if admin_photo_delete_route:
            if not self._require_admin():
                return
            try:
                scope, ids = admin_photo_delete_route
                if scope == "field":
                    result = delete_field_photo(
                        ids[0], core_config.FIELD_PHOTOS_DIR, private_dir=core_config.PRIVATE_PHOTOS_DIR
                    )
                else:
                    result = delete_wreck_photo(ids[0], ids[1], core_config.WRECKS_DIR)
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if request_path.startswith("/api/admin/geotiff-cache/"):
            if not self._require_admin():
                return
            file_name = request_path.removeprefix("/api/admin/geotiff-cache/").strip("/")
            if not file_name or "/" in file_name:
                self._send_json(400, {"error": "Nieprawidłowa nazwa pliku GeoTIFF."})
                return
            try:
                self._send_json(200, map_downloads.delete_geotiff_cache_file(file_name))
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        if request_path.startswith("/api/field-photos/"):
            if not self._require_admin():
                return
            photo_id = request_path.removeprefix("/api/field-photos/").strip("/")
            if not photo_id or "/" in photo_id:
                self._send_json(400, {"error": "Nieprawidłowy identyfikator zdjęcia."})
                return
            try:
                result = delete_field_photo(
                    photo_id, core_config.FIELD_PHOTOS_DIR, private_dir=core_config.PRIVATE_PHOTOS_DIR
                )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if request_path.startswith("/api/wrecks/"):
            if not self._require_admin():
                return
            wreck_id = request_path.removeprefix("/api/wrecks/").strip("/")
            if not wreck_id or "/" in wreck_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Nieprawidłowy identyfikator sprawy pojazdu."}).encode())
                return
            try:
                result = delete_wreck(wreck_id, core_config.WRECKS_DIR)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(result).encode())
            except FileNotFoundError as e:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())
            return

        self.send_response(404)
        self.end_headers()

    def do_PATCH(self):
        request_path = unquote(urlsplit(self.path).path)
        parts = [part for part in request_path.strip("/").split("/") if part]
        admin_wreck_review_route = self._admin_wreck_review_route(request_path)
        if admin_wreck_review_route:
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                result = review_wreck(
                    admin_wreck_review_route,
                    core_config.WRECKS_DIR,
                    status=data.get("public_review_status"),
                )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        admin_photo_review_route = self._admin_photo_review_route(request_path)
        if admin_photo_review_route:
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                scope, ids = admin_photo_review_route
                if scope == "field":
                    result = review_field_photo(
                        ids[0],
                        core_config.FIELD_PHOTOS_DIR,
                        status=data.get("public_review_status"),
                        redactions=data.get("redactions") or [],
                        private_dir=core_config.PRIVATE_PHOTOS_DIR,
                    )
                else:
                    result = review_wreck_photo(
                        ids[0],
                        ids[1],
                        core_config.WRECKS_DIR,
                        status=data.get("public_review_status"),
                        redactions=data.get("redactions") or [],
                    )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "field-photos" and parts[3] == "location":
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                result = update_field_photo_location(
                    parts[2],
                    core_config.FIELD_PHOTOS_DIR,
                    lat=data.get("lat"),
                    lon=data.get("lon"),
                    private_dir=core_config.PRIVATE_PHOTOS_DIR,
                )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        if len(parts) == 4 and parts[0] == "api" and parts[1] == "admin" and parts[2] == "privacy-requests":
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                result = update_privacy_request(parts[3], data, core_config.PRIVACY_REQUESTS_DIR)
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return

        self.send_response(404)
        self.end_headers()

    def _handle_get_settings(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        payload = load_app_settings()
        payload["defaults"] = default_app_settings()
        self.wfile.write(json.dumps(payload).encode())

    def _handle_download_progress(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(_get_download_progress()).encode())

    def _handle_cadastral_identify(self) -> None:
        query = parse_qs(urlsplit(self.path).query)
        try:
            lat = float((query.get("lat") or [""])[0])
            lon = float((query.get("lon") or [""])[0])
        except ValueError:
            self._send_json(400, {"status": "error", "error": "Nieprawidłowe współrzędne."})
            return
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            self._send_json(400, {"status": "error", "error": "Współrzędne poza zakresem."})
            return

        params = cadastral_feature_info_params(lat, lon)
        last_error: Exception | None = None
        response = None
        for upstream_url in (config.CADASTRAL_WMS_URL, config.CADASTRAL_WMS_FALLBACK_URL):
            try:
                response = map_downloads.get_http_session().get(
                    upstream_url,
                    params=params,
                    timeout=config.CADASTRAL_WMS_TIMEOUT,
                )
                response.raise_for_status()
                break
            except Exception as exc:
                last_error = exc
                response = None
        if response is None:
            self._send_json(502, {"status": "error", "error": f"Nie udało się pobrać danych działki: {last_error}"})
            return

        response.encoding = "utf-8"
        parcel = parse_cadastral_feature_info(response.text)
        if not parcel.get("parcel_id") and not parcel.get("parcel_number"):
            self._send_json(404, {"status": "not_found", "error": "Nie znaleziono działki w tym punkcie."})
            return
        self._send_json(200, {"status": "ok", "parcel": parcel})

    def _handle_get_wrecks(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        wrecks = (
            list_wrecks(core_config.WRECKS_DIR, include_pending=self._is_admin())
            if self._public_layer_allowed("saved_wrecks")
            else []
        )
        self.wfile.write(json.dumps({"status": "ok", "wrecks": wrecks}).encode())

    def _handle_wms_proxy(self) -> None:
        """Pobierz tile z UM Wrocław WMS, przepuść przez enhance_orthophoto,
        zwróć jako PNG. URL pattern: /wms_proxy/OGC_ortofoto_{year}/MapServer/WMSServer?<wms-query>"""
        upstream_path = self.path[len("/wms_proxy/") :]
        if not upstream_path or ".." in upstream_path:
            self.send_error(400, "Invalid wms_proxy path")
            return

        stripped_upstream_path = wms_cache.strip_proxy_only_params(upstream_path)
        enhancement_fingerprint = wms_cache.enhancement_fingerprint()
        cache_key = wms_cache.tile_cache_key(stripped_upstream_path, enhancement_fingerprint)
        cache_path = wms_cache.tile_cache_path(cache_key)
        cached_bytes = wms_cache.read_tile_cache(cache_path)
        if cached_bytes is not None:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(cached_bytes)))
            self.send_header("Cache-Control", config.WMS_TILE_CACHE_CONTROL)
            self.send_header("X-WMS-Cache", "HIT")
            self.end_headers()
            self._write_body(cached_bytes)
            return

        upstream_url = f"{config.WMS_UPSTREAM_BASE}/{stripped_upstream_path}"
        try:
            session = map_downloads.get_http_session()
            resp = session.get(upstream_url, timeout=config.WMS_TIMEOUT)
            resp.raise_for_status()
            raw_bytes = resp.content
        except Exception as exc:
            self._log_exception("WMS upstream request failed", exc, status=502)
            self.send_error(502, f"WMS upstream error: {exc}")
            return

        # Dekoduj PNG → BGR. Jeśli się nie uda — passthrough oryginalnego payloadu.
        nparr = np.frombuffer(raw_bytes, dtype=np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw_bytes)))
            self.send_header("Cache-Control", config.WMS_TILE_CACHE_CONTROL)
            self.send_header("X-WMS-Cache", "MISS")
            self.end_headers()
            wms_cache.write_tile_cache(cache_path, raw_bytes)
            wms_cache.cleanup_tile_cache()
            self._write_body(raw_bytes)
            return

        try:
            enhanced = enhance_orthophoto(img, settings=load_enhancement_settings())
        except Exception as exc:
            # Fail open — wolimy oryginalny tile niż błąd 500 łamiący mapę
            self._log_exception(
                "WMS enhancement failed; returning raw tile",
                exc,
                status=200,
                level=logging.WARNING,
            )
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(raw_bytes)))
            self.send_header("Cache-Control", config.WMS_TILE_CACHE_CONTROL)
            self.send_header("X-WMS-Cache", "MISS")
            self.end_headers()
            wms_cache.write_tile_cache(cache_path, raw_bytes)
            wms_cache.cleanup_tile_cache()
            self._write_body(raw_bytes)
            return

        success, encoded = cv2.imencode(".png", enhanced, [cv2.IMWRITE_PNG_COMPRESSION, 3])
        if not success:
            self.send_error(500, "PNG encoding failed")
            return

        out_bytes = encoded.tobytes()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(out_bytes)))
        self.send_header("Cache-Control", config.WMS_TILE_CACHE_CONTROL)
        self.send_header("X-WMS-Cache", "MISS")
        self.end_headers()
        wms_cache.write_tile_cache(cache_path, out_bytes)
        wms_cache.cleanup_tile_cache()
        self._write_body(out_bytes)

    def do_POST(self):
        request_path = unquote(urlsplit(self.path).path)
        if request_path == "/api/admin/login":
            self._handle_admin_login()
            return
        if request_path == "/api/admin/logout":
            self._handle_admin_logout()
            return
        if request_path == "/api/privacy-requests":
            try:
                data = self._read_json_body()
                result = create_privacy_request(data, core_config.PRIVACY_REQUESTS_DIR)
                self._send_json(200, result)
            except Exception as e:
                self._send_json(400, {"error": str(e)})
            return
        if request_path == "/api/admin/photo-retention/run":
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                dry_run = bool(data.get("dry_run", True))
                report = _run_photo_retention(dry_run=dry_run, source="admin")
                self._send_json(200, {"status": "ok", "report": report, "retention": _photo_retention_snapshot()})
            except RuntimeError as e:
                self._send_json(409, {"error": str(e)})
            except Exception as e:
                self._log_exception("Manual photo retention run failed", e, status=500)
                self._send_json(500, {"error": str(e), "retention": _photo_retention_snapshot()})
            return
        public_report_package_wreck_id = self._public_report_package_wreck_id(request_path)
        if public_report_package_wreck_id:
            try:
                fields, files = self._read_multipart_form(core_config.MAX_REPORT_PACKAGE_BODY_BYTES)
                photos = [
                    ReportPhotoUpload(
                        field_name=file.field_name,
                        filename=file.filename,
                        content_type=file.content_type,
                        data=file.data,
                    )
                    for file in files
                    if file.field_name in {"photos", "photos[]"}
                ]
                if photos and not self._require_public_feature(
                    "photo_uploads", "Dodawanie zdjec przez niezalogowanych jest teraz wylaczone."
                ):
                    return
                result = create_public_report_package(
                    public_report_package_wreck_id, fields, photos, core_config.WRECKS_DIR
                )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        report_package_wreck_id = self._report_package_wreck_id(request_path)
        if report_package_wreck_id:
            if not self._require_admin():
                return
            try:
                fields, files = self._read_multipart_form(core_config.MAX_REPORT_PACKAGE_BODY_BYTES)
                photos = [
                    ReportPhotoUpload(
                        field_name=file.field_name,
                        filename=file.filename,
                        content_type=file.content_type,
                        data=file.data,
                    )
                    for file in files
                    if file.field_name in {"photos", "photos[]"}
                ]
                result = create_report_package(report_package_wreck_id, fields, photos, core_config.WRECKS_DIR)
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        wreck_field_photo_attach_wreck_id = self._wreck_field_photo_attach_wreck_id(request_path)
        if wreck_field_photo_attach_wreck_id:
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                photo_ids = data.get("photo_ids") if isinstance(data.get("photo_ids"), list) else []
                result = attach_field_photos_to_wreck(
                    wreck_field_photo_attach_wreck_id,
                    photo_ids,
                    core_config.FIELD_PHOTOS_DIR,
                    core_config.WRECKS_DIR,
                )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        wreck_photo_upload_wreck_id = self._wreck_photo_upload_wreck_id(request_path)
        if wreck_photo_upload_wreck_id:
            if not self._require_public_feature(
                "photo_uploads", "Dodawanie zdjec przez niezalogowanych jest teraz wylaczone."
            ):
                return
            try:
                _, files = self._read_multipart_form(core_config.MAX_WRECK_PHOTO_BODY_BYTES)
                photos = [file for file in files if file.field_name in {"photos", "photos[]", "photo"}]
                if self._is_admin():
                    result = attach_wreck_photos(wreck_photo_upload_wreck_id, photos, core_config.WRECKS_DIR)
                else:
                    additional_bytes = sum(len(file.data) for file in photos)
                    self._ensure_public_submission_quota(
                        additional_bytes=additional_bytes,
                        additional_items=max(1, len(photos)),
                    )
                    result = attach_wreck_photos_for_submission(
                        wreck_photo_upload_wreck_id,
                        photos,
                        core_config.WRECKS_DIR,
                        submission_owner=self._submission_owner(),
                    )
                self._send_json(200, result)
            except FileNotFoundError as e:
                self._send_json(404, {"error": str(e)})
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        if request_path == "/api/field-photos":
            if not self._require_public_feature(
                "photo_uploads", "Dodawanie zdjec przez niezalogowanych jest teraz wylaczone."
            ):
                return
            try:
                fields, files = self._read_multipart_form(core_config.FIELD_PHOTO_MAX_BODY_BYTES)
                photo_files = [file for file in files if file.field_name == "photo"]
                if len(photo_files) != 1:
                    raise ValueError("Dodaj dokładnie jedno zdjęcie w polu 'photo'.")
                if not self._is_admin():
                    self._ensure_public_submission_quota(
                        additional_bytes=len(photo_files[0].data),
                        additional_items=1,
                    )
                ignore_exif_gps = str(fields.get("ignore_exif_gps") or "").strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                }
                result = save_field_photo(
                    photo_files[0],
                    core_config.FIELD_PHOTOS_DIR,
                    fallback_lat=fields.get("fallback_lat"),
                    fallback_lon=fields.get("fallback_lon"),
                    ignore_exif_gps=ignore_exif_gps,
                    issue_type=fields.get("issue_type"),
                    private_dir=core_config.PRIVATE_PHOTOS_DIR,
                    submission_owner=None if self._is_admin() else self._submission_owner(),
                )
                self._send_json(200, result)
            except ValueError as e:
                self._send_json(400, {"error": str(e)})
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return
        if request_path == "/api/settings":
            if not self._require_admin():
                return
            try:
                data = self._read_json_body()
                settings = save_app_settings(data)
                self._send_json(200, settings)
            except Exception as e:
                self._send_json(400, {"error": str(e)})
        elif request_path == "/api/download":
            if not self._require_public_feature(
                "scan_analysis", "Skanowanie i analiza YOLO sa teraz wylaczone dla niezalogowanych."
            ):
                return
            pipeline_token = None
            try:
                pressure = pipeline.system_pressure()
                if pressure["overloaded"]:
                    raise pipeline.HttpJsonError(
                        503, "Raspberry Pi jest teraz przeciazone: " + "; ".join(pressure["reasons"])
                    )
                data = self._read_json_body()
                lat = float(data["lat"])
                lon = float(data["lon"])
                width = float(data.get("width", 50))
                height = float(data.get("height", 50))
                width = height = max(width, height)
                if not math.isfinite(width) or width < config.MIN_SCAN_SIZE_M or width > config.MAX_SCAN_SIZE_M:
                    raise ValueError(f"Obszar analizy musi miec maksymalnie {config.MAX_SCAN_SIZE_M:g} m.")
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("Nieprawidlowe wspolrzedne.")
                pipeline_token = pipeline.start_pipeline(pipeline.client_id(self))

                print(
                    f"📍 Download request: lat={lat}, lon={lon}, area={width}×{height}m, density={core_config.NATIVE_TILE_PX}px/50m"
                )

                def progress(**payload):
                    current = payload.pop("current", None)
                    total = payload.pop("total", None)
                    percent = (
                        pipeline.progress_percent(current, total) if current is not None and total is not None else None
                    )
                    _set_download_progress(
                        status="active",
                        percent=percent,
                        current=current,
                        total=total,
                        **payload,
                    )

                _set_download_progress(
                    status="active", stage="start", message="Przygotowuję pobieranie ortofotomap", percent=0
                )
                results, bbox, wfs_summary = map_downloads.download_maps(lat, lon, width, height, progress=progress)
                wfs_replaced = [r for r in wfs_summary if r.get("status") == "replaced"]
                wfs_cache_hits = sum(1 for r in wfs_replaced if r.get("cache") == "hit")
                wfs_downloaded = sum(
                    1 for r in wfs_replaced if r.get("cache") in {"downloaded", "resumed", "restarted"}
                )
                wfs_skipped = sum(1 for r in wfs_summary if r.get("status") != "replaced")
                _set_download_progress(
                    status="done",
                    stage="done",
                    message="Pobieranie zakończone",
                    percent=100,
                )

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "status": "completed",
                            "saved": sum(1 for v in results.values() if v.get("status") == "ok"),
                            "missing": sum(1 for v in results.values() if v.get("status") == "missing"),
                            "total": len(config.WMS_YEARS),
                            "wfs_replaced": len(wfs_replaced),
                            "wfs_cache_hits": wfs_cache_hits,
                            "wfs_downloaded": wfs_downloaded,
                            "wfs_skipped": wfs_skipped,
                            "job_token": pipeline_token,
                            "bbox": bbox,
                        }
                    ).encode()
                )

            except pipeline.HttpJsonError as e:
                if pipeline_token:
                    pipeline.finish_pipeline(pipeline_token)
                _set_download_progress(status="error", stage="error", message=e.message, percent=None)
                self._send_json(e.status, {"error": e.message})
            except Exception as e:
                if pipeline_token:
                    pipeline.finish_pipeline(pipeline_token)
                _set_download_progress(status="error", stage="error", message=str(e), percent=None)
                self._send_json(400, {"error": str(e)})
        elif request_path == "/api/inspect":
            if not self._require_public_feature(
                "manual_wrecks", "Dodawanie recznych pinezek jest teraz wylaczone dla niezalogowanych."
            ):
                return
            try:
                data = self._read_json_body()
                lat = float(data["lat"])
                lon = float(data["lon"])
                crop_m = validate_crop_m(data.get("cropM", core_config.REVIEW_CROP_M))

                custom_dir = config.ANALYSIS_DIR / "custom_crops"
                ts = int(time.time() * 1000)
                crops, _metadata = save_scan_crops(
                    lat,
                    lon,
                    config.DOWNLOAD_DATA_DIR,
                    custom_dir,
                    crop_m=crop_m,
                    filename_prefix=f"custom_{ts}_",
                    jpeg_quality=config.INSPECT_JPEG_QUALITY,
                )
                self._send_json(
                    200,
                    {
                        "status": "ok",
                        "crops": [
                            {
                                "year": crop["label"],
                                "url": f"/{config.ANALYSIS_DIR_NAME}/custom_crops/{crop['file']}",
                            }
                            for crop in crops
                        ],
                    },
                )

            except Exception as e:
                self._send_json(400, {"error": str(e)})
        elif request_path == "/api/analyze":
            if not self._require_public_feature(
                "scan_analysis", "Skanowanie i analiza YOLO sa teraz wylaczone dla niezalogowanych."
            ):
                return
            pipeline_token = None
            try:
                pressure = pipeline.system_pressure()
                if pressure["overloaded"]:
                    raise pipeline.HttpJsonError(
                        503, "Raspberry Pi jest teraz przeciazone: " + "; ".join(pressure["reasons"])
                    )
                data = self._read_json_body()
                pipeline_token = str(data.get("job_token", "")).strip()
                if not pipeline_token:
                    raise pipeline.HttpJsonError(409, "Brak tokenu zadania. Uruchom skan od poczatku.")
                pipeline.advance_pipeline(pipeline_token, pipeline.client_id(self), "analyze")

                cmd = [sys.executable, str(config.ANALYZE_SCRIPT)]
                model = str(data.get("model", "")).strip()
                if model:
                    cmd.extend(["--model", model])
                device = str(data.get("device", "")).strip()
                if device:
                    if device not in {"auto", "cpu", "mps"}:
                        raise ValueError("Nieprawidłowe device. Dozwolone: auto, cpu, mps.")
                    cmd.extend(["--device", device])
                lang = str(data.get("lang", "")).strip()
                if lang in {"pl", "en"}:
                    cmd.extend(["--lang", lang])
                try:
                    conf = float(data.get("conf", 0))
                    if 0.05 <= conf <= 0.50:
                        cmd.extend(["--conf", str(conf)])
                except (TypeError, ValueError):
                    pass
                if data.get("fast") is True:
                    cmd.append("--fast")
                crop_m = validate_crop_m(data.get("cropM", core_config.REVIEW_CROP_M))
                cmd.extend(["--crop-m", f"{crop_m:g}"])

                print(
                    f"🧠 Uruchamiam analyze.py... model={model or 'domyślny'} device={device or 'auto'} fast={data.get('fast') is True}"
                )
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    **subprocess_text_kwargs(),
                    timeout=config.ANALYZE_TIMEOUT_SECONDS,
                )  # nosec B603
                stdout = proc.stdout[-config.ANALYZE_STDOUT_TAIL_CHARS :]
                stderr = proc.stderr[-config.ANALYZE_STDERR_TAIL_CHARS :]
                candidates = []
                cand_path = config.ANALYSIS_DIR / "candidates.json"
                if cand_path.exists():
                    with cand_path.open(encoding="utf-8") as f:
                        candidates = json.load(f)
                report_version = _file_asset_version(config.ROOT_DIR / config.ANALYSIS_DIR / "report.html")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {
                            "status": "ok" if proc.returncode == 0 else "error",
                            "report_url": _versioned_route_url(
                                f"/{config.ANALYSIS_DIR_NAME}/report.html", report_version
                            ),
                            "diagnostics_url": _versioned_route_url(
                                f"/{config.ANALYSIS_DIR_NAME}/run_log.json", report_version
                            ),
                            "candidates": candidates[: config.ANALYZE_MAX_CANDIDATES],
                            "stdout": stdout,
                            "stderr": stderr,
                        }
                    ).encode()
                )
                pipeline.finish_pipeline(pipeline_token)
            except subprocess.TimeoutExpired:
                pipeline.finish_pipeline(pipeline_token)
                self._send_json(504, {"error": "Analiza trwała zbyt długo (>20 min)."})
            except pipeline.HttpJsonError as e:
                self._send_json(e.status, {"error": e.message})
            except Exception as e:
                pipeline.finish_pipeline(pipeline_token)
                self._log_exception("Analysis pipeline failed", e, status=500)
                self._send_json(500, {"error": str(e)})
        elif request_path == "/api/wrecks":
            try:
                data = self._read_json_body()
                if "rank" in data:
                    if not self._require_public_feature(
                        "yolo_wrecks", "Dodawanie pinezek z YOLO jest teraz wylaczone dla niezalogowanych."
                    ):
                        return
                elif not self._require_public_feature(
                    "manual_wrecks", "Dodawanie recznych pinezek jest teraz wylaczone dla niezalogowanych."
                ):
                    return
                review_status = "approved" if self._is_admin() else "pending"
                submission_owner = None if self._is_admin() else self._submission_owner()
                if not self._is_admin():
                    self._ensure_public_submission_quota(additional_bytes=0, additional_items=1)
                if "rank" in data:
                    rank = int(data.get("rank"))
                    if rank <= 0:
                        raise ValueError("Numer kandydata musi być dodatni.")
                    result = save_wreck_from_rank(
                        rank,
                        config.ANALYSIS_DIR,
                        config.DOWNLOAD_DATA_DIR,
                        core_config.WRECKS_DIR,
                        public_review_status=review_status,
                        submission_owner=submission_owner,
                    )
                else:
                    crop_m = validate_crop_m(data.get("cropM", core_config.REVIEW_CROP_M))
                    result = save_manual_wreck(
                        data.get("lat"),
                        data.get("lon"),
                        config.DOWNLOAD_DATA_DIR,
                        core_config.WRECKS_DIR,
                        crop_m=crop_m,
                        public_review_status=review_status,
                        submission_owner=submission_owner,
                    )
                self._send_json(200, result)
            except Exception as e:
                self._send_json(400, {"error": str(e)})
        else:
            self.send_response(404)
            self.end_headers()


def main() -> None:
    try:
        srv = ReusableHTTPServer(("", config.PORT), Handler)
    except OSError as e:
        if e.errno in (errno.EADDRINUSE, 48):
            print(
                f"❌ Port {config.PORT} jest już zajęty. Zamknij poprzedni serwer albo sprawdź: lsof -nP -iTCP:{config.PORT} -sTCP:LISTEN"
            )
            sys.exit(1)
        if e.errno in (errno.EACCES, errno.EPERM):
            print(f"❌ Nie udało się otworzyć portu {config.PORT}: brak uprawnienia procesu.")
            print("   Wyjdź z obcego virtualenv, np. `deactivate`, i uruchom ponownie: python3 server.py")
            sys.exit(1)
        raise
    print(f"🚀 Serwer działa na http://localhost:{config.PORT}")
    print("   Otwórz tę stronę w przeglądarce.")
    if start_photo_retention_scheduler():
        print("   Retencja prywatnych oryginałów: automatycznie przy starcie i potem co 24h.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Serwer zatrzymany.")


if __name__ == "__main__":
    main()
