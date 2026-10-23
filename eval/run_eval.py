"""Evaluate the RAG pipeline on the Halcyon corpus and question set.

Runs fully in-process: builds the corpus (rendering the .html sources to PDF), indexes it
into an in-memory Qdrant with each document's ACL, then asks every question as its persona.

    # retrieval + "I don't know" gate only: free, deterministic, no API key
    uv --project backend run --group eval python eval/run_eval.py

    # also generate answers and score them with RAGAS (needs OPENAI_API_KEY / OPENAI_BASE_URL)
    uv --project backend run --group eval python eval/run_eval.py --llm

    # fail (exit 1) if any metric falls below eval/thresholds.json
    ... --gate

Pipeline settings come from the same env vars as the app (CHUNK_TARGET_CHARS, RERANKER_MODEL, ...),
which is how eval/tune.py compares configurations.
"""

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
import warnings
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from qdrant_client import AsyncQdrantClient

from app.auth import Principal
from app.config import get_settings
from app.db import Document
from app.demo import build_corpus
from app.generation.answer import CITATION_RE, extract_citations, generate_answer, is_idk
from app.ingest.chunk import chunk_blocks
from app.ingest.parse import parse_file
from app.ingest.pipeline import index_file
from app.retrieval.embeddings import get_dense_embedder
from app.retrieval.pipeline import passes_gate, rank
from app.retrieval.store import RetrievedChunk, VectorStore
from app.services import get_llm
from app.tracing import get_langfuse, observation, score, trace, tracing_enabled

EVAL_DIR = Path(__file__).parent
CORPUS = EVAL_DIR / "corpus"
ANSWERABLE = {"fact", "table", "paraphrase", "multi_doc"}

warnings.filterwarnings("ignore", message="Payload indexes have no effect in the local Qdrant")
logging.basicConfig(level=logging.WARNING)


@dataclass
class Result:
    id: str
    category: str
    persona: str
    question: str
    answerable: bool
    gate_passed: bool
    evidence_found: int = 0
    evidence_total: int = 0
    first_evidence_rank: int | None = None
    leaked_documents: list[str] = field(default_factory=list)
    top_score: float | None = None
    retrieved: list[dict] = field(default_factory=list)
    answer: str | None = None
    answered: bool | None = None  # False when the final answer is "I don't know"
    cited: list[int] = field(default_factory=list)
    invalid_citations: list[int] = field(default_factory=list)
    faithfulness: float | None = None
    answer_relevancy: float | None = None
    error: str | None = None


# ---------------------------------------------------------------- corpus

def validate_dataset(questions: list[dict], docs: list[Document]) -> list[str]:
    """Every evidence phrase must appear in exactly the chunks of the document it names."""
    s = get_settings()
    chunk_text = {
        d.title: [c.text.lower() for c in chunk_blocks(parse_file(Path(d.storage_path), d.mime_type).blocks,
                                                       s.chunk_target_chars, s.chunk_overlap_chars)]
        for d in docs
    }
    errors = []
    for q in questions:
        for doc, phrase in q["evidence"]:
            if doc not in chunk_text:
                errors.append(f"{q['id']}: unknown document {doc!r}")
            elif not any(phrase.lower() in t for t in chunk_text[doc]):
                errors.append(f"{q['id']}: phrase {phrase!r} not found in any chunk of {doc}")
    return errors


# ---------------------------------------------------------------- scoring

def evidence_ranks(q: dict, ranked: list[RetrievedChunk]) -> list[int | None]:
    """1-based rank of the first chunk containing each evidence item, or None if not retrieved."""
    out = []
    for doc, phrase in q["evidence"]:
        out.append(
            next(
                (i for i, c in enumerate(ranked, 1) if c.document_title == doc and phrase.lower() in c.text.lower()),
                None,
            )
        )
    return out


class Judge:
    """RAGAS faithfulness and answer relevancy. The judge LLM goes through the same OpenAI-compatible endpoint."""

    def __init__(self, model: str):
        from openai import AsyncOpenAI
        from ragas.embeddings.base import BaseRagasEmbedding
        from ragas.llms import llm_factory
        from ragas.metrics.collections import AnswerRelevancy, Faithfulness

        class LocalEmbeddings(BaseRagasEmbedding):
            """RAGAS adapter over the app's dense embedder, so relevancy needs no embeddings API."""

            def embed_text(self, text: str, **kwargs) -> list[float]:
                return asyncio.run(get_dense_embedder().embed_query(text))

            async def aembed_text(self, text: str, **kwargs) -> list[float]:
                return await get_dense_embedder().embed_query(text)

        s = get_settings()
        client = AsyncOpenAI(api_key=s.openai_api_key, base_url=s.openai_base_url)
        llm = llm_factory(model, provider="openai", client=client, max_tokens=2048)
        self.faithfulness = Faithfulness(llm=llm)
        self.relevancy = AnswerRelevancy(llm=llm, embeddings=LocalEmbeddings())

    async def score(self, question: str, answer: str, contexts: list[str]) -> tuple[float, float]:
        # Citation markers aren't claims; left in, the judge treats "[1]" as unsupported statements.
        answer = CITATION_RE.sub("", answer).replace(" .", ".")
        f, r = await asyncio.gather(
            self.faithfulness.ascore(user_input=question, response=answer, retrieved_contexts=contexts),
            self.relevancy.ascore(user_input=question, response=answer),
        )
        return float(f.value), float(r.value)


