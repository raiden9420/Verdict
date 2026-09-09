/** Start an entirely synthetic workspace. No real accounts, databases or models. */
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const python = path.join(root, 'backend', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
if (!existsSync(python)) {
  console.error('Create backend/.venv and install backend/requirements.txt first. See README.md.');
  process.exit(1);
}
const children = [];
let stopping = false;
const stop = code => { if (stopping) return; stopping = true; for (const child of children) child.kill('SIGTERM'); process.exitCode = code; };
function start(command, args, cwd, env = process.env) {
  const child = spawn(command, args, { cwd, env, stdio: 'inherit' });
  children.push(child);
  child.on('error', error => { console.error(error.message); stop(1); });
  child.on('exit', code => { if (!stopping) stop(code || 0); });
}
start(python, [path.join(root, 'scripts/preview_server.py')], root);
start(process.execPath, ['node_modules/next/dist/bin/next', 'dev', '--webpack', '--hostname', '127.0.0.1', '--port', '3000'], path.join(root, 'frontend'), {
  ...process.env,
  // Turbopack starts pooled Node workers by executable name. Codex Desktop's
  // bundled runtime is not necessarily on PATH even though process.execPath
  // points to it, so make that directory available to child workers.
  PATH: `${path.dirname(process.execPath)}${path.delimiter}${process.env.PATH || ''}`,
  NEXT_TELEMETRY_DISABLED: '1',
  NEXT_PUBLIC_API_URL: 'http://127.0.0.1:8765',
  NEXT_PUBLIC_SUPABASE_URL: 'http://127.0.0.1:8765',
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: 'sb_publishable_verdict_local_preview_only',
  NEXT_PUBLIC_LOCAL_PREVIEW: '1',
});
console.log('\nLOCAL TEST WORKSPACE: http://127.0.0.1:3000\nSign in: preview@example.test / preview-only\nAll data is synthetic and resets when stopped. No external services are called.\n');
process.on('SIGINT', () => stop(0));
process.on('SIGTERM', () => stop(0));
