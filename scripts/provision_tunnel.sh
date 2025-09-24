#!/usr/bin/env bash
set -euo pipefail

# Provision a stable Cloudflare Named Tunnel for your local app
# Usage:
#   scripts/provision_tunnel.sh [DOMAIN] [HOSTNAME]
# Defaults:
#   DOMAIN=o-forge.com
#   HOSTNAME=
#     - if provided, use it (e.g., decomp.o-forge.com)
#     - if omitted, use the apex DOMAIN (e.g., o-forge.com)

DOMAIN="${1:-o-forge.com}"
HOSTNAME="${2:-$DOMAIN}"
TUNNEL_NAME="decomp-web"

echo "==> Domain:    $DOMAIN"
echo "==> Hostname:  $HOSTNAME"
echo "==> Tunnel:    $TUNNEL_NAME"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "ERROR: cloudflared not found. Install via: brew install cloudflared" >&2
  exit 1
fi

if [[ ! -f "$HOME/.cloudflared/cert.pem" ]]; then
  echo "ERROR: Cloudflare cert not found at ~/.cloudflared/cert.pem" >&2
  echo "Run: cloudflared login   (select $DOMAIN in the browser)" >&2
  exit 1
fi

echo "==> Ensuring tunnel exists…"
if ! cloudflared tunnel list 2>/dev/null | awk 'NR>1 {print $2}' | grep -qx "$TUNNEL_NAME"; then
  cloudflared tunnel create "$TUNNEL_NAME"
fi

echo "==> Resolving tunnel UUID…"
UUID=$(cloudflared tunnel list 2>/dev/null | awk -v name="$TUNNEL_NAME" '$2==name {print $1}' | tail -n1)
if [[ -z "${UUID:-}" ]]; then
  echo "ERROR: Could not determine tunnel UUID for $TUNNEL_NAME" >&2
  cloudflared tunnel list || true
  exit 1
fi

CRED_FILE="$HOME/.cloudflared/${UUID}.json"
if [[ ! -f "$CRED_FILE" ]]; then
  echo "ERROR: Credentials file not found: $CRED_FILE" >&2
  ls -l "$HOME/.cloudflared" || true
  exit 1
fi

mkdir -p "$HOME/.cloudflared"
CFG="$HOME/.cloudflared/config.yml"
echo "==> Writing $CFG"
cat > "$CFG" <<YAML
tunnel: $TUNNEL_NAME
credentials-file: $CRED_FILE
ingress:
  - hostname: $HOSTNAME
    service: http://localhost:8000
  - service: http_status:404
YAML

echo "==> Routing DNS $HOSTNAME → tunnel $TUNNEL_NAME"
cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" || true

echo "\nAll set. Next steps:\n"
echo "1) Start your app:"
echo "   HOST=0.0.0.0 PORT=8000 decomp-web"
echo "2) In another terminal, run the tunnel:"
echo "   cloudflared tunnel run $TUNNEL_NAME"
echo "\nShare: https://$HOSTNAME"

