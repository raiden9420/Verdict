"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { PButton, PIcon, PTag } from "@porsche-design-system/components-react";
import DocumentViewer from "@/components/DocumentViewer";
import { DOMAIN_LABELS, topicName } from "@/lib/audit-config";
import { buildRoundPlan, roundMatches, type ActiveAudit, type RoundPlan } from "@/lib/audit-workspace";
import type { UseSSEResult } from "@/hooks/useSSE";
import type { AuditStatus, DebriefCard, ReproducibilitySignals, Turn, Verdict } from "@/types";

const TOTAL_EXCHANGES = 3;

export function AuditArena({
  audit,
  stream,
  onNewAudit,
  onOpenReport,
}: {
  audit: ActiveAudit;
  stream: UseSSEResult;
  onNewAudit: () => void;
  onOpenReport: () => void;
}) {
  const rounds = useMemo(
    () => buildRoundPlan(audit, stream.turns, stream.verdicts, stream.debriefs, stream.activeRound),
    [audit, stream.activeRound, stream.debriefs, stream.turns, stream.verdicts],
  );
  const [selectedRoundKey, setSelectedRoundKey] = useState(rounds[0]?.key || "");
  const [followLive, setFollowLive] = useState(true);
  const activeRoundPlan = useMemo(
    () => rounds.find((round) =>
      Boolean(stream.activeRound?.round_id && round.id === stream.activeRound.round_id) ||
      round.number === stream.activeRound?.round_number,
    ),
    [rounds, stream.activeRound],
  );

  const effectiveRoundKey = followLive && activeRoundPlan
    ? activeRoundPlan.key
    : selectedRoundKey;
  const selectedRound = rounds.find((round) => round.key === effectiveRoundKey) || rounds[0];
  const roundTurns = useMemo(
    () => stream.turns.filter((turn) => roundMatches(turn, selectedRound, rounds.length)),
    [rounds.length, selectedRound, stream.turns],
  );
  const roundVerdicts = useMemo(
    () => stream.verdicts.filter((verdict) => roundMatches(verdict, selectedRound, rounds.length)),
    [rounds.length, selectedRound, stream.verdicts],
  );
  const displayedTurns = useMemo(() => selectDisplayTurns(roundTurns), [roundTurns]);
  const selectedDebrief = stream.debriefs.find((card) => roundMatches(card, selectedRound, rounds.length)) || null;
  const completedRounds = rounds.filter((round) => stream.debriefs.some((card) => roundMatches(card, round, rounds.length))).length;
  const adjudicatedExchanges = new Set(roundVerdicts.map((verdict) => verdict.exchange_number)).size;
  const latestExchange = roundTurns.reduce((latest, turn) => Math.max(latest, turn.exchange_number), 1);
  const selectedRoundComplete = Boolean(selectedDebrief);
  const activeExchange = selectedRoundComplete
    ? TOTAL_EXCHANGES
    : Math.min(TOTAL_EXCHANGES, Math.max(latestExchange, adjudicatedExchanges + 1));
  const selectedIsLive = Boolean(stream.status === "in_progress" && activeRoundPlan?.key === selectedRound.key);

  const highlightedPages = useMemo(() => {
    const latestCitedTurn = [...displayedTurns].reverse().find((turn) => (turn.content.cited_chunk_ids?.length ?? 0) > 0);
    if (!latestCitedTurn) return [];
    const citedIds = latestCitedTurn.content.cited_chunk_ids || [];
    const pages = new Set<number>();
    for (const validator of roundTurns.filter((turn) => turn.agent_type === "validator")) {
      const validations = [...(validator.content.attacker_validations || []), ...(validator.content.defender_validations || [])];
      for (const validation of validations) {
        if (validation.chunk_id && citedIds.includes(validation.chunk_id) && typeof validation.page_number === "number") {
          pages.add(validation.page_number);
        }
      }
    }
    return [...pages].sort((left, right) => left - right);
  }, [displayedTurns, roundTurns]);

  const feedRef = useRef<HTMLDivElement>(null);
  const followFeedRef = useRef(true);
  useEffect(() => {
    if (!followFeedRef.current || !feedRef.current) return;
    const frame = window.requestAnimationFrame(() => feedRef.current?.scrollTo({ top: feedRef.current.scrollHeight }));
    return () => window.cancelAnimationFrame(frame);
  }, [displayedTurns.length, stream.processMessage, selectedRoundKey]);

  const statusLabel = auditStatusLabel(stream.status, stream.connectionState);
  const statusTone = stream.status === "completed" ? "complete" : stream.status === "error" ? "failed" : stream.connectionState === "live" ? "live" : "connecting";

  return (
    <div className="arena-page phase3-arena">
      <p className="visually-hidden" role="status" aria-live="polite">
        {stream.status === "completed" ? `Audit complete. ${completedRounds} topic debriefs and the paper-level report are available.` : stream.status === "error" ? "The audit stopped before completion." : `${statusLabel}. ${completedRounds} of ${rounds.length} topics complete.`}
      </p>
      <div className="page-header phase3-page-header">
        <div>
          <div className="eyebrow"><span className="eyebrow-line" /> AUDIT / {statusLabel.toUpperCase()}</div>
          <h1>Adversarial arena</h1>
          <p className="page-subtitle"><PIcon name="document" /> {audit.filename}</p>
        </div>
        <div className="audit-config-badges" aria-label="Audit configuration">
          <span>{audit.depth}</span><span>{audit.strictnessLevel}</span><span>{audit.mode === "reviewer_assist" ? "reviewer assist" : "author"}</span><span>{DOMAIN_LABELS[audit.domain]}</span>
        </div>
      </div>

      <div className="topic-progress-summary">
        <div><span className="small-muted">PAPER-LEVEL PROGRESS</span><strong>{completedRounds} / {rounds.length} topics complete</strong></div>
        <div className="topic-progress-track" aria-hidden="true">{rounds.map((round) => <span key={round.key} className={roundState(round, rounds, stream)} />)}</div>
        <span className={`audit-status ${statusTone}`}><i aria-hidden="true" /> {statusLabel}</span>
      </div>

      <div className="round-topic-tabs" role="tablist" aria-label="Audit topics">
        {rounds.map((round) => {
          const state = roundState(round, rounds, stream);
          const verdictCount = stream.verdicts.filter((verdict) => roundMatches(verdict, round, rounds.length)).length;
          return (
            <button key={round.key} type="button" role="tab" aria-selected={round.key === selectedRound.key} className={`round-topic-tab ${round.key === selectedRound.key ? "selected" : ""} ${state}`} onClick={() => { setSelectedRoundKey(round.key); setFollowLive(false); }}>
              <span>{String(round.number).padStart(2, "0")}</span>
              <strong>{round.topicName || topicName(round.topic)}</strong>
              <small>{state === "complete" ? "Debrief ready" : state === "active" ? `${verdictCount}/3 adjudicated · Live` : `${verdictCount}/3 adjudicated`}</small>
            </button>
          );
        })}
      </div>
      {!followLive && activeRoundPlan && stream.status === "in_progress" && (
        <button type="button" className="follow-live-button" onClick={() => { setFollowLive(true); setSelectedRoundKey(activeRoundPlan.key); }}><span className="pulse-dot" /> Follow live topic</button>
      )}

      {stream.auditError && <div className="pipeline-alert error" role="alert"><PIcon name="error-filled" /><div><strong>Audit stopped</strong><span>{stream.auditError}</span></div></div>}
      {stream.transportError && stream.status !== "error" && <div className="pipeline-alert recovery" role="status"><PIcon name="information" /><div><strong>Some saved results are still syncing</strong><span>{stream.transportError}</span></div></div>}

      <div className="round-strip">
        <div className="round-progress" role="progressbar" aria-label={`${topicName(selectedRound.topic)} exchanges adjudicated`} aria-valuemin={0} aria-valuemax={TOTAL_EXCHANGES} aria-valuenow={adjudicatedExchanges}>
          {Array.from({ length: TOTAL_EXCHANGES }, (_, index) => {
            const exchange = index + 1;
            const progressClass = exchange <= adjudicatedExchanges ? "progress-complete" : exchange === activeExchange && selectedIsLive ? "progress-active" : "";
            return <span key={exchange} className={progressClass} />;
          })}
        </div>
        <div><strong>TOPIC {String(selectedRound.number).padStart(2, "0")} · EXCHANGE {String(activeExchange).padStart(2, "0")} OF 03</strong><span>{topicName(selectedRound.topic)}</span></div>
      </div>

      <div className="arena-grid">
        <section className="document-panel"><DocumentViewer key={audit.paperId} paperId={audit.paperId} highlightedPages={highlightedPages} /></section>
        <section className="debate-panel">
          <div className="panel-top"><span className="panel-title">{selectedIsLive ? <span className="live-bars" aria-hidden="true"><i /><i /><i /></span> : <PIcon name={selectedRoundComplete ? "check" : "information"} />}{selectedIsLive ? "LIVE TOPIC TRANSCRIPT" : "TOPIC TRANSCRIPT"}</span><span>{selectedRoundComplete ? "COMPLETE" : selectedIsLive ? "LIVE" : "QUEUED"}</span></div>
          <div ref={feedRef} className="agent-feed" role="log" aria-label={`${topicName(selectedRound.topic)} transcript`} aria-live="polite" tabIndex={0} onScroll={(event) => { const feed = event.currentTarget; followFeedRef.current = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 80; }}>
            {selectedIsLive && stream.processMessage && <div className="process-update" role="status"><PIcon name="globe" /><span>{stream.processMessage}</span></div>}
            {!displayedTurns.length && <div className="empty-transcript"><PIcon name="clock" /><strong>{selectedIsLive ? "This topic is starting" : "Waiting for this topic"}</strong><span>The three-exchange transcript will appear here.</span></div>}
            {displayedTurns.map((turn) => <AgentMessage key={turn.id || `${selectedRound.key}-${turn.exchange_number}-${turn.sequence}-${turn.agent_type}`} turn={turn} allRoundTurns={roundTurns} />)}
            {selectedIsLive && <div className="typing"><span className="typing-avatar">AI</span><span>Agent is formulating a response</span><i /><i /><i /></div>}
          </div>
        </section>
      </div>

      <ExchangeVerdicts verdicts={roundVerdicts} status={selectedRoundComplete ? "completed" : stream.status} />
      <section className="topic-debriefs" aria-labelledby="topic-debriefs-title">
        <div className="verdicts-header"><div><span className="section-kicker">ONE CARD PER ROUND TOPIC</span><h2 id="topic-debriefs-title">Topic debriefs</h2></div><span>{completedRounds} / {rounds.length} ready</span></div>
        <div className="topic-debrief-list">{rounds.map((round) => { const card = stream.debriefs.find((item) => roundMatches(item, round, rounds.length)) || null; return <TopicDebrief key={round.key} round={round} debrief={card} status={stream.status} defaultOpen={round.key === selectedRound.key} />; })}</div>
      </section>
      <div className="arena-bottom"><span><PIcon name="clock" /> {stream.status === "completed" ? "All topics synthesized into a paper-level report" : stream.status === "error" ? "Audit stopped before completion" : `${completedRounds} of ${rounds.length} topics complete`}</span><div className="arena-actions">{stream.finalReport && <PButton type="button" icon="arrow-right" onClick={onOpenReport}>Open final report</PButton>}{stream.status !== "in_progress" && <PButton type="button" variant="secondary" icon="reset" onClick={onNewAudit}>Start a new audit</PButton>}</div></div>
    </div>
  );
}

