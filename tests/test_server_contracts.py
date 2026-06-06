import io
import json
import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import app.server as server
import core.settings_store as settings_store
from app import config as app_config
from app import map_downloads, pipeline, wms_cache
from core import config as core_config
from core.report_packages import report_package_asset


def make_handler(path, payload=None, headers=None, method="GET"):
    body = b""
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")

    handler = server.Handler.__new__(server.Handler)
    handler.path = path
    handler.headers = {"Content-Length": str(len(body))}
    if headers:
        handler.headers.update(headers)
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.client_address = ("127.0.0.1", 12345)
    handler.command = method
    handler.status = None
    handler.response_headers = []
    handler.send_response = lambda code, message=None: setattr(handler, "status", code)
    handler.send_header = lambda key, value: handler.response_headers.append((key, value))
    handler.end_headers = lambda: None
    handler.log_message = lambda *args, **kwargs: None
    return handler


def admin_cookie():
    token = server._make_admin_token("secret")
    return {"Cookie": f"{app_config.ADMIN_COOKIE_NAME}={token}"}


def handler_json(handler):
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def image_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (24, 24), (40, 90, 130)).save(out, "JPEG")
    return out.getvalue()


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class FakeCadastralResponse:
    text = """
    <table>
        <tr><td>Identyfikator działki</td><td>026401_1.0022.AR_27.87</td></tr>
        <tr><td>Numer działki</td><td>87</td></tr>
        <tr><td>Nazwa obrębu</td><td>Południe</td></tr>
    </table>
    """

    def raise_for_status(self):
        return None


class FakeCadastralSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return FakeCadastralResponse()


def create_wreck_fixture(root: Path) -> Path:
    wrecks_dir = root / "zidentyfikowane_wraki"
    record_dir = wrecks_dir / "wreck_51100000_17200000"
    evidence_dir = record_dir / "evidence" / "abc123"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "2025.jpg").write_bytes(image_bytes())
    (record_dir / "index.html").write_text("<html>raport</html>", encoding="utf-8")
    write_json(evidence_dir / "candidate.json", {"rank": 1})
    write_json(evidence_dir / "metadata.json", {"years": [2025]})
    write_json(evidence_dir / "links.json", {"geoportal": "https://example.test/geo"})
    write_json(
        record_dir / "record.json",
        {
            "id": "wreck_51100000_17200000",
            "lat": 51.1,
            "lon": 17.2,
            "best_score": 0.8,
            "labels_present": ["2025"],
            "latest_evidence": {
                "id": "abc123",
                "path": "evidence/abc123",
                "score": 0.8,
                "labels_present": ["2025"],
                "crops": [{"label": "2025", "file": "2025.jpg"}],
                "links": {"geoportal": "https://example.test/geo"},
            },
            "links": {"geoportal": "https://example.test/geo"},
            "evidences": [],
        },
    )
    return wrecks_dir


def multipart_payload(fields, files):
    boundary = "----wreckscanner-test-boundary"
    chunks = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    for field_name, filename, content_type, data in files:
        chunks.append(f"--{boundary}\r\n".encode())
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
        )
        chunks.append(data)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    return f"multipart/form-data; boundary={boundary}", body


def make_multipart_handler(path, fields=None, files=None, headers=None):
    content_type, body = multipart_payload(fields or {}, files or [])
    handler = make_handler(path, headers=headers)
    handler.headers.update({"Content-Type": content_type, "Content-Length": str(len(body))})
    handler.rfile = BytesIO(body)
    return handler


class SettingsApiContractTests(unittest.TestCase):
    def test_post_settings_accepts_no_limit_cache(self):
        with TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            handler = make_handler(
                "/api/settings",
                {"geotiff_cache": {"max_gb": None}},
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(settings_store, "SETTINGS_PATH", settings_path),
            ):
                server.Handler.do_POST(handler)

            self.assertEqual(handler.status, 200)
            self.assertEqual(handler_json(handler)["geotiff_cache"], {"max_gb": None})
            self.assertEqual(
                json.loads(settings_path.read_text(encoding="utf-8"))["geotiff_cache"],
                {"max_gb": None},
            )

    def test_post_settings_accepts_public_layer_visibility(self):
        with TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            handler = make_handler(
                "/api/settings",
                {"public_layers": {"saved_wrecks": False, "field_photo_smoke": False}},
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(settings_store, "SETTINGS_PATH", settings_path),
            ):
                server.Handler.do_POST(handler)

            self.assertEqual(handler.status, 200)
            self.assertEqual(
                handler_json(handler)["public_layers"],
                {
                    "saved_wrecks": False,
                    "field_photo_vehicle": True,
                    "field_photo_infrastructure": True,
                    "field_photo_smoke": False,
                },
            )

    def test_post_settings_rejects_non_object_payload(self):
        handler = make_handler("/api/settings", headers=admin_cookie())
        body = b"[]"
        handler.headers.update({"Content-Length": str(len(body))})
        handler.rfile = BytesIO(body)

        with patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}):
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 400)
        self.assertIn("Payload musi", handler_json(handler)["error"])

    def test_post_settings_requires_admin_session(self):
        handler = make_handler("/api/settings", {"geotiff_cache": {"max_gb": 2}})

        with patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}):
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 401)
        self.assertIn("administratora", handler_json(handler)["error"])


