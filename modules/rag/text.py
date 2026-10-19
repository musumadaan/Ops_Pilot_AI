"""Tokenisation shared by BM25, the dense encoder and the reranker."""
from __future__ import annotations

import re

STOP = set(["a", "an", "the", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that", "these", "those", "with", "as", "at", "by", "from", "what", "which", "who", "whom", "how", "when", "where", "why", "do", "does", "did", "can", "could", "should", "would", "may", "might", "must", "we", "our", "us", "you", "your", "i", "about", "into", "if", "than", "then", "so", "not", "no", "any", "all", "there", "their", "they", "them", "he", "she", "his", "her", "has", "have", "had", "will", "shall", "per", "also"])
_SUFFIXES = ("ations", "ation", "ingly", "ments", "ment", "ities", "ity", "ings", "ing", "ies", "ied", "ers", "er", "ed", "es", "s", "ly")


def stem(w: str) -> str:
    for s in _SUFFIXES:
        if w.endswith(s) and len(w) - len(s) >= 3:
            return w[: -len(s)]
    return w


def tokens(text: str, keep_stop: bool = False) -> list[str]:
    ws = re.findall(r"[a-z0-9]+(?:[.,][0-9]+)*", text.lower().replace(",", "").replace("$", ""))
    return [stem(w) for w in ws if keep_stop or w not in STOP]


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
