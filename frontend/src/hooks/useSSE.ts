/**
 * Live audit state with SSE delivery and a non-overlapping polling fallback.
 *
 * SSE is an optimization, not the source of truth. Every mount reconciles the
 * database snapshot first, and polling takes over immediately if the stream is
 * interrupted. Application failures arrive on the dedicated `audit_error`
 * event; the native EventSource `error` event is transport-only.
 */

"use client";

import { useEffect, useState } from "react";
import { ApiError, fetchDebrief, fetchTurns, streamUrl } from "@/lib/api";
import type {
  AuditStatus,
  DebriefCard,
  Turn,
  TurnsListResponse,
  Verdict,
} from "@/types";

const POLL_INTERVAL_MS = 2_000;
const RECONNECT_INTERVAL_MS = 5_000;
const HEARTBEAT_TIMEOUT_MS = 45_000;
const DEBRIEF_RETRY_DELAYS_MS = [0, 500, 1_000, 2_000, 4_000] as const;

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
  debrief: DebriefCard | null;
  processMessage: string | null;
  status: AuditStatus;
  connectionState: AuditConnectionState;
  auditError: string | null;
  transportError: string | null;
}

interface AuditStreamSnapshot extends UseSSEResult {
  auditId: string | null;
}

function initialSnapshot(auditId: string | null): AuditStreamSnapshot {
  return {
    auditId,
    turns: [],
    verdicts: [],
    debrief: null,
    processMessage: null,
    status: auditId ? "in_progress" : "idle",
    connectionState: auditId ? "connecting" : "idle",
    auditError: null,
    transportError: null,
  };
}

function turnKey(turn: Turn): string {
  return turn.id || `${turn.exchange_number}:${turn.sequence}:${turn.agent_type}`;
}

function verdictKey(verdict: Verdict): string {
  return verdict.id || `${verdict.exchange_number}:${verdict.verdict_type}`;
}

export function mergeTurns(current: Turn[], incoming: Turn[]): Turn[] {
  const merged = new Map(current.map((turn) => [turnKey(turn), turn]));
  for (const turn of incoming) merged.set(turnKey(turn), turn);
  return [...merged.values()].sort(
    (left, right) =>
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
      left.exchange_number - right.exchange_number ||
      verdictKey(left).localeCompare(verdictKey(right)),
  );
}

function eventData<T>(event: Event): T | null {
  if (!(event instanceof MessageEvent) || typeof event.data !== "string") return null;
  try {
    return JSON.parse(event.data) as T;
  } catch {
    return null;
  }
}

function failureMessage(data: TurnsListResponse): string {
  return data.error_message || data.error || "The audit failed while processing this paper.";
}

function errorText(error: unknown): string {
  return error instanceof Error && error.message
    ? error.message
    : "The audit service is temporarily unreachable.";
}

