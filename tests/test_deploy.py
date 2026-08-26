"""`make deploy` must not reach the launchd restart when an earlier step fails.

The script is driven with stub executables on PATH that record every call, so
the ordering guarantee is checked without a Mac, without launchd and without
touching the real agent. What matters is not that the stubs "work" but that
the trace stops at the right place.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DEPLOY = REPO / "scripts" / "deploy.sh"
ROLLBACK = REPO / "scripts" / "rollback.sh"

# `uname -s` must say Darwin or require_macos refuses to run at all.
UNAME_STUB = """#!/usr/bin/env bash
echo "uname $*" >> "$TRACE"
if [ "${1:-}" = "-s" ]; then echo Darwin; else echo Darwin; fi
"""

CURL_STUB = """#!/usr/bin/env bash
echo "curl $*" >> "$TRACE"
for arg in "$@"; do
  case "$arg" in
    http*) echo '{"status":"ok"}' ;;
  esac
done
"""

LAUNCHCTL_STUB = """#!/usr/bin/env bash
echo "launchctl $*" >> "$TRACE"
exit ${LAUNCHCTL_EXIT:-0}
"""

UV_STUB = """#!/usr/bin/env bash
echo "uv $*" >> "$TRACE"
case "$1 ${2:-}" in
  "run pytest") exit ${PYTEST_EXIT:-0} ;;
  "sync"*)      exit ${UV_SYNC_EXIT:-0} ;;
esac
exit 0
"""


def _write_stub(directory: Path, name: str, body: str) -> None:
    path = directory / name
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def harness(tmp_path):
    """A PATH of recording stubs plus a helper that runs a script under them."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_stub(bindir, "uname", UNAME_STUB)
    _write_stub(bindir, "curl", CURL_STUB)
    _write_stub(bindir, "launchctl", LAUNCHCTL_STUB)
    _write_stub(bindir, "uv", UV_STUB)

    trace = tmp_path / "trace.txt"
    trace.write_text("")

    def run(script: Path, *args: str, **overrides: str):
        env = dict(os.environ)
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env["TRACE"] = str(trace)
        env.setdefault("LINE_AI_PUBLIC_URL", "https://line-ai.example.test")
        env.update(overrides)
        result = subprocess.run(
            ["bash", str(script), *args],
            capture_output=True, text=True, env=env, cwd=str(REPO),
        )
        return result, trace.read_text().splitlines()

    return run


def _first_index(trace: list[str], prefix: str) -> int | None:
    for index, line in enumerate(trace):
        if line.startswith(prefix):
            return index
    return None


# -- the ordering guarantee ---------------------------------------------------


def test_failing_tests_stop_the_deploy_before_any_launchd_call(harness):
    result, trace = harness(DEPLOY, PYTEST_EXIT="1")

    assert result.returncode != 0, "a failing test run must fail the deploy"
    assert _first_index(trace, "uv run pytest") is not None, "tests should have run"
    assert _first_index(trace, "launchctl") is None, (
        f"launchd was touched despite failing tests: {trace}"
    )


def test_a_syntax_error_makes_the_test_step_fail(tmp_path):
    """The premise of the test above: broken Python fails step 2, not step 3."""
    staged = tmp_path / "app"
    shutil.copytree(REPO / "app", staged, ignore=shutil.ignore_patterns("__pycache__"))
    (staged / "config.py").write_text("def broken(:\n    pass\n")

    result = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(staged)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_a_failed_dependency_sync_stops_before_the_tests(harness):
    result, trace = harness(DEPLOY, UV_SYNC_EXIT="1")

    assert result.returncode != 0
    assert _first_index(trace, "uv run pytest") is None, (
        f"tests ran despite a failed sync: {trace}"
    )
    assert _first_index(trace, "launchctl") is None


def test_an_unloaded_agent_is_not_kickstarted(harness):
    # `launchctl print` failing means the agent was never installed.
    result, trace = harness(DEPLOY, LAUNCHCTL_EXIT="1")

    assert result.returncode != 0
    assert "install-agent" in result.stderr
    kickstarts = [line for line in trace if line.startswith("launchctl kickstart")]
    assert kickstarts == [], f"kickstarted an agent that is not loaded: {trace}"


def test_a_clean_run_executes_the_four_steps_in_order(harness):
    result, trace = harness(DEPLOY)

    assert result.returncode == 0, result.stderr

    sync = _first_index(trace, "uv sync")
    tests = _first_index(trace, "uv run pytest")
    check = _first_index(trace, "launchctl print")
    restart = _first_index(trace, "launchctl kickstart")
    health = _first_index(trace, "curl")

    assert None not in (sync, tests, check, restart, health), trace
    assert sync < tests < check < restart < health, trace


def test_a_missing_public_url_is_reported_not_guessed(harness):
    result, _trace = harness(DEPLOY, LINE_AI_PUBLIC_URL="")

    assert result.returncode != 0
    assert "LINE_AI_PUBLIC_URL" in result.stderr
    # The restart is step 3 and the tunnel check is step 4, so the restart did
    # happen - but nothing silently "passed" the tunnel check.
    assert "deploy/deploy.env" in result.stderr


def test_a_tunnel_that_answers_with_something_else_fails_the_deploy(harness, tmp_path):
    wrong = "#!/usr/bin/env bash\necho \"curl $*\" >> \"$TRACE\"\necho 'Bad Gateway'\n"
    _write_stub(tmp_path / "bin", "curl", wrong)
    result, _ = harness(DEPLOY)

    assert result.returncode != 0
    assert "unexpected response" in result.stderr


# -- rollback -----------------------------------------------------------------


def test_rollback_without_a_ref_lists_commits_and_stops(harness):
    result, trace = harness(ROLLBACK)

    assert result.returncode != 0
    assert "usage:" in result.stderr
    assert _first_index(trace, "launchctl") is None


def test_rollback_refuses_an_unknown_ref(harness):
    result, trace = harness(ROLLBACK, "no-such-ref-exists")

    assert result.returncode != 0
    assert "unknown git ref" in result.stderr
    assert _first_index(trace, "launchctl") is None


# -- the scripts themselves ---------------------------------------------------


@pytest.mark.parametrize(
    "script",
    [*sorted(p.name for p in (REPO / "scripts").glob("*.sh")), "hooks/block_data_access.sh"],
)
def test_every_shell_script_parses(script):
    result = subprocess.run(
        ["bash", "-n", str(REPO / "scripts" / script)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
