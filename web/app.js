// ─── STATE ──────────────────────────────────────
let currentWidth = 50;
let currentHeight = 50;
let lastDownload = null;   // { lat, lon, width, height } — gdzie i co ostatnio pobraliśmy
let currentJobToken = null;
let enhancementSettingsRevision = Date.now();
let settingsSaveTimer = null;
let defaultEnhancementSettings = null;

const initialMapView = readStoredMapView();
const map = L.map('map', {
    center: initialMapView.center,
    zoom: initialMapView.zoom,
    maxZoom: MAX_MAP_ZOOM,
    zoomControl: false,
    boxZoom: false,  // wyłączone — shift+drag używamy do zaznaczania
});
const surfacePane = map.createPane('surfacePane');
surfacePane.style.zIndex = 450;
map.on('moveend zoomend', saveMapView);

function markerDetailMode() {
    const zoom = map.getZoom();
    if (zoom <= MARKER_DETAIL_DOT_MAX_ZOOM) return 'dots';
    if (zoom < MARKER_DETAIL_FULL_MIN_ZOOM) return 'compact';
    return 'full';
}

function updateMarkerDetailMode() {
    const container = map.getContainer();
    container.classList.remove('marker-detail--full', 'marker-detail--compact', 'marker-detail--dots');
    container.classList.add(`marker-detail--${markerDetailMode()}`);
}

map.on('zoomend', updateMarkerDetailMode);
updateMarkerDetailMode();

// Same source family as analysis downloads for Wrocław sources; Geoportal sources
// are preview-only base layers and do not change the backend scan pipeline.
let currentMapSourceIndex = Math.max(0, MAP_SOURCES.findIndex(source => source.key === DEFAULT_MAP_SOURCE_KEY));
let mapSourceLayer = null;
let mapSourceSwapToken = 0;
let mapLabelLayer = null;
let cadastralLayer = null;
let cadastralLayerVisible = false;
let surfaceLayer = null;
let surfaceLayerVisible = false;
let surfaceLayerLoadToken = 0;
let surfaceLayerReloadTimer = null;
let surfaceLayerInFlightKey = '';
let surfaceLayerLoadedKey = '';
let geotiffCacheLayer = null;

try {
    cadastralLayerVisible = localStorage.getItem(CADASTRAL_LAYER_VISIBLE_STORAGE_KEY) === '1';
} catch (err) {
    console.warn('Nie udało się odczytać ustawienia warstwy działek.', err);
}

function activeMapSource() {
    return MAP_SOURCES[currentMapSourceIndex] || MAP_SOURCES[0];
}

function mapSourceAllowed(source) {
    return !source?.publicLayerKey || publicLayerAllowed(source.publicLayerKey);
}

function visibleMapSourceIndices() {
    return MAP_SOURCES.map((source, index) => ({ source, index }))
        .filter(item => mapSourceAllowed(item.source))
        .map(item => item.index);
}

function fallbackMapSourceIndex(visibleIndices = visibleMapSourceIndices()) {
    const defaultIndex = MAP_SOURCES.findIndex(source => source.key === DEFAULT_MAP_SOURCE_KEY);
    if (visibleIndices.includes(defaultIndex)) return defaultIndex;
    return visibleIndices[0] ?? Math.max(0, defaultIndex);
}

function mapSourceVisiblePosition(index = currentMapSourceIndex, visibleIndices = visibleMapSourceIndices()) {
    const position = visibleIndices.indexOf(index);
    return position >= 0 ? position : Math.max(0, visibleIndices.indexOf(fallbackMapSourceIndex(visibleIndices)));
}

function buildMapSourceLayer(source) {
    if (source.type === 'wroclaw') {
        return L.tileLayer.wms(`/wms_proxy/OGC_ortofoto_${source.year}/MapServer/WMSServer`, {
            layers: '1',
            format: 'image/png',
            transparent: false,
            version: '1.3.0',
            enhancementSettings: enhancementSettingsRevision,
            maxZoom: MAX_MAP_ZOOM,
            attribution: `Geoportal Wroclawia · ${source.year} · enhanced`,
        });
    }
    if (source.type === 'wms') {
        return L.tileLayer.wms(source.url, {
            layers: source.layers,
            styles: source.styles || '',
            format: 'image/png',
            transparent: false,
            version: source.version || '1.3.0',
            maxZoom: MAX_MAP_ZOOM,
            attribution: source.attribution || 'Geoportal.gov.pl / GUGiK',
        });
    }
    if (source.type === 'tile') {
        return L.tileLayer(source.url, {
            maxZoom: MAX_MAP_ZOOM,
            maxNativeZoom: source.maxNativeZoom || MAX_MAP_ZOOM,
            attribution: source.attribution || '',
        });
    }
    throw new Error(`Unsupported map source type: ${source.type}`);
}

function buildMapLabelLayer() {
    return L.tileLayer(CARTO_LABELS_TILE_URL, {
        maxZoom: MAX_MAP_ZOOM,
        pane: 'overlayPane',
    });
}

function updateMapLabelLayer() {
    const shouldShow = activeMapSource().labelsOverlay !== false;
    if (shouldShow) {
        if (!mapLabelLayer) mapLabelLayer = buildMapLabelLayer();
        if (!map.hasLayer(mapLabelLayer)) mapLabelLayer.addTo(map);
        return;
    }
    if (mapLabelLayer && map.hasLayer(mapLabelLayer)) {
        map.removeLayer(mapLabelLayer);
    }
}

function renderMapSourceTicks(visibleIndices = visibleMapSourceIndices()) {
    const ticks = document.getElementById('year-ticks');
    if (!ticks) return;
    ticks.innerHTML = '';
    visibleIndices.forEach(index => {
        const source = MAP_SOURCES[index];
        const tick = document.createElement('button');
        tick.type = 'button';
        tick.className = 'year-tick' + (index === currentMapSourceIndex ? ' active' : '');
        tick.dataset.index = index;
        tick.textContent = source.shortLabel;
        tick.title = source.label;
        tick.addEventListener('click', () => setMapSource(index));
        ticks.appendChild(tick);
    });
}

function updateMapSourceUi() {
    const visibleIndices = visibleMapSourceIndices();
    const source = activeMapSource();
    const currentLabel = document.getElementById('year-current');
    currentLabel.textContent = source.shortLabel;
    currentLabel.title = source.label;
    const range = document.getElementById('year-range');
    if (range) {
        range.min = 0;
        range.max = Math.max(0, visibleIndices.length - 1);
        range.value = mapSourceVisiblePosition(currentMapSourceIndex, visibleIndices);
    }
    renderMapSourceTicks(visibleIndices);
    document.getElementById('year-prev')?.toggleAttribute('disabled', mapSourceVisiblePosition(currentMapSourceIndex, visibleIndices) <= 0);
    document.getElementById('year-next')?.toggleAttribute(
        'disabled',
        mapSourceVisiblePosition(currentMapSourceIndex, visibleIndices) >= visibleIndices.length - 1
    );
    updateMapLabelLayer();
}

function swapMapSourceLayer(nextLayer, previousLayer) {
    const swapToken = ++mapSourceSwapToken;
    mapSourceLayer = nextLayer;
    nextLayer.addTo(map);
    const finishSwap = () => {
        if (previousLayer) map.removeLayer(previousLayer);
        if (swapToken === mapSourceSwapToken) {
            mapSourceLayer = nextLayer;
        } else if (map.hasLayer(nextLayer)) {
            map.removeLayer(nextLayer);
        }
    };
    nextLayer.once('load', finishSwap);
    setTimeout(() => {
        finishSwap();
    }, ORTHO_LAYER_SWAP_FALLBACK_MS);
}

function setMapSource(index) {
    const nextIndex = Math.max(0, Math.min(MAP_SOURCES.length - 1, parseInt(index, 10)));
    if (!Number.isFinite(nextIndex) || nextIndex === currentMapSourceIndex || !mapSourceAllowed(MAP_SOURCES[nextIndex])) return;
    currentMapSourceIndex = nextIndex;
    const previousLayer = mapSourceLayer;
    const nextLayer = buildMapSourceLayer(activeMapSource());
    swapMapSourceLayer(nextLayer, previousLayer);
    updateMapSourceUi();
}

function setMapSourceByVisiblePosition(position) {
    const visibleIndices = visibleMapSourceIndices();
    if (!visibleIndices.length) return;
    const nextPosition = Math.max(0, Math.min(visibleIndices.length - 1, parseInt(position, 10)));
    setMapSource(visibleIndices[nextPosition]);
}

function moveMapSource(delta) {
    const visibleIndices = visibleMapSourceIndices();
    const nextPosition = mapSourceVisiblePosition(currentMapSourceIndex, visibleIndices) + delta;
    setMapSourceByVisiblePosition(nextPosition);
}

function updateMapSourceAvailability() {
    if (mapSourceAllowed(activeMapSource())) {
        updateMapSourceUi();
        return;
    }
    currentMapSourceIndex = fallbackMapSourceIndex();
    const previousLayer = mapSourceLayer;
    const nextLayer = buildMapSourceLayer(activeMapSource());
    swapMapSourceLayer(nextLayer, previousLayer);
    updateMapSourceUi();
}

mapSourceLayer = buildMapSourceLayer(activeMapSource()).addTo(map);

function refreshOrthoLayer() {
    const previousLayer = mapSourceLayer;
    const nextLayer = buildMapSourceLayer(activeMapSource());
    swapMapSourceLayer(nextLayer, previousLayer);
}

function buildCadastralLayer() {
    return L.tileLayer.wms(CADASTRAL_WMS_URL, {
        layers: CADASTRAL_WMS_LAYERS,
        styles: 'default,default',
        format: 'image/png',
        transparent: true,
        version: '1.3.0',
        maxZoom: MAX_MAP_ZOOM,
        opacity: 0.95,
        pane: 'overlayPane',
        attribution: 'KIEG GUGiK',
    });
}

function setCadastralLayerVisible(visible) {
    cadastralLayerVisible = Boolean(visible);
    const allowed = publicLayerAllowed(PUBLIC_LAYER_KEYS.cadastral);
    const toggle = document.getElementById('toggle-cadastral-parcels');
    if (toggle) toggle.checked = cadastralLayerVisible && allowed;
    try {
        localStorage.setItem(CADASTRAL_LAYER_VISIBLE_STORAGE_KEY, cadastralLayerVisible ? '1' : '0');
    } catch (err) {
        console.warn('Nie udało się zapisać ustawienia warstwy działek.', err);
    }
    if (cadastralLayerVisible && allowed) {
        if (!cadastralLayer) cadastralLayer = buildCadastralLayer();
        if (!map.hasLayer(cadastralLayer)) cadastralLayer.addTo(map);
    } else if (cadastralLayer && map.hasLayer(cadastralLayer)) {
        map.removeLayer(cadastralLayer);
    }
}

function toggleCadastralLayer(visible) {
    setCadastralLayerVisible(visible);
}

function setSurfaceLayerStatus(key = '', params = {}, state = '') {
    const status = document.getElementById('surface-layer-status');
    if (!status) return;
    const text = key ? t(key, params) : '';
    status.textContent = text;
    status.title = params.error || text;
    status.classList.toggle('is-error', state === 'error');
    status.classList.toggle('is-ok', state === 'ok');
}

function surfaceBboxKey(bboxValues) {
    return bboxValues.map(value => Number(value).toFixed(5)).join(',');
}

async function fetchSurfaceGeojson(bboxKey) {
    const resp = await fetch(`${SURFACE_FEATURES_URL}?bbox=${encodeURIComponent(bboxKey)}`, { cache: 'no-store' });
    const data = await resp.json().catch(() => ({}));
    if (resp.ok && data.status === 'ok' && data.geojson?.type === 'FeatureCollection') {
        return data.geojson;
    }
    throw new Error(data.error || t('layers.surfaceError'));
}

function surfaceFeatureStyle(feature) {
    const kind = feature?.properties?.kind || 'surface';
    const colors = {
        road: '#f97316',
        sidewalk: '#22c55e',
        parking: '#38bdf8',
        kerb: '#f43f5e',
        surface: '#eab308',
    };
    return {
        color: colors[kind] || colors.surface,
        weight: kind === 'kerb' ? 5 : kind === 'sidewalk' ? 4 : 3,
        opacity: 0.96,
        fillOpacity: 0.18,
        lineCap: 'round',
        lineJoin: 'round',
        dashArray: kind === 'kerb' ? '3 5' : null,
        pane: 'surfacePane',
    };
}

function surfaceKnownTranslation(key) {
    const translated = t(key);
    return translated === key ? '' : translated;
}

function surfaceKindLabel(kind) {
    return surfaceKnownTranslation(`layers.surfaceKind.${kind || 'surface'}`) || String(kind || '');
}

function surfaceTagLabel(group, value) {
    const rawValue = String(value || '').trim();
    if (!rawValue) return '';
    const normalized = rawValue.toLowerCase().replace(/[^a-z0-9:_-]/g, '');
    return surfaceKnownTranslation(`layers.surface${group}.${normalized}`) || rawValue;
}

function surfacePopupRow(labelKey, value) {
    if (!value) return '';
    return `
        <div class="surface-popup-row">
            <span>${escapeHtml(t(labelKey))}</span>
            <strong>${escapeHtml(value)}</strong>
        </div>
    `;
}

function surfaceFeaturePopup(feature) {
    const props = feature?.properties || {};
    const rows = [
        surfacePopupRow('layers.surfacePopup.kind', surfaceKindLabel(props.kind)),
        surfacePopupRow('layers.surfacePopup.highway', surfaceTagLabel('Highway', props.highway)),
        surfacePopupRow('layers.surfacePopup.material', surfaceTagLabel('Material', props.surface)),
        surfacePopupRow('layers.surfacePopup.kerb', surfaceTagLabel('Kerb', props.kerb)),
        surfacePopupRow('layers.surfacePopup.source', props.source),
    ].filter(Boolean).join('');
    return `
        <div class="map-popup map-popup--surface">
            <b>${escapeHtml(t('layers.surface'))}</b>
            <div class="surface-popup-rows">${rows || escapeHtml(t('layers.surfaceEmpty'))}</div>
        </div>
    `;
}

async function loadSurfaceLayer() {
    if (!surfaceLayerVisible || !publicLayerAllowed(PUBLIC_LAYER_KEYS.surface)) return;
    const bounds = map.getBounds();
    const bboxValues = [bounds.getSouth(), bounds.getWest(), bounds.getNorth(), bounds.getEast()];
    const bboxKey = surfaceBboxKey(bboxValues);
    if (bboxKey === surfaceLayerInFlightKey || (bboxKey === surfaceLayerLoadedKey && surfaceLayer)) return;
    surfaceLayerInFlightKey = bboxKey;
    const token = ++surfaceLayerLoadToken;
    setSurfaceLayerStatus('layers.surfaceLoading');
    try {
        const geojson = await fetchSurfaceGeojson(bboxKey);
        if (token !== surfaceLayerLoadToken || !surfaceLayerVisible) return;
        if (surfaceLayer) map.removeLayer(surfaceLayer);
        const featureCount = Array.isArray(geojson.features) ? geojson.features.length : 0;
        surfaceLayerLoadedKey = bboxKey;
        if (featureCount) {
            surfaceLayer = L.geoJSON(geojson, {
                pane: 'surfacePane',
                style: surfaceFeatureStyle,
                pointToLayer: (_feature, latlng) => L.circleMarker(
                    latlng,
                    { radius: 5, pane: 'surfacePane', ...surfaceFeatureStyle(_feature) }
                ),
                onEachFeature: (feature, layer) => layer.bindPopup(surfaceFeaturePopup(feature)),
            }).addTo(map);
            surfaceLayer.bringToFront();
            setSurfaceLayerStatus('layers.surfaceLoaded', { n: featureCount, error: geojson.warning || '' }, 'ok');
        } else {
            surfaceLayer = null;
            setSurfaceLayerStatus('layers.surfaceEmpty', { error: geojson.error || '' }, 'ok');
        }
    } catch (err) {
        if (token === surfaceLayerLoadToken && surfaceLayerVisible) {
            if (surfaceLayer) {
                map.removeLayer(surfaceLayer);
                surfaceLayer = null;
            }
            setSurfaceLayerStatus('layers.surfaceLoadError', { error: err.message || '' }, 'error');
        }
        console.warn('Nie udało się pobrać warstwy nawierzchni.', err);
    } finally {
        if (surfaceLayerInFlightKey === bboxKey) surfaceLayerInFlightKey = '';
    }
}

function scheduleSurfaceLayerLoad(delayMs = 650) {
    if (!surfaceLayerVisible || !publicLayerAllowed(PUBLIC_LAYER_KEYS.surface)) return;
    if (surfaceLayerReloadTimer) clearTimeout(surfaceLayerReloadTimer);
    surfaceLayerReloadTimer = setTimeout(() => {
        surfaceLayerReloadTimer = null;
        loadSurfaceLayer();
    }, delayMs);
}

function setSurfaceLayerVisible(visible) {
    surfaceLayerVisible = Boolean(visible);
    const allowed = publicLayerAllowed(PUBLIC_LAYER_KEYS.surface);
    const toggle = document.getElementById('toggle-surface-layer');
    if (toggle) toggle.checked = surfaceLayerVisible && allowed;
    if (!surfaceLayerVisible && surfaceLayer) {
        map.removeLayer(surfaceLayer);
        surfaceLayer = null;
    }
    if (!surfaceLayerVisible || !allowed) {
        if (surfaceLayerReloadTimer) {
            clearTimeout(surfaceLayerReloadTimer);
            surfaceLayerReloadTimer = null;
        }
        surfaceLayerInFlightKey = '';
        surfaceLayerLoadedKey = '';
        surfaceLayerLoadToken += 1;
        setSurfaceLayerStatus('');
    }
    if (surfaceLayerVisible && allowed) scheduleSurfaceLayerLoad(0);
}

map.on('moveend zoomend', () => {
    if (surfaceLayerVisible && publicLayerAllowed(PUBLIC_LAYER_KEYS.surface)) scheduleSurfaceLayerLoad();
});

// ─── ENHANCEMENT SETTINGS ──────────────────────
// Ustawienia filtra są zapisywane na backendzie, żeby mapa i analiza YOLO
// używały dokładnie tych samych parametrów.
const enhancementControls = {
    enabled: document.getElementById('enhancement-enabled'),
    clahe: document.getElementById('enhancement-clahe'),
    tile: document.getElementById('enhancement-tile'),
    pLow: document.getElementById('enhancement-p-low'),
    pHigh: document.getElementById('enhancement-p-high'),
    outLow: document.getElementById('enhancement-out-low'),
    outHigh: document.getElementById('enhancement-out-high'),
    decast: document.getElementById('enhancement-decast'),
};

const enhancementControlFields = {
    clahe: 'clahe_clip_limit',
    tile: 'clahe_tile_grid_size',
    pLow: 'l_percentile_low',
    pHigh: 'l_percentile_high',
    outLow: 'l_output_low',
    outHigh: 'l_output_high',
    decast: 'decast_strength',
};

