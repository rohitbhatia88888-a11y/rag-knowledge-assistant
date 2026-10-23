from app.ingest.chunk import chunk_blocks
from app.ingest.parse import parse_markdown

DOC = """# Leave policy

## Annual leave

Full-time employees receive 25 days of paid annual leave per year.
Unused leave can be carried over up to 5 days.

| Tenure | Extra days |
|---|---|
| 5+ years | 2 |
| 10+ years | 5 |

## Sick leave

Employees may take up to 10 paid sick days per year.
"""


def test_headings_attach_to_chunks_and_tables_stay_whole():
    chunks = chunk_blocks(parse_markdown(DOC).blocks, target_chars=1200)
    texts = {c.text: c for c in chunks}

    annual = next(c for c in chunks if "25 days" in c.text)
    assert annual.headings == ["Leave policy", "Annual leave"]
    assert annual.embed_text.startswith("Leave policy > Annual leave")

    table = next(c for c in chunks if c.is_table)
    assert "| 10+ years | 5 |" in table.text and table.text.startswith("| Tenure")

    sick = next(c for c in chunks if "sick days" in c.text)
    assert sick.headings == ["Leave policy", "Sick leave"]
    assert len(texts) == 3


def test_long_paragraph_splits_with_overlap_and_hash_is_stable():
    para = " ".join(f"Sentence number {i} explains a rule." for i in range(200))
    chunks = chunk_blocks(parse_markdown(f"# Rules\n\n{para}").blocks, target_chars=500, overlap_chars=80)
    assert len(chunks) > 5
    assert all(len(c.text) <= 500 for c in chunks)
    # consecutive chunks share some trailing context
    assert chunks[0].text.split()[-1] in chunks[1].text

    again = chunk_blocks(parse_markdown(f"# Rules\n\n{para}").blocks, target_chars=500, overlap_chars=80)
    assert [c.content_hash for c in chunks] == [c.content_hash for c in again]


def test_oversized_table_repeats_header():
    rows = "\n".join(f"| item {i} | {'x' * 40} |" for i in range(200))
    md = f"| Name | Value |\n|---|---|\n{rows}"
    chunks = chunk_blocks(parse_markdown(md).blocks, target_chars=500)
    assert len(chunks) > 1
    assert all(c.text.startswith("| Name | Value |\n|---|---|") for c in chunks)
