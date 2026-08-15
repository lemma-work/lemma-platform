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
- [ ] Each release workflow verified its own artifact before publishing: the two
      PyPI workflows ran the Python SDK/CLI suites and the distribution checks,
      the npm workflow ran the TypeScript typechecks, bundle-freshness diff and
      unit tests, and the Desktop workflow ran the workspace fmt, clippy and
      tests. These run inside the publishing job, so no separate CI dispatch is
      required — but a release commit whose CI run shows those suites as
      *skipped* is expected, not a finding.
- [ ] Migration-first rolling sequence and worker-drain requirements are in the
      release notes.
- [ ] Every resolved issue entry contains implementation and exact test evidence.
- [ ] Rollback, replay, DLQ, partial cancellation, and support guidance are ready.
