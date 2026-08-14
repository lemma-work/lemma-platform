<!--
Keep pull requests focused. Delete any section that genuinely does not apply
rather than writing "N/A" everywhere — an honest short PR description beats a
padded one.
-->

## What changes

<!-- The user- or operator-visible effect, in a sentence or two. Not a file list. -->

## Why

<!-- The problem this solves. Link an issue if one exists. -->

## How to verify

<!-- Exact commands you ran, and what they printed. Reviewers should be able to
     paste these. -->

```bash

```

## Checklist

- [ ] Tests cover the behavioral change
- [ ] Lint, typecheck, and architecture gates pass locally

<!-- The rest apply only when the PR touches them. -->

- [ ] **Migrations** — Alembic upgrade *and* downgrade, both tested
- [ ] **API surface** — OpenAPI spec regenerated, both SDKs regenerated, all
      committed in this change
      ([policy](https://github.com/lemma-work/lemma-platform/blob/main/docs/security/generated-code-policy.md))
- [ ] **Compatibility** — breaking API or SDK changes are called out below with
      migration notes
- [ ] **Security** — no new secret in a log, response, or queued payload;
      authorization checked at the boundary
- [ ] **Rollback** — the plan if this has to be reverted after deploy

## Compatibility and rollback notes

<!-- Required for migrations, API changes, or anything that cannot simply be
     reverted. Say so explicitly if there is nothing to note. -->

---

<!--
Merge blockers, per CONTRIBUTING.md: generated-client drift, architecture
baseline growth, new broad `except` clauses, coverage below the committed
floor, and unresolved high/critical security findings.

Never include real credentials, customer data, or production dumps in a PR.
Suspected vulnerabilities go through SECURITY.md, not a public pull request.
-->
