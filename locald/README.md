# lemma-locald

`lemma-locald` is the durable, per-user control plane for managed Lemma Local.
Desktop and `lemma-stack` connect over an authenticated OS-local transport:

- macOS/Linux: a mode-`0600` Unix-domain socket in the local state directory;
- Windows: a login-session-scoped `LOCAL\\...` named pipe.

The daemon owns operation serialization, state snapshots, a bounded event
journal, and service lifecycle. During the migration phase it runs the existing
`lemma-stack supervise` protocol as a compatibility child. That child is later
replaced by native host-pack and managed-runtime reconcilers without changing
the desktop/CLI protocol.

```bash
cargo run --manifest-path locald/Cargo.toml -- serve
cargo run --manifest-path locald/Cargo.toml -- status
cargo run --manifest-path locald/Cargo.toml -- send '{"cmd":"start","id":"manual"}'
```

`LEMMA_LOCALD_ROOT` selects an isolated state root for tests. The daemon finds
the compatibility supervisor through `LEMMA_LOCALD_SUPERVISOR_BIN`, a sibling
`lemma-supervisor` binary, or the monorepo development fallback.

