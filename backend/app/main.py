"""
Verdict — FastAPI application entry point.

Wires up CORS, routers, and the lifespan hook that captures the
asyncio event loop for SSE streaming.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import validate_config
from app.api.papers import router as papers_router
from app.api.audits import (
    recover_orphaned_audits,
    router as audits_router,
    set_event_loop,
    shutdown_audit_executor,
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
    version="0.1.0",
    description="Phase 1: 3-agent debate, grounding validation, debrief cards.",
    lifespan=lifespan,
)

# CORS — allow the Next.js dev server and any deployed frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Phase 1: permissive; Phase 3 will lock down
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(papers_router)
app.include_router(audits_router)


@app.get("/health")
async def health():
    return {"status": "ok"}