async def evaluate_question(
    q: dict, store: VectorStore, tenant: str, personas: dict, docs_by_title: dict, judge: Judge | None, sem: asyncio.Semaphore
) -> Result:
    groups = tuple(personas[q["persona"]])
    principal = Principal(q["persona"], tenant, groups)
    res = Result(
        id=q["id"], category=q["category"], persona=q["persona"], question=q["question"],
        answerable=q["category"] in ANSWERABLE, gate_passed=False,
    )
    with trace("eval", user_id=q["persona"], tags=["eval", q["category"]], input=q["question"]) as root:
        ranked = await rank(store, principal, q["question"])
        res.gate_passed = passes_gate(ranked)
        res.top_score = round(ranked[0].score, 3) if ranked else None
        res.retrieved = [{"doc": c.document_title, "page": c.page_start, "score": round(c.score, 3)} for c in ranked]
        allowed = set(principal.principals)
        res.leaked_documents = sorted(
            {c.document_title for c in ranked if not set(docs_by_title[c.document_title].acl) & allowed}
        )

        if q["evidence"]:
            ranks = evidence_ranks(q, ranked)
            res.evidence_total = len(ranks)
            res.evidence_found = sum(r is not None for r in ranks)
            found = [r for r in ranks if r is not None]
            res.first_evidence_rank = min(found) if found else None

        if judge is None:
            return res

        chunks = ranked if res.gate_passed else []
        try:
            async with sem:
                res.answer = await generate_answer(get_llm(), q["question"], chunks) if chunks else None
            res.answered = bool(res.answer) and not is_idk(res.answer)
            if res.answer:
                res.cited, res.invalid_citations = extract_citations(res.answer, len(chunks))
            if res.answerable and res.answered:
                async with sem:
                    with observation("ragas", as_type="evaluator"):
                        res.faithfulness, res.answer_relevancy = await judge.score(
                            q["question"], res.answer, [c.text for c in chunks]
                        )
                score(root.trace_id, "faithfulness", res.faithfulness)
                score(root.trace_id, "answer_relevancy", res.answer_relevancy)
        except Exception as e:  # keep going; the run reports errors per question
            res.error = f"{type(e).__name__}: {str(e)[:300]}"
        root.update(output=res.answer)
    return res


def mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 4) if xs else None


def summarize(results: list[Result], llm: bool) -> dict:
    ans = [r for r in results if r.answerable]
    neg = [r for r in results if not r.answerable]
    ev_total = sum(r.evidence_total for r in ans)
    summary = {
        "questions": len(results),
        "retrieval.hit_rate": mean([float(r.evidence_found == r.evidence_total) for r in ans]),
        "retrieval.evidence_recall": round(sum(r.evidence_found for r in ans) / ev_total, 4) if ev_total else None,
        "retrieval.mrr": mean([1 / r.first_evidence_rank if r.first_evidence_rank else 0.0 for r in ans]),
        "gate.answer_rate": mean([float(r.gate_passed) for r in ans]),
        "gate.refusal_rate": mean([float(not r.gate_passed) for r in neg]),
        "security.leaked_chunks": sum(len(r.leaked_documents) for r in results),
    }
    if llm:
        answered = [r for r in ans if r.answered]
        summary |= {
            "answer.answer_rate": mean([float(bool(r.answered)) for r in ans]),
            "answer.refusal_rate": mean([float(not r.answered) for r in neg]),
            "answer.citation_rate": mean([float(bool(r.cited)) for r in answered]),
            "answer.invalid_citations": sum(len(r.invalid_citations) for r in results),
            "ragas.faithfulness": mean([r.faithfulness for r in answered if r.faithfulness is not None]),
            "ragas.answer_relevancy": mean([r.answer_relevancy for r in answered if r.answer_relevancy is not None]),
            "errors": sum(1 for r in results if r.error),
        }
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r.category].append(r)
    summary["by_category"] = {
        cat: {
            "n": len(rs),
            "hit_rate": mean([float(r.evidence_found == r.evidence_total) for r in rs]) if rs[0].answerable else None,
            "gate_correct": mean([float(r.gate_passed == r.answerable) for r in rs]),
            **({"final_correct": mean([float(bool(r.answered) == r.answerable) for r in rs])} if llm else {}),
        }
        for cat, rs in sorted(by_cat.items())
    }
    return summary


