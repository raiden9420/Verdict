"""Isolated, loopback-only UI test double. Never imports Verdict's real API or config.

All papers, users and model results here are fictional and live only in memory.
Run through `npm run preview` in frontend/. This is not an alternate auth mode
for the application and must never be used as a deployed backend.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import json
import time
import uuid
from pathlib import Path

import fitz
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
import uvicorn

FIXTURE = json.loads((Path(__file__).resolve().parents[1] / "frontend/src/data/example-review.json").read_text())
USER = {"id": "90000000-0000-4000-8000-000000000001", "aud": "authenticated", "role": "authenticated", "email": "preview@example.test", "email_confirmed_at": "2026-09-08T00:00:00Z", "app_metadata": {"provider": "email"}, "user_metadata": {}, "created_at": "2026-09-08T00:00:00Z"}
app = FastAPI(title="Verdict LOCAL TEST API — synthetic data only")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"], allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["*"])
PAPERS = [
    {"id": FIXTURE["audit"]["paperId"], "filename": "[Example] Spaced retrieval and delayed recall.pdf", "page_count": 3, "detected_domain": "social_science", "parent_paper_id": None, "version_number": 1, "uploaded_at": "2026-09-08T10:00:00Z"},
    {"id": "10000000-0000-4000-8000-000000000002", "filename": "[Example] Prepared methods note.pdf", "page_count": 3, "detected_domain": "social_science", "parent_paper_id": None, "version_number": 1, "uploaded_at": "2026-09-08T11:00:00Z"},
]
AUDITS: dict[str, dict] = {}
STATES: dict[str, dict] = {FIXTURE["audit"]["auditId"]: copy.deepcopy(FIXTURE["stream"])}
TASKS: set[asyncio.Task] = set()


def summary(audit_id, paper_id, topics, status="completed", **options):
    paper = next(p for p in PAPERS if p["id"] == paper_id)
    return {"audit_id": audit_id, "paper_id": paper_id, "filename": paper["filename"], "status": status, "round_topic": topics[0], "round_topics": topics, "strictness_level": options.get("strictness_level", "standard"), "depth": options.get("depth", "fast"), "mode": options.get("mode", "author"), "domain": "social_science", "created_at": "2026-09-09T10:00:00Z"}


AUDITS[FIXTURE["audit"]["auditId"]] = summary(FIXTURE["audit"]["auditId"], PAPERS[0]["id"], ["experimental_setup"])


def session():
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")
    token = f'{encode({"alg": "HS256", "typ": "JWT"})}.{encode({"sub": USER["id"], "role": "authenticated", "aud": "authenticated", "exp": int(time.time()) + 3600})}.local-preview-signature'
    return {"access_token": token, "refresh_token": "local-preview-refresh", "token_type": "bearer", "expires_in": 3600, "expires_at": int(time.time()) + 3600, "user": USER}


@app.post("/auth/v1/token")
async def token(request: Request):
    body = await request.json()
    if request.query_params.get("grant_type") != "refresh_token" and (body.get("email") != USER["email"] or body.get("password") != "preview-only"):
        raise HTTPException(400, detail="Use preview@example.test and preview-only in this local test workspace.")
    return session()


@app.get("/auth/v1/user")
@app.put("/auth/v1/user")
async def user(): return USER


@app.post("/auth/v1/recover")
@app.post("/auth/v1/logout")
async def no_op(): return {}


@app.post("/auth/v1/signup")
async def signup(): return {"user": USER, "session": None}


@app.get("/papers")
async def papers(): return list(reversed(PAPERS))


@app.get("/audits")
async def audits(): return list(reversed(list(AUDITS.values())))


@app.post("/papers")
async def upload(file: UploadFile, force: bool = False, parent_paper_id: str | None = None):
    raw = await file.read()
    if not raw or not raw.startswith(b"%PDF"):
        raise HTTPException(400, detail="This file could not be read as a PDF.")
    if len(raw) > 20 * 1024 * 1024: raise HTTPException(400, detail="PDF exceeds 20 MB.")
    if "outage" in (file.filename or "").lower(): raise HTTPException(503, detail="Document preparation is temporarily unavailable. Please retry shortly.")
    if "nonresearch" in (file.filename or "").lower() and not force:
        raise HTTPException(400, detail={"relevance_failed": True, "message": "This document may not be a research paper.", "reason": "This is a simulated classification warning for local testing."})
    await asyncio.sleep(.5)
    paper_id = str(uuid.uuid4())
    parent = next((p for p in PAPERS if p["id"] == parent_paper_id), None)
    root = (parent.get("parent_paper_id") or parent["id"]) if parent else None
    version = max((p["version_number"] for p in PAPERS if root and (p["id"] == root or p.get("parent_paper_id") == root)), default=0) + 1
    paper = {"id": paper_id, "filename": file.filename, "page_count": 3, "detected_domain": "social_science", "parent_paper_id": root, "version_number": version, "uploaded_at": "2026-09-09T11:00:00Z"}
    PAPERS.append(paper)
    return {"paper_id": paper_id, "chunk_count": 3, **{key: value for key, value in paper.items() if key != "id"}}


@app.get("/papers/{paper_id}/pdf-url")
async def pdf_url(paper_id: str):
    if not any(p["id"] == paper_id for p in PAPERS): raise HTTPException(404)
    return {"url": f"http://127.0.0.1:8765/sample.pdf?issued={int(time.time())}", "expires_in": 60}


@app.get("/sample.pdf")
async def sample_pdf():
    document = fitz.open()
    for item in FIXTURE["source"]["pages"]:
        page = document.new_page()
        page.insert_text((50, 45), "VERDICT / LOCAL TEST MANUSCRIPT", fontsize=9, color=(.2,.35,.25))
        remaining = page.insert_textbox(fitz.Rect(50,80,545,760), item["title"] + "\n\n" + item["body"], fontsize=11, fontname="helv", lineheight=1.5)
        if remaining < 0: raise RuntimeError("Synthetic PDF fixture overflowed")
        page.insert_text((290,800), str(item["page"]), fontsize=10)
    data = document.tobytes()
    document.close()
    return Response(data, media_type="application/pdf")


async def run_review(audit_id, topics, round_ids):
    state = STATES[audit_id]
    for number,(topic,round_id) in enumerate(zip(topics,round_ids),1):
        scope = {"round_id": round_id, "round_number": number, "round_topic": topic}
        for turn in FIXTURE["stream"]["turns"]:
            await asyncio.sleep(.3)
            item = {**copy.deepcopy(turn), **scope, "id": str(uuid.uuid4())}
            state["turns"].append(item)
            if turn["agent_type"] == "referee":
                verdict = copy.deepcopy(FIXTURE["stream"]["verdicts"][turn["exchange_number"]-1])
                state["verdicts"].append({**verdict, **scope, "id": str(uuid.uuid4())})
        state["debriefs"].append({**copy.deepcopy(FIXTURE["stream"]["debrief"]), **scope, "id": str(uuid.uuid4())})
    state["status"] = AUDITS[audit_id]["status"] = "completed"
    state["finalReport"] = {**copy.deepcopy(FIXTURE["stream"]["finalReport"]), "audit_id": audit_id, "id": str(uuid.uuid4())}


@app.post("/audits")
async def start(request: Request):
    body = await request.json()
    paper_id = body["paper_id"]
    if not any(p["id"] == paper_id for p in PAPERS): raise HTTPException(404)
    topics = body.get("round_topics", ["experimental_setup"])
    audit_id = str(uuid.uuid4()); round_ids = [str(uuid.uuid4()) for _ in topics]
    AUDITS[audit_id] = summary(audit_id, paper_id, topics, "in_progress", **{k:v for k,v in body.items() if k in ["strictness_level", "depth", "mode"]})
    STATES[audit_id] = {"turns": [], "verdicts": [], "debriefs": [], "finalReport": None, "status": "in_progress"}
    task = asyncio.create_task(run_review(audit_id, topics, round_ids)); TASKS.add(task); task.add_done_callback(TASKS.discard)
    return {"audit_id": audit_id, "round_id": round_ids[0], "round_ids": round_ids, "round_topics": topics, "status": "in_progress", "domain": "social_science"}


def state_for(audit_id):
    if audit_id not in STATES: raise HTTPException(404)
    return STATES[audit_id]


@app.get("/audits/{audit_id}/turns")
async def turns(audit_id: str):
    state = state_for(audit_id)
    return {"turns": state["turns"], "verdicts": state["verdicts"], "status": state["status"]}


@app.get("/audits/{audit_id}/debriefs")
async def debriefs(audit_id: str): return state_for(audit_id)["debriefs"]


@app.get("/audits/{audit_id}/final-report")
async def report(audit_id: str):
    result = state_for(audit_id).get("finalReport")
    if not result: raise HTTPException(404, detail="Report not ready")
    return result


@app.get("/audits/{audit_id}/final-report/markdown")
async def markdown(audit_id: str): return Response((await report(audit_id))["content"], media_type="text/markdown")


@app.get("/audits/{audit_id}/version-diffs")
@app.post("/audits/{audit_id}/version-diffs")
async def diffs(audit_id: str): state_for(audit_id); return []


@app.get("/audits/{audit_id}/stream")
async def stream(audit_id: str):
    state_for(audit_id)
    async def events():
        sent_turns = sent_verdicts = sent_debriefs = counter = 0
        while True:
            state = state_for(audit_id)
            for key,event,start in [("turns","turn",sent_turns),("verdicts","verdict",sent_verdicts),("debriefs","debrief",sent_debriefs)]:
                for item in state[key][start:]:
                    counter += 1
                    yield f"id: {counter}\nevent: {event}\ndata: {json.dumps(item)}\n\n"
            sent_turns, sent_verdicts, sent_debriefs = len(state["turns"]), len(state["verdicts"]), len(state["debriefs"])
            if state["status"] == "completed":
                yield 'event: complete\ndata: {}\n\n'
                return
            yield 'event: heartbeat\ndata: {}\n\n'
            await asyncio.sleep(.2)
    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
