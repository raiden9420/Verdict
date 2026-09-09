"use client";

import { useId, useMemo, useRef, useState } from "react";
import DocumentViewer, { type SampleSource } from "@/components/DocumentViewer";
import { depthChoice, DOMAIN_LABELS, topicName } from "@/lib/audit-config";
import { buildRoundPlan, roundMatches, type ActiveAudit, type RoundPlan } from "@/lib/audit-workspace";
import { citationStatus, evidenceForExchange, matchExternalValidation, outcomeCounts, OUTCOMES, safeExternalUrl, selectDisplayTurns, validatorForTurn, type EvidencePassage } from "@/lib/audit-evidence";
import type { UseSSEResult } from "@/hooks/useSSE";
import type { DebriefCard, ReproducibilitySignals, Turn, Verdict } from "@/types";
import styles from "./AuditArena.module.css";

export function AuditArena({ audit, stream, onNewAudit, onOpenReport, source }: {
  audit: ActiveAudit;
  stream: UseSSEResult;
  onNewAudit: () => void;
  onOpenReport: () => void;
  source?: SampleSource;
}) {
  const rounds = useMemo(() => buildRoundPlan(audit, stream.turns, stream.verdicts, stream.debriefs, stream.activeRound), [audit, stream]);
  const [selectedRoundNumber, setSelectedRoundNumber] = useState(1);
  const [followLive, setFollowLive] = useState(true);
  const [mobilePane, setMobilePane] = useState<"findings" | "source">("findings");
  const [sourcePage, setSourcePage] = useState<number | null>(null);
  const tabId = useId();
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const sourceRef = useRef<HTMLElement>(null);
  const activeRound = rounds.find((round) => stream.activeRound && roundMatches(stream.activeRound, round, rounds.length));
  const selectedRound = rounds.find((round) => round.number === (followLive && stream.status === "in_progress" && activeRound ? activeRound.number : selectedRoundNumber)) || rounds[0];
  const turns = stream.turns.filter((turn) => roundMatches(turn, selectedRound, rounds.length));
  const verdicts = stream.verdicts.filter((verdict) => roundMatches(verdict, selectedRound, rounds.length));
  const debrief = stream.debriefs.find((card) => roundMatches(card, selectedRound, rounds.length));
  const displayedTurns = selectDisplayTurns(turns);
  const completedRounds = rounds.filter((round) => stream.debriefs.some((card) => roundMatches(card, round, rounds.length))).length;
  const counts = outcomeCounts(stream.verdicts);
  const isLive = stream.status === "in_progress" && activeRound?.number === selectedRound.number;
  const pages = [...new Set([1, 2, 3].flatMap((exchange) => evidenceForExchange(turns, exchange)).map((item) => item.page_number).filter((page): page is number => typeof page === "number" && Number.isInteger(page) && page > 0))].sort((a, b) => a - b);
  const status = stream.status === "completed" ? "Review complete" : stream.status === "error" ? "Review interrupted" : stream.connectionState === "live" ? "Review in progress" : "Connecting to saved review";

  const selectRound = (number: number) => { setSelectedRoundNumber(number); setFollowLive(false); setMobilePane("findings"); };
  const showPage = (page: number) => {
    setSourcePage(page);
    setMobilePane("source");
    window.requestAnimationFrame(() => sourceRef.current?.focus({ preventScroll: true }));
  };
  const handleTabKey = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    let next: number;
    if (event.key === "ArrowRight") next = (index + 1) % rounds.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + rounds.length) % rounds.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = rounds.length - 1;
    else return;
    event.preventDefault(); selectRound(rounds[next].number); tabRefs.current[next]?.focus();
  };

  return (
    <div className={styles.arena}>
      <header className={styles.header}>
        <div><div className={styles.kicker}>{source ? "A worked example" : "Research review"} <span> / </span> {status}</div><h1>Evidence &amp; findings</h1><p className={styles.paperTitle}>{audit.filename} <span>Version {audit.versionNumber}</span></p></div>
        {stream.finalReport && <button className={styles.primaryButton} type="button" onClick={onOpenReport}>Read final report <span aria-hidden="true">↗</span></button>}
      </header>

      <section className={styles.overview} aria-label="Review outcomes">
        <div><span className={styles.revision}>{counts.revision}</span><strong>Revision needed</strong></div>
        <div><span className={styles.supported}>{counts.supported}</span><strong>Supported</strong></div>
        <div><span className={styles.open}>{counts.open}</span><strong>Open questions</strong></div>
        <div className={styles.overviewProgress}><strong>{completedRounds} of {rounds.length} topics complete</strong><span>{depthChoice(audit.depth).label} coverage · {DOMAIN_LABELS[audit.domain]}</span><progress value={completedRounds} max={rounds.length} aria-label="Topics completed" /></div>
      </section>
      <p className={styles.outcomeNote}>Counts describe the challenges reviewed, not the quality of the paper. A supported defense does not establish scientific correctness.</p>

      <details className={styles.methodNote}>
        <summary>How to read this review</summary>
        <div><p>Each topic contains three challenges. A defense must answer using the manuscript; evidence checks precede the final ruling. Open the discussion to inspect the challenge, response, and reasoning.</p><div className={styles.outcomeDefinitions}>{Object.values(OUTCOMES).map((outcome) => <p key={outcome.label}><strong className={styles[outcome.tone]}>{outcome.label}.</strong> {outcome.description}</p>)}</div><p>Citation checks establish title existence and broad topical relevance. They do not establish that a specific claim correctly represents the cited work. Model confidence is uncalibrated and appears only in ruling details.</p></div>
      </details>

      {stream.auditError && <div className={styles.errorNotice} role="alert"><strong>The review stopped before completion.</strong><span>{stream.auditError}</span><span>Saved findings remain available below.</span></div>}
      {stream.transportError && stream.status !== "error" && <div className={styles.notice} role="status"><strong>Recovering saved results</strong><span>{stream.transportError}</span></div>}
      <p className="visually-hidden" role="status" aria-live="polite">{status}. {completedRounds} of {rounds.length} topics complete.</p>

      <div className={styles.topicTabs} role="tablist" aria-label="Review topics">
        {rounds.map((round, index) => {
          const complete = stream.debriefs.some((card) => roundMatches(card, round, rounds.length));
          const count = stream.verdicts.filter((verdict) => roundMatches(verdict, round, rounds.length)).length;
          return <button type="button" key={round.key} ref={(element) => { tabRefs.current[index] = element; }} id={`${tabId}-tab-${round.number}`} role="tab" aria-selected={round.number === selectedRound.number} aria-controls={`${tabId}-panel-${round.number}`} tabIndex={round.number === selectedRound.number ? 0 : -1} onClick={() => selectRound(round.number)} onKeyDown={(event) => handleTabKey(event, index)}><span>{String(round.number).padStart(2, "0")}</span><strong>{shortTopic(round.topic)}</strong><small>{complete ? "Complete" : activeRound?.number === round.number && stream.status === "in_progress" ? `${count}/3 · In progress` : `${count}/3 reviewed`}</small></button>;
        })}
      </div>
      {!followLive && activeRound && stream.status === "in_progress" && <button className={styles.followButton} type="button" onClick={() => { setFollowLive(true); setSelectedRoundNumber(activeRound.number); }}>Return to the live topic →</button>}

      <section id={`${tabId}-panel-${selectedRound.number}`} role="tabpanel" aria-labelledby={`${tabId}-tab-${selectedRound.number}`} tabIndex={-1} className={styles.topicPanel}>
        <div className={styles.topicHeading}><div><span className={styles.kicker}>Topic {String(selectedRound.number).padStart(2, "0")}</span><h2>{topicName(selectedRound.topic)}</h2></div><span>{verdicts.length}/3 findings reviewed</span></div>
        {isLive && <div className={styles.liveNotice} role="status"><span className={styles.liveDot} aria-hidden="true" /><span>{stream.processMessage || "The next challenge is being checked. Accepted arguments and findings appear here as they are saved."}</span></div>}

        <div className={styles.paneToggle} role="group" aria-label="Reading pane"><button type="button" aria-pressed={mobilePane === "findings"} onClick={() => setMobilePane("findings")}>Findings &amp; discussion</button><button type="button" aria-pressed={mobilePane === "source"} onClick={() => setMobilePane("source")}>Source manuscript</button></div>
        <div className={styles.workspace}>
          <div className={`${styles.findings} ${mobilePane !== "findings" ? styles.mobileHidden : ""}`}>
            {[1, 2, 3].map((exchange) => <Finding key={`${selectedRound.key}:${exchange}`} exchange={exchange} verdict={verdicts.find((item) => item.exchange_number === exchange)} turns={displayedTurns.filter((turn) => turn.exchange_number === exchange)} allTurns={turns} isLive={isLive} interrupted={stream.status === "error"} onPage={showPage} />)}
            {debrief && <TopicDebrief round={selectedRound} debrief={debrief} />}
            {!debrief && <div className={styles.debriefPending}><strong>Topic synthesis</strong><p>{stream.status === "error" ? "This topic did not reach synthesis. Review the saved findings above." : "A synthesis of strengths, revisions, and open questions appears after all three exchanges."}</p></div>}
          </div>
          <aside ref={sourceRef} className={`${styles.sourcePanel} ${mobilePane !== "source" ? styles.mobileHidden : ""}`} tabIndex={-1} aria-label="Source manuscript"><DocumentViewer key={audit.paperId} paperId={audit.paperId} highlightedPages={pages} source={source} selectedPage={sourcePage} onPageChange={setSourcePage} /></aside>
        </div>
      </section>
      {rounds.filter((round) => round.number !== selectedRound.number).map((round) => <div key={round.key} id={`${tabId}-panel-${round.number}`} role="tabpanel" aria-labelledby={`${tabId}-tab-${round.number}`} hidden />)}
      <footer className={styles.footer}><p>{stream.status === "completed" ? "Review each finding against the source before using it in a revision or formal review." : "You can return to this review from your paper library. Saved findings are preserved."}</p><div>{stream.finalReport && <button type="button" className={styles.primaryButton} onClick={onOpenReport}>Read final report →</button>}{stream.status !== "in_progress" && <button type="button" className={styles.secondaryButton} onClick={onNewAudit}>{source ? "Review your paper" : "Start another review"}</button>}</div></footer>
    </div>
  );
}

