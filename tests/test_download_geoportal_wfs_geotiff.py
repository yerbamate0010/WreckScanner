import os
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from PIL import Image

from scripts.download_geoportal_wfs_geotiff import (
    OrthoSheet,
    TifDownloadResult,
    choose_rgb_sheet,
    cleanup_geotiff_cache,
    download_tif,
    local_tif_path,
    partial_tif_path,
)


def tiff_bytes(color=(12, 34, 56)):
    buf = BytesIO()
    Image.new("RGB", (2, 2), color=color).save(buf, format="TIFF")
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.body = body
        self.headers = {"Content-Length": str(len(body))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size):
        del chunk_size
        yield self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.headers_seen = []

    def get(self, _url, headers=None, stream=False, timeout=None):
        del stream, timeout
        self.headers_seen.append(headers)
        status_code, body = self.responses.pop(0)
        return FakeResponse(status_code, body)


def make_sheet(**overrides):
    values = {
        "year": 2025,
        "feature_id": "fid",
        "godlo": "GODLO",
        "pixel_m": 0.05,
        "color": "RGB",
        "source": None,
        "layout": None,
        "archive_module": None,
        "report_id": None,
        "acquisition_date": None,
        "pzgik_date": None,
        "filled": None,
        "download_url": "https://example.test/file.tif",
        "file_size_mb": 10,
    }
    values.update(overrides)
    return OrthoSheet(
        **values,
    )


class ChooseRgbSheetTests(unittest.TestCase):
    def test_selects_best_filled_rgb_sheet_by_pixel_then_newest_date(self):
        sheets = [
            make_sheet(feature_id="cir", color="CIR", filled="TAK", pixel_m=0.03),
            make_sheet(feature_id="empty", filled="NIE", pixel_m=0.03),
            make_sheet(feature_id="older", filled="TAK", pixel_m=0.05, acquisition_date="2024-01-01"),
            make_sheet(feature_id="newer", filled="TAK", pixel_m=0.05, acquisition_date="2025-01-01"),
            make_sheet(feature_id="best", filled="TAK", pixel_m=0.04, acquisition_date="2023-01-01"),
        ]

        selected = choose_rgb_sheet(sheets)

        self.assertIsNotNone(selected)
        self.assertEqual(selected.feature_id, "best")

    def test_returns_none_without_downloadable_filled_rgb_sheet(self):
        sheets = [
            make_sheet(color="CIR", filled="TAK"),
            make_sheet(color="RGB", filled="NIE"),
            make_sheet(color="RGB", filled="TAK", download_url=None),
        ]

        self.assertIsNone(choose_rgb_sheet(sheets))


