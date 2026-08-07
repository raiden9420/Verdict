"use client";

import { useMemo, useState, useCallback, useRef, useEffect } from 'react';
import {
  PButton,
  PButtonPure,
  PIcon,
  PTag,
  PText,
  PWordmark,
} from '@porsche-design-system/components-react';
import { uploadPaper, startAudit } from "@/lib/api";
import { ROUND_TOPICS } from "@/types";
import { useSSE } from "@/hooks/useSSE";
import DocumentViewer from "@/components/DocumentViewer";
import type { Turn } from "@/types";

type View = 'setup' | 'arena' | 'report';
type DepthOption = 'fast' | 'deep' | 'exhaustive';

export default function App() {
  const [view, setView] = useState<View>('setup');

  // Setup state
  const [topic, setTopic] = useState<string>(ROUND_TOPICS[0].slug);
  const [depth, setDepth] = useState<DepthOption>('deep');

  const [file, setFile] = useState<File | null>(null);
  const [uploadNotice, setUploadNotice] = useState(false);
  const [loading, setLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Audit state
  const [auditId, setAuditId] = useState<string | null>(null);
  const [paperId, setPaperId] = useState<string | null>(null);
  const [activeRound, setActiveRound] = useState(2);
  const [showDebrief, setShowDebrief] = useState(true);

  const selectedDescription = useMemo(() => {
    const selectedTopic = ROUND_TOPICS.find((t) => t.slug === topic);
    const topicDesc = selectedTopic ? selectedTopic.description : '';
    const effort = depth === 'exhaustive' ? 'line-by-line evidence checks across five rounds' : depth === 'deep' ? 'a comprehensive review across the core dimensions' : 'a fast scan of the highest-risk areas';
    return `The audit will evaluate: ${topicDesc} with ${effort}.`;
  }, [topic, depth]);

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) {
      setFile(selected);
      setUploadNotice(true);
    }
  };

  const launchAudit = async () => {
    if (!file || !topic) return;
    setLoading(true);
    try {
      const paper = await uploadPaper(file);
      setPaperId(paper.paper_id);
      const audit = await startAudit(paper.paper_id, topic);
      setAuditId(audit.audit_id);

      setView('arena');
      setActiveRound(2);
    } catch (error) {
      console.error(error);
      alert('Failed to launch audit');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <span className="verdict-wordmark">Verdict</span>
          <span className="brand-divider" />
          <span className="product-name">ADVERSARIAL AUDIT</span>
        </div>
        <div className="topbar-meta">
          <PButtonPure icon="menu-dots-horizontal" aria-label="More options" />
        </div>
      </header>

      <aside className="sidebar">
        <div className="sidebar-label">Audit workspace</div>
        <nav className="side-nav" aria-label="Audit stages">
          <button className={`nav-item ${view === 'setup' ? 'active' : ''}`} onClick={() => setView('setup')}>
            <span className="nav-index">01</span><span>Configure audit</span>
          </button>
          <button className={`nav-item ${view === 'arena' ? 'active' : ''}`} onClick={() => { if (auditId) setView('arena'); }}>
            <span className="nav-index">02</span><span>Live arena</span>{view === 'arena' && <span className="nav-live">LIVE</span>}
          </button>
          <button className={`nav-item ${view === 'report' ? 'active' : ''}`} onClick={() => { if (auditId) setView('report'); }}>
            <span className="nav-index">03</span><span>Final report</span>
          </button>
        </nav>
        <div className="sidebar-footer">
          <div className="mini-label">CURRENT PAPER</div>
          <div className="paper-mini"><PIcon name="document" /><span>{file ? file.name : 'No paper selected'}</span></div>
          <div className="paper-mini-meta">{file ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : ''}</div>
        </div>
      </aside>

      <main className="main-content">
        {view === 'setup' && (
          <SetupView
            file={file}
            topic={topic}
            setTopic={setTopic}
            depth={depth}
            setDepth={setDepth}
            selectedDescription={selectedDescription}
            uploadNotice={uploadNotice}
            onUpload={() => fileInputRef.current?.click()}
            onLaunch={launchAudit}
            loading={loading}
          />
        )}
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf"
          onChange={handleFileSelect}
          style={{ display: "none" }}
        />

        {view === 'arena' && auditId && paperId && (
          <ArenaView
            auditId={auditId}
            paperId={paperId}
            activeRound={activeRound}
            showDebrief={showDebrief}
            setShowDebrief={setShowDebrief}
            onReport={() => setView('report')}
          />
        )}
        {view === 'report' && <ReportView onBack={() => setView('arena')} />}
      </main>
    </div>
  );
}

