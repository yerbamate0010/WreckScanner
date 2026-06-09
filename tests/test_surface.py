import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core.surface import parse_bbox, surface_features_geojson


class FakeOverpassResponse:
    status_code = 429

    def raise_for_status(self):
        raise RuntimeError("HTTP 429")

    def json(self):
        return {}


class EmptyOverpassResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"elements": []}


class SurfaceLayerTests(unittest.TestCase):
    def test_parse_bbox_normalizes_order(self):
        self.assertEqual(parse_bbox("51.2,17.3,51.1,17.2"), (51.1, 17.2, 51.2, 17.3))

    def test_parse_bbox_rejects_invalid_and_too_large_bbox(self):
        with self.assertRaises(ValueError):
            parse_bbox("")
        with self.assertRaises(ValueError):
            parse_bbox("51,17,52,18")

    def test_surface_geojson_returns_empty_error_payload_on_overpass_rate_limit(self):
        with (
            TemporaryDirectory() as tmp,
            patch("core.surface.SURFACE_CACHE_DIR", Path(tmp)),
            patch("core.surface._overpass_cooldown_until", 0),
            patch("core.surface._overpass_cooldown_error", ""),
            patch("core.surface.requests.post", return_value=FakeOverpassResponse()),
        ):
            geojson = surface_features_geojson((51.0888, 17.0358, 51.0899, 17.0392))

        self.assertEqual(geojson["type"], "FeatureCollection")
        self.assertEqual(geojson["features"], [])
        self.assertEqual(geojson["cache"], "error")
        self.assertIn("HTTP 429", geojson["error"])

    def test_surface_geojson_uses_cooldown_after_rate_limit(self):
        with (
            TemporaryDirectory() as tmp,
            patch("core.surface.SURFACE_CACHE_DIR", Path(tmp)),
            patch("core.surface._overpass_cooldown_until", 0),
            patch("core.surface._overpass_cooldown_error", ""),
            patch("core.surface.requests.post", return_value=FakeOverpassResponse()) as post_mock,
        ):
            first = surface_features_geojson((51.0888, 17.0358, 51.0899, 17.0392))
            second = surface_features_geojson((51.0890, 17.0360, 51.0901, 17.0394))

        self.assertEqual(first["cache"], "error")
        self.assertEqual(second["cache"], "cooldown")
        self.assertEqual(post_mock.call_count, 1)

    def test_surface_geojson_uses_nearby_feature_cache_when_overpass_returns_empty(self):
        with TemporaryDirectory() as tmp:
            cache_dir = Path(tmp)
            cached = {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": []}}],
                "bbox": [17.03, 51.08, 17.05, 51.10],
            }
            (cache_dir / "51.08000_17.03000_51.10000_17.05000.json").write_text(json.dumps(cached), encoding="utf-8")
            with (
                patch("core.surface.SURFACE_CACHE_DIR", cache_dir),
                patch("core.surface._overpass_cooldown_until", 0),
                patch("core.surface._overpass_cooldown_error", ""),
                patch("core.surface.requests.post", return_value=EmptyOverpassResponse()),
            ):
                geojson = surface_features_geojson((51.0888, 17.0358, 51.0899, 17.0392))

        self.assertEqual(geojson["cache"], "nearby")
        self.assertEqual(len(geojson["features"]), 1)


if __name__ == "__main__":
    unittest.main()
