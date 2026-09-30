# apps contract

What every `apps` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `app.access.redeem` | POST | `/_lemma/app-access/redeem` | Redeem App Access |
| `app.access.request.authorize` | POST | `/apps/access/requests/{request_id}/authorize` | Authorize App Access Request |
| `app.access.request.create` | POST | `/_lemma/app-access/requests` | Create App Access Request |
| `app.asset.get` | GET | `/pods/{pod_id}/apps/{app_name}/assets/{asset_path}` | Get App Asset |
| `app.asset.root.get` | GET | `/pods/{pod_id}/apps/{app_name}/assets` | Get App Root Asset |
| `app.bundle.upload` | POST | `/pods/{pod_id}/apps/{app_name}/bundle` | Upload App Bundle |
| `app.create` | POST | `/pods/{pod_id}/apps` | Create App |
| `app.create_from_widget` | POST | `/pods/{pod_id}/apps/from-widget` | Save Widget As App |
| `app.delete` | DELETE | `/pods/{pod_id}/apps/{app_name}` | Delete App |
| `app.dist.archive.get` | GET | `/pods/{pod_id}/apps/{app_name}/dist/archive` | Download App Dist Archive |
| `app.get` | GET | `/pods/{pod_id}/apps/{app_name}` | Get App |
| `app.list` | GET | `/pods/{pod_id}/apps` | List Apps |
| `app.release.list` | GET | `/pods/{pod_id}/apps/{app_name}/releases` | List App Releases |
| `app.release.promote` | POST | `/pods/{pod_id}/apps/{app_name}/releases/{release_ref}/promote` | Promote App Release |
| `app.source.archive.get` | GET | `/pods/{pod_id}/apps/{app_name}/source/archive` | Download App Source Archive |
| `app.update` | PATCH | `/pods/{pod_id}/apps/{app_name}` | Update App |

<!-- /generated:operations -->

### `app.access.request.create`

An anonymous browser on a configured HTTPS app origin supplies a SHA-256
verifier challenge. The server derives the slug and optional release from the
Host, checks the exact Origin, and creates a five-minute Redis request bound to
an HttpOnly, Secure, host-only browser cookie. Creation is rate-limited by the
trusted client address. Neither the response nor the bootstrap reveals private
app metadata or whether the slug exists.

### `app.access.request.authorize`

A main SuperTokens session, presented through the SDK API transport, authorizes
the pending request for its exact app origin. The server checks account identity
and existing `app.read` rules; private previews also require `app.update`. The
caller may be that app origin or the configured workspace origin, and cannot
nominate another callback. Delegated credentials are refused. Successful access
produces one redemption code expiring after 60 seconds. Missing or unauthorized
apps receive a generic 404.

### `app.access.redeem`

The initiating browser redeems its code with the verifier on the same exact app
origin. Redis atomically consumes the request and code, rejecting incorrect
proofs, browser bindings and cross-app attempts. A valid parent session and
eligible account are required. The host-only `__Host-lemmaAppAccess` cookie
authorizes that app's HTML and assets only, with an expiry no later than the
parent session. Every private read rechecks identity and current app permission.

All handoff responses and private assets use `private, no-store`. Unavailable
Redis or identity services fail closed with a useful 503; external handoff
phases have bounded waits and run outside permission transactions. Public assets
retain anonymous serving and their existing caching.
