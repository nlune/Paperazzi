from __future__ import annotations

import re
from collections.abc import Iterable


WORD_RE = re.compile(r"[\w]+(?:[-'][\w]+)?", flags=re.UNICODE)


def normalize_token(token: str) -> str:
    return re.sub(r"(^[^\w]+|[^\w]+$)", "", token.casefold(), flags=re.UNICODE)


def tokenize_words(text: str) -> list[str]:
    return [match.group(0) for match in WORD_RE.finditer(text)]


def occurrence_numbers(tokens: Iterable[str]) -> list[int]:
    counts: dict[str, int] = {}
    occurrences: list[int] = []
    for token in tokens:
        normalized = normalize_token(token)
        counts[normalized] = counts.get(normalized, 0) + 1
        occurrences.append(counts[normalized])
    return occurrences
