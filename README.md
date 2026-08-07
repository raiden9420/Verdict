# Verdict — Adversarial Research Audit System

A powerful web application that audits research papers through a structured, turn-based debate between three AI personas: an **Attacker** (skeptical reviewer), a **Defender** (author advocate), and a **Referee** (adjudicator). 

Unlike standard single-pass LLM summaries that suffer from confirmation bias and hallucinated citations, Verdict enforces a brutal, deterministic critique where every claim must be backed by a programmatic citation check against the paper's actual text.

---

## 🧠 Core Concept & Personas

- **Attacker (Skeptic):** Interrogates methodology, searches for missing baselines, mathematical inconsistencies, unstated assumptions, and dataset limitations.
- **Defender (Advocate):** Grounded strictly in the paper's text. Defends the methodology using only what's actually in the document and concedes when it cannot produce evidence.
- **Validator (Trust Layer):** A deterministic, non-LLM check ensuring the Defender's citations actually exist in the retrieved text and are semantically consistent.
- **Referee (Judge):** Adjudicates each exchange, generating a final verdict with confidence scores:
  - ✅ **Solidified Fact:** Defender successfully refutes critique with verified citations.
  - ❌ **Actionable Flaw:** Defender cannot produce a citation, or it fails verification.
  - ⚠️ **Contested:** Valid citation, but only partially addresses the critique (flagged for human review).

### System Architecture

```mermaid
graph TD
    User([User]) -->|Upload PDF| API(FastAPI Backend)
    API -->|Chunk & Embed| DB[(Supabase pgvector)]
    
    User -->|Launch Audit| API
    
    subgraph LangGraph Multi-Agent Arena
        direction TB
        A[Attacker] -->|Critiques Claims| D[Defender]
        D -->|Defends with Citations| V{Grounding Validator}
        V -->|Verified/Failed| R[Referee]
        R -->|Determines Verdict| Final[Debrief Synthesis]
    end
    
    API -.->|Triggers| A
    DB -.->|Semantic Search| D
    Final -->|Server-Sent Events| UI[Next.js SPA Frontend]
```

---

## 🕸️ LangGraph State Machine

Verdict heavily relies on **LangGraph** to orchestrate the adversarial debate as a structured, cyclic state graph rather than a loose chain of prompts.

- **State Management**: The graph maintains a strict `AuditState` that tracks the unfolding transcript, parsed citations, confidence scores, and retry counts across multiple exchanges.
- **Conditional Routing**: If an agent fabricates a citation (caught by the Validator node), the graph conditionally routes back to that agent, forcing a retry before proceeding to the Referee.
- **Streaming Output**: As the graph traverses its nodes (Attacker → Defender → Validator → Referee), state updates are intercepted and streamed to the frontend via Server-Sent Events (SSE) in real-time.

---

## 🚀 Features

- **Multi-Agent Adversarial Debate**: Powered by LangGraph and Gemini.
- **Deterministic Citation Validation**: Trust is built in, not assumed.
- **Single Page Application (SPA)**: A beautiful, seamless workspace built with Next.js and the Porsche Design System.
- **Live SSE Streaming**: Watch the debate unfold in real-time.
- **Split-Screen Workspace**: Integrated PDF viewer with auto-scrolling to cited pages.
- **Round Debrief Cards**: Instant synthesis of Strengths, Weaknesses, and Contested points.

---

## 🛠️ Quick Start

### Prerequisites
- Python 3.11+
- Node.js 18+
- [Supabase](https://supabase.com) project (free tier) for vector storage
- [Google AI Studio](https://aistudio.google.com) API key (free tier)

### 1. Database Setup
1. Create a Supabase project at [supabase.com](https://supabase.com)
2. Go to **SQL Editor** and paste the contents of `backend/migrations/001_initial_schema.sql`
3. Run the query to create all tables, indexes, and the `match_chunks` RPC function.

### 2. Backend (FastAPI)
```bash
cd backend

# Create virtual environment
python -m venv venv
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your Gemini API key and Supabase credentials

# Start the server
uvicorn app.main:app --reload --port 8000
```

### 3. Frontend (Next.js SPA)
```bash
cd frontend

# Install dependencies
npm install

# Configure environment (.env.local should point to http://localhost:8000)
# Start dev server
npm run dev
```

Visit [http://localhost:3000](http://localhost:3000) to enter the Arena.

---

## 🏗️ Project Structure

```text
Verdict/
├── frontend/          → Next.js SPA, Tailwind CSS v4, Porsche Design System
│   └── src/app/       → Consolidated routing and state management
├── backend/
│   ├── app/
│   │   ├── main.py    → FastAPI app entry point
│   │   ├── services/  → PDF ingestion, embedding, retrieval
│   │   ├── agents/    → System prompts, LangGraph nodes, Validator
│   │   └── api/       → REST + SSE endpoints
│   └── migrations/    → SQL schema for Supabase
```

## 🔌 API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/papers` | Upload + ingest a PDF |
| GET | `/papers/{id}/pdf` | Serve inline PDF for the document viewer |
| POST | `/audits` | Start a new adversarial audit |
| GET | `/audits/{id}/stream` | SSE stream of live multi-agent debate |
| GET | `/audits/{id}/debrief` | Completed debrief synthesis |

---
*Built for Authors, Reviewers, and Research Labs demanding uncompromised rigorous analysis.*
