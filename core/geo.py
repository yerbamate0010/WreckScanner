from __future__ import annotations

import math

# Przybliżenie wystarczające dla kadrów rzędu kilkudziesięciu metrów.
# Zwiększanie precyzji tu nie poprawi detekcji YOLO, ale bardziej złożona
# geodezja utrudniłaby debugowanie prostych bboxów WMS.
METERS_PER_DEGREE_LAT = 111_320.0


def lon_meters_per_degree(lat: float) -> float:
    return METERS_PER_DEGREE_LAT * math.cos(math.radians(lat))


def bbox_4326(lat: float, lon: float, width_m: float, height_m: float) -> str:
    """EPSG:4326 BBOX w kolejności WMS 1.3.0: minLat,minLon,maxLat,maxLon."""

    d_lat = height_m / METERS_PER_DEGREE_LAT
    d_lon = width_m / lon_meters_per_degree(lat)
    return f"{lat - d_lat / 2:.6f},{lon - d_lon / 2:.6f},{lat + d_lat / 2:.6f},{lon + d_lon / 2:.6f}"


def meters_between(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    center_lat = (lat1 + lat2) / 2.0
    d_lat = (lat1 - lat2) * METERS_PER_DEGREE_LAT
    d_lon = (lon1 - lon2) * lon_meters_per_degree(center_lat)
    return math.hypot(d_lat, d_lon)


def external_map_links(lat: float, lon: float) -> dict[str, str]:
    return {
        "street_view": f"https://www.google.com/maps/@{lat},{lon},3a,75y,90h,75t/data=!3m6!1e1",
        "google_maps_satellite": f"https://www.google.com/maps/@{lat},{lon},80m/data=!3m1!1e3",
        "apple_maps": f"https://maps.apple.com/?ll={lat},{lon}&z=20&t=k",
        "mapillary": f"https://www.mapillary.com/app/?lat={lat}&lng={lon}&z=19",
        "geoportal": f"https://mapy.geoportal.gov.pl/imap/Imgp_2.html?gpmap=gp0&lat={lat}&lon={lon}",
    }


def google_maps_embed_url(lat: float, lon: float) -> str:
    return f"https://maps.google.com/maps?q={lat},{lon}&t=k&z=20&output=embed"
