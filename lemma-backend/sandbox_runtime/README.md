# sandbox_runtime

The program that runs *inside* a Lemma sandbox image, and the protocol the
backend talks to it with.

- `protocol.py` — the value types both sides speak
- `errors.py` — what a sandbox can do to you, as types
- `contracts.py` — the HTTP wire models
- `workspace/` — the workspace runtime server
- `function/` — the function runtime server

It lives under `lemma-backend/` because the workspace module owns it, but it is
a separate program: a sandbox image must never need the backend to start.
`lemma-backend/sandbox-images/` builds it into the two images, via
`make sandbox-images`.

## Licence

**Apache-2.0**, not the AGPLv3 that covers the rest of `lemma-backend/`.

This code ships inside container images that users run, and it was Apache-2.0
when it lived in the top-level `agentbox/` project. Absorbing it into the
backend was a structural change and was not meant to be a relicensing one, so
the licence travels with the code. See `LICENSE` in this directory.
