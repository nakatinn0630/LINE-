"""Envelope encryption for everything this app writes to disk.

A single 32-byte master key lives in the macOS Keychain. Per-user keys are
derived from it with HKDF-SHA256, so one user's key never decrypts another
user's data, and the master key itself is never used directly as a cipher key.

Every record is sealed with AES-256-GCM under a fresh random 96-bit nonce.
The associated data binds each ciphertext to the user it belongs to and to the
role it plays (file body, extracted text, summary, history row, ...), so a
ciphertext cannot be moved between users, between files, or between columns
without the open failing.
"""

from __future__ import annotations

import base64
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MASTER_KEY_ACCOUNT = "MASTER_KEY"
MASTER_KEY_BYTES = 32
NONCE_BYTES = 12

# Domain separation for HKDF. Bumping this rotates every derived key, which
# would orphan all existing ciphertexts - so it is versioned deliberately.
_HKDF_SALT = b"line-personal-ai/hkdf/v1"


class DecryptionError(RuntimeError):
    """Raised when a ciphertext fails to authenticate."""


def generate_master_key() -> str:
    """Return a fresh base64 master key, for `security add-generic-password`."""
    return base64.b64encode(secrets.token_bytes(MASTER_KEY_BYTES)).decode("ascii")


def decode_master_key(encoded: str) -> bytes:
    """Decode and validate the base64 master key read from the Keychain."""
    try:
        raw = base64.b64decode(encoded.strip(), validate=True)
    except Exception as exc:
        raise ValueError(
            "MASTER_KEY in the Keychain is not valid base64. Regenerate it with "
            "`uv run python -m app.tools genkey`."
        ) from exc
    if len(raw) != MASTER_KEY_BYTES:
        raise ValueError(
            f"MASTER_KEY must decode to {MASTER_KEY_BYTES} bytes, got {len(raw)}."
        )
    return raw


class Crypto:
    """Derives per-user keys from the master key and seals/opens records."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) != MASTER_KEY_BYTES:
            raise ValueError(f"master key must be {MASTER_KEY_BYTES} bytes")
        self._master = master_key
        self._cache: dict[str, AESGCM] = {}

    def _cipher(self, user_id: str) -> AESGCM:
        cipher = self._cache.get(user_id)
        if cipher is None:
            derived = HKDF(
                algorithm=hashes.SHA256(),
                length=MASTER_KEY_BYTES,
                salt=_HKDF_SALT,
                info=f"user:{user_id}".encode(),
            ).derive(self._master)
            cipher = AESGCM(derived)
            self._cache[user_id] = cipher
        return cipher

    def seal(self, user_id: str, plaintext: bytes, *, context: str) -> bytes:
        """Encrypt `plaintext`, returning nonce || ciphertext || tag.

        `context` is authenticated but not encrypted; it names what this record
        is (e.g. "body:<uuid>") so a ciphertext cannot be swapped for another.
        """
        nonce = os.urandom(NONCE_BYTES)
        aad = self._aad(user_id, context)
        return nonce + self._cipher(user_id).encrypt(nonce, plaintext, aad)

    def open(self, user_id: str, blob: bytes, *, context: str) -> bytes:
        """Decrypt a record produced by `seal`. Raises DecryptionError on tamper."""
        if len(blob) <= NONCE_BYTES:
            raise DecryptionError("ciphertext is too short to contain a nonce")
        nonce, body = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
        aad = self._aad(user_id, context)
        try:
            return self._cipher(user_id).decrypt(nonce, body, aad)
        except InvalidTag as exc:
            raise DecryptionError(
                f"failed to authenticate record (user={user_id!r}, context={context!r})"
            ) from exc

    def seal_text(self, user_id: str, text: str, *, context: str) -> bytes:
        return self.seal(user_id, text.encode("utf-8"), context=context)

    def open_text(self, user_id: str, blob: bytes, *, context: str) -> str:
        return self.open(user_id, blob, context=context).decode("utf-8")

    @staticmethod
    def _aad(user_id: str, context: str) -> bytes:
        return f"line-personal-ai/v1|{user_id}|{context}".encode()
