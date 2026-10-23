"""Compare pipeline configurations on the retrieval eval (no LLM, no cost), then sweep the
"I don't know" threshold using the re-ranker scores recorded by one run.

    uv --project backend run --group eval python eval/tune.py
"""

import json
import os
import subprocess
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).parent
BACKEND = EVAL_DIR.parent / "backend"

BASE = {"CHUNK_TARGET_CHARS": "1200", "RERANKER_MODEL": "Xenova/ms-marco-MiniLM-L-12-v2"}
CONFIGS = {
    "baseline (1200 chars, MiniLM-L-12, bge-small)": {},
    "chunks 600": {"CHUNK_TARGET_CHARS": "600", "CHUNK_OVERLAP_CHARS": "100"},
    "chunks 2000": {"CHUNK_TARGET_CHARS": "2000", "CHUNK_OVERLAP_CHARS": "200"},
    "reranker MiniLM-L-6": {"RERANKER_MODEL": "Xenova/ms-marco-MiniLM-L-6-v2"},
    "reranker bge-v2-m3-int8": {"RERANKER_MODEL": "BAAI/bge-reranker-v2-m3-int8"},
    "embeddings bge-base": {"FASTEMBED_MODEL": "BAAI/bge-base-en-v1.5"},
    "final_k 8": {"FINAL_K": "8"},
    "max 3 chunks per doc": {"MAX_CHUNKS_PER_DOC": "3"},
    "max 2 chunks per doc": {"MAX_CHUNKS_PER_DOC": "2"},
}
COLUMNS = ["retrieval.hit_rate", "retrieval.evidence_recall", "retrieval.mrr", "security.leaked_chunks"]


def run(overrides: dict) -> dict:
    env = {**os.environ, **BASE, **overrides}
    out = subprocess.run(
        ["uv", "run", "--group", "eval", "python", str(EVAL_DIR / "run_eval.py"), "--quiet"],
        cwd=BACKEND, env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def sweep(result_file: Path) -> None:
    results = json.loads(result_file.read_text())["results"]
    pos = [r["top_score"] for r in results if r["answerable"]]
    neg = [r["top_score"] for r in results if not r["answerable"]]
    print("\nThreshold sweep (gate only; the LLM still refuses what the gate lets through)")
    print(f"  {'min_rerank_score':>16}  {'answerable kept':>15}  {'unanswerable blocked':>20}")
    for t in [-12, -11, -10, -9, -8, -7, -6, -5, -4, -2, 0]:
        kept = sum(s is not None and s >= t for s in pos) / len(pos)
        blocked = sum(s is None or s < t for s in neg) / len(neg)
        print(f"  {t:>16}  {kept:>15.1%}  {blocked:>20.1%}")


def main() -> None:
    rows = []
    for name, overrides in CONFIGS.items():
        print(f"running: {name}", file=sys.stderr)
        rows.append((name, run(overrides)))

    width = max(len(n) for n, _ in rows)
    print(f"\n{'config':{width}}  " + "  ".join(f"{c.split('.')[-1]:>15}" for c in COLUMNS))
    for name, summary in rows:
        print(f"{name:{width}}  " + "  ".join(f"{summary[c]!s:>15}" for c in COLUMNS))

    sweep(Path(rows[0][1]["summary_file"]))


if __name__ == "__main__":
    main()
