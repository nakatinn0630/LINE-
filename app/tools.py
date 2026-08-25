"""Small operator commands: `uv run python -m app.tools <command>`."""

from __future__ import annotations

import sys

from app.crypto import generate_master_key

_USAGE = """usage: python -m app.tools <command>

commands:
  genkey     print a fresh base64 32-byte master key
  check      report which secrets resolve, without printing any of them
"""


def _check() -> int:
    from app.config import get_settings
    from app.keychain import SecretNotFoundError

    try:
        settings = get_settings()
    except (SecretNotFoundError, ValueError) as exc:
        print(f"NOT READY: {exc}", file=sys.stderr)
        return 1
    print(f"OK: all secrets resolved from Keychain service {settings.keychain_service!r}")
    print(f"    data directory: {settings.data_dir}")
    print(f"    default model:  {settings.model}")
    return 0


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(_USAGE, file=sys.stderr)
        return 2
    command = argv[0]
    if command == "genkey":
        print(generate_master_key())
        return 0
    if command == "check":
        return _check()
    print(_USAGE, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
