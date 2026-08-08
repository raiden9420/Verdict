/**
 * useSSE — Server-Sent Events hook with automatic polling fallback.
 *
 * Connects to the SSE stream. If the connection drops (3 missed heartbeats
 * or error), automatically switches to polling /turns every 2 seconds.
 * When the audit completes, fetches the debrief card.
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { streamUrl, fetchTurns, fetchDebrief } from "@/lib/api";
import type { Turn, Verdict, DebriefCard, AuditStatus } from "@/types";

const POLL_INTERVAL_MS = 2000;
const HEARTBEAT_TIMEOUT_MS = 45_000; // 30s heartbeat + 15s grace

interface UseSSEResult {
  turns: Turn[];
  verdicts: Verdict[];
  debrief: DebriefCard | null;
  skippedExchanges: number[];
  processMessage: string | null;
  status: AuditStatus;
  connectionState: "live" | "reconnecting" | "polling" | "done";
  error: string | null;
}

export function useSSE(auditId: string | null): UseSSEResult {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [verdicts, setVerdicts] = useState<Verdict[]>([]);
  const [debrief, setDebrief] = useState<DebriefCard | null>(null);
  const [skippedExchanges, setSkippedExchanges] = useState<number[]>([]);
  const [processMessage, setProcessMessage] = useState<string | null>(null);
  const [status, setStatus] = useState<AuditStatus>("in_progress");
  const [connectionState, setConnectionState] = useState<
    "live" | "reconnecting" | "polling" | "done"
  >("live");
  const [error, setError] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ---- Polling fallback ----
  const startPolling = useCallback(() => {
    if (!auditId || pollTimerRef.current) return;
    setConnectionState("polling");

    pollTimerRef.current = setInterval(async () => {
      try {
        const data = await fetchTurns(auditId);
        setTurns(data.turns);
        setVerdicts(data.verdicts);

        if (data.status === "completed") {
          stopPolling();
          setStatus("completed");
          setConnectionState("done");
          setProcessMessage(null);
          // Fetch debrief
          try {
            const d = await fetchDebrief(auditId);
            setDebrief(d);
          } catch {
            // might not be ready yet
          }
        } else if (data.status === "error") {
          stopPolling();
          setStatus("error");
          setConnectionState("done");
          setProcessMessage(null);
          setError("Audit failed");
        }
      } catch {
        // Polling error — keep trying
      }
    }, POLL_INTERVAL_MS);
  }, [auditId]);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  // ---- Heartbeat watchdog ----
  const resetHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current) {
      clearTimeout(heartbeatTimerRef.current);
    }
    heartbeatTimerRef.current = setTimeout(() => {
      // Heartbeat missed — switch to polling
      setConnectionState("reconnecting");
      eventSourceRef.current?.close();
      startPolling();
    }, HEARTBEAT_TIMEOUT_MS);
  }, [startPolling]);

  // ---- SSE connection ----
  useEffect(() => {
    if (!auditId) return;

    const url = streamUrl(auditId);
    const es = new EventSource(url);
    eventSourceRef.current = es;
    setConnectionState("live");
    resetHeartbeat();

    es.addEventListener("turn", (e) => {
      resetHeartbeat();
      setProcessMessage(null);
      try {
        const turn: Turn = JSON.parse(e.data);
        setTurns((prev) => [...prev, turn]);
      } catch { /* ignore parse errors */ }
    });

    es.addEventListener("verdict", (e) => {
      resetHeartbeat();
      try {
        const v: Verdict = JSON.parse(e.data);
        setVerdicts((prev) => [...prev, v]);
      } catch { /* ignore */ }
    });

    es.addEventListener("process_update", (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        if (data.message) {
          setProcessMessage(data.message);
        }
      } catch { /* ignore */ }
    });

    es.addEventListener("debrief", (e) => {
      resetHeartbeat();
      setProcessMessage(null);
      try {
        const d: DebriefCard = JSON.parse(e.data);
        setDebrief(d);
      } catch { /* ignore */ }
    });

    es.addEventListener("complete", () => {
      setStatus("completed");
      setConnectionState("done");
      setProcessMessage(null);
      es.close();
    });

    es.addEventListener("exchange_skipped", (e) => {
      resetHeartbeat();
      try {
        const data = JSON.parse(e.data);
        if (data.exchange_number != null) {
          setSkippedExchanges((prev) => [...prev, data.exchange_number]);
        }
      } catch { /* ignore parse errors */ }
    });

    es.addEventListener("error", (e) => {
      // EventSource "error" can be a temporary reconnect or a fatal close
      if (es.readyState === EventSource.CLOSED) {
        setConnectionState("reconnecting");
        startPolling();
      }
    });

    es.addEventListener("heartbeat", () => {
      resetHeartbeat();
    });

    return () => {
      es.close();
      stopPolling();
      if (heartbeatTimerRef.current) {
        clearTimeout(heartbeatTimerRef.current);
      }
    };
  }, [auditId, resetHeartbeat, startPolling, stopPolling]);

  return { turns, verdicts, debrief, skippedExchanges, processMessage, status, connectionState, error };
}

