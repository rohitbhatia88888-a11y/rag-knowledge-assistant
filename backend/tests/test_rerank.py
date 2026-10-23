from app.retrieval.rerank import diversify
from app.retrieval.store import RetrievedChunk


def chunk(doc: str, score: float) -> RetrievedChunk:
    return RetrievedChunk(
        id=f"{doc}-{score}", text="", document_id=doc, document_title=doc,
        headings=[], page_start=1, page_end=1, source_uri=None, score=score,
    )


def test_cap_makes_room_for_second_document():
    ranked = [chunk("a", 9), chunk("a", 8), chunk("a", 7), chunk("a", 6), chunk("b", 5), chunk("a", 4)]
    out = diversify(ranked, top_k=4, max_per_doc=3)
    # Without the cap the top 4 would all be from "a"; b5 replaces a6.
    assert [(c.document_id, c.score) for c in out] == [("a", 9), ("a", 8), ("a", 7), ("b", 5)]


def test_cap_backfills_when_only_one_document():
    ranked = [chunk("a", s) for s in (9, 8, 7, 6, 5)]
    assert [c.score for c in diversify(ranked, top_k=5, max_per_doc=2)] == [9, 8, 7, 6, 5]


def test_no_cap_is_plain_top_k():
    ranked = [chunk("a", 9), chunk("a", 8), chunk("b", 1)]
    assert [c.score for c in diversify(ranked, top_k=2, max_per_doc=0)] == [9, 8]
