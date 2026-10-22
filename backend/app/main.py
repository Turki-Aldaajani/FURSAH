from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace

from . import routes
from .infrastructure.config import get_settings
from .infrastructure.errors import ErrorCode
from .infrastructure.observability import setup_observability
from .infrastructure.tracing import Tracer
from .schemas import ApiErrorDetail, ApiErrorResponse

settings = get_settings()

# Day 4: set up tracing once, before the first request (no-op unless FURSAH_PHOENIX_ENABLED=true).
# Skipped when a tracer provider already exists: evals/run_eval.py sets one up before importing the app.
if isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider):
    setup_observability()

app = FastAPI(
    title="FURSAH API",
    version="1.0.0-starter",
    description="Applied AI Engineering Bootcamp starter backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes.router)


@app.exception_handler(RequestValidationError)
async def request_validation_handler(request: Request, exc: RequestValidationError):
    request_id = (request.headers.get("x-request-id") or "").strip()[:120] or str(uuid4())
    details: list[ApiErrorDetail] = []

    for item in exc.errors():
        loc = [str(part) for part in item.get("loc", []) if part != "body"]
        details.append(
            ApiErrorDetail(
                field=".".join(loc) or None,
                message=item.get("msg", "Invalid value."),
            )
        )

    payload = ApiErrorResponse(
        request_id=request_id,
        error_code=ErrorCode.REQUEST_VALIDATION,
        message="The request body is invalid.",
        details=details,
    )
    # Rejected before the route runs, so it is traced here. Field paths only, never the values.
    Tracer(settings.trace_jsonl_path).emit(
        request_id=request_id, step="request_failed", component="api", outcome=ErrorCode.REQUEST_VALIDATION,
        attributes={"n_errors": len(details), "invalid_fields": [d.field for d in details]},
    )
    return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    request_id = (
        getattr(request.state, "request_id", None)
        or (request.headers.get("x-request-id") or "").strip()[:120]
        or str(uuid4())
    )
    payload = ApiErrorResponse(
        request_id=request_id,
        error_code=ErrorCode.INTERNAL,
        message="An unexpected backend error occurred.",
        details=[],
    )
    return JSONResponse(status_code=500, content=payload.model_dump(mode="json"))
