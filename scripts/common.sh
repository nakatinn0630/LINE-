#!/usr/bin/env bash
# Shared settings for the deploy scripts.

LABEL="com.line-personal-ai.agent"
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_PATH="$HOME/Library/LaunchAgents/${LABEL}.plist"
LOG_DIR="$HOME/Library/Logs/line-personal-ai"
PORT="${LINE_AI_PORT:-8000}"

# Optional local overrides (LINE_AI_PUBLIC_URL, LINE_AI_PORT, ...). Gitignored:
# it names your host, not your secrets - secrets stay in the Keychain.
# shellcheck disable=SC1091
[ -f "$APP_DIR/deploy/deploy.env" ] && . "$APP_DIR/deploy/deploy.env"

die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '\033[32m  ok\033[0m %s\n' "$*"; }

require_macos() {
    [ "$(uname -s)" = "Darwin" ] || die "this script manages a launchd agent; it only runs on macOS"
}

agent_target() { echo "gui/$(id -u)/${LABEL}"; }

wait_for_local_health() {
    local tries=${1:-30}
    for _ in $(seq "$tries"); do
        if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}
