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
