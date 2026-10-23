"""Langfuse tracing. Disabled (all calls become no-ops) unless LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set.

A chat request becomes one trace: rewrite -> retrieve -> answer, with the LLM calls
recorded as generations (model, tokens, cost) by Langfuse's OpenAI instrumentation.
"""

from contextlib import contextmanager
from functools import lru_cache

from langfuse import Langfuse, propagate_attributes

from app.config import get_settings


@lru_cache
def get_langfuse() -> Langfuse:
    s = get_settings()
    enabled = bool(s.langfuse_public_key and s.langfuse_secret_key)
    if enabled:
        import langfuse.openai  # noqa: F401  (patches the OpenAI SDK so LLM calls become generations)
    return Langfuse(
        public_key=s.langfuse_public_key or "disabled",
        secret_key=s.langfuse_secret_key or "disabled",
        base_url=s.langfuse_base_url,
        environment=s.env,
        tracing_enabled=enabled,
    )


def tracing_enabled() -> bool:
    s = get_settings()
    return bool(s.langfuse_public_key and s.langfuse_secret_key)


@contextmanager
def trace(name: str, *, user_id: str | None = None, session_id: str | None = None, tags: list[str] | None = None, input=None):
    """Root observation for one request. Yields the span; `span.trace_id` links feedback to it."""
    with propagate_attributes(user_id=user_id, session_id=session_id, tags=tags, trace_name=name):
        with get_langfuse().start_as_current_observation(name=name, as_type="chain", input=input) as span:
            yield span


@contextmanager
def observation(name: str, *, as_type: str = "span", input=None):
    with get_langfuse().start_as_current_observation(name=name, as_type=as_type, input=input) as span:
        yield span


def score(trace_id: str | None, name: str, value: float, comment: str | None = None) -> None:
    if trace_id and tracing_enabled():
        get_langfuse().create_score(trace_id=trace_id, name=name, value=value, comment=comment)
