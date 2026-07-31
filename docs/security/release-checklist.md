# Backend release checklist

- [ ] Database upgrade from the current revision and clean install pass.
- [ ] Downgrade is verified in a disposable environment; destructive cleanup is
      explicitly documented.
- [ ] Backend unit/component suite and critical container E2E pass.
- [ ] Aggregate coverage is at least 70%, schedule is at least 65%, changed code
      is at least 90%, and no module falls below its committed baseline.
- [ ] Ruff, critical BasedPyright, architecture, complexity, and broad-catch
      ratchets pass.
- [ ] OpenAPI, Python SDK, TypeScript SDK, L2 hooks, browser bundles, CLI, and
      frontend builds are clean.
- [ ] CodeQL, dependency review, Gitleaks, `pip-audit`, and Trivy have no
      unresolved high/critical findings.
- [ ] A protected-environment run for the release commit is successful and less
      than seven days old.
- [ ] Migration-first rolling sequence and worker-drain requirements are in the
      release notes.
- [ ] Every resolved issue entry contains implementation and exact test evidence.
- [ ] Rollback, replay, DLQ, partial cancellation, and support guidance are ready.
