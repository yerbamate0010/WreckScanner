"""
Dodatkowe źródło danych: Geoportal Krajowy (geoportal.gov.pl).

Ten WMS udostępnia archiwalną ortofotomapę dla całej Polski. W przeciwieństwie
do WMS Wrocławia, często ma kilka warstw datowanych w ciągu roku — więcej
próbek czasowych = bardziej niezawodna detekcja "stoi długo".

Użycie:
    python3 scripts/download_geoportal_krajowy.py            # użyje metadata.json
    python3 scripts/download_geoportal_krajowy.py 51.0897 17.0389 80 80
"""

import json
import os
import sys

import requests
from defusedxml import ElementTree as ET

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.geo import bbox_4326
from core.runtime import configure_process_encoding

OUTPUT_DIR = "dane_dla_AI"
SUBDIR = "geoportal_krajowy"

WMS_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution"
WMS_HR_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/HighResolution"
ARCHIWALNE_URL = "https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/Archiwalne"


def bbox_string(lat, lon, width_meters, height_meters):
    """EPSG:4326 BBOX: minLat,minLon,maxLat,maxLon — WMS 1.3.0 order."""
    return bbox_4326(lat, lon, width_meters, height_meters)


def list_archive_layers():
    """Pobiera GetCapabilities z 'Archiwalne' i zwraca listę warstw z datami."""
    params = {"SERVICE": "WMS", "VERSION": "1.3.0", "REQUEST": "GetCapabilities"}
    try:
        resp = requests.get(ARCHIWALNE_URL, params=params, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        print(f"   ⚠️  Nie udało się pobrać GetCapabilities: {e}")
        return []

    ns = {"wms": "http://www.opengis.net/wms"}
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        return []

    layers = []
    for layer in root.iter("{http://www.opengis.net/wms}Layer"):
        name_el = layer.find("wms:Name", ns)
        title_el = layer.find("wms:Title", ns)
        if name_el is None:
            continue
        name = name_el.text or ""
        title = title_el.text if title_el is not None else name
        # nazwy zwykle zawierają rok, np. "Raster_2022"
        layers.append({"name": name, "title": title})
    return layers


def download_layer(layer_name, bbox, width, height, out_path, wms_url=ARCHIWALNE_URL):
    params = {
        "SERVICE": "WMS",
        "VERSION": "1.3.0",
        "REQUEST": "GetMap",
        "LAYERS": layer_name,
        "STYLES": "",
        "CRS": "EPSG:4326",
        "BBOX": bbox,
        "WIDTH": str(width),
        "HEIGHT": str(height),
        "FORMAT": "image/png",
    }
    try:
        resp = requests.get(wms_url, params=params, timeout=60)
    except Exception as e:
        return False, str(e)
    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}"
    if b"ServiceException" in resp.content or b"<Exception" in resp.content:
        return False, "WMS exception"
    if len(resp.content) < 1000:
        return False, "empty image"
    with open(out_path, "wb") as f:
        f.write(resp.content)
    return True, None


def main():
    configure_process_encoding()
    if len(sys.argv) >= 5:
        lat = float(sys.argv[1])
        lon = float(sys.argv[2])
        width_m = float(sys.argv[3])
        height_m = float(sys.argv[4])
    else:
        meta_path = os.path.join(OUTPUT_DIR, "metadata.json")
        if not os.path.exists(meta_path):
            print(f"❌ Brak {meta_path}. Najpierw pobierz dane przez UI lub podaj lat lon width height w argumentach.")
            sys.exit(1)
        with open(meta_path) as f:
            md = json.load(f)
        lat = md["center_lat"]
        lon = md["center_lon"]
        width_m = md["width_meters"]
        height_m = md["height_meters"]

    out_dir = os.path.join(OUTPUT_DIR, SUBDIR)
    os.makedirs(out_dir, exist_ok=True)
    print(f"📍 ({lat:.6f}, {lon:.6f})  area={width_m}×{height_m}m")
    print(f"📂 Zapis do {out_dir}/")

    print("🔎 Pobieranie listy archiwalnych warstw z Geoportalu Krajowego...")
    layers = list_archive_layers()
    print(f"   Znaleziono {len(layers)} warstw.")

    bbox = bbox_string(lat, lon, width_m, height_m)
    px_per_m = 40
    width = max(1, int(round(width_m * px_per_m)))
    height = max(1, int(round(height_m * px_per_m)))

    saved = 0
    saved_layers = []
    for layer in layers:
        name = layer["name"]
        # Pomiń warstwy parasolowe bez raster name
        if not name:
            continue
        safe_name = name.replace("/", "_").replace(":", "_")
        out_path = os.path.join(out_dir, f"{safe_name}.png")
        ok, err = download_layer(name, bbox, width, height, out_path)
        if ok:
            saved += 1
            saved_layers.append({"layer": name, "title": layer["title"], "file": out_path})
            print(f"   ✅ {name}")
        else:
            # za dużo szumu jeśli wypisujemy wszystkie błędne
            pass

    # Aktualna ortofotomap (zwykle najświeższa)
    for url, label in [(WMS_HR_URL, "HighResolution"), (WMS_URL, "StandardResolution")]:
        out_path = os.path.join(out_dir, f"current_{label}.png")
        ok, err = download_layer("Raster", bbox, width, height, out_path, wms_url=url)
        if ok:
            saved += 1
            saved_layers.append({"layer": f"current_{label}", "title": label, "file": out_path})
            print(f"   ✅ aktualna {label}")

    # zapisz manifest
    manifest = {
        "bbox_4326": bbox,
        "lat": lat,
        "lon": lon,
        "width_meters": width_m,
        "height_meters": height_m,
        "layers": saved_layers,
    }
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Pobrano {saved} obrazów z Geoportalu Krajowego.")


if __name__ == "__main__":
    main()
