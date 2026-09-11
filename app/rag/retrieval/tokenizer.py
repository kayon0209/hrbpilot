"""Chinese tokenization via jieba for PostgreSQL full-text search.

The keyword index and the query MUST use the same tokenizer so PostgreSQL's
'simple' text-search configuration works reliably for Chinese. jieba handles
the segmentation; 'simple' then treats each space-separated token as a word
(no stemming, no language-specific stopwords).
"""

import re

import jieba

# Keep tokens that contain at least one CJK / ASCII letter / digit character.
_MEANINGFUL = re.compile(r"[\u4e00-\u9fffA-Za-z0-9]")


def tokenize(text: str) -> str:
    """Segment text with jieba and return space-joined meaningful tokens."""
    tokens = [t for t in jieba.lcut(text.strip()) if _MEANINGFUL.search(t)]
    return " ".join(tokens)


def tokenize_for_query(text: str) -> str:
    """Same segmentation as ``tokenize`` — used for ``plainto_tsquery``."""
    return tokenize(text)
