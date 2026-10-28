"""Sentence-aware chunking with real overlap.  Works on structured segments so that page, section
and modality metadata survive into every chunk (needed for citations)."""
from __future__ import annotations

import re
from typing import Any, Dict, List

_SENT_SPLIT = re.compile(r"(?<=[.!?।॥])\s+|\n+")


def _units(text: str, size: int) -> List[str]:
    out: List[str] = []
    for part in _SENT_SPLIT.split(text):
        part = part.strip()
        if not part:
            continue
        while len(part) > size:                       # very long run-on line: split on whitespace
            cut = part.rfind(" ", 0, size)
            cut = cut if cut > size // 2 else size
            out.append(part[:cut].strip())
            part = part[cut:].strip()
        if part:
            out.append(part)
    return out


def chunk_segments(segments: List[Dict[str, Any]], size: int = 700, overlap: int = 120) -> List[Dict[str, Any]]:
    overlap = min(overlap, size // 2)
    chunks: List[Dict[str, Any]] = []

    def emit(units: List[str], seg: Dict[str, Any]) -> None:
        text = " ".join(units).strip()
        if len(text) >= 20:
            chunks.append({"content": text, "page_ref": seg.get("page_ref"), "section": seg.get("section"),
                           "modality": seg.get("modality", "text")})

    for seg in segments:
        cur: List[str] = []
        cur_len = 0
        for u in _units(seg.get("text", ""), size):
            if cur and cur_len + len(u) + 1 > size:
                emit(cur, seg)
                keep: List[str] = []
                total = 0
                for x in reversed(cur):
                    if total + len(x) > overlap:
                        break
                    keep.insert(0, x)
                    total += len(x) + 1
                cur, cur_len = keep, total
            cur.append(u)
            cur_len += len(u) + 1
        if cur:
            emit(cur, seg)
    for i, c in enumerate(chunks):
        c["chunk_index"] = i
    return chunks


def recursive_chunk_text(text: str, chunk_size: int = 700, chunk_overlap: int = 120) -> List[Dict[str, Any]]:
    """Backwards compatible helper for plain text."""
    return chunk_segments([{"text": text}], chunk_size, chunk_overlap)
