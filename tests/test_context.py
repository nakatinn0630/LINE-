from app.context import (
    bigrams,
    build_system_prompt,
    relevance,
    select_documents,
    truncate,
)
from app.store import DocumentEntry


def doc(record_id, filename, summary, created_at=1.0):
    return DocumentEntry(record_id, "file", filename, summary, f"t-{record_id}", created_at)


DOCS = [
    doc("r1", "2026年度予算.pdf", "来年度の部門別予算と人員計画"),
    doc("r2", "旅行のしおり.pdf", "沖縄旅行の日程と宿の情報", 2.0),
    doc("r3", "recipe.txt", "how to make sourdough bread at home", 3.0),
]


def test_relevant_document_is_selected():
    hits = select_documents("来年度の予算はどうなってる？", DOCS)
    assert [h.entry.record_id for h in hits] == ["r1"]


def test_english_query_matches_english_document():
    hits = select_documents("how do I make sourdough?", DOCS)
    assert hits and hits[0].entry.record_id == "r3"


def test_unrelated_query_selects_nothing():
    assert select_documents("今日の天気は？", DOCS) == []


def test_limit_is_respected():
    many = [doc(f"r{i}", "予算.pdf", "予算の資料", float(i)) for i in range(10)]
    assert len(select_documents("予算の資料", many, limit=3)) == 3


def test_ties_prefer_the_newer_document():
    same = [doc("old", "予算.pdf", "予算の資料", 1.0), doc("new", "予算.pdf", "予算の資料", 9.0)]
    assert select_documents("予算の資料", same, limit=1)[0].entry.record_id == "new"


def test_empty_query_scores_zero():
    assert relevance("", "anything") == 0.0


def test_bigrams_ignore_punctuation_and_case():
    assert bigrams("Hello, World!") == bigrams("helloworld")


def test_system_prompt_includes_summary_and_documents():
    prompt = build_system_prompt("BASE", conversation_summary="要約", documents=[("a.pdf", "本文")])
    assert "BASE" in prompt and "要約" in prompt and "a.pdf" in prompt and "本文" in prompt


def test_system_prompt_omits_empty_sections():
    prompt = build_system_prompt("BASE", conversation_summary=None, documents=[])
    assert prompt == "BASE"


def test_blank_documents_do_not_create_a_section():
    assert build_system_prompt("BASE", documents=[("a.pdf", "   ")]) == "BASE"


def test_truncate_marks_the_cut():
    assert truncate("abc", 10) == "abc"
    long = truncate("x" * 100, 10)
    assert long.startswith("x" * 10) and "省略" in long