class DownloadTifTests(unittest.TestCase):
    def run_case(self, *, initial_part=None, final_tif=None, responses=()):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            raw_dir.mkdir(parents=True, exist_ok=True)
            sheet = make_sheet()
            tif_path = local_tif_path(sheet, raw_dir)
            if final_tif is not None:
                tif_path.write_bytes(final_tif)
            if initial_part is not None:
                partial_tif_path(tif_path).write_bytes(initial_part)

            events = []
            session = FakeSession(responses)
            result = download_tif(
                sheet,
                raw_dir,
                session,
                timeout=1,
                progress=lambda *args, **kwargs: events.append((args, kwargs)),
            )
            return result, result.path.read_bytes(), session.headers_seen, events

    def test_existing_tif_is_cache_hit(self):
        cached = tiff_bytes()
        result, body, headers_seen, events = self.run_case(final_tif=cached)

        self.assertEqual(result.cache, "hit")
        self.assertEqual(body, cached)
        self.assertEqual(headers_seen, [])
        self.assertEqual(events, [])

    def test_fresh_download(self):
        downloaded = tiff_bytes()
        result, body, headers_seen, events = self.run_case(responses=[(200, downloaded)])

        self.assertEqual(result.cache, "downloaded")
        self.assertEqual(body, downloaded)
        self.assertEqual(headers_seen, [None])
        self.assertEqual(events[-1][1]["resumed"], False)
        self.assertEqual(events[-1][1]["restarted"], False)

    def test_partial_download_resumes_on_206(self):
        downloaded = tiff_bytes()
        initial_part = downloaded[:32]
        result, body, headers_seen, events = self.run_case(
            initial_part=initial_part,
            responses=[(206, downloaded[32:])],
        )

        self.assertEqual(result.cache, "resumed")
        self.assertEqual(result.resume_from, 32)
        self.assertEqual(body, downloaded)
        self.assertEqual(headers_seen, [{"Range": "bytes=32-"}])
        self.assertEqual(events[-1][1]["resumed"], True)
        self.assertEqual(events[-1][1]["restarted"], False)

    def test_partial_download_restarts_when_range_is_ignored(self):
        downloaded = tiff_bytes()
        result, body, headers_seen, events = self.run_case(
            initial_part=b"old",
            responses=[(200, downloaded)],
        )

        self.assertEqual(result.cache, "restarted")
        self.assertEqual(result.resume_from, 0)
        self.assertEqual(body, downloaded)
        self.assertEqual(headers_seen, [{"Range": "bytes=3-"}])
        self.assertEqual(events[-1][1]["resumed"], False)
        self.assertEqual(events[-1][1]["restarted"], True)

    def test_partial_download_restarts_after_416(self):
        downloaded = tiff_bytes()
        result, body, headers_seen, events = self.run_case(
            initial_part=b"old",
            responses=[(416, b""), (200, downloaded)],
        )

        self.assertEqual(result.cache, "restarted")
        self.assertEqual(result.resume_from, 0)
        self.assertEqual(body, downloaded)
        self.assertEqual(headers_seen, [{"Range": "bytes=3-"}, None])
        self.assertEqual(events[-1][1]["resumed"], False)
        self.assertEqual(events[-1][1]["restarted"], True)

    def test_existing_markup_tif_is_discarded_and_redownloaded(self):
        downloaded = tiff_bytes(color=(90, 80, 70))
        result, body, headers_seen, _events = self.run_case(
            final_tif=b"<!doctype html><title>error</title>",
            responses=[(200, downloaded)],
        )

        self.assertEqual(result.cache, "downloaded")
        self.assertEqual(body, downloaded)
        self.assertEqual(headers_seen, [None])

    def test_markup_download_is_rejected_and_part_removed(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            sheet = make_sheet()
            tif_path = local_tif_path(sheet, raw_dir)
            session = FakeSession([(200, b"<!doctype html><title>backend error</title>")])

            with self.assertRaises(ValueError):
                download_tif(sheet, raw_dir, session, timeout=1)

            self.assertFalse(tif_path.exists())
            self.assertFalse(partial_tif_path(tif_path).exists())

    def test_xml_service_exception_download_is_rejected(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            sheet = make_sheet()
            session = FakeSession([(200, b"<?xml version='1.0'?><ServiceException>bad bbox</ServiceException>")])

            with self.assertRaises(ValueError):
                download_tif(sheet, raw_dir, session, timeout=1)


class CleanupGeotiffCacheTests(unittest.TestCase):
    def write_file(self, path, body, mtime):
        path.write_bytes(body)
        path.touch()
        os.utime(path, (mtime, mtime))

    def test_removes_oldest_completed_maps_after_limit(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            oldest = raw_dir / "oldest.tif"
            middle = raw_dir / "middle.tif"
            newest = raw_dir / "newest.tif"
            self.write_file(oldest, b"a" * 4, 100)
            self.write_file(middle, b"b" * 4, 200)
            self.write_file(newest, b"c" * 4, 300)

            report = cleanup_geotiff_cache(raw_dir, max_bytes=8)

            self.assertFalse(oldest.exists())
            self.assertTrue(middle.exists())
            self.assertTrue(newest.exists())
            self.assertEqual([item["reason"] for item in report["removed"]], ["cache_limit"])
            self.assertEqual(report["after"]["completed_bytes"], 8)

    def test_keeps_current_map_even_when_cache_is_over_limit(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            old = raw_dir / "old.tif"
            keep = raw_dir / "keep.tif"
            self.write_file(old, b"a" * 4, 100)
            self.write_file(keep, b"b" * 4, 200)

            report = cleanup_geotiff_cache(raw_dir, max_bytes=1, keep_paths=[keep])

            self.assertFalse(old.exists())
            self.assertTrue(keep.exists())
            self.assertEqual(report["after"]["completed_bytes"], 4)

    def test_no_limit_keeps_completed_maps_but_removes_stale_partials(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            raw_dir.mkdir(parents=True, exist_ok=True)
            completed = raw_dir / "completed.tif"
            stale_part = raw_dir / "stale.tif.part"
            self.write_file(completed, b"a" * 4, 100)
            self.write_file(stale_part, b"b" * 4, 100)

            report = cleanup_geotiff_cache(raw_dir, max_bytes=None, stale_part_seconds=1)

            self.assertTrue(completed.exists())
            self.assertFalse(stale_part.exists())
            self.assertEqual([item["reason"] for item in report["removed"]], ["stale_partial"])
            self.assertEqual(report["after"]["completed_bytes"], 4)

    def test_keeps_active_partial_download_even_when_stale(self):
        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp)
            raw_dir.mkdir(parents=True, exist_ok=True)
            active_part = raw_dir / "active.tif.part"
            self.write_file(active_part, b"partial", 100)

            report = cleanup_geotiff_cache(
                raw_dir,
                max_bytes=1,
                keep_paths=[active_part],
                stale_part_seconds=1,
            )

            self.assertTrue(active_part.exists())
            self.assertEqual(report["removed"], [])


class ApplyWfsGeotiffReplacementTests(unittest.TestCase):
    def test_download_progress_callback_keeps_own_year_sheet_and_estimated_size(self):
        from app import map_downloads

        with TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            data_dir = Path(tmp) / "data"
            raw_dir.mkdir()
            data_dir.mkdir()
            sheets_by_year = {
                2024: [make_sheet(year=2024, feature_id="a", godlo="SHEET-A", file_size_mb=1.5, filled="TAK")],
                2025: [make_sheet(year=2025, feature_id="b", godlo="SHEET-B", file_size_mb=2.5, filled="TAK")],
            }
            delayed_callbacks = []
            progress_events = []

            def fake_download_tif(sheet, raw_dir_arg, _session, _timeout, progress=None):
                tif_path = raw_dir_arg / f"{sheet.year}.tif"
                tif_path.write_bytes(tiff_bytes())
                delayed_callbacks.append(progress)
                return TifDownloadResult(
                    path=tif_path,
                    cache="downloaded",
                    resume_from=0,
                    bytes_done=tif_path.stat().st_size,
                    bytes_total=tif_path.stat().st_size,
                )

            def fake_crop_geotiff_to_png(_tif_path, png_path, _metadata):
                png_path.parent.mkdir(parents=True, exist_ok=True)
                png_path.write_bytes(b"png")
                return {"status": "ok"}

            with (
                patch.object(map_downloads.config, "WFS_GEOTIFF_YEARS", [2024, 2025]),
                patch.object(map_downloads.config, "WFS_GEOTIFF_CACHE_DIR", raw_dir),
                patch.object(map_downloads.config, "DOWNLOAD_DATA_DIR", data_dir),
                patch.object(map_downloads, "get_http_session", return_value=object()),
                patch.object(map_downloads, "wfs_bbox_from_metadata", return_value="bbox"),
                patch.object(map_downloads, "query_wfs_sheets", side_effect=lambda year, *_args: sheets_by_year[year]),
                patch.object(map_downloads, "download_tif", side_effect=fake_download_tif),
                patch.object(map_downloads, "crop_geotiff_to_png", side_effect=fake_crop_geotiff_to_png),
                patch.object(map_downloads, "image_quality_for_path", return_value={"quality": "ok"}),
                patch.object(map_downloads, "geotiff_cache_report", return_value={"status": "ok"}),
            ):
                summary = map_downloads.apply_wfs_geotiff_replacements(
                    {"bbox_4326": {}},
                    {2024: {}, 2025: {}},
                    progress=lambda **event: progress_events.append(event),
                )

            self.assertEqual([item["status"] for item in summary], ["replaced", "replaced"])
            self.assertEqual(len(delayed_callbacks), 2)

            delayed_callbacks[0](done=123, total=0, resume_from=32, resumed=True)

            delayed_event = progress_events[-1]
            self.assertEqual(delayed_event["year"], 2024)
            self.assertEqual(delayed_event["cache"], "partial")
            self.assertEqual(delayed_event["resume_from"], 32)
            self.assertEqual(delayed_event["bytes_done"], 123)
            self.assertEqual(delayed_event["bytes_total"], int(1.5 * map_downloads.BYTES_PER_MIB))
            self.assertIn("SHEET-A", delayed_event["message"])
            self.assertNotIn("SHEET-B", delayed_event["message"])


if __name__ == "__main__":
    unittest.main()