function shortTopic(topic: string): string {
  return ({ novelty_scope: "Contribution", theoretical_soundness: "Theory", experimental_setup: "Experiments", reproducibility: "Reproducibility", limitations_impact: "Limitations & impact", statistical_rigor: "Statistical rigor" } as Record<string, string>)[topic] || topicName(topic);
}

function Finding({ exchange, verdict, turns, allTurns, isLive, interrupted, onPage }: { exchange: number; verdict?: Verdict; turns: Turn[]; allTurns: Turn[]; isLive: boolean; interrupted: boolean; onPage: (page: number) => void }) {
  const attacker = turns.find((turn) => turn.agent_type === "attacker");
  const defender = turns.find((turn) => turn.agent_type === "defender");
  const outcome = verdict ? OUTCOMES[verdict.verdict_type] : null;
  const passages = evidenceForExchange(allTurns, exchange);
  const hasContent = turns.length > 0 || Boolean(verdict);
  return (
    <article className={`${styles.finding} ${outcome ? styles[outcome.tone + "Finding"] : styles.pendingFinding}`}>
      <div className={styles.findingMeta}><span>Finding {String(exchange).padStart(2, "0")}</span><strong className={outcome ? styles[outcome.tone] : ""}>{outcome?.label || (interrupted ? "Not completed" : hasContent && isLive ? "Under review" : "Awaiting review")}</strong></div>
      <h3>{verdict?.claim_summary || attacker?.content.claim_summary || (interrupted ? "No completed finding was recorded" : "The next challenge will appear here")}</h3>
      {verdict?.rationale ? <p className={styles.rationale}>{verdict.rationale}</p> : <p className={styles.pendingCopy}>{defender ? "The response is being checked before a ruling is recorded." : attacker ? "The challenge has passed its evidence check. The defense follows." : interrupted ? "The review ended before this exchange was completed." : "Only challenges that pass the evidence checks are shown."}</p>}
      {defender?.content.concedes && <p className={styles.concession}>The defense conceded this point.</p>}
      {passages.length > 0 && <section className={styles.evidenceSection} aria-label={`Source evidence for finding ${exchange}`}><span className={styles.evidenceLabel}>Source evidence</span>{passages.slice(0, 2).map((passage, index) => <EvidenceExcerpt key={`${passage.citedBy}:${passage.chunk_id || index}`} passage={passage} onPage={onPage} />)}{passages.length > 2 && <details className={styles.moreEvidence}><summary>{passages.length - 2} more cited passage{passages.length - 2 === 1 ? "" : "s"}</summary>{passages.slice(2).map((passage, index) => <EvidenceExcerpt key={`${passage.citedBy}:${passage.chunk_id || index}`} passage={passage} onPage={onPage} />)}</details>}</section>}
      {verdict && !passages.length && <p className={styles.evidenceUnavailable}>No source excerpt is available in the saved validation record. Inspect the discussion and manuscript.</p>}
      {hasContent && <details className={styles.discussion} open={!verdict && isLive}><summary><span>Inspect challenge, defense &amp; ruling</span><span aria-hidden="true">+</span></summary><div className={styles.argumentList}>{turns.map((turn) => <AgentMessage key={turn.id || `${turn.exchange_number}:${turn.sequence}`} turn={turn} allTurns={allTurns} />)}{!turns.length && <p>The discussion has not loaded yet.</p>}{verdict && typeof verdict.confidence === "number" && <p className={styles.confidenceNote}>Recorded model confidence: {Number.isFinite(verdict.confidence) ? verdict.confidence.toFixed(2) : "unavailable"}. This self-reported score is not a calibrated probability that the finding is correct.</p>}</div></details>}
    </article>
  );
}

