import type {
  AuditDepth,
  AuditMode,
  AuditRoundEvent,
  AuditSummary,
  DebriefCard,
  Domain,
  PaperSummary,
  StrictnessLevel,
  Turn,
  Verdict,
} from "@/types";

export interface ActiveAudit {
  auditId: string;
  paperId: string;
  filename: string;
  roundIds: string[];
  roundTopics: string[];
  strictnessLevel: StrictnessLevel;
  depth: AuditDepth;
  mode: AuditMode;
  domain: Domain;
  versionNumber: number;
  parentPaperId?: string | null;
  compareToAuditId?: string | null;
}

export interface PreparedPaper {
  paperId: string;
  filename: string;
  fileKey: string;
  detectedDomain: Domain;
  parentPaperId?: string | null;
  versionNumber: number;
  pageCount: number;
  chunkCount: number;
}

export interface RoundPlan {
  key: string;
  id?: string | null;
  number: number;
  topic: string;
  topicName?: string;
}

type RoundScoped = {
  round_id?: string | null;
  round_number?: number | null;
  round_topic?: string | null;
};

export function paperFamilyId(paper: PaperSummary): string {
  return paper.parent_paper_id || paper.id;
}

export function paperForAudit(
  papers: PaperSummary[],
  audit: AuditSummary,
): PaperSummary | undefined {
  return papers.find((paper) => paper.id === audit.paper_id);
}

export function activeAuditFromHistory(
  audit: AuditSummary,
  paper?: PaperSummary,
): ActiveAudit {
  return {
    auditId: audit.audit_id,
    paperId: audit.paper_id,
    filename: paper?.filename || audit.filename || "Research paper",
    roundIds: [],
    roundTopics: audit.round_topics?.length
      ? audit.round_topics
      : audit.round_topic
        ? [audit.round_topic]
        : [],
    strictnessLevel: audit.strictness_level || "standard",
    depth: audit.depth || "fast",
    mode: audit.mode || "author",
    domain: audit.domain || paper?.detected_domain || "other",
    versionNumber: paper?.version_number || 1,
    parentPaperId: paper?.parent_paper_id,
  };
}

export function roundMatches(
  value: RoundScoped,
  round: RoundPlan,
  totalRounds: number,
): boolean {
  if (value.round_id && round.id) return value.round_id === round.id;
  if (value.round_number != null && value.round_number > 0) {
    return value.round_number === round.number;
  }
  if (value.round_topic) return value.round_topic === round.topic;
  return totalRounds === 1;
}

function roundKey(id: string | null | undefined, number: number, topic: string): string {
  return id || `${number}:${topic}`;
}

export function buildRoundPlan(
  activeAudit: ActiveAudit,
  turns: Turn[],
  verdicts: Verdict[],
  debriefs: DebriefCard[],
  activeRound: AuditRoundEvent | null,
): RoundPlan[] {
  const rounds: RoundPlan[] = activeAudit.roundTopics.map((topic, index) => ({
    key: roundKey(activeAudit.roundIds[index], index + 1, topic),
    id: activeAudit.roundIds[index],
    number: index + 1,
    topic,
  }));

  const scoped: Array<RoundScoped & { round_topic_name?: string }> = [
    ...turns,
    ...verdicts,
    ...debriefs,
    ...(activeRound ? [activeRound] : []),
  ];
  for (const value of scoped) {
    if (!value.round_id && !value.round_topic) continue;
    const number = value.round_number && value.round_number > 0
      ? value.round_number
      : rounds.length + 1;
    const topic = value.round_topic || `round_${number}`;
    const existing = rounds.find((round) =>
      Boolean(value.round_id && round.id === value.round_id) ||
      (!value.round_id && round.number === number) ||
      (!round.id && round.topic === topic),
    );
    if (existing) {
      existing.id ||= value.round_id;
      existing.topicName ||= value.round_topic_name;
      existing.key = roundKey(existing.id, existing.number, existing.topic);
      continue;
    }
    rounds.push({
      key: roundKey(value.round_id, number, topic),
      id: value.round_id,
      number,
      topic,
      topicName: value.round_topic_name,
    });
  }

  if (!rounds.length) {
    rounds.push({ key: "1:audit", number: 1, topic: "audit" });
  }
  return rounds.sort((left, right) => left.number - right.number);
}
