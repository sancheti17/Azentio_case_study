"""Strict request models for applicant-level verification and resolution."""
from typing import Annotated, Literal
from datetime import date
from pydantic import Field, StringConstraints, field_validator
from .models import StrictModel

ApplicantId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]

class ApplicantDocument(StrictModel):
    doc_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]
    text: Annotated[str, StringConstraints(min_length=1, max_length=100000)]

class ApplicantVerifyRequest(StrictModel):
    as_of: Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
    documents: list[ApplicantDocument] = Field(min_length=3, max_length=3)

    @field_validator("as_of")
    @classmethod
    def valid_as_of(cls, value):
        date.fromisoformat(value)
        return value

class ReviewResolveRequest(StrictModel):
    reviewer: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    decision: Literal["AUTO_APPROVE", "REJECT", "APPROVE", "APPROVED", "REJECTED"]
    corrected_fields: dict[str, dict[str, str]] = Field(default_factory=dict)