class CadastralApiContractTests(unittest.TestCase):
    def test_cadastral_identify_uses_fixed_upstream_and_returns_parcel_json(self):
        fake_session = FakeCadastralSession()
        handler = make_handler("/api/cadastral/identify?lat=51.089742&lon=17.038940")

        with patch.object(map_downloads, "get_http_session", return_value=fake_session):
            server.Handler.do_GET(handler)

        payload = handler_json(handler)
        self.assertEqual(handler.status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["parcel"]["parcel_number"], "87")
        self.assertEqual(payload["parcel"]["parcel_id"], "026401_1.0022.AR_27.87")
        self.assertEqual(fake_session.calls[0]["url"], app_config.CADASTRAL_WMS_URL)
        self.assertEqual(fake_session.calls[0]["params"]["REQUEST"], "GetFeatureInfo")
        self.assertEqual(fake_session.calls[0]["params"]["QUERY_LAYERS"], "dzialki")

    def test_cadastral_identify_rejects_invalid_coordinates(self):
        handler = make_handler("/api/cadastral/identify?lat=abc&lon=17")

        server.Handler.do_GET(handler)

        self.assertEqual(handler.status, 400)
        self.assertEqual(handler_json(handler)["status"], "error")


class SecurityHeadersContractTests(unittest.TestCase):
    def test_admin_cookie_is_secure_on_public_hosts_but_not_localhost(self):
        public_handler = make_handler(
            "/api/admin/login",
            {"password": "secret"},
            headers={"Host": "wreckscanner.pl"},
        )
        local_handler = make_handler(
            "/api/admin/login",
            {"password": "secret"},
            headers={"Host": "localhost:8000"},
        )

        with patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}):
            server.Handler.do_POST(public_handler)
            server.Handler.do_POST(local_handler)

        public_cookie = dict(public_handler.response_headers)["Set-Cookie"]
        local_cookie = dict(local_handler.response_headers)["Set-Cookie"]
        self.assertIn("HttpOnly", public_cookie)
        self.assertIn("Secure", public_cookie)
        self.assertIn("SameSite=Lax", public_cookie)
        self.assertNotIn("Secure", local_cookie)

    def test_cors_headers_are_origin_whitelisted_without_wildcard(self):
        self.assertEqual(server._cors_response_headers("https://evil.example"), {})
        allowed = server._cors_response_headers("https://wreckscanner.pl")

        self.assertEqual(allowed["Access-Control-Allow-Origin"], "https://wreckscanner.pl")
        self.assertNotEqual(allowed["Access-Control-Allow-Origin"], "*")
        self.assertEqual(allowed["Vary"], "Origin")


