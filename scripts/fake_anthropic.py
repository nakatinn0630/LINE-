#!/usr/bin/env python3
"""A stand-in Anthropic API for rehearsing the 429 and 529 paths.

Point the app at it and send yourself a LINE message:

    LINE_AI_NOTIFY_WAIT_THRESHOLD=20 \
    ANTHROPIC_BASE_URL=http://127.0.0.1:8931 \
    uv run python -m app.serve

    # in another terminal
    uv run python scripts/fake_anthropic.py --rate-limit 1 --retry-after 25

The first request gets 429 with `retry-after: 25`; because 25 exceeds the
20 second notify threshold you should receive a "25秒後に返答します。" push,
and the real answer roughly 25 seconds later.

Uses only the standard library, so it needs no extra dependencies.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s fake-anthropic: %(message)s")
logger = logging.getLogger(__name__)

STATE = {"rate_limit": 0, "overload": 0, "retry_after": "25", "requests": 0}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        STATE["requests"] += 1
        index = STATE["requests"]

        if STATE["rate_limit"] > 0:
            STATE["rate_limit"] -= 1
            logger.info(
                "request %d -> 429 (retry-after: %s)", index, STATE["retry_after"]
            )
            self._send(
                429,
                {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
                extra_headers={"retry-after": STATE["retry_after"]},
            )
            return

        if STATE["overload"] > 0:
            STATE["overload"] -= 1
            logger.info("request %d -> 529 (with a retry-after that must be ignored)", index)
            self._send(
                529,
                {"type": "error", "error": {"type": "overloaded_error", "message": "overloaded"}},
                # Deliberately present: the 529 path must not honour it.
                extra_headers={"retry-after": "999"},
            )
            return

        logger.info("request %d -> 200", index)
        self._send(
            200,
            {
                "id": f"msg_fake_{index}",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [
                    {"type": "text", "text": f"スタブからの応答です（{index}回目のリクエスト）。"}
                ],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 10},
            },
        )

    def _send(self, status: int, payload: dict, extra_headers: dict | None = None) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.send_header("request-id", f"req_fake_{STATE['requests']}")
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        """Silence the default per-request logging; we log our own lines."""


def serve(host: str, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8931)
    parser.add_argument("--rate-limit", type=int, default=0,
                        help="how many requests to answer with 429 before succeeding")
    parser.add_argument("--retry-after", default="25",
                        help="value of the retry-after header on those 429s")
    parser.add_argument("--overload", type=int, default=0,
                        help="how many requests to answer with 529 before succeeding")
    args = parser.parse_args()

    STATE["rate_limit"] = args.rate_limit
    STATE["overload"] = args.overload
    STATE["retry_after"] = args.retry_after

    server = serve(args.host, args.port)
    logger.info(
        "listening on http://%s:%d (429 x%d, 529 x%d, retry-after %s)",
        args.host, args.port, args.rate_limit, args.overload, args.retry_after,
    )
    logger.info("point the app at it with ANTHROPIC_BASE_URL=http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("stopping after %d requests", STATE["requests"])
        server.shutdown()


if __name__ == "__main__":
    main()
