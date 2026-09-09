import type { ExternalValidationResult, Turn, ValidationResult, Verdict } from "../types";

export const OUTCOMES = {
  ACTIONABLE_FLAW: { label: "Revision needed", tone: "revision", description: "The challenge exposed a weakness or the defense lacked valid evidence. Inspect the reasoning before revising." },
  SOLIDIFIED: { label: "Supported", tone: "supported", description: "The defense answered this challenge with valid paper evidence. This does not validate the paper as a whole." },
  CONTESTED: { label: "Open question", tone: "open", description: "The evidence or judgment remains unresolved and needs human review." },
} as const;

export function selectDisplayTurns(turns: Turn[]): Turn[] {
  const selected: Turn[] = [];
  for (let exchange = 1; exchange <= 3; exchange += 1) {
    const ordered = turns.filter((turn) => turn.exchange_number === exchange).sort((a, b) => a.sequence - b.sequence);
    const attacker = ordered.filter((turn) => turn.agent_type === "attacker").at(-1);
    const defender = ordered.filter((turn) => turn.agent_type === "defender" && (!attacker || turn.sequence > attacker.sequence)).at(-1);
    const referee = ordered.filter((turn) => turn.agent_type === "referee").at(-1);
    if (attacker) selected.push(attacker);
    if (defender) selected.push(defender);
    if (referee) selected.push(referee);
  }
  return selected;
}

export function validatorForTurn(turn: Turn, turns: Turn[]): Turn | undefined {
  return turns.filter((candidate) => candidate.agent_type === "validator" &&
    candidate.exchange_number === turn.exchange_number &&
    (!turn.round_id || candidate.round_id === turn.round_id) &&
    candidate.sequence > turn.sequence).sort((a, b) => a.sequence - b.sequence)[0];
}

export interface EvidencePassage extends ValidationResult {
  citedBy: "Challenge" | "Defense";
}

export function evidenceForTurn(turn: Turn, turns: Turn[]): EvidencePassage[] {
  if (turn.agent_type !== "attacker" && turn.agent_type !== "defender") return [];
  const validator = validatorForTurn(turn, turns);
  const citedBy = turn.agent_type === "attacker" ? "Challenge" : "Defense";
  const validations = turn.agent_type === "attacker"
    ? validator?.content.attacker_validations
    : validator?.content.defender_validations;
  const citedIds = new Set(turn.content.cited_chunk_ids || []);
  return (validations || []).filter((item) => !item.chunk_id || citedIds.has(item.chunk_id)).map((item) => ({ ...item, citedBy }));
}

export function evidenceForExchange(turns: Turn[], exchange: number): EvidencePassage[] {
  const passages = new Map<string, EvidencePassage>();
  // Put the defense first: it explains whether the challenge survived.
  const selected = selectDisplayTurns(turns).filter((turn) => turn.exchange_number === exchange)
    .sort((a, b) => (a.agent_type === "defender" ? -1 : 1) - (b.agent_type === "defender" ? -1 : 1));
  for (const turn of selected) {
    for (const item of evidenceForTurn(turn, turns)) {
      const key = `${item.citedBy}:${item.chunk_id || item.reason || "missing"}`;
      passages.set(key, item);
    }
  }
  return [...passages.values()];
}

function canonicalTitle(value: string): string {
  return value.toLowerCase().normalize("NFKC").replace(/[^\p{L}\p{N}]/gu, "");
}

export function matchExternalValidation(validations: ExternalValidationResult[], title: string, index: number): ExternalValidationResult | undefined {
  const normalized = canonicalTitle(title);
  // A matching title wins over a potentially stale positional index.
  return (normalized ? validations.find((item) => canonicalTitle(item.title || "") === normalized) : undefined) ||
    validations.find((item) => item.citation_index === index && (!item.title || !normalized));
}

export function citationStatus(validation?: ExternalValidationResult): { label: string; tone: string; explanation: string } {
  if (!validation) return { label: "Check pending", tone: "pending", explanation: "The saved validation result has not arrived yet." };
  if (validation.reason === "citation_not_found") return { label: "Not found", tone: "revision", explanation: "No matching title was found in the completed literature search. Confirm the reference manually." };
  if (validation.reason === "topically_unrelated") return { label: "Relevance concern", tone: "open", explanation: "A matching title exists, but broad topical similarity was below the check's threshold. Human review is needed." };
  if (validation.reason === "relevance_check_unavailable" || validation.reason === "existence_check_unavailable") return { label: "Check unavailable", tone: "pending", explanation: "A provider check could not finish. This is not evidence that the reference is incorrect." };
  if (validation.valid) return { label: "Found · topic match", tone: "supported", explanation: "Title existence and broad topical relevance were checked. Support for the specific in-text claim was not checked." };
  return { label: "Unverified", tone: "open", explanation: "This reference did not pass the recorded evidence check." };
}

export function safeExternalUrl(value?: string): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return (url.protocol === "https:" || url.protocol === "http:") && !url.username && !url.password ? url.href : null;
  } catch { return null; }
}

export function outcomeCounts(verdicts: Verdict[]) {
  const unique = new Map<string, Verdict>();
  for (const verdict of verdicts) {
    if (verdict.exchange_number < 1 || verdict.exchange_number > 3) continue;
    unique.set(`${verdict.round_id || verdict.round_topic || verdict.round_number || "legacy"}:${verdict.exchange_number}`, verdict);
  }
  return {
    revision: [...unique.values()].filter((item) => item.verdict_type === "ACTIONABLE_FLAW").length,
    supported: [...unique.values()].filter((item) => item.verdict_type === "SOLIDIFIED").length,
    open: [...unique.values()].filter((item) => item.verdict_type === "CONTESTED").length,
  };
}

export function signedUrlRefreshDelay(expiresIn: number): number {
  const seconds = Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn : 300;
  return Math.max(1_000, Math.min(seconds * 800, seconds * 1_000 - 30_000));
}
