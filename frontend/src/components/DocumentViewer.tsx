"use client";

import { useEffect, useState } from "react";
import { PIcon } from "@porsche-design-system/components-react";
import { pdfUrl, verifyPdfAccess } from "@/lib/api";

interface DocumentViewerProps {
  paperId: string;
  highlightedPages: number[];
}

export default function DocumentViewer({
  paperId,
  highlightedPages,
}: DocumentViewerProps) {
  const url = pdfUrl(paperId);
  const [selectedPage, setSelectedPage] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const [accessReady, setAccessReady] = useState(false);
  const [accessError, setAccessError] = useState<string | null>(null);
  const [probeAttempt, setProbeAttempt] = useState(0);
  const embedUrl = selectedPage ? `${url}#page=${selectedPage}` : `${url}#view=FitH`;

  useEffect(() => {
    let active = true;
    void verifyPdfAccess(paperId)
      .then(() => {
        if (!active) return;
        setAccessReady(true);
        setAccessError(null);
        setLoading(true);
      })
      .catch((error: unknown) => {
        if (!active) return;
        setAccessReady(false);
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
        <a className="viewer-open" href={url} target="_blank" rel="noopener noreferrer">
          Open PDF <PIcon name="external" />
        </a>
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
                setAccessReady(false);
                setAccessError(null);
                setLoading(true);
                setProbeAttempt((attempt) => attempt + 1);
              }}
            >
              Try again
            </button>
          </div>
        )}
        {accessReady && !accessError && (
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
