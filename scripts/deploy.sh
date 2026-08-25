#!/usr/bin/env bash
# One-shot deploy: dependencies -> tests -> launchd restart -> tunnel check.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_macos
cd "$APP_DIR"

step "1/4 Updating dependencies"
uv sync --extra dev
ok "dependencies in sync"

step "2/4 Running tests"
uv run pytest -q
ok "tests passed"

step "3/4 Restarting the launchd agent"
if ! launchctl print "$(agent_target)" >/dev/null 2>&1; then
    die "the agent is not loaded - run 'make install-agent' first"
fi
# kickstart -k kills the running instance and starts a fresh one.
launchctl kickstart -k "$(agent_target)"
if wait_for_local_health; then
    ok "local health check passed"
else
    die "the server did not come back up - check $LOG_DIR/agent.log"
fi

step "4/4 Checking the Cloudflare Tunnel"
if [ -z "${LINE_AI_PUBLIC_URL:-}" ]; then
    die "LINE_AI_PUBLIC_URL is not set.
Put it in deploy/deploy.env, e.g.
  LINE_AI_PUBLIC_URL=https://line-ai.example.com"
fi
BODY="$(curl -fsS --max-time 10 "${LINE_AI_PUBLIC_URL%/}/healthz")" \
    || die "could not reach ${LINE_AI_PUBLIC_URL%/}/healthz through the tunnel.
Is cloudflared running? Check with: cloudflared tunnel list"
case "$BODY" in
    *'"status":"ok"'*) ok "tunnel reached the app: $BODY" ;;
    *) die "unexpected response through the tunnel: $BODY" ;;
esac

step "Deployed"
printf 'commit:  %s\n' "$(git rev-parse --short HEAD)"
printf 'public:  %s/webhook\n' "${LINE_AI_PUBLIC_URL%/}"
printf 'logs:    %s/agent.log\n' "$LOG_DIR"
