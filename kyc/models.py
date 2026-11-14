from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

DocType = Literal["pan", "id_card", "address_proof"]
TextValue = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


# Structural schemas: semantic/date errors are deliberately field-level review
# results rather than discarded Pydantic exceptions.
class PAN(StrictModel):
    name: TextValue
    father_name: TextValue
    dob: TextValue
    pan_number: TextValue


class IDCard(StrictModel):
    name: TextValue
    dob: TextValue
    gender: TextValue
    id_number: TextValue
    address: TextValue
    pincode: TextValue


class AddressProof(StrictModel):
    name: TextValue
    address: TextValue
    pincode: TextValue
    issue_date: TextValue


SCHEMAS = {"pan": PAN, "id_card": IDCard, "address_proof": AddressProof}


class ExtractRequest(StrictModel):
    doc_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]{1,100}$")]
    text: Annotated[str, StringConstraints(min_length=1, max_length=100000)]


class FieldResult(StrictModel):
    value: str | None
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)
    reason_codes: list[str] = Field(default_factory=list)


class ExtractResponse(StrictModel):
    doc_id: str
    doc_type: DocType | None
    status: Literal["ok", "needs_review", "failed"]
    fields: dict[str, FieldResult] = Field(default_factory=dict)
    error: str | None = None
