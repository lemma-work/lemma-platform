# web_login contract

What every `web_login` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `web_login.delete` | DELETE | `/web-logins` | Sign your browser out of a site |
| `web_login.list` | GET | `/web-logins` | List the sites your browser is signed in to |
| `web_login.sign_in.answer` | POST | `/web-logins/sign-ins/{conversation_id}/{tool_call_id}/answer` | Say whether you signed in |
| `web_login.sign_in.pending` | GET | `/web-logins/sign-ins/{conversation_id}/{tool_call_id}` | What a sign-in link is asking for |

<!-- /generated:operations -->

## `web_login.list`

Every site the caller's sandbox browser is signed in to, grouped by
registrable domain so `example.com` and `api.example.com` read as one login
rather than two.

Read from the browser each time, not from a table — the browser keeps its own
profile, so what it holds is the only true answer. **Never carries a secret**,
and that is now structural in a stronger sense than a response shape with no
field for one: cookie values never cross the sandbox boundary at all. What
comes back is a host, a count and an expiry.

Does not start a paused computer unless `wake=true`, and answers `sleeping`
instead. Rendering a settings page should not be what spins one up, and unlike
the table read this replaces, answering costs a round trip into the sandbox.

## `web_login.delete`

Signs the browser out of a site. Addressed by origin rather than id, because
that is what the person recognises and what the agent asked about.

This really signs it out — it drops the cookies. Its predecessor deleted
Lemma's encrypted copy and left the browser exactly as it was, which is why
every caller had to carry a disclaimer saying so. Nothing the person is signed
in to in their *own* browser is touched.

Refuses with 409 when the computer is not running, rather than reporting a
success it did not achieve, and 422 when the origin is not one a session could
belong to. Answers `forgotten: false` when the browser was holding nothing for
that site, which is not an error.
