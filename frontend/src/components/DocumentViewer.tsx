/**
 * DocumentViewer — renders PDF pages with page-level citation highlighting.
 *
 * Uses an <iframe> for the PDF since react-pdf requires additional setup.
 * When citations are clicked in the LiveArena, highlighted page numbers
 * are shown as badges and the viewer scrolls approximately to the right area.
 *
 * Phase 2 upgrade: replace with react-pdf for per-page canvas rendering
 * and precise text-span highlighting.
 */

"use client";

import { useEffect, useState, useCallback } from "react";
import { pdfUrl } from "@/lib/api";

interface DocumentViewerProps {
  paperId: string;
  highlightedPages: number[];
  pageCount?: number;
}

export default function DocumentViewer({
  paperId,
  highlightedPages,
  pageCount,
}: DocumentViewerProps) {
  const url = pdfUrl(paperId);
  const embedUrl = `${url}#toolbar=0&navpanes=0`;
  const [currentPage, setCurrentPage] = useState<number | null>(null);

  // When highlighted pages change, navigate to the first one
  useEffect(() => {
    if (highlightedPages.length > 0) {
      const page = highlightedPages[0];
      setCurrentPage(page);
      // For the iframe PDF viewer, we can append #page=N to navigate
      const iframe = document.getElementById("pdf-viewer") as HTMLIFrameElement;
      if (iframe) {
        iframe.src = `${url}#page=${page}&toolbar=0&navpanes=0`;
      }
    }
  }, [highlightedPages, url]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        overflow: "hidden",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "1rem 1.25rem",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexShrink: 0,
        }}
      >
        <h2 style={{ fontWeight: 700, fontSize: "1rem" }}>📄 Document</h2>
        <div style={{ display: "flex", gap: "0.35rem", alignItems: "center" }}>
          {highlightedPages.length > 0 && (
            <>
              <span style={{ fontSize: "0.75rem", color: "var(--text-muted)" }}>
                Cited pages:
              </span>
              {highlightedPages.map((p) => (
                <button
                  key={p}
                  onClick={() => {
                    const iframe = document.getElementById("pdf-viewer") as HTMLIFrameElement;
                    if (iframe) iframe.src = `${url}#page=${p}&toolbar=0&navpanes=0`;
                  }}
                  style={{
                    background: "rgba(124, 92, 252, 0.2)",
                    border: "1px solid rgba(124, 92, 252, 0.3)",
                    color: "var(--accent)",
                    padding: "0.1rem 0.45rem",
                    borderRadius: "9999px",
                    fontSize: "0.7rem",
                    cursor: "pointer",
                    fontFamily: "'JetBrains Mono', monospace",
                    fontWeight: 600,
                  }}
                >
                  p.{p}
                </button>
              ))}
            </>
          )}
        </div>
      </div>

      {/* PDF embed */}
      <div style={{ flex: 1, overflow: "hidden" }}>
        <iframe
          id="pdf-viewer"
          src={embedUrl}
          style={{
            width: "100%",
            height: "100%",
            border: "none",
            background: "#1a1a2e",
          }}
          title="Paper PDF"
        />
      </div>
    </div>
  );
}
