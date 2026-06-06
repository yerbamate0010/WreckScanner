import re
import unittest
from pathlib import Path

from app import config as app_config
from core import config as core_config
from core.vehicle_size import looks_like_vehicle_size

ROOT_DIR = Path(__file__).resolve().parent.parent


def frontend_map_source_block(config_js: str) -> str:
    match = re.search(r"const MAP_SOURCES = \[(.*?)\];", config_js, re.S)
    if not match:
        raise AssertionError("MAP_SOURCES block not found")
    return match.group(1)


def vehicle_features(length_m: float, width_m: float) -> dict[str, float]:
    return {
        "length_m": length_m,
        "width_m": width_m,
        "aspect": length_m / width_m,
        "area_m2": length_m * width_m,
    }


class ConfigModuleContractTests(unittest.TestCase):
    def test_core_and_app_config_modules_expose_shared_runtime_contracts(self):
        self.assertEqual(core_config.YOLO_CLASS_NAMES, {9: "large vehicle", 10: "small vehicle"})
        self.assertEqual(core_config.VEHICLE_SIZE_RULES[9].max_length_m, 10.5)
        self.assertEqual(core_config.VEHICLE_SIZE_RULES[10].max_length_m, 7.5)
        self.assertEqual(app_config.PORT, 8000)
        self.assertEqual(app_config.WMS_YEARS, [2020, 2021, 2022, 2023, 2024, 2025])
        self.assertTrue(app_config.ADMIN_COOKIE_SECURE)
        self.assertEqual(app_config.CORS_ALLOWED_ORIGINS, ("https://wreckscanner.pl",))

    def test_web_config_is_loaded_before_application_code(self):
        html = (ROOT_DIR / "web" / "index.html").read_text(encoding="utf-8")
        config_js = (ROOT_DIR / "web" / "config.js").read_text(encoding="utf-8")
        app_js = (ROOT_DIR / "web" / "app.js").read_text(encoding="utf-8")
        map_helpers_js = (ROOT_DIR / "web" / "map_helpers.js").read_text(encoding="utf-8")

        self.assertLess(html.index('<script src="/config.js"></script>'), html.index('<script src="/app.js"></script>'))
        self.assertLess(
            html.index('<script src="/map_helpers.js"></script>'), html.index('<script src="/app.js"></script>')
        )
        self.assertIn("const MAP_SOURCES = [", config_js)
        self.assertNotIn("ORTHO_YEARS", config_js)
        self.assertIn("key: 'wroclaw-2025'", config_js)
        self.assertIn("shortLabel: '2025'", config_js)
        self.assertIn("key: 'geoportal-standard'", config_js)
        self.assertIn("shortLabel: 'STND'", config_js)
        self.assertIn("url: 'https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution'", config_js)
        self.assertIn("layers: 'Raster'", config_js)
        self.assertNotIn("key: 'geoportal-high'", config_js)
        self.assertNotIn("shortLabel: 'HIGH'", config_js)
        self.assertNotIn("HighResolution", config_js)
        self.assertNotIn("geoportal-trueortho", config_js)
        self.assertNotIn("PrawdziwaOrtofotomapa", config_js)
        self.assertIn("const DEFAULT_MAP_SOURCE_KEY = 'wroclaw-2025'", config_js)
        self.assertIn(
            "const CADASTRAL_WMS_URL = 'https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow'",
            config_js,
        )
        self.assertIn("const CADASTRAL_WMS_LAYERS = 'dzialki,numery_dzialek'", config_js)
        self.assertIn("const CADASTRAL_LAYER_VISIBLE_STORAGE_KEY", config_js)
        self.assertIn("const FIELD_PHOTO_GROUP_RADIUS_M = 1", config_js)
        self.assertIn("const CADASTRAL_IDENTIFY_URL = '/api/cadastral/identify'", config_js)
        self.assertEqual(app_config.CADASTRAL_WMS_URL, "https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow")
        self.assertEqual(
            app_config.CADASTRAL_WMS_FALLBACK_URL,
            "https://integracja01.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow",
        )
        self.assertEqual(app_config.CADASTRAL_WMS_TIMEOUT, (10, 30))
        self.assertNotIn("const FIELD_PHOTO_GROUP_RADIUS_M", app_js)
        self.assertIn("function readStoredMapView()", map_helpers_js)
        self.assertIn("new URLSearchParams(window.location.search)", map_helpers_js)
        self.assertIn("params.has('lat') && params.has('lon') && params.has('z')", map_helpers_js)
        self.assertIn("const urlZoom = Number(params.get('z'))", map_helpers_js)
        self.assertIn("function appPlaceUrl(lat, lon, zoom)", map_helpers_js)
        self.assertIn("function squareBounds(start, end)", map_helpers_js)
        self.assertNotIn("function readStoredMapView()", app_js)
        self.assertNotIn("function squareBounds(start, end)", app_js)

    def test_frontend_map_sources_are_structured_and_match_slider_contract(self):
        html = (ROOT_DIR / "web" / "index.html").read_text(encoding="utf-8")
        config_js = (ROOT_DIR / "web" / "config.js").read_text(encoding="utf-8")
        block = frontend_map_source_block(config_js)

        keys = re.findall(r"key: '([^']+)'", block)
        short_labels = re.findall(r"shortLabel: '([^']+)'", block)
        default_key = re.search(r"const DEFAULT_MAP_SOURCE_KEY = '([^']+)'", config_js).group(1)

        self.assertEqual(
            keys,
            [
                "wroclaw-2020",
                "wroclaw-2021",
                "wroclaw-2022",
                "wroclaw-2023",
                "wroclaw-2024",
                "wroclaw-2025",
                "geoportal-standard",
            ],
        )
        self.assertEqual(len(short_labels), len(keys))
        self.assertTrue(all(1 <= len(label) <= 4 for label in short_labels))
        self.assertIn(f'max="{len(keys) - 1}"', html)
        self.assertIn(f'value="{keys.index(default_key)}"', html)

        standard_source = re.search(r"\{\s*key: 'geoportal-standard'.*?\n\s*\}", block, re.S).group(0)
        self.assertIn("layers: 'Raster'", standard_source)
        self.assertIn("version: '1.3.0'", standard_source)
        self.assertNotIn("HighResolution", block)
        self.assertNotIn("3,2,1", block)
        self.assertNotIn("TrueOrtho", block)


class VehicleSizeRuleContractTests(unittest.TestCase):
    def test_large_vehicle_class_accepts_longer_yolo_boxes_than_small_vehicle(self):
        delivery_van = vehicle_features(length_m=10.5, width_m=2.4)

        self.assertTrue(looks_like_vehicle_size(delivery_van, 9))
        self.assertFalse(looks_like_vehicle_size(delivery_van, 10))

    def test_vehicle_size_rules_reject_boxes_above_class_length_caps(self):
        too_long_small_vehicle = vehicle_features(length_m=7.6, width_m=2.0)
        too_long_large_vehicle = vehicle_features(length_m=10.6, width_m=2.4)

        self.assertFalse(looks_like_vehicle_size(too_long_small_vehicle, 10))
        self.assertFalse(looks_like_vehicle_size(too_long_large_vehicle, 9))


if __name__ == "__main__":
    unittest.main()
