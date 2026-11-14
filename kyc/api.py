"""Run: python -m uvicorn kyc.api:app --host 127.0.0.1 --port 8000"""
import os
import re
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .audit import AuditStore
from .config import DEFAULT_REVIEW_THRESHOLD
from .models import ExtractRequest, ExtractResponse
from .pipeline import Pipeline
from .applicant_models import ApplicantVerifyRequest, ReviewResolveRequest
from .cross_checks import cross_document_reason_codes
from .review_store import ReviewStore


_ID_RE = re.compile(r"(?<![0-9])[0-9]{12}(?![0-9])")

def _mask_sensitive(text: str) -> str:
    return _ID_RE.sub(lambda m: "XXXXXXXX" + m.group(0)[-4:], str(text))


def create_app(pipeline: Pipeline | None = None, review_store: ReviewStore | None = None) -> FastAPI:
    app = FastAPI(title="SPECIMEN KYC extraction and applicant verification", version="0.2.0")
    app.state.pipeline = pipeline
    app.state.review_store = review_store

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # Do not return FastAPI's usual body, which can echo OCR/identity fields.
        return JSONResponse(status_code=422, content={"error":"REQUEST_INVALID"})

    def services():
        service = app.state.pipeline
        if service is None:
            service = Pipeline(threshold=float(os.getenv("KYC_THRESHOLD", str(DEFAULT_REVIEW_THRESHOLD))),
                               audit=AuditStore(os.getenv("KYC_AUDIT_DB", "kyc-audit.sqlite3")))
            app.state.pipeline = service
        store = app.state.review_store
        if store is None:
            db_path = getattr(getattr(service, "audit", None), "path", os.getenv("KYC_AUDIT_DB", "kyc-audit.sqlite3"))
            store = ReviewStore(db_path, os.getenv("KYC_CORRECTIONS_PATH", "data/corrections.jsonl"))
            app.state.review_store = store
        return service, store

    @app.post("/v1/documents/extract", response_model=ExtractResponse, response_model_exclude_none=True)
    def extract_document(payload: ExtractRequest):
        service, _ = services()
        result = service.extract(payload.doc_id, payload.text)
        return JSONResponse(content=result.model_dump(exclude={"error"} if result.error is None else set()))

    @app.post("/v1/applicants/{applicant_id}/verify")
    def verify_applicant(applicant_id: str, payload: ApplicantVerifyRequest):
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", applicant_id):
            raise HTTPException(status_code=422, detail={"error":"APPLICANT_ID_INVALID"})
        if len({d.doc_id for d in payload.documents}) != 3:
            raise HTTPException(status_code=422, detail={"error":"DOCUMENT_IDS_MUST_BE_UNIQUE"})
        service, store = services()
        results = [service.extract(d.doc_id, d.text) for d in payload.documents]
        by_type = {}
        reason_codes = set()
        low_fields = []
        doc_meta = []
        for request_doc, result in zip(payload.documents, results):
            kind = result.doc_type
            doc_meta.append({"doc_id":request_doc.doc_id, "doc_type":kind})
            if kind in {"pan", "id_card", "address_proof"} and kind not in by_type:
                by_type[kind] = {name: field.value for name, field in result.fields.items()}
            if result.status == "failed":
                reason_codes.add("DOCUMENT_EXTRACTION_FAILED")
                if result.error:
                    reason_codes.add(result.error)
            for field_name, field in result.fields.items():
                reason_codes.update(field.reason_codes)
                if field.confidence < service.threshold or field.reason_codes:
                    low_fields.append({"doc_id":request_doc.doc_id, "doc_type":kind,
                                       "field":field_name, "confidence":field.confidence,
                                       "reason_codes":sorted(set(field.reason_codes))})
        if set(by_type) != {"pan", "id_card", "address_proof"}:
            reason_codes.add("DOCUMENT_SET_INVALID")
        reason_codes.update(cross_document_reason_codes(by_type, payload.as_of))
        has_rule_or_uncertainty = any(
            r.status != "ok" or any(f.confidence < service.threshold or f.reason_codes for f in r.fields.values())
            for r in results
        )
        # Automated cross-document signals are review triggers, never proof of fraud.
        # REJECT is reserved for an authorized human reviewer after adjudication.
        decision = "REVIEW" if (reason_codes or has_rule_or_uncertainty) else "AUTO_APPROVE"
        safe_applicant = _mask_sensitive(applicant_id)
        review_id = store.record_decision(safe_applicant, decision, sorted(reason_codes), doc_meta, low_fields)
        body = {"applicant_id":safe_applicant, "decision":decision,
                "reason_codes":sorted(reason_codes),
                "documents":[r.model_dump(exclude_none=True) for r in results]}
        if review_id is not None:
            body["review_item_id"] = review_id
        return JSONResponse(content=body)

    @app.get("/v1/review-items")
    def list_review_items(status: str = "open"):
        if status not in {"open", "resolved", "all"}:
            raise HTTPException(status_code=422, detail={"error":"REVIEW_STATUS_INVALID"})
        _, store = services()
        return {"items":store.list_items(status)}

    @app.post("/v1/review-items/{review_id}/resolve")
    def resolve_review_item(review_id: int, payload: ReviewResolveRequest):
        _, store = services()
        decision = payload.decision
        if decision in {"APPROVE", "APPROVED"}:
            decision = "AUTO_APPROVE"
        elif decision == "REJECTED":
            decision = "REJECT"
        try:
            status, result = store.resolve(review_id, payload.reviewer, decision, payload.corrected_fields)
        except Exception:
            # Keep database/path error details and request corrections out of responses/logs.
            raise HTTPException(status_code=503, detail={"error":"REVIEW_RESOLUTION_UNAVAILABLE"}) from None
        if status == "not_found":
            raise HTTPException(status_code=404, detail={"error":"REVIEW_ITEM_NOT_FOUND"})
        if status == "already_resolved":
            raise HTTPException(status_code=409, detail={"error":"REVIEW_ITEM_ALREADY_RESOLVED"})
        if status == "invalid_correction":
            raise HTTPException(status_code=422, detail={"error":"CORRECTION_FIELDS_INVALID"})
        return result

    return app


app = create_app()