const geotiffCacheControl = document.getElementById('geotiff-cache-limit');
const publicLayerControls = {
    [PUBLIC_LAYER_KEYS.savedWrecks]: document.getElementById('admin-layer-saved-wrecks'),
    [PUBLIC_LAYER_KEYS.fieldPhotoVehicle]: document.getElementById('admin-layer-field-photo-vehicle'),
    [PUBLIC_LAYER_KEYS.fieldPhotoInfrastructure]: document.getElementById('admin-layer-field-photo-infrastructure'),
    [PUBLIC_LAYER_KEYS.fieldPhotoSmoke]: document.getElementById('admin-layer-field-photo-smoke'),
    [PUBLIC_LAYER_KEYS.cadastral]: document.getElementById('admin-layer-cadastral'),
    [PUBLIC_LAYER_KEYS.surface]: document.getElementById('admin-layer-surface'),
    [PUBLIC_LAYER_KEYS.baseMapOsm]: document.getElementById('admin-layer-base-map-osm'),
};
const publicLayerToggleRows = {
    [PUBLIC_LAYER_KEYS.savedWrecks]: document.getElementById('toggle-saved-wrecks')?.closest('.layer-toggle'),
    [PUBLIC_LAYER_KEYS.fieldPhotoVehicle]: document.getElementById('toggle-field-photo-vehicle')?.closest('.layer-toggle'),
    [PUBLIC_LAYER_KEYS.fieldPhotoInfrastructure]: document.getElementById('toggle-field-photo-infrastructure')?.closest('.layer-toggle'),
    [PUBLIC_LAYER_KEYS.fieldPhotoSmoke]: document.getElementById('toggle-field-photo-smoke')?.closest('.layer-toggle'),
    [PUBLIC_LAYER_KEYS.cadastral]: document.getElementById('toggle-cadastral-parcels')?.closest('.layer-toggle'),
    [PUBLIC_LAYER_KEYS.surface]: document.getElementById('toggle-surface-layer')?.closest('.layer-toggle'),
};
const adminSettingsControls = [
    document.getElementById('model-select'),
    document.getElementById('conf-select'),
    document.getElementById('crop-select'),
    geotiffCacheControl,
    document.getElementById('enhancement-reset'),
    document.getElementById('photo-retention-refresh'),
    document.getElementById('photo-retention-dry-run'),
    document.getElementById('photo-retention-apply'),
    document.getElementById('admin-public-layers-save'),
    ...Object.values(publicLayerControls),
    ...Object.values(enhancementControls),
].filter(Boolean);
let publicLayerSettings = Object.fromEntries(Object.values(PUBLIC_LAYER_KEYS).map(key => [key, true]));

function updateSettingsAccess() {
    const locked = !adminAuthenticated;
    const settingsModal = document.getElementById('modal-settings');
    const lockHint = document.getElementById('settings-lock-hint');
    settingsModal?.classList.toggle('settings-locked', locked);
    if (lockHint) lockHint.hidden = !locked;
    adminSettingsControls.forEach(control => { control.disabled = locked; });
    updatePublicLayerAccess();
}

function normalizePublicLayerSettings(settings) {
    const normalized = Object.fromEntries(Object.values(PUBLIC_LAYER_KEYS).map(key => [key, true]));
    if (!settings || typeof settings !== 'object') return normalized;
    Object.keys(normalized).forEach(key => {
        if (key in settings) normalized[key] = Boolean(settings[key]);
    });
    return normalized;
}

function fieldPhotoPublicLayerKey(issueType) {
    const safeIssueType = FIELD_PHOTO_ISSUE_TYPES.has(issueType) ? issueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    return FIELD_PHOTO_PUBLIC_LAYER_KEYS[safeIssueType] || PUBLIC_LAYER_KEYS.fieldPhotoVehicle;
}

function publicLayerAllowed(layerKey) {
    return adminAuthenticated || publicLayerSettings[layerKey] !== false;
}

function publicLayerFormSettings() {
    const settings = {};
    Object.entries(publicLayerControls).forEach(([key, control]) => {
        settings[key] = control ? Boolean(control.checked) : publicLayerSettings[key] !== false;
    });
    return normalizePublicLayerSettings(settings);
}

function updateAdminPublicLayerControls() {
    Object.entries(publicLayerControls).forEach(([key, control]) => {
        if (!control) return;
        control.checked = publicLayerSettings[key] !== false;
        control.disabled = !adminAuthenticated;
    });
}

function updatePublicLayerControlVisibility() {
    Object.entries(publicLayerToggleRows).forEach(([key, row]) => {
        if (!row) return;
        const allowed = publicLayerAllowed(key);
        row.hidden = !allowed;
        const input = row.querySelector('input');
        if (input) {
            input.disabled = !allowed;
        }
    });
}

function updatePublicLayerAccess() {
    updateAdminPublicLayerControls();
    updatePublicLayerControlVisibility();
}

function applyPublicLayerSettings(settings) {
    publicLayerSettings = normalizePublicLayerSettings(settings);
    updatePublicLayerAccess();
    setCadastralLayerVisible(cadastralLayerVisible);
    setSurfaceLayerVisible(surfaceLayerVisible);
    updateMapSourceAvailability();
    if (savedWreckLayerVisible && publicLayerAllowed(PUBLIC_LAYER_KEYS.savedWrecks)) {
        placeSavedWrecks(savedWreckLayerData);
    } else {
        clearSavedWreckMarkers();
    }
    placeFieldPhotos(fieldPhotoLayerData);
    updateLingeringCarsCounter();
}

function setControlValue(id, value) {
    const el = enhancementControls[id];
    if (!el || value === undefined || value === null) return;
    if (el.type === 'checkbox') el.checked = Boolean(value);
    else el.value = String(value);
}

function enhancementFormSettings() {
    return {
        enabled: enhancementControls.enabled.checked,
        clahe_clip_limit: parseFloat(enhancementControls.clahe.value),
        clahe_tile_grid_size: parseInt(enhancementControls.tile.value),
        l_percentile_low: parseFloat(enhancementControls.pLow.value),
        l_percentile_high: parseFloat(enhancementControls.pHigh.value),
        l_output_low: parseFloat(enhancementControls.outLow.value),
        l_output_high: parseFloat(enhancementControls.outHigh.value),
        decast_strength: parseFloat(enhancementControls.decast.value),
    };
}

function geotiffCacheFormSettings() {
    if (geotiffCacheControl?.value === 'none') {
        return { max_gb: null };
    }
    return {
        max_gb: parseFloat(geotiffCacheControl?.value || '4'),
    };
}

function applyEnhancementSettings(settings) {
    if (!settings) return;
    setControlValue('enabled', settings.enabled);
    setControlValue('clahe', settings.clahe_clip_limit);
    setControlValue('tile', settings.clahe_tile_grid_size);
    setControlValue('pLow', settings.l_percentile_low);
    setControlValue('pHigh', settings.l_percentile_high);
    setControlValue('outLow', settings.l_output_low);
    setControlValue('outHigh', settings.l_output_high);
    setControlValue('decast', settings.decast_strength);
    updateEnhancementLabels();
    updateEnhancementDefaultTicks();
}

function applyGeotiffCacheSettings(settings) {
    if (!settings || !geotiffCacheControl) return;
    if (settings.max_gb === null) {
        geotiffCacheControl.value = 'none';
        return;
    }
    const maxGb = Number(settings.max_gb);
    if (!Number.isFinite(maxGb)) return;
    const option = [...geotiffCacheControl.options].find(opt => Number(opt.value) === maxGb);
    geotiffCacheControl.value = option ? option.value : String(maxGb);
}

function updateEnhancementLabels() {
    document.getElementById('enhancement-clahe-value').textContent = Number(enhancementControls.clahe.value).toFixed(1);
    document.getElementById('enhancement-tile-value').textContent = enhancementControls.tile.value;
    document.getElementById('enhancement-p-low-value').textContent = Number(enhancementControls.pLow.value).toFixed(1).replace('.0', '');
    document.getElementById('enhancement-p-high-value').textContent = Number(enhancementControls.pHigh.value).toFixed(1).replace('.0', '');
    document.getElementById('enhancement-out-low-value').textContent = enhancementControls.outLow.value;
    document.getElementById('enhancement-out-high-value').textContent = enhancementControls.outHigh.value;
    document.getElementById('enhancement-decast-value').textContent = Number(enhancementControls.decast.value).toFixed(2).replace(/0$/, '').replace(/\.$/, '');
}

function updateEnhancementDefaultTicks() {
    if (!defaultEnhancementSettings) return;
    Object.entries(enhancementControlFields).forEach(([controlId, field]) => {
        const control = enhancementControls[controlId];
        if (!control) return;
        const min = parseFloat(control.min);
        const max = parseFloat(control.max);
        const value = parseFloat(defaultEnhancementSettings[field]);
        const pct = max > min ? ((value - min) / (max - min)) * 100 : 0;
        control.style.setProperty('--default-pos', `${Math.max(0, Math.min(100, pct))}%`);
    });
}

function snapEnhancementControlToDefault(control) {
    if (!defaultEnhancementSettings || control.type !== 'range') return;
    const controlId = Object.keys(enhancementControls).find(key => enhancementControls[key] === control);
    const field = enhancementControlFields[controlId];
    if (!field) return;

    const current = parseFloat(control.value);
    const defaultValue = parseFloat(defaultEnhancementSettings[field]);
    const min = parseFloat(control.min);
    const max = parseFloat(control.max);
    const step = parseFloat(control.step || '1');
    const snapDistance = Math.max(step * 1.1, (max - min) * 0.015);
    if (Math.abs(current - defaultValue) <= snapDistance) {
        control.value = String(defaultValue);
    }
}

async function loadAppSettings() {
    try {
        const resp = await fetch(SETTINGS_URL);
        const data = await resp.json();
        if (resp.ok) {
            defaultEnhancementSettings = data.defaults?.enhancement || data.enhancement;
            applyEnhancementSettings(data.enhancement);
            applyGeotiffCacheSettings(data.geotiff_cache);
            applyPublicLayerSettings(data.public_layers);
        }
    } catch (_) {
        updateEnhancementLabels();
    }
}

async function saveSettings(payload, onSaved, options = {}) {
    const status = document.getElementById(options.statusId || 'settings-save-status');
    if (!adminAuthenticated) {
        updateSettingsAccess();
        return;
    }
    try {
        const resp = await fetch(SETTINGS_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.error || 'settings save failed');
        onSaved(data);
    } catch (_) {
        if (status) status.textContent = options.errorMessage || t('modal.settings.saveError');
    }
}

async function saveEnhancementSettings() {
    await saveSettings({ enhancement: enhancementFormSettings() }, data => {
        applyEnhancementSettings(data.enhancement);
        enhancementSettingsRevision = Date.now();
        refreshOrthoLayer();
        document.getElementById('settings-save-status').textContent = t('modal.settings.enhancementHint');
    });
}

async function saveGeotiffCacheSettings() {
    await saveSettings({ geotiff_cache: geotiffCacheFormSettings() }, data => {
        applyGeotiffCacheSettings(data.geotiff_cache);
        document.getElementById('settings-save-status').textContent = t('modal.settings.cacheHint');
    });
}

async function savePublicLayerSettings() {
    await saveSettings({ public_layers: publicLayerFormSettings() }, data => {
        applyPublicLayerSettings(data.public_layers);
        const status = document.getElementById('admin-public-layers-status');
        if (status) status.textContent = t('modal.adminPanel.publicLayersSaved');
        loadSavedWrecks();
        loadFieldPhotos();
        updateMapSourceAvailability();
    }, {
        statusId: 'admin-public-layers-status',
        errorMessage: t('modal.adminPanel.publicLayersSaveError'),
    });
}

function photoRetentionReportSummary(report, state = {}) {
    if (!report) return t('modal.settings.photoRetentionIdle');
    const field = report.field_photos || {};
    const wreck = report.wreck_photos || {};
    const scanned = Number(field.scanned || 0) + Number(wreck.scanned || 0);
    const replaced = Number(field.replaced || 0) + Number(wreck.replaced || 0);
    const deleted = Number(field.deleted || 0) + Number(wreck.deleted || 0);
    const skipped = Number(field.skipped || 0) + Number(wreck.skipped || 0);
    const modeKey = report.dry_run
        ? 'modal.settings.photoRetentionSummaryDryRun'
        : 'modal.settings.photoRetentionSummaryApplied';
    return t(modeKey, {
        scanned,
        replaced,
        deleted,
        skipped,
        finished: state.last_finished_at || report.generated_at || '-',
    });
}

function updatePhotoRetentionStatus(state = {}) {
    const status = document.getElementById('photo-retention-status');
    if (!status) return;
    if (state.running) {
        status.textContent = t('modal.settings.photoRetentionRunning');
        return;
    }
    if (state.last_error) {
        status.textContent = t('modal.settings.photoRetentionLastError', { error: state.last_error });
        return;
    }
    status.textContent = photoRetentionReportSummary(state.last_report, state);
}

async function loadPhotoRetentionStatus() {
    if (!adminAuthenticated && !(await ensureAdmin())) return;
    const status = document.getElementById('photo-retention-status');
    if (status) status.textContent = t('modal.settings.photoRetentionLoading');
    try {
        const resp = await fetch(`${ADMIN_PHOTO_RETENTION_URL}?ts=${Date.now()}`, { cache: 'no-store' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.settings.photoRetentionLoadError'));
        }
        updatePhotoRetentionStatus(data.retention || {});
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.settings.photoRetentionLoadError');
    }
}

async function runPhotoRetention(dryRun = true) {
    if (!adminAuthenticated && !(await ensureAdmin())) return;
    if (!dryRun) {
        const confirmed = await confirmAction({
            title: t('modal.settings.photoRetentionApplyTitle'),
            message: t('modal.settings.photoRetentionApplyConfirm'),
            confirmLabel: t('modal.settings.photoRetentionApply'),
        });
        if (!confirmed) return;
    }
    const status = document.getElementById('photo-retention-status');
    if (status) status.textContent = t('modal.settings.photoRetentionRunning');
    try {
        const resp = await fetch(`${ADMIN_PHOTO_RETENTION_URL}/run`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ dry_run: Boolean(dryRun) }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.settings.photoRetentionRunError'));
        }
        updatePhotoRetentionStatus(data.retention || { last_report: data.report });
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.settings.photoRetentionRunError');
    }
}

function geotiffSizeLabel(bytes) {
    const value = Number(bytes || 0);
    if (value >= 1024 * 1024 * 1024) return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
    return `${Math.ceil(value / 1024)} KB`;
}

function clearGeotiffCacheLayer() {
    if (geotiffCacheLayer) {
        map.removeLayer(geotiffCacheLayer);
        geotiffCacheLayer = null;
    }
}

function renderGeotiffCacheLayer(data = {}) {
    clearGeotiffCacheLayer();
    const rectangles = (Array.isArray(data.coverage) ? data.coverage : [])
        .map(item => {
            const bounds = item.bounds_4326 || {};
            const minLat = Number(bounds.min_lat);
            const minLon = Number(bounds.min_lon);
            const maxLat = Number(bounds.max_lat);
            const maxLon = Number(bounds.max_lon);
            if (![minLat, minLon, maxLat, maxLon].every(Number.isFinite)) return null;
            const rect = L.rectangle(
                [[minLat, minLon], [maxLat, maxLon]],
                {
                    color: '#16a34a',
                    weight: 2,
                    fillColor: '#22c55e',
                    fillOpacity: 0.08,
                    interactive: true,
                }
            );
            rect.bindPopup(`
                <div class="map-popup">
                    <b>${escapeHtml(item.file || 'GeoTIFF')}</b><br>
                    ${escapeHtml(geotiffSizeLabel(item.size_bytes))}
                </div>
            `);
            return rect;
        })
        .filter(Boolean);
    if (!rectangles.length) return;
    geotiffCacheLayer = L.layerGroup(rectangles).addTo(map);
}

function renderGeotiffCacheStatus(data = {}) {
    const status = document.getElementById('geotiff-cache-status');
    const list = document.getElementById('geotiff-cache-list');
    const summary = data.summary || {};
    const estimate = data.estimate || {};
    if (status) {
        status.textContent = t('modal.geotiffCache.status', {
            total: geotiffSizeLabel(summary.total_bytes),
            files: summary.completed_files || 0,
            partials: summary.partial_files || 0,
            estimate: estimate.total_gb != null ? `${estimate.total_gb} GB` : '-',
        });
    }
    if (!list) return;
    const items = Array.isArray(data.items) ? data.items : [];
    list.innerHTML = items.slice(0, 80).map(item => `
        <div class="geotiff-cache-item geotiff-cache-item--${escapeHtml(item.status || 'unknown')}">
            <strong>${escapeHtml(item.file || '-')}</strong>
            <span>${escapeHtml(item.status || '-')} · ${escapeHtml(geotiffSizeLabel(item.size_bytes))}</span>
        </div>
    `).join('') || `<p class="modal-hint">${escapeHtml(t('modal.geotiffCache.empty'))}</p>`;
    renderGeotiffCacheLayer(data);
}

