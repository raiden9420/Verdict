/**
 * Live audit state with SSE delivery and a non-overlapping polling fallback.
 *
 * SSE is an optimization, not the source of truth. Every mount reconciles the
 * database snapshot first, and polling takes over immediately if the stream is
 * interrupted. Phase 3 artifacts are accumulated independently so every round
 * keeps its own Debrief Card and the paper-level report remains distinct.
 */

"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  fetchDebriefs,
  fetchFinalReport,
  fetchTurns,
  fetchVersionDiffs,
  openAuditStream,
} from "@/lib/api";
import type {
  AuditStatus,
  AuditRoundEvent,
  DebriefCard,
  FinalReport,
  Turn,
  TurnsListResponse,
  Verdict,
  VersionDiff,
} from "@/types";

const POLL_INTERVAL_MS = 2_000;
const RECONNECT_INTERVAL_MS = 5_000;
const HEARTBEAT_TIMEOUT_MS = 45_000;
const ARTIFACT_RETRY_DELAYS_MS = [0, 500, 1_000, 2_000, 4_000] as const;

export type AuditConnectionState =
  | "idle"
  | "connecting"
  | "live"
  | "reconnecting"
  | "polling"
  | "done";

export interface UseSSEResult {
  turns: Turn[];
  verdicts: Verdict[];
  debriefs: DebriefCard[];
  /** Compatibility alias for consumers that still show only the first card. */
  debrief: DebriefCard | null;
  finalReport: FinalReport | null;
  versionDiffs: VersionDiff[];
  activeRound: AuditRoundEvent | null;
  processMessage: string | null;
  status: AuditStatus;
  connectionState: AuditConnectionState;
  auditError: string | null;
  transportError: string | null;
  versionDiffError: string | null;
}

interface AuditStreamSnapshot extends UseSSEResult {
  auditId: string | null;
}

function initialSnapshot(auditId: string | null): AuditStreamSnapshot {
  return {
    auditId,
    turns: [],
    verdicts: [],
    debriefs: [],
    debrief: null,
    finalReport: null,
    versionDiffs: [],
    activeRound: null,
    processMessage: null,
    status: auditId ? "in_progress" : "idle",
    connectionState: auditId ? "connecting" : "idle",
    auditError: null,
    transportError: null,
    versionDiffError: null,
  };
}

function turnKey(turn: Turn): string {
  return turn.id || `${turn.round_id || turn.round_topic || "legacy"}:${turn.exchange_number}:${turn.sequence}:${turn.agent_type}`;
}

function verdictKey(verdict: Verdict): string {
  return verdict.id || `${verdict.round_id || verdict.round_topic || "legacy"}:${verdict.exchange_number}:${verdict.verdict_type}`;
}

export function mergeTurns(current: Turn[], incoming: Turn[]): Turn[] {
  const merged = new Map(current.map((turn) => [turnKey(turn), turn]));
  for (const turn of incoming) merged.set(turnKey(turn), turn);
  return [...merged.values()].sort(
    (left, right) =>
      (left.round_number ?? 0) - (right.round_number ?? 0) ||
      left.exchange_number - right.exchange_number ||
      left.sequence - right.sequence ||
      turnKey(left).localeCompare(turnKey(right)),
  );
}

export function mergeVerdicts(current: Verdict[], incoming: Verdict[]): Verdict[] {
  const merged = new Map(current.map((verdict) => [verdictKey(verdict), verdict]));
  for (const verdict of incoming) merged.set(verdictKey(verdict), verdict);
  return [...merged.values()].sort(
    (left, right) =>
      (left.round_number ?? 0) - (right.round_number ?? 0) ||
      left.exchange_number - right.exchange_number ||
      verdictKey(left).localeCompare(verdictKey(right)),
  );
}

function debriefKey(debrief: DebriefCard): string {
  return debrief.round_id || debrief.id;
}

