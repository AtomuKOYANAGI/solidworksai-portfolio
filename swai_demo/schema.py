"""Draft 2020-12 and fail-closed semantic validation for DesignSpec."""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from .errors import DesignSpecError
from .spec_tools import (
    base_bounds_mm,
    base_height_mm,
    expand_profile_instances,
    polygon_area_mm2,
    profile_bounding_radius_mm,
    profile_bounds_mm,
)


SCHEMA_PATH = Path(__file__).with_name("design_spec.schema.json")


def _cross(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    return (
        (second[0] - first[0]) * (third[1] - first[1])
        - (second[1] - first[1]) * (third[0] - first[0])
    )


def _point_on_segment(
    first: tuple[float, float],
    second: tuple[float, float],
    point: tuple[float, float],
    tolerance: float,
) -> bool:
    return (
        min(first[0], second[0]) - tolerance
        <= point[0]
        <= max(first[0], second[0]) + tolerance
        and min(first[1], second[1]) - tolerance
        <= point[1]
        <= max(first[1], second[1]) + tolerance
    )


def _segments_intersect(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    tolerance = 1e-9
    orientations = (
        _cross(first_start, first_end, second_start),
        _cross(first_start, first_end, second_end),
        _cross(second_start, second_end, first_start),
        _cross(second_start, second_end, first_end),
    )
    if (
        orientations[0] * orientations[1] < -(tolerance**2)
        and orientations[2] * orientations[3] < -(tolerance**2)
    ):
        return True
    pairs = (
        (orientations[0], first_start, first_end, second_start),
        (orientations[1], first_start, first_end, second_end),
        (orientations[2], second_start, second_end, first_start),
        (orientations[3], second_start, second_end, first_end),
    )
    return any(
        abs(orientation) <= tolerance
        and _point_on_segment(start, end, point, tolerance)
        for orientation, start, end, point in pairs
    )


def _validate_prism_vertices(vertices_value: list[list[float]]) -> None:
    tolerance = 1e-9
    vertices = [
        tuple(float(value) for value in point) for point in vertices_value
    ]
    if len(set(vertices)) != len(vertices):
        raise DesignSpecError("prism vertices must be unique")
    if polygon_area_mm2(vertices) <= tolerance:
        raise DesignSpecError("prism polygon area must be positive")
    count = len(vertices)
    for index, vertex in enumerate(vertices):
        previous = vertices[(index - 1) % count]
        following = vertices[(index + 1) % count]
        if math.dist(vertex, following) <= tolerance:
            raise DesignSpecError("prism edges must have positive length")
        if abs(_cross(previous, vertex, following)) <= tolerance:
            raise DesignSpecError(
                "prism polygon cannot contain collinear consecutive vertices"
            )
    for first_index in range(count):
        first_start = vertices[first_index]
        first_end = vertices[(first_index + 1) % count]
        for second_index in range(first_index + 1, count):
            if (
                second_index == first_index
                or second_index == (first_index + 1) % count
                or (second_index + 1) % count == first_index
            ):
                continue
            second_start = vertices[second_index]
            second_end = vertices[(second_index + 1) % count]
            if _segments_intersect(
                first_start,
                first_end,
                second_start,
                second_end,
            ):
                raise DesignSpecError("prism polygon must not self-intersect")


def _signed_twice_polygon_area(vertices_value: list[list[float]]) -> float:
    vertices = [
        tuple(float(value) for value in point) for point in vertices_value
    ]
    return sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(vertices, vertices[1:] + vertices[:1])
    )


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _schema_path(error: Any) -> str:
    path = "$"
    for item in error.absolute_path:
        path += f"[{item}]" if isinstance(item, int) else f".{item}"
    return path


def _is_inside_base(spec: dict[str, Any], profile: dict[str, Any]) -> bool:
    tolerance = 1e-9
    base = spec["base"]
    center_x, center_y = (float(value) for value in profile["center_mm"])
    if base["kind"] == "box":
        bounds = profile_bounds_mm(profile)
        base_bounds = base_bounds_mm(spec)
        return (
            bounds[0] >= base_bounds[0] - tolerance
            and bounds[1] >= base_bounds[1] - tolerance
            and bounds[2] <= base_bounds[3] + tolerance
            and bounds[3] <= base_bounds[4] + tolerance
        )
    outer_radius = float(
        base.get("diameter_mm", base.get("outer_diameter_mm"))
    ) / 2.0
    profile_radius = profile_bounding_radius_mm(profile)
    return math.hypot(center_x, center_y) + profile_radius <= outer_radius + tolerance


def _check_non_overlapping(
    profiles: list[dict[str, Any]], feature_id: str
) -> None:
    for first_index, first in enumerate(profiles):
        first_center = [float(value) for value in first["center_mm"]]
        first_radius = profile_bounding_radius_mm(first)
        for second in profiles[first_index + 1 :]:
            second_center = [float(value) for value in second["center_mm"]]
            second_radius = profile_bounding_radius_mm(second)
            separation = math.hypot(
                first_center[0] - second_center[0],
                first_center[1] - second_center[1],
            )
            if separation <= first_radius + second_radius + 1e-9:
                raise DesignSpecError(
                    f"{feature_id}: patterned profile instances overlap or touch"
                )


def validate_design_spec(value: Any) -> dict[str, Any]:
    """Return a detached validated spec or raise a bounded error."""
    errors = sorted(
        _validator().iter_errors(value),
        key=lambda item: (list(item.absolute_path), item.message),
    )
    if errors:
        first = errors[0]
        raise DesignSpecError(f"{_schema_path(first)}: {first.message}")

    spec = deepcopy(value)
    base = spec["base"]
    if base["kind"] == "tube" and not (
        float(base["inner_diameter_mm"]) < float(base["outer_diameter_mm"])
    ):
        raise DesignSpecError("tube inner diameter must be smaller than outer diameter")
    if base["kind"] == "prism":
        _validate_prism_vertices(base["vertices_mm"])
        if _signed_twice_polygon_area(base["vertices_mm"]) < 0.0:
            base["vertices_mm"].reverse()
        if spec["features"]:
            raise DesignSpecError(
                "prism bases do not yet accept secondary features"
            )

    identifiers = [feature["id"] for feature in spec["features"]]
    if len(identifiers) != len(set(identifiers)):
        raise DesignSpecError("feature ids must be unique")

    modifiers = [
        feature
        for feature in spec["features"]
        if feature["operation"] in {"fillet", "chamfer"}
    ]
    if modifiers:
        if len(modifiers) != 1 or len(spec["features"]) != 1:
            raise DesignSpecError(
                "safe fillet/chamfer requires exactly one modifier on an "
                "otherwise unmodified box/plate base"
            )
        modifier = modifiers[0]
        required_selection = {
            "fillet": "vertical_edges",
            "chamfer": "top_outer_edges",
        }[modifier["operation"]]
        if modifier["selection"] != required_selection:
            raise DesignSpecError(
                f"{modifier['id']}: {modifier['operation']} requires the fixed "
                f"{required_selection} selection"
            )

    modifier_seen = False
    base_height = base_height_mm(spec)
    for feature in spec["features"]:
        operation = feature["operation"]
        if operation in {"fillet", "chamfer"}:
            modifier_seen = True
            if base["kind"] != "box":
                raise DesignSpecError(
                    f"{feature['id']}: safe modifiers are limited to box/plate bases"
                )
            min_base_dimension = min(
                float(base["width_mm"]),
                float(base["depth_mm"]),
                float(base["height_mm"]),
            )
            if float(feature["size_mm"]) >= min_base_dimension / 2.0:
                raise DesignSpecError(
                    f"{feature['id']}: modifier size must be less than half the "
                    "smallest base dimension"
                )
            continue
        if modifier_seen:
            raise DesignSpecError("extrude features must precede fillet/chamfer features")

        profile = feature["profile"]
        extent = feature["extent"]
        if operation == "add_extrude" and extent["kind"] != "distance":
            raise DesignSpecError(
                f"{feature['id']}: additive extrude requires a fixed distance"
            )
        if operation == "cut_extrude" and profile["purpose"] == "hole" and (
            profile["kind"] != "circle"
        ):
            raise DesignSpecError(f"{feature['id']}: a hole must use a circle profile")
        if profile["purpose"] in {"hole", "slot"} and operation != "cut_extrude":
            raise DesignSpecError(
                f"{feature['id']}: hole/slot purpose requires cut_extrude"
            )
        if profile["kind"] == "slot" and not (
            float(profile["length_mm"]) > float(profile["width_mm"])
        ):
            raise DesignSpecError(
                f"{feature['id']}: slot overall length must exceed slot width"
            )
        if extent["kind"] == "distance" and operation == "cut_extrude" and not (
            float(extent["distance_mm"]) < base_height
        ):
            raise DesignSpecError(
                f"{feature['id']}: blind cut depth must be less than base height"
            )

        instances = expand_profile_instances(feature)
        _check_non_overlapping(instances, feature["id"])
        for instance in instances:
            if not _is_inside_base(spec, instance):
                raise DesignSpecError(
                    f"{feature['id']}: profile is not fully inside the base footprint"
                )

    return spec
