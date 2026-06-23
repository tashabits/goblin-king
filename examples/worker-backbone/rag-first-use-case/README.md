# RAG First Use Case

This recipe demonstrates retrieval-augmented generation shape without generation from an
external model. The worker scores a checked-in local corpus with deterministic lexical
matching and returns a normal result envelope. It is useful as a first integration step
before swapping in a project-approved retriever or model gateway.

Local fixture files:

- `fixtures/corpus.json` contains the corpus.
- `inputs/query.input.json` contains the sample query.
- `schemas/query.input.schema.json` documents the input shape.
- `workers/local-rag/worker.py` can be imported directly by tests or run in a container.

No credentials, network calls, model APIs, or cloud services are used.