function EvidenceExcerpt({ passage, onPage }: { passage: EvidencePassage; onPage: (page: number) => void }) {
  const page = passage.page_number;
  const canNavigate = typeof page === "number" && Number.isInteger(page) && page > 0;
  return <div className={styles.evidenceExcerpt}><div className={styles.evidenceMeta}><span>{passage.citedBy} {passage.valid ? "· Evidence accepted" : "· Evidence not accepted"}</span>{canNavigate && <button type="button" onClick={() => onPage(page)}>View p. {page} <span aria-hidden="true">↗</span></button>}</div>{passage.chunk_text ? <blockquote>{passage.chunk_text}</blockquote> : <p className={styles.evidenceUnavailable}>No matching source passage was recorded.</p>}{passage.source_truncated && <small>This excerpt was shortened. Open the cited PDF page for the full context.</small>}{!passage.valid && <p className={styles.failedEvidence}>{passage.reason === "chunk_not_found" ? "This cited passage could not be found in the manuscript." : "This passage did not pass the evidence check. It cannot establish a supported defense."}</p>}{passage.validation_method === "lexical_fallback" && <small>Checked using word overlap because the semantic check was unavailable.</small>}</div>;
}

function AgentMessage({ turn, allTurns }: { turn: Turn; allTurns: Turn[] }) {
  const validator = validatorForTurn(turn, allTurns);
  const role = turn.agent_type === "attacker" ? "Challenge" : turn.agent_type === "defender" ? "Defense" : "Ruling";
  const text = turn.content.critique_text || turn.content.rebuttal_text || turn.content.rationale || turn.content.claim_summary || "No argument text was recorded.";
  return <section className={styles.argument}><div className={styles.argumentHeading}><strong>{role}</strong><span>{turn.agent_type === "attacker" ? "Questions the claim" : turn.agent_type === "defender" ? "Answers from the manuscript" : "Weighs the evidence"}</span></div><p>{text}</p>{turn.content.external_citations?.map((citation, index) => {
    const validation = matchExternalValidation(validator?.content.external_validations || [], citation.title, index);
    const status = citationStatus(validation);
    const url = safeExternalUrl(citation.url);
    return <div className={styles.literature} key={`${citation.title}:${index}`}><span className={styles.evidenceLabel}>{turn.content.critique_type === "citation_integrity" ? "Reference in the manuscript" : "External literature"}</span><strong>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{citation.title} ↗</a> : citation.title}</strong><span>{[citation.authors?.join(", "), citation.year, citation.source].filter(Boolean).join(" · ")}</span><div className={styles[status.tone]}>{status.label}</div><p>{status.explanation}</p></div>;
  })}{turn.content.provenance && <p className={styles.confidenceNote}>Generation: {[turn.content.provenance.provider, turn.content.provenance.model, turn.content.provenance.prompt_version].filter(Boolean).join(" · ")}{turn.content.evidence_scope && <>. Retrieved context: {turn.content.evidence_scope.retrieved_chunk_ids?.length ?? 0} passages{turn.content.evidence_scope.retrieval_methods?.length ? ` (${turn.content.evidence_scope.retrieval_methods.join(", ")})` : ""}.</>}</p>}{turn.content.adjudication && <p className={styles.confidenceNote}>Ruling checks: {turn.content.adjudication.guard ? `guard: ${turn.content.adjudication.guard}; ` : ""}{turn.content.adjudication.consistency_check?.replaceAll("_", " ") || "No consistency check recorded"}{turn.content.adjudication.recheck_verdict ? `; second ruling: ${turn.content.adjudication.recheck_verdict}` : ""}.</p>}</section>;
}

