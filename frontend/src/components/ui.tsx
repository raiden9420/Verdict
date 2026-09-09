import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

const paths = {
  document: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z M14 2v6h6 M8 13h8 M8 17h6",
  "arrow-right": "M5 12h14 M13 6l6 6-6 6",
  "arrow-left": "M19 12H5 M11 6l-6 6 6 6",
  download: "M12 3v12 M7 10l5 5 5-5 M4 16v5h16v-5",
  upload: "M12 16V4 M7 9l5-5 5 5 M4 16v5h16v-5",
  check: "M5 12l4 4L19 6",
  close: "M6 6l12 12 M6 18L18 6",
  reset: "M3 10a9 9 0 1 1 1 7 M3 4v6h6",
  information: "M12 11v6 M12 7h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  warning: "M12 3L2 21h20z M12 10v4 M12 17h.01",
  "error-filled": "M12 8v5 M12 16h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
  compare: "M9 4H3v16h6 M15 4h6v16h-6 M12 2v20",
  search: "M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  "chevron-down": "M6 9l6 6 6-6",
  lock: "M5 10h14v11H5z M8 10V6a4 4 0 0 1 8 0v4",
  external: "M14 3h7v7 M21 3L10 14 M10 3H3v18h18v-7",
  plus: "M12 5v14 M5 12h14",
  book: "M12 5C8 2 4 3 2 4v16c3-2 7-1 10 1 3-2 7-3 10-1V4c-2-1-6-2-10 1z M12 5v16",
  clock: "M12 6v6l4 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0",
} as const;

export type IconName = keyof typeof paths;

export function Icon({ name, className = "" }: { name: IconName; className?: string }) {
  return <svg className={`icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name]} /></svg>;
}

export function Button({ children, variant = "primary", loading, icon, className = "", disabled, ref, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "ghost"; loading?: boolean; icon?: IconName; ref?: Ref<HTMLButtonElement> }) {
  return <button {...props} ref={ref} type={props.type || "button"} disabled={disabled || loading} aria-busy={loading || undefined} className={`button button-${variant} ${className}`}>{loading ? <span className="loading-mark" aria-hidden="true" /> : icon ? <Icon name={icon} /> : null}{children}</button>;
}

export function Tag({ children, variant = "secondary" }: { children: ReactNode; variant?: "secondary" | "success" | "warning" | "error" }) {
  return <span className={`tag tag-${variant}`}>{children}</span>;
}

export function Brand() {
  return <span className="brand-lockup"><span className="brand-symbol" aria-hidden="true">V<span>·</span></span><span className="verdict-wordmark">Verdict</span><span className="product-name">Research review</span></span>;
}
