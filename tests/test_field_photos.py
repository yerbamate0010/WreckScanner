import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import ExifTags, Image, TiffImagePlugin

from core.config import MAX_FIELD_PHOTO_BYTES
from core.field_photos import field_photo_asset, list_field_photos, review_field_photo, save_field_photo
from core.uploads import UploadedFile


def image_bytes(fmt: str = "JPEG", gps: tuple[float, float] | None = None) -> bytes:
    out = io.BytesIO()
    image = Image.new("RGB", (48, 32), (70, 120, 160))
    if gps and fmt.upper() == "JPEG":
        lat, lon = gps
        exif = Image.Exif()
        exif[ExifTags.IFD.GPSInfo] = {
            1: "S" if lat < 0 else "N",
            2: _dms(abs(lat)),
            3: "W" if lon < 0 else "E",
            4: _dms(abs(lon)),
        }
        image.save(out, fmt, exif=exif)
    else:
        image.save(out, fmt)
    return out.getvalue()


def image_bytes_with_invalid_gps() -> bytes:
    out = io.BytesIO()
    exif = Image.Exif()
    exif[ExifTags.IFD.GPSInfo] = {
        1: "N",
        2: (
            TiffImagePlugin.IFDRational(51, 1),
            TiffImagePlugin.IFDRational(1, 0),
            TiffImagePlugin.IFDRational(0, 1),
        ),
        3: "E",
        4: (
            TiffImagePlugin.IFDRational(17, 1),
            TiffImagePlugin.IFDRational(0, 1),
            TiffImagePlugin.IFDRational(0, 1),
        ),
    }
    Image.new("RGB", (48, 32), (70, 120, 160)).save(out, "JPEG", exif=exif)
    return out.getvalue()


def _dms(value: float):
    degrees = int(value)
    minutes_float = (value - degrees) * 60
    minutes = int(minutes_float)
    seconds = round((minutes_float - minutes) * 60, 4)
    return (
        TiffImagePlugin.IFDRational(degrees, 1),
        TiffImagePlugin.IFDRational(minutes, 1),
        TiffImagePlugin.IFDRational(int(seconds * 10_000), 10_000),
    )


def upload(data: bytes, filename: str = "teren.jpg", field_name: str = "photo") -> UploadedFile:
    return UploadedFile(field_name=field_name, filename=filename, content_type="image/jpeg", data=data)


