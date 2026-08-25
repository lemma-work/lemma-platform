# Issues

Bugs, unexpected behaviour, and places where the implementation does not deliver
what [the product specification](docs/product/README.md) says it should.

Tracked in git on purpose. Each entry is something that was found once,
verified against the code, and understood — writing it down is what stops it
being rediscovered from scratch later. A finding here is not a plan or a
roadmap: it is a statement about how the system behaves today, with a citation.

**Every entry is verified by reading the code or by running against it, never
inferred from a route name or a test name.** Each one cites `file:line`, and
says how it was found.

When a finding is fixed, delete its entry in the pull request that fixes it. A
register of already-fixed bugs is worse than no register — it teaches people to
stop trusting the file.

Ids are stable and append-only, so a `DEV-` reference in a scenario, a commit
message, or a code comment resolves to something.

## Format

```
### DEV-<AREA>-<NNN> — one-line summary
**Violates:** PS-<AREA>-<NNN>
**Severity:** high | medium | low | question
**Where:** path:line
**Required:** what the spec says must happen.
**Actual:** what happens instead.
**Why it matters:** the user-visible consequence.
**Fix:** the shape of the change.
```

Severity `question` means the divergence may be deliberate and the spec may be
the thing that is wrong — resolve it with a product decision before writing code.

## SDK — the clients we ship

### DEV-SDK-001 — The TypeScript SDK cannot authenticate outside a browser
**Violates:** *(no promise — the package is published as Node-loadable)*
**Severity:** high
**Where:** [`src/auth.ts`](lemma-typescript/src/auth.ts) — `writeStorageToken`,
`detectInjectedToken`, `setTestingToken`; config shape in
[`src/config.ts`](lemma-typescript/src/config.ts) `LemmaConfig`

**Required:** A Node caller can use the SDK. The package declares
`"type": "module"`, `"main": "dist/index.js"` and an `exports` map with no
`browser` condition, so it presents itself as usable server-side, and
`journeys/clients/` drives it that way.

**Actual:** It loads, and then every request is refused. `LemmaConfig` has no
field for a credential, and the only two ways to supply one are both gated on
`window`:

```ts
function writeStorageToken(token: string): void {
  if (typeof window === "undefined") return;   // setTestingToken is a no-op
  ...
}
function detectInjectedToken(): string | null {
  if (typeof window === "undefined") return null;
  ...
}
```

So `setTestingToken(...)` silently does nothing in Node, `AuthManager` starts
with `injectedToken = null`, no `Authorization` header is sent, and the API
answers 401. Verified against a running stack: the same script authenticates
once `globalThis.window` and a `localStorage` stand-in are defined, and fails
without them.

**Why it matters:** every **non-bundled** consumer is affected — a Node script,
a Lambda, an MCP server, any server-side integration. Those are exactly the
cases an SDK exists for. Bundler-based consumers are unaffected, which is why
this has survived.

**Fix:** give `LemmaConfig` a way to carry a credential — a `token`, or a
`getToken()` the `AuthManager` consults before falling back to browser
detection. Either makes the existing `Authorization: Bearer` path reachable
from Node; nothing else about the client needs to change.

**Found by:** `test_the_typescript_sdk_lists_pods`, which is
`xfail(strict=True)` until this is fixed.

> **Two things filed under this id are now fixed and are not this entry.** The
> built `dist` could not be *loaded* from Node at all — `src/auth.ts` and
> `src/supertokens.ts` imported the bare directory
> `supertokens-web-js/recipe/session`, which `moduleResolution: "Bundler"`
> permits and Node's ESM resolver refuses (`ERR_UNSUPPORTED_DIR_IMPORT`). And
> the scenario driving it constructed `new Lemma({baseUrl, token})`, none of
> which exists: the export is `LemmaClient` and the config field is `apiUrl`.
> With both corrected the SDK loads and reaches the API, which is how the
> authentication gap above became visible.

---