async function loadGeotiffCacheStatus() {
    if (!adminAuthenticated && !(await ensureAdmin())) return;
    const status = document.getElementById('geotiff-cache-status');
    if (status) status.textContent = t('modal.geotiffCache.loading');
    try {
        const resp = await fetch(`${ADMIN_GEOTIFF_CACHE_URL}?ts=${Date.now()}`, { cache: 'no-store' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.geotiffCache.loadError'));
        }
        renderGeotiffCacheStatus(data);
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.geotiffCache.loadError');
    }
}

async function openGeotiffCacheModal() {
    if (!(await ensureAdmin())) return;
    openModal('modal-geotiff-cache');
    await loadGeotiffCacheStatus();
}

document.getElementById('modal-geotiff-cache')?.addEventListener('modalclose', clearGeotiffCacheLayer);

function queueEnhancementSettingsSave(event = null) {
    if (!adminAuthenticated) {
        updateSettingsAccess();
        return;
    }
    if (event?.target instanceof HTMLInputElement) {
        snapEnhancementControlToDefault(event.target);
    }
    updateEnhancementLabels();
    if (settingsSaveTimer) clearTimeout(settingsSaveTimer);
    settingsSaveTimer = setTimeout(saveEnhancementSettings, ENHANCEMENT_SETTINGS_SAVE_DEBOUNCE_MS);
}

Object.values(enhancementControls).forEach(control => {
    if (!control) return;
    const eventName = control.type === 'checkbox' ? 'change' : 'input';
    control.addEventListener(eventName, queueEnhancementSettingsSave);
});
document.getElementById('enhancement-reset')?.addEventListener('click', () => {
    if (!defaultEnhancementSettings) return;
    applyEnhancementSettings(defaultEnhancementSettings);
    queueEnhancementSettingsSave();
});

function filePickerSummaryText(count) {
    if (!count) return t('filePicker.empty');
    if (count === 1) return t('filePicker.selectedOne');
    return t('filePicker.selectedMany', { n: count });
}

function updateFilePickerSummary(input) {
    if (!input?.id) return;
    const summary = document.querySelector(`.file-picker-summary[data-file-summary-for="${input.id}"]`);
    if (!summary) return;
    const count = input.files?.length || 0;
    summary.textContent = filePickerSummaryText(count);
    summary.classList.toggle('is-empty', count === 0);
}

function updateAllFilePickerSummaries() {
    document.querySelectorAll('.file-picker-input').forEach(updateFilePickerSummary);
}

document.querySelectorAll('.file-picker-input').forEach(input => {
    input.addEventListener('change', () => updateFilePickerSummary(input));
});
document.addEventListener('langchange', updateAllFilePickerSummaries);
updateAllFilePickerSummaries();

geotiffCacheControl?.addEventListener('change', saveGeotiffCacheSettings);
updateSettingsAccess();
loadAppSettings();
setCadastralLayerVisible(cadastralLayerVisible);
setSurfaceLayerVisible(surfaceLayerVisible);

// Zoom control — bottom-right
L.control.zoom({ position: 'bottomright' }).addTo(map);

// ─── MAP SOURCE SLIDER ──────────────────────────
// Inicjalizacja tików (klikalne źródła mapy) + range input + strzałki + klawisze.
(() => {
    const range = document.getElementById('year-range');
    range.step = 1;
    updateMapSourceUi();
    range.addEventListener('input', e => setMapSourceByVisiblePosition(e.target.value));
    document.getElementById('year-prev').addEventListener('click', () => moveMapSource(-1));
    document.getElementById('year-next').addEventListener('click', () => moveMapSource(1));
    document.addEventListener('keydown', e => {
        // Nie przechwytuj klawiszy gdy użytkownik pisze w polu albo gdy modal jest otwarty
        if (e.target.matches('input, select, textarea')) return;
        if (document.querySelector('.modal-backdrop:not([hidden])')) return;
        if (e.key === 'ArrowLeft') { moveMapSource(-1); e.preventDefault(); }
        else if (e.key === 'ArrowRight') { moveMapSource(1); e.preventDefault(); }
    });
})();

// ─── LIVE COORDINATES ───────────────────────────
const latVal = document.getElementById('lat-val');
const lonVal = document.getElementById('lon-val');

function updateCoords() {
    const c = map.getCenter();
    latVal.textContent = c.lat.toFixed(6);
    lonVal.textContent = c.lng.toFixed(6);
}
map.on('move', updateCoords);
updateCoords();

// ─── SQUARE AREA SLIDER ─────────────────────────
const widthSlider = document.getElementById('width-slider');
const heightSlider = document.getElementById('height-slider');
const widthDisplay = document.getElementById('width-display');
const heightDisplay = document.getElementById('height-display');
const sizeLabel = document.getElementById('size-label');
const crosshair = document.getElementById('crosshair');

function clampScanSize(value) {
    const numericValue = Number.isFinite(Number(value)) ? Number(value) : SCAN_AREA_MIN_M;
    const snapped = Math.round(numericValue / SCAN_AREA_STEP_M) * SCAN_AREA_STEP_M;
    return Math.max(SCAN_AREA_MIN_M, Math.min(SCAN_AREA_MAX_M, snapped));
}

function configureScanAreaControls() {
    [widthSlider, heightSlider].filter(Boolean).forEach(slider => {
        slider.min = String(SCAN_AREA_MIN_M);
        slider.max = String(SCAN_AREA_MAX_M);
        slider.step = String(SCAN_AREA_STEP_M);
        slider.value = String(clampScanSize(slider.value));
    });
    widthSlider.hidden = SCAN_AREA_MIN_M === SCAN_AREA_MAX_M;
}

function onSliderChange() {
    currentWidth = clampScanSize(widthSlider.value);
    currentHeight = currentWidth;
    widthSlider.value = String(currentWidth);
    heightSlider.value = String(currentWidth);
    widthDisplay.textContent = `${currentWidth} m`;
    heightDisplay.textContent = `${currentHeight} m`;
    sizeLabel.textContent = `${currentWidth} × ${currentWidth} m`;
    updateCrosshairSize();
}
configureScanAreaControls();
widthSlider.addEventListener('input', onSliderChange);
heightSlider.addEventListener('input', () => {
    widthSlider.value = heightSlider.value;
    onSliderChange();
});

function updateCrosshairSize() {
    // Approximate pixel size of the crosshair at current zoom — osobno W i H
    const center = map.getCenter();
    const metersPerPixel = 40075016.686 * Math.cos(center.lat * Math.PI / 180) / Math.pow(2, map.getZoom() + 8);
    const viewportCap = Math.max(
        CROSSHAIR_MIN_PX,
        Math.min(window.innerWidth, window.innerHeight) * CROSSHAIR_MAX_VIEWPORT_RATIO
    );
    const pxW = Math.max(CROSSHAIR_MIN_PX, Math.min(viewportCap, currentWidth / metersPerPixel));
    const pxH = Math.max(CROSSHAIR_MIN_PX, Math.min(viewportCap, currentHeight / metersPerPixel));
    const ring = document.querySelector('#crosshair .ring');
    ring.style.width = pxW + 'px';
    ring.style.height = pxH + 'px';
}

map.on('zoom', updateCrosshairSize);
map.on('move', updateCrosshairSize);
window.addEventListener('resize', updateCrosshairSize);
updateCrosshairSize();

let crosshairHiddenByPopup = false;
let crosshairHiddenByContextMenu = false;
let crosshairManuallyHidden = localStorage.getItem(CROSSHAIR_HIDDEN_STORAGE_KEY) === '1';

function updateCrosshairVisibility() {
    crosshair?.classList.toggle(
        'is-hidden',
        crosshairManuallyHidden || crosshairHiddenByPopup || crosshairHiddenByContextMenu
    );
}

function updateContextCrosshairLabel() {
    const label = document.getElementById('context-crosshair-label');
    if (!label) return;
    label.textContent = t(crosshairManuallyHidden ? 'context.showCrosshair' : 'context.hideCrosshair');
}

function setCrosshairManuallyHidden(hidden) {
    crosshairManuallyHidden = Boolean(hidden);
    localStorage.setItem(CROSSHAIR_HIDDEN_STORAGE_KEY, crosshairManuallyHidden ? '1' : '0');
    updateContextCrosshairLabel();
    updateCrosshairVisibility();
}

function toggleCrosshairFromContextMenu() {
    setCrosshairManuallyHidden(!crosshairManuallyHidden);
    closeMapContextMenu();
}

map.on('popupopen', () => {
    crosshairHiddenByPopup = true;
    closeMapContextMenu();
    updateCrosshairVisibility();
});
map.on('popupclose', () => {
    crosshairHiddenByPopup = false;
    updateCrosshairVisibility();
});

// ─── MAP CONTEXT MENU ──────────────────────────
const mapContextMenu = document.getElementById('map-context-menu');
const contextMenuCoords = document.getElementById('context-menu-coords');
let contextMenuLatLng = null;
let activeCadastralParcel = null;

const CADASTRAL_LAND_USE_LABEL_KEYS = {
    B: 'context.landUse.B',
    Ba: 'context.landUse.Ba',
    Bi: 'context.landUse.Bi',
    Bp: 'context.landUse.Bp',
    Bz: 'context.landUse.Bz',
    dr: 'context.landUse.dr',
    K: 'context.landUse.K',
    Ls: 'context.landUse.Ls',
    Lz: 'context.landUse.Lz',
    N: 'context.landUse.N',
    R: 'context.landUse.R',
    S: 'context.landUse.S',
    Tk: 'context.landUse.Tk',
    Ti: 'context.landUse.Ti',
    Tp: 'context.landUse.Tp',
    Tr: 'context.landUse.Tr',
    W: 'context.landUse.W',
    Wp: 'context.landUse.Wp',
    Ws: 'context.landUse.Ws',
    Ł: 'context.landUse.Laka',
    Ps: 'context.landUse.Ps',
};

function closeMapContextMenu() {
    if (!mapContextMenu || mapContextMenu.hidden) return;
    mapContextMenu.hidden = true;
    contextMenuLatLng = null;
    crosshairHiddenByContextMenu = false;
    updateCrosshairVisibility();
}

function openMapContextMenu(e) {
    if (!mapContextMenu || !contextMenuCoords) return;
    contextMenuLatLng = e.latlng;
    contextMenuCoords.textContent = `${contextMenuLatLng.lat.toFixed(6)}, ${contextMenuLatLng.lng.toFixed(6)}`;
    updateContextCrosshairLabel();
    mapContextMenu.hidden = false;

    const originalEvent = e.originalEvent;
    const margin = 8;
    const menuWidth = mapContextMenu.offsetWidth || 210;
    const menuHeight = mapContextMenu.offsetHeight || 110;
    const x = Math.min(originalEvent.clientX, window.innerWidth - menuWidth - margin);
    const y = Math.min(originalEvent.clientY, window.innerHeight - menuHeight - margin);
    mapContextMenu.style.left = `${Math.max(margin, x)}px`;
    mapContextMenu.style.top = `${Math.max(margin, y)}px`;

    crosshairHiddenByContextMenu = true;
    updateCrosshairVisibility();
}
updateCrosshairVisibility();
document.addEventListener('langchange', updateContextCrosshairLabel);

map.on('contextmenu', (e) => {
    e.originalEvent.preventDefault();
    openMapContextMenu(e);
});
document.addEventListener('click', (e) => {
    if (!mapContextMenu || mapContextMenu.hidden || mapContextMenu.contains(e.target)) return;
    closeMapContextMenu();
});
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMapContextMenu();
});

function centerScanOnContextPoint() {
    if (!contextMenuLatLng) return;
    map.panTo(contextMenuLatLng);
    closeMapContextMenu();
}

async function openFieldPhotoUploadAtContextPoint() {
    if (!contextMenuLatLng) return;
    const fallbackLatLng = L.latLng(contextMenuLatLng.lat, contextMenuLatLng.lng);
    closeMapContextMenu();
    await openFieldPhotoUploadModal({
        fallbackLatLng,
        ignoreExifGps: true,
    });
}

function cadastralCodeLabel(code) {
    const sourceCode = String(code || '').trim();
    if (!sourceCode) return '';
    const labelKey = CADASTRAL_LAND_USE_LABEL_KEYS[sourceCode]
        || CADASTRAL_LAND_USE_LABEL_KEYS[sourceCode.toLowerCase()]
        || CADASTRAL_LAND_USE_LABEL_KEYS[sourceCode.toUpperCase()];
    return labelKey ? `${sourceCode} - ${t(labelKey)}` : sourceCode;
}

function cadastralParcelGeoportalUrl(parcel = {}) {
    const parcelId = String(parcel.parcel_id || '').trim();
    return parcelId ? `https://mapy.geoportal.gov.pl/mobile/?identifyParcel=${encodeURIComponent(parcelId)}` : '';
}

function cadastralParcelClipboardText(parcel = {}) {
    const primaryTerrain = cadastralCodeLabel(parcel.land_use || parcel.contour);
    const geoportalUrl = cadastralParcelGeoportalUrl(parcel);
    const lines = [
        `${t('context.parcelTitle')}: ${parcel.parcel_number || '-'}`,
        `${t('context.parcelTerrainType')}: ${primaryTerrain || '-'}`,
        `${t('context.parcelId')}: ${parcel.parcel_id || '-'}`,
        `${t('context.parcelDistrict')}: ${parcel.district || '-'}`,
        `${t('context.parcelMunicipality')}: ${parcel.municipality || '-'}`,
        `${t('context.parcelCounty')}: ${parcel.county || '-'}`,
        `${t('context.parcelArea')}: ${parcel.area_ha ? `${parcel.area_ha} ha` : '-'}`,
        parcel.registry_group ? `${t('context.parcelRegistryGroup')}: ${parcel.registry_group}` : '',
        `${t('context.parcelPublishedAt')}: ${parcel.published_at || '-'}`,
        geoportalUrl ? `Geoportal: ${geoportalUrl}` : '',
    ];
    return lines.filter(Boolean).join('\n');
}

async function copyActiveCadastralParcel() {
    if (!activeCadastralParcel) return;
    try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(cadastralParcelClipboardText(activeCadastralParcel));
        statusEl.textContent = t('context.parcelCopied');
        statusEl.className = 'ok';
    } catch (_) {
        statusEl.textContent = t('context.copyError');
        statusEl.className = 'err';
    }
}

function cadastralParcelPopup(parcel = {}) {
    const terrainType = cadastralCodeLabel(parcel.land_use || parcel.contour);
    const landUse = cadastralCodeLabel(parcel.land_use);
    const contourLabel = cadastralCodeLabel(parcel.contour);
    const contour = contourLabel && contourLabel !== terrainType && contourLabel !== landUse ? contourLabel : '';
    const rows = [
        [t('context.parcelTerrainType'), terrainType],
        [t('context.parcelLandUse'), landUse],
        [t('context.parcelContour'), contour],
        [t('context.parcelNumber'), parcel.parcel_number],
        [t('context.parcelId'), parcel.parcel_id],
        [t('context.parcelDistrict'), parcel.district],
        [t('context.parcelMunicipality'), parcel.municipality],
        [t('context.parcelCounty'), parcel.county],
        [t('context.parcelVoivodeship'), parcel.voivodeship],
        [t('context.parcelArea'), parcel.area_ha ? `${parcel.area_ha} ha` : ''],
        [t('context.parcelRegistryGroup'), parcel.registry_group],
        [t('context.parcelPublishedAt'), parcel.published_at],
    ].filter(([, value]) => value);
    const rowHtml = rows.map(([label, value]) => `
        <div class="parcel-popup-row">
            <span>${escapeHtml(label)}</span>
            <strong>${escapeHtml(value)}</strong>
        </div>
    `).join('');
    const geoportalUrl = cadastralParcelGeoportalUrl(parcel);
    const links = popupLinks([
        popupCompactLink(geoportalUrl, t('context.parcelOpenGeoportal'), t('context.parcelOpenGeoportal')),
    ]);
    const actions = popupActions([
        `<button type="button" class="map-popup-text-action" onclick="copyActiveCadastralParcel()">${escapeHtml(t('context.parcelCopyData'))}</button>`,
    ]);
    return `
        <div class="map-popup map-popup--parcel">
            ${popupHeader(t('context.parcelTitle'), parcel.parcel_number || '')}
            <div class="parcel-popup-rows">${rowHtml}</div>
            ${links}
            ${actions}
        </div>
    `;
}

async function identifyCadastralParcelAtContextPoint() {
    if (!contextMenuLatLng) return;
    const latLng = L.latLng(contextMenuLatLng.lat, contextMenuLatLng.lng);
    closeMapContextMenu();
    activeCadastralParcel = null;
    const popup = L.popup({ maxWidth: 340 })
        .setLatLng(latLng)
        .setContent(`<div class="map-popup map-popup--parcel">${escapeHtml(t('context.identifyingParcel'))}</div>`)
        .openOn(map);
    try {
        const url = `${CADASTRAL_IDENTIFY_URL}?lat=${encodeURIComponent(latLng.lat.toFixed(8))}&lon=${encodeURIComponent(latLng.lng.toFixed(8))}`;
        const response = await fetch(url, { cache: 'no-store' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'ok') {
            throw new Error(data.error || t('context.parcelError'));
        }
        activeCadastralParcel = data.parcel || {};
        popup.setContent(cadastralParcelPopup(activeCadastralParcel));
    } catch (err) {
        popup.setContent(`
            <div class="map-popup map-popup--parcel">
                ${popupHeader(t('context.parcelTitle'))}
                <p class="parcel-popup-hint">${escapeHtml(err.message || t('context.parcelError'))}</p>
            </div>
        `);
    }
}

async function copyContextCoords() {
    if (!contextMenuLatLng) return;
    const text = `${contextMenuLatLng.lat.toFixed(6)}, ${contextMenuLatLng.lng.toFixed(6)}`;
    try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(text);
        statusEl.textContent = t('context.copiedCoords');
        statusEl.className = 'ok';
    } catch (_) {
        statusEl.textContent = t('context.copyError');
        statusEl.className = 'err';
    }
    closeMapContextMenu();
}

async function copyContextPlaceLink() {
    if (!contextMenuLatLng) return;
    const text = appPlaceUrl(contextMenuLatLng.lat, contextMenuLatLng.lng, map.getZoom());
    try {
        if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
        await navigator.clipboard.writeText(text);
        statusEl.textContent = t('context.copiedPlaceLink');
        statusEl.className = 'ok';
    } catch (_) {
        statusEl.textContent = t('context.copyError');
        statusEl.className = 'err';
    }
    closeMapContextMenu();
}

// ─── SHIFT+DRAG → ZAZNACZ KWADRATOWY OBSZAR ─────
// Pozycja z mapy jest tylko środkiem; analiza zawsze pobiera kwadrat.
let selectStart = null;
let selectRect = null;

map.on('mousedown', (e) => {
    if (!e.originalEvent.shiftKey) return;
    selectStart = e.latlng;
    map.dragging.disable();
    if (selectRect) { map.removeLayer(selectRect); selectRect = null; }
});

map.on('mousemove', (e) => {
    if (!selectStart) return;
    const bounds = squareBounds(selectStart, e.latlng);
    if (!selectRect) {
        selectRect = L.rectangle(bounds, { color: '#fbbf24', weight: 2, fillOpacity: 0.1, dashArray: '5,5' }).addTo(map);
    } else {
        selectRect.setBounds(bounds);
    }
});

map.on('mouseup', (e) => {
    if (!selectStart) return;
    map.dragging.enable();
    selectStart = null;
    if (!selectRect) return;
    const b = selectRect.getBounds();
    const center = b.getCenter();
    const { dLon: wM } = metersBetweenLatLng(b.getSouthWest(), b.getSouthEast());
    const { dLat: hM } = metersBetweenLatLng(b.getNorthWest(), b.getSouthWest());
    const snap = clampScanSize;
    const sizeM = Math.max(wM, hM);
    map.removeLayer(selectRect);
    selectRect = null;
    map.setView(center, map.getZoom());
    widthSlider.value = snap(sizeM);
    heightSlider.value = widthSlider.value;
    onSliderChange();
});

// ─── PIPELINE (klein i analiza w jednym) ────────
const btnRun = document.getElementById('btn-run');
const spinner = document.getElementById('spinner');
const runIcon = document.getElementById('run-icon');
const statusEl = document.getElementById('status');
const progressEl = document.getElementById('progress');
const btnReport = document.getElementById('btn-report');
const reportLabelEl = document.getElementById('report-label');
const resultActions = document.getElementById('result-actions');

let candidateMarkers = [];
let savedWreckMarkers = [];
let fieldPhotoMarkers = [];
let savedWreckLayerData = [];
let fieldPhotoLayerData = [];
let savedWreckLayerVisible = true;
let fieldPhotoIssueFilters = Object.fromEntries(Array.from(FIELD_PHOTO_ISSUE_TYPES, issueType => [issueType, true]));
let fieldPhotoUploadItems = [];
let fieldPhotoUploadFallbackLatLng = null;
let photoReviewItems = [];
let photoReviewSearchTimer = null;
let photoReviewExactPhotoIds = [];
let activePhotoReview = null;
let photoReviewImage = null;
let photoReviewRedactions = [];
let activePhotoReviewRedactionIndex = -1;
let photoReviewDraftRect = null;
let photoReviewDrawing = false;
let privacyRequestItems = [];
let activePrivacyRequest = null;
let reportPackageExtraPhotos = [];
let imageOverlay = null;
let scanArea = null;
let downloadProgressTimer = null;

// ─── STRUKTURALNY POSTĘP ──────────────────────
// Każdy krok: pending → active → done | error. Timer leci tylko w stanie active.
const stepTimers = {};

