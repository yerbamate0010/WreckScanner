from dataclasses import dataclass
from pathlib import Path

BYTES_PER_MIB = 1024 * 1024
BYTES_PER_GIB = 1024 * BYTES_PER_MIB

DATA_DIR = Path("dane_dla_AI")
EXTRA_DIR = DATA_DIR / "geoportal_krajowy"
OUTPUT_DIR = Path("analiza")
WRECKS_DIR = Path("zidentyfikowane_wraki")
FIELD_PHOTOS_DIR = Path("zdjecia_terenowe")
PRIVATE_PHOTOS_DIR = Path("prywatne_zdjecia")
PRIVATE_REPORTS_DIR = Path("prywatne_zgloszenia")
PRIVACY_REQUESTS_DIR = Path("zgloszenia_prywatnosci")
CROPS_DIR_NAME = "crops"
OVERLAY_DIR_NAME = "overlays"

DEFAULT_MODEL = Path("weights/yolo11s-obb.pt")
MODEL_SMALL = Path("weights/yolo11s-obb.pt")
MODEL_MEDIUM = Path("weights/yolo11m-obb.pt")

# Stała gęstość pikseli przy pobieraniu z WMS: 1000 px na każde 50 m kadru.
# = 20 px/m = 5 cm/pixel — blisko natywnej rozdzielczości ortofoto UM Wrocławia.
# Wyżej oznacza upscaling po stronie WMS (rozmycie bilinearne), niżej — utratę detali dla YOLO.
NATIVE_TILE_PX = 1000


@dataclass(frozen=True)
class EnhancementSettings:
    """Konfiguracja wspólnego filtra ortofoto dla mapy i analizy YOLO."""

    enabled: bool = True
    clahe_clip_limit: float = 1.5
    clahe_tile_grid_size: int = 8
    l_percentile_low: float = 2.0
    l_percentile_high: float = 98.0
    l_output_low: float = 10.0
    l_output_high: float = 245.0
    l_min_percentile_span: float = 5.0
    decast_strength: float = 0.4


DEFAULT_ENHANCEMENT_SETTINGS = EnhancementSettings()


@dataclass(frozen=True)
class VehicleSizeRule:
    """Metrowe ramy filtra po YOLO OBB.

    Zwężenie zakresów odrzuci więcej fałszywych detekcji, ale może zgubić
    vany, busy albo pojazdy widziane pod kątem. Poszerzenie zakresów daje więcej
    kandydatów i więcej szumu do ręcznej weryfikacji.
    """

    min_length_m: float
    max_length_m: float
    min_width_m: float
    max_width_m: float
    min_aspect: float
    max_aspect: float
    min_area_m2: float
    max_area_m2: float


YOLO_CLASS_NAMES = {
    9: "large vehicle",
    10: "small vehicle",
}
CAR_CLASSES = set(YOLO_CLASS_NAMES)

# Domyślna klasa pojazdu osobowego. Duże pojazdy dostają osobny, luźniejszy
# limit długości, żeby nie wycinać busów i cięższych pojazdów dostawczych.
DEFAULT_VEHICLE_SIZE_RULE = VehicleSizeRule(
    min_length_m=2.2,
    max_length_m=7.5,
    min_width_m=0.9,
    max_width_m=3.4,
    min_aspect=1.25,
    max_aspect=5.8,
    min_area_m2=2.5,
    max_area_m2=32.0,
)
VEHICLE_SIZE_RULES = {
    9: VehicleSizeRule(
        min_length_m=2.2,
        max_length_m=10.5,
        min_width_m=0.9,
        max_width_m=3.4,
        min_aspect=1.25,
        max_aspect=5.8,
        min_area_m2=2.5,
        max_area_m2=32.0,
    ),
    10: DEFAULT_VEHICLE_SIZE_RULE,
}

MIN_DETECTIONS = 3
MIN_SCORE = 0.42
MAX_ANGLE_DIFF_DEG = 35.0
REVIEW_CROP_M = 7.5
REVIEW_CROP_M_MIN = 5.0
REVIEW_CROP_M_MAX = 20.0
REVIEW_JPEG_QUALITY = 95
WRECK_DEDUPE_M = 3.0

DEFAULT_CONF = 0.15
DEFAULT_EPS_M = 2.5

