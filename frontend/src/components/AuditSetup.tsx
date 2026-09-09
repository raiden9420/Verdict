"use client";

import { useEffect, useId, useRef } from "react";
import { Button as PButton, Icon as PIcon } from "@/components/ui";
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
  onOpenLibrary,
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
  onOpenLibrary: () => void;
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
    <div className="setup-page" aria-busy={busy}>
      <header className="page-header"><div><span className="section-kicker">Research workspace / New review</span><h1>Give your paper a second reading.</h1><p className="page-subtitle">Choose the questions that matter. Leave with findings you can trace to the evidence.</p></div></header>
      <div className="setup-layout">
        <div className="setup-form">
          {revisionBase && <section className="revision-banner"><div><PIcon name="compare" /><span><small>Revision of version {revisionBase.version_number}</small><strong>{revisionBase.filename}</strong></span></div><button className="text-link" type="button" onClick={onCancelRevision} disabled={busy || Boolean(preparedPaper)}>{preparedPaper ? "Version link saved" : "Cancel revision"}</button></section>}
          <section className="setup-section" aria-labelledby="source-title">
            <div className="section-heading"><span className="step-number">01</span><div><h2 id="source-title">Choose your manuscript</h2><p>A text-based PDF, up to 20 MB and 40 pages.</p></div></div>
            {file || preparedPaper ? <div className="paper-row"><span className="paper-icon"><PIcon name="document" /></span><div className="paper-details"><strong>{preparedPaper?.filename || file?.name}</strong><span>{preparedPaper ? `Ready to review · Version ${preparedPaper.versionNumber}` : `PDF · ${((file?.size || 0) / (1024 * 1024)).toFixed(1)} MB`}</span></div><button className="icon-button" type="button" aria-label="Remove selected document" onClick={onRemove} disabled={busy}><PIcon name="close" /></button></div> : <button type="button" className="upload-zone" onClick={onUpload} disabled={busy} onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (!busy) onFileDrop(event.dataTransfer.files?.[0]); }}><PIcon name="upload" /><strong>{revisionBase ? "Choose the revised manuscript" : "Drop your paper here"}</strong><span>or browse for a PDF</span></button>}
            {!file && !preparedPaper && <div className="upload-alternative"><span>Already prepared a paper?</span><button className="text-link" type="button" onClick={onOpenLibrary} disabled={busy}>Choose from library <PIcon name="arrow-right" /></button></div>}
            {file && !preparedPaper && <div className="prepare-row"><p>{preparing ? "Reading the manuscript, identifying its field, and preparing source evidence. This can take a few minutes." : "Prepare the document once. You can reuse it for future reviews."}</p><PButton variant="secondary" loading={preparing} disabled={busy} onClick={onPrepare}>{preparing ? "Preparing paper…" : "Prepare paper"}</PButton></div>}
            {preparedPaper && <div className="prepared-summary" role="status"><PIcon name="check" /><span><strong>{DOMAIN_LABELS[preparedPaper.detectedDomain]}</strong>{preparedPaper.pageCount ? ` · ${preparedPaper.pageCount} pages` : ""}<small>{preparedPaper.relevanceOverridden ? "Prepared with your research-paper classification override." : "Source saved. Ready for an evidence-based review."}</small></span></div>}
            {error && <div className="setup-error" role="alert"><PIcon name="error-filled" /><span>{error}</span></div>}
          </section>
          <section className="setup-section" aria-labelledby="coverage-title">
            <div className="section-heading"><span className="step-number">02</span><div><h2 id="coverage-title">Set the scope</h2><p>Every selected topic receives three evidence-checked exchanges.</p></div></div>
            <ChoiceCards label="Coverage" value={depth} options={DEPTH_OPTIONS} onChange={onDepthChange} disabled={busy} compact />
            <fieldset className="topic-checkbox-group" disabled={busy}><legend>Select {depthConfig.minimumTopics}–{depthConfig.maximumTopics} topics <span>{topics.length} selected</span></legend><div className="topic-checkbox-grid">{ROUND_TOPICS.map(topic => { const selected = topics.includes(topic.slug); const atMaximum = topics.length >= depthConfig.maximumTopics; return <label key={topic.slug} className={`topic-checkbox ${selected ? "selected" : ""}`}><input type="checkbox" checked={selected} disabled={!selected && atMaximum} onChange={() => onTopicToggle(topic.slug)} /><span><strong>{topic.name}</strong><small>{topic.description}</small></span></label>; })}</div></fieldset>
            {topicError && <p className="selection-note invalid" role="status">{topicError}</p>}
            <p className="selection-note">{topics.length === depthConfig.maximumTopics ? "Uncheck a topic to select another. " : ""}Coverage changes the breadth of review; it does not guarantee that every issue will be found.</p>
          </section>
          <section className="setup-section" aria-labelledby="perspective-title">
            <div className="section-heading"><span className="step-number">03</span><div><h2 id="perspective-title">Choose the review perspective</h2><p>The evidence standards stay the same across all settings.</p></div></div>
            <ChoiceCards label="Scrutiny" value={strictness} options={STRICTNESS_OPTIONS} onChange={onStrictnessChange} disabled={busy} compact />
            <ChoiceCards label="Report for" value={mode} options={MODE_OPTIONS} onChange={onModeChange} disabled={busy} compact />
            <label className="domain-select"><span>Research field</span><select value={domainSelection} onChange={event => onDomainChange(event.target.value as DomainSelection)} disabled={busy}>{DOMAIN_OPTIONS.map(option => <option key={option.value} value={option.value}>{option.value === "auto" && preparedPaper ? `Detected: ${DOMAIN_LABELS[preparedPaper.detectedDomain]}` : option.label}</option>)}</select><small>The field guides the questions asked, including relevant methods and reproducibility details.</small></label>
            {revisionBase && <label className="domain-select"><span>Compare with an earlier review</span><select value={comparisonAuditId || ""} onChange={event => onComparisonChange(event.target.value || null)} disabled={busy}><option value="">Use the nearest compatible earlier review</option>{comparisonAudits.map(audit => <option key={audit.audit_id} value={audit.audit_id}>{new Date(audit.created_at).toLocaleDateString()} · {audit.round_topics.length} topics</option>)}</select><small>Choosing a review reuses its settings so the same topics can be compared.</small></label>}
          </section>
        </div>
        <aside className="review-plan" aria-labelledby="plan-title">
          <span className="section-kicker">Your review plan</span><h2 id="plan-title">A focused test of the evidence.</h2>
          <dl><div><dt>Manuscript</dt><dd>{preparedPaper?.filename || file?.name || "No paper selected"}</dd></div><div><dt>Coverage</dt><dd>{topics.length} topics · {topics.length * 3} exchanges</dd></div><div><dt>Scrutiny</dt><dd>{STRICTNESS_OPTIONS.find(option => option.value === strictness)?.label}</dd></div><div><dt>Deliverable</dt><dd>{mode === "author" ? "Author revision brief" : "Reviewer draft"}</dd></div></dl>
          <PButton onClick={onLaunch} icon="arrow-right" loading={launching} disabled={!preparedPaper || Boolean(topicError) || busy || auditRunning}>{launching ? "Starting review…" : "Start review"}</PButton>
          <p className="plan-hint" role="status">{auditRunning ? "A review is running. Its saved results remain available while you plan the next one." : !preparedPaper ? "Prepare a paper to start." : `${topics.length * 3} exchanges plus synthesis. Broader reviews take longer; you can return to saved results from the library.`}</p>
          <div className="plan-method"><h3>What you’ll receive</h3><ul><li>Findings with source passages</li><li>Challenges, responses, and rulings</li><li>A summary for each topic</li><li>A downloadable revision brief</li></ul></div>
          <details className="processing-note"><summary>Before you upload</summary><p>Manuscript text is sent to external AI providers to prepare and review it. Literature checks also send search queries derived from the paper to scholarly indexes. Only upload material you are permitted to share with these services.</p><p>Scanned PDFs and image-only figures are not read. Citation checks assess title existence and broad relevance, not claim-to-source accuracy. Verdict supports your judgment; it does not certify a paper.</p></details>
        </aside>
      </div>
    </div>
  );
}

