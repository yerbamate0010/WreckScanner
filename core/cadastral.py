from __future__ import annotations

import math
from html.parser import HTMLParser
from typing import Any

WEB_MERCATOR_RADIUS_M = 6378137.0
WEB_MERCATOR_MAX_LAT = 85.05112878
FEATURE_INFO_BOX_M = 360.0
FEATURE_INFO_IMAGE_SIZE_PX = 512

CADASTRAL_FIELD_KEYS = {
    "Identyfikator działki": "parcel_id",
    "Województwo": "voivodeship",
    "Powiat": "county",
    "Nazwa gminy": "municipality",
    "Nazwa obrębu": "district",
    "Numer działki": "parcel_number",
    "Pole pow. w ewidencji gruntów (ha)": "area_ha",
    "Grupa rejestrowa": "registry_group",
    "Oznaczenie użytku": "land_use",
    "Oznaczenie konturu": "contour",
    "Data publikacji danych": "published_at",
}


class _FeatureInfoTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._capturing_td = False
        self._parts: list[str] = []
        self.cells: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "td":
            self._capturing_td = True
            self._parts = []

    def handle_data(self, data: str) -> None:
        if self._capturing_td:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "td" or not self._capturing_td:
            return
        self.cells.append(" ".join("".join(self._parts).split()))
        self._capturing_td = False
        self._parts = []


def web_mercator_xy(lat: float, lon: float) -> tuple[float, float]:
    safe_lat = max(-WEB_MERCATOR_MAX_LAT, min(WEB_MERCATOR_MAX_LAT, lat))
    x = WEB_MERCATOR_RADIUS_M * math.radians(lon)
    y = WEB_MERCATOR_RADIUS_M * math.log(math.tan(math.pi / 4 + math.radians(safe_lat) / 2))
    return x, y


def cadastral_feature_info_params(lat: float, lon: float) -> dict[str, Any]:
    x, y = web_mercator_xy(lat, lon)
    half = FEATURE_INFO_BOX_M / 2
    center_px = FEATURE_INFO_IMAGE_SIZE_PX // 2
    return {
        "SERVICE": "WMS",
        "REQUEST": "GetFeatureInfo",
        "VERSION": "1.3.0",
        "LAYERS": "dzialki",
        "QUERY_LAYERS": "dzialki",
        "STYLES": "default",
        "CRS": "EPSG:3857",
        "BBOX": f"{x - half:.3f},{y - half:.3f},{x + half:.3f},{y + half:.3f}",
        "WIDTH": str(FEATURE_INFO_IMAGE_SIZE_PX),
        "HEIGHT": str(FEATURE_INFO_IMAGE_SIZE_PX),
        "I": str(center_px),
        "J": str(center_px),
        "INFO_FORMAT": "text/html",
        "FORMAT": "image/png",
        "FEATURE_COUNT": "5",
    }


def parse_cadastral_feature_info(html: str) -> dict[str, Any]:
    parser = _FeatureInfoTableParser()
    parser.feed(html or "")
    raw_fields: dict[str, str] = {}
    for index in range(0, len(parser.cells) - 1, 2):
        key = parser.cells[index]
        value = parser.cells[index + 1]
        if key:
            raw_fields[key] = value

    parcel: dict[str, Any] = {"raw_fields": raw_fields}
    for source_key, target_key in CADASTRAL_FIELD_KEYS.items():
        parcel[target_key] = raw_fields.get(source_key, "")
    return parcel