# YOLO OBB. Większy max_det i niższy próg confidence zwiększają czułość,
# ale zwracają więcej fałszywych ramek do scoringu.
YOLO_MAX_DET = 500
CURRENT_CONF_MAX = 0.10
CURRENT_DETECTION_SCALES = (0.75, 1.0, 1.25)
CURRENT_MERGE_EPS_M = 0.8
CURRENT_MERGE_EPS_MIN_PX = 12.0
OPTIMAL_CAR_PIXELS_PER_METER = 20.0
DEFAULT_DETECTION_IMGSZ = 2048
IMG_SIZE_MIN = 512
IMG_SIZE_MAX = 4096
IMG_SIZE_STRIDE = 32

# Wyrównywanie historycznych roczników względem referencji. Wyższe limity
# przesunięcia ratują gorzej spasowane ortofoto, ale mogą błędnie zaakceptować
# przesunięcie dla bardzo odmiennych kadrów.
ALIGN_MAX_DIM = 1024
ALIGN_MAX_SHIFT_MIN_PX = 30.0
ALIGN_MAX_SHIFT_IMAGE_FRACTION = 0.04
ALIGN_MIN_PHASE_RESPONSE = 0.025
ALIGN_MIN_WORKING_DIM_PX = 64
ALIGN_ALREADY_ALIGNED_SHIFT_PX = 0.5

# Ocena czy brak detekcji jest wiarygodny. Niższe progi drzew/cieni sprawiają,
# że więcej roczników zostanie zignorowanych zamiast liczyć jako "brak pojazdu".
TREE_EXG_THRESHOLD = 20.0
TREE_COVER_THRESHOLD = 0.40
LOCAL_VISIBILITY_PAD_FACTOR = 0.75
LOCAL_VISIBILITY_MIN_PAD_PX = 40
LOCAL_TOO_DARK_MEAN = 45.0
LOCAL_TOO_DARK_STD = 18.0
GLOBAL_BLUR_SHARPNESS = 150.0
LOCAL_BLUR_SHARPNESS = 18.0
LOCAL_BLUR_STD = 45.0
QUALITY_SAMPLE_SIZE_PX = 512
UNLIMITED_SHARPNESS_SENTINEL = 999.0

MATCH_WEIGHTS = {
    "distance": 0.42,
    "angle": 0.23,
    "shape": 0.20,
    "color": 0.15,
}
MATCH_MIN_SCORE = 0.48
MATCH_CLOSE_EPS_FACTOR = 0.35
MATCH_MIN_SHAPE_SIMILARITY = 0.45
MATCH_STRONG_COLOR_SIMILARITY = 0.72
MATCH_STRONG_COLOR_EPS_FACTOR = 0.45

SHAPE_LENGTH_WEIGHT = 0.65
SHAPE_WIDTH_WEIGHT = 0.35
LOW_QUALITY_DETECTION_CONF = 0.35
LOW_QUALITY_COLOR_SIMILARITY = 0.70

SCORE_WEIGHTS = {
    "coverage": 0.50,
    "color_consistency": 0.25,
    "mean_conf": 0.15,
    "span": 0.10,
}
EVIDENCE_FACTOR_BASE = 0.72
EVIDENCE_FACTOR_RANGE = 0.28
EVIDENCE_FULL_OBSERVATION_COUNT = 4.0
DETECTION_FACTOR_BASE = 0.82
DETECTION_FACTOR_RANGE = 0.18
CLEAR_MISSING_PENALTY = 0.08

# Kolor liczony jest z wnętrza obrysu pojazdu, po lekkiej normalizacji jasności.
# Większy shrink ignoruje krawędzie i cienie, ale przy małych pojazdach zostawia
# mniej pikseli do mediany HSV.
INNER_POLY_SCALE = 0.70
COLOR_NORMALIZATION_CLAHE_CLIP_LIMIT = 2.0
COLOR_NORMALIZATION_CLAHE_GRID_SIZE = 8
COLOR_HUE_WEIGHT = 0.45
COLOR_SAT_WEIGHT = 0.35
COLOR_VAL_WEIGHT = 0.20
COLOR_LOW_SATURATION_HUE_CUTOFF = 110.0