function SetupView({ file, topic, setTopic, depth, setDepth, selectedDescription, uploadNotice, onUpload, onLaunch, loading }: any) {
  const options = ROUND_TOPICS.map((t) => [t.slug, t.name, t.description]);

  return <div className="setup-page">
    <div className="eyebrow"><span className="eyebrow-line" /> RESEARCH INTEGRITY / NEW AUDIT</div>
    <div className="setup-heading"><div><h1>Interrogate the<br /><em>uncomfortable.</em></h1><PText size="medium">A multi-agent review that goes beyond the abstract.<br />Find the gaps before someone else does.</PText></div><div className="heading-mark"><span>AI</span><span>×</span><span>∞</span></div></div>

    <section className="paper-card">
      <div className="section-kicker">01 — Source document</div>
      {file ? (
        <div className="paper-row">
          <div className="paper-icon"><PIcon name="document" /></div>
          <div className="paper-details"><strong>{file.name}</strong><span>PDF · {(file.size / (1024 * 1024)).toFixed(1)} MB</span></div>
          <PTag variant="secondary" icon="check">Ready to audit</PTag>
          <PButtonPure icon="close" aria-label="Remove document" onClick={() => { }} />
        </div>
      ) : (
        <button className="upload-zone" onClick={onUpload}><PIcon name="upload" /><span>Drop paper here or <u>browse files</u></span></button>
      )}
      {uploadNotice && <div className="upload-confirm"><PIcon name="check" /> Document selected. Ready to configure.</div>}
    </section>

    <section className="config-section">
      <div className="section-kicker">02 — Configure the pressure</div>
      <div className="config-grid">
        <OptionGroup label="Round Topic" value={topic} setValue={setTopic} options={options} />
        <OptionGroup label="Depth of analysis" value={depth} setValue={setDepth} options={[
          ['fast', 'Fast audit', 'Highest-risk areas · 1–2 rounds'], ['deep', 'Deep review', 'All core dimensions · 3–4 rounds'], ['exhaustive', 'Exhaustive breakdown', 'Line-by-line validation · 5+ rounds']
        ]} />
      </div>
      <div className="selection-note"><span className="pulse-dot" /> {selectedDescription}</div>
    </section>

    <div className="launch-row"><div><span className="small-muted">ESTIMATED RUN TIME</span><strong>~ 08 min</strong></div><PButton onClick={onLaunch} icon="arrow-right" iconSource="local" loading={loading} disabled={!file || !topic || loading}>Launch adversarial audit</PButton></div>
  </div>;
}

function OptionGroup({ label, value, setValue, options }: { label: string; value: string; setValue: (value: any) => void; options: any[][] }) {
  return <div className="option-group"><label>{label}</label><div className="option-list">{options.map(([id, title, desc]) => <button key={id} className={`option-card ${value === id ? 'selected' : ''}`} onClick={() => setValue(id)}><span className="radio-dot" /><span className="option-copy"><strong>{title}</strong><small>{desc}</small></span>{value === id && <PIcon name="check" />}</button>)}</div></div>;
}

