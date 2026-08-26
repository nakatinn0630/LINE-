#!/usr/bin/env bash
# PreToolUse(Bash) hook: refuse shell commands that touch data/.
#
# data/ holds the encrypted personal archive. Permission deny rules cover the
# Read/Edit/Write tools, but Bash rules are prefix matches and a shell command
# can reach the directory in ways a prefix cannot describe (`cd data && cat *`,
# `tar cf - data/`, a glob, a subshell). This hook reads the actual command
# string and refuses anything mentioning data/ outside a two-command allowlist.
#
# Exit codes are the contract with Claude Code:
#   0 - allow the command
#   2 - blocking error; the command does not run and stderr goes back to Claude
set -uo pipefail

# 仕様書 12.10 が定める許可リスト。ファイル名は暗号化されているので一覧は安全。
ALLOWED=("ls data/" "ls -la data/" "du -sh data/")

payload="$(cat)"

if command -v jq >/dev/null 2>&1; then
    command_text="$(printf '%s' "$payload" | jq -r '.tool_input.command // empty')"
else
    command_text="$(printf '%s' "$payload" | python3 -c \
        'import json,sys; print(json.load(sys.stdin).get("tool_input",{}).get("command",""))')"
fi

# Nothing to inspect (malformed payload, or a non-Bash tool slipping through).
[ -n "$command_text" ] || exit 0

# Match the directory as a path segment rather than as a bare substring. This
# catches the forms a prefix rule cannot express - `cd data && cat *`,
# `tar cf - data`, `./data/x` - while leaving `metadata/`, `bigdata/` and
# `data_export/` alone.
if [[ ! "$command_text" =~ (^|[^[:alnum:]_-])(\./)?data($|/|[^[:alnum:]_-]) ]]; then
    exit 0
fi

# Collapse whitespace so " ls   data/ " still matches the allowlist.
normalized="$(printf '%s' "$command_text" | tr -s '[:space:]' ' ' | sed -e 's/^ //' -e 's/ $//')"

for allowed in "${ALLOWED[@]}"; do
    if [ "$normalized" = "$allowed" ]; then
        exit 0
    fi
done

{
    echo "Blocked: this command touches data/, which holds the encrypted personal archive."
    echo "  command: ${command_text}"
    echo
    echo "Allowed commands against the archive:"
    for allowed in "${ALLOWED[@]}"; do
        echo "  ${allowed}"
    done
    echo
    echo "To inspect a record, decrypt it deliberately with the app's own code."
} >&2
exit 2
