"""
CQR Orchestration API — main FastAPI application entrypoint.
All external traffic enters through this service.
"""
import logging
import logging.config
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .router import router
from .ws import ws_router

# ---------------------------------------------------------------------------
# Structured JSON logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}',
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="CQR Orchestration API",
    description="Secure runtime for coding agents — backend orchestration layer.",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO(AMBIGUITY): tighten to known frontend origin in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")
app.include_router(ws_router)


@app.on_event("startup")
async def startup_event() -> None:
    """Log service startup with port information."""
    port = os.getenv("ORCHESTRATION_PORT", "8000")
    logger.info("CQR Orchestration API starting on port %s", port)


@app.get("/health", tags=["health"])
async def health_check() -> dict:
    """Return service health status."""
    return {"status": "ok", "service": "orchestration"}
