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

export type Domain = "ml_cs" | "life_sciences" | "social_science" | "other";
export type DomainSelection = "auto" | Domain;
export type StrictnessLevel = "constructive" | "standard" | "brutal";
export type AuditDepth = "fast" | "deep" | "exhaustive";
export type AuditMode = "author" | "reviewer_assist";

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
  detected_domain: Domain;
  parent_paper_id?: string | null;
  version_number: number;
}

export interface PaperSummary {
  id: string;
  filename: string;
  page_count?: number | null;
  detected_domain: Domain;
  parent_paper_id?: string | null;
  version_number: number;
  uploaded_at: string;
}

export interface AuditCreateRequest {
  paper_id: string;
  round_topic?: string;
  round_topics?: string[];
  strictness_level?: StrictnessLevel;
  depth?: AuditDepth;
  mode?: AuditMode;
  domain?: DomainSelection;
  compare_to_audit_id?: string | null;
}

export interface AuditCreateResponse {
  audit_id: string;
  round_id: string;
  status: string;
  round_ids?: string[];
  round_topics?: string[];
  strictness_level?: StrictnessLevel;
  depth?: AuditDepth;
  mode?: AuditMode;
  domain?: Domain;
}

export interface AuditSummary {
  audit_id: string;
  paper_id: string;
  filename?: string | null;
  status: string;
  round_topic: string;
  round_topics: string[];
  strictness_level: StrictnessLevel;
  depth: AuditDepth;
  mode: AuditMode;
  domain: Domain;
  created_at: string;
}

export interface AuditRoundEvent {
  round_id: string;
  round_number: number;
  round_topic: string;
  round_topic_name?: string;
}

export interface ExternalCitation {
  title: string;
  authors?: string[];
  year?: number | string;
  url?: string;
  source?: string;
  similarity_score?: number;
  validated?: boolean;
  reference_id?: string;
}

export interface ExternalValidationResult {
  title: string;
  valid: boolean;
  citation_type?: "citation_integrity" | "missing_baseline";
  reference_id?: string | null;
  matched_title?: string | null;
  exists?: boolean;
  relevant?: boolean;
  similarity_score?: number | null;
  validation_complete?: boolean;
  source?: string;
  reason?: string;
  citation_index?: number;
  attempt_sequence?: number;
}

export interface TurnContent {
  // Attacker fields
  claim_summary?: string;
  critique_text?: string;
  cited_chunk_ids?: string[];
  cited_reference_id?: string | null;
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
  defender_citations_valid?: boolean;
}

export interface ValidationResult {
  chunk_id: string | null;
  valid: boolean;
  similarity_score: number;
  chunk_text?: string;
  page_number?: number;
  validation_method?: string;
  reason?: string;
}

export interface Turn {
  id: string;
  exchange_number: number;
  agent_type: "attacker" | "defender" | "referee" | "validator";
  sequence: number;
  content: TurnContent;
  created_at?: string;
  round_id?: string | null;
  round_number?: number | null;
  round_topic?: string | null;
}

export interface Verdict {
  id: string;
  exchange_number: number;
  claim_summary?: string;
  verdict_type: "SOLIDIFIED" | "ACTIONABLE_FLAW" | "CONTESTED";
  confidence?: number;
  rationale?: string;
  cited_chunk_ids?: string[];
  round_id?: string | null;
  round_number?: number | null;
  round_topic?: string | null;
}

export interface ReproducibilitySignals {
  domain?: Domain;
  code_available?: boolean;
  code_details?: string[];
  data_available?: boolean;
  data_details?: string[];
  hyperparameters_disclosed?: boolean;
  hyperparameter_details?: string[];
  compute_disclosed?: boolean;
  compute_details?: string[];
  seed_disclosed?: boolean;
  seed_details?: string[];
  materials_available?: boolean;
  material_details?: string[];
  database_deposition?: boolean;
  deposition_details?: string[];
  protocols_disclosed?: boolean;
  protocol_details?: string[];
  ethics_disclosed?: boolean;
  ethics_approval?: boolean;
  ethics_details?: string[];
  preregistered?: boolean;
  preregistration_details?: string[];
  instruments_disclosed?: boolean;
  instrument_details?: string[];
  methods_documented?: boolean;
  method_details?: string[];
}

export interface DebriefCard {
  id: string;
  executive_synthesis?: string;
  solidified_strengths?: string[];
  actionable_weaknesses?: string[];
  contested_points?: string[];
  reproducibility_checklist?: ReproducibilitySignals;
  round_id?: string | null;
  round_number?: number | null;
  round_topic?: string | null;
}

export interface FinalReport {
  id: string;
  audit_id: string;
  mode: AuditMode;
  content: string;
  created_at: string;
}

export interface VersionDiff {
  id: string;
  audit_id_old: string;
  audit_id_new: string;
  round_topic: string;
  diff_summary: string;
  created_at: string;
}

export interface PdfUrlResponse {
  url: string;
  expires_in: number;
}

export interface TurnsListResponse {
  turns: Turn[];
  verdicts: Verdict[];
  status: string;
  error?: string;
  error_message?: string;
}

export type AuditStatus = "idle" | "in_progress" | "completed" | "error";
