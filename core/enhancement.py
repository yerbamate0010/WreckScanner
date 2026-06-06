from __future__ import annotations

import cv2
import numpy as np

from core.config import DEFAULT_ENHANCEMENT_SETTINGS, EnhancementSettings
from core.models import ImageItem


def enhance_orthophoto(
    img: np.ndarray,
    settings: EnhancementSettings = DEFAULT_ENHANCEMENT_SETTINGS,
) -> np.ndarray:
    """Wspólny filtr ortofoto: CLAHE + auto-levels na L + soft decast a/b."""
    if not settings.enabled:
        return img.copy()

    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)

    tile_size = max(1, int(settings.clahe_tile_grid_size))
    clahe = cv2.createCLAHE(
        clipLimit=float(settings.clahe_clip_limit),
        tileGridSize=(tile_size, tile_size),
    )
    l_channel = clahe.apply(l_channel)

    l_float = l_channel.astype(np.float32)
    p_low, p_high = np.percentile(
        l_float,
        [settings.l_percentile_low, settings.l_percentile_high],
    )
    if p_high - p_low > settings.l_min_percentile_span:
        l_float = (l_float - p_low) * (settings.l_output_high - settings.l_output_low) / (
            p_high - p_low
        ) + settings.l_output_low
    l_channel = np.clip(l_float, 0, 255).astype(np.uint8)

    decast = float(settings.decast_strength)
    a_float = a_channel.astype(np.float32)
    b_float = b_channel.astype(np.float32)
    a_float = a_float - (a_float.mean() - 128.0) * decast
    b_float = b_float - (b_float.mean() - 128.0) * decast
    a_channel = np.clip(a_float, 0, 255).astype(np.uint8)
    b_channel = np.clip(b_float, 0, 255).astype(np.uint8)

    return cv2.cvtColor(cv2.merge([l_channel, a_channel, b_channel]), cv2.COLOR_LAB2BGR)


def enhance_image_items(
    items: list[ImageItem],
    settings: EnhancementSettings = DEFAULT_ENHANCEMENT_SETTINGS,
) -> None:
    """Zastosuj ten sam filtr do obrazów po wyrównaniu, przed analizą YOLO."""
    if not settings.enabled:
        print("Enhancement koloru: wyłączony")
        return

    print(enhancement_summary(settings))
    for item in items:
        img = item.img if item.img_aligned is None else item.img_aligned
        before_l = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[..., 0].mean()
        item.img_aligned = enhance_orthophoto(img, settings=settings)
        after_l = cv2.cvtColor(item.img_aligned, cv2.COLOR_BGR2LAB)[..., 0].mean()
        print(f" - {item.label}: L {before_l:.0f} -> {after_l:.0f}")


def enhancement_summary(settings: EnhancementSettings = DEFAULT_ENHANCEMENT_SETTINGS) -> str:
    status = "włączony" if settings.enabled else "wyłączony"
    return (
        "Enhancement koloru: "
        f"{status}, CLAHE={settings.clahe_clip_limit:g}, "
        f"percentyle L=[{settings.l_percentile_low:g}, {settings.l_percentile_high:g}], "
        f"zakres L=[{settings.l_output_low:g}, {settings.l_output_high:g}], "
        f"decast={settings.decast_strength:g}"
    )
