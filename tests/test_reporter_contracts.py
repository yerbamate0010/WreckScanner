import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

from core.models import Candidate, ImageItem, Observation
from core.reporter import write_analysis_outputs


class ReportAssetCacheContractTests(unittest.TestCase):
    def test_report_versions_overwritten_image_assets(self):
        with TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            crops_dir = output_dir / "crops"
            overlay_dir = output_dir / "overlays"
            crops_dir.mkdir()
            overlay_dir.mkdir()

            img = np.full((80, 80, 3), 120, dtype=np.uint8)
            items = [
                ImageItem(source="test", label="2024", path=Path("ortofoto_2024.png"), img=img.copy()),
                ImageItem(source="test", label="2025", path=Path("ortofoto_2025.png"), img=img.copy()),
            ]
            candidate = Candidate(
                cx=40.0,
                cy=40.0,
                lat=51.0,
                lon=17.0,
                score=0.9,
                current_conf=0.8,
                coverage=1.0,
                color_consistency=0.9,
                mean_conf=0.8,
                mean_match=0.8,
                span_score=1.0,
                evidence_factor=1.0,
                labels_present=["2024", "2025"],
                n_detections=2,
                valid_items=2,
                ignored_count=0,
                clear_missing_count=0,
                observations=[
                    Observation(label="2024", status="present", conf=0.8, crop_cx=40.0, crop_cy=40.0),
                    Observation(label="2025", status="present", conf=0.9, crop_cx=40.0, crop_cy=40.0),
                ],
                poly=np.array([[35, 35], [45, 35], [45, 45], [35, 45]], dtype=np.float32),
            )

            write_analysis_outputs(
                items,
                items[1],
                [candidate],
                {},
                output_dir,
                crops_dir,
                overlay_dir,
                img_w=80,
                img_h=80,
                eps_px=5.0,
                crop_px=20,
            )

            html = (output_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn('src="overlays/scored_overlay.jpg?v=', html)
            self.assertIn('src="crops/cand_000_2024.jpg?v=', html)
            self.assertIn('src="crops/cand_000_2025.jpg?v=', html)
            self.assertNotIn('src="overlays/scored_overlay.jpg"', html)
            self.assertNotIn('src="crops/cand_000_2024.jpg"', html)

            manifest = json.loads((output_dir / "crop_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["candidates"][0]["crops"][0]["file"], "crops/cand_000_2024.jpg")


if __name__ == "__main__":
    unittest.main()
