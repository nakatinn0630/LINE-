#!/usr/bin/env bash
# Render the launchd plist for this machine and load it.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_macos

UV="$(command -v uv)" || die "uv is not on PATH"

step "Rendering $PLIST_PATH"
mkdir -p "$(dirname "$PLIST_PATH")" "$LOG_DIR"
sed \
    -e "s|__LABEL__|${LABEL}|g" \
    -e "s|__UV__|${UV}|g" \
    -e "s|__APP_DIR__|${APP_DIR}|g" \
    -e "s|__LOG_DIR__|${LOG_DIR}|g" \
    -e "s|__PORT__|${PORT}|g" \
    -e "s|__PATH__|$(dirname "$UV"):/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin|g" \
    "$APP_DIR/deploy/com.line-personal-ai.agent.plist.template" > "$PLIST_PATH"
ok "wrote $PLIST_PATH"

step "Loading the agent"
launchctl bootout "$(agent_target)" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "$(agent_target)"
ok "loaded ${LABEL}"

step "Waiting for the server"
if wait_for_local_health; then
    ok "http://127.0.0.1:${PORT}/healthz responded"
else
    die "the server did not come up - check $LOG_DIR/agent.log
(if the Keychain master key is missing the agent exits cleanly on purpose and
 will not retry; run 'make check' to see what is missing)"
fi
