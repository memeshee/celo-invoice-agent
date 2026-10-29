#!/usr/bin/env bash
# Keep a public HTTPS URL pointed at bot/serve.py, and keep the world in sync.
#
# A Cloudflare quick tunnel is free, needs no account and — unlike ngrok's free
# tier — serves our page to a browser instead of its own interstitial. The
# catch is that the hostname is reborn with the process, so every restart would
# silently strand PUBLIC_BASE_URL and the agent card. This wraps the tunnel and
# re-syncs both whenever the hostname changes.
#
# Deliberate: only the quick tunnel this script owns is touched. The host also
# runs a token-managed cloudflared for another project, and killing that would
# take a different service down.
set -uo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PUBLIC_PORT:-8088}"
LOG="$REPO/tunnel.log"
ENVFILE="$REPO/.env"
PY="$REPO/.venv/bin/python"

log() { echo "$(date -u +%FT%TZ) $*"; }

while true; do
  : > "$LOG"
  cloudflared tunnel --url "http://localhost:$PORT" --no-autoupdate \
      --logfile "$LOG" --loglevel info &
  CF_PID=$!

  URL=""
  for _ in $(seq 1 45); do
    URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" 2>/dev/null | head -1)
    [ -n "$URL" ] && break
    sleep 1
  done

  if [ -z "$URL" ]; then
    log "tunnel produced no hostname in 45s; restarting it"
    kill "$CF_PID" 2>/dev/null
    wait "$CF_PID" 2>/dev/null
    sleep 5
    continue
  fi

  CURRENT=$(sed -n 's/^ORIGIN_BASE_URL=//p' "$ENVFILE" | tr -d '\r')
  if [ "$URL" != "$CURRENT" ]; then
    # The tunnel is only the origin now: the URL judges hold is the stable Vercel
    # host, so a rotation republishes the proxy instead of invalidating links.
    sed -i "s|^ORIGIN_BASE_URL=.*|ORIGIN_BASE_URL=$URL|" "$ENVFILE"
    log "origin -> $URL (synced into .env)"
    systemctl --user restart celo-invoice-serve.service celo-invoice-bot.service || true
    sleep 5
    bash "$REPO/scripts/publish_site.sh" >> "$REPO/tunnel.log" 2>&1 \
      && log "site republished, proxy follows the new origin" \
      || log "site republish failed (see log)"
    "$PY" "$REPO/scripts/set_agent_services.py" >> "$REPO/tunnel.log" 2>&1 \
      && log "agent card confirmed at the stable endpoint" \
      || log "agent card update skipped/failed (see log)"
  else
    log "tunnel up at $URL (origin unchanged)"
  fi

  wait "$CF_PID"
  log "tunnel exited; restarting in 5s"
  sleep 5
done
