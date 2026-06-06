// Centralna konfiguracja frontendu. Zmiany tutaj wpływają tylko na zachowanie
// przeglądarki; limity bezpieczeństwa backendu są osobno egzekwowane w Pythonie.

// Klucze localStorage. Zmiana nazwy resetuje zapisane preferencje użytkownika.
const PANEL_COLLAPSED_KEY = 'wroclaw-ortho-panel-collapsed';
const MODAL_POSITION_STORAGE_PREFIX = 'wroclaw-ortho-modal-position:';
const MAP_VIEW_STORAGE_KEY = 'carsDetector.mapView';
const CROSSHAIR_HIDDEN_STORAGE_KEY = 'wroclaw-ortho-crosshair-hidden';
const CADASTRAL_LAYER_VISIBLE_STORAGE_KEY = 'wroclaw-ortho-cadastral-visible';

// Endpointy są relatywne, żeby aplikacja działała przez tunel/proxy bez
// twardego hosta i portu w JS.
const API_URL = '/api/download';
const DOWNLOAD_PROGRESS_URL = '/api/download/progress';
const ANALYZE_URL = '/api/analyze';
const SETTINGS_URL = '/api/settings';
const WRECKS_URL = '/api/wrecks';
const FIELD_PHOTOS_URL = '/api/field-photos';
const CADASTRAL_IDENTIFY_URL = '/api/cadastral/identify';
const ADMIN_STATUS_URL = '/api/admin/status';
const ADMIN_LOGIN_URL = '/api/admin/login';
const ADMIN_LOGOUT_URL = '/api/admin/logout';
const ADMIN_PHOTOS_URL = '/api/admin/photos';
const ADMIN_PRIVACY_REQUESTS_URL = '/api/admin/privacy-requests';
const ADMIN_PHOTO_RETENTION_URL = '/api/admin/photo-retention';

// Źródła podkładu mapy w UI. Backend ma własną listę pobierania do analizy;
// frontend steruje tutaj wyłącznie podglądem w Leaflet.
const MAP_SOURCES = [
    { key: 'wroclaw-2020', shortLabel: '2020', label: 'Wrocław 2020', type: 'wroclaw', year: 2020 },
    { key: 'wroclaw-2021', shortLabel: '2021', label: 'Wrocław 2021', type: 'wroclaw', year: 2021 },
    { key: 'wroclaw-2022', shortLabel: '2022', label: 'Wrocław 2022', type: 'wroclaw', year: 2022 },
    { key: 'wroclaw-2023', shortLabel: '2023', label: 'Wrocław 2023', type: 'wroclaw', year: 2023 },
    { key: 'wroclaw-2024', shortLabel: '2024', label: 'Wrocław 2024', type: 'wroclaw', year: 2024 },
    { key: 'wroclaw-2025', shortLabel: '2025', label: 'Wrocław 2025', type: 'wroclaw', year: 2025 },
    {
        key: 'geoportal-standard',
        shortLabel: 'STND',
        label: 'Geoportal standard',
        type: 'wms',
        url: 'https://mapy.geoportal.gov.pl/wss/service/PZGIK/ORTO/WMS/StandardResolution',
        layers: 'Raster',
        version: '1.3.0',
        attribution: 'Geoportal.gov.pl / GUGiK',
    },
];
const DEFAULT_MAP_SOURCE_KEY = 'wroclaw-2025';
const CADASTRAL_WMS_URL = 'https://integracja.gugik.gov.pl/cgi-bin/KrajowaIntegracjaEwidencjiGruntow';
const CADASTRAL_WMS_LAYERS = 'dzialki,numery_dzialek';
const DEFAULT_MAP_VIEW = {
    center: [51.089742, 17.038940],
    zoom: 19,
};
const MAX_MAP_ZOOM = 22;
const METERS_PER_DEGREE_LAT = 111320;

// Rozmiar obszaru skanu w UI. Backend ma własny limit bezpieczeństwa 50 m;
// minimum 50 m usuwa mało użyteczne, zbyt ciasne kadry z interfejsu.
const SCAN_AREA_MIN_M = 50;
const SCAN_AREA_MAX_M = 50;
const SCAN_AREA_STEP_M = 5;

// Crosshair pokazuje realny obszar skanu w pikselach mapy. Maksimum zależy od
// viewportu, żeby przy dużym zoomie kwadrat nie zatrzymywał się zbyt wcześnie.
const CROSSHAIR_MIN_PX = 48;
const CROSSHAIR_MAX_VIEWPORT_RATIO = 0.82;

// Poziomy szczegółowości pinezek. Nie skalujemy płynnie ikon, tylko zdejmujemy
// detale przy oddalaniu: najpierw liczniki, potem zostają małe klikane kropki.
const MARKER_DETAIL_FULL_MIN_ZOOM = 18;
const MARKER_DETAIL_DOT_MAX_ZOOM = 16;

// Uploady: backend dalej waliduje te same limity, frontend tylko daje szybszy
// komunikat przed wysłaniem formularza.
const REPORT_PHOTO_MAX_COUNT = 5;
const REPORT_PHOTO_MAX_BYTES = 10 * 1024 * 1024;
const WRECK_PHOTO_MAX_COUNT = 25;
const WRECK_PHOTO_MAX_BYTES = 10 * 1024 * 1024;
const FIELD_PHOTO_MAX_BYTES = 10 * 1024 * 1024;
const FIELD_PHOTO_MAX_FILES = 25;
const FIELD_PHOTO_ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp']);
const FIELD_PHOTO_ISSUE_TYPE_VEHICLE = 'vehicle';
const FIELD_PHOTO_ISSUE_TYPES = new Set([
    FIELD_PHOTO_ISSUE_TYPE_VEHICLE,
    'infrastructure',
    'smoke',
]);
const PUBLIC_LAYER_KEYS = {
    savedWrecks: 'saved_wrecks',
    fieldPhotoVehicle: 'field_photo_vehicle',
    fieldPhotoInfrastructure: 'field_photo_infrastructure',
    fieldPhotoSmoke: 'field_photo_smoke',
};
const FIELD_PHOTO_PUBLIC_LAYER_KEYS = {
    vehicle: PUBLIC_LAYER_KEYS.fieldPhotoVehicle,
    infrastructure: PUBLIC_LAYER_KEYS.fieldPhotoInfrastructure,
    smoke: PUBLIC_LAYER_KEYS.fieldPhotoSmoke,
};

// Grupowanie zdjęć terenowych na mapie. 1 m jest celowo ciasne: większy promień
// scalał zdjęcia z sąsiednich pojazdów albo różnych stron tego samego parkingu.
const FIELD_PHOTO_GROUP_RADIUS_M = 1;
// Maksymalna odległość przeciągniętej pinezki zdjęć od teczki pojazdu, przy której zdjęcia
// są przenoszone do tej teczki i znikają z warstwy zdjęć terenowych.
const FIELD_PHOTO_ATTACH_TO_WRECK_RADIUS_M = 1;

// Timery UI. Krótsze wartości dają szybszą reakcję kosztem większej liczby
// requestów/przełączeń warstw; dłuższe uspokajają słabsze urządzenia.
const ORTHO_LAYER_SWAP_FALLBACK_MS = 3000;
const ENHANCEMENT_SETTINGS_SAVE_DEBOUNCE_MS = 350;
const STEP_TIMER_INTERVAL_MS = 500;
const DOWNLOAD_PROGRESS_POLL_MS = 700;
