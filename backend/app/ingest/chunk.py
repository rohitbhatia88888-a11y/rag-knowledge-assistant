"""Heading-aware chunking.

Each chunk carries the heading path it sits under (prepended for embedding so a
paragraph like "Employees get 20 days" still knows it belongs to "Leave policy"),
the pages it spans, and a content hash used for incremental re-embedding.
"""

import hashlib
import re
from dataclasses import dataclass

from app.ingest.parse import Block


@dataclass
class Chunk:
    text: str
    headings: list[str]
    page_start: int
    page_end: int
    is_table: bool = False

    @property
    def embed_text(self) -> str:
        """What gets embedded: heading context + body."""
        if not self.headings:
            return self.text
        return " > ".join(self.headings) + "\n\n" + self.text

    @property
    def content_hash(self) -> str:
        key = f"{self.page_start}-{self.page_end}\n{self.embed_text}"
        return hashlib.sha256(key.encode()).hexdigest()


def chunk_blocks(blocks: list[Block], target_chars: int = 1200, overlap_chars: int = 150) -> list[Chunk]:
    chunks: list[Chunk] = []
    heading_stack: list[tuple[int, str]] = []
    buf: list[str] = []
    buf_pages: list[int] = []

    def headings() -> list[str]:
        return [h for _, h in heading_stack]

    def flush() -> None:
        if buf:
            chunks.append(Chunk("\n\n".join(buf), headings(), min(buf_pages), max(buf_pages)))
            buf.clear()
            buf_pages.clear()

    for block in blocks:
        if block.kind == "heading":
            flush()
            while heading_stack and heading_stack[-1][0] >= block.level:
                heading_stack.pop()
            heading_stack.append((block.level, block.text))
            continue

        if block.kind == "table":
            flush()
            for part in _split_table(block.text, target_chars * 2):
                chunks.append(Chunk(part, headings(), block.page, block.page, is_table=True))
            continue

        pieces = _split_long_text(block.text, target_chars, overlap_chars) if len(block.text) > target_chars else [block.text]
        for piece in pieces:
            if buf and sum(len(b) for b in buf) + len(piece) > target_chars:
                flush()
            buf.append(piece)
            buf_pages.append(block.page)
    flush()
    return chunks


def _split_long_text(text: str, target: int, overlap: int) -> list[str]:
    """Split on sentence boundaries, carrying ~overlap chars of trailing context forward."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > target:
            parts.append(current.strip())
            tail = current[-overlap:]
            current = tail[tail.find(" ") + 1 :] if " " in tail else ""
        current = f"{current} {sentence}".strip()
        while len(current) > target:  # a single sentence longer than target
            parts.append(current[:target])
            current = current[target - overlap :]
    if current.strip():
        parts.append(current.strip())
    return parts


def _split_table(markdown: str, max_chars: int) -> list[str]:
    """Split oversized tables by rows, repeating the header row in every part."""
    if len(markdown) <= max_chars:
        return [markdown]
    lines = markdown.splitlines()
    header, rows = lines[:2], lines[2:]
    parts, current = [], list(header)
    for row in rows:
        if len("\n".join(current)) + len(row) > max_chars and len(current) > len(header):
            parts.append("\n".join(current))
            current = list(header)
        current.append(row)
    parts.append("\n".join(current))
    return parts