export function useSSE(auditId: string | null): UseSSEResult {
  const [snapshot, setSnapshot] = useState<AuditStreamSnapshot>(() => initialSnapshot(null));

  useEffect(() => {
    if (!auditId) return;

    let disposed = false;
    let terminal = false;
    let source: EventSource | null = null;
    let pollingEnabled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
    let snapshotRequest: Promise<TurnsListResponse> | null = null;
    let receivedDebrief: DebriefCard | null = null;

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
      source?.close();
      source = null;
    };

    const requestSnapshot = (): Promise<TurnsListResponse> => {
      if (!snapshotRequest) {
        snapshotRequest = fetchTurns(auditId).finally(() => {
          snapshotRequest = null;
        });
      }
      return snapshotRequest;
    };

    const mergeServerData = (data: TurnsListResponse): void => {
      update((current) => ({
        ...current,
        turns: mergeTurns(current.turns, data.turns),
        verdicts: mergeVerdicts(current.verdicts, data.verdicts),
        transportError: null,
      }));
    };

    const streamIsOpen = (): boolean => source?.readyState === EventSource.OPEN;

    const loadDebrief = async (): Promise<void> => {
      if (receivedDebrief) return;
      let lastError: unknown = null;
      for (const delay of DEBRIEF_RETRY_DELAYS_MS) {
        if (disposed) return;
        if (delay > 0) {
          await new Promise((resolve) => setTimeout(resolve, delay));
          if (disposed) return;
        }
        try {
          const debrief = await fetchDebrief(auditId);
          receivedDebrief = debrief;
          update((current) => ({ ...current, debrief, transportError: null }));
          return;
        } catch (error) {
          lastError = error;
          if (error instanceof ApiError && error.status !== 404 && error.status < 500) {
            break;
          }
        }
      }
      if (receivedDebrief) return;
      update((current) => ({
        ...current,
        transportError:
          current.transportError ||
          `The audit completed, but its final debrief could not be loaded: ${errorText(lastError)}`,
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
      void loadDebrief();
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
        await loadDebrief();
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
        const data = await requestSnapshot();
        applyServerStatus(data);
      } catch (error) {
        if (streamIsOpen()) return;
        if (
          error instanceof ApiError &&
          (error.status === 400 || error.status === 403 || error.status === 404)
        ) {
          failAudit("This saved audit is no longer available for this browser session.");
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
        source?.close();
        source = null;
        startPolling("The live connection timed out; recovering from saved audit state.");
        scheduleReconnect();
      }, HEARTBEAT_TIMEOUT_MS);
    };

    const openStream = (): void => {
      if (disposed || terminal || source) return;

      try {
        const nextSource = new EventSource(streamUrl(auditId));
        source = nextSource;
        resetHeartbeat();

        nextSource.addEventListener("open", () => {
          if (disposed || terminal || source !== nextSource) return;
          stopPolling();
          clearReconnect();
          resetHeartbeat();
          update((current) => ({
            ...current,
            connectionState: "live",
            transportError: null,
          }));
        });

        nextSource.addEventListener("turn", (event) => {
          if (terminal) return;
          resetHeartbeat();
          const turn = eventData<Turn>(event);
          if (!turn) return;
          update((current) => ({
            ...current,
            turns: mergeTurns(current.turns, [turn]),
            processMessage: null,
          }));
        });

        nextSource.addEventListener("verdict", (event) => {
          if (terminal) return;
          resetHeartbeat();
          const verdict = eventData<Verdict>(event);
          if (!verdict) return;
          update((current) => ({
            ...current,
            verdicts: mergeVerdicts(current.verdicts, [verdict]),
          }));
        });

        nextSource.addEventListener("process_update", (event) => {
          if (terminal) return;
          resetHeartbeat();
          const data = eventData<{ message?: string }>(event);
          if (data?.message) {
            update((current) => ({ ...current, processMessage: data.message || null }));
          }
        });

        nextSource.addEventListener("debrief", (event) => {
          if (terminal) return;
          resetHeartbeat();
          const debrief = eventData<DebriefCard>(event);
          if (!debrief) return;
          receivedDebrief = debrief;
          update((current) => ({ ...current, debrief, processMessage: null }));
        });

        nextSource.addEventListener("heartbeat", () => {
          if (!terminal) resetHeartbeat();
        });

        nextSource.addEventListener("audit_error", (event) => {
          const data = eventData<{ message?: string }>(event);
          failAudit(data?.message || "The audit failed while processing this paper.");
        });

        nextSource.addEventListener("complete", () => {
          finishFromEvent();
        });

        nextSource.addEventListener("error", () => {
          if (disposed || terminal || source !== nextSource) return;
          clearHeartbeat();
          startPolling("The live connection was interrupted; recovering from saved audit state.");
          if (nextSource.readyState === EventSource.CLOSED) {
            nextSource.close();
            source = null;
            scheduleReconnect();
          } else {
            update((current) => ({ ...current, connectionState: "reconnecting" }));
          }
        });
      } catch (error) {
        source = null;
        startPolling(`Could not open live updates; using recovery polling: ${errorText(error)}`);
        scheduleReconnect();
      }
    };

    void (async () => {
      try {
        const data = await requestSnapshot();
        applyServerStatus(data);
      } catch (error) {
        if (
          error instanceof ApiError &&
          (error.status === 400 || error.status === 403 || error.status === 404)
        ) {
          failAudit("This saved audit is no longer available for this browser session.");
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
  }, [auditId]);

  const current = snapshot.auditId === auditId ? snapshot : initialSnapshot(auditId);
  return {
    turns: current.turns,
    verdicts: current.verdicts,
    debrief: current.debrief,
    processMessage: current.processMessage,
    status: current.status,
    connectionState: current.connectionState,
    auditError: current.auditError,
    transportError: current.transportError,
  };
}
