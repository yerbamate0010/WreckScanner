import io
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

import core.report_pdf as report_pdf
from core import config as core_config
from core.config import MAX_REPORT_PHOTO_BYTES
from core.report_packages import (
    ReportPhotoUpload,
    create_public_report_package,
    create_report_package,
    prepare_report_photos,
    public_report_package_asset,
    report_package_asset,
)
from core.wrecks import save_manual_wreck


def image_bytes(fmt: str = "JPEG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 24), (120, 80, 40)).save(out, fmt)
    return out.getvalue()


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def valid_fields() -> dict[str, str]:
    return {
        "reporter_name": "Jan Kowalski",
        "reporter_address": "ul. Testowa 1, Wrocław",
        "reporter_phone": "500 600 700",
        "reporter_email": "jan@example.com",
        "location_description": "ul. Długa 10, przy parkingu",
        "observed_at": "2026-06-02T12:30",
        "vehicle_description": "Pojazd zabrudzony, długo stoi w tym samym miejscu.",
    }


def create_wreck_fixture(root: Path) -> Path:
    wrecks_dir = root / "zidentyfikowane_wraki"
    record_dir = wrecks_dir / "wreck_51100000_17200000"
    evidence_dir = record_dir / "evidence" / "abc123"
    attached_photo_dir = record_dir / "photos" / "photo_20260603T000000Z_abcdef12"
    evidence_dir.mkdir(parents=True)
    attached_photo_dir.mkdir(parents=True)
    (evidence_dir / "2025.jpg").write_bytes(image_bytes())
    (attached_photo_dir / "original.jpg").write_bytes(image_bytes())
    (attached_photo_dir / "thumb.jpg").write_bytes(image_bytes())
    (record_dir / "index.html").write_text(
        '<html><body><img src="evidence/abc123/2025.jpg"><a href="evidence/abc123/candidate.json">json</a></body></html>',
        encoding="utf-8",
    )
    write_json(evidence_dir / "candidate.json", {"rank": 1})
    write_json(evidence_dir / "metadata.json", {"years": [2025]})
    write_json(evidence_dir / "links.json", {"geoportal": "https://example.test/geo"})
    write_json(attached_photo_dir / "record.json", {"id": "photo_20260603T000000Z_abcdef12"})
    write_json(
        record_dir / "record.json",
        {
            "id": "wreck_51100000_17200000",
            "status": "confirmed",
            "lat": 51.1,
            "lon": 17.2,
            "best_score": 0.87,
            "labels_present": ["2025"],
            "latest_evidence": {
                "id": "abc123",
                "path": "evidence/abc123",
                "score": 0.87,
                "labels_present": ["2025"],
                "crops": [{"label": "2025", "file": "2025.jpg"}],
                "links": {"geoportal": "https://example.test/geo"},
            },
            "links": {
                "street_view": "https://example.test/street",
                "geoportal": "https://example.test/geo",
            },
            "attached_photos": [
                {
                    "id": "photo_20260603T000000Z_abcdef12",
                    "created_at": "2026-06-03T00:00:00Z",
                    "original_filename": "teren.jpg",
                    "original_file": "photos/photo_20260603T000000Z_abcdef12/original.jpg",
                    "thumb_file": "photos/photo_20260603T000000Z_abcdef12/thumb.jpg",
                }
            ],
            "evidences": [],
        },
    )
    return wrecks_dir


class ReportPackageTests(unittest.TestCase):
    def test_prepare_report_photos_validates_count_size_and_type(self):
        jpg = ReportPhotoUpload("photos[]", "a.jpg", "image/jpeg", image_bytes())
        self.assertEqual(len(prepare_report_photos([jpg])), 1)

        with self.assertRaisesRegex(ValueError, "maksymalnie"):
            prepare_report_photos([jpg, jpg, jpg, jpg, jpg, jpg])

        with self.assertRaisesRegex(ValueError, "10 MB"):
            prepare_report_photos(
                [ReportPhotoUpload("photos[]", "big.jpg", "image/jpeg", b"x" * (MAX_REPORT_PHOTO_BYTES + 1))]
            )

        with self.assertRaisesRegex(ValueError, "obsługiwanym zdjęciem"):
            prepare_report_photos([ReportPhotoUpload("photos[]", "bad.txt", "text/plain", b"not an image")])

    def test_create_report_package_generates_zip_and_keeps_record_without_reporter_data(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            private_photos_dir = root / "private_photos"
            private_reports_dir = root / "private_reports"
            public_index_path = wrecks_dir / "wreck_51100000_17200000" / "index.html"
            record_path = wrecks_dir / "wreck_51100000_17200000" / "record.json"

            with (
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
                patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir),
            ):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["attached_photos"][0]["public_review_status"] = "approved"
                record["attached_photos"][0]["redactions"] = []
                record["attached_photos"][0]["reviewed_at"] = "2026-06-03T00:00:00Z"
                write_json(record_path, record)
                result = create_report_package(
                    "wreck_51100000_17200000",
                    valid_fields(),
                    [ReportPhotoUpload("photos[]", "miejsce.png", "image/png", image_bytes("PNG"))],
                    wrecks_dir,
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["recipient"], "interwencje@smwroclaw.pl")
            self.assertIn("Zgłoszenie pojazdu nieużytkowanego", result["subject"])
            self.assertIn("Jan Kowalski", result["body"])
            self.assertIn("/api/report-packages/", result["pdf_url"])
            self.assertNotIn("Jan Kowalski", public_index_path.read_text(encoding="utf-8"))
            self.assertIn("metric-strip", public_index_path.read_text(encoding="utf-8"))
            self.assertNotIn('"original_file"', record_path.read_text(encoding="utf-8"))

            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                zip_path, _ = report_package_asset("wreck_51100000_17200000", result["package_id"], "zip")
            self.assertTrue(zip_path.exists())
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
                self.assertIn("zgloszenie.txt", names)
                self.assertIn("raport.html", names)
                self.assertIn("evidence/abc123/2025.jpg", names)
                self.assertIn("evidence/abc123/candidate.json", names)
                self.assertIn("miniatury_historyczne/2025.jpg", names)
                self.assertIn("zdjecia_z_miejsca/zdjecie_01.jpg", names)
                self.assertIn("photos/photo_20260603T000000Z_abcdef12/public_thumb.jpg", names)
                self.assertIn("photos/photo_20260603T000000Z_abcdef12/public.jpg", names)
                self.assertNotIn("photos/photo_20260603T000000Z_abcdef12/original.jpg", names)
                self.assertIn("metadane/record.json", names)
                self.assertEqual(archive.read("evidence/abc123/2025.jpg"), image_bytes())
                self.assertEqual(archive.read("miniatury_historyczne/2025.jpg"), image_bytes())
                self.assertIn("Jan Kowalski", archive.read("zgloszenie.txt").decode("utf-8"))
                report_html = archive.read("raport.html").decode("utf-8")
                self.assertIn("Treść zgłoszenia", report_html)
                self.assertIn("interwencje@smwroclaw.pl", report_html)
                self.assertIn(result["subject"], report_html)
                self.assertIn("Dane osoby zgłaszającej", report_html)
                self.assertIn("Jan Kowalski", report_html)
                self.assertIn("Zdjęcia z miejsca", report_html)
                self.assertIn("photos/photo_20260603T000000Z_abcdef12/public_thumb.jpg", report_html)
                self.assertNotIn("photos/photo_20260603T000000Z_abcdef12/original.jpg", report_html)
                self.assertIn("Zdjęcia dołączone do zgłoszenia", report_html)
                self.assertIn("zdjecia_z_miejsca/zdjecie_01.jpg", report_html)
                self.assertIn("data-report-package-style", report_html)
                self.assertNotIn("data-report-photo-upload", report_html)

            package_dir = zip_path.with_suffix("")
            self.assertTrue((package_dir / "oryginalne_zdjecia" / "miejsce.png").exists())
            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                pdf_path, _ = report_package_asset("wreck_51100000_17200000", result["package_id"], "pdf")
            self.assertTrue(pdf_path.exists())
            self.assertGreater(pdf_path.stat().st_size, 10_000)
            self.assertEqual(pdf_path.read_bytes()[:5], b"%PDF-")
            self.assertGreater(result["pdf_size_bytes"], 10_000)

    def test_create_report_package_accepts_manual_wreck_without_yolo_crops(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_reports_dir = root / "private_reports"
            saved = save_manual_wreck(51.088784, 17.035782, wrecks_dir)
            wreck_id = saved["wreck"]["id"]

            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                result = create_report_package(wreck_id, valid_fields(), [], wrecks_dir)

            self.assertEqual(result["status"], "ok")
            self.assertIn("/api/report-packages/", result["zip_url"])
            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                zip_path, _ = report_package_asset(wreck_id, result["package_id"], "zip")
            self.assertTrue(zip_path.exists())
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
                self.assertIn("zgloszenie.txt", names)
                self.assertIn("raport.html", names)
                self.assertIn("metadane/record.json", names)
                self.assertTrue(any(name.endswith("/manual_inspection.json") for name in names))
                self.assertFalse(any(name.startswith("miniatury_historyczne/") for name in names))
                self.assertIn("Treść zgłoszenia", archive.read("raport.html").decode("utf-8"))
            with patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir):
                pdf_path, _ = report_package_asset(wreck_id, result["package_id"], "pdf")
            self.assertTrue(pdf_path.exists())
            self.assertEqual(pdf_path.read_bytes()[:5], b"%PDF-")

    def test_create_public_report_package_uses_clean_assets_and_token(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = create_wreck_fixture(root)
            private_photos_dir = root / "private_photos"
            private_reports_dir = root / "private_reports"
            record_path = wrecks_dir / "wreck_51100000_17200000" / "record.json"

            with (
                patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_photos_dir),
                patch.object(core_config, "PRIVATE_REPORTS_DIR", private_reports_dir),
            ):
                record = json.loads(record_path.read_text(encoding="utf-8"))
                record["attached_photos"][0]["public_review_status"] = "approved"
                record["attached_photos"][0]["redactions"] = []
                record["attached_photos"][0]["reviewed_at"] = "2026-06-03T00:00:00Z"
                write_json(record_path, record)
                result = create_public_report_package(
                    "wreck_51100000_17200000",
                    valid_fields(),
                    [ReportPhotoUpload("photos[]", "miejsce.png", "image/png", image_bytes("PNG"))],
                    wrecks_dir,
                )

                self.assertEqual(result["status"], "ok")
                self.assertIn("/api/public-report-packages/", result["zip_url"])
                self.assertIn("token=", result["zip_url"])
                with self.assertRaises(FileNotFoundError):
                    public_report_package_asset("wreck_51100000_17200000", result["package_id"], "zip", "bad-token")
                token = result["zip_url"].split("token=", 1)[1]
                zip_path, _ = public_report_package_asset("wreck_51100000_17200000", result["package_id"], "zip", token)
                pdf_path, _ = public_report_package_asset("wreck_51100000_17200000", result["package_id"], "pdf", token)

            self.assertTrue(zip_path.exists())
            self.assertTrue(pdf_path.exists())
            self.assertFalse((zip_path.with_suffix("") / "oryginalne_zdjecia").exists())
            with zipfile.ZipFile(zip_path) as archive:
                names = set(archive.namelist())
                self.assertIn("zgloszenie.txt", names)
                self.assertIn("raport.html", names)
                self.assertIn("miniatury_historyczne/2025.jpg", names)
                self.assertIn("photos/photo_20260603T000000Z_abcdef12/public_thumb.jpg", names)
                self.assertIn("photos/photo_20260603T000000Z_abcdef12/public.jpg", names)
                self.assertIn("zdjecia_z_miejsca/zdjecie_01.jpg", names)
                self.assertNotIn("metadane/record.json", names)
                self.assertNotIn("evidence/abc123/candidate.json", names)
                self.assertNotIn("evidence/abc123/metadata.json", names)
                self.assertNotIn("photos/photo_20260603T000000Z_abcdef12/original.jpg", names)

    def test_report_pdf_starts_mail_draft_on_new_page(self):
        events = []
        original_page_break = report_pdf._PdfPages.page_break
        original_heading = report_pdf._PdfPages.heading

        def page_break(self):
            events.append("page_break")
            return original_page_break(self)

        def heading(self, text):
            events.append(("heading", text))
            return original_heading(self, text)

        with (
            TemporaryDirectory() as tmp,
            patch.object(report_pdf._PdfPages, "page_break", page_break),
            patch.object(report_pdf._PdfPages, "heading", heading),
        ):
            record_dir = Path(tmp)
            report_pdf.build_report_pdf(
                record={
                    "id": "wreck_51100000_17200000",
                    "status": "confirmed",
                    "lat": 51.1,
                    "lon": 17.2,
                    "best_score": 0.9,
                    "labels_present": ["2024", "2025"],
                    "links": {},
                    "evidences": [],
                },
                evidence={"path": "", "crops": []},
                record_dir=record_dir,
                recipient="interwencje@example.test",
                subject="Test",
                mail_body="Dzień dobry,\n\nTreść zgłoszenia.",
                report_photos=[],
            )

        draft_heading_idx = events.index(("heading", "Treść zgłoszenia"))
        self.assertEqual(events[draft_heading_idx - 1], "page_break")

    def test_report_pdf_uses_approved_public_attached_photo(self):
        with TemporaryDirectory() as tmp:
            record_dir = Path(tmp)
            photo_dir = record_dir / "photos" / "photo_20260603T000000Z_abcdef12"
            photo_dir.mkdir(parents=True)
            public = image_bytes()
            thumb = b"tiny-thumbnail"
            (photo_dir / "public.jpg").write_bytes(public)
            (photo_dir / "public_thumb.jpg").write_bytes(thumb)

            photo = report_pdf._photo_bytes_from_record(
                record_dir,
                {
                    "id": "photo_20260603T000000Z_abcdef12",
                    "original_filename": "teren.jpg",
                    "public_review_status": "approved",
                    "public_image_file": "photos/photo_20260603T000000Z_abcdef12/public.jpg",
                    "public_thumb_file": "photos/photo_20260603T000000Z_abcdef12/public_thumb.jpg",
                },
            )

            self.assertIsNotNone(photo)
            self.assertEqual(photo.data, public)

            pending = report_pdf._photo_bytes_from_record(
                record_dir,
                {
                    "id": "photo_20260603T000000Z_abcdef12",
                    "public_review_status": "pending",
                    "public_image_file": "photos/photo_20260603T000000Z_abcdef12/public.jpg",
                },
            )
            self.assertIsNone(pending)


if __name__ == "__main__":
    unittest.main()
