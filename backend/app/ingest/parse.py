"""Turn source files into an ordered list of blocks (headings, text, tables) with page numbers.

Headings and tables are kept as their own blocks so the chunker can attach heading
context to every chunk and never cut a table in half.
"""

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf

BlockKind = Literal["heading", "text", "table"]


@dataclass
class Block:
    kind: BlockKind
    text: str
    page: int  # 1-based
    level: int = 0  # heading level, 1 = top


@dataclass
class ParsedDocument:
    blocks: list[Block]
    page_count: int


def parse_file(path: Path, mime_type: str) -> ParsedDocument:
    if mime_type == "application/pdf" or path.suffix.lower() == ".pdf":
        return parse_pdf(path)
    if path.suffix.lower() in {".md", ".markdown", ".txt"} or mime_type.startswith("text/"):
        return parse_markdown(path.read_text(encoding="utf-8", errors="replace"))
    raise ValueError(f"Unsupported file type: {mime_type} ({path.suffix})")


def parse_markdown(text: str) -> ParsedDocument:
    blocks: list[Block] = []
    para: list[str] = []
    table: list[str] = []

    def flush_para() -> None:
        if para:
            blocks.append(Block("text", "\n".join(para).strip(), page=1))
            para.clear()

    def flush_table() -> None:
        if table:
            blocks.append(Block("table", "\n".join(table), page=1))
            table.clear()

    for line in text.splitlines():
        stripped = line.strip()
        if heading := re.match(r"^(#{1,6})\s+(.*)", stripped):
            flush_para()
            flush_table()
            blocks.append(Block("heading", heading.group(2).strip(), page=1, level=len(heading.group(1))))
        elif stripped.startswith("|"):
            flush_para()
            table.append(stripped)
        elif not stripped:
            flush_para()
            flush_table()
        else:
            flush_table()
            para.append(line)
    flush_para()
    flush_table()
    return ParsedDocument(blocks=[b for b in blocks if b.text], page_count=1)


def parse_pdf(path: Path) -> ParsedDocument:
    blocks: list[Block] = []
    with pymupdf.open(path) as doc:
        body_size = _body_font_size(doc)
        for page_index, page in enumerate(doc):
            page_no = page_index + 1
            tables = _extract_tables(page)
            table_rects = [rect for rect, _ in tables]
            items: list[tuple[float, Block]] = [(rect.y0, Block("table", md, page_no)) for rect, md in tables]

            for b in page.get_text("dict")["blocks"]:
                if b.get("type") != 0:  # skip images
                    continue
                rect = pymupdf.Rect(b["bbox"])
                if any(rect.intersects(t) for t in table_rects):
                    continue
                spans = [s for line in b["lines"] for s in line["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = _join_lines(b)
                size = max(s["size"] for s in spans)
                bold = all(s["flags"] & 16 for s in spans)
                is_heading = len(text) < 120 and (size >= body_size * 1.15 or (bold and size >= body_size))
                if is_heading:
                    level = 1 if size >= body_size * 1.5 else 2 if size >= body_size * 1.15 else 3
                    items.append((rect.y0, Block("heading", text, page_no, level)))
                else:
                    items.append((rect.y0, Block("text", text, page_no)))

            items.sort(key=lambda item: item[0])
            blocks.extend(block for _, block in items)
        return ParsedDocument(blocks=blocks, page_count=doc.page_count)


def _body_font_size(doc: pymupdf.Document) -> float:
    """Most common span size, weighted by text length, sampled from the first pages."""
    sizes: list[float] = []
    for page in doc.pages(0, min(doc.page_count, 10)):
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for s in line["spans"]:
                    sizes.extend([round(s["size"], 1)] * len(s["text"].strip()))
    return statistics.mode(sizes) if sizes else 11.0


def _extract_tables(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, str]]:
    try:
        found = page.find_tables()
    except Exception:  # table detection is best-effort
        return []
    out = []
    for t in found.tables:
        md = t.to_markdown(clean=True).strip()
        if md:
            out.append((pymupdf.Rect(t.bbox), md))
    return out


def _join_lines(block: dict) -> str:
    lines = ["".join(s["text"] for s in line["spans"]).strip() for line in block["lines"]]
    text = " ".join(line for line in lines if line)
    text = re.sub(r"(\w)- (\w)", r"\1\2", text)  # re-join words hyphenated across lines
    return re.sub(r"\s+", " ", text).strip()
