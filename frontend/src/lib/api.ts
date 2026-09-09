/**
 * Authenticated API client for the Verdict backend.
 *
 * Every protected request carries the current Supabase access token. A single
 * refresh-and-retry is allowed for a 401 so requests racing token rotation do
 * not surface as false audit failures.
 */

import { isTurnsResponse, isDebrief, isFinalReport, isVersionDiff, arrayOf } from "./audit-stream";
import { getSupabaseBrowserClient } from "./supabase";
import { ApiError, createAuthenticatedFetch } from "./session-fetch";
export { ApiError } from "./session-fetch";
import type {
  AuditCreateRequest,
  AuditCreateResponse,
  AuditSummary,
  DebriefCard,
  FinalReport,
  PaperSummary,
  PaperUploadResponse,
  PdfUrlResponse,
  TurnsListResponse,
  VersionDiff,
} from "@/types";

const LOCAL_API_BASE = "http://localhost:8000";

function resolveApiBase(): string {
  const configuredBase = process.env.NEXT_PUBLIC_API_URL?.trim();
  return (configuredBase || LOCAL_API_BASE).replace(/\/+$/, "");
}

const API_BASE = resolveApiBase();

interface ErrorPayload {
  detail?: unknown;
  message?: unknown;
}

const authenticatedFetch = createAuthenticatedFetch(() => getSupabaseBrowserClient().auth);

export class UploadError extends Error {
  relevanceFailed: boolean;
  reason?: string;
  status?: number;

  constructor(
    message: string,
    relevanceFailed: boolean = false,
    reason?: string,
    status?: number,
  ) {
    super(message);
    this.name = "UploadError";
    this.relevanceFailed = relevanceFailed;
    this.reason = reason;
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

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const payload = await readErrorPayload(response);
  return new ApiError(
    errorMessage(payload.detail ?? payload.message, fallback),
    response.status,
  );
}

async function getJson<T>(path: string, fallback: string, guard?: (value: unknown) => value is T): Promise<T> {
  const response = await authenticatedFetch(`${API_BASE}${path}`, {
    cache: "no-store",
  });
  if (!response.ok) throw await responseError(response, fallback);
  const value: unknown = await response.json();
  if (guard && !guard(value)) throw new ApiError("The service returned an invalid saved artifact. Please retry.", 502);
  return value as T;
}

// ---------------------------------------------------------------------------
// Papers
// ---------------------------------------------------------------------------
export async function uploadPaper(
  file: File,
  force: boolean = false,
  parentPaperId?: string | null,
): Promise<PaperUploadResponse> {
  const form = new FormData();
  form.append("file", file);

  const query = new URLSearchParams();
  if (force) query.set("force", "true");
  if (parentPaperId) query.set("parent_paper_id", parentPaperId);
  const suffix = query.size > 0 ? `?${query.toString()}` : "";
  const response = await authenticatedFetch(`${API_BASE}/papers${suffix}`, {
    method: "POST",
    body: form,
  });

  if (!response.ok) {
    const payload = await readErrorPayload(response);
    const detail = payload.detail;
    if (typeof detail === "object" && detail !== null) {
      const relevanceFailed = objectValue(detail, "relevance_failed") === true;
      const reason = objectValue(detail, "reason");
      throw new UploadError(
        errorMessage(detail, `Upload failed (${response.status})`),
        relevanceFailed,
        typeof reason === "string" ? reason : undefined,
        response.status,
      );
    }
    throw new UploadError(
      errorMessage(detail, `Upload failed (${response.status})`),
      false,
      undefined,
      response.status,
    );
  }

  return response.json() as Promise<PaperUploadResponse>;
}

export async function fetchPapers(): Promise<PaperSummary[]> {
  return getJson<PaperSummary[]>("/papers", "Failed to load papers");
}

export async function fetchPdfUrl(paperId: string): Promise<PdfUrlResponse> {
  const result = await getJson<PdfUrlResponse>(
    `/papers/${encodeURIComponent(paperId)}/pdf-url`,
    "PDF preview is unavailable",
  );
  let parsed: URL;
  try {
    parsed = new URL(result.url);
  } catch {
    throw new ApiError("The PDF service returned an invalid URL.", 502);
  }
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new ApiError("The PDF service returned an unsafe URL.", 502);
  }
  return result;
}

