import type { AuditRoundEvent, DebriefCard, FinalReport, Turn, TurnsListResponse, Verdict, VersionDiff } from "../types";

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}

const text = (value: unknown): value is string => typeof value === "string";
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const integer = (value: unknown): value is number => finite(value) && Number.isInteger(value);
const optionalText = (value: unknown) => value == null || text(value);
const stringList = (value: unknown) => Array.isArray(value) && value.every(text);
const optionalStringList = (value: unknown) => value == null || stringList(value);
const optionalNumber = (value: unknown) => value == null || finite(value);
const optionalBoolean = (value: unknown) => value == null || typeof value === "boolean";
const optionalArray = (value: unknown, check: (item: unknown) => boolean) => value == null || (Array.isArray(value) && value.every(check));
const verdictType = (value: unknown) => value === "SOLIDIFIED" || value === "ACTIONABLE_FLAW" || value === "CONTESTED";

function scoped(value: Record<string, unknown>): boolean {
  return optionalText(value.round_id) && optionalText(value.round_topic) &&
    (value.round_number == null || (integer(value.round_number) && value.round_number >= 0));
}

function isValidation(value: unknown): boolean {
  return isRecord(value) && optionalText(value.chunk_id) && typeof value.valid === "boolean" &&
    optionalNumber(value.similarity_score) && optionalText(value.chunk_text) && optionalText(value.section) &&
    optionalText(value.validation_method) && optionalText(value.reason) && optionalBoolean(value.source_truncated) &&
    (value.page_number == null || (integer(value.page_number) && value.page_number >= 1));
}

function isExternalCitation(value: unknown): boolean {
  return isRecord(value) && text(value.title) && optionalStringList(value.authors) &&
    (value.year == null || text(value.year) || finite(value.year)) &&
    optionalText(value.url) && optionalText(value.source) && optionalNumber(value.similarity_score);
}

function isExternalValidation(value: unknown): boolean {
  return isRecord(value) && text(value.title) && typeof value.valid === "boolean" &&
    optionalText(value.reason) && optionalNumber(value.citation_index) &&
    optionalText(value.matched_title) && optionalText(value.reference_id) &&
    optionalBoolean(value.exists) && optionalBoolean(value.relevant) && optionalNumber(value.similarity_score);
}

function isTurnContent(value: unknown): boolean {
  if (!isRecord(value)) return false;
  for (const field of ["claim_summary", "critique_text", "rebuttal_text", "rationale", "critique_type", "cited_reference_id"]) {
    if (!optionalText(value[field])) return false;
  }
  return optionalStringList(value.cited_chunk_ids) && optionalBoolean(value.concedes) &&
    optionalBoolean(value.external_search_performed) && optionalNumber(value.external_candidate_count) &&
    optionalNumber(value.confidence) && (value.verdict == null || verdictType(value.verdict)) &&
    optionalArray(value.external_citations, isExternalCitation) && optionalArray(value.external_validations, isExternalValidation) &&
    optionalArray(value.attacker_validations, isValidation) && optionalArray(value.defender_validations, isValidation) &&
    (value.provenance == null || (isRecord(value.provenance) && optionalText(value.provenance.provider) && optionalText(value.provenance.model) && optionalText(value.provenance.prompt_version))) &&
    (value.evidence_scope == null || (isRecord(value.evidence_scope) && optionalStringList(value.evidence_scope.retrieved_chunk_ids) && optionalStringList(value.evidence_scope.retrieval_methods) && optionalNumber(value.evidence_scope.reference_count))) &&
    (value.adjudication == null || (isRecord(value.adjudication) && Object.values(value.adjudication).every((item) => item == null || typeof item === "string" || typeof item === "boolean")));
}

export function isTurn(value: unknown): value is Turn {
  return isRecord(value) && text(value.id) && integer(value.exchange_number) && value.exchange_number >= 1 && value.exchange_number <= 3 &&
    integer(value.sequence) && value.sequence >= 0 && scoped(value) && optionalText(value.created_at) &&
    ["attacker", "defender", "referee", "validator"].includes(String(value.agent_type)) && isTurnContent(value.content);
}

export function isVerdict(value: unknown): value is Verdict {
  return isRecord(value) && text(value.id) && integer(value.exchange_number) && value.exchange_number >= 1 && value.exchange_number <= 3 &&
    verdictType(value.verdict_type) && optionalText(value.claim_summary) && optionalText(value.rationale) &&
    optionalNumber(value.confidence) && optionalStringList(value.cited_chunk_ids) && scoped(value);
}