function ChoiceCards<Value extends string>({ label, value, options, onChange, disabled, compact = false }: { label: string; value: Value; options: ReadonlyArray<Choice<Value>>; onChange: (value: Value) => void; disabled: boolean; compact?: boolean }) {
  const name = useId();
  return <fieldset className={`option-group option-count-${options.length} ${compact ? "compact" : ""}`} disabled={disabled}><legend>{label}</legend><div className="option-list">{options.map((option) => <label key={option.value} className={`option-card ${value === option.value ? "selected" : ""}`}><input className="option-radio visually-hidden" type="radio" name={name} value={option.value} checked={value === option.value} onChange={() => onChange(option.value)} /><span className="radio-dot" aria-hidden="true" /><span className="option-copy"><strong>{option.label}</strong><small>{option.description}</small></span>{value === option.value && <PIcon name="check" />}</label>)}</div></fieldset>;
}

export function RelevanceDialog({ prompt, loading, onChangeDocument, onProceed }: { prompt: RelevancePrompt; loading: boolean; onChangeDocument: () => void; onProceed: () => void }) {
  const changeButtonRef = useRef<HTMLButtonElement>(null);
  const proceedButtonRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(loading);
  useEffect(() => { loadingRef.current = loading; if (loading) dialogRef.current?.focus(); }, [loading]);
  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusFrame = window.requestAnimationFrame(() => changeButtonRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !loadingRef.current) { event.preventDefault(); onChangeDocument(); return; }
      if (event.key !== "Tab") return;
      const focusable = [changeButtonRef.current, proceedButtonRef.current].filter((element): element is HTMLButtonElement => Boolean(element && !element.hasAttribute("disabled")));
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