class FieldPhotoTests(unittest.TestCase):
    def test_save_field_photo_uses_exif_gps_before_map_fallback(self):
        with TemporaryDirectory() as tmp:
            result = save_field_photo(
                upload(image_bytes(gps=(51.1, 17.2))),
                Path(tmp),
                fallback_lat=1.0,
                fallback_lon=2.0,
                private_dir=Path(tmp) / "private",
            )

            photo = result["photo"]
            self.assertEqual(photo["coordinate_source"], "exif")
            self.assertAlmostEqual(photo["lat"], 51.1, places=5)
            self.assertAlmostEqual(photo["lon"], 17.2, places=5)

    def test_save_field_photo_can_ignore_exif_gps_and_use_map_fallback(self):
        with TemporaryDirectory() as tmp:
            result = save_field_photo(
                upload(image_bytes(gps=(51.1, 17.2))),
                Path(tmp),
                fallback_lat="51.3",
                fallback_lon="17.4",
                ignore_exif_gps=True,
                private_dir=Path(tmp) / "private",
            )

            photo = result["photo"]
            self.assertEqual(photo["coordinate_source"], "map")
            self.assertAlmostEqual(photo["lat"], 51.3, places=5)
            self.assertAlmostEqual(photo["lon"], 17.4, places=5)

    def test_save_field_photo_uses_map_fallback_without_exif_gps(self):
        with TemporaryDirectory() as tmp:
            private_dir = Path(tmp) / "private"
            result = save_field_photo(
                upload(image_bytes("PNG"), filename="teren.png"),
                Path(tmp),
                fallback_lat="51.3",
                fallback_lon="17.4",
                private_dir=private_dir,
            )

            photo = result["photo"]
            record_dir = Path(tmp) / photo["id"]
            record = json.loads((record_dir / "record.json").read_text(encoding="utf-8"))
            self.assertEqual(photo["coordinate_source"], "map")
            self.assertEqual(photo["issue_type"], "vehicle")
            self.assertEqual(record["public_review_status"], "pending")
            self.assertEqual(record["private_original_file"], f"field_photos/{photo['id']}/original.png")
            self.assertNotIn("original_file", record)
            self.assertTrue((private_dir / record["private_original_file"]).exists())
            self.assertEqual(record["issue_type"], "vehicle")
            self.assertFalse((record_dir / "public.jpg").exists())
            self.assertEqual(list_field_photos(Path(tmp), private_dir=private_dir), [])

    def test_review_field_photo_generates_redacted_public_copy_without_exif(self):
        with TemporaryDirectory() as tmp:
            private_dir = Path(tmp) / "private"
            result = save_field_photo(
                upload(image_bytes(gps=(51.1, 17.2))),
                Path(tmp),
                fallback_lat="51.3",
                fallback_lon="17.4",
                private_dir=private_dir,
            )
            photo_id = result["photo"]["id"]

            review_field_photo(
                photo_id,
                Path(tmp),
                status="approved",
                redactions=[{"x": 0, "y": 0, "width": 0.5, "height": 0.5}],
                private_dir=private_dir,
            )

            public_list = list_field_photos(Path(tmp), private_dir=private_dir)
            self.assertEqual(len(public_list), 1)
            record = json.loads((Path(tmp) / photo_id / "record.json").read_text(encoding="utf-8"))
            self.assertEqual(list(record["redactions"][0]), ["points"])
            self.assertEqual(len(record["redactions"][0]["points"]), 4)
            self.assertIn("public_image", public_list[0])
            self.assertIn("public_thumb", public_list[0])
            self.assertNotIn("original_url", public_list[0])
            public_path, public_type = field_photo_asset(photo_id, Path(tmp), "public-image", private_dir=private_dir)
            thumb_path, _ = field_photo_asset(photo_id, Path(tmp), "public-thumb", private_dir=private_dir)
            original_path, _ = field_photo_asset(photo_id, Path(tmp), "original", private_dir=private_dir)
            self.assertEqual(public_type, "image/jpeg")
            self.assertTrue(public_path.exists())
            self.assertTrue(thumb_path.exists())
            self.assertTrue(original_path.exists())
            with Image.open(public_path) as public:
                self.assertEqual(public.getexif(), {})
                self.assertNotEqual(public.getpixel((1, 1)), (70, 120, 160))

    def test_save_field_photo_stores_supported_issue_type(self):
        with TemporaryDirectory() as tmp:
            result = save_field_photo(
                upload(image_bytes("PNG"), filename="teren.png"),
                Path(tmp),
                fallback_lat="51.3",
                fallback_lon="17.4",
                issue_type="infrastructure",
                private_dir=Path(tmp) / "private",
            )

            photo = result["photo"]
            record = json.loads((Path(tmp) / photo["id"] / "record.json").read_text(encoding="utf-8"))
            self.assertEqual(photo["issue_type"], "infrastructure")
            self.assertEqual(record["issue_type"], "infrastructure")

    def test_save_field_photo_rejects_unknown_issue_type(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "typ pinezki"):
                save_field_photo(
                    upload(image_bytes("PNG"), filename="teren.png"),
                    Path(tmp),
                    fallback_lat="51.3",
                    fallback_lon="17.4",
                    issue_type="other",
                    private_dir=Path(tmp) / "private",
                )

    def test_save_field_photo_uses_map_fallback_for_invalid_exif_gps(self):
        with TemporaryDirectory() as tmp:
            result = save_field_photo(
                upload(image_bytes_with_invalid_gps()),
                Path(tmp),
                fallback_lat="51.3",
                fallback_lon="17.4",
                private_dir=Path(tmp) / "private",
            )

            photo = result["photo"]
            self.assertEqual(photo["coordinate_source"], "map")
            self.assertEqual(photo["lat"], 51.3)
            self.assertEqual(photo["lon"], 17.4)

    def test_save_field_photo_rejects_missing_coordinates(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "nie ma współrzędnych GPS"):
                save_field_photo(
                    upload(image_bytes("PNG"), filename="teren.png"), Path(tmp), private_dir=Path(tmp) / "private"
                )

    def test_save_field_photo_validates_size_type_and_field(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "10 MB"):
                save_field_photo(
                    upload(b"x" * (MAX_FIELD_PHOTO_BYTES + 1), "big.jpg"),
                    Path(tmp),
                    private_dir=Path(tmp) / "private",
                )

            with self.assertRaisesRegex(ValueError, "Dozwolone"):
                save_field_photo(
                    upload(image_bytes("GIF"), "teren.gif"),
                    Path(tmp),
                    fallback_lat=51,
                    fallback_lon=17,
                    private_dir=Path(tmp) / "private",
                )

            with self.assertRaisesRegex(ValueError, "photo"):
                save_field_photo(
                    upload(image_bytes("JPEG"), field_name="photos[]"),
                    Path(tmp),
                    fallback_lat=51,
                    fallback_lon=17,
                    private_dir=Path(tmp) / "private",
                )

            with self.assertRaisesRegex(ValueError, "obsługiwanym zdjęciem"):
                save_field_photo(
                    upload(b"not an image", "bad.jpg"),
                    Path(tmp),
                    fallback_lat=51,
                    fallback_lon=17,
                    private_dir=Path(tmp) / "private",
                )


if __name__ == "__main__":
    unittest.main()
