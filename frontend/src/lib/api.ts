/**
 * Authenticated API client for the Verdict backend.
 *
 * Every protected request carries the current Supabase access token. A single
 * refresh-and-retry is allowed for a 401 so requests racing token rotation do
 * not surface as false audit failures.
 */

import { getSupabaseBrowserClient } from "./supabase";
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
const PRODUCTION_API_BASE = "https://verdict-backend-dw29.onrender.com";

function resolveApiBase(): string {
  const configuredBase = process.env.NEXT_PUBLIC_API_URL?.trim();
  const fallback = process.env.NODE_ENV === "production"
    ? PRODUCTION_API_BASE
    : LOCAL_API_BASE;
  return (configuredBase || fallback).replace(/\/+$/, "");
}

const API_BASE = resolveApiBase();

interface ErrorPayload {
  detail?: unknown;
  message?: unknown;
}

let refreshPromise: Promise<string | null> | null = null;

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

async function currentAccessToken(): Promise<string | null> {
  const { data, error } = await getSupabaseBrowserClient().auth.getSession();
  if (error) throw new ApiError(error.message, 401);
  return data.session?.access_token ?? null;
}

async function refreshedAccessToken(): Promise<string | null> {
  if (!refreshPromise) {
    refreshPromise = getSupabaseBrowserClient().auth
      .refreshSession()
      .then(({ data, error }) => {
        if (error) return null;
        return data.session?.access_token ?? null;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function endInvalidLocalSession(): Promise<void> {
  try {
    await getSupabaseBrowserClient().auth.signOut({ scope: "local" });
  } catch {
    // The request still fails closed below. Supabase will reconcile its local
    // session on the next auth event or page load if sign-out is unavailable.
  }
}

function requestWithToken(
  input: string,
  init: RequestInit,
  accessToken: string,
): Promise<Response> {
  const requestHeaders = new Headers(init.headers);
  requestHeaders.set("Authorization", `Bearer ${accessToken}`);
  return fetch(input, { ...init, headers: requestHeaders });
}

async function authenticatedFetch(
  input: string,
  init: RequestInit = {},
): Promise<Response> {
  const accessToken = await currentAccessToken();
  if (!accessToken) {
    throw new ApiError("Sign in to continue.", 401);
  }

  let response = await requestWithToken(input, init, accessToken);
  if (response.status !== 401) return response;

  const refreshedToken = await refreshedAccessToken();
  if (refreshedToken) {
    response = await requestWithToken(input, init, refreshedToken);
    if (response.status !== 401) return response;
  }

  await endInvalidLocalSession();
  return response;
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  const payload = await readErrorPayload(response);
  return new ApiError(
    errorMessage(payload.detail ?? payload.message, fallback),
    response.status,
  );
}

async function getJson<T>(path: string, fallback: string): Promise<T> {
  const response = await authenticatedFetch(`${API_BASE}${path}`, {
    cache: "no-store",
  });
  if (!response.ok) throw await responseError(response, fallback);
  return response.json() as Promise<T>;
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
  );
}

export async function fetchFinalReport(auditId: string): Promise<FinalReport> {
  return getJson<FinalReport>(
    `/audits/${encodeURIComponent(auditId)}/final-report`,
    "Failed to fetch final report",
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
  return response.json() as Promise<VersionDiff[]>;
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
