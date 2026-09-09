"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Brand, Icon as PIcon } from "@/components/ui";
import { readWorkspaceLocation, workspaceUrl, type WorkspaceView } from "@/lib/workspace-navigation";
import { AuthLoadingScreen, AuthScreen, PasswordRecoveryScreen } from "@/components/AuthScreen";
import { useAuth } from "@/components/AuthProvider";
import { AuditArena } from "@/components/AuditArena";
import { AuditSetup, RelevanceDialog, type RelevancePrompt } from "@/components/AuditSetup";
import { FinalReportView } from "@/components/FinalReportView";
import { LibraryView } from "@/components/LibraryView";
import { ThemeToggle } from "@/components/ThemeToggle";
import { useSSE } from "@/hooks/useSSE";
import {
  fetchAudits,
  fetchPapers,
  startAudit,
  uploadPaper,
  UploadError,
} from "@/lib/api";
import {
  defaultTopicsForDepth,
  effectiveDomain,
  topicSelectionError,
} from "@/lib/audit-config";
import {
  activeAuditFromHistory,
  paperForAudit,
  type ActiveAudit,
  type PreparedPaper,
} from "@/lib/audit-workspace";
import type {
  AuditDepth,
  AuditMode,
  AuditSummary,
  Domain,
  DomainSelection,
  PaperSummary,
  StrictnessLevel,
} from "@/types";

type View = WorkspaceView;

const ACTIVE_AUDIT_KEY = "verdict_active_audit";
const MAX_UPLOAD_BYTES = 20 * 1024 * 1024;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function activeAuditKey(userId: string): string {
  return `${ACTIVE_AUDIT_KEY}:${userId}`;
}

function readActiveAudit(userId: string): ActiveAudit | null {
  try {
    const storageKey = activeAuditKey(userId);
    const raw = localStorage.getItem(storageKey);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<ActiveAudit>;
    if (
      typeof value.auditId !== "string" ||
      typeof value.paperId !== "string" ||
      typeof value.filename !== "string" ||
      !UUID_PATTERN.test(value.auditId) ||
      !UUID_PATTERN.test(value.paperId)
    ) {
      localStorage.removeItem(storageKey);
      return null;
    }
    return {
      auditId: value.auditId,
      paperId: value.paperId,
      filename: value.filename,
      roundIds: Array.isArray(value.roundIds) ? value.roundIds.filter((item): item is string => typeof item === "string") : [],
      roundTopics: Array.isArray(value.roundTopics) ? value.roundTopics.filter((item): item is string => typeof item === "string") : [],
      strictnessLevel: isStrictness(value.strictnessLevel) ? value.strictnessLevel : "standard",
      depth: isDepth(value.depth) ? value.depth : "fast",
      mode: isMode(value.mode) ? value.mode : "author",
      domain: isDomain(value.domain) ? value.domain : "other",
      versionNumber: typeof value.versionNumber === "number" ? value.versionNumber : 1,
      parentPaperId: typeof value.parentPaperId === "string" ? value.parentPaperId : null,
      compareToAuditId: typeof value.compareToAuditId === "string" ? value.compareToAuditId : null,
    };
  } catch {
    return null;
  }
}

function saveActiveAudit(userId: string, audit: ActiveAudit): void {
  try {
    localStorage.setItem(activeAuditKey(userId), JSON.stringify(audit));
  } catch {
    // Persistence is a recovery aid; an unavailable storage API must not block an audit.
  }
}

function isStrictness(value: unknown): value is StrictnessLevel {
  return value === "constructive" || value === "standard" || value === "brutal";
}

function isDepth(value: unknown): value is AuditDepth {
  return value === "fast" || value === "deep" || value === "exhaustive";
}

function isMode(value: unknown): value is AuditMode {
  return value === "author" || value === "reviewer_assist";
}

function isDomain(value: unknown): value is Domain {
  return value === "ml_cs" || value === "life_sciences" || value === "social_science" || value === "other";
}

