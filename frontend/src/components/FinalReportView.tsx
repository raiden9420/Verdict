"use client";

import { useState } from "react";
import { PButton, PIcon, PTag } from "@porsche-design-system/components-react";
import type { UseSSEResult } from "@/hooks/useSSE";
import { fetchFinalReportMarkdown, retryVersionDiffs } from "@/lib/api";
import { DOMAIN_LABELS, topicName } from "@/lib/audit-config";
import type { ActiveAudit } from "@/lib/audit-workspace";
import type { VersionDiff } from "@/types";

function safeFilename(filename: string): string {
  const stem = filename.replace(/\.pdf$/i, "").replace(/[^a-z0-9._-]+/gi, "-").replace(/^-+|-+$/g, "");
  return `${stem || "verdict-audit"}-final-report.md`;
}

export function FinalReportView({
  audit,
  stream,
  onOpenArena,
  onNewVersion,
}: {
  audit: ActiveAudit;
  stream: UseSSEResult;
  onOpenArena: () => void;
  onNewVersion: () => void;
}) {
  const [downloading, setDownloading] = useState(false);
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [retryingDiffs, setRetryingDiffs] = useState(false);
  const [retryDiffError, setRetryDiffError] = useState<string | null>(null);
  const [retryResolvedStreamError, setRetryResolvedStreamError] = useState(false);
  const [retriedDiffs, setRetriedDiffs] = useState<VersionDiff[]>([]);

  const versionDiffs = mergeVersionDiffs(stream.versionDiffs, retriedDiffs);
  const visibleVersionDiffError = retryDiffError ||
    (retryResolvedStreamError ? null : stream.versionDiffError);
  // Keep this available even when some rows already exist: a provider outage
  // can persist early topic comparisons before a later topic fails. The retry
  // endpoint is idempotent and fills only missing topics.
  const canRetryVersionDiff = audit.versionNumber > 1 &&
    stream.status === "completed";

  const download = async () => {
    if (downloading) return;
    setDownloading(true);
    setDownloadError(null);
    try {
      const blob = await fetchFinalReportMarkdown(audit.auditId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = safeFilename(audit.filename);
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      setDownloadError(error instanceof Error ? error.message : "The Markdown report could not be downloaded.");
    } finally {
      setDownloading(false);
    }
  };

  const retryComparison = async () => {
    if (retryingDiffs || !canRetryVersionDiff) return;
    setRetryingDiffs(true);
    setRetryDiffError(null);
    try {
      const diffs = await retryVersionDiffs(
        audit.auditId,
        audit.compareToAuditId || versionDiffs[0]?.audit_id_old,
      );
      setRetriedDiffs((current) => mergeVersionDiffs(current, diffs));
      if (diffs.length) {
        setRetryResolvedStreamError(true);
      } else {
        setRetryDiffError(
          "No shared-topic comparison was returned. Check that an earlier version has a completed audit covering the same topics.",
        );
      }
    } catch (error) {
      setRetryDiffError(
        error instanceof Error
          ? error.message
          : "The revision comparison could not be retried.",
      );
    } finally {
      setRetryingDiffs(false);
    }
  };

  return (
    <div className="report-page">
      <div className="page-header phase3-page-header">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> PAPER-LEVEL SYNTHESIS</div>
          <h1>Final report</h1>
          <p className="page-subtitle"><PIcon name="document" /> {audit.filename} · Version {audit.versionNumber}</p>
        </div>
        <div className="report-actions">
          <PButton type="button" variant="secondary" onClick={onOpenArena}>View topic evidence</PButton>
          <PButton type="button" icon="download" loading={downloading} disabled={!stream.finalReport || downloading} onClick={() => { void download(); }}>Download Markdown</PButton>
        </div>
      </div>

      <div className="report-context-strip">
        <span><small>MODE</small><strong>{audit.mode === "reviewer_assist" ? "Reviewer assist" : "Author"}</strong></span>
        <span><small>DOMAIN</small><strong>{DOMAIN_LABELS[audit.domain]}</strong></span>
        <span><small>COVERAGE</small><strong>{audit.roundTopics.length} topics</strong></span>
        <span><small>STRICTNESS</small><strong>{audit.strictnessLevel}</strong></span>
      </div>

      {downloadError && <div className="pipeline-alert error" role="alert"><PIcon name="error-filled" /><div><strong>Export failed</strong><span>{downloadError}</span></div></div>}
      {stream.transportError && stream.status === "completed" && !stream.finalReport && <div className="pipeline-alert recovery" role="status"><PIcon name="information" /><div><strong>Report is still syncing</strong><span>{stream.transportError}</span></div></div>}

      <section className="final-report-card" aria-labelledby="final-report-title">
        <div className="final-report-heading">
          <div><span className="section-kicker">{audit.mode === "reviewer_assist" ? "EDITABLE REVIEW DRAFT" : "PRIORITIZED REVISION BRIEF"}</span><h2 id="final-report-title">Paper-level synthesis</h2></div>
          {stream.finalReport ? <PTag variant="success">Final</PTag> : stream.status === "error" ? <PTag variant="error">Unavailable</PTag> : <PTag variant="warning">Synthesizing</PTag>}
        </div>
        {stream.finalReport ? (
          <pre className="markdown-report">{stream.finalReport.content}</pre>
        ) : (
          <div className="report-pending"><span className="loading-mark" aria-hidden="true" /><strong>{stream.status === "error" ? "No final report was produced" : "Synthesizing across all topic debriefs"}</strong><span>{stream.status === "error" ? stream.auditError || "The audit stopped before report synthesis." : "The report appears after every selected topic completes."}</span></div>
        )}
      </section>

      <section className="version-diff-section" aria-labelledby="version-diff-title">
        <div className="verdicts-header">
          <div><span className="section-kicker">REVISION INTELLIGENCE</span><h2 id="version-diff-title">Changes from the prior version</h2></div>
          <div className="version-diff-heading-actions">
            <span>{versionDiffs.length ? `${versionDiffs.length} topic comparisons` : "Optional"}</span>
            {canRetryVersionDiff && <PButton type="button" variant="secondary" icon="reset" loading={retryingDiffs} disabled={retryingDiffs} onClick={() => { void retryComparison(); }}>{versionDiffs.length ? "Refresh comparison" : "Retry comparison"}</PButton>}
          </div>
        </div>
        {visibleVersionDiffError && <div className="pipeline-alert recovery" role="status"><PIcon name="information" /><div><strong>Comparison unavailable</strong><span>{visibleVersionDiffError}</span></div></div>}
        {versionDiffs.length ? (
          <div className="version-diff-list">
            {versionDiffs.map((diff) => (
              <details key={diff.id} className="version-diff-card" open>
                <summary><span><PIcon name="compare" /><strong>{topicName(diff.round_topic)}</strong></span><span>Compared with {diff.audit_id_old.slice(0, 8)}</span></summary>
                <pre>{diff.diff_summary}</pre>
              </details>
            ))}
          </div>
        ) : (
          <div className="empty-report-section"><PIcon name="compare" /><div><strong>No revision comparison for this audit</strong><span>Upload a newer version from the library and select a completed earlier audit to generate topic-level change summaries.</span></div></div>
        )}
      </section>

      <div className="arena-bottom"><span><PIcon name="information" /> Markdown is displayed as text and exported from the stored report.</span><PButton type="button" variant="secondary" icon="upload" onClick={onNewVersion}>Upload a new version</PButton></div>
    </div>
  );
}

function mergeVersionDiffs(
  current: VersionDiff[],
  incoming: VersionDiff[],
): VersionDiff[] {
  const merged = new Map<string, VersionDiff>();
  for (const diff of [...current, ...incoming]) {
    const key = diff.id || `${diff.audit_id_old}:${diff.audit_id_new}:${diff.round_topic}`;
    merged.set(key, diff);
  }
  return [...merged.values()];
}
