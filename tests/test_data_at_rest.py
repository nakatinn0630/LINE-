"""The `strings`-over-data/ guarantee, checked automatically.

This mirrors the manual procedure in the README: put identifiable content
through the whole pipeline, then scan every byte under `data/` for it.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from app.crypto import Crypto
from app.store import UserStore
from app.vault import Vault

SECRETS = [
    "三菱UFJ銀行 普通 1234567",
    "パスワードは Hunter2 です",
    "acquisition of NewCo closes on March 14",
    "健康診断の結果.pdf",
    "給与明細_2026年3月.docx",
]


@pytest.fixture
def populated_data_dir(tmp_path, crypto: Crypto):
    """A data/ tree holding every kind of record the app writes."""
    root = tmp_path / "data"
    vault = Vault(root, crypto)
    user = "Uabcdef1234567890"

    body_id = vault.write(
        user, SECRETS[0].encode(), filename=SECRETS[3],
        mime="application/pdf", kind="file", created_at=1.0,
    )
    text_id = vault.write(
        user, SECRETS[1].encode(), filename=f"{SECRETS[3]}.txt",
        mime="text/plain", kind="extracted-text", created_at=1.0,
    )
    vault.write(
        user, SECRETS[2].encode(), filename=SECRETS[4],
        mime="image/jpeg", kind="image", created_at=2.0,
    )

    store = UserStore(vault.user_dir(user) / "store.db", user, crypto)
    store.add_turn("user", SECRETS[0])
    store.add_turn("assistant", SECRETS[2])
    store.set_summary(2, SECRETS[1])
    store.add_document(
        body_id, kind="file", filename=SECRETS[3], summary=SECRETS[2],
        text_record_id=text_id, created_at=1.0,
    )
    return root


def _all_bytes(root) -> bytes:
    return b"".join(p.read_bytes() for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("secret", SECRETS)
def test_no_secret_appears_verbatim_anywhere_under_data(populated_data_dir, secret):
    assert secret.encode("utf-8") not in _all_bytes(populated_data_dir)


@pytest.mark.parametrize("secret", SECRETS)
def test_strings_output_contains_no_secret(populated_data_dir, secret):
    """The literal command from the README's verification procedure."""
    if shutil.which("strings") is None:
        pytest.skip("the strings binary is not installed here")
    files = [str(p) for p in populated_data_dir.rglob("*") if p.is_file()]
    result = subprocess.run(
        ["strings", "-a", *files], capture_output=True, text=True, check=True
    )
    assert secret not in result.stdout
    # Also check the ASCII-only fragments, which `strings` finds most readily.
    for fragment in ("Hunter2", "NewCo", "1234567"):
        assert fragment not in result.stdout


def test_filenames_on_disk_reveal_nothing(populated_data_dir):
    names = [p.name for p in populated_data_dir.rglob("*") if p.is_file()]
    assert names, "nothing was written"
    for name in names:
        assert name.endswith(".enc") or name.startswith("store.db"), name
        for secret in SECRETS:
            assert secret not in name


def test_the_directory_is_owner_only(populated_data_dir):
    for directory in [p for p in populated_data_dir.rglob("*") if p.is_dir()]:
        assert directory.stat().st_mode & 0o077 == 0, directory


def test_a_wrong_master_key_cannot_read_any_of_it(populated_data_dir, tmp_path):
    from app.crypto import Crypto, DecryptionError, decode_master_key, generate_master_key

    attacker = Vault(populated_data_dir, Crypto(decode_master_key(generate_master_key())))
    user = "Uabcdef1234567890"
    record_ids = list(attacker.record_ids(user))
    assert record_ids
    for record_id in record_ids:
        with pytest.raises(DecryptionError):
            attacker.read(user, record_id)
