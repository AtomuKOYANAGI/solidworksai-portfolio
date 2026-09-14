"""Portfolio regression tests, added with AI assistance during extraction.

Tests cover the exposed plate demo and its relationship to archived evidence.
They do not launch CAD, call model APIs, or certify general CAD capabilities.
"""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import unittest

from swai_demo import evaluate_request
from swai_demo.errors import DesignSpecError
from swai_demo.parser import parse_request
from swai_demo.schema import validate_design_spec


ROOT = Path(__file__).resolve().parents[1]
REQUEST = (ROOT / "examples/plate_ja.txt").read_text(encoding="utf-8")


class PlateDemoTests(unittest.TestCase):
    def test_japanese_request_matches_archived_spec(self):
        expected = json.loads((ROOT / "evidence/plate/design_spec.json").read_text())
        result = evaluate_request(REQUEST)
        self.assertEqual(result["status"], "validated")
        self.assertEqual(result["design_spec"], expected)
        self.assertFalse(result["cad_executed"])
        self.assertFalse(result["external_llm_used"])

    def test_analytic_geometry_matches_independent_formula(self):
        result = evaluate_request(REQUEST)["analytic_expectations"]
        self.assertEqual(result["bounding_box_mm"]["size"], [100.0, 60.0, 10.0])
        self.assertEqual([h["center_mm"] for h in result["holes"]], [[-30.0, 0.0], [30.0, 0.0]])
        self.assertEqual([h["diameter_mm"] for h in result["holes"]], [8.0, 8.0])
        self.assertAlmostEqual(result["expected_volume_mm3"], 100 * 60 * 10 - 2 * math.pi * 4**2 * 10)

    def test_inch_values_are_normalized_to_mm(self):
        request = (ROOT / "examples/plate_en_inches.txt").read_text()
        spec = evaluate_request(request)["design_spec"]
        self.assertEqual(spec["source"]["input_units"], "inch")
        self.assertAlmostEqual(spec["base"]["width_mm"], 101.6)
        self.assertAlmostEqual(spec["base"]["height_mm"], 12.7)
        self.assertAlmostEqual(spec["features"][0]["profile"]["diameter_mm"], 6.35)

    def test_missing_hole_details_return_questions(self):
        request = (ROOT / "examples/missing_dimensions_ja.txt").read_text()
        result = evaluate_request(request)
        self.assertEqual(result["status"], "clarification_required")
        fields = {q["field"] for q in result["questions"]}
        self.assertIn("features[].extent.kind", fields)
        self.assertIn("features[].pattern.instances", fields)
        self.assertNotIn("design_spec", result)
        self.assertNotIn("analytic_expectations", result)

    def test_conflicting_dimensions_are_not_guessed(self):
        result = evaluate_request(REQUEST + "、幅90 mm")
        self.assertEqual(result["status"], "clarification_required")
        self.assertIn("base.width_mm", {q["field"] for q in result["questions"]})

    def test_mixed_units_are_rejected(self):
        result = evaluate_request(REQUEST.replace("幅100 mm", "幅4 inch"))
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["questions"][0]["id"], "mixed_units")

    def test_nonpositive_dimension_is_rejected(self):
        result = evaluate_request(REQUEST.replace("幅100 mm", "幅0 mm"))
        self.assertEqual(result["status"], "invalid_design_spec")

    def test_holes_outside_plate_are_rejected(self):
        result = evaluate_request(REQUEST.replace("左右30 mm", "左右49 mm"))
        self.assertEqual(result["status"], "invalid_design_spec")
        self.assertIn("inside the base", result["message"])

    def test_touching_holes_are_rejected(self):
        result = evaluate_request(REQUEST.replace("左右30 mm", "左右4 mm"))
        self.assertEqual(result["status"], "invalid_design_spec")
        self.assertIn("overlap or touch", result["message"])

    def test_valid_blind_holes_use_given_depth(self):
        request = REQUEST.replace("貫通穴", "止まり穴") + "、深さ4 mm"
        result = evaluate_request(request)
        self.assertEqual(result["status"], "validated")
        self.assertAlmostEqual(result["analytic_expectations"]["expected_volume_mm3"], 100 * 60 * 10 - 2 * math.pi * 4**2 * 4)

    def test_blind_depth_equal_to_plate_thickness_is_rejected(self):
        request = REQUEST.replace("貫通穴", "止まり穴") + "、深さ10 mm"
        result = evaluate_request(request)
        self.assertEqual(result["status"], "invalid_design_spec")
        self.assertIn("blind cut depth", result["message"])

    def test_unconsumed_requirement_is_not_silently_dropped(self):
        result = evaluate_request(REQUEST + "、赤色にして")
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["questions"][0]["id"], "unconsumed_text_requirement")

    def test_assembly_requirement_is_rejected(self):
        result = evaluate_request(REQUEST + "、アセンブリにして")
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["questions"][0]["id"], "unsupported_assembly")

    def test_other_source_capabilities_are_outside_public_demo(self):
        result = evaluate_request("円柱 直径20 mm 高さ40 mm")
        self.assertEqual(result["status"], "clarification_required")
        self.assertEqual(result["questions"][0]["id"], "outside_portfolio_scope")

    def test_schema_rejects_extra_code_field(self):
        spec = parse_request(REQUEST)
        spec["untrusted_code"] = "print('example of an unwanted field')"
        with self.assertRaises(DesignSpecError):
            validate_design_spec(spec)

    def test_validator_does_not_mutate_input(self):
        spec = parse_request(REQUEST)
        original = deepcopy(spec)
        detached = validate_design_spec(spec)
        detached["base"]["width_mm"] = 1
        self.assertEqual(spec, original)

    def test_cli_success_outputs_json(self):
        result = subprocess.run([sys.executable, "-m", "swai_demo", "--request-file", "examples/plate_ja.txt"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "validated")

    def test_cli_clarification_is_nonzero_and_json(self):
        result = subprocess.run([sys.executable, "-m", "swai_demo", "--request-file", "examples/missing_dimensions_ja.txt"], cwd=ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "clarification_required")

    def test_archived_cad_measurements_agree_with_current_expectations(self):
        archive = json.loads((ROOT / "evidence/plate/step_validation.json").read_text())
        expected = evaluate_request(REQUEST)["analytic_expectations"]
        self.assertEqual(archive["status"], "passed")
        self.assertTrue(archive["checks"])
        self.assertTrue(all(c["status"] == "passed" for c in archive["checks"]))
        self.assertEqual(archive["measured"]["bounding_box_mm"]["size"], expected["bounding_box_mm"]["size"])
        self.assertAlmostEqual(archive["measured"]["volume_mm3"], expected["expected_volume_mm3"], delta=expected["expected_volume_mm3"] * 1e-6)
        measured_holes = sorted(archive["measured"]["cylindrical_faces"], key=lambda h: h["axis_position_mm"][0])
        self.assertEqual(len(measured_holes), 2)
        for measured, required in zip(measured_holes, expected["holes"]):
            self.assertEqual(measured["axis_position_mm"][:2], required["center_mm"])
            self.assertEqual(measured["diameter_mm"], required["diameter_mm"])
        sw = json.loads((ROOT / "evidence/plate/solidworks_summary.json").read_text())
        self.assertEqual(sw["after_sldprt_reopen"]["solid_count"], 1)
        self.assertAlmostEqual(sw["after_sldprt_reopen"]["volume_mm3"], expected["expected_volume_mm3"], delta=expected["expected_volume_mm3"] * 1e-6)

    def test_extracted_files_match_source_hashes(self):
        manifest = json.loads((ROOT / "docs/provenance.json").read_text())
        for entry in manifest["copied_files"]:
            with self.subTest(path=entry["path"]):
                data = (ROOT / entry["path"]).read_bytes()
                self.assertEqual(hashlib.sha256(data).hexdigest(), entry["sha256"])


if __name__ == "__main__":
    unittest.main()
