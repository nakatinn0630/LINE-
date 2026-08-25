"""Building the model's context: rolling summary, recent turns, relevant documents.

Document retrieval scores the user's message against the *summary index* - the
short encrypted summary and filename we stored when the document arrived - and
only the winning documents get their full text decrypted. Nothing else is
brought into memory.

Scoring uses character bigrams rather than word tokens, because the messages
are largely Japanese and there are no spaces to split on.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.store import DocumentEntry

_PUNCTUATION = re.compile(r"[\s　-〿＀-／：-＠!-/:-@\[-`{-~]+")


def _normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _PUNCTUATION.sub("", folded)


def bigrams(text: str) -> set[str]:
    cleaned = _normalize(text)
    if len(cleaned) < 2:
        return {cleaned} if cleaned else set()
    return {cleaned[i : i + 2] for i in range(len(cleaned) - 1)}


def relevance(query: str, document_text: str) -> float:
    """Fraction of the query's bigrams that the document covers. 0.0 to 1.0."""
    query_grams = bigrams(query)
    if not query_grams:
        return 0.0
    return len(query_grams & bigrams(document_text)) / len(query_grams)


@dataclass(frozen=True)
class ScoredDocument:
    entry: DocumentEntry
    score: float


def select_documents(
    query: str,
    documents: list[DocumentEntry],
    *,
    limit: int = 3,
    threshold: float = 0.08,
) -> list[ScoredDocument]:
    """Pick the documents worth decrypting for this message."""
    scored = [
        ScoredDocument(entry, relevance(query, f"{entry.filename}\n{entry.summary}"))
        for entry in documents
    ]
    hits = [item for item in scored if item.score >= threshold]
    hits.sort(key=lambda item: (-item.score, -item.entry.created_at))
    return hits[:limit]


def build_system_prompt(
    base: str,
    *,
    conversation_summary: str | None = None,
    documents: list[tuple[str, str]] | None = None,
) -> str:
    """Assemble the system prompt from the base persona, summary and documents.

    `documents` is a list of (label, text) already decrypted in memory.
    """
    sections = [base]

    if conversation_summary:
        sections.append(
            "# これまでの会話の要約\n"
            "以前のやり取りの要約です。必要なときだけ参照してください。\n\n"
            f"{conversation_summary}"
        )

    if documents:
        body = "\n\n".join(
            f"## {label}\n{text}" for label, text in documents if text.strip()
        )
        if body:
            sections.append(
                "# 参考資料\n"
                "ユーザーが過去に共有した資料のうち、今回の質問に関係しそうなものです。"
                "内容に触れるときは、どの資料の話かがわかるように答えてください。\n\n"
                f"{body}"
            )

    return "\n\n".join(sections)


def truncate(text: str, limit: int) -> str:
    """Trim to `limit` characters, marking the cut so the model knows it happened."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... 以降は長さの都合で省略 ...]"