function setStep(id, state, label = null, meta = null) {
    const el = document.getElementById('step-' + id);
    if (!el) return;
    el.classList.remove('active', 'done', 'error');
    if (state !== 'pending') el.classList.add(state);

    if (label) el.querySelector('.step-label').textContent = label;

    const metaEl = el.querySelector('.step-meta');
    if (meta) {
        metaEl.textContent = meta;
        metaEl.classList.add('show');
    } else {
        metaEl.textContent = '';
        metaEl.classList.remove('show');
    }

    // Timer
    if (stepTimers[id]) {
        clearInterval(stepTimers[id].interval);
        delete stepTimers[id];
    }
    if (state === 'active') {
        const timeEl = el.querySelector('.step-time');
        const start = Date.now();
        timeEl.textContent = '0:00';
        stepTimers[id] = {
            interval: setInterval(() => {
                const s = Math.floor((Date.now() - start) / 1000);
                timeEl.textContent = `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
            }, STEP_TIMER_INTERVAL_MS),
        };
    }
}

function setStepMeta(id, meta) {
    const el = document.getElementById('step-' + id);
    const metaEl = el?.querySelector('.step-meta');
    if (!metaEl) return;
    if (meta) {
        metaEl.textContent = meta;
        metaEl.classList.add('show');
    } else {
        metaEl.textContent = '';
        metaEl.classList.remove('show');
    }
}

function setStepProgress(id, percent = null, indeterminate = false) {
    const el = document.getElementById('step-' + id);
    const bar = el?.querySelector('.step-progress');
    const fill = bar?.querySelector('div');
    if (!bar || !fill) return;
    const show = indeterminate || Number.isFinite(percent);
    bar.classList.toggle('show', show);
    bar.classList.toggle('indeterminate', Boolean(indeterminate));
    if (Number.isFinite(percent)) {
        fill.style.width = `${Math.max(0, Math.min(100, percent))}%`;
    } else if (!indeterminate) {
        fill.style.width = '0%';
    }
}

function formatBytes(bytes) {
    const n = Number(bytes);
    if (!Number.isFinite(n) || n <= 0) return '';
    if (n >= 1024 * 1024 * 1024) return `${(n / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(0)} MB`;
    return `${(n / 1024).toFixed(0)} KB`;
}

function downloadProgressMeta(data) {
    if (!data || data.status !== 'active') return null;
    if (data.stage === 'wfs_download') {
        const done = formatBytes(data.bytes_done);
        const total = formatBytes(data.bytes_total);
        if (done && total) return `${data.message} · ${done}/${total}`;
        return data.message || t('step.download.wfsDownloading');
    }
    if (data.stage === 'wfs_cache') return data.message || t('step.download.wfsCache');
    return data.message || null;
}

function startDownloadProgressPolling() {
    stopDownloadProgressPolling();
    const poll = async () => {
        try {
            const resp = await fetch(`${DOWNLOAD_PROGRESS_URL}?ts=${Date.now()}`, { cache: 'no-store' });
            const data = await resp.json();
            if (data.status === 'active') {
                const percent = Number.isFinite(Number(data.percent)) ? Number(data.percent) : null;
                const indeterminate = percent === null;
                const meta = downloadProgressMeta(data);
                if (meta) setStepMeta('download', meta);
                setStepProgress('download', percent, indeterminate);
            }
        } catch (_) {
            setStepProgress('download', null, true);
        }
    };
    poll();
    downloadProgressTimer = setInterval(poll, DOWNLOAD_PROGRESS_POLL_MS);
}

function stopDownloadProgressPolling() {
    if (downloadProgressTimer) {
        clearInterval(downloadProgressTimer);
        downloadProgressTimer = null;
    }
}

function resetProgress() {
    ['download', 'detect'].forEach(id => {
        const el = document.getElementById('step-' + id);
        el.classList.remove('active', 'done', 'error');
        el.querySelector('.step-time').textContent = '';
        const metaEl = el.querySelector('.step-meta');
        metaEl.textContent = '';
        metaEl.classList.remove('show');
        setStepProgress(id, null, false);
        if (stepTimers[id]) { clearInterval(stepTimers[id].interval); delete stepTimers[id]; }
    });
    // Przywróć oryginalne labelki (mogły zostać podmienione na komunikat błędu)
    document.querySelector('#step-download .step-label').textContent = t('step.download.label');
    document.querySelector('#step-detect .step-label').textContent = t('step.detect.label');
    progressEl.hidden = true;
    resultActions.hidden = true;
}

function clearCandidateMarkers() {
    candidateMarkers.forEach(m => map.removeLayer(m));
    candidateMarkers = [];
}

function clearSavedWreckMarkers() {
    savedWreckMarkers.forEach(m => map.removeLayer(m));
    savedWreckMarkers = [];
}

function clearFieldPhotoMarkers() {
    fieldPhotoMarkers.forEach(m => map.removeLayer(m));
    fieldPhotoMarkers = [];
}

function clearResults() {
    clearCandidateMarkers();
    currentJobToken = null;
    if (imageOverlay) {
        map.removeLayer(imageOverlay);
        imageOverlay = null;
    }
    if (scanArea) {
        map.removeLayer(scanArea);
        scanArea = null;
    }
    statusEl.textContent = '';
    statusEl.className = '';
    resetProgress();
}

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#039;',
    }[ch]));
}

function safeWreckId(value) {
    return String(value ?? '').replace(/[^A-Za-z0-9_-]/g, '');
}

function safeFieldPhotoId(value) {
    return String(value ?? '').replace(/[^A-Za-z0-9_-]/g, '');
}

function fieldPhotoIssueType(photo) {
    const issueType = String(photo?.issue_type || FIELD_PHOTO_ISSUE_TYPE_VEHICLE);
    return FIELD_PHOTO_ISSUE_TYPES.has(issueType) ? issueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
}

function fieldPhotoIssueLabel(issueType) {
    return t(`fieldPhoto.issueType.${FIELD_PHOTO_ISSUE_TYPES.has(issueType) ? issueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE}`);
}

function fieldPhotoIssueVisible(issueType) {
    const safeIssueType = FIELD_PHOTO_ISSUE_TYPES.has(issueType) ? issueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    return publicLayerAllowed(fieldPhotoPublicLayerKey(safeIssueType)) && fieldPhotoIssueFilters[safeIssueType] !== false;
}

function filteredFieldPhotos(photos = fieldPhotoLayerData) {
    return (photos || []).filter(photo => fieldPhotoIssueVisible(fieldPhotoIssueType(photo)));
}

function pinIcon(rank, score) {
    const color = score > 0.7 ? '#10b981' : score > 0.55 ? '#f59e0b' : '#ef4444';
    const html = `<div style="
        background:${color}; color:#000; font-weight:800; font-size:13px;
        width:30px; height:30px; border-radius:50% 50% 50% 0;
        transform:rotate(-45deg); display:flex; align-items:center; justify-content:center;
        border:2px solid #fff; box-shadow:0 2px 8px rgba(0,0,0,0.5);">
        <span style="transform:rotate(45deg);">${rank}</span>
    </div>`;
    return L.divIcon({ html, className: '', iconSize: [30,30], iconAnchor:[15,30] });
}

function countBadge(count, className) {
    const numericCount = Math.max(0, Math.floor(Number(count) || 0));
    if (numericCount <= 0) return '';
    return `<span class="map-pin-count ${className}">${numericCount}</span>`;
}

function wreckIcon(photoCount = 0) {
    const numericCount = Math.max(0, Math.floor(Number(photoCount) || 0));
    const badge = countBadge(numericCount, 'saved-wreck-pin-count');
    const className = numericCount > 0 ? 'saved-wreck-pin saved-wreck-pin--with-photos' : 'saved-wreck-pin';
    const html = `<div class="${className}">${badge}</div>`;
    return L.divIcon({ html, className: 'map-pin-icon', iconSize: [34,34], iconAnchor:[17,34] });
}

function fieldPhotoIcon(count = 1, issueType = FIELD_PHOTO_ISSUE_TYPE_VEHICLE) {
    const photoCount = Math.max(1, Number(count) || 1);
    const badge = photoCount > 1 ? countBadge(photoCount, 'field-photo-pin-count') : '';
    const safeIssueType = FIELD_PHOTO_ISSUE_TYPES.has(issueType) ? issueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    const html = `<div class="field-photo-pin field-photo-pin--${safeIssueType}">${badge}</div>`;
    return L.divIcon({ html, className: 'map-pin-icon', iconSize: [34,34], iconAnchor:[17,34] });
}

function popupCompactLink(href, label, title) {
    if (!href) return '';
    return `<a href="${escapeHtml(href)}" target="_blank" rel="noopener" title="${escapeHtml(title || label)}">${escapeHtml(label)}</a>`;
}

function popupHeader(title, value = '') {
    const valueHtml = value ? `<span>${escapeHtml(value)}</span>` : '';
    return `
        <div class="map-popup-head">
            <strong>${escapeHtml(title)}</strong>
            ${valueHtml}
        </div>
    `;
}

function popupMeta(parts) {
    const items = (parts || []).filter(part => part !== undefined && part !== null && String(part).trim() !== '');
    if (!items.length) return '';
    return `<div class="map-popup-meta">${items.map(part => `<span>${escapeHtml(part)}</span>`).join('')}</div>`;
}

function popupLinks(links) {
    const html = (links || []).filter(Boolean).join(' · ');
    return html ? `<div class="map-popup-links">${html}</div>` : '';
}

function popupActions(actions) {
    const html = (actions || []).filter(Boolean).join('');
    return html ? `<div class="map-popup-actions">${html}</div>` : '';
}

function popupPhotoGrid(previews, { className = '', max = 6 } = {}) {
    const photos = Array.isArray(previews)
        ? previews.filter(photo => photo && photo.public_thumb).slice(0, max)
        : [];
    if (!photos.length) return '';
    const classAttr = ['map-popup-photo-grid', className].filter(Boolean).join(' ');
    return `
        <div class="${classAttr}">
            ${photos.map(photo => {
                const thumbUrl = escapeHtml(photo.public_thumb || '');
                const publicUrl = escapeHtml(photo.public_image || photo.public_thumb || '');
                const label = escapeHtml(photo.label || '');
                return `
                    <a class="map-popup-photo" href="${publicUrl}" target="_blank" rel="noopener" title="${label}">
                        <img src="${thumbUrl}" loading="lazy" alt="">
                        ${label ? `<span>${label}</span>` : ''}
                    </a>
                `;
            }).join('')}
        </div>
    `;
}

function popupPhotoSection(title, previews, options = {}) {
    const grid = popupPhotoGrid(previews, options);
    if (!grid) return '';
    return `
        <section class="map-popup-photo-section">
            <div class="map-popup-section-title">${escapeHtml(title)}</div>
            ${grid}
        </section>
    `;
}

function mapPopupIconAction(className, title, onclick, path) {
    return `
        <button type="button" class="map-popup-action ${className}" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}" onclick="${onclick}">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="${path}"/></svg>
        </button>
    `;
}

function savedWreckPopup(wreck) {
    const lat = Number(wreck.lat);
    const lon = Number(wreck.lon);
    const score = Number(wreck.best_score || 0);
    const years = (wreck.labels_present || []).join(', ');
    const wreckId = safeWreckId(wreck.id);
    const folder = wreck.folder_url;
    const links = wreck.links || {};
    const reportButton = mapPopupIconAction(
        'map-popup-action--report',
        t('wreck.reportPackage'),
        `openReportPackageModal('${wreckId}')`,
        'M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm1 7V3.5L18.5 9H15zM8 13h8v2H8v-2zm0 4h8v2H8v-2z'
    );
    const photoButton = mapPopupIconAction(
        'map-popup-action--photo',
        t('wreck.addPhotos'),
        `openWreckPhotoModal('${wreckId}')`,
        'M5 7h2.8L9.4 5h5.2l1.6 2H19c1.1 0 2 .9 2 2v10c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V9c0-1.1.9-2 2-2zm7 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm5-5h-2v2h-2v2h2v2h2v-2h2v-2h-2v-2z'
    );
    const deleteButton = adminAuthenticated
        ? mapPopupIconAction(
            'map-popup-action--delete',
            t('wreck.delete'),
            `deleteWreck('${wreckId}', this)`,
            'M9 3v1H4v2h16V4h-5V3H9zm-3 5l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13H6zm4 3h1v9h-1v-9zm3 0h1v9h-1v-9z'
        )
        : '';
    const reviewPhotosButton = adminAuthenticated
        ? mapPopupIconAction(
            'map-popup-action--photo',
            t('wreck.reviewPhotos'),
            `openPhotoReviewForWreck('${wreckId}')`,
            'M4 5h16v14H4V5zm2 2v10h12V7H6zm2 8h8l-2.5-3.2-1.8 2.2-1.3-1.5L8 15z'
        )
        : '';
    const approveButton = adminAuthenticated && wreck.public_review_status === 'pending'
        ? mapPopupIconAction(
            'map-popup-action--approve',
            t('wreck.approve'),
            `reviewWreckStatus('${wreckId}', 'approved', this)`,
            'M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z'
        )
        : '';
    const rejectButton = adminAuthenticated && wreck.public_review_status === 'pending'
        ? mapPopupIconAction(
            'map-popup-action--delete',
            t('wreck.reject'),
            `reviewWreckStatus('${wreckId}', 'rejected', this)`,
            'M18.3 5.7 12 12l6.3 6.3-1.4 1.4-6.3-6.3-6.3 6.3-1.4-1.4L10.6 12 4.3 5.7l1.4-1.4 6.3 6.3 6.3-6.3 1.4 1.4z'
        )
        : '';
    const compactLinks = [
        popupCompactLink(folder, t('wreck.openCaseShort'), t('wreck.openFolder')),
        popupCompactLink(links.street_view, 'SV', t('popup.streetView')),
        popupCompactLink(links.google_maps_satellite, 'Sat', t('popup.gmapsSat')),
        popupCompactLink(links.geoportal, 'Geoportal', t('popup.geoportal')),
    ];
    return `
        <div class="map-popup map-popup--vehicle-case">
            ${popupHeader(t('wreck.popup.title'), `${(score * 100).toFixed(0)}%`)}
            ${popupMeta([years || '-', `${lat.toFixed(6)}, ${lon.toFixed(6)}`])}
            ${popupPhotoSection(t('wreck.popup.fieldPhotos'), wreck.field_photo_previews, { className: 'map-popup-photo-grid--field', max: 3 })}
            ${popupPhotoSection(t('wreck.popup.evidencePreviews'), wreck.evidence_previews, { className: 'map-popup-photo-grid--evidence', max: 6 })}
            ${popupLinks(compactLinks)}
            ${popupActions([reportButton, photoButton, reviewPhotosButton, approveButton, rejectButton, deleteButton])}
        </div>
    `;
}

function placeSavedWrecks(wrecks = savedWreckLayerData) {
    clearSavedWreckMarkers();
    if (!savedWreckLayerVisible || !publicLayerAllowed(PUBLIC_LAYER_KEYS.savedWrecks)) return;
    wrecks.forEach(wreck => {
        const lat = Number(wreck.lat);
        const lon = Number(wreck.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
        const marker = L.marker([lat, lon], {
            icon: wreckIcon(wreck.photo_count),
            zIndexOffset: 1200,
        }).addTo(map).bindPopup(savedWreckPopup(wreck));
        savedWreckMarkers.push(marker);
    });
}

async function loadSavedWrecks() {
    try {
        const resp = await fetch(`${WRECKS_URL}?ts=${Date.now()}`, { cache: 'no-store' });
        const data = await resp.json();
        if (resp.ok && data.status === 'ok') {
            savedWreckLayerData = data.wrecks || [];
            placeSavedWrecks(savedWreckLayerData);
            updateLingeringCarsCounter();
        }
    } catch (_) {}
}

function toggleSavedWreckLayer(visible) {
    savedWreckLayerVisible = Boolean(visible);
    placeSavedWrecks(savedWreckLayerData);
}

async function saveWreck(rank, button = null) {
    const btn = button instanceof HTMLElement ? button : null;
    if (btn) {
        btn.disabled = true;
        btn.textContent = t('wreck.saving');
    }
    try {
        const resp = await fetch(WRECKS_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ rank }),
        });
        const data = await resp.json();
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('wreck.saveError'));
        }
        await loadSavedWrecks();
        if (btn) btn.textContent = data.evidence_created ? t('wreck.savedShort') : t('wreck.alreadySavedShort');
        statusEl.textContent = data.evidence_created ? t('wreck.saved') : t('wreck.alreadySaved');
        statusEl.className = 'ok';
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = t('wreck.save');
        }
        statusEl.textContent = err.message;
        statusEl.className = 'err';
    }
}

async function saveManualWreck(lat, lon, button = null) {
    const latNumber = Number(lat);
    const lonNumber = Number(lon);
    if (!Number.isFinite(latNumber) || !Number.isFinite(lonNumber)) return;

    const btn = button instanceof HTMLElement ? button : null;
    if (btn) {
        btn.disabled = true;
        btn.textContent = t('inspect.savingWreck');
    }
    try {
        const resp = await fetch(WRECKS_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lat: latNumber, lon: lonNumber }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('inspect.saveWreckError'));
        }
        await loadSavedWrecks();
        const created = Boolean(data.created);
        if (btn) btn.textContent = created ? t('inspect.savedWreck') : t('inspect.alreadySavedWreck');
        statusEl.textContent = created ? t('inspect.savedWreck') : t('inspect.alreadySavedWreck');
        statusEl.className = 'ok';
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = t('inspect.saveWreck');
        }
        statusEl.textContent = err.message || t('inspect.saveWreckError');
        statusEl.className = 'err';
    }
}

async function deleteWreck(wreckId, button = null) {
    if (!(await ensureAdmin())) return;
    const id = safeWreckId(wreckId);
    if (!id) return;
    const confirmed = await confirmAction({
        title: t('wreck.deleteTitle'),
        message: t('wreck.deleteConfirm'),
        confirmLabel: t('wreck.delete'),
    });
    if (!confirmed) return;

    const btn = button instanceof HTMLElement ? button : null;
    if (btn) {
        btn.disabled = true;
        btn.textContent = t('wreck.deleting');
    }
    try {
        const resp = await fetch(`${WRECKS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('wreck.deleteError'));
        }
        await loadSavedWrecks();
        statusEl.textContent = t('wreck.deleted');
        statusEl.className = 'ok';
    } catch (err) {
        if (btn) {
            btn.disabled = false;
            btn.textContent = t('wreck.delete');
        }
        statusEl.textContent = err.message || t('wreck.deleteError');
        statusEl.className = 'err';
    }
}

async function reviewWreckStatus(wreckId, publicReviewStatus, button = null) {
    if (!(await ensureAdmin())) return;
    const id = safeWreckId(wreckId);
    if (!id) return;
    const btn = button instanceof HTMLElement ? button : null;
    if (btn) btn.disabled = true;
    try {
        const resp = await fetch(`${ADMIN_WRECKS_URL}/${encodeURIComponent(id)}/review`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ public_review_status: publicReviewStatus }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('wreck.reviewError'));
        }
        await loadSavedWrecks();
        statusEl.textContent = publicReviewStatus === 'approved' ? t('wreck.approved') : t('wreck.rejected');
        statusEl.className = 'ok';
    } catch (err) {
        statusEl.textContent = err.message || t('wreck.reviewError');
        statusEl.className = 'err';
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function openPhotoReviewForWreck(wreckId) {
    if (!(await ensureAdmin())) return;
    const id = safeWreckId(wreckId);
    if (!id) return;
    photoReviewExactPhotoIds = [];
    openModal('modal-photo-review');
    const filter = document.getElementById('photo-review-filter');
    const scope = document.getElementById('photo-review-scope');
    const search = document.getElementById('photo-review-search');
    if (filter) filter.value = 'all';
    if (scope) scope.value = 'wreck';
    if (search) search.value = id;
    await loadPhotoReviewQueue();
}

async function openPhotoReviewForFieldPhotoGroup(encodedPhotoIds) {
    if (!(await ensureAdmin())) return;
    const photoIds = decodeFieldPhotoIds(encodedPhotoIds);
    if (!photoIds.length) return;
    photoReviewExactPhotoIds = photoIds;
    openModal('modal-photo-review');
    const filter = document.getElementById('photo-review-filter');
    const scope = document.getElementById('photo-review-scope');
    const search = document.getElementById('photo-review-search');
    if (filter) filter.value = 'all';
    if (scope) scope.value = 'field';
    if (search) search.value = photoIds.join(', ');
    await loadPhotoReviewQueue();
}

function updateFieldPhotoFallbackText() {
    const el = document.getElementById('field-photo-fallback');
    if (!el) return;
    const point = fieldPhotoUploadFallbackLatLng || (typeof map !== 'undefined' ? map.getCenter() : null);
    if (!point) return;
    el.textContent = t('modal.fieldPhoto.fallbackCoords', {
        lat: Number(point.lat).toFixed(6),
        lon: Number(point.lng).toFixed(6),
    });
}

function currentFieldPhotoUploadFallbackLatLng() {
    return fieldPhotoUploadFallbackLatLng || map.getCenter();
}

function resetFieldPhotoUploadModal(options = {}) {
    const form = document.getElementById('field-photo-form');
    const status = document.getElementById('field-photo-status');
    const submit = document.getElementById('field-photo-submit');
    const queue = document.getElementById('field-photo-queue');
    const retry = document.getElementById('field-photo-retry');
    const ignoreExif = document.getElementById('field-photo-ignore-exif');
    const filesInput = document.getElementById('field-photo-files');
    fieldPhotoUploadItems = [];
    form?.reset();
    updateFilePickerSummary(filesInput);
    const rawFallback = options.fallbackLatLng;
    fieldPhotoUploadFallbackLatLng = rawFallback && Number.isFinite(Number(rawFallback.lat)) && Number.isFinite(Number(rawFallback.lng))
        ? L.latLng(Number(rawFallback.lat), Number(rawFallback.lng))
        : (typeof map !== 'undefined' ? map.getCenter() : null);
    if (ignoreExif) ignoreExif.checked = Boolean(options.ignoreExifGps);
    updateFieldPhotoFallbackText();
    if (status) status.textContent = '';
    if (queue) {
        queue.hidden = true;
        queue.innerHTML = '';
    }
    if (retry) {
        retry.hidden = true;
        retry.disabled = false;
    }
    if (submit) {
        submit.disabled = false;
        submit.querySelector('span').textContent = t('modal.fieldPhoto.submit');
    }
}

async function openFieldPhotoUploadModal(options = {}) {
    resetFieldPhotoUploadModal(options);
    openModal('modal-field-photo-upload');
}

function fieldPhotoFileSizeLabel(bytes) {
    const size = Number(bytes) || 0;
    if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
    if (size >= 1024) return `${Math.ceil(size / 1024)} KB`;
    return `${size} B`;
}

function fieldPhotoValidationError(file) {
    if (file.size > FIELD_PHOTO_MAX_BYTES) {
        return t('modal.fieldPhoto.fileLimitError');
    }
    if (file.type && !FIELD_PHOTO_ALLOWED_TYPES.has(file.type)) {
        return t('modal.fieldPhoto.fileTypeError');
    }
    return '';
}

function validateFieldPhotoFiles(files) {
    const photoFiles = Array.from(files || []);
    if (!photoFiles.length) {
        throw new Error(t('modal.fieldPhoto.noFiles'));
    }
    if (photoFiles.length > FIELD_PHOTO_MAX_FILES) {
        throw new Error(t('modal.fieldPhoto.fileCountError', { n: FIELD_PHOTO_MAX_FILES }));
    }
    return photoFiles.map((file, index) => {
        const error = fieldPhotoValidationError(file);
        return {
            file,
            index,
            status: error ? 'error' : 'pending',
            message: error,
            validationError: Boolean(error),
            fallbackLat: null,
            fallbackLon: null,
            ignoreExifGps: false,
        };
    });
}

function fieldPhotoQueueStatusLabel(item) {
    if (item.status === 'saved') return t('modal.fieldPhoto.queueSaved');
    if (item.status === 'uploading') return t('modal.fieldPhoto.queueUploading');
    if (item.status === 'error') return item.message || t('fieldPhoto.saveError');
    return t('modal.fieldPhoto.queuePending');
}

function updateFieldPhotoRetryButton(uploading = false) {
    const retry = document.getElementById('field-photo-retry');
    if (!retry) return;
    const hasRetryable = fieldPhotoUploadItems.some(item => item.status === 'error' && !item.validationError);
    retry.hidden = !hasRetryable;
    retry.disabled = uploading;
}

function renderFieldPhotoQueue(uploading = false) {
    const queue = document.getElementById('field-photo-queue');
    if (!queue) return;
    if (!fieldPhotoUploadItems.length) {
        queue.hidden = true;
        queue.innerHTML = '';
        updateFieldPhotoRetryButton(uploading);
        return;
    }
    queue.hidden = false;
    queue.innerHTML = fieldPhotoUploadItems.map(item => `
        <div class="field-photo-queue-item field-photo-queue-item--${item.status}">
            <span class="field-photo-queue-name">${escapeHtml(item.file.name || '-')}</span>
            <span class="field-photo-queue-size">${escapeHtml(fieldPhotoFileSizeLabel(item.file.size))}</span>
            <span class="field-photo-queue-status">${escapeHtml(fieldPhotoQueueStatusLabel(item))}</span>
        </div>
    `).join('');
    updateFieldPhotoRetryButton(uploading);
}

function fieldPhotoUploadSummary() {
    const saved = fieldPhotoUploadItems.filter(item => item.status === 'saved').length;
    const failed = fieldPhotoUploadItems.filter(item => item.status === 'error').length;
    return { saved, failed, total: fieldPhotoUploadItems.length };
}

async function uploadFieldPhotoItems(items) {
    const input = document.getElementById('field-photo-files');
    const status = document.getElementById('field-photo-status');
    const submit = document.getElementById('field-photo-submit');
    if (submit) {
        submit.disabled = true;
        submit.querySelector('span').textContent = t('modal.fieldPhoto.uploading');
    }
    updateFieldPhotoRetryButton(true);

    let attempted = 0;
    for (const item of items) {
        item.status = 'uploading';
        item.message = '';
        renderFieldPhotoQueue(true);
        if (status) status.textContent = t('modal.fieldPhoto.uploadProgress', { done: attempted + 1, total: items.length });
        const formData = new FormData();
        formData.append('fallback_lat', item.fallbackLat);
        formData.append('fallback_lon', item.fallbackLon);
        formData.append('ignore_exif_gps', item.ignoreExifGps ? '1' : '0');
        formData.append('issue_type', item.issueType || FIELD_PHOTO_ISSUE_TYPE_VEHICLE);
        formData.append('photo', item.file);
        try {
            const resp = await fetch(FIELD_PHOTOS_URL, { method: 'POST', body: formData });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || data.status !== 'ok') {
                throw new Error(data.error || t('fieldPhoto.saveError'));
            }
            item.status = 'saved';
            item.message = '';
        } catch (err) {
            item.status = 'error';
            item.validationError = false;
            item.message = err.message || t('fieldPhoto.saveError');
        }
        attempted += 1;
        renderFieldPhotoQueue(true);
    }

    await loadFieldPhotos();
    const summary = fieldPhotoUploadSummary();
    if (status) {
        status.textContent = summary.failed
            ? t('modal.fieldPhoto.uploadSummaryWithErrors', summary)
            : t('modal.fieldPhoto.saved', { n: summary.saved });
    }
    if (!summary.failed && input) {
        input.value = '';
        updateFilePickerSummary(input);
    }
    if (submit) {
        submit.disabled = false;
        submit.querySelector('span').textContent = t('modal.fieldPhoto.submit');
    }
    renderFieldPhotoQueue(false);
}

async function submitFieldPhotoUpload(event) {
    event.preventDefault();
    const input = document.getElementById('field-photo-files');
    const status = document.getElementById('field-photo-status');
    try {
        fieldPhotoUploadItems = validateFieldPhotoFiles(input?.files);
    } catch (err) {
        fieldPhotoUploadItems = [];
        renderFieldPhotoQueue();
        if (status) status.textContent = err.message;
        return;
    }

    const fallbackLatLng = currentFieldPhotoUploadFallbackLatLng();
    const ignoreExifGps = document.getElementById('field-photo-ignore-exif')?.checked === true;
    const selectedIssueType = document.getElementById('field-photo-issue-type')?.value || FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    const issueType = FIELD_PHOTO_ISSUE_TYPES.has(selectedIssueType) ? selectedIssueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    fieldPhotoUploadItems.forEach(item => {
        item.fallbackLat = fallbackLatLng.lat;
        item.fallbackLon = fallbackLatLng.lng;
        item.ignoreExifGps = ignoreExifGps;
        item.issueType = issueType;
    });
    renderFieldPhotoQueue();
    const uploadable = fieldPhotoUploadItems.filter(item => item.status === 'pending');
    if (!uploadable.length) {
        if (status) status.textContent = t('modal.fieldPhoto.noValidFiles');
        return;
    }
    await uploadFieldPhotoItems(uploadable);
}

async function retryFailedFieldPhotoUploads() {
    if (!(await ensureAdmin())) return;
    const fallbackLatLng = currentFieldPhotoUploadFallbackLatLng();
    const retryable = fieldPhotoUploadItems.filter(item => item.status === 'error' && !item.validationError);
    retryable.forEach(item => {
        item.status = 'pending';
        item.message = '';
        item.fallbackLat = item.fallbackLat ?? fallbackLatLng.lat;
        item.fallbackLon = item.fallbackLon ?? fallbackLatLng.lng;
        item.ignoreExifGps = Boolean(item.ignoreExifGps);
        item.issueType = item.issueType || FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    });
    renderFieldPhotoQueue();
    if (!retryable.length) {
        const status = document.getElementById('field-photo-status');
        if (status) status.textContent = t('modal.fieldPhoto.noRetryableFiles');
        return;
    }
    await uploadFieldPhotoItems(retryable);
}

function fieldPhotoSourceLabel(source) {
    if (source === 'exif') return t('fieldPhoto.source.exif');
    if (source === 'manual') return t('fieldPhoto.source.manual');
    return t('fieldPhoto.source.map');
}

function fieldPhotoCard(photo) {
    const lat = Number(photo.lat);
    const lon = Number(photo.lon);
    const links = photo.links || {};
    const issueType = fieldPhotoIssueType(photo);
    const thumbUrl = `${photo.public_thumb}?ts=${Date.now()}`;
    const publicImage = `${photo.public_image || photo.public_thumb}?ts=${Date.now()}`;
    const capturedAt = photo.captured_at ? escapeHtml(photo.captured_at) : t('fieldPhoto.noCapturedAt');
    const linksHtml = popupLinks([
        popupCompactLink(links.street_view, t('popup.streetView'), t('popup.streetView')),
        popupCompactLink(links.google_maps_satellite, t('popup.gmapsSat'), t('popup.gmapsSat')),
        popupCompactLink(links.geoportal, t('popup.geoportal'), t('popup.geoportal')),
        `<a href="${escapeHtml(publicImage)}" download>${t('fieldPhoto.downloadPublic')}</a>`,
    ]);
    return `
        <div class="map-popup-card">
            <a href="${escapeHtml(publicImage)}" target="_blank" rel="noopener">
                <img class="map-popup-thumb" src="${escapeHtml(thumbUrl)}" alt="">
            </a>
            <div class="map-popup-card-meta">
                <strong>${escapeHtml(fieldPhotoIssueLabel(issueType))}</strong>
                <span>${t('fieldPhoto.popup.source', { source: fieldPhotoSourceLabel(photo.coordinate_source) })}</span>
                <span>${t('fieldPhoto.popup.capturedAt', { date: capturedAt })}</span>
                <span>${escapeHtml(lat.toFixed(6))}, ${escapeHtml(lon.toFixed(6))}</span>
            </div>
            ${linksHtml}
        </div>
    `;
}

function encodedFieldPhotoIdsForGroup(group) {
    return encodeURIComponent(JSON.stringify(photoIdsForGroup(group)));
}

function fieldPhotoGroupActions(group) {
    const lat = Number(group.lat);
    const lon = Number(group.lon);
    const encodedPhotoIds = encodedFieldPhotoIdsForGroup(group);
    const coordinatesOk = Number.isFinite(lat) && Number.isFinite(lon) && encodedPhotoIds;
    const canCreateVehicleCase = coordinatesOk && group.issueType === FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    const reportButton = canCreateVehicleCase
        ? mapPopupIconAction(
            'map-popup-action--report',
            t('fieldPhoto.reportPackage'),
            `openFieldPhotoGroupReport(${lat}, ${lon}, '${encodedPhotoIds}', this)`,
            'M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm1 7V3.5L18.5 9H15zM8 13h8v2H8v-2zm0 4h8v2H8v-2z'
        )
        : '';
    const photoButton = canCreateVehicleCase
        ? mapPopupIconAction(
            'map-popup-action--photo',
            t('fieldPhoto.addPhotosToCase'),
            `openFieldPhotoGroupPhotoUpload(${lat}, ${lon}, '${encodedPhotoIds}', this)`,
            'M5 7h2.8L9.4 5h5.2l1.6 2H19c1.1 0 2 .9 2 2v10c0 1.1-.9 2-2 2H5c-1.1 0-2-.9-2-2V9c0-1.1.9-2 2-2zm7 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zm5-5h-2v2h-2v2h2v2h2v-2h2v-2h-2v-2z'
        )
        : '';
    const reviewButton = adminAuthenticated && encodedPhotoIds
        ? mapPopupIconAction(
            'map-popup-action--photo',
            t('fieldPhoto.reviewPhotos'),
            `openPhotoReviewForFieldPhotoGroup('${encodedPhotoIds}')`,
            'M4 5h16v14H4V5zm2 2v10h12V7H6zm2 8h8l-2.5-3.2-1.8 2.2-1.3-1.5L8 15zm10-9.5 1.1-1.1 1.5 1.5-1.1 1.1-1.5-1.5zm-6.5 6.5L18 5.5 19.5 7 13 13.5H11.5V12z'
        )
        : '';
    const deleteButton = adminAuthenticated && encodedPhotoIds
        ? mapPopupIconAction(
            'map-popup-action--delete',
            t('fieldPhoto.delete'),
            `deleteFieldPhotoGroup('${encodedPhotoIds}', this)`,
            'M9 3v1H4v2h16V4h-5V3H9zm-3 5l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13H6zm4 3h1v9h-1v-9zm3 0h1v9h-1v-9z'
        )
        : '';
    return popupActions([reportButton, photoButton, reviewButton, deleteButton]);
}

function fieldPhotoGroupPopup(group) {
    const photos = group.photos || [];
    const isGroup = photos.length > 1;
    const issueType = group.issueType || fieldPhotoIssueType(photos[0]);
    const issueLabel = fieldPhotoIssueLabel(issueType);
    const title = isGroup
        ? t('fieldPhoto.popup.groupTitleWithType', { type: issueLabel, n: photos.length })
        : issueLabel;
    return `
        <div class="map-popup ${isGroup ? 'map-popup--field-photo-group' : 'map-popup--field-photo'}">
            ${popupHeader(title)}
            <div class="map-popup-card-grid">
                ${photos.map(photo => fieldPhotoCard(photo)).join('')}
            </div>
            ${fieldPhotoGroupActions({ ...group, issueType })}
        </div>
    `;
}

function fieldPhotoPopup(photo) {
    return fieldPhotoGroupPopup({ photos: [photo] });
}

function groupFieldPhotos(photos) {
    const groups = [];
    (photos || []).forEach(photo => {
        const lat = Number(photo.lat);
        const lon = Number(photo.lon);
        if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
        const issueType = fieldPhotoIssueType(photo);
        let group = groups.find(candidate =>
            candidate.issueType === issueType
            && metersBetween(candidate.lat, candidate.lon, lat, lon) <= FIELD_PHOTO_GROUP_RADIUS_M
        );
        if (!group) {
            group = { lat, lon, issueType, photos: [] };
            groups.push(group);
        }
        group.photos.push(photo);
        const count = group.photos.length;
        group.lat = ((group.lat * (count - 1)) + lat) / count;
        group.lon = ((group.lon * (count - 1)) + lon) / count;
    });
    return groups;
}

function countLingeringCars() {
    const wrecksWithFieldPhotos = publicLayerAllowed(PUBLIC_LAYER_KEYS.savedWrecks)
        ? savedWreckLayerData.filter(wreck => Number(wreck.photo_count) > 0).length
        : 0;
    const vehiclePhotos = publicLayerAllowed(PUBLIC_LAYER_KEYS.fieldPhotoVehicle)
        ? fieldPhotoLayerData.filter(photo => fieldPhotoIssueType(photo) === FIELD_PHOTO_ISSUE_TYPE_VEHICLE)
        : [];
    const looseFieldPhotoGroups = groupFieldPhotos(vehiclePhotos).length;
    return wrecksWithFieldPhotos + looseFieldPhotoGroups;
}

function updateLingeringCarsCounter() {
    const count = countLingeringCars();
    const tooltip = t('panel.lingeringCarsTooltip');
    const badgeEl = document.getElementById('lingering-cars-badge');
    if (badgeEl) {
        badgeEl.textContent = String(count);
        badgeEl.title = tooltip;
        badgeEl.setAttribute('aria-label', tooltip);
    }
}

async function updateFieldPhotoLocation(photo, lat, lon) {
    const id = safeFieldPhotoId(photo.id);
    if (!id) throw new Error(t('fieldPhoto.locationError'));
    const resp = await fetch(`${FIELD_PHOTOS_URL}/${encodeURIComponent(id)}/location`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat, lon }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.status !== 'ok') {
        throw new Error(data.error || t('fieldPhoto.locationError'));
    }
}

