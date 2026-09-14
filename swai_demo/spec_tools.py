"""Pure data transformations shared by generation and verification."""

from __future__ import annotations

from copy import deepcopy
import json
import math
from typing import Any, Iterable


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _rotated_point(
    point: tuple[float, float],
    center: tuple[float, float],
    angle_deg: float,
) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    cos_a = math.cos(angle)
    sin_a = math.sin(angle)
    rel_x = point[0] - center[0]
    rel_y = point[1] - center[1]
    return (
        center[0] + rel_x * cos_a - rel_y * sin_a,
        center[1] + rel_x * sin_a + rel_y * cos_a,
    )


def expand_profile_instances(feature: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deterministic, fully positioned profile copies for a feature."""
    profile = feature.get("profile")
    if profile is None:
        return []
    pattern = feature.get("pattern")
    if pattern is None:
        return [deepcopy(profile)]

    result: list[dict[str, Any]] = []
    seed_x, seed_y = (float(value) for value in profile["center_mm"])
    if pattern["kind"] == "linear":
        count = int(pattern["instances"])
        direction = math.radians(float(pattern["direction_deg"]))
        step_x = float(pattern["spacing_mm"]) * math.cos(direction)
        step_y = float(pattern["spacing_mm"]) * math.sin(direction)
        offset = -(count - 1) / 2.0 if pattern["centered"] else 0.0
        for index in range(count):
            instance = deepcopy(profile)
            multiplier = offset + index
            instance["center_mm"] = [
                seed_x + multiplier * step_x,
                seed_y + multiplier * step_y,
            ]
            result.append(instance)
        return result

    count = int(pattern["instances"])
    total_angle = float(pattern["total_angle_deg"])
    step_angle = total_angle / count if math.isclose(
        total_angle, 360.0, abs_tol=1e-12
    ) else total_angle / (count - 1)
    center = tuple(float(value) for value in pattern["center_mm"])
    for index in range(count):
        angle = index * step_angle
        instance = deepcopy(profile)
        instance["center_mm"] = list(
            _rotated_point((seed_x, seed_y), center, angle)
        )
        if "angle_deg" in instance:
            instance["angle_deg"] = float(instance["angle_deg"]) + angle
        result.append(instance)
    return result


def profile_area_mm2(profile: dict[str, Any]) -> float:
    kind = profile["kind"]
    if kind == "circle":
        radius = float(profile["diameter_mm"]) / 2.0
        return math.pi * radius * radius
    if kind == "rectangle":
        return float(profile["width_mm"]) * float(profile["depth_mm"])
    length = float(profile["length_mm"])
    width = float(profile["width_mm"])
    return (length - width) * width + math.pi * (width / 2.0) ** 2


def polygon_area_mm2(vertices: Iterable[Iterable[float]]) -> float:
    """Return the absolute shoelace area of a validated polygon."""
    points = [tuple(float(value) for value in point) for point in vertices]
    signed_twice_area = sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:] + points[:1])
    )
    return abs(signed_twice_area) / 2.0


def profile_bounding_radius_mm(profile: dict[str, Any]) -> float:
    kind = profile["kind"]
    if kind == "circle":
        return float(profile["diameter_mm"]) / 2.0
    if kind == "slot":
        return float(profile["length_mm"]) / 2.0
    return math.hypot(
        float(profile["width_mm"]) / 2.0,
        float(profile["depth_mm"]) / 2.0,
    )


def profile_bounds_mm(profile: dict[str, Any]) -> list[float]:
    center_x, center_y = (float(value) for value in profile["center_mm"])
    kind = profile["kind"]
    if kind == "circle":
        half_x = half_y = float(profile["diameter_mm"]) / 2.0
    else:
        if kind == "rectangle":
            raw_x = float(profile["width_mm"]) / 2.0
            raw_y = float(profile["depth_mm"]) / 2.0
        else:
            raw_x = float(profile["length_mm"]) / 2.0
            raw_y = float(profile["width_mm"]) / 2.0
        angle = math.radians(float(profile["angle_deg"]))
        half_x = abs(raw_x * math.cos(angle)) + abs(raw_y * math.sin(angle))
        half_y = abs(raw_x * math.sin(angle)) + abs(raw_y * math.cos(angle))
    return [
        center_x - half_x,
        center_y - half_y,
        center_x + half_x,
        center_y + half_y,
    ]


def base_height_mm(spec: dict[str, Any]) -> float:
    return float(spec["base"]["height_mm"])


def base_bounds_mm(spec: dict[str, Any]) -> list[float]:
    base = spec["base"]
    if base["kind"] == "box":
        half_x = float(base["width_mm"]) / 2.0
        half_y = float(base["depth_mm"]) / 2.0
    elif base["kind"] == "prism":
        vertices = [
            tuple(float(value) for value in point)
            for point in base["vertices_mm"]
        ]
        x_values = [point[0] for point in vertices]
        y_values = [point[1] for point in vertices]
        return [
            min(x_values),
            min(y_values),
            0.0,
            max(x_values),
            max(y_values),
            float(base["height_mm"]),
        ]
    else:
        half_x = half_y = float(
            base.get("diameter_mm", base.get("outer_diameter_mm"))
        ) / 2.0
    return [-half_x, -half_y, 0.0, half_x, half_y, float(base["height_mm"])]


def base_volume_mm3(spec: dict[str, Any]) -> float:
    base = spec["base"]
    height = float(base["height_mm"])
    if base["kind"] == "box":
        return float(base["width_mm"]) * float(base["depth_mm"]) * height
    if base["kind"] == "cylinder":
        return math.pi * (float(base["diameter_mm"]) / 2.0) ** 2 * height
    if base["kind"] == "tube":
        outer = float(base["outer_diameter_mm"]) / 2.0
        inner = float(base["inner_diameter_mm"]) / 2.0
        return math.pi * (outer * outer - inner * inner) * height
    return polygon_area_mm2(base["vertices_mm"]) * height


def iter_extrude_instances(
    spec: dict[str, Any],
) -> Iterable[tuple[dict[str, Any], dict[str, Any]]]:
    for feature in spec["features"]:
        if feature["operation"] in {"add_extrude", "cut_extrude"}:
            for profile in expand_profile_instances(feature):
                yield feature, profile


def expected_geometry(spec: dict[str, Any]) -> dict[str, Any]:
    """Calculate independent analytic expectations from the DesignSpec."""
    bounds = base_bounds_mm(spec)
    base_height = base_height_mm(spec)
    expected_volume = base_volume_mm3(spec)
    holes: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    generic_extrudes: list[dict[str, Any]] = []
    modifiers: list[dict[str, Any]] = []

    for feature in spec["features"]:
        operation = feature["operation"]
        if operation in {"fillet", "chamfer"}:
            modifiers.append(deepcopy(feature))
            continue
        extent = feature["extent"]
        distance = (
            base_height
            if extent["kind"] == "through_all"
            else float(extent["distance_mm"])
        )
        sign = 1.0 if operation == "add_extrude" else -1.0
        for instance_index, profile in enumerate(
            expand_profile_instances(feature), 1
        ):
            expected_volume += sign * profile_area_mm2(profile) * distance
            if operation == "add_extrude":
                profile_bounds = profile_bounds_mm(profile)
                bounds[0] = min(bounds[0], profile_bounds[0])
                bounds[1] = min(bounds[1], profile_bounds[1])
                bounds[3] = max(bounds[3], profile_bounds[2])
                bounds[4] = max(bounds[4], profile_bounds[3])
                bounds[5] = max(bounds[5], base_height + distance)
            if profile["kind"] == "circle" and profile["purpose"] == "hole":
                holes.append(
                    {
                        "feature_id": feature["id"],
                        "center_mm": [float(v) for v in profile["center_mm"]],
                        "diameter_mm": float(profile["diameter_mm"]),
                        "end_condition": extent["kind"],
                        "depth_mm": distance,
                    }
                )
            if profile["kind"] == "slot" and profile["purpose"] == "slot":
                slots.append(
                    {
                        "feature_id": feature["id"],
                        "center_mm": [float(v) for v in profile["center_mm"]],
                        "length_mm": float(profile["length_mm"]),
                        "width_mm": float(profile["width_mm"]),
                        "angle_deg": float(profile["angle_deg"]),
                        "end_condition": extent["kind"],
                        "depth_mm": distance,
                    }
                )
            if profile["purpose"] == "generic":
                generic_extrudes.append(
                    {
                        "feature_id": feature["id"],
                        "instance_index": instance_index,
                        "operation": operation,
                        "profile": deepcopy(profile),
                        "end_condition": extent["kind"],
                        "depth_mm": distance,
                    }
                )

    expected_final_volume = expected_volume
    modifier_geometry: dict[str, Any] | None = None
    if modifiers:
        modifier = modifiers[0]
        base = spec["base"]
        size = float(modifier["size_mm"])
        width = float(base["width_mm"])
        depth = float(base["depth_mm"])
        height = float(base["height_mm"])
        if modifier["operation"] == "fillet":
            removed_volume = 4.0 * (
                size * size - math.pi * size * size / 4.0
            ) * height
            expected_final_volume -= removed_volume
            modifier_geometry = {
                "feature_id": modifier["id"],
                "operation": "fillet",
                "size_mm": size,
                "expected_face_count": 4,
                "axis_centers_mm": [
                    [x_sign * (width / 2.0 - size), y_sign * (depth / 2.0 - size)]
                    for x_sign in (-1.0, 1.0)
                    for y_sign in (-1.0, 1.0)
                ],
                "z_span_mm": height,
            }
        else:
            removed_volume = (
                size * size * (width + depth) - (4.0 / 3.0) * size**3
            )
            expected_final_volume -= removed_volume
            x_min, x_max = -width / 2.0, width / 2.0
            y_min, y_max = -depth / 2.0, depth / 2.0
            z_min = height - size
            modifier_geometry = {
                "feature_id": modifier["id"],
                "operation": "chamfer",
                "size_mm": size,
                "expected_face_count": 4,
                "top_face_size_mm": [width - 2.0 * size, depth - 2.0 * size],
                "top_face_bounding_box_mm": {
                    "min": [x_min + size, y_min + size, height],
                    "max": [x_max - size, y_max - size, height],
                },
                "face_bounding_boxes_mm": [
                    {
                        "side": "negative_x",
                        "min": [x_min, y_min, z_min],
                        "max": [x_min + size, y_max, height],
                    },
                    {
                        "side": "negative_y",
                        "min": [x_min, y_min, z_min],
                        "max": [x_max, y_min + size, height],
                    },
                    {
                        "side": "positive_y",
                        "min": [x_min, y_max - size, z_min],
                        "max": [x_max, y_max, height],
                    },
                    {
                        "side": "positive_x",
                        "min": [x_max - size, y_min, z_min],
                        "max": [x_max, y_max, height],
                    },
                ],
                "top_z_mm": height,
            }

    return {
        "bounding_box_mm": {
            "min": bounds[:3],
            "max": bounds[3:],
            "size": [
                bounds[3] - bounds[0],
                bounds[4] - bounds[1],
                bounds[5] - bounds[2],
            ],
        },
        "pre_modifier_volume_mm3": expected_volume,
        "expected_volume_mm3": expected_final_volume,
        "volume_mode": "analytic",
        "holes": holes,
        "slots": slots,
        "generic_extrudes": generic_extrudes,
        "modifiers": modifiers,
        "modifier_geometry": modifier_geometry,
        "base_geometry": (
            {
                "kind": "prism",
                "vertices_mm": deepcopy(spec["base"]["vertices_mm"]),
                "height_mm": float(spec["base"]["height_mm"]),
            }
            if spec["base"]["kind"] == "prism"
            else None
        ),
        "solid_count": int(spec["verification"]["expected_solid_count"]),
    }
