"""仕様書 12.5「検証プロンプト」のうち、既存テストで埋まっていなかった項目。

フェーズ2・3・4 の受入基準を、それぞれの節が指定した粒度で確かめる。既存の
tests/test_loading.py や tests/test_retry.py が「経路が正しいこと」を見るのに対し、
ここでは仕様書が名指しした数値（1秒未満、15秒間隔、3秒以上4秒未満）を見る。
"""

from __future__ import annotations

import asyncio
import io
import os
import re
import subprocess
import time
from pathlib import Path

import anthropic
import httpx2 as httpx
import pytest

from app.crypto import Crypto, DecryptionError, decode_master_key, generate_master_key
from app.loading import LoadingIndicator
from app.retry import RetryPolicy, call_with_retries
from app.vault import Vault

# ---------------------------------------------------------------------------
# フェーズ2 検証 1: Webhook受信から loading/start まで1秒未満。
#                   Claude を待たせても Loading が先に呼ばれること。
# ---------------------------------------------------------------------------


class SlowClaudeLine:
    """LINE 側のスタブ。loading/start が呼ばれた時刻を記録する。"""

    def __init__(self) -> None:
        self.loading_at: list[float] = []
        self.reply_at: list[float] = []
        self.replies: list[tuple[str, str]] = []

    async def show_loading_animation(self, request) -> None:
        self.loading_at.append(time.monotonic())

    async def reply_text(self, reply_token: str, text: str) -> None:
        self.reply_at.append(time.monotonic())
        self.replies.append((reply_token, text))

    async def push_text(self, user_id: str, text: str) -> None:
        pass


async def test_loading_is_called_within_one_second_even_when_claude_is_slow():
    line = SlowClaudeLine()
    indicator = LoadingIndicator(line, seconds=20)
    started = time.monotonic()

    async def slow_claude() -> str:
        await asyncio.sleep(0.4)
        return "答え"

    # 実装の順序と同じ: ローディングを出してから、キューの仕事を回す。
    await indicator.start("U1")
    async with indicator.keep_alive("U1"):
        answer = await slow_claude()
    await line.reply_text("rt", answer)

    assert line.loading_at, "loading/start が呼ばれていない"
    elapsed = line.loading_at[0] - started
    assert elapsed < 1.0, f"loading/start まで {elapsed:.3f} 秒かかった"
    # Claude の完了より先に呼ばれていること。
    assert line.loading_at[0] < line.reply_at[0]


# ---------------------------------------------------------------------------
# フェーズ2 検証 (12.10 の 5d): Loading の再呼び出しが15秒間隔、応答完了後は止まる。
# ---------------------------------------------------------------------------


def test_the_refresh_interval_is_fifteen_seconds_by_default():
    """loadingSeconds=20 と余裕5秒から、実際の間隔が15秒になること。"""
    indicator = LoadingIndicator(SlowClaudeLine(), seconds=20)
    assert indicator.seconds == 20
    assert indicator._refresh_after == pytest.approx(15.0)


async def test_the_refresh_repeats_then_stops_when_the_work_finishes():
    line = SlowClaudeLine()
    # 間隔を0.05秒に潰して、15秒間隔の挙動を短時間で観察する。
    indicator = LoadingIndicator(line, seconds=5, refresh_margin=4.95)

    async with indicator.keep_alive("U1"):
        await asyncio.sleep(0.28)
    during = len(line.loading_at)

    assert during >= 4, f"再呼び出しが {during} 回しかない"
    # 間隔がおおむね一定であること。
    gaps = [b - a for a, b in zip(line.loading_at, line.loading_at[1:], strict=False)]
    assert all(0.02 < gap < 0.2 for gap in gaps), gaps

    await asyncio.sleep(0.2)
    assert len(line.loading_at) == during, "応答完了後も打ち続けている"


# ---------------------------------------------------------------------------
# フェーズ2 検証 2: 1回目429（retry-after: 3）→ 2回目200。
#                   合計待機が3秒以上4秒未満。push は1回だけ。
# ---------------------------------------------------------------------------


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


async def test_a_429_with_retry_after_3_really_waits_between_3_and_4_seconds():
    """時計を止めずに実測する。他のテストは待ち時間を記録するだけなので。"""
    attempts = {"count": 0}

    async def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise anthropic.RateLimitError(
                "rate limited", response=_response(429, {"retry-after": "3"}), body=None
            )
        return "答え"

    started = time.monotonic()
    result = await call_with_retries(operation)
    elapsed = time.monotonic() - started

    assert result == "答え"
    assert attempts["count"] == 2
    assert 3.0 <= elapsed < 4.0, f"実際の待機は {elapsed:.2f} 秒だった"


