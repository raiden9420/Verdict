import type { ComponentPropsWithoutRef } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import styles from "./MarkdownReport.module.css";

type MarkdownReportProps = {
  content: string;
  variant?: "report" | "compact";
};

function MarkdownLink({ href, children, ...props }: ComponentPropsWithoutRef<"a">) {
  const opensNewWindow = Boolean(href && /^https?:\/\//i.test(href));

  return (
    <a
      {...props}
      href={href}
      {...(opensNewWindow ? { rel: "noopener noreferrer", target: "_blank" } : {})}
    >
      {children}
    </a>
  );
}

/**
 * Render the stored report as Markdown without ever interpreting embedded HTML.
 * Reports can contain model-generated text, so images are also represented as
 * labels instead of initiating requests to arbitrary remote URLs.
 */
export function MarkdownReport({ content, variant = "report" }: MarkdownReportProps) {
  return (
    <div className={`${styles.report} ${variant === "compact" ? styles.compact : ""}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          a: MarkdownLink,
          img: ({ alt }) => (
            <span className={styles.imagePlaceholder}>
              {alt ? `[Image: ${alt}]` : "[Image omitted]"}
            </span>
          ),
          table: ({ children, ...props }) => (
            <div
              aria-label="Report data table"
              className={styles.tableScroller}
              role="region"
              tabIndex={0}
            >
              <table {...props}>{children}</table>
            </div>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
