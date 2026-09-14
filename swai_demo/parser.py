"""Deterministic, fail-closed natural-language parser for known MVP orders."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable

from .controlled_parser import looks_like_controlled_request, parse_controlled_request
from .errors import OrderClarificationRequired, UnrecognizedOrderGrammar
from .schema import validate_design_spec


_NUMBER = r"(?:0|[1-9]\d*)(?:\.\d+)?"
_UNIT = r"(?:mm|millimeters?|millimetres?|インチ|inch(?:es)?|in\b|\")"
_UNSUPPORTED = {
    "assembly": ("アセンブリ", "assembly", "mate", "合致"),
    "fem": ("fem", "simulation", "解析", "最適化"),
    "sheet_metal": ("板金", "sheet metal"),
    "thread": ("完全なねじ", "thread geometry", "threaded"),
    "freeform": ("自由曲面", "freeform", "nurbs"),
}


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def _language(text: str) -> str:
    return "ja" if re.search(r"[ぁ-んァ-ヶ一-龠]", text) else "en"


def _factor(unit: str) -> float:
    normalized = unit.lower()
    return 25.4 if normalized in {"inch", "inches", "in", '"'} or unit == "インチ" else 1.0


def _measure_matches(text: str, patterns: Iterable[str]) -> list[tuple[float, str]]:
    result: list[tuple[float, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            result.append((float(match.group("value")), match.group("unit")))
    unique: list[tuple[float, str]] = []
    for value, unit in result:
        normalized = (value * _factor(unit), "inch" if _factor(unit) != 1.0 else "mm")
        if not any(abs(item[0] - normalized[0]) <= 1e-12 for item in unique):
            unique.append(normalized)
    return unique


def _required_measure(
    text: str,
    patterns: Iterable[str],
    field: str,
    label_ja: str,
    questions: list[dict[str, str]],
) -> tuple[float | None, str | None]:
    matches = _measure_matches(text, patterns)
    if not matches:
        questions.append(
            {
                "id": f"missing_{field}",
                "field": field,
                "question": f"{label_ja}を単位付きで指定してください。",
            }
        )
        return None, None
    if len(matches) != 1:
        questions.append(
            {
                "id": f"ambiguous_{field}",
                "field": field,
                "question": f"{label_ja}が複数解釈できます。値を1つに明確化してください。",
            }
        )
        return None, None
    return matches[0]


def _detect_unsupported(text: str) -> list[dict[str, str]]:
    lowered = text.lower()
    questions = []
    for category, terms in _UNSUPPORTED.items():
        if any(term.lower() in lowered for term in terms):
            questions.append(
                {
                    "id": f"unsupported_{category}",
                    "field": "scope",
                    "question": f"初版MVP対象外の要件（{category}）を除外してください。",
                }
            )
    return questions


def _reject_unconsumed_legacy_text(text: str) -> None:
    """Reject requirements outside the two-hole legacy sentence grammar."""

    residual = text
    quantity_patterns = (
        rf"(?:幅|width|奥行き?|depth|厚さ|板厚|thickness)\s*"
        rf"(?:は|=|:)?\s*{_NUMBER}\s*{_UNIT}",
        rf"(?:直径|diameter|dia\.?|φ|⌀)\s*{_NUMBER}\s*{_UNIT}",
        rf"{_NUMBER}\s*{_UNIT}\s*(?:径|diameter)の?(?:貫通穴|止まり穴|holes?)",
        rf"(?:左右|left\s+and\s+right(?:\s+by)?)\s*{_NUMBER}\s*{_UNIT}",
        rf"(?:深さ|depth)\s*(?:は|=|:)?\s*{_NUMBER}\s*{_UNIT}",
        r"(?:貫通穴|止まり穴|holes?)[^。\n]{0,40}?[1-9]\d*\s*(?:個|holes?)",
        r"[1-9]\d*\s*(?:個の?)\s*(?:貫通穴|止まり穴)",
    )
    for pattern in quantity_patterns:
        residual = re.sub(pattern, " ", residual, flags=re.IGNORECASE)

    allowed_phrases = (
        "板の中央線上で",
        "on the centerline",
        "on the center line",
        "through holes",
        "through hole",
        "through",
        "blind holes",
        "blind hole",
        "blind",
        "の板を作って",
        "の板を作り",
        "板を作って",
        "板を作り",
        "の位置に",
        "中央線上",
        "centerline",
        "center line",
        "プレート",
        "plate",
        "貫通穴",
        "止まり穴",
        "開けて",
        "開ける",
        "作成して",
        "作成",
        "create",
        "make",
        "drill",
        "holes",
        "hole",
        "板",
        "穴",
        "left",
        "right",
        "by",
        "on",
        "the",
        "a",
        "an",
        "of",
        "with",
        "and",
    )
    for phrase in sorted(allowed_phrases, key=len, reverse=True):
        residual = re.sub(
            re.escape(phrase), " ", residual, flags=re.IGNORECASE
        )
    residual = re.sub(r"[\s,，、。.=:：;；()（）・]+", "", residual)
    residual = re.sub(r"[のをにでとへがは]", "", residual)
    if residual:
        raise OrderClarificationRequired(
            [
                {
                    "id": "unconsumed_text_requirement",
                    "field": "request",
                    "question": (
                        "未解釈の要件 "
                        f"{residual[:80]!r} があります。対応する制御文法で明記してください。"
                    ),
                }
            ]
        )


def _source_units(units: list[str]) -> str:
    distinct = set(units)
    if len(distinct) != 1:
        raise OrderClarificationRequired(
            [
                {
                    "id": "mixed_units",
                    "field": "source.input_units",
                    "question": "初版では注文内の寸法単位をmmまたはinchのどちらか一方に統一してください。",
                }
            ]
        )
    return distinct.pop()


def parse_request(request: str) -> dict[str, Any]:
    """Parse a known plate order without a model call or guessed dimensions."""
    if not isinstance(request, str) or not request.strip():
        raise OrderClarificationRequired(
            [
                {
                    "id": "missing_request",
                    "field": "request",
                    "question": "作成する単一部品の寸法と形状を指定してください。",
                }
            ]
        )
    text = _normalize(request)
    questions = _detect_unsupported(text)
    if questions:
        raise OrderClarificationRequired(questions)
    if looks_like_controlled_request(text):
        return parse_controlled_request(request)
    lowered = text.lower()
    if not any(term in lowered for term in ("板", "プレート", "plate")):
        raise UnrecognizedOrderGrammar(
            [
                {
                    "id": "unrecognized_deterministic_grammar",
                    "field": "request",
                    "question": (
                        "対応する制御文法で単一部品の形状と全寸法を"
                        "明記してください。"
                    ),
                }
            ]
        )

    width, width_unit = _required_measure(
        text,
        (
            rf"(?:幅|width)\s*(?:は|=|:)?\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
        ),
        "base.width_mm",
        "幅",
        questions,
    )
    depth, depth_unit = _required_measure(
        text,
        (
            rf"(?:奥行き?|depth)\s*(?:は|=|:)?\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
        ),
        "base.depth_mm",
        "奥行",
        questions,
    )
    height, height_unit = _required_measure(
        text,
        (
            rf"(?:厚さ|板厚|thickness)\s*(?:は|=|:)?\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
        ),
        "base.height_mm",
        "厚さ",
        questions,
    )

    hole_words = bool(re.search(r"(?:穴|hole|bore)", text, flags=re.IGNORECASE))
    features: list[dict[str, Any]] = []
    feature_units: list[str] = []
    template_id = "plate_v1"
    if hole_words:
        through = bool(re.search(r"(?:貫通穴|through\s*holes?)", text, flags=re.IGNORECASE))
        blind = bool(re.search(r"(?:止まり穴|blind\s*holes?)", text, flags=re.IGNORECASE))
        if through == blind:
            questions.append(
                {
                    "id": "ambiguous_hole_end",
                    "field": "features[].extent.kind",
                    "question": "穴が貫通穴か止まり穴かを明記してください。",
                }
            )
        diameter, diameter_unit = _required_measure(
            text,
            (
                rf"(?:直径|diameter|dia\.?|φ|⌀)\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
                rf"(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})\s*(?:径|diameter)の?(?:貫通穴|止まり穴|holes?)",
            ),
            "features[].profile.diameter_mm",
            "穴径",
            questions,
        )
        if diameter_unit is not None:
            feature_units.append(diameter_unit)

        count_matches = [
            int(match.group("count"))
            for match in re.finditer(
                r"(?:貫通穴|止まり穴|holes?)[^。\n]{0,40}?(?P<count>[1-9]\d*)\s*(?:個|holes?)",
                text,
                flags=re.IGNORECASE,
            )
        ]
        if not count_matches:
            count_matches = [
                int(match.group("count"))
                for match in re.finditer(
                    r"(?P<count>[1-9]\d*)\s*(?:個の?)\s*(?:貫通穴|止まり穴)",
                    text,
                    flags=re.IGNORECASE,
                )
            ]
        distinct_counts = sorted(set(count_matches))
        if len(distinct_counts) != 1:
            questions.append(
                {
                    "id": "missing_or_ambiguous_hole_count",
                    "field": "features[].pattern.instances",
                    "question": "穴数を1つの明確な整数で指定してください。",
                }
            )
            hole_count = None
        else:
            hole_count = distinct_counts[0]

        offset, offset_unit = _required_measure(
            text,
            (
                rf"(?:左右|left\s+and\s+right(?:\s+by)?)\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
            ),
            "features[].profile.center_mm",
            "中央から左右への穴位置",
            questions,
        )
        if offset_unit is not None:
            feature_units.append(offset_unit)
        if not re.search(r"(?:中央線上|center\s*line)", text, flags=re.IGNORECASE):
            questions.append(
                {
                    "id": "missing_hole_centerline",
                    "field": "features[].profile.center_mm[1]",
                    "question": "穴の奥行方向位置（例: 板の中央線上）を指定してください。",
                }
            )
        if hole_count is not None and hole_count != 2:
            questions.append(
                {
                    "id": "count_position_mismatch",
                    "field": "features[].pattern.instances",
                    "question": "左右対称位置の指定は穴数2個と一致させてください。",
                }
            )

        extent: dict[str, Any] = {"kind": "through_all"}
        if blind:
            depth_value, blind_unit = _required_measure(
                text,
                (
                    rf"(?:深さ|depth)\s*(?:は|=|:)?\s*(?P<value>{_NUMBER})\s*(?P<unit>{_UNIT})",
                ),
                "features[].extent.distance_mm",
                "止まり穴深さ",
                questions,
            )
            if depth_value is not None:
                extent = {"kind": "distance", "distance_mm": depth_value}
            if blind_unit is not None:
                feature_units.append(blind_unit)

        if not questions and None not in (diameter, offset, hole_count):
            features.append(
                {
                    "id": "hole_pattern_001",
                    "operation": "cut_extrude",
                    "profile": {
                        "kind": "circle",
                        "purpose": "hole",
                        "center_mm": [0.0, 0.0],
                        "diameter_mm": diameter,
                    },
                    "extent": extent,
                    "pattern": {
                        "kind": "linear",
                        "instances": 2,
                        "spacing_mm": float(offset) * 2.0,
                        "direction_deg": 0.0,
                        "centered": True,
                    },
                }
            )
            template_id = "plate_symmetric_two_holes_v1"

    if questions:
        raise OrderClarificationRequired(questions)
    _reject_unconsumed_legacy_text(text)
    assert width is not None and depth is not None and height is not None
    units = _source_units(
        [
            unit
            for unit in (width_unit, depth_unit, height_unit, *feature_units)
            if unit is not None
        ]
    )
    request_hash = hashlib.sha256(request.encode("utf-8")).hexdigest()
    design_slug = "plate-two-holes" if features else "plate"
    spec = {
        "schema_version": "fast-mvp/design-spec/1.0",
        "design_id": f"{design_slug}-{request_hash[:12]}",
        "source": {
            "request_sha256": request_hash,
            "language": _language(text),
            "input_units": units,
        },
        "generation": {
            "route": "deterministic_template",
            "template_id": template_id,
        },
        "units": "mm",
        "base": {
            "kind": "box",
            "width_mm": width,
            "depth_mm": depth,
            "height_mm": height,
        },
        "features": features,
        "verification": {
            "dimension_tolerance_mm": 0.01,
            "volume_relative_tolerance": 0.000001,
            "expected_solid_count": 1,
        },
    }
    return validate_design_spec(spec)