class DownloadApiContractTests(unittest.TestCase):
    def test_versioned_route_url_appends_cache_buster(self):
        self.assertEqual(
            server._versioned_route_url("/analiza/report.html", "123"),
            "/analiza/report.html?v=123",
        )
        self.assertEqual(
            server._versioned_route_url("/analiza/report.html?lang=pl", "123"),
            "/analiza/report.html?lang=pl&v=123",
        )

    def test_download_response_counts_all_non_cache_wfs_sources_as_downloaded(self):
        handler = make_handler(
            "/api/download",
            {"lat": 51.1, "lon": 17.1, "width": 50, "height": 50},
        )
        results = {
            2020: {"status": "ok"},
            2021: {"status": "missing"},
        }
        wfs_summary = [
            {"status": "replaced", "cache": "hit"},
            {"status": "replaced", "cache": "downloaded"},
            {"status": "replaced", "cache": "resumed"},
            {"status": "replaced", "cache": "restarted"},
            {"status": "skipped_low_resolution"},
        ]

        with (
            patch.object(map_downloads, "download_maps", return_value=(results, "bbox", wfs_summary)),
            patch.object(pipeline, "system_pressure", return_value={"overloaded": False}),
            patch("builtins.print"),
        ):
            server.Handler.do_POST(handler)

        payload = handler_json(handler)
        self.assertEqual(handler.status, 200)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["missing"], 1)
        self.assertEqual(payload["wfs_replaced"], 4)
        self.assertEqual(payload["wfs_cache_hits"], 1)
        self.assertEqual(payload["wfs_downloaded"], 3)
        self.assertEqual(payload["wfs_skipped"], 1)
        self.assertTrue(payload["job_token"])
        pipeline.finish_pipeline(payload["job_token"])

    def test_download_rejects_concurrent_pipeline(self):
        token = pipeline.start_pipeline("busy-client")
        handler = make_handler(
            "/api/download",
            {"lat": 51.1, "lon": 17.1, "width": 50, "height": 50},
        )

        with patch.object(pipeline, "system_pressure", return_value={"overloaded": False}):
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 429)
        self.assertIn("zajety", handler_json(handler)["error"])
        pipeline.finish_pipeline(token)

    def test_download_rejects_oversized_area(self):
        handler = make_handler(
            "/api/download",
            {"lat": 51.1, "lon": 17.1, "width": 500, "height": 500},
        )

        with patch.object(pipeline, "system_pressure", return_value={"overloaded": False}):
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 400)
        self.assertIn("maksymalnie", handler_json(handler)["error"])

    def test_download_progress_percent_is_clamped_and_indeterminate_for_zero_total(self):
        self.assertEqual(pipeline.progress_percent(50, 100), 50.0)
        self.assertEqual(pipeline.progress_percent(200, 100), 100.0)
        self.assertEqual(pipeline.progress_percent(-1, 100), 0.0)
        self.assertIsNone(pipeline.progress_percent(10, 0))

    def test_wms_proxy_strips_only_frontend_enhancement_param(self):
        upstream = "OGC_ortofoto_2025/MapServer/WMSServer?SERVICE=WMS&enhancementSettings=123&LAYERS=1&FORMAT=image/png"

        stripped = wms_cache.strip_proxy_only_params(upstream)

        self.assertEqual(
            stripped,
            "OGC_ortofoto_2025/MapServer/WMSServer?SERVICE=WMS&LAYERS=1&FORMAT=image%2Fpng",
        )

    def test_wms_tile_cache_key_ignores_frontend_revision_param(self):
        first = wms_cache.strip_proxy_only_params(
            "OGC_ortofoto_2025/MapServer/WMSServer?SERVICE=WMS&enhancementSettings=111&LAYERS=1"
        )
        second = wms_cache.strip_proxy_only_params(
            "OGC_ortofoto_2025/MapServer/WMSServer?SERVICE=WMS&enhancementSettings=222&LAYERS=1"
        )

        self.assertEqual(
            wms_cache.tile_cache_key(first, "settings-a"),
            wms_cache.tile_cache_key(second, "settings-a"),
        )
        self.assertNotEqual(
            wms_cache.tile_cache_key(first, "settings-a"),
            wms_cache.tile_cache_key(second, "settings-b"),
        )

    def test_wms_tile_cache_write_read_and_lru_cleanup(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "wms_tiles"
            with (
                patch.object(app_config, "WMS_TILE_CACHE_DIR", cache_dir),
                patch.object(app_config, "WMS_TILE_CACHE_MAX_BYTES", 8),
            ):
                old_path = wms_cache.tile_cache_path("a" * 64)
                new_path = wms_cache.tile_cache_path("b" * 64)
                wms_cache.write_tile_cache(old_path, b"old-data")
                time_old = 1_700_000_000
                os.utime(old_path, (time_old, time_old))
                wms_cache.write_tile_cache(new_path, b"new-data")

                self.assertEqual(wms_cache.read_tile_cache(new_path), b"new-data")
                wms_cache.cleanup_tile_cache(force=True)

                self.assertFalse(old_path.exists())
                self.assertTrue(new_path.exists())


class PrivacyRequestApiContractTests(unittest.TestCase):
    def test_privacy_pages_and_report_request_queue(self):
        with TemporaryDirectory() as tmp:
            requests_dir = Path(tmp) / "privacy_requests"

            privacy_page = make_handler("/privacy")
            privacy_head = make_handler("/privacy", method="HEAD")
            report_page = make_handler("/report")
            report_head = make_handler("/report", method="HEAD")
            server.Handler.do_GET(privacy_page)
            server.Handler.do_HEAD(privacy_head)
            server.Handler.do_GET(report_page)
            server.Handler.do_HEAD(report_head)
            self.assertEqual(privacy_page.status, 200)
            self.assertEqual(privacy_head.status, 200)
            self.assertEqual(privacy_head.wfile.getvalue(), b"")
            privacy_html = privacy_page.wfile.getvalue().decode("utf-8")
            self.assertIn("Oryginały zdjęć", privacy_html)
            self.assertIn("privacy@wreckscanner.pl", privacy_html)
            self.assertIn("Skarga do UODO", privacy_html)
            self.assertEqual(report_page.status, 200)
            self.assertEqual(report_head.status, 200)
            self.assertEqual(report_head.wfile.getvalue(), b"")
            self.assertIn("prośbę o usunięcie", report_page.wfile.getvalue().decode("utf-8"))

            create_handler = make_handler(
                "/api/privacy-requests",
                {
                    "email": "jan@example.com",
                    "target": "wreck_51100000_17200000",
                    "reason": "Proszę o zamazanie identyfikatora.",
                },
            )
            public_queue = make_handler("/api/admin/privacy-requests")
            admin_queue = make_handler("/api/admin/privacy-requests", headers=admin_cookie())

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "PRIVACY_REQUESTS_DIR", requests_dir),
            ):
                server.Handler.do_POST(create_handler)
                server.Handler.do_GET(public_queue)
                server.Handler.do_GET(admin_queue)
                request_id = handler_json(admin_queue)["requests"][0]["id"]
                update_handler = make_handler(
                    f"/api/admin/privacy-requests/{request_id}",
                    {
                        "status": "in_progress",
                        "admin_note": "Sprawdzam zdjęcie i teczkę.",
                    },
                    headers=admin_cookie(),
                    method="PATCH",
                )
                server.Handler.do_PATCH(update_handler)
                filtered_queue = make_handler(
                    "/api/admin/privacy-requests?status=in_progress",
                    headers=admin_cookie(),
                )
                server.Handler.do_GET(filtered_queue)

            self.assertEqual(create_handler.status, 200)
            self.assertEqual(public_queue.status, 401)
            self.assertEqual(admin_queue.status, 200)
            queue_payload = handler_json(admin_queue)
            self.assertEqual(len(queue_payload["requests"]), 1)
            self.assertEqual(queue_payload["requests"][0]["email"], "jan@example.com")
            self.assertEqual(update_handler.status, 200)
            updated_request = handler_json(update_handler)["request"]
            self.assertEqual(updated_request["status"], "in_progress")
            self.assertEqual(updated_request["admin_note"], "Sprawdzam zdjęcie i teczkę.")
            self.assertIsNone(updated_request["handled_at"])
            self.assertEqual(filtered_queue.status, 200)
            self.assertEqual(handler_json(filtered_queue)["requests"][0]["id"], request_id)


