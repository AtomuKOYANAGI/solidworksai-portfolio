"""Bounded error types returned by the fast MVP."""

from __future__ import annotations


class FastMvpError(RuntimeError):
    """Base class for expected, user-safe MVP failures."""

    failure_class = "fast_mvp_error"


class DesignSpecError(FastMvpError, ValueError):
    """A DesignSpec failed schema or semantic validation."""

    failure_class = "invalid_design_spec"


class OrderClarificationRequired(FastMvpError, ValueError):
    """The request is incomplete or ambiguous and must not be generated."""

    failure_class = "clarification_required"

    def __init__(self, questions: list[dict[str, str]]) -> None:
        if not questions:
            raise ValueError("at least one clarification question is required")
        self.questions = questions
        super().__init__("; ".join(item["question"] for item in questions))


class UnrecognizedOrderGrammar(OrderClarificationRequired):
    """Only this parse outcome may use an explicitly selected provider."""


class TemplateGenerationError(FastMvpError):
    """A fixed build123d template could not create its STEP output."""

    failure_class = "template_generation_failed"


class GeometryValidationError(FastMvpError):
    """Generated geometry did not satisfy the normalized DesignSpec."""

    failure_class = "geometry_validation_failed"


class DesignSpecProviderError(FastMvpError):
    """An optional data-only provider failed without yielding valid JSON."""

    failure_class = "design_spec_provider_failed"
