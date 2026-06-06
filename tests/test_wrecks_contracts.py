import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from core import config as core_config
from core.uploads import UploadedFile
from core.wrecks import (
    attach_wreck_photos,
    delete_wreck,
    list_wrecks,
    review_wreck,
    review_wreck_photo,
    save_manual_wreck,
    save_wreck_from_rank,
)


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def image_bytes() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (32, 24), (80, 110, 140)).save(out, "JPEG")
    return out.getvalue()


def upload(data: bytes, filename: str = "miejsce.jpg") -> UploadedFile:
    return UploadedFile(field_name="photos[]", filename=filename, content_type="image/jpeg", data=data)


def create_scan_data(root: Path) -> Path:
    data_dir = root / "dane"
    write_json(
        data_dir / "metadata.json",
        {
            "bbox_4326": {
                "min_lat": 51.0886,
                "max_lat": 51.0890,
                "min_lon": 17.0355,
                "max_lon": 17.0361,
            },
            "image_width_px": 200,
            "image_height_px": 200,
            "years": [2024, 2025],
        },
    )
    Image.new("RGB", (200, 200), (80, 110, 140)).save(data_dir / "ortofoto_2024.png")
    Image.new("RGB", (200, 200), (120, 90, 70)).save(data_dir / "ortofoto_2025.png")
    return data_dir