def check_thresholds(summary: dict, thresholds: dict) -> list[str]:
    failures = []
    for key, rule in thresholds.items():
        if key.startswith("_"):
            continue
        value = summary.get(key)
        if value is None:
            continue  # metric not computed in this mode (e.g. RAGAS without --llm)
        if "min" in rule and value < rule["min"]:
            failures.append(f"{key} = {value} < min {rule['min']}")
        if "max" in rule and value > rule["max"]:
            failures.append(f"{key} = {value} > max {rule['max']}")
    return failures


def print_report(summary: dict, results: list[Result]) -> None:
    print("\nSummary")
    for k, v in summary.items():
        if k != "by_category":
            print(f"  {k:28} {v}")
    print("\nBy category")
    for cat, m in summary["by_category"].items():
        print(f"  {cat:12} " + "  ".join(f"{k}={v}" for k, v in m.items()))
    misses = [r for r in results if r.answerable and r.evidence_found < r.evidence_total]
    refused = [r for r in results if r.answerable and not r.gate_passed]
    if misses:
        print("\nRetrieval misses")
        for r in misses:
            print(f"  {r.id:7} {r.evidence_found}/{r.evidence_total}  {r.question}")
    if refused:
        print("\nAnswerable questions refused by the score gate")
        for r in refused:
            print(f"  {r.id:7} top={r.top_score}  {r.question}")
    # Unanswerable questions passing the gate are expected: the LLM decides those. With --llm,
    # wrong final answers show up in by_category final_correct and the per-question results file.
    wrong_final = [r for r in results if r.answered is not None and r.answered != r.answerable]
    if wrong_final:
        print("\nWrong final behaviour (answered when it should refuse, or the reverse)")
        for r in wrong_final:
            print(f"  {r.id:7} answered={r.answered}  {r.question}")


# ---------------------------------------------------------------- main

async def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--llm", action="store_true", help="generate answers and score them with RAGAS")
    p.add_argument("--judge-model", default=None, help="RAGAS judge model (default: LLM_MODEL)")
    p.add_argument("--limit", type=int, default=None, help="only the first N questions")
    p.add_argument("--ids", default=None, help="comma-separated question ids")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--gate", action="store_true", help="exit 1 if a metric is below eval/thresholds.json")
    p.add_argument("--out", type=Path, default=EVAL_DIR / "results")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args()

    s = get_settings()
    if args.llm and not s.openai_api_key:
        print("--llm needs OPENAI_API_KEY (and OPENAI_BASE_URL for OpenRouter)", file=sys.stderr)
        return 2
    get_langfuse()

    questions = [json.loads(line) for line in (EVAL_DIR / "dataset.jsonl").read_text().splitlines() if line.strip()]
    if args.ids:
        wanted = set(args.ids.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    if args.limit:
        questions = questions[: args.limit]

    with tempfile.TemporaryDirectory() as tmp:
        tenant, docs, personas = build_corpus(Path(tmp), CORPUS)
        if errors := validate_dataset(questions, docs):
            print("Dataset errors:\n  " + "\n  ".join(errors), file=sys.stderr)
            return 2

        store = VectorStore(AsyncQdrantClient(location=":memory:"), "eval")
        await store.ensure_collection(get_dense_embedder().dim)
        t0 = time.perf_counter()
        for d in docs:
            await index_file(d, store)
        index_s = time.perf_counter() - t0

        judge = Judge(args.judge_model or s.llm_model) if args.llm else None
        sem = asyncio.Semaphore(args.concurrency)
        docs_by_title = {d.title: d for d in docs}
        t0 = time.perf_counter()
        results = await asyncio.gather(
            *(evaluate_question(q, store, tenant, personas, docs_by_title, judge, sem) for q in questions)
        )
        run_s = time.perf_counter() - t0

    summary = summarize(results, args.llm)
    config = {
        "chunk_target_chars": s.chunk_target_chars,
        "chunk_overlap_chars": s.chunk_overlap_chars,
        "embedding": s.openai_embedding_model if s.embedding_provider == "openai" else s.fastembed_model,
        "reranker": s.reranker_model,
        "min_rerank_score": s.min_rerank_score,
        "final_k": s.final_k,
        "llm": s.llm_model if args.llm else None,
        "judge": (args.judge_model or s.llm_model) if args.llm else None,
    }
    if not args.quiet:
        print(f"Config: {config}")
        print(f"Indexed {len(docs)} documents in {index_s:.1f}s; evaluated {len(results)} questions in {run_s:.1f}s")
        print_report(summary, results)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = args.out / f"run-{stamp}{'-llm' if args.llm else ''}.json"
    out_file.write_text(
        json.dumps({"config": config, "summary": summary, "results": [asdict(r) for r in results]}, indent=2)
    )
    print(json.dumps({"summary_file": str(out_file), **{k: v for k, v in summary.items() if k != "by_category"}}))
    get_langfuse().flush()

    if args.gate:
        thresholds = json.loads((EVAL_DIR / "thresholds.json").read_text())
        if failures := check_thresholds(summary, thresholds):
            print("\nEVAL GATE FAILED\n  " + "\n  ".join(failures), file=sys.stderr)
            return 1
        print("\nEval gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