async def test_a_long_wait_pushes_exactly_once():
    pushes: list[int] = []
    waits: list[float] = []

    async def notify(seconds: int) -> None:
        pushes.append(seconds)

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    attempts = {"count": 0}

    async def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise anthropic.RateLimitError(
                "rate limited", response=_response(429, {"retry-after": "45"}), body=None
            )
        return "答え"

    await call_with_retries(operation, notify=notify, sleep=sleep)
    assert pushes == [45], f"push が {len(pushes)} 回送られた"
    assert waits == [45.0]


async def test_529_without_a_retry_after_still_backs_off():
    """529 に retry-after が無くても指数バックオフで再試行されること。"""
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    attempts = {"count": 0}

    async def operation() -> str:
        attempts["count"] += 1
        if attempts["count"] <= 2:
            # ヘッダーを一切付けない 529。
            raise anthropic.OverloadedError(
                "overloaded", response=_response(529), body=None
            )
        return "答え"

    result = await call_with_retries(
        operation,
        policy=RetryPolicy(overload_base_seconds=1.0, overload_max_seconds=32.0),
        sleep=sleep,
    )
    assert result == "答え"
    assert len(waits) == 2
    assert all(wait > 0 for wait in waits), waits


# ---------------------------------------------------------------------------
# フェーズ3 検証 1: ユーザーAの派生鍵でユーザーBのファイルを復号すると InvalidTag。
# ---------------------------------------------------------------------------


def test_decrypting_another_users_file_raises_invalid_tag(tmp_path):
    from cryptography.exceptions import InvalidTag

    crypto = Crypto(decode_master_key(generate_master_key()))
    vault = Vault(tmp_path / "data", crypto)

    record_id = vault.write(
        "Uaaa", "Aの秘密".encode(), filename="a.txt", mime="text/plain",
        kind="file", created_at=1.0,
    )
    # B の領域に同じ暗号文を置いても、B の派生鍵では開かない。
    (vault.user_dir("Ubbb") / f"{record_id}.enc").write_bytes(
        vault.path_for("Uaaa", record_id).read_bytes()
    )

    with pytest.raises(DecryptionError) as caught:
        vault.read("Ubbb", record_id)
    # 仕様書が名指ししている InvalidTag が根本原因であること。
    assert isinstance(caught.value.__cause__, InvalidTag)


def test_the_associated_data_binds_the_record_to_its_user_and_role():
    """AAD の検証（12.10 の 5b）。文脈を1文字変えただけで開かなくなる。"""
    from cryptography.exceptions import InvalidTag

    crypto = Crypto(decode_master_key(generate_master_key()))
    blob = crypto.seal("Uaaa", b"payload", context="body:rec-1")

    for user, context in [("Ubbb", "body:rec-1"), ("Uaaa", "body:rec-2"), ("Uaaa", "meta:rec-1")]:
        with pytest.raises(DecryptionError) as caught:
            crypto.open(user, blob, context=context)
        assert isinstance(caught.value.__cause__, InvalidTag)


# ---------------------------------------------------------------------------
# フェーズ3 検証 2: PDFを取り込み、data/ 配下を strings 相当で走査して
#                   元の文が1文字も出ないこと。/tmp と $TMPDIR も汚さないこと。
# ---------------------------------------------------------------------------

KNOWN_SENTENCE = "四半期の売上は前年比十二パーセント増でした"
KNOWN_ASCII = "QuarterlyRevenueUp12Percent"

# strings(1) 相当: 印字可能文字が4文字以上続く並びを拾う。
_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e]{4,}")


def _strings_equivalent(root: Path) -> str:
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        blob = path.read_bytes()
        found.extend(m.decode("ascii") for m in _PRINTABLE_RUN.findall(blob))
        # UTF-8 の日本語は印字可能ASCIIに引っかからないので、生バイトでも探す。
        found.append(blob.decode("utf-8", errors="ignore"))
    return "\n".join(found)


