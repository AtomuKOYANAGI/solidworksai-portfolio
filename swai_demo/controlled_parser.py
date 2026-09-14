"""Strict controlled-natural-language parser for the bounded MVP surface."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable

from .errors import OrderClarificationRequired
from .schema import validate_design_spec


_SIGNED_NUMBER = r"-?(?:0|[1-9]\d*)(?:\.\d+)?"
_UNIT = r"(?:mm|millimeters?|millimetres?|インチ|inch(?:es)?|in\b|\")"


def _normal(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def looks_like_controlled_request(text: str) -> bool:
    normalized = _normal(text).lower()
    return any(
        normalized.startswith(prefix)
        for prefix in (
            "板 ",
            "プレート ",
            "plate ",
            "直方体 ",
            "box ",
            "rectangular block ",
            "円柱 ",
            "cylinder ",
            "円管 ",
            "tube ",
        )
    )


def _question(identifier: str, field: str, message: str) -> None:
    raise OrderClarificationRequired(
        [{"id": identifier, "field": field, "question": message}]
    )


def _unit_name(unit: str) -> str:
    lowered = unit.lower()
    return "inch" if lowered in {"inch", "inches", "in", '"'} or unit == "インチ" else "mm"


def _unit_factor(unit: str) -> float:
    return 25.4 if _unit_name(unit) == "inch" else 1.0


class _ClauseReader:
    def __init__(self, clause: str, clause_index: int, units: list[str]) -> None:
        self.clause = clause
        self.clause_index = clause_index
        self.units = units
        self.consumed: list[tuple[int, int]] = []

    @staticmethod
    def _labels(labels: Iterable[str]) -> str:
        return "(?:" + "|".join(labels) + ")"

    def quantity(
        self,
        labels: Iterable[str],
        field: str,
        label_ja: str,
        *,
        required: bool = True,
    ) -> float | None:
        pattern = (
            self._labels(labels)
            + rf"\s*(?:は|=|:)?\s*(?P<value>{_SIGNED_NUMBER})\s*(?P<unit>{_UNIT})"
        )
        matches = list(re.finditer(pattern, self.clause, flags=re.IGNORECASE))
        if not matches:
            if required:
                _question(
                    f"missing_{field}",
                    field,
                    f"句{self.clause_index}の{label_ja}を単位付きで指定してください。",
                )
            return None
        values = {
            round(float(match.group("value")) * _unit_factor(match.group("unit")), 12)
            for match in matches
        }
        if len(values) != 1:
            _question(
                f"ambiguous_{field}",
                field,
                f"句{self.clause_index}の{label_ja}は値を1つにしてください。",
            )
        match = matches[0]
        self.consumed.extend(item.span() for item in matches)
        self.units.extend(_unit_name(item.group("unit")) for item in matches)
        return values.pop()

    def point(
        self,
        labels: Iterable[str],
        field: str,
        label_ja: str,
    ) -> list[float]:
        pattern = (
            self._labels(labels)
            + rf"\s*(?:は|=|:)?\s*\(\s*"
            rf"(?P<x>{_SIGNED_NUMBER})\s*(?P<xunit>{_UNIT})\s*,\s*"
            rf"(?P<y>{_SIGNED_NUMBER})\s*(?P<yunit>{_UNIT})\s*\)"
        )
        matches = list(re.finditer(pattern, self.clause, flags=re.IGNORECASE))
        if len(matches) != 1:
            _question(
                f"missing_or_ambiguous_{field}",
                field,
                f"句{self.clause_index}の{label_ja}を(x unit, y unit)で1点指定してください。",
            )
        match = matches[0]
        self.consumed.append(match.span())
        x_unit = match.group("xunit")
        y_unit = match.group("yunit")
        self.units.extend((_unit_name(x_unit), _unit_name(y_unit)))
        return [
            float(match.group("x")) * _unit_factor(x_unit),
            float(match.group("y")) * _unit_factor(y_unit),
        ]

    def angle(
        self,
        labels: Iterable[str],
        field: str,
        label_ja: str,
    ) -> float:
        pattern = (
            self._labels(labels)
            + rf"\s*(?:は|=|:)?\s*(?P<value>{_SIGNED_NUMBER})\s*(?:deg(?:rees?)?|度)"
        )
        matches = list(re.finditer(pattern, self.clause, flags=re.IGNORECASE))
        if len(matches) != 1:
            _question(
                f"missing_or_ambiguous_{field}",
                field,
                f"句{self.clause_index}の{label_ja}をdegreeで1つ指定してください。",
            )
        self.consumed.append(matches[0].span())
        return float(matches[0].group("value"))

    def integer(
        self,
        labels: Iterable[str],
        field: str,
        label_ja: str,
    ) -> int:
        pattern = self._labels(labels) + r"\s*(?:は|=|:)?\s*(?P<value>[1-9]\d*)"
        matches = list(re.finditer(pattern, self.clause, flags=re.IGNORECASE))
        if len(matches) != 1:
            _question(
                f"missing_or_ambiguous_{field}",
                field,
                f"句{self.clause_index}の{label_ja}を正の整数で1つ指定してください。",
            )
        self.consumed.append(matches[0].span())
        return int(matches[0].group("value"))

    def reject_unconsumed_numbers(self) -> None:
        for match in re.finditer(_SIGNED_NUMBER, self.clause):
            if not any(start <= match.start() and match.end() <= end for start, end in self.consumed):
                _question(
                    "unconsumed_numeric_fact",
                    f"clauses[{self.clause_index}]",
                    f"句{self.clause_index}に未解釈の数値 {match.group(0)} があります。ラベルを明記してください。",
                )

    def reject_unconsumed_text(self, allowed_terms: Iterable[str]) -> None:
        masked = list(self.clause)
        for start, end in self.consumed:
            masked[start:end] = " " * (end - start)
        residual = "".join(masked)
        for term in sorted(set(allowed_terms), key=len, reverse=True):
            residual = re.sub(
                re.escape(term), " ", residual, flags=re.IGNORECASE
            )
        residual = re.sub(r"[\s,，、。.=:：()（）\[\]{}・]+", "", residual)
        if residual:
            _question(
                "unconsumed_text_requirement",
                f"clauses[{self.clause_index}]",
                (
                    f"句{self.clause_index}に未解釈の要件 "
                    f"{residual[:80]!r} があります。対応する制御文法で明記してください。"
                ),
            )


def _contains(clause: str, *terms: str) -> bool:
    lowered = clause.lower()
    return any(term.lower() in lowered for term in terms)


def _parse_base(clause: str, units: list[str]) -> dict[str, Any]:
    reader = _ClauseReader(clause, 1, units)
    if _contains(clause, "円管", "tube"):
        outer = reader.quantity(
            ("外径", "outer\\s+diameter"), "base.outer_diameter_mm", "外径"
        )
        inner = reader.quantity(
            ("内径", "inner\\s+diameter"),
            "base.inner_diameter_mm",
            "内径",
            required=False,
        )
        wall = reader.quantity(
            ("肉厚", "wall\\s+thickness"),
            "base.wall_thickness_mm",
            "肉厚",
            required=False,
        )
        if (inner is None) == (wall is None):
            _question(
                "tube_inner_or_wall",
                "base",
                "円管は内径または肉厚のどちらか一方だけを指定してください。",
            )
        assert outer is not None
        if wall is not None:
            inner = round(outer - 2.0 * wall, 12)
        height = reader.quantity(
            ("長さ", "高さ", "length", "height"), "base.height_mm", "長さ"
        )
        reader.reject_unconsumed_numbers()
        reader.reject_unconsumed_text(("円管", "tube"))
        return {
            "kind": "tube",
            "outer_diameter_mm": outer,
            "inner_diameter_mm": inner,
            "height_mm": height,
        }
    if _contains(clause, "円柱", "cylinder"):
        diameter = reader.quantity(
            ("直径", "diameter"), "base.diameter_mm", "直径"
        )
        height = reader.quantity(
            ("長さ", "高さ", "length", "height"), "base.height_mm", "高さ"
        )
        reader.reject_unconsumed_numbers()
        reader.reject_unconsumed_text(("円柱", "cylinder"))
        return {"kind": "cylinder", "diameter_mm": diameter, "height_mm": height}
    if not _contains(
        clause, "板", "プレート", "plate", "直方体", "box", "rectangular block"
    ):
        _question(
            "unknown_base",
            "base.kind",
            "先頭句は板・直方体・円柱・円管のいずれかにしてください。",
        )
    width = reader.quantity(("幅", "width"), "base.width_mm", "幅")
    depth = reader.quantity(("奥行き?", "depth"), "base.depth_mm", "奥行")
    height = reader.quantity(
        ("厚さ", "板厚", "高さ", "thickness", "height"),
        "base.height_mm",
        "厚さまたは高さ",
    )
    reader.reject_unconsumed_numbers()
    reader.reject_unconsumed_text(
        ("板", "プレート", "plate", "直方体", "box", "rectangular block")
    )
    return {"kind": "box", "width_mm": width, "depth_mm": depth, "height_mm": height}


def _pattern(
    reader: _ClauseReader,
    clause: str,
) -> dict[str, Any] | None:
    linear = _contains(clause, "直線パターン", "linear pattern")
    circular = _contains(clause, "円形パターン", "circular pattern")
    if linear and circular:
        _question(
            "ambiguous_pattern",
            f"clauses[{reader.clause_index}].pattern",
            "1つのフィーチャー句にパターン種別は1つだけ指定してください。",
        )
    if linear:
        count = reader.integer(("個数", "count"), "pattern.instances", "個数")
        spacing = reader.quantity(("間隔", "spacing"), "pattern.spacing_mm", "間隔")
        direction = reader.angle(("方向", "direction"), "pattern.direction_deg", "方向")
        centered = _contains(clause, "中心配置", "centered")
        forward = _contains(clause, "前方配置", "forward")
        if centered == forward:
            _question(
                "linear_pattern_placement",
                "pattern.centered",
                "直線パターンは中心配置または前方配置のどちらか一方を指定してください。",
            )
        return {
            "kind": "linear",
            "instances": count,
            "spacing_mm": spacing,
            "direction_deg": direction,
            "centered": centered,
        }
    if circular:
        count = reader.integer(("個数", "count"), "pattern.instances", "個数")
        center = reader.point(
            ("パターン中心", "pattern\\s+center"),
            "pattern.center_mm",
            "パターン中心",
        )
        total_angle = reader.angle(
            ("全角度", "total\\s+angle"),
            "pattern.total_angle_deg",
            "全角度",
        )
        return {
            "kind": "circular",
            "instances": count,
            "center_mm": center,
            "total_angle_deg": total_angle,
        }
    return None


def _parse_modifier(
    clause: str, clause_index: int, units: list[str], feature_index: int
) -> dict[str, Any] | None:
    fillet = _contains(clause, "フィレット", "fillet")
    chamfer = _contains(clause, "面取り", "chamfer")
    if not fillet and not chamfer:
        return None
    if fillet and chamfer:
        _question("ambiguous_modifier", "features", "修飾句はフィレットか面取りの一方にしてください。")
    reader = _ClauseReader(clause, clause_index, units)
    size = reader.quantity(
        ("半径", "サイズ", "radius", "size"),
        "features[].size_mm",
        "サイズ",
    )
    vertical = _contains(clause, "垂直エッジ", "vertical edges")
    top = _contains(clause, "上面外周エッジ", "top outer edges")
    if vertical == top:
        _question(
            "modifier_selection",
            "features[].selection",
            "修飾対象は垂直エッジまたは上面外周エッジの一方を指定してください。",
        )
    reader.reject_unconsumed_numbers()
    operation = "fillet" if fillet else "chamfer"
    operation_terms = (
        ("フィレット", "fillet") if fillet else ("面取り", "chamfer")
    )
    selection_terms = (
        ("垂直エッジ", "vertical edges")
        if vertical
        else ("上面外周エッジ", "top outer edges")
    )
    reader.reject_unconsumed_text((*operation_terms, *selection_terms))
    return {
        "id": f"{operation}_{feature_index:03d}",
        "operation": operation,
        "selection": "vertical_edges" if vertical else "top_outer_edges",
        "size_mm": size,
    }


def _parse_feature(
    clause: str, clause_index: int, units: list[str], feature_index: int
) -> dict[str, Any]:
    modifier = _parse_modifier(clause, clause_index, units, feature_index)
    if modifier is not None:
        return modifier
    reader = _ClauseReader(clause, clause_index, units)
    pattern = _pattern(reader, clause)
    center = reader.point(
        ("基準中心", "seed\\s+center"),
        "features[].profile.center_mm",
        "基準中心",
    )

    through_hole = _contains(clause, "貫通穴", "through hole")
    blind_hole = _contains(clause, "止まり穴", "blind hole")
    through_slot = _contains(clause, "貫通長穴", "through slot")
    blind_slot = _contains(clause, "止まり長穴", "blind slot")
    add_extrude = _contains(clause, "加算押し出し", "add extrude")
    through_cut = _contains(clause, "貫通切削", "through cut")
    blind_cut = _contains(clause, "止まり切削", "blind cut")
    operation_flags = [
        through_hole,
        blind_hole,
        through_slot,
        blind_slot,
        add_extrude,
        through_cut,
        blind_cut,
    ]
    if sum(bool(flag) for flag in operation_flags) != 1:
        _question(
            "unknown_or_ambiguous_feature_operation",
            f"clauses[{clause_index}].operation",
            f"句{clause_index}は対応するフィーチャー操作を1つだけ明記してください。",
        )

    if through_hole or blind_hole:
        diameter = reader.quantity(
            ("直径", "diameter"), "features[].profile.diameter_mm", "穴径"
        )
        profile = {
            "kind": "circle",
            "purpose": "hole",
            "center_mm": center,
            "diameter_mm": diameter,
        }
    elif through_slot or blind_slot:
        length = reader.quantity(
            ("全長", "overall\\s+length", "length"),
            "features[].profile.length_mm",
            "長穴全長",
        )
        width = reader.quantity(
            ("幅", "width"), "features[].profile.width_mm", "長穴幅"
        )
        angle = reader.angle(
            ("(?<!全)角度", "(?<!total\\s)angle"),
            "features[].profile.angle_deg",
            "長穴角度",
        )
        profile = {
            "kind": "slot",
            "purpose": "slot",
            "center_mm": center,
            "length_mm": length,
            "width_mm": width,
            "angle_deg": angle,
        }
    else:
        rectangle = _contains(clause, "長方形", "rectangle")
        circle = _contains(clause, " 円 ", " circle ") or clause.strip().endswith((" 円", " circle"))
        if rectangle == circle:
            _question(
                "generic_profile_kind",
                "features[].profile.kind",
                f"句{clause_index}の押し出し断面は長方形または円の一方を指定してください。",
            )
        if rectangle:
            width = reader.quantity(
                ("幅", "width"), "features[].profile.width_mm", "断面幅"
            )
            depth = reader.quantity(
                ("断面奥行", "profile\\s+depth"),
                "features[].profile.depth_mm",
                "断面奥行",
            )
            angle = reader.angle(
                ("(?<!全)角度", "(?<!total\\s)angle"),
                "features[].profile.angle_deg",
                "断面角度",
            )
            profile = {
                "kind": "rectangle",
                "purpose": "generic",
                "center_mm": center,
                "width_mm": width,
                "depth_mm": depth,
                "angle_deg": angle,
            }
        else:
            diameter = reader.quantity(
                ("直径", "diameter"),
                "features[].profile.diameter_mm",
                "円直径",
            )
            profile = {
                "kind": "circle",
                "purpose": "generic",
                "center_mm": center,
                "diameter_mm": diameter,
            }

    if through_hole or through_slot or through_cut:
        extent = {"kind": "through_all"}
        operation = "cut_extrude"
    elif blind_hole or blind_slot or blind_cut:
        distance = reader.quantity(
            ("深さ", "cut\\s+depth", "depth"),
            "features[].extent.distance_mm",
            "切削深さ",
        )
        extent = {"kind": "distance", "distance_mm": distance}
        operation = "cut_extrude"
    else:
        distance = reader.quantity(
            ("押出距離", "高さ", "extrusion\\s+distance", "height"),
            "features[].extent.distance_mm",
            "押出距離",
        )
        extent = {"kind": "distance", "distance_mm": distance}
        operation = "add_extrude"

    reader.reject_unconsumed_numbers()
    if through_hole:
        operation_terms = ("貫通穴", "through hole")
    elif blind_hole:
        operation_terms = ("止まり穴", "blind hole")
    elif through_slot:
        operation_terms = ("貫通長穴", "through slot")
    elif blind_slot:
        operation_terms = ("止まり長穴", "blind slot")
    elif add_extrude:
        operation_terms = ("加算押し出し", "add extrude")
    elif through_cut:
        operation_terms = ("貫通切削", "through cut")
    else:
        operation_terms = ("止まり切削", "blind cut")
    profile_terms: tuple[str, ...] = ()
    if not (through_hole or blind_hole or through_slot or blind_slot):
        profile_terms = (
            ("長方形", "rectangle")
            if profile["kind"] == "rectangle"
            else ("円", "circle")
        )
    pattern_terms: tuple[str, ...] = ()
    if pattern is not None and pattern["kind"] == "linear":
        placement_terms = (
            ("中心配置", "centered")
            if pattern["centered"]
            else ("前方配置", "forward")
        )
        pattern_terms = (
            "直線パターン",
            "linear pattern",
            *placement_terms,
        )
    elif pattern is not None:
        pattern_terms = ("円形パターン", "circular pattern")
    reader.reject_unconsumed_text(
        (*operation_terms, *profile_terms, *pattern_terms)
    )
    feature = {
        "id": f"feature_{feature_index:03d}",
        "operation": operation,
        "profile": profile,
        "extent": extent,
    }
    if pattern is not None:
        feature["pattern"] = pattern
    return feature


def parse_controlled_request(request: str) -> dict[str, Any]:
    text = _normal(request)
    clauses = [item.strip().rstrip("。. ") for item in re.split(r"[;；]", text) if item.strip()]
    if not clauses:
        _question("missing_request", "request", "注文文を指定してください。")
    units: list[str] = []
    base = _parse_base(clauses[0], units)
    features = [
        _parse_feature(clause, index, units, index - 1)
        for index, clause in enumerate(clauses[1:], start=2)
    ]
    distinct_units = set(units)
    if len(distinct_units) != 1:
        _question(
            "mixed_or_missing_units",
            "source.input_units",
            "注文内の全寸法をmmまたはinchのどちらか一方に統一してください。",
        )
    request_hash = hashlib.sha256(request.encode("utf-8")).hexdigest()
    language = "ja" if re.search(r"[ぁ-んァ-ヶ一-龠]", text) else "en"
    spec = {
        "schema_version": "fast-mvp/design-spec/1.0",
        "design_id": f"{base['kind']}-{request_hash[:12]}",
        "source": {
            "request_sha256": request_hash,
            "language": language,
            "input_units": distinct_units.pop(),
        },
        "generation": {
            "route": "deterministic_template",
            "template_id": "controlled_natural_language_v1",
        },
        "units": "mm",
        "base": base,
        "features": features,
        "verification": {
            "dimension_tolerance_mm": 0.01,
            "volume_relative_tolerance": 0.000001,
            "expected_solid_count": 1,
        },
    }
    return validate_design_spec(spec)
