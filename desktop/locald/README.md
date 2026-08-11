# lemma-locald

`lemma-locald` is the durable per-user control plane shared by Lemma Desktop
and `lemma-stack`. It remains operable when the frontend, backend, managed
guest, or network is unhealthy.

- macOS/Linux uses a private mode-`0600` Unix-domain socket and capability
  token;
- Windows uses a login-session-scoped `LOCAL\\...` named pipe;
- operations are serialized and publish resumable state/event snapshots;
- host backend/frontend processes and the managed guest are reconciled as one
  release;
- configuration secrets live in the OS credential vault and never appear in
  status payloads or persisted JSON;
- applying operator configuration restarts and health-gates only the backend,
  with transactional configuration/vault rollback on failure.

The managed host pack runs one all-in-one backend and one frontend. The private
guest controller starts PostgreSQL, Redis, compatibility auth when enabled,
and on-demand sandboxes through a narrow authenticated protocol. It
does not expose Docker/Podman/containerd sockets to the backend.

```bash
cargo run --manifest-path desktop/Cargo.toml -p lemma-locald -- serve
cargo run --manifest-path desktop/Cargo.toml -p lemma-locald -- status
cargo run --manifest-path desktop/Cargo.toml -p lemma-locald -- send '{"cmd":"start","id":"manual"}'
cargo run --manifest-path desktop/Cargo.toml -p lemma-locald -- send '{"cmd":"control.snapshot","id":"manual"}'
```

`LEMMA_LOCALD_ROOT` selects an isolated state root. Packaged launchers provide
`LEMMA_LOCALD_HOST_PACK_ROOT`,
`LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT`, and the relevant
`LEMMA_LOCALD_RUNTIME_BRIDGE_BIN`/`LEMMA_LOCALD_VZ_BIN` helper explicitly. The
monorepo path remains a development fallback; managed mode has no Python
supervisor dependency.