function fileKey(file: File): string {
  return `${file.name}:${file.size}:${file.lastModified}`;
}

function userFacingError(error: unknown, fallback: string): string {
  if (error instanceof TypeError) return "The audit service could not be reached. Check your connection and try again.";
  return error instanceof Error && error.message ? error.message : fallback;
}

export default function App() {
  const { user, loading, signOut, passwordRecovery } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  if (loading) return <AuthLoadingScreen />;
  if (!user) return <AuthScreen />;
  if (passwordRecovery) return <PasswordRecoveryScreen />;

  const handleSignOut = async () => {
    if (signingOut) return;
    setSigningOut(true);
    setSignOutError(null);
    try {
      await signOut();
    } catch (error) {
      setSignOutError(userFacingError(error, "Could not log out. Please try again."));
    } finally {
      setSigningOut(false);
    }
  };

  return <AuthenticatedApp key={user.id} userId={user.id} userEmail={user.email || "Signed in"} signingOut={signingOut} signOutError={signOutError} onSignOut={() => { void handleSignOut(); }} />;
}

function AuthenticatedApp({ userId, userEmail, signingOut, signOutError, onSignOut }: { userId: string; userEmail: string; signingOut: boolean; signOutError: string | null; onSignOut: () => void }) {
  const [view, setViewState] = useState<View>("setup");
  const [restoringAudit, setRestoringAudit] = useState(true);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [audits, setAudits] = useState<AuditSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);

  const [file, setFile] = useState<File | null>(null);
  const [preparedPaper, setPreparedPaper] = useState<PreparedPaper | null>(null);
  const [revisionBase, setRevisionBase] = useState<PaperSummary | null>(null);
  const [comparisonAuditId, setComparisonAuditId] = useState<string | null>(null);
  const [relevancePrompt, setRelevancePrompt] = useState<RelevancePrompt | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [launching, setLaunching] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [strictness, setStrictness] = useState<StrictnessLevel>("standard");
  const [depth, setDepth] = useState<AuditDepth>("fast");
  const [topics, setTopics] = useState<string[]>(() => defaultTopicsForDepth("fast"));
  const [mode, setMode] = useState<AuditMode>("author");
  const [domainSelection, setDomainSelection] = useState<DomainSelection>("auto");

  const [activeAudit, setActiveAudit] = useState<ActiveAudit | null>(null);
  const auditStream = useSSE(
    activeAudit?.auditId ?? null,
    activeAudit?.compareToAuditId,
  );
  const auditRunning = Boolean(activeAudit && auditStream.status === "in_progress");
  const activeAuditRef = useRef(activeAudit);
  const historyRequestRef = useRef(0);
  const prepareGuard = useRef(false);
  const launchGuard = useRef(false);
  useEffect(() => { activeAuditRef.current = activeAudit; }, [activeAudit]);

  const setView = useCallback((next: View, auditId?: string | null, replace = false) => {
    const url = workspaceUrl(next, auditId ?? activeAuditRef.current?.auditId);
    if (`${window.location.pathname}${window.location.search}` !== url) {
      window.history[replace ? "replaceState" : "pushState"](null, "", url);
    }
    setViewState(next);
    window.requestAnimationFrame(() => {
      document.getElementById("main-content")?.focus({ preventScroll: true });
      window.scrollTo({ top: 0 });
    });
  }, []);


  const refreshHistory = useCallback(async () => {
    const request = ++historyRequestRef.current;
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const [nextPapers, nextAudits] = await Promise.all([fetchPapers(), fetchAudits()]);
      if (request !== historyRequestRef.current) return;
      setPapers(nextPapers);
      setAudits(nextAudits);
    } catch (error) {
      if (request === historyRequestRef.current) setHistoryError(userFacingError(error, "Your paper and audit history could not be loaded."));
    } finally {
      if (request === historyRequestRef.current) setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const restored = readActiveAudit(userId);
      const location = readWorkspaceLocation(window.location.search);
      if (restored && (!location.auditId || location.auditId === restored.auditId)) setActiveAudit(restored);
      const initialView = location.view || (restored ? "arena" : "setup");
      setViewState(initialView);
      if (!location.view) window.history.replaceState(null, "", workspaceUrl(initialView, restored?.auditId));
      setRestoringAudit(false);
      void refreshHistory();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [refreshHistory, userId]);

  useEffect(() => {
    if (!activeAudit || !audits.length) return;
    const summary = audits.find((audit) => audit.audit_id === activeAudit.auditId);
    if (!summary) return;
    const paper = paperForAudit(papers, summary);
    const reconciled = activeAuditFromHistory(summary, paper);
    reconciled.roundIds = activeAudit.roundIds;
    reconciled.compareToAuditId = activeAudit.compareToAuditId;
    const currentSerialized = JSON.stringify(activeAudit);
    const nextSerialized = JSON.stringify(reconciled);
    if (currentSerialized === nextSerialized) return;
    const timer = window.setTimeout(() => {
      setActiveAudit(reconciled);
      saveActiveAudit(userId, reconciled);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [activeAudit, audits, papers, userId]);


  useEffect(() => {
    const restoreLocation = () => {
      const location = readWorkspaceLocation(window.location.search);
      const target = location.view || "setup";
      if (location.auditId && location.auditId !== activeAuditRef.current?.auditId) {
        const summary = audits.find(item => item.audit_id === location.auditId);
        if (summary) {
          const next = activeAuditFromHistory(summary, paperForAudit(papers, summary));
          setActiveAudit(next);
          saveActiveAudit(userId, next);
        } else if (!historyLoading && !historyError) {
          setHistoryError("This review is unavailable for this account. Choose a saved review below.");
          setViewState("library");
          window.history.replaceState(null, "", workspaceUrl("library"));
          return;
        }
      }
      setViewState(target);
    };
    window.addEventListener("popstate", restoreLocation);
    if (!restoringAudit && !historyLoading) restoreLocation();
    return () => window.removeEventListener("popstate", restoreLocation);
  }, [audits, papers, userId, restoringAudit, historyLoading, historyError]);

  const terminalRefreshKey = useRef<string | null>(null);
  useEffect(() => {
    if (!activeAudit || (auditStream.status !== "completed" && auditStream.status !== "error")) return;
    const key = `${activeAudit.auditId}:${auditStream.status}`;
    if (terminalRefreshKey.current === key) return;
    terminalRefreshKey.current = key;
    void refreshHistory();
  }, [activeAudit, auditStream.status, refreshHistory]);

  const comparisonAudits = useMemo(() => {
    const paperIds = new Set(revisionBase ? [revisionBase.id] : []);
    return audits
      .filter((audit) => paperIds.has(audit.paper_id) && audit.status.toLowerCase() === "completed")
      .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime());
  }, [audits, revisionBase]);

  const applyAuditConfiguration = useCallback((audit: AuditSummary) => {
    setStrictness(audit.strictness_level);
    setDepth(audit.depth);
    setTopics(audit.round_topics?.length ? [...audit.round_topics] : [audit.round_topic]);
    setMode(audit.mode);
    setDomainSelection(audit.domain);
  }, []);

  const selectFile = (selected?: File) => {
    if (!selected || prepareGuard.current || launchGuard.current) return;
    setSetupError(null);
    setRelevancePrompt(null);
    setPreparedPaper(null);
    const looksLikePdf = selected.type === "application/pdf" || selected.name.toLowerCase().endsWith(".pdf");
    if (!looksLikePdf) {
      setFile(null);
      setSetupError("Choose a PDF file. Other document formats cannot be audited.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    if (selected.size === 0) {
      setFile(null); setSetupError("This file is empty. Choose a text-based research PDF.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    if (selected.size > MAX_UPLOAD_BYTES) {
      setFile(null);
      setSetupError("This PDF is larger than 20 MB. Choose a smaller research paper.");
      if (fileInputRef.current) fileInputRef.current.value = "";
      return;
    }
    setFile(selected);
  };

  const clearSelectedFile = useCallback(() => {
    setFile(null);
    setPreparedPaper(null);
    setRelevancePrompt(null);
    setSetupError(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, []);

  const preparePaper = async (force = false) => {
    if (!file || prepareGuard.current || launchGuard.current) return;
    prepareGuard.current = true;
    setPreparing(true);
    setSetupError(null);
    try {
      const response = await uploadPaper(file, force, revisionBase?.id);
      const prepared: PreparedPaper = {
        paperId: response.paper_id,
        filename: response.filename,
        fileKey: fileKey(file),
        detectedDomain: response.detected_domain,
        parentPaperId: response.parent_paper_id,
        versionNumber: response.version_number,
        pageCount: response.page_count,
        chunkCount: response.chunk_count,
        relevanceOverridden: force,
      };
      setPreparedPaper(prepared);
      setRelevancePrompt(null);
      await refreshHistory();
    } catch (error) {
      if (!force && error instanceof UploadError && error.relevanceFailed) {
        setRelevancePrompt({ message: error.message, reason: error.reason });
      } else {
        if (force) setRelevancePrompt(null);
        setSetupError(userFacingError(error, "The paper could not be prepared."));
      }
    } finally {
      prepareGuard.current = false;
      setPreparing(false);
    }
  };

  const launchAudit = async () => {
    if (!preparedPaper || launchGuard.current || prepareGuard.current) return;
    const selectionError = topicSelectionError(depth, topics);
    if (selectionError) { setSetupError(selectionError); return; }
    if (auditRunning) { setSetupError("An audit is already running. Let it finish before launching another one."); return; }
    launchGuard.current = true;
    setLaunching(true);
    setSetupError(null);
    try {
      const response = await startAudit(preparedPaper.paperId, {
        round_topics: topics,
        strictness_level: strictness,
        depth,
        mode,
        domain: domainSelection,
        compare_to_audit_id: comparisonAuditId || undefined,
      });
      const nextAudit: ActiveAudit = {
        auditId: response.audit_id,
        paperId: preparedPaper.paperId,
        filename: preparedPaper.filename,
        roundIds: response.round_ids || (response.round_id ? [response.round_id] : []),
        roundTopics: response.round_topics || [...topics],
        strictnessLevel: response.strictness_level || strictness,
        depth: response.depth || depth,
        mode: response.mode || mode,
        domain: response.domain || effectiveDomain(domainSelection, preparedPaper.detectedDomain),
        versionNumber: preparedPaper.versionNumber,
        parentPaperId: preparedPaper.parentPaperId,
        compareToAuditId: comparisonAuditId,
      };
      saveActiveAudit(userId, nextAudit);
      setActiveAudit(nextAudit);
      terminalRefreshKey.current = null;
      setView("arena", nextAudit.auditId);
      setRelevancePrompt(null);
      await refreshHistory();
    } catch (error) {
      setSetupError(userFacingError(error, "The configured audit could not be launched."));
    } finally {
      launchGuard.current = false;
      setLaunching(false);
    }
  };

  const changeDepth = (nextDepth: AuditDepth) => {
    setDepth(nextDepth);
    setTopics(defaultTopicsForDepth(nextDepth));
    setSetupError(null);
  };

  const toggleTopic = (slug: string) => {
    setTopics((current) => current.includes(slug) ? current.filter((topic) => topic !== slug) : [...current, slug]);
    setSetupError(null);
  };

  const changeComparison = (auditId: string | null) => {
    setComparisonAuditId(auditId);
    const audit = audits.find((candidate) => candidate.audit_id === auditId);
    if (audit) applyAuditConfiguration(audit);
  };

  const startRevision = useCallback((paper: PaperSummary, comparison?: AuditSummary) => {
    if (auditRunning) {
      setSetupError("Let the active audit finish before preparing a revision.");
      setView("setup");
      return;
    }
    const fallback = audits
      .filter((audit) => audit.paper_id === paper.id && audit.status.toLowerCase() === "completed")
      .sort((left, right) => new Date(right.created_at).getTime() - new Date(left.created_at).getTime())[0];
    const selectedComparison = comparison || fallback;
    setRevisionBase(paper);
    setComparisonAuditId(selectedComparison?.audit_id || null);
    if (selectedComparison) applyAuditConfiguration(selectedComparison);
    clearSelectedFile();
    setView("setup");
    window.requestAnimationFrame(() => fileInputRef.current?.click());
  }, [applyAuditConfiguration, auditRunning, audits, clearSelectedFile, setView]);

  const startRevisionFromActive = () => {
    if (!activeAudit) return;
    const paper = papers.find((candidate) => candidate.id === activeAudit.paperId) || {
      id: activeAudit.paperId,
      filename: activeAudit.filename,
      detected_domain: activeAudit.domain,
      parent_paper_id: activeAudit.parentPaperId,
      version_number: activeAudit.versionNumber,
      uploaded_at: new Date().toISOString(),
    };
    const summary = audits.find((audit) => audit.audit_id === activeAudit.auditId);
    startRevision(paper, summary);
  };

  const openHistoricalAudit = (summary: AuditSummary, target: "arena" | "report") => {
    if (auditRunning && activeAudit?.auditId !== summary.audit_id) {
      setHistoryError("Let the active audit finish before switching to another saved audit.");
      return;
    }
    const nextAudit = activeAuditFromHistory(summary, paperForAudit(papers, summary));
    saveActiveAudit(userId, nextAudit);
    setActiveAudit(nextAudit);
    terminalRefreshKey.current = null;
    setView(target, nextAudit.auditId);
  };


  const auditExistingPaper = (paper: PaperSummary) => {
    clearSelectedFile();
    setRevisionBase(null);
    setComparisonAuditId(null);
    setPreparedPaper({ paperId: paper.id, filename: paper.filename, fileKey: `stored:${paper.id}`,
      detectedDomain: paper.detected_domain, parentPaperId: paper.parent_paper_id,
      versionNumber: paper.version_number, pageCount: paper.page_count });
    setDomainSelection("auto");
    setView("setup");
  };

  const beginNewAudit = () => {
    if (auditRunning) return;
    try { localStorage.removeItem(activeAuditKey(userId)); } catch { /* nonessential */ }
    setActiveAudit(null);
    setRevisionBase(null);
    setComparisonAuditId(null);
    clearSelectedFile();
    setView("setup");
  };

  const workspacePaperName = activeAudit?.filename || preparedPaper?.filename || file?.name;
  const busy = preparing || launching;

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to main content</a>
      <header className="topbar" inert={relevancePrompt ? true : undefined}>
        <Brand />
        <div className="topbar-meta"><Link className="text-link method-link" href="/example">Worked example</Link><ThemeToggle /><div className="account-control"><span title={userEmail}>{userEmail}</span><button type="button" onClick={onSignOut} disabled={signingOut}>{signingOut ? "Logging out…" : "Log out"}</button></div></div>
      </header>
      {signOutError && <div className="signout-error" role="alert">{signOutError}</div>}

      <aside className="sidebar" inert={relevancePrompt ? true : undefined}>
        <div className="sidebar-label">Workspace</div>
        <nav className="side-nav" aria-label="Audit stages">
          <NavButton index="01" label="New review" active={view === "setup"} disabled={busy || restoringAudit} onClick={() => setView("setup")} />
          <NavButton index="02" label="Findings & evidence" active={view === "arena"} disabled={!activeAudit || busy || restoringAudit} onClick={() => setView("arena")} live={view === "arena" && auditStream.status === "in_progress" && auditStream.connectionState === "live"} />
          <NavButton index="03" label="Revision brief" active={view === "report"} disabled={!activeAudit || busy || restoringAudit} onClick={() => setView("report")} />
          <NavButton index="04" label="Paper library" active={view === "library"} disabled={busy || restoringAudit} onClick={() => setView("library")} />
        </nav>
        <div className="sidebar-footer"><div className="mini-label">CURRENT PAPER</div><div className="paper-mini"><PIcon name="document" /><span>{workspacePaperName || "No paper selected"}</span></div><div className="paper-mini-meta">{activeAudit ? `Version ${activeAudit.versionNumber} · ${activeAudit.roundTopics.length || "…"} topics` : file ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : ""}</div></div>
      </aside>

      <main id="main-content" className="main-content" inert={relevancePrompt ? true : undefined} tabIndex={-1}>
        {restoringAudit && <div className="workspace-loading" role="status"><span className="loading-mark" aria-hidden="true" /><strong>Restoring your private workspace</strong><span>Loading the active audit and account-scoped history.</span></div>}
        {!restoringAudit && view === "setup" && <AuditSetup file={file} preparedPaper={preparedPaper} revisionBase={revisionBase} comparisonAudits={comparisonAudits} comparisonAuditId={comparisonAuditId} strictness={strictness} depth={depth} topics={topics} mode={mode} domainSelection={domainSelection} preparing={preparing} launching={launching} error={setupError} auditRunning={auditRunning} onUpload={() => fileInputRef.current?.click()} onFileDrop={selectFile} onRemove={clearSelectedFile} onPrepare={() => { void preparePaper(); }} onLaunch={() => { void launchAudit(); }} onCancelRevision={() => { setRevisionBase(null); setComparisonAuditId(null); }} onComparisonChange={changeComparison} onStrictnessChange={setStrictness} onDepthChange={changeDepth} onTopicToggle={toggleTopic} onModeChange={setMode} onDomainChange={setDomainSelection} onOpenLibrary={() => setView("library")} />}
        {!restoringAudit && (view === "arena" || view === "report") && !activeAudit && <div className="library-empty"><PIcon name="book" /><h2>Choose a review from your library.</h2><p>Your saved evidence and reports are available there.</p><button className="button button-primary" onClick={() => setView("library")}>Open library</button></div>}
        <input ref={fileInputRef} type="file" accept=".pdf,application/pdf" onChange={(event) => selectFile(event.target.files?.[0])} className="file-input" />
        {!restoringAudit && view === "arena" && activeAudit && <AuditArena key={activeAudit.auditId} audit={activeAudit} stream={auditStream} onNewAudit={beginNewAudit} onOpenReport={() => setView("report")} />}
        {!restoringAudit && view === "report" && activeAudit && <FinalReportView key={activeAudit.auditId} audit={activeAudit} stream={auditStream} onOpenArena={() => setView("arena")} onNewVersion={startRevisionFromActive} />}
        {!restoringAudit && view === "library" && <LibraryView papers={papers} audits={audits} loading={historyLoading} error={historyError} onRefresh={() => { void refreshHistory(); }} onOpenAudit={openHistoricalAudit} onNewVersion={startRevision} onAuditPaper={auditExistingPaper} onNewAudit={beginNewAudit} />}
      </main>

      {relevancePrompt && <RelevanceDialog prompt={relevancePrompt} loading={preparing} onChangeDocument={() => { setRelevancePrompt(null); clearSelectedFile(); window.requestAnimationFrame(() => fileInputRef.current?.click()); }} onProceed={() => { void preparePaper(true); }} />}
    </div>
  );
}

function NavButton({ index, label, active, disabled, live = false, onClick }: { index: string; label: string; active: boolean; disabled: boolean; live?: boolean; onClick: () => void }) {
  return <button type="button" className={`nav-item ${active ? "active" : ""}`} onClick={onClick} disabled={disabled} aria-current={active ? "page" : undefined}><span className="nav-index">{index}</span><span>{label}</span>{live && <span className="nav-live">LIVE</span>}</button>;
}
