from __future__ import annotations

from core.config import DEFAULT_VEHICLE_SIZE_RULE, VEHICLE_SIZE_RULES


def looks_like_vehicle_size(feat: dict[str, float] | None, cls_id: int) -> bool:
    """Sprawdź metrowe wymiary OBB po YOLO bez importowania samego modelu YOLO."""
    if not feat or "length_m" not in feat:
        return True
    length = feat["length_m"]
    width = feat["width_m"]
    aspect = feat["aspect"]
    rule = VEHICLE_SIZE_RULES.get(cls_id, DEFAULT_VEHICLE_SIZE_RULE)
    return (
        rule.min_length_m <= length <= rule.max_length_m
        and rule.min_width_m <= width <= rule.max_width_m
        and rule.min_aspect <= aspect <= rule.max_aspect
        and rule.min_area_m2 <= feat["area_m2"] <= rule.max_area_m2
    )
