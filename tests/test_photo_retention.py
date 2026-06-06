import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from core.data_diagnostics import run_data_diagnostics
from core.photo_retention import retire_private_originals


NOW = datetime(2026, 6, 5, 12, 0, 0, tzinfo=timezone.utc)
OLD_REVIEWED_AT = "2025-12-01T10:00:00Z"
RECENT_REVIEWED_AT = "2026-05-01T10:00:00Z"


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_jpeg(path: Path, size=(48, 32), color=(70, 120, 160)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path, "JPEG", quality=90)


def field_record(photo_id: str, *, status: str = "approved", reviewed_at: str = OLD_REVIEWED_AT):
    return {
        "id": photo_id,
        "created_at": "2025-11-01T10:00:00Z",
        "original_filename": "teren.jpg",
        "content_type": "image/jpeg",
        "format": "JPEG",
        "size_bytes": 100,
        "image_width": 48,
        "image_height": 32,
        "issue_type": "vehicle",
        "lat": 51.1,
        "lon": 17.2,
        "coordinate_source": "map",
        "private_original_file": f"field_photos/{photo_id}/original.jpg",
        "public_review_status": status,
        "redactions": [],
        "reviewed_at": reviewed_at,
        "links": {},
    }


class PhotoRetentionTests(unittest.TestCase):
    def test_replaces_old_approved_field_private_original_with_public_copy(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "field"
            private_dir = root / "private"
            (root / "wrecks").mkdir()
            photo_id = "photo_20251101T100000Z_abcdef12"
            record_dir = field_dir / photo_id
            record = field_record(photo_id)
            record["public_image_file"] = "public.jpg"
            record["public_thumb_file"] = "public_thumb.jpg"
            write_json(record_dir / "record.json", record)
            write_jpeg(private_dir / record["private_original_file"], color=(255, 0, 0))
            write_jpeg(record_dir / "public.jpg", color=(15, 23, 42))
            write_jpeg(record_dir / "public_thumb.jpg", size=(24, 16), color=(15, 23, 42))

            report = retire_private_originals(
                field_photos_dir=field_dir,
                wrecks_dir=root / "wrecks",
                private_photos_dir=private_dir,
                now=NOW,
                dry_run=False,
            )

            self.assertEqual(report["field_photos"]["replaced"], 1)
            updated = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            self.assertEqual(updated["private_original_file"], f"field_photos/{photo_id}/retained_public.jpg")
            self.assertEqual(updated["private_original_retention_action"], "replaced_with_public_copy")
            self.assertEqual(updated["private_original_replaced_at"], "2026-06-05T12:00:00Z")
            self.assertEqual(updated["content_type"], "image/jpeg")
            self.assertEqual(updated["format"], "JPEG")
            self.assertFalse((private_dir / f"field_photos/{photo_id}/original.jpg").exists())
            self.assertTrue((private_dir / updated["private_original_file"]).exists())
            self.assertEqual(
                (private_dir / updated["private_original_file"]).read_bytes(),
                (record_dir / "public.jpg").read_bytes(),
            )
            diagnostics = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=root / "wrecks",
                private_photos_dir=private_dir,
            )
            self.assertEqual(diagnostics["status"], "ok")

    def test_deletes_old_rejected_field_private_original(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "field"
            private_dir = root / "private"
            (root / "wrecks").mkdir()
            photo_id = "photo_20251101T100000Z_abcdef12"
            record_dir = field_dir / photo_id
            record = field_record(photo_id, status="rejected")
            write_json(record_dir / "record.json", record)
            write_jpeg(private_dir / record["private_original_file"])

            report = retire_private_originals(
                field_photos_dir=field_dir,
                wrecks_dir=root / "wrecks",
                private_photos_dir=private_dir,
                now=NOW,
                dry_run=False,
            )

            self.assertEqual(report["field_photos"]["deleted"], 1)
            updated = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            self.assertNotIn("private_original_file", updated)
            self.assertEqual(updated["private_original_retention_action"], "deleted_rejected_original")
            self.assertEqual(updated["private_original_deleted_at"], "2026-06-05T12:00:00Z")
            self.assertFalse((private_dir / f"field_photos/{photo_id}/original.jpg").exists())
            diagnostics = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=root / "wrecks",
                private_photos_dir=private_dir,
            )
            self.assertEqual(diagnostics["status"], "ok")

    def test_skips_recent_or_pending_private_originals(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "field"
            private_dir = root / "private"
            recent_id = "photo_20260501T100000Z_abcdef12"
            pending_id = "photo_20251101T100000Z_abcdef12"
            for photo_id, record in [
                (recent_id, field_record(recent_id, reviewed_at=RECENT_REVIEWED_AT)),
                (pending_id, field_record(pending_id, status="pending", reviewed_at="")),
            ]:
                record_dir = field_dir / photo_id
                if record["public_review_status"] == "approved":
                    record["public_image_file"] = "public.jpg"
                    record["public_thumb_file"] = "public_thumb.jpg"
                    write_jpeg(record_dir / "public.jpg")
                    write_jpeg(record_dir / "public_thumb.jpg", size=(24, 16))
                write_json(record_dir / "record.json", record)
                write_jpeg(private_dir / record["private_original_file"])

            report = retire_private_originals(
                field_photos_dir=field_dir,
                wrecks_dir=root / "wrecks",
                private_photos_dir=private_dir,
                now=NOW,
                dry_run=False,
            )

            self.assertEqual(report["field_photos"]["replaced"], 0)
            self.assertEqual(report["field_photos"]["deleted"], 0)
            self.assertTrue((private_dir / f"field_photos/{recent_id}/original.jpg").exists())
            self.assertTrue((private_dir / f"field_photos/{pending_id}/original.jpg").exists())

    def test_replaces_old_approved_wreck_photo_in_main_and_photo_records(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            wrecks_dir = root / "wrecks"
            private_dir = root / "private"
            wreck_id = "wreck_51100000_17200000"
            photo_id = "photo_20251101T100000Z_abcdef12"
            record_dir = wrecks_dir / wreck_id
            photo = {
                "id": photo_id,
                "created_at": "2025-11-01T10:00:00Z",
                "original_filename": "miejsce.jpg",
                "content_type": "image/jpeg",
                "format": "JPEG",
                "size_bytes": 100,
                "image_width": 48,
                "image_height": 32,
                "private_original_file": f"wreck_photos/{wreck_id}/{photo_id}/original.jpg",
                "public_review_status": "approved",
                "redactions": [],
                "reviewed_at": OLD_REVIEWED_AT,
                "public_image_file": f"photos/{photo_id}/public.jpg",
                "public_thumb_file": f"photos/{photo_id}/public_thumb.jpg",
            }
            wreck = {
                "id": wreck_id,
                "status": "confirmed",
                "lat": 51.1,
                "lon": 17.2,
                "updated_at": "2025-12-01T10:00:00Z",
                "evidences": [],
                "attached_photos": [dict(photo)],
            }
            write_json(record_dir / "record.json", wreck)
            write_json(record_dir / "photos" / photo_id / "record.json", photo)
            write_jpeg(private_dir / photo["private_original_file"], color=(255, 0, 0))
            write_jpeg(record_dir / "photos" / photo_id / "public.jpg", color=(15, 23, 42))
            write_jpeg(record_dir / "photos" / photo_id / "public_thumb.jpg", size=(24, 16), color=(15, 23, 42))

            report = retire_private_originals(
                field_photos_dir=root / "field",
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
                now=NOW,
                dry_run=False,
            )

            self.assertEqual(report["wreck_photos"]["replaced"], 1)
            main_record = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            photo_record = json.loads((record_dir / "photos" / photo_id / "record.json").read_text(encoding="utf-8"))
            expected_private = f"wreck_photos/{wreck_id}/{photo_id}/retained_public.jpg"
            self.assertEqual(main_record["attached_photos"][0]["private_original_file"], expected_private)
            self.assertEqual(photo_record["private_original_file"], expected_private)
            self.assertEqual(main_record["updated_at"], "2026-06-05T12:00:00Z")
            self.assertFalse((private_dir / f"wreck_photos/{wreck_id}/{photo_id}/original.jpg").exists())
            self.assertTrue((private_dir / expected_private).exists())