export function mergeDebriefs(
  current: DebriefCard[],
  incoming: DebriefCard[],
): DebriefCard[] {
  const merged = new Map(current.map((debrief) => [debriefKey(debrief), debrief]));
  for (const debrief of incoming) merged.set(debriefKey(debrief), debrief);
  return [...merged.values()].sort(
    (left, right) =>
      (left.round_number ?? 0) - (right.round_number ?? 0) ||
      debriefKey(left).localeCompare(debriefKey(right)),
  );
}

function versionDiffKey(diff: VersionDiff): string {
  return diff.id || `${diff.audit_id_old}:${diff.audit_id_new}:${diff.round_topic}`;
}

function mergeVersionDiffs(
  current: VersionDiff[],
  incoming: VersionDiff[],
): VersionDiff[] {
  const merged = new Map(current.map((diff) => [versionDiffKey(diff), diff]));
  for (const diff of incoming) merged.set(versionDiffKey(diff), diff);
  return [...merged.values()];
}

function roundFromPayload(value: {
  round_id?: string | null;
  round_number?: number | null;
  round_topic?: string | null;
  round_topic_name?: string;
}): AuditRoundEvent | null {
  if (!value.round_id || !value.round_topic) return null;
  return {
    round_id: value.round_id,
    round_number: value.round_number ?? 0,
    round_topic: value.round_topic,
    round_topic_name: value.round_topic_name,
  };
}

interface AuditStreamEvent {
  type: string;
  data: string;
  id?: string;
}

function eventData<T>(data: string): T | null {
  try {
    return JSON.parse(data) as T;
  } catch {
    return null;
  }
}

function parseEventLines(lines: string[]): AuditStreamEvent | null {
  let type = "message";
  let id: string | undefined;
  const data: string[] = [];
  let hasEventField = false;

  for (const line of lines) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);

    if (field === "event") {
      type = value || "message";
      hasEventField = true;
    } else if (field === "data") {
      data.push(value);
      hasEventField = true;
    } else if (field === "id" && !value.includes("\0")) {
      id = value;
      hasEventField = true;
    }
  }

  return hasEventField ? { type, data: data.join("\n"), id } : null;
}

async function consumeEventStream(
  response: Response,
  onEvent: (event: AuditStreamEvent) => void,
): Promise<void> {
  if (!response.body) {
    throw new ApiError("The live audit stream returned no response body.", 502);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let eventLines: string[] = [];

  const consumeLine = (rawLine: string) => {
    const line = rawLine.endsWith("\r") ? rawLine.slice(0, -1) : rawLine;
    if (line !== "") {
      eventLines.push(line);
      return;
    }
    const event = parseEventLines(eventLines);
    eventLines = [];
    if (event) onEvent(event);
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let newline = buffer.indexOf("\n");
      while (newline >= 0) {
        consumeLine(buffer.slice(0, newline));
        buffer = buffer.slice(newline + 1);
        newline = buffer.indexOf("\n");
      }
    }

    buffer += decoder.decode();
    if (buffer) consumeLine(buffer);
    const trailingEvent = parseEventLines(eventLines);
    if (trailingEvent) onEvent(trailingEvent);
  } finally {
    reader.releaseLock();
  }
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null &&
    "name" in error && (error as { name?: unknown }).name === "AbortError";
}

function failureMessage(data: TurnsListResponse): string {
  return data.error_message || data.error || "The audit failed while processing this paper.";
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "The audit service is temporarily unreachable.";
}