function TopicDebrief({ round, debrief }: { round: RoundPlan; debrief: DebriefCard }) {
  return <section className={styles.debrief} aria-labelledby={`debrief-${round.key}`}><div className={styles.kicker}>Topic synthesis</div><h3 id={`debrief-${round.key}`}>{shortTopic(round.topic)}: what to take forward</h3><p>{debrief.executive_synthesis}</p><div className={styles.debriefGroups}><DebriefList label="Priority revisions" items={debrief.actionable_weaknesses} tone="revision" /><DebriefList label="Preserved strengths" items={debrief.solidified_strengths} tone="supported" /><DebriefList label="Open questions" items={debrief.contested_points} tone="open" /></div>{debrief.reproducibility_checklist && <ReproducibilityChecklist signals={debrief.reproducibility_checklist} />}</section>;
}

function DebriefList({ label, items, tone }: { label: string; items?: string[]; tone: string }) {
  if (!items?.length) return null;
  return <div><h4 className={styles[tone]}>{label}</h4><ul>{items.map((item, index) => <li key={index}>{item}</li>)}</ul></div>;
}

function ReproducibilityChecklist({ signals }: { signals: ReproducibilitySignals }) {
  const checks: Array<[string, boolean | undefined, string[] | undefined]> = signals.domain === "life_sciences" ? [["Reagents / materials", signals.materials_available, signals.material_details], ["Database deposition", signals.database_deposition, signals.deposition_details], ["Protocols", signals.protocols_disclosed, signals.protocol_details], ["Data availability", signals.data_available, signals.data_details], ["Ethics disclosure", signals.ethics_disclosed, signals.ethics_details]] : signals.domain === "social_science" ? [["Preregistration", signals.preregistered, signals.preregistration_details], ["Ethics approval", signals.ethics_approval, signals.ethics_details], ["Materials repository", signals.materials_available, signals.material_details], ["Data availability", signals.data_available, signals.data_details], ["Instruments", signals.instruments_disclosed, signals.instrument_details]] : signals.domain === "other" ? [["Data availability", signals.data_available, signals.data_details], ["Materials", signals.materials_available, signals.material_details], ["Methods", signals.methods_documented, signals.method_details]] : [["Code / repository", signals.code_available, signals.code_details], ["Dataset availability", signals.data_available, signals.data_details], ["Hyperparameters", signals.hyperparameters_disclosed, signals.hyperparameter_details], ["Compute / hardware", signals.compute_disclosed, signals.compute_details], ["Random seed", signals.seed_disclosed, signals.seed_details]];
  return <details className={styles.checklist}><summary>Reproducibility disclosure checks</summary><p>These are signals detected in extracted text. “Not detected” is a prompt to inspect the paper, not proof that a resource is absent.</p><dl>{checks.map(([label, found, details]) => <div key={label}><dt>{label}</dt><dd className={found === true ? styles.supported : ""}>{found === true ? "Detected" : found === false ? "Not detected" : "Not checked"}{details?.length ? <small>{details.slice(0, 3).join(", ")}</small> : null}</dd></div>)}</dl></details>;
}