class PhotoRetentionApiContractTests(unittest.TestCase):
    def test_admin_can_view_and_run_photo_retention(self):
        fake_report = {
            "status": "ok",
            "dry_run": True,
            "retention_days": 180,
            "field_photos": {"scanned": 1, "replaced": 0, "deleted": 0, "skipped": 1},
            "wreck_photos": {"scanned": 0, "replaced": 0, "deleted": 0, "skipped": 0},
            "items": [],
        }
        public_status = make_handler("/api/admin/photo-retention")
        admin_status = make_handler("/api/admin/photo-retention", headers=admin_cookie())
        run_handler = make_handler(
            "/api/admin/photo-retention/run",
            {"dry_run": True},
            headers=admin_cookie(),
        )

        with (
            patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
            patch.object(server, "retire_private_originals", return_value=fake_report) as retire_mock,
        ):
            server.Handler.do_GET(public_status)
            server.Handler.do_GET(admin_status)
            server.Handler.do_POST(run_handler)

        self.assertEqual(public_status.status, 401)
        self.assertEqual(admin_status.status, 200)
        self.assertEqual(run_handler.status, 200)
        retire_mock.assert_called_once()
        self.assertTrue(retire_mock.call_args.kwargs["dry_run"])
        payload = handler_json(run_handler)
        self.assertEqual(payload["report"], fake_report)
        self.assertFalse(payload["retention"]["running"])
        self.assertEqual(payload["retention"]["last_source"], "admin")


