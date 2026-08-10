/**
 * Shared TypeScript types for the Verdict frontend.
 * Mirrors the backend Pydantic schemas.
 */

// ---------------------------------------------------------------------------
// Round topics
// ---------------------------------------------------------------------------
export interface RoundTopic {
  slug: string;
  name: string;
  description: string;
}

export const ROUND_TOPICS: RoundTopic[] = [
  {
    slug: "novelty_scope",
    name: "Novelty, Scope & Problem Formulation",
    description: "Is the problem well-posed, and is the contribution actually novel?",
  },
  {
    slug: "theoretical_soundness",
    name: "Theoretical Soundness & Mathematical Rigor",
    description: "Are the assumptions, proofs, and derivations sound?",
  },
  {
    slug: "experimental_setup",
    name: "Experimental Setup, Datasets & Baselines",
    description: "Are the experiments fair, and are the baselines appropriate?",
  },
  {
    slug: "reproducibility",
    name: "Reproducibility, Compute & Ablation Studies",
    description: "Could someone else reproduce this? Are ablations sufficient?",
  },
  {
    slug: "limitations_impact",
    name: "Limitations, Broader Impact & Edge Cases",
    description: "Does the paper honestly address where it breaks down?",
  },
  {
    slug: "statistical_rigor",
    name: "Statistical Rigor & Methodological Validity",
    description: "Are p-values, sample sizes, effect sizes, and multiple comparisons handled correctly?",
  },
];


// ---------------------------------------------------------------------------
// API response types
// ---------------------------------------------------------------------------
export interface PaperUploadResponse {
  paper_id: string;
  filename: string;
  page_count: number;
  chunk_count: number;
}

export interface AuditCreateResponse {
  audit_id: string;
  round_id: string;
  status: string;
}

export interface ExternalCitation {
  title: string;
  authors?: string[];
  year?: number | string;
  url?: string;
  source?: string;
  similarity_score?: number;
  validated?: boolean;
}

export interface ExternalValidationResult {
  title: string;
  valid: boolean;
  source?: string;
}

export interface TurnContent {
  // Attacker fields
  claim_summary?: string;
  critique_text?: string;
  cited_chunk_ids?: string[];
  critique_type?: string;
  external_citations?: ExternalCitation[];
  external_search_performed?: boolean;
  external_sources?: string[];
  external_candidate_count?: number;
  // Defender fields
  rebuttal_text?: string;
  concedes?: boolean;
  // Referee fields
  verdict?: string;
  confidence?: number;
  rationale?: string;
  // Validator fields
  attacker_validations?: ValidationResult[];
  defender_validations?: ValidationResult[];
  external_validations?: ExternalValidationResult[];
  attacker_citations_valid?: boolean;
}

export interface ValidationResult {
  chunk_id: string;
  valid: boolean;
  similarity_score: number;
  chunk_text?: string;
  page_number?: number;
}

export interface Turn {
  id: string;
  exchange_number: number;
  agent_type: "attacker" | "defender" | "referee" | "validator";
  sequence: number;
  content: TurnContent;
  created_at?: string;
}

export interface Verdict {
  id: string;
  exchange_number: number;
  claim_summary?: string;
  verdict_type: "SOLIDIFIED" | "ACTIONABLE_FLAW" | "CONTESTED";
  confidence?: number;
  rationale?: string;
  cited_chunk_ids?: string[];
}

export interface ReproducibilitySignals {
  code_available: boolean;
  code_details?: string[];
  data_available: boolean;
  data_details?: string[];
  hyperparameters_disclosed: boolean;
  hyperparameter_details?: string[];
  compute_disclosed: boolean;
  compute_details?: string[];
  seed_disclosed: boolean;
  seed_details?: string[];
}

export interface DebriefCard {
  id: string;
  executive_synthesis?: string;
  solidified_strengths?: string[];
  actionable_weaknesses?: string[];
  contested_points?: string[];
  reproducibility_checklist?: ReproducibilitySignals;
}

export interface TurnsListResponse {
  turns: Turn[];
  verdicts: Verdict[];
  status: string;
}

// ---------------------------------------------------------------------------
// SSE event types
// ---------------------------------------------------------------------------
export type SSEEventType = "turn" | "verdict" | "debrief" | "complete" | "error" | "heartbeat" | "exchange_skipped" | "process_update";


export interface SSEEvent {
  type: SSEEventType;
  data: Turn | Verdict | DebriefCard | { message?: string } | Record<string, never>;
}

// ---------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------
export type AuditStatus = "idle" | "uploading" | "starting" | "in_progress" | "completed" | "error";

export interface AuditState {
  status: AuditStatus;
  paperId?: string;
  auditId?: string;
  roundId?: string;
  turns: Turn[];
  verdicts: Verdict[];
  debrief?: DebriefCard;
  error?: string;
  highlightedPages: number[];
  skippedExchanges: number[];
}
