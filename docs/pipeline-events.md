# Repository discovery and local Git events

I use a root `Dockerfile` with the repository root as its build context. A
`pipeline-service.json` beside it declares the test command, HTTP port and health
path. The `test` Dockerfile target is the default test image; the final stage is
the default runtime image. Discovery reports a missing target before attachment.
The Python and Node examples in `examples/pipeline/fixtures` show both contracts.

Discovery checks the numeric repository identity, protected environment branches,
review requirements and exact installed image and release destinations. The API
shows the discovered contract and routes before management can be enabled.
Attachment and enablement establish a starting cursor without deploying existing
production history.

The GitHub adapter checks webhook HMAC against the original body, installation
identity and approved routing. It then reads authoritative branch state, ancestry
and current review evidence. Delivery order does not establish freshness. Polling
uses the same admission path to recover missed deliveries.

For local development, `examples/pipeline/provider_simulator.py` provides a small
GitHub App API backed by local Git repositories. It generates local signing keys,
installation tokens and signed webhooks. Its fixture controls require a separate
credential. The application repositories and release-state repository remain
inside its persistent volume; they do not access remote fixture repositories.

Review records in this simulator are synthetic. They exercise authorization
decisions but do not prove that GitHub reviews or branch protections were enforced.
Real GitHub App installation and review acceptance remain separate from the local
demonstration. The simulator should remain on a private development network.

I keep keys, passwords, tokens and installation-specific values outside Git, using
Kubernetes Secret references. Discovery and public status responses never include
credential values.
