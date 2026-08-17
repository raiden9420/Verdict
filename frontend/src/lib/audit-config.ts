import { ROUND_TOPICS } from "@/types";
import type {
  AuditDepth,
  AuditMode,
  Domain,
  DomainSelection,
  StrictnessLevel,
} from "@/types";

export interface Choice<Value extends string> {
  value: Value;
  label: string;
  description: string;
}

export interface DepthChoice extends Choice<AuditDepth> {
  minimumTopics: number;
  maximumTopics: number;
  defaultTopics: string[];
}

export const STRICTNESS_OPTIONS: Choice<StrictnessLevel>[] = [
  {
    value: "constructive",
    label: "Constructive Peer",
    description: "Demanding but collaborative, with practical paths to revision.",
  },
  {
    value: "standard",
    label: "Standard Reviewer",
    description: "Balanced venue-review standards without softening material flaws.",
  },
  {
    value: "brutal",
    label: "Brutal Adversary",
    description: "Exacting stress tests aimed at publication-blocking weaknesses.",
  },
];

export const DEPTH_OPTIONS: DepthChoice[] = [
  {
    value: "fast",
    label: "Fast",
    description: "Focused audit across one or two research dimensions.",
    minimumTopics: 1,
    maximumTopics: 2,
    defaultTopics: ["theoretical_soundness", "experimental_setup"],
  },
  {
    value: "deep",
    label: "Deep",
    description: "Broader audit across three or four research dimensions.",
    minimumTopics: 3,
    maximumTopics: 4,
    defaultTopics: [
      "novelty_scope",
      "theoretical_soundness",
      "experimental_setup",
      "reproducibility",
    ],
  },
  {
    value: "exhaustive",
    label: "Exhaustive",
    description: "Five or six dimensions; highest call volume and longest runtime.",
    minimumTopics: 5,
    maximumTopics: 6,
    defaultTopics: ROUND_TOPICS.map((topic) => topic.slug),
  },
];

export const MODE_OPTIONS: Choice<AuditMode>[] = [
  {
    value: "author",
    label: "Author",
    description: "A coaching-oriented report with a prioritized revision plan.",
  },
  {
    value: "reviewer_assist",
    label: "Reviewer assist",
    description: "An editable draft review with strengths, weaknesses, questions, and recommendation.",
  },
];

export const DOMAIN_OPTIONS: Choice<DomainSelection>[] = [
  {
    value: "auto",
    label: "Auto-detected",
    description: "Use the field detected while preparing the paper.",
  },
  {
    value: "ml_cs",
    label: "ML / Computer Science",
    description: "Calibrate for baselines, ablations, leakage, compute, and code.",
  },
  {
    value: "life_sciences",
    label: "Life Sciences",
    description: "Calibrate for controls, protocols, materials, deposition, and ethics.",
  },
  {
    value: "social_science",
    label: "Social Science",
    description: "Calibrate for sampling, constructs, preregistration, materials, and IRB.",
  },
  {
    value: "other",
    label: "Other / General",
    description: "Use field-neutral scholarly standards inferred from the manuscript.",
  },
];

export const DOMAIN_LABELS: Record<Domain, string> = {
  ml_cs: "ML / Computer Science",
  life_sciences: "Life Sciences",
  social_science: "Social Science",
  other: "Other / General Research",
};

export function depthChoice(depth: AuditDepth): DepthChoice {
  return DEPTH_OPTIONS.find((option) => option.value === depth) ?? DEPTH_OPTIONS[0];
}

export function defaultTopicsForDepth(depth: AuditDepth): string[] {
  return [...depthChoice(depth).defaultTopics];
}

export function topicSelectionError(depth: AuditDepth, topics: string[]): string | null {
  const option = depthChoice(depth);
  if (topics.length < option.minimumTopics || topics.length > option.maximumTopics) {
    return `${option.label} depth requires ${option.minimumTopics}–${option.maximumTopics} topics; ${topics.length} selected.`;
  }
  if (new Set(topics).size !== topics.length) {
    return "Each round topic can be selected only once.";
  }
  if (topics.some((slug) => !ROUND_TOPICS.some((topic) => topic.slug === slug))) {
    return "The audit plan includes an unknown round topic.";
  }
  return null;
}

export function topicName(slug?: string | null): string {
  return ROUND_TOPICS.find((topic) => topic.slug === slug)?.name || slug || "Audit topic";
}

export function effectiveDomain(
  selection: DomainSelection,
  detected: Domain,
): Domain {
  return selection === "auto" ? detected : selection;
}
