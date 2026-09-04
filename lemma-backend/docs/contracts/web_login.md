# web_login contract

What every `web_login` API operation guarantees: who may call it, what must be true first, what changes, what it emits, and how it refuses.

The product promises these serve are in [the product specification](../../../docs/product/README.md). This says what each operation does; that says what any of it is for.

The table below is generated from the committed OpenAPI specification by `scripts/check_contracts.py --write`. Add the behaviour in prose under each operation's heading, outside the generated block — that part is preserved across regeneration.

<!-- generated:operations -- do not edit below -->

| Operation | Method | Path | Summary |
| --- | --- | --- | --- |
| `web_login.delete` | DELETE | `/web-logins` | Remove a saved site login |
| `web_login.history` | GET | `/web-logins/history` | What has been done with your saved logins |
| `web_login.list` | GET | `/web-logins` | List saved site logins |

<!-- /generated:operations -->

## `web_login.list`

Every site the caller has a saved login for, with when each was last used.
**Never carries a secret**, at any privilege level, including to the person who
created it — the same promise `connector.auth_config.get` makes. The response
shape has no field to put one in, so this is structural rather than a rule
somebody has to remember.

## `web_login.delete`

Forgets a site. Addressed by origin rather than id, because that is what the
person recognises and what the agent asked about.

Removing the row is the whole revocation from Lemma's side. It does **not** sign
the person out at the site: a deleted session is still a valid session there
until it expires or they log out, and the copy says so rather than implying a
revocation that did not happen.

Refuses with 404 when nothing is saved for that origin, and 422 when the origin
is not one a session could belong to.

## `web_login.history`

What has been done with the caller's saved logins: used, captured, asked for,
removed — with which agent did it and when. Carries no secret and no page
content by construction.

This exists because a credential store nobody can inspect is one nobody can
trust, and because nothing else in the platform keeps a durable audit trail to
fall back on.
