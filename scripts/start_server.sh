#!/usr/bin/env bash
set -euo pipefail

# Start the web app and the Cloudflare tunnel together.
# Usage: scripts/start_server.sh

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
TUNNEL_NAME="${TUNNEL_NAME:-decomp-web}"

if [[ -z "${WOLFRAMSCRIPT:-}" ]]; then
  echo "WARNING: WOLFRAMSCRIPT is not set. If auto-detect fails, set it, e.g.:" >&2
  echo "  export WOLFRAMSCRIPT=/Users/ayushkhaitan/Desktop/Wolfram.app/Contents/MacOS/wolframscript" >&2
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared not found. Install via: brew install cloudflared" >&2
  exit 1
fi

cleanup() {
  echo "\nStopping processes…"
  [[ -n "${APP_PID:-}" ]] && kill "$APP_PID" 2>/dev/null || true
  [[ -n "${CF_PID:-}"  ]] && kill "$CF_PID"  2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "==> Starting app on http://$HOST:$PORT …"
HOST="$HOST" PORT="$PORT" decomp-web &
APP_PID=$!

sleep 1
echo "==> Starting Cloudflare tunnel ($TUNNEL_NAME)…"
cloudflared tunnel run "$TUNNEL_NAME" &
CF_PID=$!

echo "\nApp PID: $APP_PID  |  Tunnel PID: $CF_PID"
echo "Press Ctrl+C to stop."
wait

