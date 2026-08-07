/**
 * Session management — anonymous UUID stored in localStorage.
 * No accounts in Phase 1; this scopes audits to the current browser.
 */

const SESSION_KEY = "verdict_session_id";

/**
 * Get or create the session UUID.
 * Safe to call during SSR — returns a placeholder that will be replaced
 * on the client side.
 */
export function getSessionId(): string {
  if (typeof window === "undefined") {
    return "ssr-placeholder";
  }

  let id = localStorage.getItem(SESSION_KEY);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(SESSION_KEY, id);
  }
  return id;
}
