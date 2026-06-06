import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from core.data_diagnostics import format_data_diagnostics, run_data_diagnostics

ROOT_DIR = Path(__file__).resolve().parent.parent


def write_json(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def write_image(path: Path, size: tuple[int, int] = (48, 32)) -> None:
    out = io.BytesIO()
    Image.new("RGB", size, (80, 120, 160)).save(out, "JPEG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out.getvalue())


def write_field_photo_record(
    field_dir: Path,
    private_dir: Path,
    photo_id: str = "photo_20260604T200730Z_37885295",
    *,
    issue_type: str | None = "vehicle",
    private_original_file: str | None = None,
    public_review_status: str = "approved",
    public_image_file: str | None = "public.jpg",
    public_thumb_file: str | None = "public_thumb.jpg",
    image_size: tuple[int, int] = (48, 32),
    thumb_size: tuple[int, int] = (36, 24),
    write_private: bool = True,
) -> Path:
    record_dir = field_dir / photo_id
    private_original = private_original_file or f"field_photos/{photo_id}/original.jpg"
    if write_private and not private_original.startswith("../") and "\\" not in private_original:
        write_image(private_dir / private_original, image_size)
    if public_review_status == "approved" and public_image_file and "../" not in public_image_file:
        write_image(record_dir / public_image_file, image_size)
    if public_review_status == "approved" and public_thumb_file and "../" not in public_thumb_file:
        write_image(record_dir / public_thumb_file, thumb_size)
    record = {
        "id": photo_id,
        "created_at": "2026-06-04T20:07:30Z",
        "original_filename": "teren.jpg",
        "content_type": "image/jpeg",
        "format": "JPEG",
        "size_bytes": 123,
        "image_width": image_size[0],
        "image_height": image_size[1],
        "lat": 51.1,
        "lon": 17.2,
        "coordinate_source": "map",
        "private_original_file": private_original,
        "public_review_status": public_review_status,
        "redactions": [],
        "reviewed_at": "2026-06-04T20:08:00Z" if public_review_status == "approved" else None,
    }
    if public_image_file:
        record["public_image_file"] = public_image_file
    if public_thumb_file:
        record["public_thumb_file"] = public_thumb_file
    if issue_type is not None:
        record["issue_type"] = issue_type
    write_json(record_dir / "record.json", record)
    return record_dir


def write_wreck_with_attached_photo(
    wrecks_dir: Path,
    private_dir: Path,
    wreck_id: str = "wreck_51100000_17200000",
    *,
    photo_id: str = "photo_20260604T201000Z_11111111",
    issue_type: str = "vehicle",
    private_original_file: str | None = None,
    public_review_status: str = "approved",
    public_image_file: str | None = None,
    public_thumb_file: str | None = None,
    write_index: bool = True,
) -> Path:
    record_dir = wrecks_dir / wreck_id
    private_original = private_original_file or f"wreck_photos/{wreck_id}/{photo_id}/original.jpg"
    public_image = public_image_file or f"photos/{photo_id}/public.jpg"
    public_thumb = public_thumb_file or f"photos/{photo_id}/public_thumb.jpg"
    if not private_original.startswith("../") and "\\" not in private_original:
        write_image(private_dir / private_original, (64, 48))
    if public_review_status == "approved" and public_image and "../" not in public_image:
        write_image(record_dir / public_image, (64, 48))
    if public_review_status == "approved" and public_thumb and "../" not in public_thumb:
        write_image(record_dir / public_thumb, (40, 30))
    write_json(record_dir / f"photos/{photo_id}/record.json", {"id": photo_id})
    if write_index:
        (record_dir / "index.html").parent.mkdir(parents=True, exist_ok=True)
        (record_dir / "index.html").write_text("<html></html>", encoding="utf-8")
    write_json(
        record_dir / "record.json",
        {
            "id": wreck_id,
            "lat": 51.1,
            "lon": 17.2,
            "evidences": [],
            "attached_photos": [
                {
                    "id": photo_id,
                    "issue_type": issue_type,
                    "private_original_file": private_original,
                    "public_review_status": public_review_status,
                    "redactions": [],
                    "reviewed_at": "2026-06-04T20:11:00Z" if public_review_status == "approved" else None,
                    "public_image_file": public_image,
                    "public_thumb_file": public_thumb,
                }
            ],
        },
    )
    return record_dir


class DataDiagnosticsTests(unittest.TestCase):
    def test_run_data_diagnostics_reports_healthy_field_photos_and_wrecks(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "zdjecia_terenowe"
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_dir = root / "prywatne_zdjecia"
            private_dir = root / "prywatne_zdjecia"

            write_field_photo_record(field_dir, private_dir, issue_type=None)
            write_field_photo_record(
                field_dir,
                private_dir,
                "photo_20260604T200810Z_6b21b28a",
                issue_type="smoke",
                image_size=(50, 40),
                thumb_size=(36, 28),
            )

            wreck_dir = wrecks_dir / "wreck_51100000_17200000"
            manual_evidence_dir = wreck_dir / "evidence" / "manual_11111111111111"
            write_json(manual_evidence_dir / "manual_inspection.json", {"source": "manual_inspection"})
            write_json(manual_evidence_dir / "links.json", {"geoportal": "https://example.test/geo"})
            write_wreck_with_attached_photo(wrecks_dir, private_dir)
            record = json.loads((wreck_dir / "record.json").read_text(encoding="utf-8"))
            record["evidences"] = [
                {
                    "id": "manual_11111111111111",
                    "source": "manual_inspection",
                    "path": "evidence/manual_11111111111111",
                    "crops": [],
                }
            ]
            write_json(wreck_dir / "record.json", record)

            report = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
            )

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["summary"]["field_photos"]["records"], 2)
            self.assertEqual(report["summary"]["field_photos"]["legacy_without_issue_type"], 1)
            self.assertEqual(report["summary"]["field_photos"]["issue_types"]["vehicle"], 1)
            self.assertEqual(report["summary"]["field_photos"]["issue_types"]["smoke"], 1)
            self.assertEqual(report["summary"]["wrecks"]["attached_photos"], 1)
            self.assertIn("Zdjęcia terenowe: 2 rekordów", format_data_diagnostics(report))

    def test_run_data_diagnostics_finds_corrupt_records_and_missing_files(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "zdjecia_terenowe"
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_dir = root / "prywatne_zdjecia"

            broken_field = field_dir / "photo_20260604T200730Z_37885295"
            write_json(
                broken_field / "record.json",
                {
                    "id": broken_field.name,
                    "issue_type": "unknown",
                    "lat": 999,
                    "lon": 17.2,
                    "private_original_file": "field_photos/photo_20260604T200730Z_37885295/missing.jpg",
                    "public_review_status": "approved",
                    "redactions": [],
                    "public_image_file": "public.jpg",
                    "public_thumb_file": "public_thumb.jpg",
                },
            )
            (wrecks_dir / "wreck_51100000_17200000").mkdir(parents=True)

            report = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
                check_images=False,
            )

            self.assertEqual(report["status"], "error")
            codes = {issue["code"] for issue in report["issues"]}
            self.assertIn("field_photo_bad_issue_type", codes)
            self.assertIn("field_photo_bad_coordinates", codes)
            self.assertIn("field_photo_private_original_missing", codes)
            self.assertIn("wreck_record_missing", codes)

    def test_run_data_diagnostics_finds_unsafe_paths_and_missing_wreck_index(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "zdjecia_terenowe"
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_dir = root / "prywatne_zdjecia"

            write_field_photo_record(field_dir, private_dir, private_original_file="../outside.jpg")
            write_wreck_with_attached_photo(
                wrecks_dir,
                private_dir,
                private_original_file="../outside.jpg",
                write_index=False,
            )

            report = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
                check_images=False,
            )

            self.assertEqual(report["status"], "error")
            codes = {issue["code"] for issue in report["issues"]}
            self.assertIn("field_photo_unsafe_private_original_path", codes)
            self.assertIn("wreck_attached_photo_unsafe_private_original_path", codes)
            self.assertIn("wreck_index_missing", codes)

    def test_run_data_diagnostics_finds_unreadable_images_size_mismatch_and_large_thumbnail(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "zdjecia_terenowe"
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_dir = root / "prywatne_zdjecia"

            record_dir = write_field_photo_record(field_dir, private_dir, image_size=(48, 32), thumb_size=(480, 320))
            private_original = private_dir / f"field_photos/{record_dir.name}/original.jpg"
            private_original.write_bytes(b"not an image")
            write_json(
                record_dir / "record.json",
                {
                    "id": record_dir.name,
                    "issue_type": "vehicle",
                    "lat": 51.1,
                    "lon": 17.2,
                    "private_original_file": f"field_photos/{record_dir.name}/original.jpg",
                    "public_review_status": "approved",
                    "redactions": [],
                    "public_image_file": "public.jpg",
                    "public_thumb_file": "public_thumb.jpg",
                    "image_width": 64,
                    "image_height": 64,
                },
            )
            mismatch_dir = write_field_photo_record(
                field_dir,
                private_dir,
                "photo_20260604T200810Z_6b21b28a",
                image_size=(50, 40),
                thumb_size=(36, 28),
            )
            mismatch_record = json.loads((mismatch_dir / "record.json").read_text(encoding="utf-8"))
            mismatch_record["image_width"] = 99
            mismatch_record["image_height"] = 88
            write_json(mismatch_dir / "record.json", mismatch_record)

            report = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
            )

            codes = {issue["code"] for issue in report["issues"]}
            self.assertEqual(report["status"], "error")
            self.assertIn("image_unreadable", codes)
            self.assertIn("field_photo_public_thumb_too_large", codes)
            self.assertIn("field_photo_size_mismatch", codes)

    def test_run_data_diagnostics_finds_attached_photo_duplicates_and_wrong_type(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "zdjecia_terenowe"
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_dir = root / "prywatne_zdjecia"
            photo_id = "photo_20260604T201000Z_11111111"

            write_field_photo_record(field_dir, private_dir, photo_id=photo_id)
            write_wreck_with_attached_photo(
                wrecks_dir, private_dir, "wreck_51100000_17200000", photo_id=photo_id, issue_type="smoke"
            )
            write_wreck_with_attached_photo(wrecks_dir, private_dir, "wreck_51100001_17200001", photo_id=photo_id)

            report = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
                check_images=False,
            )

            codes = {issue["code"] for issue in report["issues"]}
            self.assertEqual(report["status"], "error")
            self.assertIn("attached_photo_still_loose", codes)
            self.assertIn("wreck_attached_photo_duplicate", codes)
            self.assertIn("wreck_attached_photo_bad_issue_type", codes)

    def test_run_data_diagnostics_finds_unsafe_evidence_paths_and_missing_metadata(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "zdjecia_terenowe"
            wrecks_dir = root / "zidentyfikowane_wraki"
            private_dir = root / "prywatne_zdjecia"
            wreck_dir = wrecks_dir / "wreck_51100000_17200000"
            (wreck_dir / "index.html").parent.mkdir(parents=True, exist_ok=True)
            (wreck_dir / "index.html").write_text("<html></html>", encoding="utf-8")
            evidence_dir = wreck_dir / "evidence" / "auto_11111111111111"
            evidence_dir.mkdir(parents=True)
            write_json(evidence_dir / "candidate.json", {"rank": 1})
            write_json(evidence_dir / "links.json", {})
            write_json(
                wreck_dir / "record.json",
                {
                    "id": wreck_dir.name,
                    "lat": 51.1,
                    "lon": 17.2,
                    "evidences": [
                        {"id": "bad", "path": "../outside", "crops": []},
                        {
                            "id": "auto_11111111111111",
                            "path": "evidence/auto_11111111111111",
                            "crops": [{"label": "2025", "file": "../crop.jpg"}],
                        },
                    ],
                    "attached_photos": [],
                },
            )

            report = run_data_diagnostics(
                field_photos_dir=field_dir,
                wrecks_dir=wrecks_dir,
                private_photos_dir=private_dir,
                check_images=False,
            )

            codes = {issue["code"] for issue in report["issues"]}
            self.assertEqual(report["status"], "error")
            self.assertIn("wreck_evidence_unsafe_path", codes)
            self.assertIn("wreck_evidence_metadata_missing", codes)
            self.assertIn("wreck_evidence_crop_unsafe_path", codes)

    def test_diagnose_data_script_returns_error_for_broken_data(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "field"
            wrecks_dir = root / "wrecks"
            (field_dir / "photo_20260604T200730Z_37885295").mkdir(parents=True)
            wrecks_dir.mkdir()

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "diagnose_data.py"),
                    "--field-photos-dir",
                    str(field_dir),
                    "--wrecks-dir",
                    str(wrecks_dir),
                    "--no-image-check",
                ],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("field_photo_record_missing", result.stdout)

    def test_diagnose_data_script_writes_json_and_strict_fails_on_warnings(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            field_dir = root / "missing-field"
            wrecks_dir = root / "missing-wrecks"
            output_path = root / "diagnostics.json"

            non_strict = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "diagnose_data.py"),
                    "--field-photos-dir",
                    str(field_dir),
                    "--wrecks-dir",
                    str(wrecks_dir),
                    "--json",
                    "--output-json",
                    str(output_path),
                    "--no-image-check",
                ],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )
            strict = subprocess.run(
                [
                    sys.executable,
                    str(ROOT_DIR / "scripts" / "diagnose_data.py"),
                    "--field-photos-dir",
                    str(field_dir),
                    "--wrecks-dir",
                    str(wrecks_dir),
                    "--strict",
                    "--no-image-check",
                ],
                cwd=ROOT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )

            stdout_report = json.loads(non_strict.stdout)
            file_report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(non_strict.returncode, 0)
            self.assertEqual(stdout_report["status"], "warning")
            self.assertEqual(file_report["status"], "warning")
            self.assertEqual(strict.returncode, 1)
            self.assertIn("field_photos_dir_missing", strict.stdout)


if __name__ == "__main__":
    unittest.main()
