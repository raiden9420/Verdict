"use client";

import { useEffect, useId, useRef } from "react";
import { PButton, PButtonPure, PIcon, PTag, PText } from "@porsche-design-system/components-react";
import {
  DEPTH_OPTIONS,
  DOMAIN_LABELS,
  DOMAIN_OPTIONS,
  MODE_OPTIONS,
  STRICTNESS_OPTIONS,
  depthChoice,
  topicSelectionError,
} from "@/lib/audit-config";
import type { Choice } from "@/lib/audit-config";
import type { PreparedPaper } from "@/lib/audit-workspace";
import { ROUND_TOPICS } from "@/types";
import type { AuditDepth, AuditMode, AuditSummary, DomainSelection, PaperSummary, StrictnessLevel } from "@/types";

export interface RelevancePrompt {
  message: string;
  reason?: string;
}

export function AuditSetup({
  file,
  preparedPaper,
  revisionBase,
  comparisonAudits,
  comparisonAuditId,
  strictness,
  depth,
  topics,
  mode,
  domainSelection,
  preparing,
  launching,
  error,
  auditRunning,
  onUpload,
  onFileDrop,
  onRemove,
  onPrepare,
  onLaunch,
  onCancelRevision,
  onComparisonChange,
  onStrictnessChange,
  onDepthChange,
  onTopicToggle,
  onModeChange,
  onDomainChange,
}: {
  file: File | null;
  preparedPaper: PreparedPaper | null;
  revisionBase: PaperSummary | null;
  comparisonAudits: AuditSummary[];
  comparisonAuditId: string | null;
  strictness: StrictnessLevel;
  depth: AuditDepth;
  topics: string[];
  mode: AuditMode;
  domainSelection: DomainSelection;
  preparing: boolean;
  launching: boolean;
  error: string | null;
  auditRunning: boolean;
  onUpload: () => void;
  onFileDrop: (file?: File) => void;
  onRemove: () => void;
  onPrepare: () => void;
  onLaunch: () => void;
  onCancelRevision: () => void;
  onComparisonChange: (auditId: string | null) => void;
  onStrictnessChange: (value: StrictnessLevel) => void;
  onDepthChange: (value: AuditDepth) => void;
  onTopicToggle: (slug: string) => void;
  onModeChange: (value: AuditMode) => void;
  onDomainChange: (value: DomainSelection) => void;
}) {
  const busy = preparing || launching;
  const topicError = topicSelectionError(depth, topics);
  const depthConfig = depthChoice(depth);
  return (
    <div className="setup-page phase3-setup" aria-busy={busy}>
      <span className="visually-hidden" role="status" aria-live="polite">{preparing ? "Preparing and classifying the paper." : launching ? "Starting the configured audit." : ""}</span>
      <div className="eyebrow"><span className="eyebrow-line" /> RESEARCH INTEGRITY / NEW AUDIT</div>
      <div className="setup-heading"><div><h1>Put every claim<br /><em>under pressure.</em></h1><PText size="medium">Configure the scrutiny, cover multiple research dimensions,<br />and leave with a paper-level report.</PText></div></div>

      {revisionBase && (
        <section className="revision-banner">
          <div><PIcon name="compare" /><span><small>NEW PAPER VERSION</small><strong>Building on v{revisionBase.version_number} · {revisionBase.filename}</strong></span></div>
          <button type="button" onClick={onCancelRevision} disabled={busy || Boolean(preparedPaper)}>{preparedPaper ? "Version link saved" : "Cancel version link"}</button>
        </section>
      )}

      <section className="paper-card">
        <div className="section-kicker">01 — Source document</div>
        {file ? (
          <div className="paper-row">
            <div className="paper-icon"><PIcon name="document" /></div>
            <div className="paper-details"><strong>{file.name}</strong><span>PDF · {(file.size / (1024 * 1024)).toFixed(1)} MB</span></div>
            <PTag variant={preparedPaper ? "success" : "secondary"} icon={preparedPaper ? "check" : undefined}>{preparedPaper ? `Prepared · v${preparedPaper.versionNumber}` : "Selected"}</PTag>
            <PButtonPure icon="close" aria-label="Remove document" onClick={onRemove} disabled={busy} />
          </div>
        ) : (
          <button type="button" className="upload-zone" onClick={onUpload} disabled={busy} onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); onFileDrop(event.dataTransfer.files?.[0]); }} aria-describedby="upload-requirements">
            <span className="upload-icon"><PIcon name="upload" /></span><span><strong>{revisionBase ? "Choose the revised manuscript" : "Choose a research paper"}</strong><small id="upload-requirements">Drop a PDF here or browse · up to 20 MB</small></span>
          </button>
        )}
        {file && !preparedPaper && <div className="prepare-row"><div><strong>Prepare before configuration</strong><span>We extract the PDF, run the relevance gate, and detect its research domain once.</span></div><PButton type="button" variant="secondary" loading={preparing} disabled={busy} onClick={onPrepare}>Prepare paper</PButton></div>}
        {preparedPaper && <div className="prepared-summary"><div><span className="small-muted">DETECTED DOMAIN</span><strong>{DOMAIN_LABELS[preparedPaper.detectedDomain]}</strong></div><div><span className="small-muted">DOCUMENT</span><strong>{preparedPaper.pageCount} pages · {preparedPaper.chunkCount} evidence chunks</strong></div><PTag variant="success" icon="check">Relevance passed</PTag></div>}
        {error && <div className="setup-error" role="alert"><PIcon name="error-filled" /> <span>{error}</span></div>}
      </section>

      <section className="config-section phase3-config">
        <div className="section-kicker">02 — Configure scrutiny</div>
        <div className="config-columns">
          <ChoiceCards label="Strictness" value={strictness} options={STRICTNESS_OPTIONS} onChange={onStrictnessChange} disabled={busy} />
          <ChoiceCards label="Report mode" value={mode} options={MODE_OPTIONS} onChange={onModeChange} disabled={busy} />
        </div>
        <ChoiceCards label="Audit depth" value={depth} options={DEPTH_OPTIONS} onChange={onDepthChange} disabled={busy} compact />
        {depth === "exhaustive" && <div className="quota-warning"><PIcon name="warning" /><span><strong>High call volume</strong> Exhaustive runs use 5–6 topics and can strain provider free-tier quotas.</span></div>}
      </section>

      <section className="config-section">
        <div className="section-kicker">03 — Select round topics</div>
        <fieldset className="topic-checkbox-group" disabled={busy}>
          <legend><strong>{depthConfig.label} depth</strong> · select {depthConfig.minimumTopics}–{depthConfig.maximumTopics} dimensions</legend>
          <div className="topic-checkbox-grid">
            {ROUND_TOPICS.map((topic, index) => {
              const selected = topics.includes(topic.slug);
              const atMaximum = topics.length >= depthConfig.maximumTopics;
              return (
                <label key={topic.slug} className={`topic-checkbox ${selected ? "selected" : ""} ${!selected && atMaximum ? "limit-reached" : ""}`}>
                  <input type="checkbox" checked={selected} disabled={!selected && atMaximum} onChange={() => onTopicToggle(topic.slug)} />
                  <span className="topic-check-mark">{selected ? <PIcon name="check" /> : String(index + 1).padStart(2, "0")}</span>
                  <span><strong>{topic.name}</strong><small>{topic.description}</small></span>
                </label>
              );
            })}
          </div>
        </fieldset>
        <div className={`selection-note ${topicError ? "invalid" : ""}`}><span className="pulse-dot" /> {topicError || `${topics.length} topics selected · ${topics.length * 3} total exchanges`}</div>
      </section>

      <section className="config-section">
        <div className="section-kicker">04 — Domain persona &amp; version comparison</div>
        <fieldset className="domain-selector" disabled={!preparedPaper || busy}>
          <legend>Critique persona</legend>
          <div className="domain-options">
            {DOMAIN_OPTIONS.map((option) => <label key={option.value} className={domainSelection === option.value ? "selected" : ""}><input type="radio" name="domain" value={option.value} checked={domainSelection === option.value} onChange={() => onDomainChange(option.value)} /><span><strong>{option.value === "auto" && preparedPaper ? `Auto · ${DOMAIN_LABELS[preparedPaper.detectedDomain]}` : option.label}</strong><small>{option.description}</small></span></label>)}
          </div>
        </fieldset>
        {revisionBase && (
          <label className="comparison-selector"><span>Compare this version with</span><select value={comparisonAuditId || ""} onChange={(event) => onComparisonChange(event.target.value || null)} disabled={busy}><option value="">Auto-select nearest earlier completed audit</option>{comparisonAudits.map((audit) => <option key={audit.audit_id} value={audit.audit_id}>{new Date(audit.created_at).toLocaleDateString()} · {audit.round_topics.length} topics · {audit.depth} · {audit.mode === "reviewer_assist" ? "reviewer assist" : "author"}</option>)}</select><small>Selecting an audit inherits its depth, topics, strictness, mode, and domain so topic-by-topic diffs have a shared baseline.</small></label>
        )}
      </section>

      <div className="launch-row">
        <div><span className="small-muted">AUDIT PLAN</span><strong>{topics.length} topics · {topics.length * 3} exchanges · {mode === "reviewer_assist" ? "reviewer draft" : "author revision brief"}</strong></div>
        <PButton type="button" onClick={onLaunch} icon="arrow-right" loading={launching} disabled={!preparedPaper || Boolean(topicError) || busy || auditRunning}>Launch {depth} audit</PButton>
      </div>
    </div>
  );
}

