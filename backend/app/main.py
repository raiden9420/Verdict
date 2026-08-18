"""
Verdict — FastAPI application entry point.

Wires up CORS, routers, and the lifespan hook that captures the
asyncio event loop for SSE streaming.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.config import FRONTEND_ORIGIN_REGEX, FRONTEND_ORIGINS, validate_config
from app.api.papers import router as papers_router
from app.api.audits import (
    recover_orphaned_audits,
    router as audits_router,
    set_event_loop,
    shutdown_audit_executor,
)
from app.services.readiness_service import (
    APP_VERSION,
    PRODUCT_PHASE,
    ReadinessError,
    verify_phase3_readiness,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan — runs once at startup and shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    validate_config()
    set_event_loop(asyncio.get_running_loop())
    try:
        await asyncio.to_thread(recover_orphaned_audits)
    except Exception as exc:
        # A transient database outage should not prevent the health endpoint
        # from starting; audit creation will still fail closed until DB recovers.
        logger.warning("Could not reconcile interrupted audits at startup: %s", exc)
    logger.info("Verdict backend started ✓")
    yield
    # Shutdown
    shutdown_audit_executor()
    logger.info("Verdict backend shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Verdict — Adversarial Research Audit API",
    version=APP_VERSION,
    description=(
        "Authenticated, multi-topic adversarial research audits with grounded "
        "round debriefs, final reports, exports, and revision comparisons."
    ),
    lifespan=lifespan,
)

# CORS — configured explicitly now that authenticated accounts are enabled.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(FRONTEND_ORIGINS),
    allow_origin_regex=FRONTEND_ORIGIN_REGEX,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(papers_router)
app.include_router(audits_router)


@app.get("/health")
async def health():
    """Cheap liveness probe; it deliberately does not contact providers."""
    return {"status": "ok", "version": APP_VERSION, "phase": PRODUCT_PHASE}


@app.get("/ready")
async def ready():
    """Deployment gate for the Phase 3 schema and private paper storage."""
    try:
        await asyncio.to_thread(verify_phase3_readiness)
    except ReadinessError as exc:
        logger.error("Phase 3 readiness failed (%s): %s", exc.component, exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "version": APP_VERSION,
                "phase": PRODUCT_PHASE,
                "component": exc.component,
                "detail": str(exc),
            },
        )
    return {"status": "ready", "version": APP_VERSION, "phase": PRODUCT_PHASE}
