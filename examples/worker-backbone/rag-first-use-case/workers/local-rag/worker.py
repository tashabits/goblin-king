"""Deterministic local RAG-style worker backed by checked-in fixture documents."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

KIND = "example.worker-backbone.local-rag"
MODEL_NAME = "deterministic-lexical-fixture"
STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "by",
    "do",
    "does",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "to",
    "which",
    "with",
}


def build_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a deterministic result envelope for the local query fixture."""
    query = str(payload.get("query", "")).strip()
    top_k = max(1, int(payload.get("top_k", 2)))
    corpus_path = resolve_corpus_path(str(payload.get("corpus_path", "fixtures/corpus.json")))
    documents = load_corpus(corpus_path)
    matches = retrieve(query, documents, top_k)
    best_score = matches[0]["score"] if matches else 0

    return {
        "status": "success",
        "data": {
            "kind": KIND,
            "query": query,
            "answer": synthesize_answer(matches),
            "matches": matches,
            "citations": [
                {"id": match["id"], "title": match["title"]}
                for match in matches
            ],
            "policy": {
                "model": MODEL_NAME,
                "external_calls": 0,
            },
        },
        "artifacts": [],
        "metrics": {
            "documents_scored": len(documents),
            "matches_returned": len(matches),
            "best_score": best_score,
        },
        "handoff": [],
        "error": None,
    }


def load_corpus(path: Path) -> list[dict[str, str]]:
    """Load the local corpus document list."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    documents = payload.get("documents", [])
    return [
        {
            "id": str(document["id"]),
            "title": str(document["title"]),
            "text": str(document["text"]),
        }
        for document in documents
    ]


def retrieve(query: str, documents: list[dict[str, str]], top_k: int) -> list[dict[str, Any]]:
    """Rank documents by lexical overlap and return stable match records."""
    query_terms = set(tokenize(query))
    scored = []
    for document in documents:
        document_terms = set(tokenize(f"{document['title']} {document['text']}"))
        overlap_terms = sorted(query_terms.intersection(document_terms))
        score = len(overlap_terms)
        scored.append(
            {
                "id": document["id"],
                "title": document["title"],
                "score": score,
                "overlap_terms": overlap_terms,
                "excerpt": first_sentence(document["text"]),
            }
        )
    scored.sort(key=lambda item: (-item["score"], item["id"]))
    return scored[:top_k]


def synthesize_answer(matches: list[dict[str, Any]]) -> str:
    """Create a deterministic answer from the best matching fixture document."""
    if not matches or matches[0]["score"] == 0:
        return "No local fixture document matched the query."
    best = matches[0]
    return f"{best['title']}: {best['excerpt']}"


def tokenize(text: str) -> list[str]:
    """Tokenize text into lowercase search terms."""
    return [
        token
        for token in re.findall(r"[a-z0-9]+", text.lower())
        if token not in STOPWORDS
    ]


def first_sentence(text: str) -> str:
    """Return a compact deterministic excerpt."""
    sentence = text.strip().split(".")[0].strip()
    return f"{sentence}."


def resolve_corpus_path(corpus_path: str) -> Path:
    """Resolve fixture paths for source-tree imports and container execution."""
    path = Path(corpus_path)
    if path.is_absolute():
        return path

    here = Path(__file__).resolve()
    candidates = [
        here.parent / path,
        here.parents[2] / path,
        Path.cwd() / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def main() -> None:
    """Read contract input and write the deterministic RAG result envelope."""
    payload = json.loads(Path(os.environ["GOBLIN_INPUT_PATH"]).read_text(encoding="utf-8"))
    if "GOBLIN_CONTEXT_PATH" in os.environ:
        Path(os.environ["GOBLIN_CONTEXT_PATH"]).read_text(encoding="utf-8")
    result = build_result(payload)
    result_path = Path(os.environ["GOBLIN_RESULT_PATH"])
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    main()

