# App alias proof (WKWebView)

Shows, in the WebKit the macOS app ships, why pod apps are framed through a
locald alias and that the alias works:

```sh
make desktop-app-alias-proof      # or: desktop/e2e/app_alias_proof/run.sh
```

What runs:

- `stand_in.py` -- the backend's side, stood in: a workspace port, and a
  backend port that signs in with a `Domain=lemma.localhost` HttpOnly
  SameSite=Lax cookie and serves an app by `Host`
  (`proof.apps.lemma.localhost`) with its API door at `/_lemma`.
- `examples/app_alias_serve.rs` -- locald's real `app_alias` service, fronting
  that app on `app.lemma.localhost:<alias port>`.
- `probe.swift` -- a WKWebView with a fresh data store. It opens the workspace
  on `app.lemma.localhost`, signs in, frames the app, and reads back what the
  framed app's `/_lemma/users/me` call returned; then it opens the app's
  canonical URL top level.

It passes when:

- the workspace is a secure context with `navigator.mediaDevices.getUserMedia`
  and `crypto.subtle`;
- the app framed through the alias is signed in (200, and its origin is the
  alias);
- the canonical URL opened top level is signed in;
- and, as the control, the same app framed on its canonical address is **not**
  signed in (401) -- the WebKit behaviour the alias exists for.

Not a CI job yet: it needs a macOS runner with Xcode's `swift`, which the
Desktop macOS jobs have, and about 30 seconds. It does not cover the backend's
own cookie and host routing, which its tests cover, or the real app shell's
navigation gate, which `desktop/src/tests/navigation.rs` covers.