class SavedWreckContractTests(unittest.TestCase):
    def test_save_wreck_creates_record_evidence_and_dedupes_same_evidence(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_dir = root / "analiza"
            data_dir = root / "dane"
            wrecks_dir = root / "wraki"
            (analysis_dir / "crops").mkdir(parents=True)
            (analysis_dir / "crops" / "cand_000_2024.jpg").write_bytes(b"jpg-2024")
            (analysis_dir / "crops" / "cand_000_2025.jpg").write_bytes(b"jpg-2025")
            write_json(
                analysis_dir / "candidates.json",
                [
                    {
                        "rank": 1,
                        "lat": 51.1,
                        "lon": 17.2,
                        "score": 0.87,
                        "labels_present": ["2024", "2025"],
                    }
                ],
            )
            write_json(
                data_dir / "metadata.json",
                {
                    "bbox_4326": {"min_lat": 51.0, "max_lat": 51.2, "min_lon": 17.1, "max_lon": 17.3},
                    "years": [2024, 2025],
                },
            )

            first = save_wreck_from_rank(1, analysis_dir, data_dir, wrecks_dir)
            second = save_wreck_from_rank(1, analysis_dir, data_dir, wrecks_dir)

            self.assertTrue(first["created"])
            self.assertTrue(first["evidence_created"])
            self.assertFalse(second["created"])
            self.assertFalse(second["evidence_created"])
            wrecks = list_wrecks(wrecks_dir)
            self.assertEqual(len(wrecks), 1)
            self.assertEqual(wrecks[0]["labels_present"], ["2024", "2025"])
            self.assertEqual(wrecks[0]["evidence_count"], 1)

            record_path = next(wrecks_dir.glob("*/record.json"))
            record = json.loads(record_path.read_text(encoding="utf-8"))
            evidence_path = record_path.parent / record["evidences"][0]["path"]
            self.assertNotIn("preview_photos", wrecks[0])
            self.assertEqual(wrecks[0]["field_photo_previews"], [])
            preview = wrecks[0]["evidence_previews"]
            self.assertEqual([photo["label"] for photo in preview], ["2024", "2025"])
            self.assertEqual(preview[0]["source"], "evidence")
            self.assertEqual(
                preview[0]["public_thumb"],
                f"/zidentyfikowane_wraki/{record['id']}/{record['evidences'][0]['path']}/2024.jpg",
            )
            self.assertTrue((evidence_path / "2024.jpg").exists())
            self.assertTrue((evidence_path / "candidate.json").exists())
            self.assertTrue((record_path.parent / "index.html").exists())

    def test_attach_wreck_photos_updates_record_files_and_public_report(self):
        with TemporaryDirectory() as tmp:
            wrecks_dir = Path(tmp)
            record_dir = wrecks_dir / "wreck_51100000_17200000"
            write_json(
                record_dir / "record.json",
                {
                    "id": "wreck_51100000_17200000",
                    "status": "confirmed",
                    "lat": 51.1,
                    "lon": 17.2,
                    "best_score": 0.92,
                    "labels_present": ["2020", "2021", "2022", "2023", "2024", "2025"],
                    "latest_evidence": {"created_at": "2026-05-29T11:11:53Z"},
                    "links": {"geoportal": "https://example.test/geo"},
                    "evidences": [],
                },
            )

            private_dir = Path(tmp) / "private"
            with patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_dir):
                result = attach_wreck_photos("wreck_51100000_17200000", [upload(image_bytes())], wrecks_dir)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["photo_count"], 1)
            record = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            photo = record["attached_photos"][0]
            self.assertEqual(photo["public_review_status"], "pending")
            self.assertTrue((private_dir / photo["private_original_file"]).exists())
            self.assertNotIn("original_file", photo)
            self.assertNotIn("thumb_file", photo)
            with patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_dir):
                summary = list_wrecks(wrecks_dir)[0]
            self.assertEqual(summary["photo_count"], 0)
            self.assertEqual(summary["review_photo_count"], 1)
            self.assertEqual(summary["field_photo_previews"], [])
            self.assertEqual(summary["evidence_previews"], [])

            with patch.object(core_config, "PRIVATE_PHOTOS_DIR", private_dir):
                review_wreck_photo(
                    "wreck_51100000_17200000",
                    photo["id"],
                    wrecks_dir,
                    status="approved",
                    redactions=[{"x": 0, "y": 0, "width": 0.5, "height": 0.5}],
                )
                summary = list_wrecks(wrecks_dir)[0]
            self.assertEqual(summary["photo_count"], 1)
            self.assertEqual(summary["review_photo_count"], 1)
            reviewed_record = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            reviewed_photo = reviewed_record["attached_photos"][0]
            self.assertEqual(list(reviewed_photo["redactions"][0]), ["points"])
            self.assertEqual(len(reviewed_photo["redactions"][0]["points"]), 4)
            self.assertEqual(summary["evidence_previews"], [])
            self.assertEqual(summary["field_photo_previews"][0]["source"], "attached")
            self.assertEqual(
                summary["field_photo_previews"][0]["public_thumb"],
                f"/zidentyfikowane_wraki/wreck_51100000_17200000/photos/{photo['id']}/public_thumb.jpg",
            )
            report_html = (record_dir / "index.html").read_text(encoding="utf-8")
            self.assertIn("metric-strip", report_html)
            self.assertIn("2020-2025 (6 lat)", report_html)
            self.assertIn("Zdjęcia z miejsca", report_html)
            self.assertIn(f'<img src="photos/{photo["id"]}/public_thumb.jpg"', report_html)
            self.assertNotIn("original.jpg", report_html)
            self.assertIn("wreck-photo-form", report_html)

    def test_save_manual_wreck_creates_manual_record_and_dedupes_nearby_location(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = create_scan_data(root)
            wrecks_dir = root / "wraki"

            first = save_manual_wreck(51.088784, 17.035782, data_dir, wrecks_dir)
            second = save_manual_wreck(51.088785, 17.035783, data_dir, wrecks_dir)

            self.assertTrue(first["created"])
            self.assertTrue(first["evidence_created"])
            self.assertFalse(second["created"])
            self.assertFalse(second["evidence_created"])
            self.assertEqual(len(list_wrecks(wrecks_dir)), 1)

            record_path = next(wrecks_dir.glob("*/record.json"))
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "manual")
            self.assertEqual(record["source"], "manual_inspection")
            self.assertEqual(record["latest_evidence"]["source"], "manual_inspection")
            self.assertEqual(record["labels_present"], ["2024", "2025"])
            evidence_dir = record_path.parent / record["latest_evidence"]["path"]
            self.assertEqual([crop["label"] for crop in record["latest_evidence"]["crops"]], ["2024", "2025"])
            self.assertTrue((evidence_dir / "2024.jpg").exists())
            self.assertTrue((evidence_dir / "2025.jpg").exists())
            self.assertTrue((evidence_dir / "metadata.json").exists())
            self.assertTrue((evidence_dir / "manual_inspection.json").exists())
            self.assertTrue((evidence_dir / "links.json").exists())
            self.assertTrue((record_path.parent / "index.html").exists())

    def test_pending_manual_wreck_is_hidden_until_reviewed(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = create_scan_data(root)
            wrecks_dir = root / "wraki"

            result = save_manual_wreck(
                51.088784,
                17.035782,
                data_dir,
                wrecks_dir,
                public_review_status="pending",
                submission_owner="public:test",
            )

            self.assertEqual(list_wrecks(wrecks_dir), [])
            admin_wrecks = list_wrecks(wrecks_dir, include_pending=True)
            self.assertEqual(len(admin_wrecks), 1)
            self.assertEqual(admin_wrecks[0]["public_review_status"], "pending")
            self.assertEqual(admin_wrecks[0]["review_photo_count"], 0)

            review_wreck(result["wreck"]["id"], wrecks_dir, status="approved")

            public_wrecks = list_wrecks(wrecks_dir)
            self.assertEqual(len(public_wrecks), 1)
            self.assertEqual(public_wrecks[0]["public_review_status"], "approved")

    def test_save_manual_wreck_rejects_invalid_coordinates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "dane"
            wrecks_dir = root / "wraki"

            with self.assertRaises(ValueError):
                save_manual_wreck("not-a-lat", 17.035782, data_dir, wrecks_dir)
            with self.assertRaises(ValueError):
                save_manual_wreck(91, 17.035782, data_dir, wrecks_dir)

    def test_save_manual_wreck_rejects_points_outside_last_scan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = create_scan_data(root)
            wrecks_dir = root / "wraki"

            with self.assertRaisesRegex(ValueError, "poza ostatnio zeskanowanym obszarem"):
                save_manual_wreck(51.2, 17.2, data_dir, wrecks_dir)
            self.assertFalse(wrecks_dir.exists())

    def test_delete_wreck_removes_only_valid_record_folder(self):
        with TemporaryDirectory() as tmp:
            wrecks_dir = Path(tmp)
            record_dir = wrecks_dir / "wreck_51100000_17200000"
            write_json(record_dir / "record.json", {"id": "wreck_51100000_17200000", "lat": 51.1, "lon": 17.2})

            result = delete_wreck("wreck_51100000_17200000", wrecks_dir)

            self.assertEqual(result["deleted"], "wreck_51100000_17200000")
            self.assertFalse(record_dir.exists())
            with self.assertRaises(ValueError):
                delete_wreck("../wreck_51100000_17200000", wrecks_dir)


if __name__ == "__main__":
    unittest.main()
