"""Portfolio adapter: validate a bounded plate request and report expectations.

The parser and schema modules are copied from the original project. This
adapter is new for the portfolio; it does not generate or inspect CAD files.
"""

from __future__ import annotations

from typing import Any

from .errors import DesignSpecError, OrderClarificationRequired
from .parser import parse_request
from .spec_tools import expected_geometry


def evaluate_request(request: str) -> dict[str, Any]:
    """Return a JSON-compatible result without a model call or CAD execution."""
    context = {
        "validation_scope": "design_spec_only",
        "external_llm_used": False,
        "cad_executed": False,
    }
    try:
        spec = parse_request(request)
        # Keep the public demo small and its analytic volume assumptions clear.
        # The unchanged source modules also contain other, unexposed features.
        if spec["generation"]["template_id"] not in {
            "plate_v1", "plate_symmetric_two_holes_v1"
        }:
            raise OrderClarificationRequired([{
                "id": "outside_portfolio_scope",
                "field": "scope",
                "question": "公開デモでは寸法付きの矩形板と左右対称の2穴を扱います。examplesの書式で指定してください。",
            }])
        return {
            "status": "validated",
            **context,
            "design_spec": spec,
            "analytic_expectations": expected_geometry(spec),
        }
    except OrderClarificationRequired as error:
        return {
            "status": "clarification_required",
            **context,
            "questions": error.questions,
        }
    except DesignSpecError as error:
        return {
            "status": "invalid_design_spec",
            **context,
            "message": str(error),
        }
