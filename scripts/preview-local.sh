#!/bin/sh
# Convenience launcher for a shell without Node on PATH (including Codex Desktop).
set -eu
verdict_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if command -v node >/dev/null 2>&1; then
  verdict_node=$(command -v node)
else
  verdict_node="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
  if [ ! -x "$verdict_node" ]; then
    echo 'Install Node.js 20.x and run npm run preview in frontend/.' >&2
    exit 1
  fi
fi
exec "$verdict_node" "$verdict_root/scripts/preview-local.mjs"
