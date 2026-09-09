"use client";

import { useEffect, useRef, useState } from "react";
import { fetchPdfUrl } from "@/lib/api";
import { signedUrlRefreshDelay } from "@/lib/audit-evidence";
import styles from "./AuditArena.module.css";

export interface SampleSource {
  title: string;
  pages: Array<{ page: number; title?: string; body: string }>;
}

interface DocumentViewerProps {
  paperId: string;
  highlightedPages: number[];
  source?: SampleSource;
  selectedPage?: number | null;
  onPageChange?: (page: number) => void;
}

export default function DocumentViewer({ paperId, highlightedPages, source, selectedPage, onPageChange }: DocumentViewerProps) {
  const [localPage, setLocalPage] = useState<number | null>(null);
  const [signedUrl, setSignedUrl] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(!source);
  const [refreshing, setRefreshing] = useState(false);
  const [failed, setFailed] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  const refreshRef = useRef<(() => Promise<void>) | null>(null);
  const currentUrlRef = useRef<string | null>(null);
  const page = selectedPage ?? localPage;
  const samplePage = source?.pages.find((item) => item.page === page) || source?.pages[0];
  const embedUrl = previewUrl ? `${previewUrl}#${page ? `page=${page}` : "view=FitH"}` : null;

  useEffect(() => {
    if (source) return;
    let active = true;
    let inFlight = false;
    let expiresAt = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const refresh = async () => {
      if (!active || inFlight) return;
      inFlight = true;
      setRefreshing(true);
      if (timer) clearTimeout(timer);
      try {
        const { url, expires_in: expiresIn } = await fetchPdfUrl(paperId);
        if (!active) return;
        setSignedUrl(url);
        currentUrlRef.current = url;
        // Keep an already loaded preview stable while its open-link is renewed.
        setPreviewUrl((current) => current || url);
        setAccessError(null);
        expiresAt = Date.now() + (Number.isFinite(expiresIn) && expiresIn > 0 ? expiresIn : 300) * 1_000;
        timer = setTimeout(() => { void refresh(); }, signedUrlRefreshDelay(expiresIn));
      } catch (error) {
        if (!active) return;
        setAccessError(error instanceof Error ? error.message : "The source paper could not be opened.");
        setLoading(false);
        if (Date.now() >= expiresAt) { setSignedUrl(null); currentUrlRef.current = null; }
        timer = setTimeout(() => { void refresh(); }, 30_000);
      } finally {
        inFlight = false;
        if (active) setRefreshing(false);
      }
    };
    refreshRef.current = refresh;
    const onVisible = () => { if (document.visibilityState === "visible" && Date.now() > expiresAt - 30_000) void refresh(); };
    document.addEventListener("visibilitychange", onVisible);
    void refresh();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
      refreshRef.current = null;
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [paperId, source]);

  useEffect(() => {
    if (source || !page) return;
    // A source chip outside this component can change the selected page. Use
    // the latest access URL on that navigation, without interrupting reading
    // when a background renewal happens on its own.
    const frame = window.requestAnimationFrame(() => {
      if (currentUrlRef.current) setPreviewUrl(currentUrlRef.current);
      setFailed(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [page, source]);

  const navigate = (nextPage: number) => {
    if (nextPage === page && previewUrl === signedUrl) return;
    if (onPageChange) onPageChange(nextPage);
    else setLocalPage(nextPage);
    if (!source) {
      if (signedUrl) setPreviewUrl(signedUrl);
      setLoading(true);
      setFailed(false);
    }
  };

  const retry = async () => {
    setFailed(false);
    setAccessError(null);
    setLoading(true);
    setPreviewUrl(null);
    await refreshRef.current?.();
  };

  return (
    <div className={styles.viewer}>
      <div className={styles.viewerHeader}>
        <div><span className={styles.kicker}>{source ? "Illustrative manuscript" : "Source manuscript"}</span><strong>{page ? `Page ${page}` : "Read alongside the findings"}</strong></div>
        {!source && <div className={styles.viewerActions}>{signedUrl && <a href={signedUrl} target="_blank" rel="noopener noreferrer">Open PDF ↗</a>}<button type="button" disabled={refreshing} onClick={() => { void retry(); }} aria-label="Reload source PDF">{refreshing ? "Connecting…" : "Reload"}</button></div>}
      </div>
      {(source ? source.pages.map((item) => item.page) : highlightedPages).length > 0 && (
        <nav className={styles.pageNavigation} aria-label={source ? "Sample manuscript pages" : "Cited manuscript pages"}>
          <span>{source ? "Pages" : "Cited pages"}</span>
          {(source ? source.pages.map((item) => item.page) : highlightedPages).map((number) => <button type="button" key={number} aria-pressed={(page || samplePage?.page) === number} onClick={() => navigate(number)}>{number}</button>)}
        </nav>
      )}
      {source ? (
        <article key={samplePage?.page} className={styles.samplePage} aria-label={`Illustrative manuscript page ${samplePage?.page || 1}`}>
          <span className={styles.sampleStamp}>ILLUSTRATIVE EXAMPLE · NOT A REAL PAPER</span>
          <h3>{source.title}</h3>
          {samplePage?.title && <h4>{samplePage.title}</h4>}
          {(samplePage?.body || "No source text is available for this page.").split(/\n\s*\n/).map((paragraph, index) => <p key={index}>{paragraph}</p>)}
          <span className={styles.samplePageNumber}>— {samplePage?.page || 1} —</span>
        </article>
      ) : (
        <div className={styles.viewerFrame}>
          {loading && !failed && !accessError && <div className={styles.viewerNotice} role="status">Opening source paper…</div>}
          {(failed || accessError) && <div className={styles.viewerNotice} role="alert"><strong>The PDF preview is unavailable.</strong><span>{accessError || "Try opening the PDF in a separate tab."}</span><button type="button" className={styles.secondaryButton} onClick={() => { void retry(); }} disabled={refreshing}>Try again</button></div>}
          {embedUrl && <iframe key={embedUrl} src={embedUrl} className={styles.pdfFrame} title={page ? `Research manuscript, page ${page}` : "Research manuscript PDF"} onLoad={() => setLoading(false)} onError={() => { setLoading(false); setFailed(true); }} />}
        </div>
      )}
      <p className={styles.viewerFootnote}>{source ? "This example shows how passages connect to findings. It was written to demonstrate the workflow." : "Source passages are text excerpts. Open the PDF to check surrounding text, figures, and tables."}</p>
    </div>
  );
}
