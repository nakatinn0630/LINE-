import pytest

from app.crypto import (
    MASTER_KEY_BYTES,
    Crypto,
    DecryptionError,
    decode_master_key,
    generate_master_key,
)


def test_roundtrip(crypto):
    blob = crypto.seal("U1", b"secret", context="body:1")
    assert crypto.open("U1", blob, context="body:1") == b"secret"


def test_nonce_is_fresh_per_record(crypto):
    a = crypto.seal("U1", b"same", context="c")
    b = crypto.seal("U1", b"same", context="c")
    assert a != b, "identical plaintexts must not produce identical ciphertexts"


def test_another_users_key_cannot_open_it(crypto):
    blob = crypto.seal("U1", b"secret", context="body:1")
    with pytest.raises(DecryptionError):
        crypto.open("U2", blob, context="body:1")


def test_context_binding_prevents_swapping_records(crypto):
    blob = crypto.seal("U1", b"secret", context="body:1")
    with pytest.raises(DecryptionError):
        crypto.open("U1", blob, context="body:2")


def test_tampering_is_detected(crypto):
    blob = bytearray(crypto.seal("U1", b"secret", context="c"))
    blob[-1] ^= 0x01
    with pytest.raises(DecryptionError):
        crypto.open("U1", bytes(blob), context="c")


def test_derived_keys_are_per_user_not_per_instance(master_key):
    one, two = Crypto(master_key), Crypto(master_key)
    blob = one.seal("U1", b"secret", context="c")
    # Same master key, so a second instance opens U1's record...
    assert two.open("U1", blob, context="c") == b"secret"
    # ...but the key derived for U2 is a different key entirely.
    with pytest.raises(DecryptionError):
        two.open("U2", blob, context="c")


def test_generated_key_is_32_bytes():
    assert len(decode_master_key(generate_master_key())) == MASTER_KEY_BYTES


@pytest.mark.parametrize("bad", ["not base64!!", "c2hvcnQ="])
def test_bad_master_key_is_rejected(bad):
    with pytest.raises(ValueError):
        decode_master_key(bad)
