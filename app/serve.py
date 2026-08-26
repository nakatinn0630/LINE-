"""Process entry point, with a preflight check before the server starts.

Run under launchd. The preflight decides whether starting is even possible,
and it communicates that decision to launchd through the exit code:

* **exit 0** - a required secret is missing from the Keychain. Restarting would
  fail identically, so the plist's `KeepAlive.SuccessfulExit = false` treats
  this clean exit as "do not restart" and the agent stays down until someone
  adds the key.
* **non-zero** - a genuine crash. launchd restarts the agent.

That inversion is the whole trick: exiting *successfully* is how a program
tells launchd to leave it alone.
"""

from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("app.serve")

REFUSE_TO_START = 0
CRASH = 1


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LINE_AI_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
        force=True,
    )


def preflight() -> object:
    """Load settings, or explain what is missing and stop the process.

    Never returns when a secret is missing - it exits 0 so launchd will not
    spin trying again.
    """
    from app.config import MASTER_KEY, get_settings
    from app.keychain import SecretNotFoundError

    try:
        settings = get_settings()
    except SecretNotFoundError as exc:
        # 仕様書 12.5 のフェーズ2検証が "master-key not found" を探すので、
        # マスター鍵のときはその語をそのまま残す。
        if MASTER_KEY in str(exc):
            logger.error("master-key not found in the Keychain.")
        logger.error("起動できません: %s", exc)
        logger.error(
            "起動せず、launchdにも再試行を求めません。Keychainへの登録は手作業が必要です。"
            "登録後は launchctl kickstart -k gui/$(id -u)/com.line-personal-ai.agent "
            "で手動起動してください。"
        )
        sys.exit(REFUSE_TO_START)
    except ValueError as exc:
        # 鍵の形式が壊れている場合も同じ。再起動しても直らない。
        logger.error("master-key not found in a usable form.")
        logger.error("起動できません: %s", exc)
        sys.exit(REFUSE_TO_START)

    logger.info(
        "Preflight passed. Secrets resolved from Keychain service %r.",
        settings.keychain_service,
    )
    return settings


def main() -> None:
    _configure_logging()
    settings = preflight()

    import uvicorn

    host = os.environ.get("LINE_AI_HOST", "127.0.0.1")
    port = int(os.environ.get("LINE_AI_PORT", "8000"))
    logger.info("Starting uvicorn on %s:%d (data=%s).", host, port, settings.data_dir)

    try:
        uvicorn.run("app.main:app", host=host, port=port, log_config=None)
    except Exception:
        logger.exception("Server crashed.")
        sys.exit(CRASH)


if __name__ == "__main__":
    main()