function photoIdsForGroup(group) {
    return (group.photos || []).map(photo => safeFieldPhotoId(photo.id)).filter(Boolean);
}

function nearestWreckForAttachment(lat, lon) {
    let nearest = null;
    savedWreckLayerData.forEach(wreck => {
        const wreckLat = Number(wreck.lat);
        const wreckLon = Number(wreck.lon);
        if (!Number.isFinite(wreckLat) || !Number.isFinite(wreckLon)) return;
        const distanceM = metersBetween(lat, lon, wreckLat, wreckLon);
        if (distanceM > FIELD_PHOTO_ATTACH_TO_WRECK_RADIUS_M) return;
        if (!nearest || distanceM < nearest.distanceM) nearest = { wreck, distanceM };
    });
    return nearest;
}

async function attachFieldPhotoGroupToWreck(group, wreck) {
    const photoIds = photoIdsForGroup(group);
    const wreckId = safeWreckId(wreck.id);
    if (!photoIds.length || !wreckId) return;
    const resp = await fetch(`${WRECKS_URL}/${encodeURIComponent(wreckId)}/field-photos/attach`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ photo_ids: photoIds }),
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok || data.status !== 'ok') {
        throw new Error(data.error || t('fieldPhoto.attachToWreckError'));
    }
}

