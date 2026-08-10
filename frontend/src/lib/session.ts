/**
 * Session management — anonymous UUID stored in localStorage.
 * No accounts in Phase 1; this scopes audits to the current browser.
 */

const SESSION_KEY = "verdict_session_id";
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
let volatileSessionId: string | null = null;

/**
 * Get or create the session UUID.
 * Safe to call during SSR — returns a placeholder that will be replaced
 * on the client side.
 */
export function getSessionId(): string {
  if (typeof window === "undefined") {
    return "ssr-placeholder";
  }

  if (volatileSessionId) return volatileSessionId;

  try {
    const stored = localStorage.getItem(SESSION_KEY);
    if (stored && UUID_PATTERN.test(stored)) {
      volatileSessionId = stored;
      return stored;
    }
    if (stored) localStorage.removeItem(SESSION_KEY);
  } catch {
    // Some privacy modes disable storage. A stable in-memory ID still keeps
    // every request in the current page session consistently scoped.
  }

  volatileSessionId = crypto.randomUUID();
  try {
    localStorage.setItem(SESSION_KEY, volatileSessionId);
  } catch {
    // The in-memory ID remains valid for this page lifetime.
  }
  return volatileSessionId;
}