# Lokalne dopasowanie miniatur raportu. Wyższe response/niższy max shift dają
# stabilniejsze cropy, ale częściej wracają do środka z najnowszego rocznika.
LOCAL_CROP_ALIGN_MIN_PX = 160
LOCAL_CROP_ALIGN_CONTEXT_FACTOR = 2.5
LOCAL_CROP_ALIGN_MIN_RESPONSE = 0.045
LOCAL_CROP_ALIGN_REFERENCE_MIN_RESPONSE = 0.070
LOCAL_CROP_ALIGN_WEAK_DET_MIN_RESPONSE = 0.030
LOCAL_CROP_ALIGN_MAX_SHIFT_FACTOR = 0.65
LOCAL_CROP_ALIGN_REFERENCE_MAX_SHIFT_FACTOR = 0.35
LOCAL_CROP_ALIGN_MIN_ACCEPTED_SHIFT_PX = 20.0
DETECTION_CROP_MAX_DIST_M = 1.50
DETECTION_CROP_MIN_MATCH_SCORE = 0.60

# Uploady i raporty. Zwiększenie limitów poprawia wygodę, ale podnosi zużycie
# pamięci przy parsowaniu multipart i rozmiar lokalnych paczek zgłoszeniowych.
ALLOWED_UPLOAD_IMAGE_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
}
ALLOWED_REPORT_PHOTO_EXTENSIONS = {
    image_format: values[0] for image_format, values in ALLOWED_UPLOAD_IMAGE_FORMATS.items()
}

MAX_FIELD_PHOTO_BYTES = 10 * BYTES_PER_MIB
FIELD_PHOTO_MAX_BODY_BYTES = 12 * BYTES_PER_MIB
FIELD_PHOTO_THUMBNAIL_MAX_EDGE_PX = 360
FIELD_PHOTO_THUMBNAIL_JPEG_QUALITY = 82
PUBLIC_PHOTO_JPEG_QUALITY = 88
PRIVATE_ORIGINAL_RETENTION_DAYS = 180
DEFAULT_FIELD_PHOTO_ISSUE_TYPE = "vehicle"
# Typ obserwacji zapisany przy zdjęciu terenowym. Dodanie nowego typu tutaj
# pozwala rozróżniać pinezki bez mieszania ich z logiką teczek pojazdów.
FIELD_PHOTO_ISSUE_TYPES = {
    "vehicle": "zalegający pojazd",
    "infrastructure": "niebezpieczna infrastruktura",
    "smoke": "dym papierosowy",
}

MAX_WRECK_PHOTO_BYTES = 10 * BYTES_PER_MIB
MAX_WRECK_PHOTOS_PER_UPLOAD = 25
MAX_WRECK_PHOTO_BODY_BYTES = (MAX_WRECK_PHOTOS_PER_UPLOAD * MAX_WRECK_PHOTO_BYTES) + (2 * BYTES_PER_MIB)
WRECK_PHOTO_THUMB_MAX_EDGE_PX = 900
WRECK_PHOTO_THUMB_QUALITY = 84
# Ile miniaturek teczki pojazdu wysyłać do popupu mapy. Więcej obrazów daje bogatszy
# glance, ale zwiększa payload `/api/wrecks` i rozmiar dymka na mapie.
WRECK_POPUP_PREVIEW_MAX_IMAGES = 6

REPORT_RECIPIENT = "interwencje@smwroclaw.pl"
MAX_REPORT_PHOTOS = 5
MAX_REPORT_PHOTO_BYTES = 10 * BYTES_PER_MIB
MAX_REPORT_PACKAGE_BODY_BYTES = 60 * BYTES_PER_MIB
PUBLIC_REPORT_PACKAGE_TOKEN_TTL_SECONDS = 60 * 60
OPTIMIZED_PHOTO_MAX_EDGE_PX = 1600
OPTIMIZED_PHOTO_JPEG_QUALITY = 82

# Ustawienia trwałe widoczne w panelu. Szerszy zakres pozwala eksperymentować,
# ale zwiększa ryzyko bardzo ciężkich cache'y na słabszym sprzęcie.
SETTINGS_FILENAME = "settings.json"
DEFAULT_GEOTIFF_CACHE_MAX_GB = 4.0
GEOTIFF_CACHE_MAX_GB_RANGE = (2.0, 32.0)
