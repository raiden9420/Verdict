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

const LOCAL_API_BASE = "http://localhost:8000";
const PRODUCTION_API_BASE = "https://verdict-backend-dw29.onrender.com";

function resolveApiBase(): string {
  if (process.env.NODE_ENV === "production") {
    return PRODUCTION_API_BASE;
  }

  return (process.env.NEXT_PUBLIC_API_URL?.trim() || LOCAL_API_BASE).replace(
    /\/+$/,
    "",
  );
}

const API_BASE = resolveApiBase();

interface ErrorPayload {
  detail?: unknown;
  message?: unknown;
}

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

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function objectValue(value: unknown, key: string): unknown {
  if (typeof value !== "object" || value === null) return undefined;
  return (value as Record<string, unknown>)[key];
}

function errorMessage(detail: unknown, fallback: string): string {
  if (typeof detail === "string" && detail.trim()) return detail;
  const message = objectValue(detail, "message");
  if (typeof message === "string" && message.trim()) return message;
  return fallback;
}

async function readErrorPayload(response: Response): Promise<ErrorPayload> {
  return response.json().catch(() => ({ detail: response.statusText }));
}

function withSessionQuery(url: string): string {
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}session_id=${encodeURIComponent(getSessionId())}`;
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
    const err = await readErrorPayload(res);
    const detail = err.detail;
    if (typeof detail === "object" && detail !== null) {
      const relevanceFailed = objectValue(detail, "relevance_failed") === true;
      const reason = objectValue(detail, "reason");
      throw new UploadError(
        errorMessage(detail, `Upload failed (${res.status})`),
        relevanceFailed,
        typeof reason === "string" ? reason : undefined,
      );
    }
    throw new UploadError(errorMessage(detail, `Upload failed (${res.status})`));
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
    const err = await readErrorPayload(res);
    throw new ApiError(
      errorMessage(err.detail ?? err.message, `Failed to start audit (${res.status})`),
      res.status,
    );
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// SSE stream URL (consumed by useSSE hook directly)
// ---------------------------------------------------------------------------
export function streamUrl(auditId: string): string {
  return withSessionQuery(`${API_BASE}/audits/${auditId}/stream`);
}

// ---------------------------------------------------------------------------
// Polling fallback
// ---------------------------------------------------------------------------
export async function fetchTurns(auditId: string): Promise<TurnsListResponse> {
  const res = await fetch(`${API_BASE}/audits/${auditId}/turns`, {
    headers: headers(),
    cache: "no-store",
  });

  if (!res.ok) {
    const err = await readErrorPayload(res);
    throw new ApiError(
      errorMessage(err.detail ?? err.message, `Failed to fetch audit state (${res.status})`),
      res.status,
    );
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Debrief card
// ---------------------------------------------------------------------------
export async function fetchDebrief(auditId: string): Promise<DebriefCard> {
  const res = await fetch(`${API_BASE}/audits/${auditId}/debrief`, {
    headers: headers(),
    cache: "no-store",
  });

  if (!res.ok) {
    if (res.status === 404) {
      throw new ApiError("Debrief not ready yet", res.status);
    }
    const err = await readErrorPayload(res);
    throw new ApiError(
      errorMessage(err.detail ?? err.message, `Failed to fetch debrief (${res.status})`),
      res.status,
    );
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// PDF URL (for the Document Viewer)
// ---------------------------------------------------------------------------
export function pdfUrl(paperId: string): string {
  return withSessionQuery(`${API_BASE}/papers/${paperId}/pdf`);
}

/**
 * Verify PDF authorization without following the signed-storage redirect or
 * downloading the paper. Cross-origin manual redirects are intentionally
 * exposed as `opaqueredirect`; that still means the backend authorized access.
 */
export async function verifyPdfAccess(paperId: string): Promise<void> {
  const res = await fetch(pdfUrl(paperId), {
    method: "GET",
    redirect: "manual",
    cache: "no-store",
  });

  if (res.type === "opaqueredirect" || (res.status >= 300 && res.status < 400)) {
    return;
  }
  if (!res.ok) {
    const err = await readErrorPayload(res);
    throw new ApiError(
      errorMessage(err.detail ?? err.message, `PDF preview is unavailable (${res.status})`),
      res.status,
    );
  }
}