class ReportPackageApiContractTests(unittest.TestCase):
    def test_report_package_requires_admin_before_form_validation(self):
        public_handler = make_multipart_handler(
            "/api/wrecks/wreck_51100000_17200000/report-package",
            fields={"unused": "value"},
            files=[],
        )
        admin_handler = make_multipart_handler(
            "/api/wrecks/wreck_51100000_17200000/report-package",
            fields={"unused": "value"},
            files=[],
            headers=admin_cookie(),
        )

        with patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}):
            server.Handler.do_POST(public_handler)
            server.Handler.do_POST(admin_handler)

        self.assertEqual(public_handler.status, 401)
        self.assertEqual(admin_handler.status, 400)
        self.assertIn("Uzupełnij wymagane pola", handler_json(admin_handler)["error"])

    def test_public_report_package_generates_clean_tokenized_download(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            private_reports_dir = root / "private_reports"
            fields = {
                "reporter_name": "Jan Kowalski",
                "reporter_address": "ul. Testowa 1, Wrocław",
                "reporter_phone": "500 600 700",
                "reporter_email": "jan@example.com",
                "location_description": "ul. Długa 10",
                "observed_at": "2026-06-02T12:30",
                "vehicle_description": "Pojazd długo stoi w tym samym miejscu.",
            }
            handler = make_multipart_handler(
                "/api/wrecks/wreck_51100000_17200000/public-report-package",
                fields=fields,
                files=[("photos[]", "miejsce.jpg", "image/jpeg", image_bytes())],
            )

            with (
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir),
            ):
                server.Handler.do_POST(handler)

            payload = handler_json(handler)
            self.assertEqual(handler.status, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertIn("/api/public-report-packages/", payload["zip_url"])
            self.assertIn("token=", payload["zip_url"])

            public_zip = make_handler(payload["zip_url"])
            bad_zip = make_handler(payload["zip_url"].replace("token=", "token=bad"))
            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                server.Handler.do_GET(public_zip)
                server.Handler.do_GET(bad_zip)
            self.assertEqual(public_zip.status, 200)
            self.assertEqual(bad_zip.status, 404)
            self.assertIn(("Content-Type", "application/zip"), public_zip.response_headers)

    def test_save_wreck_endpoint_is_public(self):
        handler = make_handler("/api/wrecks", {"rank": 3})

        with patch.object(
            server,
            "save_wreck_from_rank",
            return_value={"status": "ok", "created": True, "evidence_created": True, "wreck": {"id": "w"}},
        ) as save_mock:
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler_json(handler)["status"], "ok")
        save_mock.assert_called_once()

    def test_manual_wreck_endpoint_accepts_map_coordinates(self):
        handler = make_handler("/api/wrecks", {"lat": 51.088784, "lon": 17.035782})

        with patch.object(
            server,
            "save_manual_wreck",
            return_value={"status": "ok", "created": True, "evidence_created": True, "wreck": {"id": "w"}},
        ) as save_mock:
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler_json(handler)["status"], "ok")
        save_mock.assert_called_once_with(51.088784, 17.035782, core_config.WRECKS_DIR)

    def test_wreck_layer_visibility_filters_guest_api_only(self):
        wrecks = [{"id": "wreck_1", "lat": 51.1, "lon": 17.2}]
        settings = {
            "public_layers": {
                "saved_wrecks": False,
                "field_photo_vehicle": True,
                "field_photo_infrastructure": True,
                "field_photo_smoke": True,
            }
        }
        guest = make_handler("/api/wrecks")
        admin = make_handler("/api/wrecks", headers=admin_cookie())

        with (
            patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
            patch.object(server, "load_app_settings", return_value=settings),
            patch.object(server, "list_wrecks", return_value=wrecks),
        ):
            server.Handler.do_GET(guest)
            server.Handler.do_GET(admin)

        self.assertEqual(handler_json(guest)["wrecks"], [])
        self.assertEqual(handler_json(admin)["wrecks"], wrecks)

    def test_report_package_endpoint_accepts_multipart_and_generates_zip(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            private_reports_dir = root / "private_reports"
            fields = {
                "reporter_name": "Jan Kowalski",
                "reporter_address": "ul. Testowa 1, Wrocław",
                "reporter_phone": "500 600 700",
                "reporter_email": "jan@example.com",
                "location_description": "ul. Długa 10",
                "observed_at": "2026-06-02T12:30",
                "vehicle_description": "Pojazd długo stoi w tym samym miejscu.",
            }
            handler = make_multipart_handler(
                "/api/wrecks/wreck_51100000_17200000/report-package",
                fields=fields,
                files=[("photos[]", "miejsce.jpg", "image/jpeg", image_bytes())],
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir),
            ):
                server.Handler.do_POST(handler)

            payload = handler_json(handler)
            self.assertEqual(handler.status, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["recipient"], "interwencje@smwroclaw.pl")
            self.assertIn("Zgłoszenie pojazdu nieużytkowanego", payload["subject"])
            self.assertIn("/api/report-packages/", payload["zip_url"])
            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                zip_path, _ = report_package_asset("wreck_51100000_17200000", payload["package_id"], "zip")
                pdf_path, _ = report_package_asset("wreck_51100000_17200000", payload["package_id"], "pdf")
            self.assertTrue(zip_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertGreater(payload["pdf_size_bytes"], 10_000)

            public_zip_get = make_handler(payload["zip_url"])
            zip_get = make_handler(payload["zip_url"], headers=admin_cookie())
            pdf_get = make_handler(payload["pdf_url"], headers=admin_cookie())
            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir),
            ):
                server.Handler.do_GET(public_zip_get)
                server.Handler.do_GET(zip_get)
                server.Handler.do_GET(pdf_get)
            self.assertEqual(public_zip_get.status, 401)
            self.assertEqual(zip_get.status, 200)
            self.assertEqual(pdf_get.status, 200)
            self.assertIn(("Cache-Control", "no-store"), zip_get.response_headers)

    def test_wreck_photo_upload_requires_admin_and_updates_wreck_folder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            private_photos_dir = root / "private_photos"

            no_admin = make_multipart_handler(
                "/api/wrecks/wreck_51100000_17200000/photos",
                files=[("photos[]", "miejsce.jpg", "image/jpeg", image_bytes())],
            )
            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
            ):
                server.Handler.do_POST(no_admin)

                self.assertEqual(no_admin.status, 401)

                handler = make_multipart_handler(
                    "/api/wrecks/wreck_51100000_17200000/photos",
                    files=[("photos[]", "miejsce.jpg", "image/jpeg", image_bytes())],
                    headers=admin_cookie(),
                )
                server.Handler.do_POST(handler)

            payload = handler_json(handler)
            record_dir = wrecks_dir / "wreck_51100000_17200000"
            record = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            self.assertEqual(handler.status, 200)
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["photo_count"], 1)
            photo = record["attached_photos"][0]
            self.assertEqual(photo["public_review_status"], "pending")
            self.assertTrue((private_photos_dir / photo["private_original_file"]).exists())
            self.assertNotIn("original_file", photo)
            self.assertNotIn("thumb_file", photo)
            self.assertEqual(payload["wreck"]["photo_count"], 0)
            self.assertNotIn("Zdjęcia z miejsca", (record_dir / "index.html").read_text(encoding="utf-8"))

            public_original = make_handler(
                f"/zidentyfikowane_wraki/wreck_51100000_17200000/photos/{photo['id']}/original.jpg"
            )
            public_original_head = make_handler(
                f"/zidentyfikowane_wraki/wreck_51100000_17200000/photos/{photo['id']}/original.jpg"
            )
            record_json = make_handler("/zidentyfikowane_wraki/wreck_51100000_17200000/record.json")
            record_json_head = make_handler("/zidentyfikowane_wraki/wreck_51100000_17200000/record.json")
            with patch.object(core_config, "WRECKS_DIR", wrecks_dir):
                server.Handler.do_GET(public_original)
                server.Handler.do_HEAD(public_original_head)
                server.Handler.do_GET(record_json)
                server.Handler.do_HEAD(record_json_head)
            self.assertEqual(public_original.status, 404)
            self.assertEqual(public_original_head.status, 404)
            self.assertEqual(public_original_head.wfile.getvalue(), b"")
            self.assertEqual(record_json.status, 404)
            self.assertEqual(record_json_head.status, 404)
            self.assertEqual(record_json_head.wfile.getvalue(), b"")

            no_admin_original = make_handler(
                f"/api/admin/photos/wreck/wreck_51100000_17200000/{photo['id']}/original"
            )
            admin_original = make_handler(
                f"/api/admin/photos/wreck/wreck_51100000_17200000/{photo['id']}/original",
                headers=admin_cookie(),
            )
            approve = make_handler(
                f"/api/admin/photos/wreck/wreck_51100000_17200000/{photo['id']}/review",
                {"public_review_status": "approved", "redactions": []},
                headers=admin_cookie(),
            )
            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
            ):
                server.Handler.do_GET(no_admin_original)
                server.Handler.do_GET(admin_original)
                server.Handler.do_PATCH(approve)
            self.assertEqual(no_admin_original.status, 401)
            self.assertEqual(admin_original.status, 200)
            self.assertEqual(approve.status, 200)

            public_thumb = make_handler(
                f"/zidentyfikowane_wraki/wreck_51100000_17200000/photos/{photo['id']}/public_thumb.jpg"
            )
            public_thumb_head = make_handler(
                f"/zidentyfikowane_wraki/wreck_51100000_17200000/photos/{photo['id']}/public_thumb.jpg"
            )
            with (
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
            ):
                server.Handler.do_GET(public_thumb)
                server.Handler.do_HEAD(public_thumb_head)
            self.assertEqual(public_thumb.status, 200)
            self.assertIn(("Content-Type", "image/jpeg"), public_thumb.response_headers)
            self.assertEqual(public_thumb_head.status, 200)
            self.assertEqual(public_thumb_head.wfile.getvalue(), b"")

    def test_wreck_index_get_refreshes_public_report_html(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            record_dir = wrecks_dir / "wreck_51100000_17200000"
            (record_dir / "index.html").write_text("<html>stary raport</html>", encoding="utf-8")
            handler = make_handler("/zidentyfikowane_wraki/wreck_51100000_17200000/index.html")
            head_handler = make_handler("/zidentyfikowane_wraki/wreck_51100000_17200000/index.html")

            with patch.object(core_config, "WRECKS_DIR", wrecks_dir):
                server.Handler.do_GET(handler)
                server.Handler.do_HEAD(head_handler)

            body = handler.wfile.getvalue().decode("utf-8")
            self.assertEqual(handler.status, 200)
            self.assertEqual(head_handler.status, 200)
            self.assertEqual(head_handler.wfile.getvalue(), b"")
            self.assertIn("metric-strip", body)
            self.assertIn("Dodaj zdjęcia do teczki", body)
            self.assertIn("wreck-photo-form", body)


class FieldPhotoApiContractTests(unittest.TestCase):
    def test_field_photo_upload_requires_admin_session(self):
        handler = make_multipart_handler(
            "/api/field-photos",
            fields={"fallback_lat": "51.1", "fallback_lon": "17.2"},
            files=[("photo", "teren.jpg", "image/jpeg", image_bytes())],
        )

        with patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}):
            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 401)
        self.assertIn("administratora", handler_json(handler)["error"])

    def test_field_photo_layer_visibility_filters_guest_api_only(self):
        photos = [
            {"id": "p1", "issue_type": "vehicle", "lat": 51.1, "lon": 17.1},
            {"id": "p2", "issue_type": "smoke", "lat": 51.2, "lon": 17.2},
        ]
        settings = {
            "public_layers": {
                "saved_wrecks": True,
                "field_photo_vehicle": True,
                "field_photo_infrastructure": True,
                "field_photo_smoke": False,
            }
        }
        guest = make_handler("/api/field-photos")
        admin = make_handler("/api/field-photos", headers=admin_cookie())

        with (
            patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
            patch.object(server, "load_app_settings", return_value=settings),
            patch.object(server, "list_field_photos", return_value=photos),
        ):
            server.Handler.do_GET(guest)
            server.Handler.do_GET(admin)

        self.assertEqual([photo["id"] for photo in handler_json(guest)["photos"]], ["p1"])
        self.assertEqual([photo["id"] for photo in handler_json(admin)["photos"]], ["p1", "p2"])

    def test_field_photo_list_assets_are_public_and_delete_requires_admin(self):
        with TemporaryDirectory() as tmp:
            field_photos_dir = Path(tmp) / "zdjecia_terenowe"
            private_photos_dir = Path(tmp) / "private_photos"
            handler = make_multipart_handler(
                "/api/field-photos",
                fields={"fallback_lat": "51.1", "fallback_lon": "17.2", "issue_type": "smoke"},
                files=[("photo", "teren.jpg", "image/jpeg", image_bytes())],
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "FIELD_PHOTOS_DIR", field_photos_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
                patch.object(server, "load_app_settings", return_value=settings_store.default_app_settings()),
            ):
                server.Handler.do_POST(handler)

                payload = handler_json(handler)
                self.assertEqual(handler.status, 200)
                self.assertEqual(payload["status"], "ok")
                self.assertEqual(payload["photo"]["coordinate_source"], "map")
                self.assertEqual(payload["photo"]["issue_type"], "smoke")
                photo_id = payload["photo"]["id"]
                field_record = json.loads((field_photos_dir / photo_id / "record.json").read_text(encoding="utf-8"))
                self.assertEqual(field_record["issue_type"], "smoke")
                self.assertEqual(field_record["public_review_status"], "pending")
                self.assertTrue((private_photos_dir / field_record["private_original_file"]).exists())
                self.assertNotIn("original_file", field_record)

                list_handler = make_handler("/api/field-photos")
                server.Handler.do_GET(list_handler)
                list_payload = handler_json(list_handler)
                self.assertEqual(list_handler.status, 200)
                self.assertEqual(list_payload["photos"], [])

                thumb_handler = make_handler(f"/api/field-photos/{photo_id}/thumbnail")
                server.Handler.do_GET(thumb_handler)
                self.assertEqual(thumb_handler.status, 404)

                original_handler = make_handler(f"/api/field-photos/{photo_id}/original")
                server.Handler.do_GET(original_handler)
                self.assertEqual(original_handler.status, 404)

                no_admin_original = make_handler(f"/api/admin/photos/field/{photo_id}/original")
                server.Handler.do_GET(no_admin_original)
                self.assertEqual(no_admin_original.status, 401)

                admin_original = make_handler(f"/api/admin/photos/field/{photo_id}/original", headers=admin_cookie())
                server.Handler.do_GET(admin_original)
                self.assertEqual(admin_original.status, 200)
                self.assertIn(("Content-Type", "image/jpeg"), admin_original.response_headers)
                self.assertEqual(admin_original.wfile.getvalue(), image_bytes())

                approve_handler = make_handler(
                    f"/api/admin/photos/field/{photo_id}/review",
                    {"public_review_status": "approved", "redactions": []},
                    headers=admin_cookie(),
                )
                server.Handler.do_PATCH(approve_handler)
                self.assertEqual(approve_handler.status, 200)

                list_after_review = make_handler("/api/field-photos")
                server.Handler.do_GET(list_after_review)
                reviewed_payload = handler_json(list_after_review)
                self.assertEqual(len(reviewed_payload["photos"]), 1)
                self.assertIn("public_image", reviewed_payload["photos"][0])
                self.assertIn("public_thumb", reviewed_payload["photos"][0])
                self.assertNotIn("original_url", reviewed_payload["photos"][0])

                public_thumb = make_handler(f"/api/field-photos/{photo_id}/public-thumb")
                server.Handler.do_GET(public_thumb)
                self.assertEqual(public_thumb.status, 200)
                self.assertIn(("Content-Type", "image/jpeg"), public_thumb.response_headers)

                public_image = make_handler(f"/api/field-photos/{photo_id}/public-image")
                server.Handler.do_GET(public_image)
                self.assertEqual(public_image.status, 200)
                self.assertIn(("Content-Type", "image/jpeg"), public_image.response_headers)

                no_admin_delete = make_handler(f"/api/field-photos/{photo_id}")
                server.Handler.do_DELETE(no_admin_delete)
                self.assertEqual(no_admin_delete.status, 401)
                self.assertTrue((field_photos_dir / photo_id).exists())

                delete_handler = make_handler(f"/api/field-photos/{photo_id}", headers=admin_cookie())
                server.Handler.do_DELETE(delete_handler)
                self.assertEqual(delete_handler.status, 200)
                self.assertFalse((field_photos_dir / photo_id).exists())
                self.assertFalse((private_photos_dir / field_record["private_original_file"]).exists())

    def test_field_photo_location_patch_requires_admin_and_updates_record(self):
        with TemporaryDirectory() as tmp:
            field_photos_dir = Path(tmp) / "zdjecia_terenowe"
            private_photos_dir = Path(tmp) / "private_photos"
            upload_handler = make_multipart_handler(
                "/api/field-photos",
                fields={"fallback_lat": "51.1", "fallback_lon": "17.2"},
                files=[("photo", "teren.jpg", "image/jpeg", image_bytes())],
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "FIELD_PHOTOS_DIR", field_photos_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
            ):
                server.Handler.do_POST(upload_handler)
                photo_id = handler_json(upload_handler)["photo"]["id"]

                public_patch = make_handler(
                    f"/api/field-photos/{photo_id}/location",
                    {"lat": 51.3, "lon": 17.4},
                )
                server.Handler.do_PATCH(public_patch)
                self.assertEqual(public_patch.status, 401)

                invalid_patch = make_handler(
                    f"/api/field-photos/{photo_id}/location",
                    {"lat": 91, "lon": 17.4},
                    headers=admin_cookie(),
                )
                server.Handler.do_PATCH(invalid_patch)
                self.assertEqual(invalid_patch.status, 400)

                update_handler = make_handler(
                    f"/api/field-photos/{photo_id}/location",
                    {"lat": 51.3, "lon": 17.4},
                    headers=admin_cookie(),
                )
                server.Handler.do_PATCH(update_handler)
                payload = handler_json(update_handler)
                self.assertEqual(update_handler.status, 200)
                self.assertEqual(payload["status"], "ok")
                self.assertEqual(payload["photo"]["coordinate_source"], "manual")
                self.assertEqual(payload["photo"]["lat"], 51.3)
                self.assertEqual(payload["photo"]["lon"], 17.4)
                self.assertTrue(payload["photo"]["position_updated_at"])

                list_handler = make_handler("/api/field-photos")
                server.Handler.do_GET(list_handler)
                list_payload = handler_json(list_handler)
                self.assertEqual(list_handler.status, 200)
                self.assertEqual(list_payload["photos"], [])

                approve_handler = make_handler(
                    f"/api/admin/photos/field/{photo_id}/review",
                    {"public_review_status": "approved", "redactions": []},
                    headers=admin_cookie(),
                )
                server.Handler.do_PATCH(approve_handler)
                self.assertEqual(approve_handler.status, 200)

                reviewed_list = make_handler("/api/field-photos")
                server.Handler.do_GET(reviewed_list)
                reviewed_payload = handler_json(reviewed_list)
                self.assertEqual(reviewed_payload["photos"][0]["coordinate_source"], "manual")

    def test_field_photos_can_be_moved_to_wreck_folder_and_removed_from_field_layer(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            field_photos_dir = root / "zdjecia_terenowe"
            private_photos_dir = root / "private_photos"
            upload_handler = make_multipart_handler(
                "/api/field-photos",
                fields={"fallback_lat": "51.1", "fallback_lon": "17.2"},
                files=[("photo", "teren.jpg", "image/jpeg", image_bytes())],
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "FIELD_PHOTOS_DIR", field_photos_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
            ):
                server.Handler.do_POST(upload_handler)
                photo_id = handler_json(upload_handler)["photo"]["id"]

                public_attach = make_handler(
                    "/api/wrecks/wreck_51100000_17200000/field-photos/attach",
                    {"photo_ids": [photo_id]},
                )
                server.Handler.do_POST(public_attach)
                self.assertEqual(public_attach.status, 401)

                attach_handler = make_handler(
                    "/api/wrecks/wreck_51100000_17200000/field-photos/attach",
                    {"photo_ids": [photo_id]},
                    headers=admin_cookie(),
                )
                server.Handler.do_POST(attach_handler)

                payload = handler_json(attach_handler)
                field_record_dir = field_photos_dir / photo_id
                wreck_record_dir = wrecks_dir / "wreck_51100000_17200000"
                wreck_record = json.loads((wreck_record_dir / "record.json").read_text(encoding="utf-8"))
                attached_photo = wreck_record["attached_photos"][0]
                self.assertEqual(attach_handler.status, 200)
                self.assertEqual(payload["status"], "ok")
                self.assertEqual(payload["wreck_id"], "wreck_51100000_17200000")
                self.assertEqual(payload["removed_field_photo_ids"], [photo_id])
                self.assertEqual(payload["attached_count"], 1)
                self.assertEqual(payload["photo_count"], 1)
                self.assertEqual(payload["wreck"]["photo_count"], 0)
                self.assertFalse(field_record_dir.exists())
                self.assertEqual(attached_photo["id"], photo_id)
                self.assertEqual(attached_photo["source"], "field_photo")
                self.assertEqual(attached_photo["public_review_status"], "pending")
                self.assertTrue((private_photos_dir / attached_photo["private_original_file"]).exists())
                self.assertNotIn("original_file", attached_photo)
                self.assertNotIn("thumb_file", attached_photo)
                report_html = (wreck_record_dir / "index.html").read_text(encoding="utf-8")
                self.assertNotIn("Zdjęcia z miejsca", report_html)
                self.assertNotIn("teren.jpg", report_html)

                list_handler = make_handler("/api/field-photos")
                server.Handler.do_GET(list_handler)
                list_payload = handler_json(list_handler)
                self.assertEqual(list_payload["photos"], [])

    def test_non_vehicle_field_photos_cannot_be_moved_to_wreck_folder(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            field_photos_dir = root / "zdjecia_terenowe"
            private_photos_dir = root / "private_photos"
            upload_handler = make_multipart_handler(
                "/api/field-photos",
                fields={"fallback_lat": "51.1", "fallback_lon": "17.2", "issue_type": "infrastructure"},
                files=[("photo", "teren.jpg", "image/jpeg", image_bytes())],
                headers=admin_cookie(),
            )

            with (
                patch.dict(os.environ, {"WRECKSCANNER_ADMIN_PASSWORD": "secret"}),
                patch.object(core_config, "WRECKS_DIR", wrecks_dir),
                patch.object(core_config, "FIELD_PHOTOS_DIR", field_photos_dir),
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
            ):
                server.Handler.do_POST(upload_handler)
                photo_id = handler_json(upload_handler)["photo"]["id"]

                attach_handler = make_handler(
                    "/api/wrecks/wreck_51100000_17200000/field-photos/attach",
                    {"photo_ids": [photo_id]},
                    headers=admin_cookie(),
                )
                server.Handler.do_POST(attach_handler)

                payload = handler_json(attach_handler)
                self.assertEqual(attach_handler.status, 400)
                self.assertIn("zalegających pojazdów", payload["error"])
                self.assertTrue((field_photos_dir / photo_id / "record.json").exists())


if __name__ == "__main__":
    unittest.main()
