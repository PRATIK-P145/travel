"""Travel-Genie FastAPI application. Run with: uvicorn app:app --reload"""
import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import settings
from models import PlanResponse, TripRequest
from services.planner import build_plan

BASE_DIR = Path(__file__).parent
log = logging.getLogger("travel_genie")

app = FastAPI(title="Travel-Genie", version="1.0.0")

FIELD_LABELS = {
    "start_city": "Starting city",
    "destination": "Destination",
    "departure_date": "Departure date",
    "return_date": "Return date",
    "duration_days": "Trip duration",
    "travelers": "Number of travelers",
    "budget": "Total budget",
    "currency": "Currency",
    "travel_style": "Travel style",
    "interests": "Interests",
    "accommodation": "Accommodation preference",
    "notes": "Additional preferences",
}


def _error(status: int, code: str, message: str, details: list | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message, "details": details or []}})


def _friendly(err: dict) -> str:
    kind, ctx, msg = err.get("type", ""), err.get("ctx") or {}, err.get("msg", "Invalid value")
    if kind == "missing":
        return "is required."
    if kind == "string_too_short":
        return "is required." if ctx.get("min_length", 0) <= 2 else "is too short."
    if kind == "string_too_long":
        return f"must be at most {ctx.get('max_length')} characters."
    if kind in ("date_parsing", "date_from_datetime_parsing", "date_type"):
        return "must be a valid date (YYYY-MM-DD)."
    if kind in ("int_parsing", "float_parsing", "int_from_float"):
        return "must be a whole number." if "int" in kind else "must be a number."
    if kind == "greater_than":
        return f"must be greater than {ctx.get('gt'):g}."
    if kind == "greater_than_equal":
        return f"must be at least {ctx.get('ge'):g}."
    if kind == "less_than_equal":
        return f"must be at most {ctx.get('le'):g}."
    if kind == "literal_error":
        return "has an unsupported value."
    return msg.removeprefix("Value error, ")


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    details = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", []) if p != "body"]
        field = loc[0] if loc else ""
        label = FIELD_LABELS.get(field, "")
        text = _friendly(err)
        message = f"{label} {text}" if label and text[:1].islower() else (f"{label}: {text}" if label else text)
        details.append({"field": field, "message": message})
    summary = "Please fix the highlighted details: " + " ".join(d["message"] for d in details) if details else "Invalid request."
    return _error(422, "validation_error", summary, details)


@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException):
    return _error(exc.status_code, "http_error", str(exc.detail))


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception):
    log.exception("Unhandled error")
    return _error(500, "internal_error", "Something went wrong while planning your trip. Please try again.")


@app.get("/api/health")
async def health():
    gemini_ok, serp_ok = bool(settings.gemini_key()), bool(settings.serpapi_key())
    return {
        "status": "ok",
        "mode": "live" if gemini_ok else "demo",
        "integrations": {
            "gemini": {"configured": gemini_ok, "model": settings.gemini_model()},
            "serpapi": {"configured": serp_ok},
        },
    }


@app.post("/api/plan", response_model=PlanResponse)
async def plan(trip: TripRequest):
    return await build_plan(trip)


app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")
