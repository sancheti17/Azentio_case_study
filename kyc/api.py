"""Run: python -m uvicorn kyc.api:app --host 127.0.0.1 --port 8000"""
import os
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from .audit import AuditStore
from .models import ExtractRequest, ExtractResponse
from .pipeline import Pipeline


def create_app(pipeline: Pipeline | None = None) -> FastAPI:
    app = FastAPI(title="SPECIMEN KYC extraction", version="0.1.0")
    app.state.pipeline = pipeline

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError):
        # FastAPI's usual error body echoes input values, potentially full IDs.
        return JSONResponse(status_code=422, content={"error":"REQUEST_INVALID"})

    @app.post("/v1/documents/extract", response_model=ExtractResponse, response_model_exclude_none=True)
    def extract_document(payload: ExtractRequest):
        service = app.state.pipeline
        if service is None:
            try:
                service = Pipeline(threshold=float(os.getenv("KYC_THRESHOLD", "0.8")),
                                   audit=AuditStore(os.getenv("KYC_AUDIT_DB", "kyc-audit.sqlite3")))
            except Exception:
                result = ExtractResponse(doc_id="unavailable", doc_type=None, status="failed", error="CONFIGURATION_ERROR")
                return JSONResponse(content=result.model_dump())
        result = service.extract(payload.doc_id, payload.text)
        return JSONResponse(content=result.model_dump(exclude={"error"} if result.error is None else set()))

    return app


app = create_app()