function ChoiceCards<Value extends string>({ label, value, options, onChange, disabled, compact = false }: { label: string; value: Value; options: ReadonlyArray<Choice<Value>>; onChange: (value: Value) => void; disabled: boolean; compact?: boolean }) {
  const name = useId();
  return <fieldset className={`option-group ${compact ? "compact" : ""}`} disabled={disabled}><legend>{label}</legend><div className="option-list">{options.map((option) => <label key={option.value} className={`option-card ${value === option.value ? "selected" : ""}`}><input className="option-radio visually-hidden" type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} /><span className="radio-dot" aria-hidden="true" /><span className="option-copy"><strong>{option.label}</strong><small>{option.description}</small></span>{value === option.value && <PIcon name="check" />}</label>)}</div></fieldset>;
}

export function RelevanceDialog({ prompt, loading, onChangeDocument, onProceed }: { prompt: RelevancePrompt; loading: boolean; onChangeDocument: () => void; onProceed: () => void }) {
  const changeButtonRef = useRef<HTMLElement>(null);
  const proceedButtonRef = useRef<HTMLElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(loading);
  useEffect(() => { loadingRef.current = loading; if (loading) dialogRef.current?.focus(); }, [loading]);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = window.requestAnimationFrame(() => changeButtonRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !loadingRef.current) { event.preventDefault(); onChangeDocument(); return; }
      if (event.key !== "Tab") return;
      const focusable = [changeButtonRef.current, proceedButtonRef.current].filter((element): element is HTMLElement => Boolean(element && !element.hasAttribute("disabled")));
      if (!focusable.length) { event.preventDefault(); dialogRef.current?.focus(); return; }
      const first = focusable[0]; const last = focusable.at(-1) || first;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => { window.cancelAnimationFrame(focusFrame); document.removeEventListener("keydown", handleKeyDown); previousFocus?.focus(); };
  }, [onChangeDocument]);
  return <div className="dialog-backdrop"><div ref={dialogRef} className="relevance-dialog" role="alertdialog" aria-modal="true" aria-labelledby="relevance-dialog-title" aria-describedby="relevance-dialog-description" aria-busy={loading} tabIndex={-1}><div className="dialog-icon"><PIcon name="warning" /></div><div><div className="section-kicker">RESEARCH VALIDATION</div><h2 id="relevance-dialog-title">This may not be a research paper.</h2><p id="relevance-dialog-description">{prompt.message}</p>{prompt.reason && prompt.reason !== prompt.message && <p className="dialog-reason"><strong>Why it was flagged:</strong> {prompt.reason}</p>}</div><div className="dialog-actions"><PButton ref={changeButtonRef} type="button" variant="secondary" onClick={onChangeDocument} disabled={loading}>Change document</PButton><PButton ref={proceedButtonRef} type="button" onClick={onProceed} loading={loading} disabled={loading}>Proceed anyway</PButton></div></div></div>;
}
