from app.generation.answer import extract_citations, is_idk
from app.generation.prompts import IDK_ANSWER


def test_extract_citations_splits_valid_and_invalid():
    valid, invalid = extract_citations("Leave is 25 days [1][3]. Carry-over is 5 [1]. See [7].", n_sources=5)
    assert valid == [1, 3]
    assert invalid == [7]


def test_idk_detection():
    assert is_idk(IDK_ANSWER)
    assert is_idk(f"  {IDK_ANSWER.rstrip('.')}  ")
    assert not is_idk("Leave is 25 days [1].")
