import unittest
from pathlib import Path

import numpy as np

from core.models import Detection, ImageItem
from core.scoring import angle_diff_deg, best_detection_match, has_temporal_support, temporal_span_score


def detection(cx=30.0, cy=30.0, angle=0.0, conf=0.8):
    return Detection(
        cx=cx,
        cy=cy,
        poly=np.array([[25, 25], [35, 25], [35, 35], [25, 35]], dtype=np.float32),
        class_id=9,
        angle=angle,
        conf=conf,
        length_m=4.5,
        width_m=1.8,
        aspect=2.5,
        area_m2=8.0,
    )


class ScoringHelperTests(unittest.TestCase):
    def test_angle_difference_wraps_at_180_degrees(self):
        self.assertEqual(angle_diff_deg(5, 175), 10)
        self.assertEqual(angle_diff_deg(10, 40), 30)

    def test_temporal_span_score_uses_year_range(self):
        self.assertEqual(temporal_span_score(["2025"]), 0.0)
        self.assertEqual(temporal_span_score(["2020", "2025"]), 1.0)
        self.assertAlmostEqual(temporal_span_score(["2022", "2024"]), 0.4)

    def test_temporal_support_requires_present_year_before_and_after(self):
        img = np.zeros((10, 10, 3), dtype=np.uint8)
        items = [
            ImageItem(source="test", label="2020", path=Path("2020.png"), img=img),
            ImageItem(source="test", label="2022", path=Path("2022.png"), img=img),
            ImageItem(source="test", label="2025", path=Path("2025.png"), img=img),
        ]

        self.assertTrue(has_temporal_support(1, items, {0, 2}))
        self.assertFalse(has_temporal_support(0, items, {1, 2}))

    def test_best_detection_match_chooses_close_valid_detection(self):
        img = np.full((80, 80, 3), (40, 120, 180), dtype=np.uint8)
        ref = detection(cx=30, cy=30)
        far = detection(cx=60, cy=60, conf=0.95)
        close = detection(cx=33, cy=31, conf=0.7)

        match = best_detection_match(ref, [far, close], ref_color=(30.0, 200.0, 180.0), img=img, eps_px=12)

        self.assertIsNotNone(match)
        self.assertIs(match.det, close)
        self.assertLess(match.dist_px, 4.0)

    def test_best_detection_match_rejects_detection_outside_radius(self):
        img = np.full((80, 80, 3), (40, 120, 180), dtype=np.uint8)
        ref = detection(cx=30, cy=30)
        far = detection(cx=55, cy=55)

        self.assertIsNone(best_detection_match(ref, [far], ref_color=(30.0, 200.0, 180.0), img=img, eps_px=10))


if __name__ == "__main__":
    unittest.main()
