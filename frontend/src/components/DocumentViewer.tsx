"use client";

import { useEffect, useState } from "react";
import { PIcon } from "@porsche-design-system/components-react";
import { fetchPdfUrl } from "@/lib/api";

interface DocumentViewerProps {
  paperId: string;
  highlightedPages: number[];
}

export default function DocumentViewer({
  paperId,
  highlightedPages,
}: DocumentViewerProps) {
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const [signedUrl, setSignedUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [probeAttempt, setProbeAttempt] = useState(0);
  const embedUrl = signedUrl
    ? selectedPage
      ? `${signedUrl}#page=${selectedPage}`
      : `${signedUrl}#view=FitH`
    : null;

  useEffect(() => {
    let active = true;
    void fetchPdfUrl(paperId)
      .then(({ url }) => {
        if (!active) return;
        setSignedUrl(url);
        setAccessError(null);
        setLoading(true);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setSignedUrl(null);
        setLoading(false);
        setAccessError(error instanceof Error ? error.message : "The PDF preview is unavailable.");
      });
    return () => {
      active = false;
    };
  }, [paperId, probeAttempt]);

  const navigateToPage = (page: number) => {
    setSelectedPage(page);
    setLoading(true);
    setFailed(false);
  };

  return (
    <div className="document-viewer">
      <div className="viewer-header">
        <div className="viewer-title"><PIcon name="document" /><strong>Source paper</strong></div>
        {signedUrl ? (
          <a className="viewer-open" href={signedUrl} target="_blank" rel="noopener noreferrer">
            Open PDF <PIcon name="external" />
          </a>
        ) : <span className="viewer-open unavailable" aria-hidden="true">Open PDF</span>}
      </div>

      {highlightedPages.length > 0 && (
        <div className="cited-pages" aria-label="Pages cited in the latest argument">
          <span>Cited pages</span>
          {highlightedPages.map((page) => (
            <button
              type="button"
              key={page}
              className={selectedPage === page ? "active" : ""}
              aria-pressed={selectedPage === page}
              onClick={() => navigateToPage(page)}
            >
              {page}
            </button>
          ))}
        </div>
      )}

      <div className="viewer-frame">
        {loading && !failed && !accessError && <div className="viewer-state" role="status">Loading paper…</div>}
        {(failed || accessError) && (
          <div className="viewer-state error" role="alert">
            <PIcon name="warning" />
            <strong>The embedded preview could not be loaded.</strong>
            {accessError && <span>{accessError}</span>}
            <button
              type="button"
              className="viewer-retry"
              onClick={() => {
                setFailed(false);
                setSignedUrl(null);
                setAccessError(null);
                setLoading(true);
                setProbeAttempt((attempt) => attempt + 1);
              }}
            >
              Try again
            </button>
          </div>
        )}
        {embedUrl && !accessError && (
          <iframe
            key={embedUrl}
            src={embedUrl}
            className="pdf-frame"
            title={selectedPage ? `Research paper, page ${selectedPage}` : "Research paper PDF"}
            onLoad={() => setLoading(false)}
            onError={() => {
              setLoading(false);
              setFailed(true);
            }}
          />
        )}
      </div>
    </div>
  );
}