export function isDebrief(value: unknown): value is DebriefCard {
  if (!isRecord(value) || !text(value.id) || !scoped(value) || !optionalText(value.executive_synthesis) ||
    !optionalStringList(value.solidified_strengths) || !optionalStringList(value.actionable_weaknesses) || !optionalStringList(value.contested_points)) return false;
  if (value.reproducibility_checklist == null) return true;
  if (!isRecord(value.reproducibility_checklist)) return false;
  return Object.entries(value.reproducibility_checklist).every(([key, item]) => key === "domain" ? text(item) : item == null || typeof item === "boolean" || stringList(item));
}

export function isFinalReport(value: unknown): value is FinalReport {
  return isRecord(value) && text(value.id) && text(value.audit_id) && text(value.content) && text(value.created_at) && (value.mode === "author" || value.mode === "reviewer_assist");
}

export function isVersionDiff(value: unknown): value is VersionDiff {
  return isRecord(value) && text(value.id) && text(value.audit_id_old) && text(value.audit_id_new) && text(value.round_topic) && text(value.diff_summary) && text(value.created_at);
}

export function isRoundEvent(value: unknown): value is AuditRoundEvent {
  return isRecord(value) && text(value.round_id) && Boolean(value.round_id) && text(value.round_topic) && Boolean(value.round_topic) && integer(value.round_number) && value.round_number >= 1 && optionalText(value.round_topic_name);
}

export function isTurnsResponse(value: unknown): value is TurnsListResponse {
  return isRecord(value) && text(value.status) && ["in_progress", "completed", "error", "failed"].includes(value.status.toLowerCase()) &&
    Array.isArray(value.turns) && value.turns.every(isTurn) && Array.isArray(value.verdicts) && value.verdicts.every(isVerdict) &&
    optionalText(value.error) && optionalText(value.error_message);
}

export interface AuditStreamEvent { type: string; data: string; id?: string }

export function parseEventLines(lines: string[]): AuditStreamEvent | null {
  let type = "message";
  let id: string | undefined;
  const data: string[] = [];
  let hasEventField = false;
  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") { type = value || "message"; hasEventField = true; }
    else if (field === "data") { data.push(value); hasEventField = true; }
    else if (field === "id" && !value.includes("\0")) { id = value; hasEventField = true; }
  }
  return hasEventField ? { type, data: data.join("\n"), id } : null;
}

const MAX_EVENT_CHARACTERS = 1_000_000;

export async function consumeEventStream(response: Response, onEvent: (event: AuditStreamEvent) => void): Promise<void> {
  if (!response.body) throw new Error("The live stream returned no response body.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventLines: string[] = [];
  let eventSize = 0;
  let completed = false;
  const consumeLine = (rawLine: string) => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line !== "") {
      eventSize += line.length;
      if (eventSize > MAX_EVENT_CHARACTERS) throw new Error("The live update exceeded the supported size.");
      eventLines.push(line);
      return;
    }
    const event = parseEventLines(eventLines);
    eventLines = []; eventSize = 0;
    if (event) onEvent(event);
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        consumeLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
      if (buffer.length + eventSize > MAX_EVENT_CHARACTERS) throw new Error("The live update exceeded the supported size.");
    }
    buffer += decoder.decode();
    if (buffer) consumeLine(buffer);
    const trailing = parseEventLines(eventLines);
    if (trailing) onEvent(trailing);
    completed = true;
  } finally {
    if (!completed) await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function validatedEventData<T>(data: string, guard: (value: unknown) => value is T): T {
  let value: unknown;
  try { value = JSON.parse(data); } catch { throw new Error("The live update was not valid JSON."); }
  if (!guard(value)) throw new Error("The live update contained an invalid payload.");
  return value;
}

export function isMessageEvent(value: unknown): value is { message?: string; round_id?: string; round_number?: number; round_topic?: string } {
  return isRecord(value) && optionalText(value.message) && scoped(value);
}

export function isVersionDiffsEvent(value: unknown): value is { items: VersionDiff[] } {
  return isRecord(value) && Array.isArray(value.items) && value.items.every(isVersionDiff);
}

export function arrayOf<T>(guard: (value: unknown) => value is T): (value: unknown) => value is T[] {
  return (value): value is T[] => Array.isArray(value) && value.every(guard);
}
