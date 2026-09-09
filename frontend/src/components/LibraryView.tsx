"use client";

import { useMemo, useState } from "react";
import { Button, Icon, Tag } from "@/components/ui";
import { DOMAIN_LABELS, depthChoice, topicName } from "@/lib/audit-config";
import { paperFamilyId } from "@/lib/audit-workspace";
import type { AuditSummary, PaperSummary } from "@/types";

export function LibraryView({ papers, audits, loading, error, onRefresh, onOpenAudit, onNewVersion, onAuditPaper, onNewAudit }: {
  papers: PaperSummary[];
  audits: AuditSummary[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onOpenAudit: (audit: AuditSummary, target: "arena" | "report") => void;
  onNewVersion: (paper: PaperSummary, comparison?: AuditSummary) => void;
  onAuditPaper: (paper: PaperSummary) => void;
  onNewAudit: () => void;
}) {
  const [query, setQuery] = useState("");
  const families = useMemo(() => {
    const grouped = new Map<string, PaperSummary[]>();
    for (const paper of papers) {
      const id = paperFamilyId(paper);
      grouped.set(id, [...(grouped.get(id) || []), paper]);
    }
    return [...grouped.entries()].map(([id, members]) => ({ id, papers: members.sort((a,b) => b.version_number-a.version_number), updated: Math.max(...members.map(p => Date.parse(p.uploaded_at) || 0)) })).sort((a,b) => b.updated-a.updated);
  }, [papers]);
  const filtered = families.filter(family => family.papers.some(paper => `${paper.filename} ${DOMAIN_LABELS[paper.detected_domain]}`.toLowerCase().includes(query.trim().toLowerCase())));
  return <div className="library-page">
    <header className="page-header"><div><span className="section-kicker">Your research workspace</span><h1>Paper library</h1><p className="page-subtitle">Return to the evidence. Reuse a paper or follow its next revision.</p></div><Button icon="plus" onClick={onNewAudit}>New review</Button></header>
    <div className="library-toolbar"><label className="search-input"><Icon name="search" /><span className="visually-hidden">Find a paper</span><input type="search" value={query} onChange={event => setQuery(event.target.value)} placeholder="Find a paper or research field" /></label><span className="small-muted">{families.length} paper{families.length === 1 ? "" : "s"}</span><Button variant="ghost" icon="reset" onClick={onRefresh} loading={loading}>Refresh</Button></div>
    {error && <div className="pipeline-alert error" role="alert"><Icon name="error-filled" /><div><strong>We couldn’t refresh your library.</strong><span>{error}</span></div></div>}
    {loading && !papers.length && <div className="workspace-loading" role="status"><span className="loading-mark" /><strong>Opening your library</strong><span>Loading saved papers and reviews.</span></div>}
    {!loading && !error && !families.length && <div className="library-empty"><Icon name="book" /><h2>Your next review starts here.</h2><p>Prepare a manuscript once. Its reviews, evidence, and revisions stay together in this library.</p><Button onClick={onNewAudit}>Choose your first paper</Button></div>}
    {!!families.length && !filtered.length && <div className="library-empty"><h2>No matching papers</h2><p>Try a different filename or research field.</p><button className="text-link" onClick={() => setQuery("")}>Clear search</button></div>}
    <div className="paper-family-list">{filtered.map(family => <section key={family.id} className="paper-family" aria-labelledby={`family-${family.id}`}><header className="paper-family-header"><Icon name="document" /><div><h2 id={`family-${family.id}`}>{family.papers[0].filename}</h2><p>{DOMAIN_LABELS[family.papers[0].detected_domain]} · {family.papers.length} version{family.papers.length === 1 ? "" : "s"}</p></div></header>{family.papers.map((paper,index) => <details className="paper-version" key={paper.id} open={index === 0}><summary><span><strong>Version {paper.version_number}</strong>{index === 0 && <span className="version-current">Latest</span>}<span>{paper.page_count ? `${paper.page_count} pages · ` : ""}{formatDate(paper.uploaded_at)}</span></span><Icon name="chevron-down" /></summary><PaperVersion paper={paper} audits={audits.filter(audit => audit.paper_id === paper.id)} onOpenAudit={onOpenAudit} onNewVersion={onNewVersion} onAuditPaper={onAuditPaper} /></details>)}</section>)}</div>
  </div>;
}

function PaperVersion({ paper, audits, onOpenAudit, onNewVersion, onAuditPaper }: {
  paper: PaperSummary;
  audits: AuditSummary[];
  onOpenAudit: (audit: AuditSummary, target: "arena" | "report") => void;
  onNewVersion: (paper: PaperSummary, comparison?: AuditSummary) => void;
  onAuditPaper: (paper: PaperSummary) => void;
}) {
  const ordered = [...audits].sort((a,b) => Date.parse(b.created_at)-Date.parse(a.created_at));
  const latestCompleted = ordered.find(audit => audit.status === "completed");
  return <div className="paper-version-body"><div className="version-actions"><Button variant="secondary" icon="plus" onClick={() => onAuditPaper(paper)}>Review this version</Button><button className="text-link" type="button" onClick={() => onNewVersion(paper, latestCompleted)}><Icon name="upload" /> Upload a revision</button></div>{ordered.length ? ordered.map(audit => <article className="history-audit" key={audit.audit_id}><div className="history-audit-main"><Tag variant={audit.status === "completed" ? "success" : audit.status === "error" ? "error" : "warning"}>{audit.status === "completed" ? "Complete" : audit.status === "error" ? "Interrupted" : "In progress"}</Tag><div><h3>{depthChoice(audit.depth).label} review · {audit.round_topics.length} {audit.round_topics.length === 1 ? "topic" : "topics"}</h3><p>{audit.round_topics.map(topicName).join(" · ")}</p><small>{formatDate(audit.created_at)} · {audit.mode === "reviewer_assist" ? "Reviewer draft" : "Author brief"}</small></div></div><div className="history-audit-actions"><button className="text-link" type="button" onClick={() => onOpenAudit(audit,"arena")}>{audit.status === "in_progress" ? "Follow review" : "Inspect findings"}<Icon name="arrow-right" /></button>{audit.status === "completed" && <button className="text-link" type="button" onClick={() => onOpenAudit(audit,"report")}>Read brief</button>}</div></article>) : <p className="history-no-audits">This paper is prepared and ready for its first review. No need to upload it again.</p>}</div>;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "Date unavailable" : date.toLocaleDateString(undefined, { dateStyle: "medium" });
}
