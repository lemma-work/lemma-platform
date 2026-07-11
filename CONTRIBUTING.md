# Contributing to Lemma

Thanks for helping improve Lemma. Keep pull requests focused, explain user and
operator impact, and add tests for behavioral changes.

## Backend setup

The backend requires Python 3.14, PostgreSQL, and Redis. From `lemma-backend`:

```bash
uv sync
uv run alembic upgrade head
make test-unit
make lint
make lint-async
make typecheck-critical
make architecture
```

Use `make test-e2e-fast E2E_WORKERS=1` for the deterministic container-backed
suite. Real providers, model calls, and Docker AgentBox tests are protected and
must never use personal or production credentials.

## Backend architecture

- The canonical module documentation lives in `lemma-backend/docs/modules`.
- Cross-module collaboration belongs in an explicit `contracts` package or a
  versioned domain event. Do not import another module's API, service,
  infrastructure, or persistence model from new code.
- Persist state and its domain event in one database transaction through the
  unit of work. Consumers must be inbox-backed and idempotent.
- Classify errors at process boundaries. Preserve cancellation, redact secrets,
  and never return or log raw provider exceptions.
- Add an Alembic upgrade and downgrade test for schema changes. Reliability
  work for this release is intentionally consolidated in revision
  `0003_backend_reliability`.

## Generated code

Do not hand-edit OpenAPI client output. See the
[generated-code policy](docs/security/generated-code-policy.md), run both SDK
generation scripts, and commit the specification and resulting clients in the
same change.

## Pull requests

A backend PR should include migration/API compatibility notes, exact test
commands, security implications, and rollback guidance. Generated-client drift,
architecture growth, new broad catches, insufficient coverage, and unresolved
high/critical security findings are merge blockers.

Report vulnerabilities privately according to [SECURITY.md](SECURITY.md).