function ArenaView({ auditId, paperId, activeRound, showDebrief, setShowDebrief, onReport }: any) {
  const { turns, verdicts, debrief, status, connectionState, error } = useSSE(auditId);

  const [highlightedPages, setHighlightedPages] = useState<number[]>([]);

  // Calculate highlighted pages based on turns (similar to old UI)
  const autoHighlightPages = useMemo(() => {
    const lastNonValidatorTurn = [...turns]
      .reverse()
      .find((t) => t.agent_type !== "validator" && (t.content.cited_chunk_ids?.length ?? 0) > 0);

    if (!lastNonValidatorTurn) return [];

    const chunkIds = lastNonValidatorTurn.content.cited_chunk_ids || [];
    const pages = new Set<number>();
    for (const turn of turns) {
      if (turn.agent_type === "validator" && turn.content) {
        const validations = [
          ...(turn.content.attacker_validations || []),
          ...(turn.content.defender_validations || []),
        ];
        for (const v of validations) {
          if (chunkIds.includes(v.chunk_id) && v.page_number) {
            pages.add(v.page_number);
          }
        }
      }
    }
    return [...pages];
  }, [turns]);

  const effectiveHighlightedPages = highlightedPages.length > 0 ? highlightedPages : autoHighlightPages;

  return <div className="arena-page">
    <div className="page-header"><div><div className="eyebrow"><span className="eyebrow-line" /> LIVE AUDIT / IN PROGRESS</div><h1>Adversarial arena</h1></div><div className="header-actions"></div></div>
    <div className="round-strip"><div className="round-progress"><span className="progress-complete" /><span className="progress-complete" /><span className="progress-active" /><span /><span /></div><div><strong>ROUND {String(activeRound + 1).padStart(2, '0')} OF 05</strong><span>Experimental setup</span></div><div></div></div>
    <div className="arena-grid">
      <section className="document-panel">
        <DocumentViewer
          paperId={paperId}
          highlightedPages={effectiveHighlightedPages}
        />
      </section>

      <section className="debate-panel">
        <div className="panel-top"><span className="panel-title"><span className="live-bars"><i /><i /><i /></span> LIVE DEBATE</span><span>{status}</span></div>
        <div className="agent-feed">
          {turns.map((turn, index) => (
            <AgentMessage
              key={turn.id || index}
              role={turn.agent_type.toUpperCase()}
              time={turn.created_at ? new Date(turn.created_at).toLocaleTimeString() : ''}
              tone={turn.agent_type}
              text={turn.content.critique_text || turn.content.rebuttal_text || turn.content.rationale || turn.content.claim_summary || ''}
              cite={turn.content.cited_chunk_ids?.length ? 'Citations provided' : ''}
            />
          ))}
          {status === 'in_progress' && (
            <div className="typing"><span className="typing-avatar">AI</span><span>Agent is formulating a response</span><i /><i /><i /></div>
          )}
        </div>
      </section>
    </div>

    <section className={`debrief-card ${showDebrief ? 'open' : ''}`}><button className="debrief-header" onClick={() => setShowDebrief(!showDebrief)}><span><PIcon name="chart" /><strong>Round debrief</strong>{debrief ? <PTag variant="success">Final</PTag> : <PTag variant="warning">Provisional</PTag>}</span><span className="debrief-toggle">{showDebrief ? 'Collapse' : 'Expand'} <PIcon name={showDebrief ? 'arrow-up' : 'arrow-down'} /></span></button>{showDebrief && <div className="debrief-body">
      <div className="synthesis"><span className="small-muted">EXECUTIVE SYNTHESIS</span><p>{debrief?.executive_synthesis || 'Debrief will be generated at the end of the round.'}</p></div>
      <div className="debrief-columns">
        <div><span className="debrief-label strength">SOLIDIFIED STRENGTHS</span>
          {debrief?.solidified_strengths?.map((s, i) => <p key={i}><PIcon name="check" /> {s}</p>)}
        </div>
        <div><span className="debrief-label weakness">ACTIONABLE WEAKNESSES</span>
          {debrief?.actionable_weaknesses?.map((w, i) => <button key={i}><PIcon name="error-filled" /> <span>{w}</span><PIcon name="arrow-right" /></button>)}
        </div>
      </div>
    </div>}</section>
    <div className="arena-bottom"><span><PIcon name="clock" /> Next round begins automatically</span><PButton variant="secondary" onClick={onReport}>View interim report</PButton></div>
  </div>;
}