function roundState(round: RoundPlan, rounds: RoundPlan[], stream: UseSSEResult): "complete" | "active" | "queued" {
  if (stream.debriefs.some((card) => roundMatches(card, round, rounds.length))) return "complete";
  if (stream.status === "in_progress" && stream.activeRound && roundMatches(stream.activeRound, round, rounds.length)) return "active";
  return "queued";
}

function auditStatusLabel(status: AuditStatus, connectionState: UseSSEResult["connectionState"]): string {
  if (status === "completed") return "Completed";
  if (status === "error") return "Failed";
  if (connectionState === "live") return "Live";
  if (connectionState === "connecting") return "Connecting";
  if (connectionState === "reconnecting") return "Reconnecting";
  if (connectionState === "polling") return "Syncing";
  return "Starting";
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
    const defender = ordered.filter((turn) => turn.agent_type === "defender" && (!attacker || turn.sequence > attacker.sequence)).at(-1);
    const referee = ordered.filter((turn) => turn.agent_type === "referee").at(-1);
    if (attacker) selected.push(attacker);
    if (defender) selected.push(defender);
    if (referee) selected.push(referee);
  }
  return selected.sort((left, right) => left.exchange_number - right.exchange_number || left.sequence - right.sequence);
}

function confidencePercent(confidence?: number): string {
  if (typeof confidence !== "number" || !Number.isFinite(confidence)) return "—";
  const percentage = confidence <= 1 ? confidence * 100 : confidence;
  return `${Math.round(Math.max(0, Math.min(100, percentage)))}%`;
}

