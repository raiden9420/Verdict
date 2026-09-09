# Verdict frontend

Next.js 16.3.4 / React 19.2.8 / TypeScript. Native controls and a small shared UI
layer support the research workspace. Read `AGENTS.md` before framework edits.

From this directory:

```bash
npm ci
npm run preview
```

The preview requires the repository's `backend/.venv` with backend dependencies.
It starts a synthetic, local-only API. Sign in at `http://127.0.0.1:3000` using
`preview@example.test` / `preview-only`; records reset on shutdown. `/example`
is the public, fictional worked review. No real provider credentials are needed.

For a real development backend, copy `.env.example` to `.env.local`, set all
three public API/Supabase values, then run `npm run dev`. `NEXT_PUBLIC_API_URL`
is mandatory for production builds. Never place a service-role key in public
variables. Follow the root README for isolated database setup and worker limits.

Checks: `npm test`, `npm run lint`, `npm run build`. Unit tests cover evidence
matching, citation explanations, transport payloads, session races, signed URL
renewal timing, topic boundaries, and workspace recovery. Browser QA uses the
synthetic preview; there is not yet an automated component/E2E runner.

Core files: `src/app/page.tsx` coordinates owned workspace state;
`src/hooks/useSSE.ts` reconciles durable data with live updates;
`src/lib/audit-stream.ts` validates incoming artifacts;
`src/lib/session-fetch.ts` owns bounded auth retry;
`src/components/AuditArena.tsx` presents findings and source evidence.
