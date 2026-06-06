from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from core import config
from core.config import DEFAULT_ENHANCEMENT_SETTINGS, EnhancementSettings

SETTINGS_PATH = Path(__file__).resolve().parent.parent / config.SETTINGS_FILENAME
DEFAULT_PUBLIC_LAYERS: dict[str, bool] = {
    "saved_wrecks": True,
    "field_photo_vehicle": True,
    "field_photo_infrastructure": True,
    "field_photo_smoke": True,
    "cadastral": True,
    "surface": True,
    "base_map_osm": True,
}

_ENHANCEMENT_LIMITS: dict[str, tuple[float, float]] = {
    "clahe_clip_limit": (0.1, 5.0),
    "clahe_tile_grid_size": (1, 32),
    "l_percentile_low": (0.0, 40.0),
    "l_percentile_high": (60.0, 100.0),
    "l_output_low": (0.0, 120.0),
    "l_output_high": (135.0, 255.0),
    "l_min_percentile_span": (1.0, 50.0),
    "decast_strength": (0.0, 1.0),
}


def enhancement_settings_to_dict(settings: EnhancementSettings) -> dict[str, Any]:
    return asdict(settings)


def enhancement_settings_from_dict(raw: Any) -> EnhancementSettings:
    defaults = enhancement_settings_to_dict(DEFAULT_ENHANCEMENT_SETTINGS)
    if not isinstance(raw, dict):
        return DEFAULT_ENHANCEMENT_SETTINGS

    data = defaults.copy()
    if "enabled" in raw:
        data["enabled"] = bool(raw["enabled"])

    for key, (min_value, max_value) in _ENHANCEMENT_LIMITS.items():
        if key not in raw:
            continue
        try:
            value = float(raw[key])
        except (TypeError, ValueError):
            continue
        value = max(min_value, min(max_value, value))
        data[key] = int(round(value)) if key == "clahe_tile_grid_size" else value

    if data["l_percentile_low"] >= data["l_percentile_high"]:
        data["l_percentile_low"] = defaults["l_percentile_low"]
        data["l_percentile_high"] = defaults["l_percentile_high"]
    if data["l_output_low"] >= data["l_output_high"]:
        data["l_output_low"] = defaults["l_output_low"]
        data["l_output_high"] = defaults["l_output_high"]

    return EnhancementSettings(**data)


def default_app_settings() -> dict[str, Any]:
    return {
        "enhancement": enhancement_settings_to_dict(DEFAULT_ENHANCEMENT_SETTINGS),
        "geotiff_cache": {
            "max_gb": config.DEFAULT_GEOTIFF_CACHE_MAX_GB,
        },
        "public_layers": DEFAULT_PUBLIC_LAYERS.copy(),
    }


def geotiff_cache_settings_from_dict(raw: Any) -> dict[str, Any]:
    defaults = default_app_settings()["geotiff_cache"]
    if not isinstance(raw, dict):
        return defaults

    if "max_gb" in raw and raw["max_gb"] is None:
        return {"max_gb": None}

    try:
        max_gb = float(raw.get("max_gb", defaults["max_gb"]))
    except (TypeError, ValueError):
        max_gb = defaults["max_gb"]

    min_gb, max_allowed_gb = config.GEOTIFF_CACHE_MAX_GB_RANGE
    max_gb = max(min_gb, min(max_allowed_gb, max_gb))
    return {"max_gb": max_gb}


def public_layer_settings_from_dict(raw: Any) -> dict[str, bool]:
    settings = DEFAULT_PUBLIC_LAYERS.copy()
    if not isinstance(raw, dict):
        return settings

    for key in settings:
        if key in raw:
            settings[key] = bool(raw[key])
    return settings


def load_app_settings() -> dict[str, Any]:
    if not SETTINGS_PATH.exists():
        return default_app_settings()

    try:
        with SETTINGS_PATH.open(encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError):
        return default_app_settings()

    if not isinstance(raw, dict):
        return default_app_settings()

    settings = default_app_settings()
    settings["enhancement"] = enhancement_settings_to_dict(enhancement_settings_from_dict(raw.get("enhancement")))
    settings["geotiff_cache"] = geotiff_cache_settings_from_dict(raw.get("geotiff_cache"))
    settings["public_layers"] = public_layer_settings_from_dict(raw.get("public_layers"))
    return settings


def load_enhancement_settings() -> EnhancementSettings:
    return enhancement_settings_from_dict(load_app_settings().get("enhancement"))


def load_geotiff_cache_max_bytes() -> int | None:
    settings = geotiff_cache_settings_from_dict(load_app_settings().get("geotiff_cache"))
    if settings["max_gb"] is None:
        return None
    return int(settings["max_gb"] * config.BYTES_PER_GIB)


def save_app_settings(raw: dict[str, Any]) -> dict[str, Any]:
    current = load_app_settings()
    if "enhancement" in raw:
        current["enhancement"] = enhancement_settings_to_dict(enhancement_settings_from_dict(raw["enhancement"]))
    if "geotiff_cache" in raw:
        current["geotiff_cache"] = geotiff_cache_settings_from_dict(raw["geotiff_cache"])
    if "public_layers" in raw:
        current["public_layers"] = public_layer_settings_from_dict(raw["public_layers"])

    with SETTINGS_PATH.open("w", encoding="utf-8") as f:
        json.dump(current, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return current
