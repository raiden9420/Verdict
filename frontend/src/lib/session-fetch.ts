/** Session-safe, bounded HTTP transport. No mutation is retried except after a 401. */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); this.name = "ApiError"; }
}

type Session = { access_token: string; user: { id: string } };
type AuthResult = { data: { session: Session | null }; error: unknown };
type AuthClient = {
  getSession(): Promise<AuthResult>;
  refreshSession(): Promise<AuthResult>;
  signOut(options: { scope: "local" }): Promise<unknown>;
};

const INVALID_SESSION_CODES = new Set(["refresh_token_not_found", "refresh_token_already_used", "session_not_found", "session_expired", "invalid_grant", "bad_jwt"]);

export function isDefinitiveSessionError(error: unknown): boolean {
  if (!error || typeof error !== "object") return false;
  const { code, status } = error as { code?: string; status?: number };
  return status === 401 || status === 403 || Boolean(code && INVALID_SESSION_CODES.has(code));
}

function sessionError(error: unknown): ApiError {
  return isDefinitiveSessionError(error)
    ? new ApiError("Your sign-in session expired. Sign in again to continue.", 401)
    : new ApiError("Sign-in verification is temporarily unavailable. Your session has been kept; please try again shortly.", 503);
}

export function createAuthenticatedFetch(getAuth: () => AuthClient, request: typeof fetch = fetch) {
  let refreshing: { userId: string; promise: Promise<Session | null> } | null = null;
  const currentSession = async () => {
    let result: AuthResult;
    try { result = await getAuth().getSession(); }
    catch (error) { throw sessionError(error); }
    if (result.error) throw sessionError(result.error);
    return result.data.session;
  };
  const refresh = (userId: string) => {
    if (refreshing?.userId === userId) return refreshing.promise;
    const promise = getAuth().refreshSession().then(({ data, error }) => {
      if (error) {
        if (isDefinitiveSessionError(error)) return null;
        throw sessionError(error);
      }
      return data.session;
    }).catch(error => { throw error instanceof ApiError ? error : sessionError(error); });
    refreshing = { userId, promise };
    void promise.finally(() => { if (refreshing?.promise === promise) refreshing = null; }).catch(() => undefined);
    return promise;
  };
  const send = async (url: string, init: RequestInit, token: string) => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${token}`);
    // SSE owns its heartbeat/abort lifecycle. Normal requests also bound a
    // stalled response body; uploads allow time for classification + indexing.
    const signal = init.signal || AbortSignal.timeout(init.body instanceof FormData ? 240_000 : 30_000);
    try { return await request(url, { ...init, headers, signal }); }
    catch (error) {
      if (error instanceof Error && error.name === "TimeoutError") throw new ApiError("The request timed out. Your paper or review may already be saved; check the library before trying again.", 504);
      throw error;
    }
  };
  return async (url: string, init: RequestInit = {}): Promise<Response> => {
    const session = await currentSession();
    if (!session) throw new ApiError("Sign in to continue.", 401);
    let response = await send(url, init, session.access_token);
    if (response.status !== 401) return response;
    const beforeRefresh = await currentSession();
    if (beforeRefresh?.user.id !== session.user.id) throw new ApiError("The signed-in account changed. Reopen this review from your library.", 401);
    const renewed = beforeRefresh.access_token !== session.access_token ? beforeRefresh : await refresh(session.user.id);
    if (renewed && renewed.user.id === session.user.id) {
      response = await send(url, init, renewed.access_token);
      if (response.status !== 401) return response;
    }
    // Never expire a new account/session because an old request finished late.
    const latest = await currentSession();
    if (latest?.user.id === session.user.id && latest.access_token === (renewed?.access_token || session.access_token)) {
      try { await getAuth().signOut({ scope: "local" }); } catch { /* fail closed below */ }
    }
    return response;
  };
}