function decodeFieldPhotoIds(encodedPhotoIds) {
    try {
        const ids = JSON.parse(decodeURIComponent(String(encodedPhotoIds || '[]')));
        return Array.isArray(ids) ? ids.map(safeFieldPhotoId).filter(Boolean) : [];
    } catch (_) {
        return [];
    }
}

function fieldPhotosForReport(encodedPhotoIds) {
    const photoIds = new Set(decodeFieldPhotoIds(encodedPhotoIds));
    if (!photoIds.size) return [];
    return fieldPhotoLayerData
        .filter(photo => photoIds.has(safeFieldPhotoId(photo.id)) && fieldPhotoIssueType(photo) === FIELD_PHOTO_ISSUE_TYPE_VEHICLE)
        .map(photo => {
            const id = safeFieldPhotoId(photo.id);
            return {
                id,
                url: photo.public_image || photo.public_thumb,
                filename: `zdjecie_terenowe_${id}.jpg`,
            };
        })
        .filter(photo => photo.id && photo.url)
        .slice(0, REPORT_PHOTO_MAX_COUNT);
}

async function createManualWreckForFieldPhotoGroup(lat, lon) {
    const latNumber = Number(lat);
    const lonNumber = Number(lon);
    if (!Number.isFinite(latNumber) || !Number.isFinite(lonNumber)) {
        throw new Error(t('fieldPhoto.prepareCaseError'));
    }

    const saveResp = await fetch(WRECKS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: latNumber, lon: lonNumber }),
    });
    const saveData = await saveResp.json().catch(() => ({}));
    if (!saveResp.ok || saveData.status !== 'ok') {
        throw new Error(saveData.error || t('fieldPhoto.prepareCaseError'));
    }
    const wreckId = safeWreckId(saveData.wreck?.id);
    if (!wreckId) throw new Error(t('fieldPhoto.prepareCaseError'));
    await loadSavedWrecks();
    return wreckId;
}

async function createWreckForFieldPhotoGroup(lat, lon, encodedPhotoIds) {
    if (!(await ensureAdmin())) return null;
    const latNumber = Number(lat);
    const lonNumber = Number(lon);
    const photoIds = decodeFieldPhotoIds(encodedPhotoIds);
    if (!Number.isFinite(latNumber) || !Number.isFinite(lonNumber) || !photoIds.length) {
        throw new Error(t('fieldPhoto.prepareCaseError'));
    }

    const saveResp = await fetch(WRECKS_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ lat: latNumber, lon: lonNumber }),
    });
    const saveData = await saveResp.json().catch(() => ({}));
    if (!saveResp.ok || saveData.status !== 'ok') {
        throw new Error(saveData.error || t('fieldPhoto.prepareCaseError'));
    }
    const wreckId = safeWreckId(saveData.wreck?.id);
    if (!wreckId) throw new Error(t('fieldPhoto.prepareCaseError'));

    const attachResp = await fetch(`${WRECKS_URL}/${encodeURIComponent(wreckId)}/field-photos/attach`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ photo_ids: photoIds }),
    });
    const attachData = await attachResp.json().catch(() => ({}));
    if (!attachResp.ok || attachData.status !== 'ok') {
        throw new Error(attachData.error || t('fieldPhoto.attachToWreckError'));
    }

    await loadSavedWrecks();
    await loadFieldPhotos();
    return wreckId;
}

