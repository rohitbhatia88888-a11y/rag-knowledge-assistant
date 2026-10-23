"""Retrieval eval: does the right passage show up in the top-k the LLM sees?

Runs against a live API with a dev token. Each line of the dataset is
{"question": ..., "expected_document": "<title>", "expected_text": "<phrase that must appear in a retrieved chunk>"}

    uv --project backend run python eval/retrieval_eval.py eval/sample_dataset.jsonl --user alice --groups hr

Reports hit rate and MRR. Answer quality (faithfulness, relevancy) is scored with RAGAS in Phase 2.
"""

import argparse
import json
from pathlib import Path

import httpx


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("dataset", type=Path)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--user", default="alice")
    p.add_argument("--tenant", default="acme")
    p.add_argument("--groups", default="")
    args = p.parse_args()

    client = httpx.Client(base_url=args.api, timeout=120)
    groups = [g for g in args.groups.split(",") if g]
    token = client.post("/auth/dev-token", json={"user_id": args.user, "tenant_id": args.tenant, "groups": groups}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    rows = [json.loads(line) for line in args.dataset.read_text().splitlines() if line.strip()]
    hits, rr = 0, 0.0
    for row in rows:
        sources = _sources(client, headers, row["question"])
        rank = next(
            (
                i
                for i, s in enumerate(sources, start=1)
                if s["document_title"] == row["expected_document"]
                and row.get("expected_text", "").lower() in s["text"].lower()
            ),
            None,
        )
        hits += rank is not None
        rr += 1 / rank if rank else 0
        print(f"{'HIT ' if rank else 'MISS'} rank={rank or '-'}  {row['question']}")

    n = len(rows) or 1
    print(f"\nhit@k: {hits / n:.2%}   MRR: {rr / n:.3f}   ({len(rows)} questions)")


def _sources(client: httpx.Client, headers: dict, question: str) -> list[dict]:
    with client.stream("POST", "/chat", json={"question": question}, headers=headers) as resp:
        event = None
        for line in resp.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event == "sources":
                return json.loads(line[6:])["sources"]
    return []


if __name__ == "__main__":
    main()
