import contextlib
import io
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app import wms_cache
from core.config import DEFAULT_ENHANCEMENT_SETTINGS
from core.enhancement import enhance_image_items
from core.models import ImageItem
from core.scoring import score_candidates


class LibraryLoggingContractTests(unittest.TestCase):
    def test_scoring_progress_uses_logging_not_stdout(self):
        img = np.zeros((20, 20, 3), dtype=np.uint8)
        ref = ImageItem(source="test", label="2025", path=Path("2025.png"), img=img)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            candidates = score_candidates([ref], ref, None, img_w=20, img_h=20, px_per_m=1.0, eps_px=2.0)

        self.assertEqual(candidates, [])
        self.assertEqual(stdout.getvalue(), "")

    def test_enhancement_progress_uses_logging_not_stdout(self):
        img = np.zeros((20, 20, 3), dtype=np.uint8)
        items = [ImageItem(source="test", label="2025", path=Path("2025.png"), img=img)]
        settings = replace(DEFAULT_ENHANCEMENT_SETTINGS, enabled=False)

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), self.assertLogs("core.enhancement", level="INFO") as logs:
            enhance_image_items(items, settings=settings)

        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Enhancement koloru: wyłączony", "\n".join(logs.output))

    def test_wms_cache_cleanup_uses_logging_not_stdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            old_path = cache_dir / "aa" / "old.png"
            new_path = cache_dir / "bb" / "new.png"
            old_path.parent.mkdir(parents=True)
            new_path.parent.mkdir(parents=True)
            old_path.write_bytes(b"old-data")
            new_path.write_bytes(b"new-data")
            os.utime(old_path, (1, 1))
            os.utime(new_path, (2, 2))

            stdout = io.StringIO()
            with (
                patch.object(wms_cache.config, "WMS_TILE_CACHE_DIR", cache_dir),
                patch.object(wms_cache.config, "WMS_TILE_CACHE_MAX_BYTES", len(b"new-data")),
                contextlib.redirect_stdout(stdout),
                self.assertLogs("app.wms_cache", level="INFO") as logs,
            ):
                wms_cache.cleanup_tile_cache(force=True)

            self.assertEqual(stdout.getvalue(), "")
            self.assertFalse(old_path.exists())
            self.assertTrue(new_path.exists())
            self.assertIn("WMS tile cache cleanup", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
