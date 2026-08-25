from __future__ import annotations

import pytest

from app.store import UserStore


@pytest.fixture
def store(tmp_path, crypto):
    return UserStore(tmp_path / "U1" / "store.db", "U1", crypto)


def test_history_roundtrip_in_order(store):
    store.add_turn("user", "ひとつめ")
    store.add_turn("assistant", "こたえ1")
    store.add_turn("user", "ふたつめ")
    store.add_turn("assistant", "こたえ2")
    assert [(t.role, t.content) for t in store.recent_turns(1)] == [
        ("user", "ふたつめ"),
        ("assistant", "こたえ2"),
    ]
    assert store.message_count() == 4


def test_recent_turns_of_zero_is_empty(store):
    store.add_turn("user", "x")
    assert store.recent_turns(0) == []


def test_unexpected_role_is_rejected(store):
    with pytest.raises(ValueError):
        store.add_turn("system", "nope")


def test_turns_between_is_bounded(store):
    for i in range(10):
        store.add_turn("user" if i % 2 == 0 else "assistant", f"m{i}")
    assert [t.content for t in store.turns_between(4, 8)] == ["m4", "m5", "m6", "m7"]


def test_summary_keeps_the_latest(store):
    store.set_summary(4, "ふるい要約")
    store.set_summary(8, "あたらしい要約")
    assert store.latest_summary() == (8, "あたらしい要約")


def test_summary_is_none_before_any_compaction(store):
    assert store.latest_summary() is None


def test_documents_come_back_newest_first(store):
    store.add_document("r1", kind="file", filename="a.pdf", summary="A",
                       text_record_id="t1", created_at=1.0)
    store.add_document("r2", kind="image", filename="b.jpg", summary="B",
                       text_record_id=None, created_at=2.0)
    assert [d.record_id for d in store.documents()] == ["r2", "r1"]
    assert store.documents()[1].text_record_id == "t1"


def test_settings_roundtrip(store):
    assert store.get_setting("model") is None
    store.set_setting("model", "claude-fable-5")
    store.set_setting("model", "claude-opus-5")
    assert store.get_setting("model") == "claude-opus-5"


def test_no_user_content_is_readable_in_the_database_file(tmp_path, crypto):
    path = tmp_path / "U1" / "store.db"
    store = UserStore(path, "U1", crypto)
    secrets = ["給与は毎月25日", "acquisition of NewCo", "決算資料.pdf", "予算の要約"]
    store.add_turn("user", secrets[0])
    store.add_turn("assistant", secrets[1])
    store.add_document("r1", kind="file", filename=secrets[2], summary=secrets[3],
                       text_record_id=None, created_at=1.0)
    store.set_summary(2, secrets[3])

    blob = path.read_bytes()
    for secret in secrets:
        assert secret.encode() not in blob, secret


def test_another_users_store_cannot_read_the_rows(tmp_path, crypto):
    path = tmp_path / "U1" / "store.db"
    UserStore(path, "U1", crypto).add_turn("user", "secret")
    from app.crypto import DecryptionError

    impostor = UserStore(path, "U2", crypto)
    with pytest.raises(DecryptionError):
        impostor.recent_turns(1)
