import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import core.settings_store as settings_store
from core.config import DEFAULT_GEOTIFF_CACHE_MAX_GB
from core.settings_store import geotiff_cache_settings_from_dict, public_layer_settings_from_dict


class GeotiffCacheSettingsTests(unittest.TestCase):
    def test_missing_cache_settings_use_default_limit(self):
        self.assertEqual(
            geotiff_cache_settings_from_dict({}),
            {"max_gb": DEFAULT_GEOTIFF_CACHE_MAX_GB},
        )

    def test_null_cache_limit_means_no_limit(self):
        self.assertEqual(
            geotiff_cache_settings_from_dict({"max_gb": None}),
            {"max_gb": None},
        )

    def test_numeric_cache_limit_is_clamped(self):
        self.assertEqual(geotiff_cache_settings_from_dict({"max_gb": 1}), {"max_gb": 2.0})
        self.assertEqual(geotiff_cache_settings_from_dict({"max_gb": 64}), {"max_gb": 32.0})

    def test_load_cache_limit_bytes_uses_saved_16_gb(self):
        with TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text('{"geotiff_cache": {"max_gb": 16}}', encoding="utf-8")

            with patch.object(settings_store, "SETTINGS_PATH", settings_path):
                self.assertEqual(settings_store.load_geotiff_cache_max_bytes(), 16 * 1024 * 1024 * 1024)

    def test_load_cache_limit_bytes_returns_none_for_no_limit(self):
        with TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text('{"geotiff_cache": {"max_gb": null}}', encoding="utf-8")

            with patch.object(settings_store, "SETTINGS_PATH", settings_path):
                self.assertIsNone(settings_store.load_geotiff_cache_max_bytes())


class PublicLayerSettingsTests(unittest.TestCase):
    def test_missing_public_layer_settings_default_to_visible(self):
        self.assertEqual(
            public_layer_settings_from_dict({}),
            {
                "saved_wrecks": True,
                "field_photo_vehicle": True,
                "field_photo_infrastructure": True,
                "field_photo_smoke": True,
                "cadastral": True,
                "surface": True,
                "base_map_osm": True,
            },
        )

    def test_public_layer_settings_accept_booleans_per_layer(self):
        self.assertEqual(
            public_layer_settings_from_dict({"saved_wrecks": False, "field_photo_smoke": False}),
            {
                "saved_wrecks": False,
                "field_photo_vehicle": True,
                "field_photo_infrastructure": True,
                "field_photo_smoke": False,
                "cadastral": True,
                "surface": True,
                "base_map_osm": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
