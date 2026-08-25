"""macOS Keychain-backed secret loading.

Secrets are never stored in the repository, in a `.env` file, or in any other
plaintext file on disk. They live in the login Keychain and are read at process
start via `security find-generic-password`.

An environment variable of the same name takes precedence. That escape hatch
exists only for CI and for running on non-macOS hosts; on a developer machine
leave it unset so the Keychain is the single source of truth.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_SERVICE = "line-personal-ai"


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret is in neither the environment nor the Keychain."""


def _read_keychain(service: str, account: str) -> str | None:
    """Return the password for (service, account), or None if it is not there."""
    if platform.system() != "Darwin":
        return None
    security = shutil.which("security")
    if security is None:
        return None

    result = subprocess.run(
        [security, "find-generic-password", "-s", service, "-a", account, "-w"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        # Exit code 44 is "item not found"; anything else is worth logging, but
        # never log stdout - that would be the secret itself.
        if result.returncode != 44:
            logger.warning(
                "security find-generic-password failed for %s/%s (exit %d): %s",
                service,
                account,
                result.returncode,
                result.stderr.strip(),
            )
        return None
    return result.stdout.rstrip("\n")


def get_secret(account: str, *, service: str = DEFAULT_SERVICE) -> str:
    """Load a secret, preferring the environment and falling back to the Keychain."""
    from_env = os.environ.get(account)
    if from_env:
        logger.info("Loaded %s from the environment.", account)
        return from_env

    from_keychain = _read_keychain(service, account)
    if from_keychain:
        logger.info("Loaded %s from the macOS Keychain (service=%s).", account, service)
        return from_keychain

    raise SecretNotFoundError(
        f"Secret {account!r} not found. Add it to the login Keychain with:\n"
        f"  security add-generic-password -s {service} -a {account} -w\n"
        f"(the -w flag with no value prompts for the secret, keeping it out of shell history)"
    )
