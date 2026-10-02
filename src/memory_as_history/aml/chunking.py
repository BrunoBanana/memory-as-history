"""Message chunking for the AML layer (memory-as-history).

Primary-source preservation rule (史料保真): a long message is never
truncated at the retrieval limit — instead it is divided at sentence
boundaries into complete blocks that share the original event
metadata, so the full text stays reconstructable and every semantic
segment can be retrieved independently.

Conservative by design:
- Only messages longer than ``max_chars`` are chunked; ordinary
  messages (the LoCoMo-style case) pass through untouched.
- A block never splits a sentence; text with no sentence boundary is
  left whole (better one intact source than broken shards).
- Defaults keep chunk count small (max ~2-4 blocks per message).
"""

from __future__ import annotations

import re

# Sentence-terminating punctuation kept with the sentence it ends
# (Chinese primary, English fallback; newlines also act as boundaries).
_SENTENCE_END = re.compile(r"(?<=[。！？；.!?\n])")


def split_long_message(
    content: str,
    max_chars: int = 1200,
    min_chars: int = 200,
) -> list[str]:
    """Split ``content`` into sentence-complete blocks when it exceeds
    ``max_chars``. Returns ``[content]`` unchanged otherwise.

    ``min_chars`` prevents degenerate slivers: a block is only emitted
    once it has at least ``min_chars`` characters (unless it is the
    only content of the message).
    """
    if not isinstance(content, str) or not content.strip():
        return [content]
    if len(content) <= max_chars:
        return [content]

    sentences = [
        s
        for s in _SENTENCE_END.split(content)
        if s.strip() or "\n" in s  # keep newlines so the source stays exact
    ]
    if len(sentences) <= 1:
        # No usable sentence boundary: keep the source intact rather
        # than cutting mid-sentence (保真 over 收益).
        return [content]

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = current + sentence
        if len(candidate) > max_chars and len(current) >= min_chars:
            chunks.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current.strip():
        chunks.append(current.strip())

    # Guard: if boundaries are too sparse the merge above may produce a
    # single chunk still over the limit; keep it whole rather than
    # splitting mid-sentence (never return empty).
    return chunks or [content]