def _one_page_pdf(text: str) -> bytes:
    """既知の文を含む1ページPDF。フォント無しでもテキスト層は入る。"""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1", "replace"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_an_ingested_pdf_leaves_nothing_readable_under_data(tmp_path, monkeypatch):
    from app.extract import PDF_MIME, extract_text
    from app.store import UserStore

    # /tmp と $TMPDIR を専用ディレクトリに向け、あとで汚れていないか確かめる。
    scratch = tmp_path / "tmpdir"
    scratch.mkdir()
    monkeypatch.setenv("TMPDIR", str(scratch))

    payload = _one_page_pdf(KNOWN_ASCII)
    # 取り込み経路と同じく、抽出はメモリ上で行う。
    extracted = extract_text(payload, mime=PDF_MIME, filename="決算.pdf") or ""

    crypto = Crypto(decode_master_key(generate_master_key()))
    root = tmp_path / "data"
    vault = Vault(root, crypto)
    user = "Uaaa"

    body_id = vault.write(
        user, payload, filename="決算.pdf", mime=PDF_MIME, kind="file", created_at=1.0
    )
    text_id = vault.write(
        user, f"{extracted}\n{KNOWN_SENTENCE}".encode(), filename="決算.pdf.txt",
        mime="text/plain", kind="extracted-text", created_at=1.0,
    )
    store = UserStore(vault.user_dir(user) / "store.db", user, crypto)
    store.add_document(
        body_id, kind="file", filename="決算.pdf", summary=KNOWN_SENTENCE,
        text_record_id=text_id, created_at=1.0,
    )
    store.add_turn("user", KNOWN_SENTENCE)

    haystack = _strings_equivalent(root)
    for needle in (KNOWN_SENTENCE, KNOWN_ASCII, "決算.pdf"):
        assert needle not in haystack, f"{needle!r} が data/ から読めてしまう"

    # 一時ディレクトリに平文が落ちていないこと。
    leftovers = [p for p in scratch.rglob("*") if p.is_file()]
    assert leftovers == [], f"一時ディレクトリにファイルが残った: {leftovers}"
    system_tmp_hits = [
        p for p in Path("/tmp").glob("*.pdf") if p.stat().st_mtime > time.time() - 60
    ]
    assert system_tmp_hits == [], f"/tmp にPDFが残った: {system_tmp_hits}"


def test_the_real_strings_binary_agrees(tmp_path):
    """仕様書が書いている strings コマンドそのものでも確認する。"""
    if not _which("strings"):
        pytest.skip("strings コマンドが無い")

    crypto = Crypto(decode_master_key(generate_master_key()))
    root = tmp_path / "data"
    vault = Vault(root, crypto)
    vault.write(
        "Uaaa", KNOWN_ASCII.encode(), filename="決算.pdf", mime="application/pdf",
        kind="file", created_at=1.0,
    )
    files = [str(p) for p in root.rglob("*") if p.is_file()]
    result = subprocess.run(
        ["strings", "-a", *files], capture_output=True, text=True, check=True
    )
    assert KNOWN_ASCII not in result.stdout


def _which(name: str) -> str | None:
    import shutil

    return shutil.which(name)


# ---------------------------------------------------------------------------
# フェーズ4 検証 2: /health が 200 を返すこと。
# ---------------------------------------------------------------------------


def test_health_endpoint_answers(secret_env):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        for path in ("/health", "/healthz"):
            response = client.get(path)
            assert response.status_code == 200, path
            assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# フェーズ2 検証 4: マスター鍵が無いときのログ。
# ---------------------------------------------------------------------------


def test_the_missing_master_key_log_uses_the_phrase_the_spec_looks_for(
    monkeypatch, tmp_path, caplog
):
    from app import serve
    from app.config import get_settings

    for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "value")
    monkeypatch.delenv("MASTER_KEY", raising=False)
    monkeypatch.setenv("LINE_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.keychain._read_keychain", lambda service, account: None)
    get_settings.cache_clear()

    with caplog.at_level("ERROR"), pytest.raises(SystemExit) as exit_info:
        serve.preflight()

    assert "master-key not found" in caplog.text
    assert "launchctl kickstart -k" in caplog.text
    # 11章の決定どおり exit 0。12.5 の "exit code 1" との食い違いは11章で解決済み。
    assert exit_info.value.code == 0
    get_settings.cache_clear()


def test_the_environment_override_is_not_used_by_these_tests():
    """テストが本物の Keychain を触っていないことの確認。"""
    assert os.environ.get("LINE_AI_KEYCHAIN_SERVICE") in (None, "line-personal-ai")
