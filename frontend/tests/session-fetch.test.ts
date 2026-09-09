import { test } from "node:test";
import assert from "node:assert/strict";
import { ApiError, createAuthenticatedFetch } from "../src/lib/session-fetch";

const makeSession = (access_token = "old", id = "user-a") => ({ access_token, user: { id } });
function setup(refreshError: unknown = null) {
  let session: ReturnType<typeof makeSession> | null = makeSession();
  let signsOut = 0, refreshes = 0;
  const auth = {
    getSession: async () => ({ data: { session }, error: null }),
    refreshSession: async () => { refreshes++; if (refreshError) return { data: { session: null }, error: refreshError }; session = makeSession("fresh"); return { data: { session }, error: null }; },
    signOut: async () => { signsOut++; session = null; },
  };
  return { auth, switchAccount: () => { session = makeSession("user-b-token", "user-b"); }, counts: () => ({ signsOut, refreshes }) };
}

test("a normal request carries bearer auth and a bounded signal", async () => {
  const fixture = setup();
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async (_url, init) => {
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer old");
    assert.ok(init?.signal); return new Response("ok");
  });
  assert.equal((await fetcher("http://example.test")).status, 200);
  assert.deepEqual(fixture.counts(), { signsOut: 0, refreshes: 0 });
});

test("backend auth outage preserves session and does not refresh", async () => {
  const fixture = setup();
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async () => new Response("unavailable", { status: 503 }));
  assert.equal((await fetcher("http://example.test")).status, 503);
  assert.deepEqual(fixture.counts(), { signsOut: 0, refreshes: 0 });
});

test("temporary refresh failures surface503 without signing out", async () => {
  for (const error of [{ status: 503 }, { status: 429 }, { name: "AuthRetryableFetchError", status: 0 }, new TypeError("offline")]) {
    const fixture = setup(error);
    const fetcher = createAuthenticatedFetch(() => fixture.auth, async () => new Response(null, { status: 401 }));
    await assert.rejects(fetcher("http://example.test"), error => error instanceof ApiError && error.status === 503);
    assert.equal(fixture.counts().signsOut, 0);
  }
});

test("definitively invalid refresh token signs out", async () => {
  const fixture = setup({ status: 400, code: "refresh_token_not_found" });
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async () => new Response(null, { status: 401 }));
  assert.equal((await fetcher("http://example.test")).status, 401);
  assert.equal(fixture.counts().signsOut, 1);
});

test("401 refresh retries exactly once with the fresh token", async () => {
  const fixture = setup(); let calls = 0;
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async (_url, init) => {
    calls++; const token = new Headers(init?.headers).get("Authorization");
    return new Response(null, { status: token === "Bearer fresh" ? 200 : 401 });
  });
  assert.equal((await fetcher("http://example.test")).status, 200);
  assert.equal(calls, 2); assert.equal(fixture.counts().refreshes, 1);
});

test("concurrent401s share one refresh", async () => {
  const fixture = setup();
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async (_url, init) => new Response(null, { status: new Headers(init?.headers).get("Authorization") === "Bearer fresh" ? 200 : 401 }));
  await Promise.all([fetcher("http://example.test/a"), fetcher("http://example.test/b")]);
  assert.equal(fixture.counts().refreshes, 1);
});

test("old requests cannot retry as a new account or sign it out", async () => {
  const fixture = setup(); let calls = 0;
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async () => { calls++; fixture.switchAccount(); return new Response(null, { status: 401 }); });
  await assert.rejects(fetcher("http://example.test"), error => error instanceof ApiError && error.status === 401);
  assert.equal(calls, 1); assert.deepEqual(fixture.counts(), { signsOut: 0, refreshes: 0 });
});

test("SSE retains the caller's abort lifecycle", async () => {
  const controller = new AbortController(), fixture = setup();
  const fetcher = createAuthenticatedFetch(() => fixture.auth, async (_url, init) => { assert.equal(init?.signal, controller.signal); return new Response(null); });
  await fetcher("http://example.test", { signal: controller.signal });
});