// ---------------------------------------------------------------------------
// Audits
// ---------------------------------------------------------------------------
type AuditOptions = Omit<AuditCreateRequest, "paper_id">;

export async function startAudit(
  paperId: string,
  roundTopicOrOptions: string | AuditOptions,
): Promise<AuditCreateResponse> {
  const body: AuditCreateRequest = typeof roundTopicOrOptions === "string"
    ? { paper_id: paperId, round_topic: roundTopicOrOptions }
    : { paper_id: paperId, ...roundTopicOrOptions };
  const response = await authenticatedFetch(`${API_BASE}/audits`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

  if (!response.ok) {
    throw await responseError(response, `Failed to start audit (${response.status})`);
  }
  return response.json() as Promise<AuditCreateResponse>;
}

export async function openAuditStream(
  auditId: string,
  lastEventId: string,
  signal: AbortSignal,
): Promise<Response> {
  const headers = new Headers({ Accept: "text/event-stream" });
  if (lastEventId) headers.set("Last-Event-ID", lastEventId);
  const response = await authenticatedFetch(
    `${API_BASE}/audits/${encodeURIComponent(auditId)}/stream`,
    { headers, cache: "no-store", signal },
  );
  if (!response.ok) {
    throw await responseError(response, `Live audit stream is unavailable (${response.status})`);
  }
  if (!response.body) {
    throw new ApiError("The live audit stream returned no response body.", 502);
  }
  return response;
}

export async function fetchTurns(auditId: string): Promise<TurnsListResponse> {
  return getJson<TurnsListResponse>(
    `/audits/${encodeURIComponent(auditId)}/turns`,
    "Failed to fetch audit state",
    isTurnsResponse,
  );
}

export async function fetchAudits(): Promise<AuditSummary[]> {
  return getJson<AuditSummary[]>("/audits", "Failed to load audits");
}

export async function fetchDebrief(auditId: string): Promise<DebriefCard> {
  const response = await authenticatedFetch(
    `${API_BASE}/audits/${encodeURIComponent(auditId)}/debrief`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    if (response.status === 404) {
      throw new ApiError("Debrief not ready yet", response.status);
    }
    throw await responseError(response, `Failed to fetch debrief (${response.status})`);
  }
  return response.json() as Promise<DebriefCard>;
}

export async function fetchDebriefs(auditId: string): Promise<DebriefCard[]> {
  return getJson<DebriefCard[]>(
    `/audits/${encodeURIComponent(auditId)}/debriefs`,
    "Failed to fetch audit debriefs",
    arrayOf(isDebrief),
  );
}

export async function fetchFinalReport(auditId: string): Promise<FinalReport> {
  return getJson<FinalReport>(
    `/audits/${encodeURIComponent(auditId)}/final-report`,
    "Failed to fetch final report",
    isFinalReport,
  );
}

export async function fetchVersionDiffs(
  auditId: string,
  compareToAuditId?: string | null,
): Promise<VersionDiff[]> {
  const query = compareToAuditId
    ? `?compare_to=${encodeURIComponent(compareToAuditId)}`
    : "";
  return getJson<VersionDiff[]>(
    `/audits/${encodeURIComponent(auditId)}/version-diffs${query}`,
    "Failed to fetch version comparison",
    arrayOf(isVersionDiff),
  );
}

export async function retryVersionDiffs(
  auditId: string,
  compareToAuditId?: string | null,
): Promise<VersionDiff[]> {
  const query = compareToAuditId
    ? `?compare_to=${encodeURIComponent(compareToAuditId)}`
    : "";
  const response = await authenticatedFetch(
    `${API_BASE}/audits/${encodeURIComponent(auditId)}/version-diffs${query}`,
    { method: "POST", cache: "no-store" },
  );
  if (!response.ok) {
    throw await responseError(
      response,
      `Failed to retry version comparison (${response.status})`,
    );
  }
  const value: unknown = await response.json();
  if (!arrayOf(isVersionDiff)(value)) throw new ApiError("The service returned an invalid revision comparison.", 502);
  return value;
}

export async function fetchFinalReportMarkdown(auditId: string): Promise<Blob> {
  const response = await authenticatedFetch(
    `${API_BASE}/audits/${encodeURIComponent(auditId)}/final-report/markdown`,
    { cache: "no-store" },
  );
  if (!response.ok) {
    throw await responseError(response, `Failed to export final report (${response.status})`);
  }
  return response.blob();
}
