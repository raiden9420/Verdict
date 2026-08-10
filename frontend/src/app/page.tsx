"use client";

import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';
import {
  PButton,
  PButtonPure,
  PIcon,
  PTag,
  PText,
} from '@porsche-design-system/components-react';
import { startAudit, uploadPaper, UploadError } from "@/lib/api";
import { ROUND_TOPICS } from "@/types";
import { useSSE, type UseSSEResult } from "@/hooks/useSSE";
import DocumentViewer from "@/components/DocumentViewer";
import { ThemeToggle } from "@/components/ThemeToggle";
import type { AuditStatus, DebriefCard, ReproducibilitySignals, Turn, Verdict } from "@/types";

type View = 'setup' | 'arena';

const ACTIVE_AUDIT_KEY = "verdict_active_audit";
const TOTAL_EXCHANGES = 3;
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

interface ActiveAudit {
  auditId: string;
  paperId: string;
  filename: string;
}

interface PreparedPaper {
  paperId: string;
  filename: string;
  fileKey: string;
}

interface RelevancePrompt {
  message: string;
  reason?: string;
}

function readActiveAudit(): ActiveAudit | null {
  try {
    const raw = localStorage.getItem(ACTIVE_AUDIT_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<ActiveAudit>;
    if (
      typeof value.auditId !== "string" ||
      typeof value.paperId !== "string" ||
      typeof value.filename !== "string" ||
      !UUID_PATTERN.test(value.auditId) ||
      !UUID_PATTERN.test(value.paperId)
    ) {
      localStorage.removeItem(ACTIVE_AUDIT_KEY);
      return null;
    }
    return value as ActiveAudit;
  } catch {
    return null;
  }
}

function saveActiveAudit(audit: ActiveAudit): void {
  try {
    localStorage.setItem(ACTIVE_AUDIT_KEY, JSON.stringify(audit));
  } catch {
    // Persistence is a recovery aid; an unavailable storage API must not block an audit.
  }
}

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function userFacingError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) {
    return "The audit service could not be reached. Check your connection and try again.";
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function App() {
  const [view, setView] = useState<View>('setup');
  const [restoringAudit, setRestoringAudit] = useState(true);

  // Setup state
  const [topic, setTopic] = useState<string>(ROUND_TOPICS[0].slug);

  const [file, setFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [relevancePrompt, setRelevancePrompt] = useState<RelevancePrompt | null>(null);
  const [preparedPaper, setPreparedPaper] = useState<PreparedPaper | null>(null);
  const [loading, setLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Audit state
  const [activeAudit, setActiveAudit] = useState<ActiveAudit | null>(null);
  const [showDebrief, setShowDebrief] = useState(false);
  const auditStream = useSSE(activeAudit?.auditId ?? null);
  const auditRunning = Boolean(activeAudit && auditStream.status === "in_progress");

  useEffect(() => {
    const restoreTimer = window.setTimeout(() => {
      const restored = readActiveAudit();
      if (restored) {
        setActiveAudit(restored);
        setView("arena");
      }
      setRestoringAudit(false);
    }, 0);
    return () => window.clearTimeout(restoreTimer);
  }, []);

  const selectedDescription = useMemo(() => {
    const selectedTopic = ROUND_TOPICS.find((t) => t.slug === topic);
    return selectedTopic?.description || '';
  }, [topic]);

  const selectFile = (selected?: File) => {
    setUploadError(null);
    setRelevancePrompt(null);
    setPreparedPaper(null);
    if (!selected) return;

    const looksLikePdf =
      selected.type === "application/pdf" || selected.name.toLowerCase().endsWith(".pdf");
    if (!looksLikePdf) {
      setFile(null);
      setUploadError("Choose a PDF file. Other document formats cannot be audited.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    if (selected.size > MAX_UPLOAD_BYTES) {
      setFile(null);
      setUploadError("This PDF is larger than 20 MB. Choose a smaller research paper.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }

    setFile(selected);
  };

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    selectFile(event.target.files?.[0]);
  };

  const removeDocument = () => {
    setFile(null);
    setPreparedPaper(null);
    setRelevancePrompt(null);
    setUploadError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  const launchAudit = async (force = false) => {
    if (!file || !topic || loading) return;
    if (auditRunning) {
      setUploadError("An audit is already running. Let it finish before launching another one.");
      return;
    }

    setLoading(true);
    setUploadError(null);
    try {
      const selectedFileKey = fileKey(file);
      let acceptedPaper = preparedPaper?.fileKey === selectedFileKey
        ? preparedPaper
        : null;
      if (!acceptedPaper) {
        const paper = await uploadPaper(file, force);
        acceptedPaper = {
          paperId: paper.paper_id,
          filename: paper.filename,
          fileKey: selectedFileKey,
        };
        setPreparedPaper(acceptedPaper);
      }
      const audit = await startAudit(acceptedPaper.paperId, topic);
      const nextAudit = {
        auditId: audit.audit_id,
        paperId: acceptedPaper.paperId,
        filename: acceptedPaper.filename,
      };
      saveActiveAudit(nextAudit);
      setActiveAudit(nextAudit);
      setView('arena');
      setShowDebrief(false);
      setPreparedPaper(null);
      setRelevancePrompt(null);
    } catch (error) {
      if (!force && error instanceof UploadError && error.relevanceFailed) {
        setRelevancePrompt({ message: error.message, reason: error.reason });
      } else {
        if (force) setRelevancePrompt(null);
        setUploadError(userFacingError(error, "The audit could not be launched."));
      }
    } finally {
      setLoading(false);
    }
  };

  const changeDocument = useCallback(() => {
    setRelevancePrompt(null);
    setUploadError(null);
    setFile(null);
    setPreparedPaper(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    window.requestAnimationFrame(() => fileInputRef.current?.click());
  }, []);

  const beginNewAudit = () => {
    if (auditRunning) return;
    try {
      localStorage.removeItem(ACTIVE_AUDIT_KEY);
    } catch {
      // A blocked storage API should not prevent starting another audit.
    }
    setActiveAudit(null);
    setView('setup');
    setShowDebrief(false);
    removeDocument();
  };

  const workspacePaperName = view === 'arena'
    ? activeAudit?.filename
    : file?.name || activeAudit?.filename;

  return (
    <div className="app-shell">
      <header className="topbar" inert={relevancePrompt ? true : undefined}>
        <div className="brand-lockup">
          <span className="verdict-wordmark">Verdict</span>
          <span className="brand-divider" />
          <span className="product-name">ADVERSARIAL AUDIT</span>
        </div>
        <div className="topbar-meta">
          <span className="trust-mark"><PIcon name="check" /> Evidence-grounded</span>
          <ThemeToggle />
        </div>
      </header>

      <aside className="sidebar" inert={relevancePrompt ? true : undefined}>
        <div className="sidebar-label">Audit workspace</div>
        <nav className="side-nav" aria-label="Audit stages">
          <button
            type="button"
            className={`nav-item ${view === 'setup' ? 'active' : ''}`}
            onClick={() => setView('setup')}
            disabled={loading || restoringAudit}
            aria-current={view === 'setup' ? 'page' : undefined}
          >
            <span className="nav-index">01</span><span>Configure audit</span>
          </button>
          <button
            type="button"
            className={`nav-item ${view === 'arena' ? 'active' : ''}`}
            onClick={() => setView('arena')}
            disabled={!activeAudit || loading || restoringAudit}
            aria-current={view === 'arena' ? 'page' : undefined}
          >
            <span className="nav-index">02</span><span>Audit results</span>
            {view === 'arena' && auditStream.status === 'in_progress' && auditStream.connectionState === 'live' && <span className="nav-live">LIVE</span>}
          </button>
        </nav>
        <div className="sidebar-footer">
          <div className="mini-label">CURRENT PAPER</div>
          <div className="paper-mini"><PIcon name="document" /><span>{workspacePaperName || 'No paper selected'}</span></div>
          <div className="paper-mini-meta">{view === 'setup' && file ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : ''}</div>
        </div>
      </aside>

      <main className="main-content" inert={relevancePrompt ? true : undefined}>
        {restoringAudit && (
          <div className="workspace-loading" role="status">
            <span className="loading-mark" aria-hidden="true" />
            <strong>Restoring audit workspace</strong>
            <span>Checking for an active audit in this browser session.</span>
          </div>
        )}
        {!restoringAudit && view === 'setup' && (
          <SetupView
            file={file}
            topic={topic}
            setTopic={setTopic}
            selectedDescription={selectedDescription}
            onUpload={() => fileInputRef.current?.click()}
            onFileDrop={selectFile}
            onRemove={removeDocument}
            onLaunch={() => { void launchAudit(); }}
            loading={loading}
            uploadError={uploadError}
            auditRunning={auditRunning}
          />
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          onChange={handleFileSelect}
          className="file-input"
        />

        {!restoringAudit && view === 'arena' && activeAudit && (
          <ArenaView
            paperId={activeAudit.paperId}
            stream={auditStream}
            showDebrief={showDebrief}
            setShowDebrief={setShowDebrief}
            paperName={activeAudit.filename}
            onNewAudit={beginNewAudit}
          />
        )}
      </main>

      {relevancePrompt && (
        <RelevanceDialog
          prompt={relevancePrompt}
          loading={loading}
          onChangeDocument={changeDocument}
          onProceed={() => {
            void launchAudit(true);
          }}
        />
      )}
    </div>
  );
}

interface SetupViewProps {
  file: File | null;
  topic: string;
  setTopic: (topic: string) => void;
  selectedDescription: string;
  onUpload: () => void;
  onFileDrop: (file?: File) => void;
  onRemove: () => void;
  onLaunch: () => void;
  loading: boolean;
  uploadError: string | null;
  auditRunning: boolean;
}

function SetupView({
  file,
  topic,
  setTopic,
  selectedDescription,
  onUpload,
  onFileDrop,
  onRemove,
  onLaunch,
  loading,
  uploadError,
  auditRunning,
}: SetupViewProps) {
  const options = ROUND_TOPICS.map(
    (round): readonly [string, string, string] => [round.slug, round.name, round.description],
  );

  return <div className="setup-page" aria-busy={loading}>
    <span className="visually-hidden" role="status" aria-live="polite">{loading ? "Validating the paper and starting the audit." : ""}</span>
    <div className="eyebrow"><span className="eyebrow-line" /> RESEARCH INTEGRITY / NEW AUDIT</div>
    <div className="setup-heading"><div><h1>Put every claim<br /><em>under pressure.</em></h1><PText size="medium">Three evidence-grounded exchanges expose weak claims,<br />test the defense, and deliver a final verdict.</PText></div></div>

    <section className="paper-card">
      <div className="section-kicker">01 — Source document</div>
      {file ? (
        <div className="paper-row">
          <div className="paper-icon"><PIcon name="document" /></div>
          <div className="paper-details"><strong>{file.name}</strong><span>PDF · {(file.size / (1024 * 1024)).toFixed(1)} MB</span></div>
          <PTag variant="secondary" icon="check">Ready to audit</PTag>
          <PButtonPure icon="close" aria-label="Remove document" onClick={onRemove} disabled={loading} />
        </div>
      ) : (
        <button
          type="button"
          className="upload-zone"
          onClick={onUpload}
          disabled={loading}
          onDragOver={(event) => event.preventDefault()}
          onDrop={(event) => {
            event.preventDefault();
            onFileDrop(event.dataTransfer.files?.[0]);
          }}
          aria-describedby="upload-requirements"
        >
          <span className="upload-icon"><PIcon name="upload" /></span>
          <span><strong>Choose a research paper</strong><small id="upload-requirements">Drop a PDF here or browse · up to 20 MB</small></span>
        </button>
      )}
      {uploadError && <div className="setup-error" role="alert"><PIcon name="error-filled" /> <span>{uploadError}</span></div>}
    </section>

    <section className="config-section">
      <div className="section-kicker">02 — Choose the audit focus</div>
      <OptionGroup label="Research dimension" value={topic} setValue={setTopic} options={options} disabled={loading} />
      <div className="selection-note"><span className="pulse-dot" /> {selectedDescription}</div>
    </section>

    <div className="launch-row"><div><span className="small-muted">AUDIT PLAN</span><strong>3 exchanges · grounded verdicts · final debrief</strong></div><PButton type="button" onClick={onLaunch} icon="arrow-right" loading={loading} disabled={!file || !topic || loading || auditRunning}>Launch adversarial audit</PButton></div>
  </div>;
}

function OptionGroup<OptionValue extends string>({
  label,
  value,
  setValue,
  options,
  disabled = false,
}: {
  label: string;
  value: OptionValue;
  setValue: (value: OptionValue) => void;
  options: ReadonlyArray<readonly [OptionValue, string, string]>;
  disabled?: boolean;
}) {
  const groupName = useId();
  return <fieldset className="option-group" disabled={disabled}><legend>{label}</legend><div className="option-list">{options.map(([id, title, desc]) => <label key={id} className={`option-card ${value === id ? 'selected' : ''}`}><input className="option-radio visually-hidden" type="radio" name={groupName} value={id} checked={value === id} onChange={() => setValue(id)} /><span className="radio-dot" aria-hidden="true" /><span className="option-copy"><strong>{title}</strong><small>{desc}</small></span>{value === id && <PIcon name="check" />}</label>)}</div></fieldset>;
}

function RelevanceDialog({
  prompt,
  loading,
  onChangeDocument,
  onProceed,
}: {
  prompt: RelevancePrompt;
  loading: boolean;
  onChangeDocument: () => void;
  onProceed: () => void;
}) {
  const changeButtonRef = useRef<HTMLElement>(null);
  const proceedButtonRef = useRef<HTMLElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(loading);

  useEffect(() => {
    loadingRef.current = loading;
    if (loading) dialogRef.current?.focus();
  }, [loading]);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement
      ? document.activeElement
      : null;
    const focusFrame = window.requestAnimationFrame(() => changeButtonRef.current?.focus());
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !loadingRef.current) {
        event.preventDefault();
        onChangeDocument();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = [changeButtonRef.current, proceedButtonRef.current]
        .filter((element): element is HTMLElement => Boolean(element && !element.hasAttribute("disabled")));
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      window.cancelAnimationFrame(focusFrame);
      document.removeEventListener("keydown", handleKeyDown);
      previousFocus?.focus();
    };
  }, [onChangeDocument]);

  return (
    <div className="dialog-backdrop">
      <div
        ref={dialogRef}
        className="relevance-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="relevance-dialog-title"
        aria-describedby="relevance-dialog-description"
        aria-busy={loading}
        tabIndex={-1}
      >
        <div className="dialog-icon"><PIcon name="warning" /></div>
        <div>
          <div className="section-kicker">RESEARCH VALIDATION</div>
          <h2 id="relevance-dialog-title">This may not be a research paper.</h2>
          <p id="relevance-dialog-description">{prompt.message}</p>
          {prompt.reason && prompt.reason !== prompt.message && (
            <p className="dialog-reason"><strong>Why it was flagged:</strong> {prompt.reason}</p>
          )}
        </div>
        <div className="dialog-actions">
          <PButton ref={changeButtonRef} type="button" variant="secondary" onClick={onChangeDocument} disabled={loading}>Change document</PButton>
          <PButton ref={proceedButtonRef} type="button" onClick={onProceed} loading={loading} disabled={loading}>Proceed anyway</PButton>
        </div>
      </div>
    </div>
  );
}

interface ArenaViewProps {
  paperId: string;
  paperName: string;
  stream: UseSSEResult;
  showDebrief: boolean;
  setShowDebrief: (show: boolean) => void;
  onNewAudit: () => void;
}

function ArenaView({ paperId, paperName, stream, showDebrief, setShowDebrief, onNewAudit }: ArenaViewProps) {
  const {
    turns,
    verdicts,
    debrief,
    processMessage,
    status,
    connectionState,
    auditError,
    transportError,
  } = stream;

  const displayedTurns = useMemo(() => selectDisplayTurns(turns), [turns]);
  const feedRef = useRef<HTMLDivElement>(null);
  const followFeedRef = useRef(true);
  const adjudicatedExchanges = useMemo(
    () => new Set(verdicts.map((verdict) => verdict.exchange_number)).size,
    [verdicts],
  );
  const latestExchange = turns.reduce(
    (latest, turn) => Math.max(latest, turn.exchange_number),
    1,
  );
  const activeExchange = status === "completed"
    ? TOTAL_EXCHANGES
    : Math.min(TOTAL_EXCHANGES, Math.max(latestExchange, adjudicatedExchanges + 1));

  const highlightedPages = useMemo(() => {
    const latestCitedTurn = [...displayedTurns]
      .reverse()
      .find((turn) => (turn.content.cited_chunk_ids?.length ?? 0) > 0);
    if (!latestCitedTurn) return [];

    const citedIds = latestCitedTurn.content.cited_chunk_ids || [];
    const pages = new Set<number>();
    for (const validator of turns.filter((turn) => turn.agent_type === "validator")) {
      const validations = [
        ...(validator.content.attacker_validations || []),
        ...(validator.content.defender_validations || []),
      ];
      for (const validation of validations) {
        if (
          validation.chunk_id &&
          citedIds.includes(validation.chunk_id) &&
          typeof validation.page_number === "number"
        ) {
          pages.add(validation.page_number);
        }
      }
    }
    return [...pages].sort((left, right) => left - right);
  }, [displayedTurns, turns]);

  useEffect(() => {
    if (!followFeedRef.current || !feedRef.current) return;
    const frame = window.requestAnimationFrame(() => {
      feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [displayedTurns.length, processMessage]);

  const statusLabel = status === "completed"
    ? "Completed"
    : status === "error"
      ? "Failed"
      : connectionState === "live"
        ? "Live"
        : connectionState === "connecting"
          ? "Connecting"
          : connectionState === "reconnecting"
            ? "Reconnecting"
            : connectionState === "polling"
              ? "Syncing"
              : "Starting";
  const statusTone = status === "completed"
    ? "complete"
    : status === "error"
      ? "failed"
      : connectionState === "live"
        ? "live"
        : "connecting";

  return (
    <div className="arena-page">
      <p className="visually-hidden" role="status" aria-live="polite">
        {status === "completed"
          ? debrief
            ? "Audit complete. All three exchange verdicts and the final debrief are available."
            : transportError
              ? "Audit complete. All three exchange verdicts are available, but the final debrief could not be loaded."
              : "Audit complete. All three exchange verdicts are available. The final debrief is loading."
          : status === "error"
            ? "The audit stopped before completion."
            : `${statusLabel}. ${adjudicatedExchanges} of ${TOTAL_EXCHANGES} exchanges adjudicated.`}
      </p>
      <div className="page-header">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> AUDIT / {statusLabel.toUpperCase()}</div>
          <h1>Adversarial arena</h1>
          <p className="page-subtitle"><PIcon name="document" /> {paperName}</p>
        </div>
      </div>

      <div className="round-strip">
        <div
          className="round-progress"
          role="progressbar"
          aria-label="Audit exchanges adjudicated"
          aria-valuemin={0}
          aria-valuemax={TOTAL_EXCHANGES}
          aria-valuenow={adjudicatedExchanges}
          aria-valuetext={`${adjudicatedExchanges} of ${TOTAL_EXCHANGES} exchanges adjudicated`}
        >
          {Array.from({ length: TOTAL_EXCHANGES }, (_, index) => {
            const exchange = index + 1;
            const progressClass = exchange <= adjudicatedExchanges
              ? "progress-complete"
              : exchange === activeExchange && status === "in_progress"
                ? "progress-active"
                : "";
            return <span key={exchange} className={progressClass} />;
          })}
        </div>
        <div>
          <strong>EXCHANGE {String(activeExchange).padStart(2, "0")} OF 03</strong>
          <span>{adjudicatedExchanges} adjudicated</span>
        </div>
        <span className={`audit-status ${statusTone}`}><i aria-hidden="true" /> {statusLabel}</span>
      </div>

      {auditError && (
        <div className="pipeline-alert error" role="alert">
          <PIcon name="error-filled" />
          <div><strong>Audit stopped</strong><span>{auditError}</span></div>
        </div>
      )}
      {transportError && status !== "error" && (
        <div className="pipeline-alert recovery" role="status">
          <PIcon name="information" />
          <div><strong>{status === "completed" ? "Final debrief unavailable" : "Live updates delayed"}</strong><span>{transportError}</span></div>
        </div>
      )}

      <div className="arena-grid">
        <section className="document-panel">
          <DocumentViewer key={paperId} paperId={paperId} highlightedPages={highlightedPages} />
        </section>

        <section className="debate-panel">
          <div className="panel-top">
            <span className="panel-title">
              {status === "in_progress" ? <span className="live-bars" aria-hidden="true"><i /><i /><i /></span> : <PIcon name={status === "completed" ? "check" : "warning"} />}
              {status === "in_progress" ? "LIVE AUDIT TRANSCRIPT" : "AUDIT TRANSCRIPT"}
            </span>
            <span>{statusLabel.toUpperCase()}</span>
          </div>
          <div
            ref={feedRef}
            className="agent-feed"
            role="log"
            aria-label="Audit transcript"
            aria-live="polite"
            tabIndex={0}
            onScroll={(event) => {
              const feed = event.currentTarget;
              followFeedRef.current = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80;
            }}
          >
            {processMessage && (
              <div className="process-update" role="status">
                <PIcon name="globe" /><span>{processMessage}</span>
              </div>
            )}

            {displayedTurns.map((turn) => (
              <AgentMessage
                key={turn.id || `${turn.exchange_number}-${turn.sequence}-${turn.agent_type}`}
                turn={turn}
                allTurns={turns}
                role={turn.agent_type.toUpperCase()}
                time={turn.created_at ? new Date(turn.created_at).toLocaleTimeString() : ''}
                tone={turn.agent_type}
                text={turn.content.critique_text || turn.content.rebuttal_text || turn.content.rationale || turn.content.claim_summary || ''}
                cite={turn.content.cited_chunk_ids?.length ? 'In-document citations provided' : ''}
              />
            ))}

            {status === 'in_progress' && (
              <div className="typing"><span className="typing-avatar">AI</span><span>Agent is formulating a response</span><i /><i /><i /></div>
            )}
          </div>
        </section>
      </div>

      <ExchangeVerdicts verdicts={verdicts} status={status} />
      <DebriefPanel
        debrief={debrief}
        status={status}
        open={showDebrief}
        onToggle={() => setShowDebrief(!showDebrief)}
      />

      <div className="arena-bottom">
        <span><PIcon name="clock" /> {status === "completed" ? "All three exchanges adjudicated" : status === "error" ? "Audit stopped before completion" : `Exchange ${activeExchange} is running`}</span>
        {status !== "in_progress" && <PButton type="button" variant="secondary" icon="reset" onClick={onNewAudit}>Start a new audit</PButton>}
      </div>
    </div>
  );
}

function ReproducibilityChecklist({ signals }: { signals: ReproducibilitySignals }) {
  const checks = [
    ["Code / repository", signals.code_available, signals.code_details],
    ["Dataset availability", signals.data_available, signals.data_details],
    ["Hyperparameters", signals.hyperparameters_disclosed, signals.hyperparameter_details],
    ["Compute / hardware", signals.compute_disclosed, signals.compute_details],
    ["Random seed", signals.seed_disclosed, signals.seed_details],
  ] as const;

  return (
    <div className="reproducibility-checklist">
      <span className="small-muted">REPRODUCIBILITY CHECKLIST</span>
      <div className="reproducibility-grid">
        {checks.map(([label, disclosed, details]) => (
          <div key={label}>
            <span>{label}</span>
            <strong className={disclosed ? "disclosed" : "missing"}>{disclosed ? "Disclosed" : "Missing"}</strong>
            {details?.length ? <small>{details.slice(0, 3).join(', ')}</small> : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function DebriefPanel({
  debrief,
  status,
  open,
  onToggle,
}: {
  debrief: DebriefCard | null;
  status: AuditStatus;
  open: boolean;
  onToggle: () => void;
}) {
  const fallback = status === "error"
    ? "No final debrief was produced because the audit failed."
    : "The final debrief will appear after all three exchanges are adjudicated.";

  return (
    <section className={`debrief-card ${open ? 'open' : ''}`}>
      <button type="button" className="debrief-header" onClick={onToggle} aria-expanded={open} aria-controls="final-debrief-content">
        <span><PIcon name="chart" /><strong>Final debrief</strong>{debrief ? <PTag variant="success">Final</PTag> : status === "error" ? <PTag variant="error">Unavailable</PTag> : <PTag variant="warning">Pending</PTag>}</span>
        <span className="debrief-toggle">{open ? 'Collapse' : 'Expand'} <PIcon name={open ? 'arrow-up' : 'arrow-down'} /></span>
      </button>
      {open && (
        <div className="debrief-body" id="final-debrief-content" role="region" aria-label="Final audit debrief">
          <div className="synthesis"><span className="small-muted">FINAL EXECUTIVE SYNTHESIS</span><p>{debrief?.executive_synthesis || fallback}</p></div>
          {debrief?.reproducibility_checklist && <ReproducibilityChecklist signals={debrief.reproducibility_checklist} />}
          <div className="debrief-columns">
            <div>
              <span className="debrief-label strength">SOLIDIFIED STRENGTHS</span>
              {debrief?.solidified_strengths?.map((strength, index) => <p key={index}><PIcon name="check" /> {strength}</p>)}
            </div>
            <div>
              <span className="debrief-label weakness">ACTIONABLE WEAKNESSES</span>
              {debrief?.actionable_weaknesses?.map((weakness, index) => <p key={index}><PIcon name="error-filled" /> {weakness}</p>)}
            </div>
            <div>
              <span className="debrief-label contested">CONTESTED POINTS</span>
              {debrief?.contested_points?.map((point, index) => <p key={index}><PIcon name="information" /> {point}</p>)}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function selectDisplayTurns(turns: Turn[]): Turn[] {
  const exchanges = new Map<number, Turn[]>();
  for (const turn of turns) {
    if (turn.exchange_number < 1 || turn.exchange_number > TOTAL_EXCHANGES) continue;
    const exchangeTurns = exchanges.get(turn.exchange_number) || [];
    exchangeTurns.push(turn);
    exchanges.set(turn.exchange_number, exchangeTurns);
  }

  const selected: Turn[] = [];
  for (const exchangeTurns of exchanges.values()) {
    const ordered = [...exchangeTurns].sort((left, right) => left.sequence - right.sequence);
    const attacker = ordered.filter((turn) => turn.agent_type === "attacker").at(-1);
    const defender = ordered
      .filter(
        (turn) =>
          turn.agent_type === "defender" &&
          (!attacker || turn.sequence > attacker.sequence),
      )
      .at(-1);
    const referee = ordered.filter((turn) => turn.agent_type === "referee").at(-1);
    if (attacker) selected.push(attacker);
    if (defender) selected.push(defender);
    if (referee) selected.push(referee);
  }

  return selected.sort(
    (left, right) =>
      left.exchange_number - right.exchange_number || left.sequence - right.sequence,
  );
}

function confidencePercent(confidence?: number): string {
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) return "—";
  const percentage = confidence <= 1 ? confidence * 100 : confidence;
  return `${Math.round(Math.max(0, Math.min(100, percentage)))}%`;
}

function ExchangeVerdicts({ verdicts, status }: { verdicts: Verdict[]; status: AuditStatus }) {
  const byExchange = new Map<number, Verdict>();
  for (const verdict of verdicts) {
    if (verdict.exchange_number >= 1 && verdict.exchange_number <= TOTAL_EXCHANGES) {
      byExchange.set(verdict.exchange_number, verdict);
    }
  }

  return (
    <section className="exchange-verdicts" aria-labelledby="exchange-verdicts-title">
      <div className="verdicts-header">
        <div>
          <span className="section-kicker">THREE-EXCHANGE ADJUDICATION</span>
          <h2 id="exchange-verdicts-title">Exchange verdicts</h2>
        </div>
        <span>{byExchange.size} / {TOTAL_EXCHANGES} final</span>
      </div>
      {status === "completed" && byExchange.size !== TOTAL_EXCHANGES && (
        <div className="pipeline-alert error" role="alert">
          <PIcon name="error-filled" />
          <div><strong>Incomplete final result</strong><span>The audit completed with {byExchange.size} of {TOTAL_EXCHANGES} required verdicts.</span></div>
        </div>
      )}
      <div className="verdict-grid">
        {Array.from({ length: TOTAL_EXCHANGES }, (_, index) => {
          const exchangeNumber = index + 1;
          const verdict = byExchange.get(exchangeNumber);
          const verdictClass = verdict?.verdict_type.toLowerCase().replaceAll("_", "-") || "pending";
          return (
            <article className={`verdict-card ${verdictClass}`} key={exchangeNumber}>
              <div className="verdict-card-header">
                <span>EXCHANGE {String(exchangeNumber).padStart(2, "0")}</span>
                {verdict ? (
                  <PTag variant={verdict.verdict_type === "SOLIDIFIED" ? "success" : verdict.verdict_type === "ACTIONABLE_FLAW" ? "error" : "warning"}>
                    {verdict.verdict_type.replaceAll("_", " ")}
                  </PTag>
                ) : <PTag variant="secondary">PENDING</PTag>}
              </div>
              {verdict ? (
                <>
                  <strong>{verdict.claim_summary || "Adjudicated claim"}</strong>
                  <p>{verdict.rationale || "No rationale was returned."}</p>
                  <small>Confidence {confidencePercent(verdict.confidence)}</small>
                </>
              ) : (
                <p>Waiting for the Attacker, Defender, validation, and Referee ruling.</p>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function canonicalTitle(title?: string): string {
  return (title || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function AgentMessage({ turn, allTurns, role, time, tone, text, cite }: { turn: Turn; allTurns: Turn[]; role: string; time: string; tone: string; text: string; cite: string }) {
  const validatorTurn = [...allTurns]
    .filter(
      (candidate) =>
        candidate.agent_type === "validator" &&
        candidate.exchange_number === turn.exchange_number &&
        candidate.sequence > turn.sequence,
    )
    .sort((left, right) => left.sequence - right.sequence)[0];
  const externalValidations = validatorTurn?.content.external_validations || [];

  return (
    <article className={`agent-message ${tone}`}>
      <div className="message-meta">
        <span className="agent-avatar">{role[0]}</span>
        <strong>EXCHANGE {turn.exchange_number} · {role}</strong>
        <span>{time}</span>
        <PTag variant={tone === 'attacker' ? 'error' : tone === 'defender' ? 'success' : 'secondary'}>
          {tone === 'referee' ? 'RULING' : 'ARGUMENT'}
        </PTag>
      </div>

      <p>{text}</p>

      {turn?.content?.external_search_performed && (
        <div className="literature-search-note">
          <PIcon name="globe" />
          <span>External literature search executed across Semantic Scholar, arXiv, & OpenAlex ({turn.content.external_candidate_count ?? 0} candidates retrieved)</span>
        </div>
      )}

      {turn?.content?.external_citations && turn.content.external_citations.length > 0 && (
        <div className="external-literature">
          <span className="external-literature-title">
            <PIcon name="linked" /> Cited literature
          </span>
          <div className="external-citation-list">
            {turn.content.external_citations.map((ext, idx) => {
              const normalizedTitle = canonicalTitle(ext.title);
              const valMatch = externalValidations.find(
                (validation) =>
                  validation.citation_index === idx ||
                  (normalizedTitle.length > 0 && canonicalTitle(validation.title) === normalizedTitle),
              );
              const validationStatus = valMatch?.valid === true
                ? "verified"
                : valMatch?.valid === false
                  ? "invalid"
                  : "pending";
              return (
                <div key={`${ext.title}-${idx}`} className="external-citation-item">
                  <div className="external-citation-copy">
                    <div className="external-citation-name">
                      {ext.url ? <a href={ext.url} target="_blank" rel="noopener noreferrer">{ext.title}<PIcon name="external" /></a> : ext.title}
                    </div>
                    <div className="external-citation-meta">
                      {ext.authors?.join(', ')} {ext.year ? `(${ext.year})` : ''} {ext.source ? `• ${ext.source}` : ''}
                      {ext.similarity_score != null ? ` • Overlap Similarity: ${Math.round(ext.similarity_score * 100)}%` : ''}
                    </div>
                  </div>
                  <span className={`validation-badge ${validationStatus}`}>
                    {validationStatus === "verified" ? "Verified" : validationStatus === "invalid" ? "Unverified" : "Pending"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {cite && <div className="citation"><PIcon name="linked" /> {cite}</div>}
    </article>
  );
}
