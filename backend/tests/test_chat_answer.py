import pytest
from fastapi import HTTPException

from app.api.chat import collect_answer


async def events(*items):
    for item in items:
        yield item


async def test_collect_answer_joins_tokens_and_drops_source_text():
    out = await collect_answer(events(
        ("session", {"session_id": "s1"}),
        ("sources", {"search_query": "leave days", "sources": [{"n": 1, "document_title": "Handbook", "text": "long"}]}),
        ("token", {"text": "Leave is "}),
        ("token", {"text": "25 days [1]."}),
        ("done", {"message_id": "m1", "idk": False, "cited": [1], "invalid_citations": [], "latency_ms": 12}),
    ))
    assert out["session_id"] == "s1"
    assert out["answer"] == "Leave is 25 days [1]."
    assert out["sources"] == [{"n": 1, "document_title": "Handbook"}]
    assert out["cited"] == [1] and out["idk"] is False


async def test_collect_answer_turns_error_event_into_http_error():
    with pytest.raises(HTTPException) as e:
        await collect_answer(events(
            ("session", {"session_id": "s1"}),
            ("error", {"message": "No LLM configured"}),
        ))
    assert e.value.status_code == 503