export function useSSE(
  auditId: string | null,
  compareToAuditId?: string | null,
): UseSSEResult {
  const [snapshot, setSnapshot] = useState<AuditStreamSnapshot>(() => initialSnapshot(null));

  useEffect(() => {
    if (!auditId) return;

    let disposed = false;
    let terminal = false;
    let streamController: AbortController | null = null;
    let streamOpen = false;
    let lastEventId = "";
    let pollingEnabled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
    let snapshotRequest: Promise<TurnsListResponse> | null = null;
    let debriefRequest: Promise<DebriefCard[]> | null = null;

    const update = (
      apply: (current: AuditStreamSnapshot) => AuditStreamSnapshot,
    ): void => {
      if (disposed) return;
      setSnapshot((current) =>
        apply(current.auditId === auditId ? current : initialSnapshot(auditId)),
      );
    };

    const stopPolling = (): void => {
      pollingEnabled = false;
      if (pollTimer) clearTimeout(pollTimer);
      pollTimer = null;
    };

    const clearHeartbeat = (): void => {
      if (heartbeatTimer) clearTimeout(heartbeatTimer);
      heartbeatTimer = null;
    };

    const clearReconnect = (): void => {
      if (reconnectTimer) clearTimeout(reconnectTimer);
      reconnectTimer = null;
    };

    const closeStream = (): void => {
      clearHeartbeat();
      clearReconnect();
      streamOpen = false;
      const controller = streamController;
      streamController = null;
      controller?.abort();
    };

    const requestSnapshot = (): Promise<TurnsListResponse> => {
      if (!snapshotRequest) {
        snapshotRequest = fetchTurns(auditId).finally(() => {
          snapshotRequest = null;
        });
      }
      return snapshotRequest;
    };

    const requestDebriefs = (): Promise<DebriefCard[]> => {
      if (!debriefRequest) {
        debriefRequest = fetchDebriefs(auditId).finally(() => {
          debriefRequest = null;
        });
      }
      return debriefRequest;
    };

    const mergeServerDebriefs = (debriefs: DebriefCard[]): void => {
      update((current) => {
        const merged = mergeDebriefs(current.debriefs, debriefs);
        return {
          ...current,
          debriefs: merged,
          debrief: merged[0] || null,
        };
      });
    };

    const mergeServerData = (data: TurnsListResponse): void => {
      update((current) => ({
        ...current,
        turns: mergeTurns(current.turns, data.turns),
        verdicts: mergeVerdicts(current.verdicts, data.verdicts),
        activeRound:
          roundFromPayload(data.turns.at(-1) || data.verdicts.at(-1) || {}) ||
          current.activeRound,
        transportError: null,
      }));
    };

    const streamIsOpen = (): boolean =>
      streamOpen && Boolean(streamController && !streamController.signal.aborted);

    const loadArtifacts = async (): Promise<void> => {
      let debriefsLoaded = false;
      let reportLoaded = false;
      let diffsLoaded = false;
      let lastArtifactError: unknown = null;
      let lastDiffError: unknown = null;
      for (const delay of ARTIFACT_RETRY_DELAYS_MS) {
        if (disposed) return;
        if (delay > 0) {
          await new Promise((resolve) => setTimeout(resolve, delay));
          if (disposed) return;
        }
        const [debriefResult, reportResult, diffResult] = await Promise.allSettled([
          debriefsLoaded ? Promise.resolve<DebriefCard[] | null>(null) : fetchDebriefs(auditId),
          reportLoaded ? Promise.resolve<FinalReport | null>(null) : fetchFinalReport(auditId),
          diffsLoaded ? Promise.resolve<VersionDiff[] | null>(null) : fetchVersionDiffs(auditId, compareToAuditId),
        ]);

        const debriefs = debriefResult.status === "fulfilled" ? debriefResult.value : null;
        const finalReport = reportResult.status === "fulfilled" ? reportResult.value : null;
        const versionDiffs = diffResult.status === "fulfilled" ? diffResult.value : null;
        if (debriefResult.status === "rejected") lastArtifactError = debriefResult.reason;
        if (reportResult.status === "rejected") lastArtifactError = reportResult.reason;
        if (diffResult.status === "rejected") lastDiffError = diffResult.reason;
        if (debriefs) debriefsLoaded = true;
        if (finalReport) reportLoaded = true;
        if (versionDiffs) diffsLoaded = true;

        if (debriefs || finalReport || versionDiffs) {
          update((current) => {
            const mergedDebriefs = debriefs
              ? mergeDebriefs(current.debriefs, debriefs)
              : current.debriefs;
            return {
              ...current,
              debriefs: mergedDebriefs,
              debrief: mergedDebriefs[0] || null,
              finalReport: finalReport || current.finalReport,
              versionDiffs: versionDiffs
                ? mergeVersionDiffs(current.versionDiffs, versionDiffs)
                : current.versionDiffs,
              transportError: debriefsLoaded && reportLoaded ? null : current.transportError,
              versionDiffError: diffsLoaded ? null : current.versionDiffError,
            };
          });
        }
        if (debriefsLoaded && reportLoaded && diffsLoaded) return;
      }
      update((current) => ({
        ...current,
        transportError: debriefsLoaded && reportLoaded
          ? current.transportError
          : current.transportError || `The audit completed, but some saved report artifacts could not be loaded: ${errorText(lastArtifactError)}`,
        versionDiffError: diffsLoaded
          ? current.versionDiffError
          : current.versionDiffError || `The saved revision comparison could not be loaded: ${errorText(lastDiffError)}`,
      }));
    };

    const failAudit = (message: string): void => {
      if (terminal) return;
      terminal = true;
      stopPolling();
      closeStream();
      update((current) => ({
        ...current,
        status: "error",
        connectionState: "done",
        processMessage: null,
        auditError: message,
        transportError: null,
      }));
      void requestDebriefs()
        .then(mergeServerDebriefs)
        .catch(() => {
          // A terminal 409 means no additional debrief artifacts are available;
          // partial cards already observed over SSE remain in state.
        });
    };

    const finishFromSnapshot = (data: TurnsListResponse): void => {
      if (terminal) return;
      terminal = true;
      stopPolling();
      closeStream();
      update((current) => ({
        ...current,
        turns: mergeTurns(current.turns, data.turns),
        verdicts: mergeVerdicts(current.verdicts, data.verdicts),
        status: "completed",
        connectionState: "done",
        processMessage: null,
        auditError: null,
        transportError: null,
      }));
      void loadArtifacts();
    };

    const finishFromEvent = (): void => {
      if (terminal) return;
      terminal = true;
      stopPolling();
      closeStream();
      update((current) => ({
        ...current,
        connectionState: "done",
        processMessage: null,
      }));

      void (async () => {
        try {
          const data = await requestSnapshot();
          mergeServerData(data);
          if (data.status.toLowerCase() === "error") {
            update((current) => ({
              ...current,
              status: "error",
              auditError: failureMessage(data),
              transportError: null,
            }));
            return;
          }
        } catch {
          // The success event is authoritative if the final reconciliation is unavailable.
        }

        update((current) => ({
          ...current,
          status: "completed",
          auditError: null,
        }));
        await loadArtifacts();
      })();
    };

    const applyServerStatus = (data: TurnsListResponse): void => {
      mergeServerData(data);
      if (terminal) return;
      const serverStatus = data.status.toLowerCase();
      if (serverStatus === "completed") {
        finishFromSnapshot(data);
      } else if (serverStatus === "error" || serverStatus === "failed") {
        failAudit(failureMessage(data));
      }
    };

    const schedulePoll = (delay: number): void => {
      if (disposed || terminal || !pollingEnabled || pollTimer) return;
      pollTimer = setTimeout(() => {
        pollTimer = null;
        void pollOnce();
      }, delay);
    };

    const pollOnce = async (): Promise<void> => {
      if (disposed || terminal) return;
      try {
        const [data, savedDebriefs] = await Promise.all([
          requestSnapshot(),
          requestDebriefs().catch(() => null),
        ]);
        if (savedDebriefs) mergeServerDebriefs(savedDebriefs);
        applyServerStatus(data);
      } catch (error) {
        if (streamIsOpen()) return;
        if (
          error instanceof ApiError &&
          (error.status === 400 || error.status === 403 || error.status === 404)
        ) {
          failAudit("This saved audit is no longer available for this account.");
          return;
        }
        if (error instanceof ApiError && error.status === 401) {
          failAudit("Your sign-in session expired. Sign in again to resume this audit.");
          return;
        }
        update((current) => ({
          ...current,
          connectionState: "polling",
          transportError: `Live updates are unavailable; retrying: ${errorText(error)}`,
        }));
      } finally {
        if (!disposed && !terminal && pollingEnabled && !streamIsOpen()) {
          schedulePoll(POLL_INTERVAL_MS);
        }
      }
    };

    const startPolling = (message: string): void => {
      if (disposed || terminal || streamIsOpen()) return;
      pollingEnabled = true;
      update((current) => ({
        ...current,
        connectionState: "polling",
        transportError: message,
      }));
      schedulePoll(0);
    };

    const scheduleReconnect = (): void => {
      if (disposed || terminal || reconnectTimer) return;
      reconnectTimer = setTimeout(() => {
        reconnectTimer = null;
        if (disposed || terminal) return;
        update((current) => ({ ...current, connectionState: "reconnecting" }));
        openStream();
      }, RECONNECT_INTERVAL_MS);
    };

    const resetHeartbeat = (): void => {
      clearHeartbeat();
      if (terminal) return;
      heartbeatTimer = setTimeout(() => {
        heartbeatTimer = null;
        streamOpen = false;
        const stalledController = streamController;
        streamController = null;
        stalledController?.abort();
        startPolling("The live connection timed out; recovering from saved audit state.");
        scheduleReconnect();
      }, HEARTBEAT_TIMEOUT_MS);
    };

    const openStream = (): void => {
      if (disposed || terminal || streamController) return;

      const controller = new AbortController();
      streamController = controller;
      streamOpen = false;
      resetHeartbeat();

      void (async () => {
        try {
          const response = await openAuditStream(
            auditId,
            lastEventId,
            controller.signal,
          );
          if (disposed || terminal || streamController !== controller) {
            controller.abort();
            return;
          }

          streamOpen = true;
          stopPolling();
          clearReconnect();
          resetHeartbeat();
          update((current) => ({
            ...current,
            connectionState: "live",
            transportError: null,
          }));

          await consumeEventStream(response, (event) => {
            if (disposed || terminal || streamController !== controller) return;
            if (event.id !== undefined && /^\d+$/.test(event.id)) {
              lastEventId = event.id;
            }
            // Every data event proves that the connection is healthy; terminal
            // artifacts are still reconciled from durable endpoints below.
            resetHeartbeat();

            if (event.type === "heartbeat") {
              return;
            }
            if (event.type === "round_start") {
              const round = eventData<AuditRoundEvent>(event.data);
              if (!round?.round_id || !round.round_topic) return;
              update((current) => ({
                ...current,
                activeRound: round,
                processMessage: `Starting ${round.round_topic_name || round.round_topic}`,
              }));
              return;
            }
            if (event.type === "turn") {
              const turn = eventData<Turn>(event.data);
              if (!turn) return;
              update((current) => ({
                ...current,
                turns: mergeTurns(current.turns, [turn]),
                activeRound: roundFromPayload(turn) || current.activeRound,
                processMessage: null,
              }));
              return;
            }
            if (event.type === "verdict") {
              const verdict = eventData<Verdict>(event.data);
              if (!verdict) return;
              update((current) => ({
                ...current,
                verdicts: mergeVerdicts(current.verdicts, [verdict]),
                activeRound: roundFromPayload(verdict) || current.activeRound,
              }));
              return;
            }
            if (event.type === "process_update") {
              const data = eventData<{
                message?: string;
                round_id?: string | null;
                round_number?: number | null;
                round_topic?: string | null;
              }>(event.data);
              if (data?.message) {
                update((current) => ({
                  ...current,
                  processMessage: data.message || null,
                  activeRound: roundFromPayload(data) || current.activeRound,
                }));
              }
              return;
            }
            if (event.type === "debrief") {
              const debrief = eventData<DebriefCard>(event.data);
              if (!debrief) return;
              update((current) => {
                const debriefs = mergeDebriefs(current.debriefs, [debrief]);
                return {
                  ...current,
                  debriefs,
                  debrief: debriefs[0] || null,
                  activeRound: roundFromPayload(debrief) || current.activeRound,
                  processMessage: null,
                };
              });
              return;
            }
            if (event.type === "final_report") {
              const finalReport = eventData<FinalReport>(event.data);
              if (!finalReport) return;
              update((current) => ({
                ...current,
                finalReport,
                processMessage: null,
              }));
              return;
            }
            if (event.type === "version_diffs") {
              const data = eventData<{ items?: VersionDiff[] }>(event.data);
              if (!data?.items) return;
              update((current) => ({
                ...current,
                versionDiffs: mergeVersionDiffs(current.versionDiffs, data.items || []),
                versionDiffError: null,
              }));
              return;
            }
            if (event.type === "version_diff_error") {
              const data = eventData<{ message?: string }>(event.data);
              update((current) => ({
                ...current,
                versionDiffError: data?.message || "The revision comparison could not be generated.",
              }));
              return;
            }
            if (event.type === "audit_error") {
              const data = eventData<{ message?: string }>(event.data);
              failAudit(data?.message || "The audit failed while processing this paper.");
              return;
            }
            if (event.type === "complete") {
              finishFromEvent();
            }
          });

          if (disposed || terminal || streamController !== controller) return;
          streamOpen = false;
          streamController = null;
          clearHeartbeat();
          startPolling("The live connection ended; recovering from saved audit state.");
          scheduleReconnect();
        } catch (error) {
          const ownsStream = streamController === controller;
          if (ownsStream) {
            streamController = null;
            streamOpen = false;
            clearHeartbeat();
          }
          if (disposed || terminal || isAbortError(error)) return;
          if (error instanceof ApiError && error.status === 401) {
            failAudit("Your sign-in session expired. Sign in again to resume this audit.");
            return;
          }
          if (
            error instanceof ApiError &&
            (error.status === 400 || error.status === 403 || error.status === 404)
          ) {
            failAudit("This saved audit is no longer available for this account.");
            return;
          }
          startPolling(`Could not open live updates; using recovery polling: ${errorText(error)}`);
          scheduleReconnect();
        }
      })();
    };

    void (async () => {
      try {
        const [data, savedDebriefs] = await Promise.all([
          requestSnapshot(),
          requestDebriefs().catch(() => null),
        ]);
        if (savedDebriefs) mergeServerDebriefs(savedDebriefs);
        applyServerStatus(data);
      } catch (error) {
        if (
          error instanceof ApiError &&
          (error.status === 400 || error.status === 403 || error.status === 404)
        ) {
          failAudit("This saved audit is no longer available for this account.");
        } else if (error instanceof ApiError && error.status === 401) {
          failAudit("Your sign-in session expired. Sign in again to resume this audit.");
        } else if (!streamIsOpen()) {
          startPolling(`Could not load the initial audit state; retrying: ${errorText(error)}`);
        }
      }
    })();
    openStream();

    return () => {
      disposed = true;
      stopPolling();
      closeStream();
    };
  }, [auditId, compareToAuditId]);

  const current = snapshot.auditId === auditId ? snapshot : initialSnapshot(auditId);
  return {
    turns: current.turns,
    verdicts: current.verdicts,
    debriefs: current.debriefs,
    debrief: current.debrief,
    finalReport: current.finalReport,
    versionDiffs: current.versionDiffs,
    activeRound: current.activeRound,
    processMessage: current.processMessage,
    status: current.status,
    connectionState: current.connectionState,
    auditError: current.auditError,
    transportError: current.transportError,
    versionDiffError: current.versionDiffError,
  };
}