async function openFieldPhotoGroupReport(lat, lon, encodedPhotoIds, button = null) {
    const btn = button instanceof HTMLElement ? button : null;
    if (btn) btn.disabled = true;
    statusEl.textContent = t('fieldPhoto.prepareCaseSaving');
    statusEl.className = '';
    try {
        if (typeof refreshAdminStatus === 'function') await refreshAdminStatus();
        const wreckId = adminAuthenticated
            ? await createWreckForFieldPhotoGroup(lat, lon, encodedPhotoIds)
            : await createManualWreckForFieldPhotoGroup(lat, lon);
        if (!wreckId) return;
        statusEl.textContent = t('fieldPhoto.prepareCaseSaved');
        statusEl.className = 'ok';
        openReportPackageModal(wreckId, { extraPhotos: adminAuthenticated ? [] : fieldPhotosForReport(encodedPhotoIds) });
    } catch (err) {
        statusEl.textContent = err.message || t('fieldPhoto.prepareCaseError');
        statusEl.className = 'err';
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function openFieldPhotoGroupPhotoUpload(lat, lon, encodedPhotoIds, button = null) {
    const btn = button instanceof HTMLElement ? button : null;
    if (btn) btn.disabled = true;
    statusEl.textContent = t('fieldPhoto.prepareCaseSaving');
    statusEl.className = '';
    try {
        if (typeof refreshAdminStatus === 'function') await refreshAdminStatus();
        const wreckId = adminAuthenticated
            ? await createWreckForFieldPhotoGroup(lat, lon, encodedPhotoIds)
            : await createManualWreckForFieldPhotoGroup(lat, lon);
        if (!wreckId) return;
        statusEl.textContent = t('fieldPhoto.prepareCaseSaved');
        statusEl.className = 'ok';
        openWreckPhotoModal(wreckId);
    } catch (err) {
        statusEl.textContent = err.message || t('fieldPhoto.prepareCaseError');
        statusEl.className = 'err';
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function updateFieldPhotoGroupLocation(group, marker) {
    if (!adminAuthenticated) {
        await loadFieldPhotos();
        return;
    }
    const latlng = marker.getLatLng();
    const photos = (group.photos || []).filter(photo => safeFieldPhotoId(photo.id));
    if (!photos.length) {
        await loadFieldPhotos();
        return;
    }

    const nearWreck = group.issueType === FIELD_PHOTO_ISSUE_TYPE_VEHICLE
        ? nearestWreckForAttachment(latlng.lat, latlng.lng)
        : null;
    if (nearWreck) {
        marker.dragging?.disable();
        statusEl.textContent = t('fieldPhoto.attachToWreckSaving', { n: photos.length });
        statusEl.className = '';
        try {
            await attachFieldPhotoGroupToWreck(group, nearWreck.wreck);
            statusEl.textContent = t('fieldPhoto.attachToWreckSaved', { n: photos.length });
            statusEl.className = 'ok';
        } catch (err) {
            statusEl.textContent = err.message || t('fieldPhoto.attachToWreckError');
            statusEl.className = 'err';
        } finally {
            await loadSavedWrecks();
            await loadFieldPhotos();
        }
        return;
    }

    marker.dragging?.disable();
    statusEl.textContent = t('fieldPhoto.locationSaving', { n: photos.length });
    statusEl.className = '';
    try {
        for (const photo of photos) {
            await updateFieldPhotoLocation(photo, latlng.lat, latlng.lng);
        }
        statusEl.textContent = t('fieldPhoto.locationUpdated', { n: photos.length });
        statusEl.className = 'ok';
    } catch (err) {
        statusEl.textContent = err.message || t('fieldPhoto.locationError');
        statusEl.className = 'err';
    } finally {
        await loadFieldPhotos();
    }
}

function placeFieldPhotos(photos = fieldPhotoLayerData) {
    clearFieldPhotoMarkers();
    groupFieldPhotos(filteredFieldPhotos(photos)).forEach(group => {
        const marker = L.marker([group.lat, group.lon], {
            icon: fieldPhotoIcon(group.photos.length, group.issueType),
            zIndexOffset: 1400,
            draggable: adminAuthenticated,
            autoPan: adminAuthenticated,
        }).addTo(map).bindPopup(fieldPhotoGroupPopup(group), { maxWidth: group.photos.length > 1 ? 560 : 300 });
        if (adminAuthenticated) {
            marker.on('dragstart', () => marker.closePopup());
            marker.on('dragend', () => updateFieldPhotoGroupLocation(group, marker));
        }
        fieldPhotoMarkers.push(marker);
    });
}

async function loadFieldPhotos() {
    try {
        const resp = await fetch(`${FIELD_PHOTOS_URL}?ts=${Date.now()}`, { cache: 'no-store' });
        const data = await resp.json().catch(() => ({}));
        if (resp.ok && data.status === 'ok') {
            fieldPhotoLayerData = data.photos || [];
            placeFieldPhotos(fieldPhotoLayerData);
            updateLingeringCarsCounter();
        }
    } catch (_) {}
}

function toggleFieldPhotoIssueFilter(issueType, visible) {
    const safeIssueType = FIELD_PHOTO_ISSUE_TYPES.has(issueType) ? issueType : FIELD_PHOTO_ISSUE_TYPE_VEHICLE;
    fieldPhotoIssueFilters[safeIssueType] = Boolean(visible);
    placeFieldPhotos(fieldPhotoLayerData);
}

async function deleteFieldPhotoGroup(encodedPhotoIds, button = null) {
    if (!(await ensureAdmin())) return;
    const photoIds = decodeFieldPhotoIds(encodedPhotoIds);
    if (!photoIds.length) return;
    const confirmed = await confirmAction({
        title: t('fieldPhoto.deleteTitle'),
        message: t('fieldPhoto.deleteConfirm', { n: photoIds.length }),
        confirmLabel: t('fieldPhoto.delete'),
    });
    if (!confirmed) return;

    const btn = button instanceof HTMLElement ? button : null;
    if (btn) {
        btn.disabled = true;
    }
    try {
        for (const id of photoIds) {
            const resp = await fetch(`${FIELD_PHOTOS_URL}/${encodeURIComponent(id)}`, { method: 'DELETE' });
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok || data.status !== 'ok') {
                throw new Error(data.error || t('fieldPhoto.deleteError'));
            }
        }
        await loadFieldPhotos();
        statusEl.textContent = t('fieldPhoto.deleted', { n: photoIds.length });
        statusEl.className = 'ok';
    } catch (err) {
        if (btn) btn.disabled = false;
        statusEl.textContent = err.message || t('fieldPhoto.deleteError');
        statusEl.className = 'err';
    }
}

function photoReviewStatusLabel(status) {
    if (status === 'approved') return t('modal.photoReview.approved');
    if (status === 'rejected') return t('modal.photoReview.rejected');
    return t('modal.photoReview.pending');
}

function photoReviewEndpoint(item) {
    if (!item) return null;
    if (item.scope === 'field') {
        return `${ADMIN_PHOTOS_URL}/field/${encodeURIComponent(item.photo_id)}/review`;
    }
    if (item.scope === 'wreck') {
        return `${ADMIN_PHOTOS_URL}/wreck/${encodeURIComponent(item.wreck_id)}/${encodeURIComponent(item.photo_id)}/review`;
    }
    return null;
}

function renderPhotoReviewQueue() {
    const list = document.getElementById('photo-review-list');
    if (!list) return;
    if (!photoReviewItems.length) {
        list.innerHTML = `<p class="modal-hint" style="padding:10px">${escapeHtml(t('modal.photoReview.noItems'))}</p>`;
        return;
    }
    list.innerHTML = photoReviewItems.map(item => {
        const active = activePhotoReview?.id === item.id;
        const scope = item.scope === 'wreck' ? t('modal.photoReview.scopeWreck') : t('modal.photoReview.scopeField');
        const title = item.original_filename || item.photo_id || item.id;
        return `
            <button type="button" class="photo-review-item ${active ? 'is-active' : ''}" onclick="selectPhotoReview('${escapeHtml(item.id)}')">
                <strong>${escapeHtml(title)}</strong>
                <span class="photo-review-pill">${escapeHtml(photoReviewStatusLabel(item.public_review_status))}</span>
                <span>${escapeHtml(scope)}${item.wreck_id ? ` · ${escapeHtml(item.wreck_id)}` : ''}</span>
            </button>
        `;
    }).join('');
}

async function openPhotoReviewModal() {
    if (!(await ensureAdmin())) return;
    photoReviewExactPhotoIds = [];
    const filter = document.getElementById('photo-review-filter');
    const scope = document.getElementById('photo-review-scope');
    const search = document.getElementById('photo-review-search');
    if (filter) filter.value = 'pending';
    if (scope) scope.value = 'all';
    if (search) search.value = '';
    openModal('modal-photo-review');
    await loadPhotoReviewQueue();
}

async function loadPhotoReviewQueue() {
    if (!adminAuthenticated && !(await ensureAdmin())) return;
    const filter = document.getElementById('photo-review-filter')?.value || 'pending';
    const scope = document.getElementById('photo-review-scope')?.value || 'all';
    const query = document.getElementById('photo-review-search')?.value || '';
    const status = document.getElementById('photo-review-status');
    if (status) status.textContent = t('modal.photoReview.loading');
    try {
        const params = new URLSearchParams({
            status: filter,
            scope,
            q: photoReviewExactPhotoIds.length ? '' : query,
            ts: String(Date.now()),
        });
        if (photoReviewExactPhotoIds.length) {
            params.set('ids', photoReviewExactPhotoIds.join(','));
        }
        const resp = await fetch(`${ADMIN_PHOTOS_URL}?${params.toString()}`, { cache: 'no-store' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.photoReview.loadError'));
        }
        photoReviewItems = Array.isArray(data.photos) ? data.photos : [];
        activePhotoReview = null;
        photoReviewImage = null;
        photoReviewRedactions = [];
        activePhotoReviewRedactionIndex = -1;
        renderPhotoReviewQueue();
        clearPhotoReviewCanvas();
        if (photoReviewItems[0]) selectPhotoReview(photoReviewItems[0].id);
        if (status) status.textContent = '';
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.photoReview.loadError');
    }
}

document.getElementById('photo-review-search')?.addEventListener('input', () => {
    photoReviewExactPhotoIds = [];
    if (photoReviewSearchTimer) clearTimeout(photoReviewSearchTimer);
    photoReviewSearchTimer = setTimeout(loadPhotoReviewQueue, 250);
});

function clearPhotoReviewCanvas() {
    const canvas = document.getElementById('photo-review-canvas');
    const empty = document.getElementById('photo-review-empty');
    if (canvas) {
        canvas.style.display = 'none';
        clearPhotoReviewCursorState(canvas);
        const ctx = canvas.getContext('2d');
        ctx?.clearRect(0, 0, canvas.width, canvas.height);
    }
    if (empty) empty.hidden = false;
}

function selectPhotoReview(itemId) {
    const item = photoReviewItems.find(candidate => candidate.id === itemId);
    if (!item) return;
    activePhotoReview = item;
    photoReviewRedactions = (Array.isArray(item.redactions) ? item.redactions : [])
        .map(normalizePhotoReviewRedaction)
        .filter(Boolean);
    activePhotoReviewRedactionIndex = photoReviewRedactions.length ? photoReviewRedactions.length - 1 : -1;
    photoReviewDraftRect = null;
    renderPhotoReviewQueue();
    const status = document.getElementById('photo-review-status');
    if (status) status.textContent = t('modal.photoReview.imageLoading');
    const image = new Image();
    image.onload = () => {
        photoReviewImage = image;
        const canvas = document.getElementById('photo-review-canvas');
        const empty = document.getElementById('photo-review-empty');
        if (!canvas) return;
        const maxWidth = 900;
        const scale = Math.min(1, maxWidth / Math.max(1, image.naturalWidth));
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        canvas.style.aspectRatio = `${canvas.width} / ${canvas.height}`;
        canvas.style.display = 'block';
        clearPhotoReviewCursorState(canvas);
        if (empty) empty.hidden = true;
        drawPhotoReviewCanvas();
        if (status) status.textContent = t('modal.photoReview.drawHint');
    };
    image.onerror = () => {
        photoReviewImage = null;
        clearPhotoReviewCanvas();
        if (status) status.textContent = t('modal.photoReview.imageError');
    };
    image.src = `${item.original_image}?ts=${Date.now()}`;
}

function drawPhotoReviewCanvas() {
    const canvas = document.getElementById('photo-review-canvas');
    if (!canvas || !photoReviewImage) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const metrics = photoReviewCanvasMetrics(canvas);
    const handleHalfX = (PHOTO_REVIEW_HANDLE_SIZE_PX * metrics.canvasScaleX) / 2;
    const handleHalfY = (PHOTO_REVIEW_HANDLE_SIZE_PX * metrics.canvasScaleY) / 2;
    const centerRadius = PHOTO_REVIEW_CENTER_DOT_SIZE_PX * Math.max(metrics.canvasScaleX, metrics.canvasScaleY);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(photoReviewImage, 0, 0, canvas.width, canvas.height);
    const drawRedaction = (redaction, draft = false, index = -1) => {
        const points = Array.isArray(redaction?.points) ? redaction.points : [];
        if (points.length < 3) return;
        ctx.fillStyle = draft ? 'rgba(250, 204, 21, 0.25)' : 'rgba(15, 23, 42, 0.82)';
        ctx.strokeStyle = draft ? '#facc15' : (index === activePhotoReviewRedactionIndex ? '#f97316' : '#93c5fd');
        ctx.lineWidth = index === activePhotoReviewRedactionIndex ? 3 : 2;
        ctx.beginPath();
        points.forEach((point, pointIndex) => {
            const x = point.x * canvas.width;
            const y = point.y * canvas.height;
            if (pointIndex === 0) ctx.moveTo(x, y);
            else ctx.lineTo(x, y);
        });
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
        if (!draft && index === activePhotoReviewRedactionIndex) {
            const center = redactionCenter(redaction);
            ctx.beginPath();
            ctx.arc(center.x * canvas.width, center.y * canvas.height, centerRadius, 0, Math.PI * 2);
            ctx.fillStyle = '#f97316';
            ctx.fill();
            points.forEach(point => {
                const x = point.x * canvas.width;
                const y = point.y * canvas.height;
                ctx.fillStyle = '#fff7ed';
                ctx.strokeStyle = '#f97316';
                ctx.lineWidth = 2;
                ctx.fillRect(x - handleHalfX, y - handleHalfY, handleHalfX * 2, handleHalfY * 2);
                ctx.strokeRect(x - handleHalfX, y - handleHalfY, handleHalfX * 2, handleHalfY * 2);
            });
        }
    };
    photoReviewRedactions.forEach((redaction, index) => drawRedaction(redaction, false, index));
    if (photoReviewDraftRect) drawRedaction(photoReviewDraftRect, true);
}

const PHOTO_REVIEW_HANDLE_SIZE_PX = 14;
const PHOTO_REVIEW_HANDLE_HIT_RADIUS_PX = 18;
const PHOTO_REVIEW_CENTER_DOT_SIZE_PX = 4;

function photoReviewCanvasMetrics(canvas = document.getElementById('photo-review-canvas')) {
    if (!canvas) {
        return { displayWidth: 1, displayHeight: 1, canvasScaleX: 1, canvasScaleY: 1 };
    }
    const rect = canvas.getBoundingClientRect();
    const displayWidth = Math.max(1, rect.width || canvas.width || 1);
    const displayHeight = Math.max(1, rect.height || canvas.height || 1);
    return {
        rect,
        displayWidth,
        displayHeight,
        canvasScaleX: Math.max(1, canvas.width || 1) / displayWidth,
        canvasScaleY: Math.max(1, canvas.height || 1) / displayHeight,
    };
}

function photoReviewPointer(event) {
    const canvas = document.getElementById('photo-review-canvas');
    if (!canvas) return null;
    const metrics = photoReviewCanvasMetrics(canvas);
    const x = (event.clientX - metrics.rect.left) / metrics.displayWidth;
    const y = (event.clientY - metrics.rect.top) / metrics.displayHeight;
    return {
        x: Math.min(Math.max(x, 0), 1),
        y: Math.min(Math.max(y, 0), 1),
    };
}

function clearPhotoReviewCursorState(canvas = document.getElementById('photo-review-canvas')) {
    if (!canvas) return;
    canvas.classList.remove(
        'is-hovering-handle',
        'is-hovering-redaction',
        'is-drawing-redaction',
        'is-moving-redaction',
        'is-resizing-redaction',
    );
    canvas.style.cursor = '';
}

function setPhotoReviewCursorState(canvas, state, cursor = '') {
    clearPhotoReviewCursorState(canvas);
    if (!canvas || !state) return;
    if (state === 'handle') canvas.classList.add('is-hovering-handle');
    if (state === 'redaction') canvas.classList.add('is-hovering-redaction');
    if (state === 'draw') canvas.classList.add('is-drawing-redaction');
    if (state === 'move') canvas.classList.add('is-moving-redaction');
    if (state === 'resize') canvas.classList.add('is-resizing-redaction');
    canvas.style.cursor = cursor;
}

function capturePhotoReviewPointer(canvas, pointerId) {
    try {
        canvas.setPointerCapture?.(pointerId);
    } catch (_) {
        // Pointer capture can fail when the browser has already cancelled the pointer.
    }
}

function releasePhotoReviewPointer(canvas, pointerId) {
    try {
        canvas.releasePointerCapture?.(pointerId);
    } catch (_) {
        // The pointer may already be released after a cancel/lost-capture event.
    }
}

function clampUnit(value) {
    return Math.min(Math.max(Number(value) || 0, 0), 1);
}

function rectToRedaction(x, y, width, height) {
    x = clampUnit(x);
    y = clampUnit(y);
    width = Math.min(Math.max(Number(width) || 0, 0), 1 - x);
    height = Math.min(Math.max(Number(height) || 0, 0), 1 - y);
    if (width < 0.005 || height < 0.005) return null;
    return {
        points: [
            { x, y },
            { x: x + width, y },
            { x: x + width, y: y + height },
            { x, y: y + height },
        ].map(point => ({ x: Number(point.x.toFixed(6)), y: Number(point.y.toFixed(6)) })),
    };
}

function normalizePhotoReviewRedaction(redaction) {
    if (!redaction || typeof redaction !== 'object') return null;
    if (Array.isArray(redaction.points)) {
        const points = redaction.points
            .map(point => point && typeof point === 'object'
                ? { x: Number(clampUnit(point.x).toFixed(6)), y: Number(clampUnit(point.y).toFixed(6)) }
                : null)
            .filter(Boolean);
        return points.length >= 3 ? { points } : null;
    }
    return rectToRedaction(redaction.x, redaction.y, redaction.width, redaction.height);
}

function normalizeReviewRect(start, end) {
    const x = Math.min(start.x, end.x);
    const y = Math.min(start.y, end.y);
    const width = Math.abs(end.x - start.x);
    const height = Math.abs(end.y - start.y);
    return rectToRedaction(x, y, width, height);
}

function redactionCenter(redaction) {
    const points = Array.isArray(redaction?.points) ? redaction.points : [];
    const total = points.reduce((acc, point) => ({ x: acc.x + point.x, y: acc.y + point.y }), { x: 0, y: 0 });
    const count = Math.max(1, points.length);
    return { x: total.x / count, y: total.y / count };
}

function pointInsideRedaction(point, redaction) {
    const points = Array.isArray(redaction?.points) ? redaction.points : [];
    if (points.length < 3) return false;
    let inside = false;
    for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
        const a = points[i];
        const b = points[j];
        const intersects = ((a.y > point.y) !== (b.y > point.y))
            && (point.x < ((b.x - a.x) * (point.y - a.y)) / ((b.y - a.y) || 1e-9) + a.x);
        if (intersects) inside = !inside;
    }
    return inside;
}

function selectPhotoRedactionAt(point) {
    for (let index = photoReviewRedactions.length - 1; index >= 0; index--) {
        if (pointInsideRedaction(point, photoReviewRedactions[index])) {
            activePhotoReviewRedactionIndex = index;
            drawPhotoReviewCanvas();
            return true;
        }
    }
    activePhotoReviewRedactionIndex = -1;
    drawPhotoReviewCanvas();
    return false;
}

function redactionAtPoint(point) {
    for (let index = photoReviewRedactions.length - 1; index >= 0; index--) {
        if (pointInsideRedaction(point, photoReviewRedactions[index])) return index;
    }
    return -1;
}

function photoReviewHandleCursor(redaction, pointIndex) {
    const point = redaction?.points?.[pointIndex];
    if (!point) return 'grab';
    const center = redactionCenter(redaction);
    return (point.x - center.x) * (point.y - center.y) >= 0 ? 'nwse-resize' : 'nesw-resize';
}

function redactionHandleAtPoint(point, redactionIndex = activePhotoReviewRedactionIndex) {
    if (!point || redactionIndex < 0) return null;
    const redaction = photoReviewRedactions[redactionIndex];
    const points = redaction?.points || [];
    const metrics = photoReviewCanvasMetrics();
    for (let pointIndex = 0; pointIndex < points.length; pointIndex++) {
        const candidate = points[pointIndex];
        const dx = (candidate.x - point.x) * metrics.displayWidth;
        const dy = (candidate.y - point.y) * metrics.displayHeight;
        if (Math.hypot(dx, dy) <= PHOTO_REVIEW_HANDLE_HIT_RADIUS_PX) {
            return {
                redactionIndex,
                pointIndex,
                cursor: photoReviewHandleCursor(redaction, pointIndex),
            };
        }
    }
    return null;
}

function resizeRedactionPoint(redaction, pointIndex, point) {
    const points = Array.isArray(redaction?.points) ? redaction.points : [];
    if (pointIndex < 0 || pointIndex >= points.length) return redaction;
    return {
        points: points.map((candidate, index) => (
            index === pointIndex
                ? { x: Number(clampUnit(point.x).toFixed(6)), y: Number(clampUnit(point.y).toFixed(6)) }
                : candidate
        )),
    };
}

function moveRedaction(redaction, dx, dy) {
    const points = Array.isArray(redaction?.points) ? redaction.points : [];
    if (!points.length) return redaction;
    const minX = Math.min(...points.map(point => point.x));
    const maxX = Math.max(...points.map(point => point.x));
    const minY = Math.min(...points.map(point => point.y));
    const maxY = Math.max(...points.map(point => point.y));
    const safeDx = Math.min(Math.max(dx, -minX), 1 - maxX);
    const safeDy = Math.min(Math.max(dy, -minY), 1 - maxY);
    return {
        points: points.map(point => ({
            x: Number(clampUnit(point.x + safeDx).toFixed(6)),
            y: Number(clampUnit(point.y + safeDy).toFixed(6)),
        })),
    };
}

function undoPhotoRedaction() {
    if (!photoReviewRedactions.length) return;
    photoReviewRedactions.pop();
    activePhotoReviewRedactionIndex = photoReviewRedactions.length ? photoReviewRedactions.length - 1 : -1;
    drawPhotoReviewCanvas();
}

function rotatePhotoRedaction(degrees) {
    if (!photoReviewRedactions.length) return;
    if (activePhotoReviewRedactionIndex < 0) {
        activePhotoReviewRedactionIndex = photoReviewRedactions.length - 1;
    }
    const redaction = photoReviewRedactions[activePhotoReviewRedactionIndex];
    if (!redaction?.points?.length) return;
    const center = redactionCenter(redaction);
    const radians = (Number(degrees) || 0) * Math.PI / 180;
    const cos = Math.cos(radians);
    const sin = Math.sin(radians);
    photoReviewRedactions[activePhotoReviewRedactionIndex] = {
        points: redaction.points.map(point => {
            const dx = point.x - center.x;
            const dy = point.y - center.y;
            return {
                x: Number(clampUnit(center.x + dx * cos - dy * sin).toFixed(6)),
                y: Number(clampUnit(center.y + dx * sin + dy * cos).toFixed(6)),
            };
        }),
    };
    drawPhotoReviewCanvas();
}

async function savePhotoReviewStatus(publicReviewStatus) {
    const endpoint = photoReviewEndpoint(activePhotoReview);
    const status = document.getElementById('photo-review-status');
    if (!endpoint) return;
    if (status) status.textContent = t('modal.photoReview.saving');
    try {
        const resp = await fetch(endpoint, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                public_review_status: publicReviewStatus,
                redactions: photoReviewRedactions,
            }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.photoReview.saveError'));
        }
        if (status) status.textContent = t('modal.photoReview.saved');
        await loadSavedWrecks();
        await loadFieldPhotos();
        await loadPhotoReviewQueue();
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.photoReview.saveError');
    }
}

function privacyRequestStatusLabel(status) {
    if (status === 'in_progress') return t('modal.privacyRequests.inProgress');
    if (status === 'done') return t('modal.privacyRequests.done');
    if (status === 'rejected') return t('modal.privacyRequests.rejected');
    return t('modal.privacyRequests.new');
}

function renderPrivacyRequestQueue() {
    const list = document.getElementById('privacy-request-list');
    if (!list) return;
    if (!privacyRequestItems.length) {
        list.innerHTML = `<p class="modal-hint" style="padding:10px">${escapeHtml(t('modal.privacyRequests.noItems'))}</p>`;
        return;
    }
    list.innerHTML = privacyRequestItems.map(item => {
        const active = activePrivacyRequest?.id === item.id;
        const title = item.target || item.id;
        return `
            <button type="button" class="privacy-request-item ${active ? 'is-active' : ''}" onclick="selectPrivacyRequest('${escapeHtml(item.id)}')">
                <strong>${escapeHtml(title)}</strong>
                <span class="photo-review-pill">${escapeHtml(privacyRequestStatusLabel(item.status))}</span>
                <span>${escapeHtml(item.email || '')}</span>
                <span>${escapeHtml(item.updated_at || item.created_at || '')}</span>
            </button>
        `;
    }).join('');
}

function privacyRequestTargetHtml(target) {
    const text = String(target || '').trim();
    if (/^https?:\/\//i.test(text)) {
        const safeHref = escapeHtml(text);
        return `<a href="${safeHref}" target="_blank" rel="noopener">${safeHref}</a>`;
    }
    return escapeHtml(text);
}

function renderPrivacyRequestDetail() {
    const detail = document.getElementById('privacy-request-detail');
    if (!detail) return;
    if (!activePrivacyRequest) {
        detail.innerHTML = `<p class="modal-hint">${escapeHtml(t('modal.privacyRequests.empty'))}</p>`;
        return;
    }
    detail.innerHTML = `
        <div class="privacy-request-meta">
            <label class="report-field">
                <span>${escapeHtml(t('modal.privacyRequests.status'))}</span>
                <select class="modal-input" id="privacy-request-status-select">
                    <option value="new" ${activePrivacyRequest.status === 'new' ? 'selected' : ''}>${escapeHtml(t('modal.privacyRequests.new'))}</option>
                    <option value="in_progress" ${activePrivacyRequest.status === 'in_progress' ? 'selected' : ''}>${escapeHtml(t('modal.privacyRequests.inProgress'))}</option>
                    <option value="done" ${activePrivacyRequest.status === 'done' ? 'selected' : ''}>${escapeHtml(t('modal.privacyRequests.done'))}</option>
                    <option value="rejected" ${activePrivacyRequest.status === 'rejected' ? 'selected' : ''}>${escapeHtml(t('modal.privacyRequests.rejected'))}</option>
                </select>
            </label>
            <div class="privacy-request-facts">
                <span><b>${escapeHtml(t('modal.privacyRequests.email'))}</b> ${escapeHtml(activePrivacyRequest.email || '')}</span>
                <span><b>${escapeHtml(t('modal.privacyRequests.createdAt'))}</b> ${escapeHtml(activePrivacyRequest.created_at || '')}</span>
                <span><b>${escapeHtml(t('modal.privacyRequests.updatedAt'))}</b> ${escapeHtml(activePrivacyRequest.updated_at || '')}</span>
            </div>
        </div>
        <section class="modal-section privacy-request-section">
            <label class="modal-label">${escapeHtml(t('modal.privacyRequests.target'))}</label>
            <p class="privacy-request-text">${privacyRequestTargetHtml(activePrivacyRequest.target)}</p>
        </section>
        <section class="modal-section privacy-request-section">
            <label class="modal-label">${escapeHtml(t('modal.privacyRequests.reason'))}</label>
            <p class="privacy-request-text">${escapeHtml(activePrivacyRequest.reason || '')}</p>
        </section>
        <label class="report-field">
            <span>${escapeHtml(t('modal.privacyRequests.adminNote'))}</span>
            <textarea id="privacy-request-admin-note" rows="6" maxlength="4000">${escapeHtml(activePrivacyRequest.admin_note || '')}</textarea>
        </label>
        <div class="privacy-request-actions">
            <button type="button" class="btn-download report-submit-btn" onclick="savePrivacyRequestUpdate()">
                <span>${escapeHtml(t('modal.privacyRequests.save'))}</span>
            </button>
        </div>
    `;
}

function selectPrivacyRequest(requestId) {
    activePrivacyRequest = privacyRequestItems.find(item => item.id === requestId) || null;
    renderPrivacyRequestQueue();
    renderPrivacyRequestDetail();
}

async function openPrivacyRequestsModal() {
    if (!(await ensureAdmin())) return;
    openModal('modal-privacy-requests');
    await loadPrivacyRequestQueue();
}

async function loadPrivacyRequestQueue() {
    if (!adminAuthenticated && !(await ensureAdmin())) return;
    const filter = document.getElementById('privacy-request-filter')?.value || 'new';
    const status = document.getElementById('privacy-request-status');
    if (status) status.textContent = t('modal.privacyRequests.loading');
    try {
        const resp = await fetch(`${ADMIN_PRIVACY_REQUESTS_URL}?status=${encodeURIComponent(filter)}&ts=${Date.now()}`, { cache: 'no-store' });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.privacyRequests.loadError'));
        }
        privacyRequestItems = Array.isArray(data.requests) ? data.requests : [];
        activePrivacyRequest = null;
        renderPrivacyRequestQueue();
        renderPrivacyRequestDetail();
        if (privacyRequestItems[0]) selectPrivacyRequest(privacyRequestItems[0].id);
        if (status) status.textContent = '';
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.privacyRequests.loadError');
    }
}

async function savePrivacyRequestUpdate() {
    if (!activePrivacyRequest?.id) return;
    const statusEl = document.getElementById('privacy-request-status');
    const statusValue = document.getElementById('privacy-request-status-select')?.value || 'new';
    const adminNote = document.getElementById('privacy-request-admin-note')?.value || '';
    if (statusEl) statusEl.textContent = t('modal.privacyRequests.saving');
    try {
        const resp = await fetch(`${ADMIN_PRIVACY_REQUESTS_URL}/${encodeURIComponent(activePrivacyRequest.id)}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ status: statusValue, admin_note: adminNote }),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.privacyRequests.saveError'));
        }
        if (statusEl) statusEl.textContent = t('modal.privacyRequests.saved');
        await loadPrivacyRequestQueue();
        if (data.request?.id) selectPrivacyRequest(data.request.id);
    } catch (err) {
        if (statusEl) statusEl.textContent = err.message || t('modal.privacyRequests.saveError');
    }
}

(() => {
    const canvas = document.getElementById('photo-review-canvas');
    if (!canvas) return;
    let dragState = null;

    const updateHoverState = event => {
        if (!photoReviewImage) return;
        const point = photoReviewPointer(event);
        const handle = redactionHandleAtPoint(point);
        if (handle) {
            setPhotoReviewCursorState(canvas, 'handle', handle.cursor);
            return;
        }
        const hitIndex = redactionAtPoint(point);
        if (hitIndex >= 0) {
            setPhotoReviewCursorState(canvas, 'redaction');
            return;
        }
        setPhotoReviewCursorState(canvas, 'draw');
    };

    canvas.addEventListener('pointerdown', event => {
        if (!photoReviewImage) return;
        event.preventDefault();
        const start = photoReviewPointer(event);
        if (!start) return;
        const activeHandle = redactionHandleAtPoint(start);
        if (activeHandle) {
            activePhotoReviewRedactionIndex = activeHandle.redactionIndex;
            dragState = {
                pointerId: event.pointerId,
                mode: 'resize',
                start,
                lastPoint: start,
                moved: false,
                activeHandle,
            };
            photoReviewDrawing = false;
            setPhotoReviewCursorState(canvas, 'resize', activeHandle.cursor);
            capturePhotoReviewPointer(canvas, event.pointerId);
            drawPhotoReviewCanvas();
            return;
        }
        const hitIndex = redactionAtPoint(start);
        if (hitIndex >= 0) {
            activePhotoReviewRedactionIndex = hitIndex;
            dragState = {
                pointerId: event.pointerId,
                mode: 'move',
                start,
                lastPoint: start,
                moved: false,
            };
            photoReviewDrawing = false;
            setPhotoReviewCursorState(canvas, 'move');
            capturePhotoReviewPointer(canvas, event.pointerId);
            drawPhotoReviewCanvas();
            return;
        }
        activePhotoReviewRedactionIndex = -1;
        dragState = {
            pointerId: event.pointerId,
            mode: 'draw',
            start,
            lastPoint: start,
            moved: false,
        };
        photoReviewDrawing = true;
        setPhotoReviewCursorState(canvas, 'draw');
        capturePhotoReviewPointer(canvas, event.pointerId);
    });

    canvas.addEventListener('pointermove', event => {
        if (!photoReviewImage) return;
        if (!dragState) {
            updateHoverState(event);
            return;
        }
        if (event.pointerId !== dragState.pointerId) return;
        event.preventDefault();
        const current = photoReviewPointer(event);
        if (!current) return;
        const distance = Math.hypot(current.x - dragState.start.x, current.y - dragState.start.y);
        dragState.moved = dragState.moved || distance > 0.003;
        if (dragState.mode === 'move' && activePhotoReviewRedactionIndex >= 0 && dragState.lastPoint) {
            const dx = current.x - dragState.lastPoint.x;
            const dy = current.y - dragState.lastPoint.y;
            photoReviewRedactions[activePhotoReviewRedactionIndex] = moveRedaction(
                photoReviewRedactions[activePhotoReviewRedactionIndex],
                dx,
                dy,
            );
            dragState.lastPoint = current;
        } else if (dragState.mode === 'resize' && dragState.activeHandle) {
            photoReviewRedactions[dragState.activeHandle.redactionIndex] = resizeRedactionPoint(
                photoReviewRedactions[dragState.activeHandle.redactionIndex],
                dragState.activeHandle.pointIndex,
                current,
            );
        } else if (dragState.mode === 'draw') {
            photoReviewDraftRect = normalizeReviewRect(dragState.start, current);
        }
        drawPhotoReviewCanvas();
    });

    const finishDrag = event => {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        event.preventDefault();
        const end = photoReviewPointer(event);
        const rect = dragState.mode === 'draw' && end ? normalizeReviewRect(dragState.start, end) : null;
        if (dragState.mode === 'draw' && rect) {
            photoReviewRedactions.push(rect);
            activePhotoReviewRedactionIndex = photoReviewRedactions.length - 1;
        } else if (!dragState.moved && end) {
            selectPhotoRedactionAt(end);
        }
        releasePhotoReviewPointer(canvas, event.pointerId);
        photoReviewDraftRect = null;
        photoReviewDrawing = false;
        dragState = null;
        drawPhotoReviewCanvas();
        if (end) updateHoverState(event);
        else clearPhotoReviewCursorState(canvas);
    };

    canvas.addEventListener('pointerup', finishDrag);
    canvas.addEventListener('pointercancel', event => {
        if (!dragState || event.pointerId !== dragState.pointerId) return;
        releasePhotoReviewPointer(canvas, event.pointerId);
        photoReviewDraftRect = null;
        photoReviewDrawing = false;
        dragState = null;
        clearPhotoReviewCursorState(canvas);
        drawPhotoReviewCanvas();
    });
    canvas.addEventListener('pointerleave', () => {
        if (!dragState) clearPhotoReviewCursorState(canvas);
    });
})();

function resetWreckPhotoModal(wreckId) {
    const form = document.getElementById('wreck-photo-form');
    const status = document.getElementById('wreck-photo-status');
    const submit = document.getElementById('wreck-photo-submit');
    const filesInput = document.getElementById('wreck-photo-files');
    form?.reset();
    updateFilePickerSummary(filesInput);
    document.getElementById('wreck-photo-wreck-id').value = wreckId;
    if (status) status.textContent = '';
    if (submit) {
        submit.disabled = false;
        submit.querySelector('span').textContent = t('modal.wreckPhoto.submit');
    }
}

async function openWreckPhotoModal(wreckId) {
    const id = safeWreckId(wreckId);
    if (!id) return;
    resetWreckPhotoModal(id);
    openModal('modal-wreck-photo-upload');
}

function validateWreckPhotoFiles(files) {
    const photoFiles = Array.from(files || []);
    if (!photoFiles.length) {
        throw new Error(t('modal.wreckPhoto.noFiles'));
    }
    if (photoFiles.length > WRECK_PHOTO_MAX_COUNT) {
        throw new Error(t('modal.wreckPhoto.fileCountError', { n: WRECK_PHOTO_MAX_COUNT }));
    }
    for (const file of photoFiles) {
        if (file.size > WRECK_PHOTO_MAX_BYTES) {
            throw new Error(t('modal.wreckPhoto.fileLimitError'));
        }
        if (file.type && !FIELD_PHOTO_ALLOWED_TYPES.has(file.type)) {
            throw new Error(t('modal.wreckPhoto.fileTypeError'));
        }
    }
}

async function submitWreckPhotoUpload(event) {
    event.preventDefault();
    const form = document.getElementById('wreck-photo-form');
    const wreckId = safeWreckId(document.getElementById('wreck-photo-wreck-id')?.value);
    const status = document.getElementById('wreck-photo-status');
    const submit = document.getElementById('wreck-photo-submit');
    if (!form || !wreckId) return;

    try {
        validateWreckPhotoFiles(document.getElementById('wreck-photo-files')?.files);
    } catch (err) {
        if (status) status.textContent = err.message;
        return;
    }

    if (submit) {
        submit.disabled = true;
        submit.querySelector('span').textContent = t('modal.wreckPhoto.uploading');
    }
    if (status) status.textContent = t('modal.wreckPhoto.uploading');

    try {
        const resp = await fetch(`${WRECKS_URL}/${encodeURIComponent(wreckId)}/photos`, {
            method: 'POST',
            body: new FormData(form),
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('modal.wreckPhoto.saveError'));
        }
        await loadSavedWrecks();
        closeModal();
        statusEl.textContent = t('modal.wreckPhoto.saved', { n: data.photo_count || 0 });
        statusEl.className = 'ok';
    } catch (err) {
        if (status) status.textContent = err.message || t('modal.wreckPhoto.saveError');
    } finally {
        if (submit) {
            submit.disabled = false;
            submit.querySelector('span').textContent = t('modal.wreckPhoto.submit');
        }
    }
}

function localDatetimeValue(date = new Date()) {
    const pad = n => String(n).padStart(2, '0');
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function resetReportPackageModal(wreckId, { extraPhotos = [] } = {}) {
    const form = document.getElementById('report-package-form');
    const result = document.getElementById('report-package-result');
    const status = document.getElementById('report-package-status');
    const submit = document.getElementById('report-package-submit');
    const extraPhotosHint = document.getElementById('report-package-extra-photos');
    const filesInput = document.getElementById('report-photos');
    reportPackageExtraPhotos = Array.isArray(extraPhotos) ? extraPhotos.slice(0, REPORT_PHOTO_MAX_COUNT) : [];
    form?.reset();
    updateFilePickerSummary(filesInput);
    document.getElementById('report-wreck-id').value = wreckId;
    const observedAt = form?.querySelector('[name="observed_at"]');
    if (observedAt) observedAt.value = localDatetimeValue();
    if (result) result.hidden = true;
    if (status) status.textContent = '';
    if (extraPhotosHint) {
        extraPhotosHint.hidden = reportPackageExtraPhotos.length === 0;
        extraPhotosHint.textContent = reportPackageExtraPhotos.length
            ? t('modal.report.extraPublicPhotos', { n: reportPackageExtraPhotos.length })
            : '';
    }
    if (submit) {
        submit.disabled = false;
        submit.querySelector('span').textContent = t('modal.report.submit');
    }
}

async function openReportPackageModal(wreckId, options = {}) {
    const id = safeWreckId(wreckId);
    if (!id) return;
    resetReportPackageModal(id, options);
    openModal('modal-report-package');
}

function validateReportPackageFiles(files, extraCount = 0) {
    const photoFiles = Array.from(files || []);
    if (photoFiles.length + extraCount > REPORT_PHOTO_MAX_COUNT) {
        throw new Error(t('modal.report.fileLimitError'));
    }
    const allowedTypes = new Set(['image/jpeg', 'image/png', 'image/webp']);
    for (const file of photoFiles) {
        if (file.size > REPORT_PHOTO_MAX_BYTES) {
            throw new Error(t('modal.report.fileLimitError'));
        }
        if (file.type && !allowedTypes.has(file.type)) {
            throw new Error(t('modal.report.fileTypeError'));
        }
    }
}

async function appendReportPackageExtraPhotos(formData) {
    for (const photo of reportPackageExtraPhotos) {
        const url = String(photo.url || '');
        if (!url.startsWith('/')) continue;
        const resp = await fetch(url, { cache: 'no-store' });
        if (!resp.ok) throw new Error(t('modal.report.extraPublicPhotosError'));
        const blob = await resp.blob();
        formData.append('photos[]', blob, photo.filename || 'zdjecie_terenowe.jpg');
    }
}

async function submitReportPackage(event) {
    event.preventDefault();
    const form = document.getElementById('report-package-form');
    const wreckId = safeWreckId(document.getElementById('report-wreck-id')?.value);
    const status = document.getElementById('report-package-status');
    const submit = document.getElementById('report-package-submit');
    const result = document.getElementById('report-package-result');
    if (!form || !wreckId) return;
    if (!form.reportValidity()) return;

    try {
        validateReportPackageFiles(document.getElementById('report-photos')?.files, reportPackageExtraPhotos.length);
    } catch (err) {
        if (status) status.textContent = err.message;
        return;
    }

    if (submit) {
        submit.disabled = true;
        submit.querySelector('span').textContent = t('modal.report.generating');
    }
    if (status) status.textContent = t('modal.report.generating');
    if (result) result.hidden = true;

    try {
        const reportPath = adminAuthenticated ? 'report-package' : 'public-report-package';
        const formData = new FormData(form);
        await appendReportPackageExtraPhotos(formData);
        const resp = await fetch(`${WRECKS_URL}/${encodeURIComponent(wreckId)}/${reportPath}`, {
            method: 'POST',
            body: formData,
        });
        const data = await resp.json().catch(() => ({}));
        if (!resp.ok || data.status !== 'ok') {
            throw new Error(data.error || t('wreck.reportPackageError'));
        }
        document.getElementById('report-package-recipient').value = data.recipient || '';
        document.getElementById('report-package-subject').value = data.subject || '';
        document.getElementById('report-package-body').value = data.body || '';
        document.getElementById('report-package-download').href = data.zip_url || '#';
        document.getElementById('report-package-pdf').href = data.pdf_url || '#';
        if (result) result.hidden = false;
        if (status) status.textContent = t('modal.report.ready');
    } catch (err) {
        if (status) status.textContent = err.message || t('wreck.reportPackageError');
    } finally {
        if (submit) {
            submit.disabled = false;
            submit.querySelector('span').textContent = t('modal.report.submit');
        }
    }
}

async function copyReportEmailDraft() {
    const recipient = document.getElementById('report-package-recipient')?.value || '';
    const subject = document.getElementById('report-package-subject')?.value || '';
    const body = document.getElementById('report-package-body')?.value || '';
    const text = `Do: ${recipient}\nTemat: ${subject}\n\n${body}`;
    try {
        await navigator.clipboard.writeText(text);
    } catch (_) {
        const draft = document.getElementById('report-package-body');
        draft?.focus();
        draft?.select();
        document.execCommand('copy');
    }
    const status = document.getElementById('report-package-status');
    if (status) status.textContent = t('modal.report.copied');
}

async function runAll() {
    if (btnRun.disabled) return;
    const center = map.getCenter();
    const lat = center.lat;
    const lon = center.lng;
    const width = currentWidth;
    const height = currentHeight;
    const model = document.getElementById('model-select').value;
    const modelName = model.includes('m-obb') ? 'Medium' : 'Small';
    const conf = parseFloat(document.getElementById('conf-select').value);
    const cropM = parseFloat(document.getElementById('crop-select').value);

    let dlData = null;

    // 1. RESET — usuń stare wyniki i pokaż świeży progress
    clearResults();
    progressEl.hidden = false;
    setStep('download', 'active', t('step.download.label'), t('step.download.area', { w: width, h: height }));
    setStep('detect', 'pending');

    btnRun.disabled = true;
    spinner.style.display = 'block';
    runIcon.style.display = 'none';

    // 2. POBIERZ
    startDownloadProgressPolling();
    try {
        const dlResp = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lat, lon, width, height }),
        });
        dlData = await dlResp.json();
        if (!dlResp.ok || dlData.status !== 'completed') {
            currentJobToken = null;
            setStep('download', 'error', t('step.download.error'), dlData.error || t('step.download.unknownError'));
            setStepProgress('download', null, false);
            return;
        }
        const okCount = dlData.saved || 0;
        const missingCount = dlData.missing || 0;
        const totalCount = dlData.total || 0;
        const wfsReplaced = dlData.wfs_replaced || 0;
        const wfsCacheHits = dlData.wfs_cache_hits || 0;
        const wfsDownloaded = dlData.wfs_downloaded || 0;
        currentJobToken = dlData.job_token || null;
        lastDownload = { lat, lon, width, height };

        let metaParts = [];
        if (missingCount > 0) metaParts.push(t('step.download.missing', { n: missingCount }));
        if (wfsReplaced > 0) metaParts.push(t('step.download.wfs', { n: wfsReplaced, cached: wfsCacheHits, downloaded: wfsDownloaded }));
        const meta = metaParts.length ? metaParts.join(' · ') : null;

        setStep('download', 'done', t('step.download.done', { ok: okCount, total: totalCount }), meta);
        setStepProgress('download', 100, false);
    } catch (err) {
        currentJobToken = null;
        setStep('download', 'error', t('step.network.error'), err.message);
        setStepProgress('download', null, false);
        return;
    } finally {
        stopDownloadProgressPolling();
    }

    // 3. ANALIZUJ
    setStep('detect', 'active', t('step.detect.label'), t('step.detect.model', { name: modelName }));
    try {
        const anResp = await fetch(ANALYZE_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model, lang: CURRENT_LANG, conf, cropM, job_token: currentJobToken }),
        });
        const anData = await anResp.json();
        if (anResp.ok && anData.status === 'ok') {
            const reportLink = anData.report_url;
            const candidates = anData.candidates || [];
            const n = candidates.length;
            setStep('detect', 'done', n > 0 ? t(`step.detect.${carsForm(n)}`, { n }) : t('step.detect.none'));

            // Overlay z wynikami + obramowanie obszaru analizy (żeby było jasne czego dotyczył skan)
            if (dlData.bbox) {
                const bboxParts = dlData.bbox.split(',');
                const bounds = [[parseFloat(bboxParts[0]), parseFloat(bboxParts[1])], [parseFloat(bboxParts[2]), parseFloat(bboxParts[3])]];
                const overlayUrl = `/analiza/overlays/scored_overlay.jpg?ts=${Date.now()}`;
                imageOverlay = L.imageOverlay(overlayUrl, bounds, { opacity: 1.0, zIndex: 400 }).addTo(map);
                scanArea = L.rectangle(bounds, { color: '#fbbf24', weight: 2, fillOpacity: 0, dashArray: '6,4', interactive: false }).addTo(map);
            }

            placeMarkers(candidates, reportLink);

            btnReport.href = reportLink;
            reportLabelEl.textContent = t('result.reportShort');
            resultActions.hidden = false;
        } else {
            const errTxt = (anData.stderr || anData.error || '').split('\n').slice(-2).join(' ');
            setStep('detect', 'error', t('step.detect.error'), errTxt);
        }
    } catch (err) {
        setStep('detect', 'error', t('step.detect.networkError'), err.message);
    } finally {
        currentJobToken = null;
        btnRun.disabled = false;
        spinner.style.display = 'none';
        runIcon.style.display = 'block';
    }
}

function placeMarkers(candidates, reportLink) {
    const withCoords = candidates.filter(c => c.lat && c.lon);
    if (!withCoords.length) return;
    withCoords.forEach(c => {
        const sv    = `https://www.google.com/maps/@${c.lat},${c.lon},3a,75y,90h,75t/data=!3m6!1e1`;
        const gmap  = `https://www.google.com/maps/@${c.lat},${c.lon},80m/data=!3m1!1e3`;
        const amap  = `https://maps.apple.com/?ll=${c.lat},${c.lon}&z=20&t=k`;
        const mapil = `https://www.mapillary.com/app/?lat=${c.lat}&lng=${c.lon}&z=19`;
        const geop  = `https://mapy.geoportal.gov.pl/imap/Imgp_2.html?gpmap=gp0&lat=${c.lat}&lon=${c.lon}`;
        const saveButton = `<button type="button" class="map-popup-text-action" onclick="saveWreck(${c.rank}, this)">${t('wreck.save')}</button>`;
        const candidateLinks = [
            popupCompactLink(sv, t('popup.streetView'), t('popup.streetView')),
            popupCompactLink(gmap, t('popup.gmapsSat'), t('popup.gmapsSat')),
            popupCompactLink(amap, t('popup.appleMaps'), t('popup.appleMaps')),
            popupCompactLink(mapil, t('popup.mapillary'), t('popup.mapillary')),
            popupCompactLink(geop, t('popup.geoportal'), t('popup.geoportal')),
            popupCompactLink(reportLink, t('popup.report'), t('popup.report')),
        ];
        const popupHtml = `
            <div class="map-popup map-popup--candidate">
                ${popupHeader(t('popup.candidateTitle', { rank: c.rank }), `${(c.score * 100).toFixed(0)}%`)}
                ${popupMeta([
                    t('popup.metrics', { cov: (c.coverage * 100).toFixed(0), col: (c.color_consistency * 100).toFixed(0) }),
                    t('popup.present', { labels: c.labels_present.join(', ') }),
                    `${c.lat.toFixed(6)}, ${c.lon.toFixed(6)}`,
                ])}
                ${popupLinks(candidateLinks)}
                ${popupActions([saveButton])}
            </div>`;
        const marker = L.marker([c.lat, c.lon], { icon: pinIcon(c.rank, c.score) })
            .addTo(map)
            .bindPopup(popupHtml);
        candidateMarkers.push(marker);
    });
    // dopasuj widok aby pokazać wszystkie markery
    if (candidateMarkers.length > 0) {
        const group = L.featureGroup(candidateMarkers);
        map.fitBounds(group.getBounds().pad(0.15));
    }
}

// ─── MANUAL INSPECTION ON CLICK ───────────────────
// Inspekcja działa tylko gdy mamy pobrane ortofoto i klik trafia w obszar analizy.
// W innych przypadkach nie pokazujemy nic — żeby nie wyświetlać mylących błędów serwera.
map.on('click', async (e) => {
    if (e.originalEvent.shiftKey) return; // shift+drag is for area selection
    if (!lastDownload) return;             // nic nie pobrano jeszcze
    const inspectLat = Number(e.latlng.lat);
    const inspectLon = Number(e.latlng.lng);
    if (!Number.isFinite(inspectLat) || !Number.isFinite(inspectLon)) return;

    // Czy klik jest w obszarze ostatnio pobranego kwadratu?
    const lat_m = METERS_PER_DEGREE_LAT;
    const lon_m = METERS_PER_DEGREE_LAT * Math.cos(lastDownload.lat * Math.PI / 180);
    const dLatM = Math.abs(inspectLat - lastDownload.lat) * lat_m;
    const dLonM = Math.abs(inspectLon - lastDownload.lon) * lon_m;
    if (dLatM > lastDownload.height / 2 || dLonM > lastDownload.width / 2) return;

    const popup = L.popup({ maxWidth: 300 })
        .setLatLng([inspectLat, inspectLon])
        .setContent(`<div style="text-align:center; padding:10px;">${t('inspect.loading')}</div>`)
        .openOn(map);

    try {
        const res = await fetch('/api/inspect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lat: inspectLat, lon: inspectLon })
        });
        const data = await res.json();

        if (!res.ok || data.status !== 'ok') {
            map.closePopup(popup);
            return;
        }

        let gridHtml = '<div style="display:grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin-top: 8px;">';
        data.crops.forEach(c => {
            const fullUrl = c.url;
            gridHtml += `
                <div style="text-align:center;">
                    <div style="font-size:11px; font-weight:600; color:#555; margin-bottom:2px;">${c.year}</div>
                    <img src="${fullUrl}" style="width:100%; aspect-ratio:1; object-fit:cover; border-radius:4px; border:1px solid #ddd; box-shadow:0 1px 3px rgba(0,0,0,0.1);">
                </div>
            `;
        });
        gridHtml += '</div>';

        const content = `
            <div style="font-family:system-ui; width: 260px;">
                <div style="font-weight:700; font-size:14px; margin-bottom: 2px;">${t('inspect.title')}</div>
                <div style="font-size:11px; color:#666;">${t('inspect.coords', { lat: inspectLat.toFixed(6), lon: inspectLon.toFixed(6) })}</div>
                ${gridHtml}
                <button type="button" class="map-popup-text-action" onclick="saveManualWreck(${inspectLat.toFixed(8)}, ${inspectLon.toFixed(8)}, this)">${t('inspect.saveWreck')}</button>
            </div>
        `;
        popup.setContent(content);
    } catch (err) {
        map.closePopup(popup);
    }
});

refreshAdminStatus().finally(() => {
    updateLingeringCarsCounter();
    loadSavedWrecks();
    loadFieldPhotos();
});
document.addEventListener('langchange', updateLingeringCarsCounter);
