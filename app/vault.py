"""Encrypted file storage under `data/`.

Layout is `data/<userId>/<uuid>.enc`. The on-disk name is an opaque UUID; the
real filename, MIME type and size live in an encrypted metadata header inside
the file, so nothing about the original document is readable from a directory
listing or from `strings`.

Each file is written to a temporary name in its final directory and renamed
into place, so a crash mid-write never leaves a half-file that looks valid.
Nothing is ever written outside `data/` - in particular no plaintext copy in
a temp directory.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from app.crypto import Crypto

MAGIC = b"LPAE\x01"
_META_LEN_BYTES = 4


@dataclass(frozen=True)
class RecordMeta:
    filename: str
    mime: str
    kind: str
    size: int
    created_at: float


class Vault:
    """Reads and writes encrypted records for one user at a time."""

    def __init__(self, root: Path, crypto: Crypto) -> None:
        self._root = root
        self._crypto = crypto

    def user_dir(self, user_id: str) -> Path:
        path = self._root / _safe_component(user_id)
        path.mkdir(parents=True, exist_ok=True)
        # The directory itself lists nothing useful, but keep it owner-only.
        os.chmod(path, 0o700)
        return path

    def path_for(self, user_id: str, record_id: str) -> Path:
        return self.user_dir(user_id) / f"{_safe_component(record_id)}.enc"

    def write(
        self,
        user_id: str,
        payload: bytes,
        *,
        filename: str,
        mime: str,
        kind: str,
        created_at: float,
        record_id: str | None = None,
    ) -> str:
        """Encrypt and store `payload`. Returns the record id (the UUID stem)."""
        record_id = record_id or str(uuid.uuid4())
        meta = {
            "filename": filename,
            "mime": mime,
            "kind": kind,
            "size": len(payload),
            "created_at": created_at,
        }
        meta_blob = self._crypto.seal_text(
            user_id, json.dumps(meta, ensure_ascii=False), context=f"meta:{record_id}"
        )
        body_blob = self._crypto.seal(user_id, payload, context=f"body:{record_id}")

        target = self.path_for(user_id, record_id)
        tmp = target.with_suffix(".enc.partial")
        with open(tmp, "wb") as handle:
            handle.write(MAGIC)
            handle.write(len(meta_blob).to_bytes(_META_LEN_BYTES, "big"))
            handle.write(meta_blob)
            handle.write(body_blob)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
        return record_id

    def read_meta(self, user_id: str, record_id: str) -> RecordMeta:
        with open(self.path_for(user_id, record_id), "rb") as handle:
            meta_blob, _ = self._read_header(handle, record_id)
        raw = json.loads(
            self._crypto.open_text(user_id, meta_blob, context=f"meta:{record_id}")
        )
        return RecordMeta(**raw)

    def read(self, user_id: str, record_id: str) -> tuple[RecordMeta, bytes]:
        """Decrypt a whole record into memory. Nothing touches disk."""
        with open(self.path_for(user_id, record_id), "rb") as handle:
            meta_blob, body_blob = self._read_header(handle, record_id, with_body=True)
        raw = json.loads(
            self._crypto.open_text(user_id, meta_blob, context=f"meta:{record_id}")
        )
        payload = self._crypto.open(user_id, body_blob, context=f"body:{record_id}")
        return RecordMeta(**raw), payload

    def read_text(self, user_id: str, record_id: str) -> str:
        return self.read(user_id, record_id)[1].decode("utf-8")

    def delete(self, user_id: str, record_id: str) -> None:
        self.path_for(user_id, record_id).unlink(missing_ok=True)

    def record_ids(self, user_id: str) -> Iterator[str]:
        directory = self._root / _safe_component(user_id)
        if not directory.is_dir():
            return
        for entry in sorted(directory.glob("*.enc")):
            yield entry.stem

    @staticmethod
    def _read_header(handle, record_id: str, *, with_body: bool = False):
        if handle.read(len(MAGIC)) != MAGIC:
            raise ValueError(f"{record_id}.enc is not an encrypted record")
        meta_len = int.from_bytes(handle.read(_META_LEN_BYTES), "big")
        meta_blob = handle.read(meta_len)
        if len(meta_blob) != meta_len:
            raise ValueError(f"{record_id}.enc is truncated")
        return meta_blob, (handle.read() if with_body else b"")


def _safe_component(value: str) -> str:
    """Reject anything that could escape the data directory."""
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError(f"unsafe path component: {value!r}")
    return value
