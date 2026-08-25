import time

import pytest

from app.vault import Vault


@pytest.fixture
def vault(tmp_path, crypto):
    return Vault(tmp_path / "data", crypto)


def test_roundtrip_preserves_metadata(vault):
    record_id = vault.write(
        "U1", b"payload", filename="秘密.pdf", mime="application/pdf",
        kind="file", created_at=1.0,
    )
    meta, body = vault.read("U1", record_id)
    assert body == b"payload"
    assert (meta.filename, meta.mime, meta.kind, meta.size) == (
        "秘密.pdf", "application/pdf", "file", 7,
    )


def test_nothing_readable_lands_on_disk(vault):
    record_id = vault.write(
        "U1", "機密の中身".encode(), filename="給与明細.pdf",
        mime="application/pdf", kind="file", created_at=time.time(),
    )
    raw = vault.path_for("U1", record_id).read_bytes()
    assert "機密の中身".encode() not in raw
    assert "給与明細".encode() not in raw
    assert b".pdf" not in raw


def test_on_disk_name_is_only_the_uuid(vault):
    record_id = vault.write(
        "U1", b"x", filename="invoice.pdf", mime="application/pdf",
        kind="file", created_at=1.0,
    )
    assert vault.path_for("U1", record_id).name == f"{record_id}.enc"
    assert list(vault.record_ids("U1")) == [record_id]


def test_another_user_cannot_read_the_record(vault):
    record_id = vault.write(
        "U1", b"x", filename="a.txt", mime="text/plain", kind="file", created_at=1.0
    )
    # Same path, different user context: the AAD no longer matches.
    (vault.user_dir("U2") / f"{record_id}.enc").write_bytes(
        vault.path_for("U1", record_id).read_bytes()
    )
    from app.crypto import DecryptionError

    with pytest.raises(DecryptionError):
        vault.read("U2", record_id)


def test_no_partial_files_are_left_behind(vault):
    vault.write("U1", b"x", filename="a.txt", mime="text/plain", kind="f", created_at=1.0)
    assert not list(vault.user_dir("U1").glob("*.partial"))


@pytest.mark.parametrize("evil", ["../escape", "a/b", ".."])
def test_path_traversal_is_refused(vault, evil):
    with pytest.raises(ValueError):
        vault.path_for(evil, "id")
