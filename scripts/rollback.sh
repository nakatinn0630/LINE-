#!/usr/bin/env bash
# Roll back to a git ref and redeploy it.
set -euo pipefail
. "$(dirname "${BASH_SOURCE[0]}")/common.sh"
require_macos
cd "$APP_DIR"

REF="${1:-}"
[ -n "$REF" ] || die "usage: scripts/rollback.sh <git-ref>
Recent commits:
$(git log --oneline -10)"

git rev-parse --verify "${REF}^{commit}" >/dev/null 2>&1 || die "unknown git ref: $REF"

step "Rolling back to $REF"
git -c advice.detachedHead=false checkout --detach "$REF"
ok "checked out $(git rev-parse --short HEAD)"

exec "$APP_DIR/scripts/deploy.sh"
