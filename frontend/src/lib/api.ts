/**
 * API client for the Verdict backend.
 * Attaches the X-Session-Id header to every request.
 */

import { getSessionId } from "./session";
import type {
  PaperUploadResponse,
  AuditCreateResponse,
  TurnsListResponse,
  DebriefCard,
} from "@/types";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * Build headers with the session ID.
 */
function headers(extra: Record<string, string> = {}): Record<string, string> {
  return {
    "X-Session-Id": getSessionId(),
    ...extra,
  };
}

// Custom error class for paper upload failures (e.g. relevance check failure)
export class UploadError extends Error {
  relevanceFailed: boolean;
  reason?: string;

  constructor(message: string, relevanceFailed: boolean = false, reason?: string) {
    super(message);
    this.name = "UploadError";
    this.relevanceFailed = relevanceFailed;
    this.reason = reason;
  }
}

// ---------------------------------------------------------------------------
// Paper upload
// ---------------------------------------------------------------------------
export async function uploadPaper(file: File, force: boolean = false): Promise<PaperUploadResponse> {
  const form = new FormData();
  form.append("file", file);

  const url = force ? `${API_BASE}/papers?force=true` : `${API_BASE}/papers`;
  const res = await fetch(url, {
    method: "POST",
    headers: headers(), // don't set Content-Type — browser sets it for FormData
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = err.detail;
    if (typeof detail === "object" && detail !== null) {
      throw new UploadError(
        detail.message || "Upload failed",
        Boolean(detail.relevance_failed),
        detail.reason
      );
    }
    const msg = typeof detail === "string" ? detail : "Upload failed";
    throw new UploadError(msg, false);
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Start audit
// ---------------------------------------------------------------------------
export async function startAudit(
  paperId: string,
  roundTopic: string,
): Promise<AuditCreateResponse> {
  const res = await fetch(`${API_BASE}/audits`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ paper_id: paperId, round_topic: roundTopic }),
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail || "Failed to start audit");
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// SSE stream URL (consumed by useSSE hook directly)
// ---------------------------------------------------------------------------
export function streamUrl(auditId: string): string {
  return `${API_BASE}/audits/${auditId}/stream`;
}

// ---------------------------------------------------------------------------
// Polling fallback
// ---------------------------------------------------------------------------
export async function fetchTurns(auditId: string): Promise<TurnsListResponse> {
  const res = await fetch(`${API_BASE}/audits/${auditId}/turns`, {
    headers: headers(),
  });

  if (!res.ok) {
    throw new Error("Failed to fetch turns");
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Debrief card
// ---------------------------------------------------------------------------
export async function fetchDebrief(auditId: string): Promise<DebriefCard> {
  const res = await fetch(`${API_BASE}/audits/${auditId}/debrief`, {
    headers: headers(),
  });

  if (!res.ok) {
    if (res.status === 404) {
      throw new Error("Debrief not ready yet");
    }
    throw new Error("Failed to fetch debrief");
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// PDF URL (for the Document Viewer)
// ---------------------------------------------------------------------------
export function pdfUrl(paperId: string): string {
  return `${API_BASE}/papers/${paperId}/pdf`;
}