function ExchangeVerdicts({ verdicts, status }: { verdicts: Verdict[]; status: AuditStatus }) {
  const byExchange = new Map<number, Verdict>();
  for (const verdict of verdicts) {
    if (verdict.exchange_number >= 1 && verdict.exchange_number <= TOTAL_EXCHANGES) byExchange.set(verdict.exchange_number, verdict);
  }
  return (
    <section className="exchange-verdicts" aria-labelledby="exchange-verdicts-title">
      <div className="verdicts-header"><div><span className="section-kicker">THREE-EXCHANGE ADJUDICATION</span><h2 id="exchange-verdicts-title">Exchange verdicts</h2></div><span>{byExchange.size} / {TOTAL_EXCHANGES} final</span></div>
      {status === "completed" && byExchange.size !== TOTAL_EXCHANGES && <div className="pipeline-alert error" role="alert"><PIcon name="error-filled" /><div><strong>Incomplete topic result</strong><span>This topic completed with {byExchange.size} of {TOTAL_EXCHANGES} required verdicts.</span></div></div>}
      <div className="verdict-grid">
        {Array.from({ length: TOTAL_EXCHANGES }, (_, index) => {
          const exchangeNumber = index + 1;
          const verdict = byExchange.get(exchangeNumber);
          const verdictClass = verdict?.verdict_type.toLowerCase().replaceAll("_", "-") || "pending";
          return (
            <article className={`verdict-card ${verdictClass}`} key={exchangeNumber}>
              <div className="verdict-card-header"><span>EXCHANGE {String(exchangeNumber).padStart(2, "0")}</span>{verdict ? <PTag variant={verdict.verdict_type === "SOLIDIFIED" ? "success" : verdict.verdict_type === "ACTIONABLE_FLAW" ? "error" : "warning"}>{verdict.verdict_type.replaceAll("_", " ")}</PTag> : <PTag variant="secondary">PENDING</PTag>}</div>
              {verdict ? <><strong>{verdict.claim_summary || "Adjudicated claim"}</strong><p>{verdict.rationale || "No rationale was returned."}</p><small>Confidence {confidencePercent(verdict.confidence)}</small></> : <p>Waiting for the Attacker, Defender, validation, and Referee ruling.</p>}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function TopicDebrief({ round, debrief, status, defaultOpen }: { round: RoundPlan; debrief: DebriefCard | null; status: AuditStatus; defaultOpen: boolean }) {
  const fallback = status === "error" ? "No debrief was produced for this topic before the audit stopped." : "This debrief will appear after the topic's three exchanges are adjudicated.";
  return (
    <details className="debrief-card topic-debrief-card" open={defaultOpen}>
      <summary className="debrief-header"><span><span className="round-number-mark">{String(round.number).padStart(2, "0")}</span><strong>{topicName(round.topic)}</strong>{debrief ? <PTag variant="success">Final</PTag> : status === "error" ? <PTag variant="error">Unavailable</PTag> : <PTag variant="warning">Pending</PTag>}</span><span className="debrief-toggle">View card <PIcon name="arrow-down" /></span></summary>
      <div className="debrief-body">
        <div className="synthesis"><span className="small-muted">TOPIC EXECUTIVE SYNTHESIS</span><p>{debrief?.executive_synthesis || fallback}</p></div>
        {debrief?.reproducibility_checklist && <ReproducibilityChecklist signals={debrief.reproducibility_checklist} />}
        <div className="debrief-columns">
          <DebriefList label="SOLIDIFIED STRENGTHS" tone="strength" icon="check" items={debrief?.solidified_strengths} />
          <DebriefList label="ACTIONABLE WEAKNESSES" tone="weakness" icon="error-filled" items={debrief?.actionable_weaknesses} />
          <DebriefList label="CONTESTED POINTS" tone="contested" icon="information" items={debrief?.contested_points} />
        </div>
      </div>
    </details>
  );
}

function DebriefList({ label, tone, icon, items }: { label: string; tone: string; icon: "check" | "error-filled" | "information"; items?: string[] }) {
  return <div><span className={`debrief-label ${tone}`}>{label}</span>{items?.length ? items.map((item, index) => <p key={index}><PIcon name={icon} /> {item}</p>) : <p className="small-muted">No items reported.</p>}</div>;
}

function ReproducibilityChecklist({ signals }: { signals: ReproducibilitySignals }) {
  const domain = signals.domain || "ml_cs";
  const checks: Array<[string, boolean | undefined, string[] | undefined]> = domain === "life_sciences"
    ? [
        ["Reagents / materials", signals.materials_available, signals.material_details],
        ["Database deposition", signals.database_deposition, signals.deposition_details],
        ["Protocols", signals.protocols_disclosed, signals.protocol_details],
        ["Data availability", signals.data_available, signals.data_details],
        ["Ethics disclosure", signals.ethics_disclosed, signals.ethics_details],
      ]
    : domain === "social_science"
      ? [
          ["Preregistration", signals.preregistered, signals.preregistration_details],
          ["IRB / ethics approval", signals.ethics_approval, signals.ethics_details],
          ["Materials repository", signals.materials_available, signals.material_details],
          ["Data availability", signals.data_available, signals.data_details],
          ["Instruments", signals.instruments_disclosed, signals.instrument_details],
        ]
      : domain === "other"
        ? [
            ["Data availability", signals.data_available, signals.data_details],
            ["Materials", signals.materials_available, signals.material_details],
            ["Methods", signals.methods_documented, signals.method_details],
          ]
        : [
            ["Code / repository", signals.code_available, signals.code_details],
            ["Dataset availability", signals.data_available, signals.data_details],
            ["Hyperparameters", signals.hyperparameters_disclosed, signals.hyperparameter_details],
            ["Compute / hardware", signals.compute_disclosed, signals.compute_details],
            ["Random seed", signals.seed_disclosed, signals.seed_details],
          ];
  return (
    <div className="reproducibility-checklist">
      <span className="small-muted">DOMAIN-CALIBRATED REPRODUCIBILITY CHECKLIST</span>
      <div className="reproducibility-grid">
        {checks.map(([label, disclosed, details]) => <div key={label}><span>{label}</span><strong className={disclosed ? "disclosed" : "missing"}>{disclosed ? "Disclosed" : "Missing"}</strong>{details?.length ? <small>{details.slice(0, 3).join(", ")}</small> : null}</div>)}
      </div>
    </div>
  );
}

function canonicalTitle(title?: string): string {
  return (title || "").toLowerCase().replace(/[^a-z0-9]/g, "");
}

function AgentMessage({ turn, allRoundTurns }: { turn: Turn; allRoundTurns: Turn[] }) {
  const validatorTurn = [...allRoundTurns]
    .filter((candidate) => candidate.agent_type === "validator" && candidate.exchange_number === turn.exchange_number && candidate.sequence > turn.sequence)
    .sort((left, right) => left.sequence - right.sequence)[0];
  const externalValidations = validatorTurn?.content.external_validations || [];
  const tone = turn.agent_type;
  const role = tone.toUpperCase();
  const text = turn.content.critique_text || turn.content.rebuttal_text || turn.content.rationale || turn.content.claim_summary || "";
  const cite = turn.content.cited_chunk_ids?.length ? "In-document citations provided" : "";

  return (
    <article className={`agent-message ${tone}`}>
      <div className="message-meta">
        <span className="agent-avatar">{role[0]}</span><strong>EXCHANGE {turn.exchange_number} · {role}</strong><span>{turn.created_at ? new Date(turn.created_at).toLocaleTimeString() : ""}</span>
        <PTag variant={tone === "attacker" ? "error" : tone === "defender" ? "success" : "secondary"}>{tone === "referee" ? "RULING" : "ARGUMENT"}</PTag>
      </div>
      <p>{text}</p>
      {turn.content.external_search_performed && <div className="literature-search-note"><PIcon name="globe" /><span>External literature search executed across Semantic Scholar, arXiv, &amp; OpenAlex ({turn.content.external_candidate_count ?? 0} candidates retrieved)</span></div>}
      {turn.content.external_citations?.length ? (
        <div className="external-literature">
          <span className="external-literature-title"><PIcon name="linked" /> Cited literature</span>
          <div className="external-citation-list">
            {turn.content.external_citations.map((citation, index) => {
              const normalizedTitle = canonicalTitle(citation.title);
              const validation = externalValidations.find((item) => item.citation_index === index || (Boolean(normalizedTitle) && canonicalTitle(item.title) === normalizedTitle));
              const validationStatus = validation?.valid === true ? "verified" : validation?.valid === false ? "invalid" : "pending";
              return (
                <div key={`${citation.title}-${index}`} className="external-citation-item">
                  <div className="external-citation-copy">
                    <div className="external-citation-name">{citation.url ? <a href={citation.url} target="_blank" rel="noopener noreferrer">{citation.title}<PIcon name="external" /></a> : citation.title}</div>
                    <div className="external-citation-meta">{citation.authors?.join(", ")} {citation.year ? `(${citation.year})` : ""} {citation.source ? `• ${citation.source}` : ""}{citation.similarity_score != null ? ` • Overlap Similarity: ${Math.round(citation.similarity_score * 100)}%` : ""}</div>
                  </div>
                  <span className={`validation-badge ${validationStatus}`}>{validationStatus === "verified" ? "Verified" : validationStatus === "invalid" ? "Unverified" : "Pending"}</span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
      {cite && <div className="citation"><PIcon name="linked" /> {cite}</div>}
    </article>
  );
}
