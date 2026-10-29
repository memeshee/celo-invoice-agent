#!/usr/bin/env bash
# Publish the stable public site to Vercel.
#
# The live app runs on this machine behind a Cloudflare quick tunnel whose hostname
# changes whenever the tunnel restarts. A judge holding a link must not care, so:
#   /               -> static HTML, always up
#   /i/*, /proof... -> reverse-proxied to the current tunnel host
# Re-running this after a tunnel rotation repoints the proxy and redeploys.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

# The origin lives in .env, not in the shell environment, and it must not be the
# public host: proxying the site to itself returns 508 (infinite loop) and takes the
# whole demo down. Cheap guard, real outage avoided.
ORIGIN="${ORIGIN_BASE_URL:-$(sed -n 's/^ORIGIN_BASE_URL=//p' .env 2>/dev/null | tr -d '\r')}"
if [ -z "$ORIGIN" ]; then
  echo "no ORIGIN_BASE_URL in .env — the proxy needs the tunnel host, not the public one" >&2
  exit 1
fi
case "$ORIGIN" in
  *vercel.app*)
    echo "refusing: origin $ORIGIN is the public host itself (would 508)" >&2
    exit 1;;
esac

PY="$REPO/.venv/bin/python"
"$PY" scripts/build_site.py --origin "$ORIGIN"

# Vercel CLI: deploy from the repo root (the project's root directory), force so a
# cached build cannot serve a stale proxy target, and --yes so it never prompts (this
# runs from a systemd unit).
cd "$REPO"
PROJECT=impay
vercel link --project "$PROJECT" --yes >/dev/null 2>&1 || true
OUT=$(vercel deploy --prod --force --yes 2>&1)
echo "$OUT" | grep -E '"url"|readyState' | head -3
printf '%s\n' "$OUT" | grep -qE 'READY|ready' || { echo "deploy did not report ready" >&2; exit 1; }
# Publish the custom domain, and NEVER assume "$PROJECT.vercel.app" is ours:
# celo.uconfidant.tech belongs to another team and returns 200 for their site, so a
# status-only check would happily pass on a stranger's page.
URL="${PUBLIC_URL:-https://celo.uconfidant.tech}"
FINGERPRINT="${FINGERPRINT:-impay}"
echo "$URL" > "$REPO/.site_url"
echo "deployed: $URL  (proxy -> $ORIGIN)"

# *.vercel.app sits behind Vercel Authentication by default, which turns the demo
# into a login wall for anyone who is not the owner. Clear it, then prove it.
TOK=$("$PY" -c "import json;print(json.load(open('$HOME/.local/share/com.vercel.cli/auth.json'))['token'])")
TEAM=$(curl -s -H "Authorization: Bearer $TOK" https://api.vercel.com/v2/user \
  | "$PY" -c "import sys,json;print((json.load(sys.stdin).get('user') or {}).get('defaultTeamId') or '')")
SLUG=impay
PID=$(curl -s -H "Authorization: Bearer $TOK" \
  "https://api.vercel.com/v9/projects/$SLUG${TEAM:+?teamId=$TEAM}" \
  | "$PY" -c "import sys,json;print(json.load(sys.stdin).get('id') or '')")
if [ -n "$PID" ]; then
  curl -s -X PATCH -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
    "https://api.vercel.com/v9/projects/$PID${TEAM:+?teamId=$TEAM}" \
    -d '{"ssoProtection":null}' > /dev/null && echo "ssoProtection cleared on $SLUG"
else
  echo "warning: could not resolve project id for $SLUG (auth wall may remain)" >&2
fi

ok=0
for path in / /proof /agent.json; do
  body=$(curl -s --max-time 25 "$URL$path" || true)
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 25 "$URL$path" || echo 000)
  if [ "$code" = "200" ] && printf '%s' "$body" | grep -qi "$FINGERPRINT"; then
    printf '  %-12s %s (fingerprint ok)\n' "$path" "$code"; ok=$((ok+1))
  else
    printf '  %-12s %s (FINGERPRINT MISSING — not our page?)\n' "$path" "$code"
  fi
done
[ "$ok" -eq 3 ] || { echo "verification failed on $((3-ok))/3 paths at $URL" >&2; exit 1; }