function AgentMessage({ role, time, tone, text, cite }: { role: string; time: string; tone: string; text: string; cite: string }) { return <article className={`agent-message ${tone}`}><div className="message-meta"><span className="agent-avatar">{role[0]}</span><strong>{role}</strong><span>{time}</span><PTag variant={tone === 'attacker' ? 'error' : tone === 'defender' ? 'success' : 'secondary'}>{tone === 'referee' ? 'RULING' : 'ARGUMENT'}</PTag></div><p>{text}</p>{cite && <div className="citation"><PIcon name="linked" /> {cite}</div>}</article>; }

function ReportView({ onBack }: { onBack: () => void }) {
  const rounds = [
    { number: '01', title: 'Novelty & scope', status: 'Complete', score: 82 },
    { number: '02', title: 'Theory & rigor', status: 'Complete', score: 74 },
    { number: '03', title: 'Experimental setup', status: 'In progress', score: 68 },
    { number: '04', title: 'Reproducibility', status: 'Queued', score: 0 },
    { number: '05', title: 'Limitations & impact', status: 'Queued', score: 0 },
  ];
  return <div className="report-page"><div className="page-header"><div><div className="eyebrow"><span className="eyebrow-line" /> AUDIT COMPLETE / EXECUTIVE REPORT</div><h1>What survived scrutiny.</h1><PText size="medium">A concise map of the paper's verified contributions and the gaps that still need work.</PText></div><div className="header-actions"><PButtonPure icon="download" aria-label="Download report" /><PButtonPure icon="copy" aria-label="Copy report" /></div></div><div className="report-overview"><div className="score-ring"><strong>76</strong><span>/ 100</span><small>OVERALL RIGOR</small></div><div className="overview-copy"><PTag variant="warning">Moderate confidence</PTag><h2>Promising work with one critical gap.</h2><p>The core contribution is supported, but baseline parity and external validation should be addressed before publication.</p><div className="score-bars"><div><span>Novelty</span><i style={{ width: '82%' }} /><b>82</b></div><div><span>Rigor</span><i style={{ width: '74%' }} /><b>74</b></div><div><span>Reproducibility</span><i style={{ width: '61%' }} /><b>61</b></div></div></div></div><div className="report-columns"><section><div className="section-kicker">Verified core strengths</div><div className="finding-card green"><PIcon name="check" /><div><strong>Consistent benchmark performance</strong><p>Improvements hold across three datasets and five random seeds.</p></div></div><div className="finding-card green"><PIcon name="check" /><div><strong>Transparent compute disclosure</strong><p>Training configuration and hardware are clearly reported.</p></div></div></section><section><div className="section-kicker">Critical actionable improvements</div><div className="finding-card red"><PIcon name="error-filled" /><div><strong>Make baseline parity explicit</strong><p>Document identical preprocessing and evaluation protocols.</p></div></div><div className="finding-card red"><PIcon name="error-filled" /><div><strong>Add external validation</strong><p>Test the method on a held-out domain to support generalization.</p></div></div></section></div><div className="report-rounds"><div className="section-kicker">Round-by-round audit trail</div>{rounds.map((round) => <button className="report-round" key={round.number}><span>{round.number}</span><strong>{round.title}</strong><PTag variant={round.status === 'Complete' ? 'success' : 'secondary'}>{round.status}</PTag><b>{round.score ? `${round.score}/100` : '—'}</b><PIcon name="arrow-right" /></button>)}</div><div className="report-footer"><PButton variant="secondary" onClick={onBack}>Return to arena</PButton><PButton icon="download">Download full report</PButton></div></div>;
}
