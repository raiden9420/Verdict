"use client";

import { useMemo, useState } from "react";
import { PButton, PIcon, PTag } from "@porsche-design-system/components-react";
import { DOMAIN_LABELS, topicName } from "@/lib/audit-config";
import { paperFamilyId } from "@/lib/audit-workspace";
import type { AuditSummary, PaperSummary } from "@/types";

interface PaperFamily {
  id: string;
  papers: PaperSummary[];
  newestTimestamp: number;
}

export function LibraryView({
  papers,
  audits,
  loading,
  error,
  onRefresh,
  onOpenAudit,
  onNewVersion,
}: {
  papers: PaperSummary[];
  audits: AuditSummary[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onOpenAudit: (audit: AuditSummary, target: "arena" | "report") => void;
  onNewVersion: (paper: PaperSummary, comparison?: AuditSummary) => void;
}) {
  const families = useMemo(() => {
    const grouped = new Map<string, PaperSummary[]>();
    for (const paper of papers) {
      const family = paperFamilyId(paper);
      grouped.set(family, [...(grouped.get(family) || []), paper]);
    }
    return [...grouped.entries()]
      .map(([id, familyPapers]): PaperFamily => ({
        id,
        papers: familyPapers.sort((left, right) => left.version_number - right.version_number),
        newestTimestamp: Math.max(...familyPapers.map((paper) => new Date(paper.uploaded_at).getTime() || 0)),
      }))
      .sort((left, right) => right.newestTimestamp - left.newestTimestamp);
  }, [papers]);

  const revisionBaselines = useMemo(
    () => audits
      .filter((audit) => audit.status.toLowerCase() === "completed")
      .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime()),
    [audits],
  );

  return (
    <div className="library-page">
      <div className="page-header phase3-page-header">
        <div><div className="eyebrow"><span className="eyebrow-line" /> PRIVATE WORKSPACE</div><h1>Paper &amp; audit library</h1><p className="page-subtitle">Only papers and audits owned by this account appear here.</p></div>
        <PButton type="button" variant="secondary" icon="reset" onClick={onRefresh} loading={loading}>Refresh</PButton>
      </div>

      {error && <div className="pipeline-alert error" role="alert"><PIcon name="error-filled" /><div><strong>Library unavailable</strong><span>{error}</span></div></div>}
      {loading && !papers.length ? <div className="workspace-loading" role="status"><span className="loading-mark" aria-hidden="true" /><strong>Loading your library</strong><span>Fetching owned papers and audits.</span></div> : null}
      {!loading && !families.length ? <div className="library-empty"><PIcon name="document" /><strong>No papers yet</strong><span>Prepare a PDF in Configure audit to create your first version.</span></div> : null}

      <div className="paper-family-list">
        {families.map((family) => (
          <section key={family.id} className="paper-family" aria-labelledby={`family-${family.id}`}>
            <div className="paper-family-header">
              <div><span className="section-kicker">VERSION FAMILY</span><h2 id={`family-${family.id}`}>{family.papers.at(-1)?.filename || "Research paper"}</h2></div>
              <span>{family.papers.length} version{family.papers.length === 1 ? "" : "s"}</span>
            </div>
            <div className="paper-version-list">
              {[...family.papers].reverse().map((paper) => (
                <PaperVersion
                  key={paper.id}
                  paper={paper}
                  audits={audits.filter((audit) => audit.paper_id === paper.id)}
                  comparisonAudits={revisionBaselines.filter((audit) => audit.paper_id === paper.id)}
                  onOpenAudit={onOpenAudit}
                  onNewVersion={onNewVersion}
                />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function PaperVersion({
  paper,
  audits,
  comparisonAudits,
  onOpenAudit,
  onNewVersion,
}: {
  paper: PaperSummary;
  audits: AuditSummary[];
  comparisonAudits: AuditSummary[];
  onOpenAudit: (audit: AuditSummary, target: "arena" | "report") => void;
  onNewVersion: (paper: PaperSummary, comparison?: AuditSummary) => void;
}) {
  const [comparisonId, setComparisonId] = useState("");
  const comparison = comparisonAudits.find((audit) => audit.audit_id === comparisonId);

  return (
    <article className="paper-version-card">
      <div className="paper-version-meta">
        <span className="version-pill">v{paper.version_number}</span>
        <div><strong>{paper.filename}</strong><span>{paper.page_count ? `${paper.page_count} pages · ` : ""}{DOMAIN_LABELS[paper.detected_domain]} · uploaded {formatDate(paper.uploaded_at)}</span></div>
      </div>
      <div className="paper-version-audits">
        {audits.length ? audits.map((audit) => (
          <div className="history-audit" key={audit.audit_id}>
            <div className="history-audit-main">
              <PTag variant={audit.status === "completed" ? "success" : audit.status === "error" ? "error" : "warning"}>{audit.status}</PTag>
              <div><strong>{audit.round_topics.map(topicName).join(" · ")}</strong><span>{audit.depth} · {audit.strictness_level} · {audit.mode === "reviewer_assist" ? "reviewer assist" : "author"} · {formatDate(audit.created_at)}</span></div>
            </div>
            <div className="history-audit-actions">
              <button type="button" onClick={() => onOpenAudit(audit, "arena")}>Open evidence</button>
              {audit.status === "completed" && <button type="button" onClick={() => onOpenAudit(audit, "report")}>Final report</button>}
            </div>
          </div>
        )) : <div className="history-no-audits">No audits run on this version yet.</div>}
      </div>
      <div className="revision-action">
        <label>
          <span>Comparison baseline</span>
          <select value={comparisonId} onChange={(event) => setComparisonId(event.target.value)}>
            <option value="">Auto-select nearest earlier audit</option>
            {comparisonAudits.map((audit) => <option key={audit.audit_id} value={audit.audit_id}>v{paper.version_number} · {formatDate(audit.created_at)} · {audit.round_topics.length} topics · {audit.depth}</option>)}
          </select>
        </label>
        <PButton type="button" variant="secondary" icon="upload" onClick={() => onNewVersion(paper, comparison)}>Upload new version</PButton>
      </div>
    </article>
  );
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "date unavailable" : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
