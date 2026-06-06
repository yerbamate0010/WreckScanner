// odległość w metrach między dwoma punktami GPS (mała skala)
function metersBetween(lat1, lon1, lat2, lon2) {
    const dLat = (lat1 - lat2) * METERS_PER_DEGREE_LAT;
    const dLon = (lon1 - lon2) * METERS_PER_DEGREE_LAT * Math.cos(lat1 * Math.PI/180);
    return Math.sqrt(dLat*dLat + dLon*dLon);
}

function readStoredMapView() {
    const params = new URLSearchParams(window.location.search);
    if (params.has('lat') && params.has('lon') && params.has('z')) {
        const urlLat = Number(params.get('lat'));
        const urlLon = Number(params.get('lon'));
        const urlZoom = Number(params.get('z'));
        if (
            Number.isFinite(urlLat) && urlLat >= -90 && urlLat <= 90 &&
            Number.isFinite(urlLon) && urlLon >= -180 && urlLon <= 180 &&
            Number.isFinite(urlZoom) && urlZoom >= 0 && urlZoom <= MAX_MAP_ZOOM
        ) {
            return { center: [urlLat, urlLon], zoom: urlZoom };
        }
    }

    try {
        const stored = JSON.parse(localStorage.getItem(MAP_VIEW_STORAGE_KEY) || 'null');
        if (!stored) return DEFAULT_MAP_VIEW;

        const lat = Number(stored.lat);
        const lon = Number(stored.lon);
        const zoom = Number(stored.zoom);
        if (
            Number.isFinite(lat) && lat >= -90 && lat <= 90 &&
            Number.isFinite(lon) && lon >= -180 && lon <= 180 &&
            Number.isFinite(zoom) && zoom >= 0 && zoom <= MAX_MAP_ZOOM
        ) {
            return { center: [lat, lon], zoom };
        }
    } catch (err) {
        console.warn('Nie udało się odczytać zapisanej pozycji mapy.', err);
    }
    return DEFAULT_MAP_VIEW;
}

function saveMapView() {
    try {
        const center = map.getCenter();
        localStorage.setItem(MAP_VIEW_STORAGE_KEY, JSON.stringify({
            lat: center.lat,
            lon: center.lng,
            zoom: map.getZoom(),
        }));
    } catch (err) {
        console.warn('Nie udało się zapisać pozycji mapy.', err);
    }
}

function appPlaceUrl(lat, lon, zoom) {
    const url = new URL(window.location.href);
    const placeLat = Number(lat);
    const placeLon = Number(lon);
    const placeZoom = Number(zoom);
    url.searchParams.set('lat', Number.isFinite(placeLat) ? placeLat.toFixed(6) : DEFAULT_MAP_VIEW.center[0].toFixed(6));
    url.searchParams.set('lon', Number.isFinite(placeLon) ? placeLon.toFixed(6) : DEFAULT_MAP_VIEW.center[1].toFixed(6));
    url.searchParams.set('z', String(Number.isFinite(placeZoom) ? Math.round(placeZoom) : DEFAULT_MAP_VIEW.zoom));
    return url.toString();
}

function metersBetweenLatLng(latlng1, latlng2) {
    const lat_m = METERS_PER_DEGREE_LAT;
    const lon_m = METERS_PER_DEGREE_LAT * Math.cos(latlng1.lat * Math.PI / 180);
    const dLat = (latlng1.lat - latlng2.lat) * lat_m;
    const dLon = (latlng1.lng - latlng2.lng) * lon_m;
    return { dLat: Math.abs(dLat), dLon: Math.abs(dLon) };
}

function squareBounds(start, end) {
    const lat_m = METERS_PER_DEGREE_LAT;
    const lon_m = METERS_PER_DEGREE_LAT * Math.cos(start.lat * Math.PI / 180);
    const dLatM = (end.lat - start.lat) * lat_m;
    const dLonM = (end.lng - start.lng) * lon_m;
    const sizeM = Math.max(Math.abs(dLatM), Math.abs(dLonM));
    const lat2 = start.lat + Math.sign(dLatM || 1) * sizeM / lat_m;
    const lng2 = start.lng + Math.sign(dLonM || 1) * sizeM / lon_m;
    return L.latLngBounds(start, L.latLng(lat2, lng2));
}
