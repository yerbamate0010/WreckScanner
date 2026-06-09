import os
import unittest

import numpy as np

from core.config import IMG_SIZE_MAX, IMG_SIZE_MIN, IMG_SIZE_STRIDE

os.environ.setdefault("MPLCONFIGDIR", "/tmp/wreckscanner-matplotlib-tests")

from core.detection import detect_cars, merge_detections, optimal_imgsz, rect_features
from core.models import Detection


def detection(cx=30.0, cy=30.0, conf=0.8):
    return Detection(
        cx=cx,
        cy=cy,
        poly=np.array([[25, 25], [35, 25], [35, 35], [25, 35]], dtype=np.float32),
        class_id=10,
        angle=0.0,
        conf=conf,
        length_m=4.5,
        width_m=1.8,
        aspect=2.5,
        area_m2=8.0,
    )


class FakeTensor:
    def __init__(self, value):
        self.value = np.array(value)

    def cpu(self):
        return self

    def numpy(self):
        return self.value


class FakeObb:
    def __init__(self, polys, classes, confs):
        self.xyxyxyxy = FakeTensor(polys)
        self.cls = FakeTensor(classes)
        self.conf = FakeTensor(confs)
        self._count = len(classes)

    def __len__(self):
        return self._count


class FakeResult:
    def __init__(self, obb):
        self.obb = obb


class FakeDetector:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def predict(self, img, conf, imgsz):
        self.calls.append({"shape": img.shape, "conf": conf, "imgsz": imgsz})
        return self.results


class DetectionGeometryTests(unittest.TestCase):
    def test_rect_features_reports_pixel_and_meter_dimensions(self):
        poly = np.array([[10, 20], [50, 20], [50, 40], [10, 40]], dtype=np.float32)

        features = rect_features(poly, px_per_m=10.0)

        self.assertIsNotNone(features)
        self.assertAlmostEqual(features["length_px"], 40.0)
        self.assertAlmostEqual(features["width_px"], 20.0)
        self.assertAlmostEqual(features["area_px"], 800.0)
        self.assertAlmostEqual(features["aspect"], 2.0)
        self.assertAlmostEqual(features["length_m"], 4.0)
        self.assertAlmostEqual(features["width_m"], 2.0)
        self.assertAlmostEqual(features["area_m2"], 8.0)
        self.assertGreaterEqual(features["angle"], 0.0)
        self.assertLess(features["angle"], 180.0)

    def test_rect_features_rejects_degenerate_polygon(self):
        poly = np.array([[10, 10], [10, 10], [10, 10], [10, 10]], dtype=np.float32)

        self.assertIsNone(rect_features(poly, px_per_m=10.0))

    def test_merge_detections_keeps_highest_confidence_duplicate_and_far_detection(self):
        low_near = detection(cx=0, cy=0, conf=0.5)
        high_near = detection(cx=3, cy=4, conf=0.9)
        far = detection(cx=30, cy=30, conf=0.7)

        merged = merge_detections([low_near, far, high_near], eps_px=6.0)

        self.assertEqual(merged, [high_near, far])

    def test_optimal_imgsz_uses_stride_and_clamps_bounds(self):
        self.assertEqual(optimal_imgsz(1.0, 1.0), IMG_SIZE_MIN)
        self.assertEqual(optimal_imgsz(10_000.0, 10_000.0), IMG_SIZE_MAX)

        imgsz = optimal_imgsz(123.4, 50.0)

        self.assertEqual(imgsz % IMG_SIZE_STRIDE, 0)
        self.assertGreaterEqual(imgsz, IMG_SIZE_MIN)
        self.assertLessEqual(imgsz, IMG_SIZE_MAX)


class DetectCarsTests(unittest.TestCase):
    def test_detect_cars_filters_yolo_obb_results_and_maps_detection_fields(self):
        img = np.zeros((80, 80, 3), dtype=np.uint8)
        valid_vehicle = [[10, 20], [50, 20], [50, 40], [10, 40]]
        non_vehicle_class = [[12, 22], [52, 22], [52, 42], [12, 42]]
        too_large_vehicle = [[0, 0], [79, 0], [79, 79], [0, 79]]
        obb = FakeObb(
            polys=[valid_vehicle, non_vehicle_class, too_large_vehicle],
            classes=[10, 0, 10],
            confs=[0.81, 0.99, 0.95],
        )
        detector = FakeDetector([FakeResult(obb)])

        detections = detect_cars(detector, img, conf=0.2, imgsz=640, px_per_m=10.0)

        self.assertEqual(len(detections), 1)
        det = detections[0]
        self.assertAlmostEqual(det.cx, 30.0)
        self.assertAlmostEqual(det.cy, 30.0)
        self.assertEqual(det.class_id, 10)
        self.assertAlmostEqual(det.conf, 0.81)
        self.assertAlmostEqual(det.length_m, 4.0)
        self.assertAlmostEqual(det.width_m, 2.0)
        self.assertAlmostEqual(det.area_m2, 8.0)
        self.assertEqual(detector.calls, [{"shape": img.shape, "conf": 0.2, "imgsz": 640}])

    def test_detect_cars_handles_missing_or_empty_obb_results(self):
        img = np.zeros((20, 20, 3), dtype=np.uint8)
        empty_obb = FakeObb(polys=[], classes=[], confs=[])
        detector = FakeDetector([FakeResult(None), FakeResult(empty_obb)])

        self.assertEqual(detect_cars(detector, img), [])


if __name__ == "__main__":
    unittest.main()
