# OpenAPI (custom API)

A generic catalog entry: on its own it is no app at all. Point an install at an
OpenAPI document and Lemma reads it, turning **every endpoint in the spec into an
operation**. That is how a pod reaches an internal service, a partner's API, or
anything else nobody has written a connector for.

Because the operations come from *your* spec rather than from this catalog entry,
there is no fixed list of them here. Discover them on the install.

**Install config** — the address, set once when the install is created:

| Field | |
| --- | --- |
| `spec_url` | **Required.** URL of the OpenAPI JSON document. |
| `server_url` | Optional. Where requests actually go. Defaults to the first server in the spec; set it when the spec names a base URL you cannot reach. |
| `default_headers` | Optional. Sent with every request — anything the API needs beyond the credentials. |

**Account credentials** — per person, added after the install exists. Both
optional: `access_token` (sent as a bearer header) and `api_key` (sent however the
spec's security scheme says to).

## Set one up

```bash
lemma connectors auth-configs create openapi --kind http --name acme-api \
  -d '{"spec_url": "https://api.acme.test/openapi.json"}'

lemma connectors accounts create --auth-config acme-api -d '{"api_key": "sk-..."}'
```

An org can hold several — one per API — told apart by `--name`, which is what
every later command addresses.

## Then the ordinary loop

```bash
lemma connectors operations search acme-api "create an invoice"
lemma connectors operations details acme-api <OPERATION>
lemma connectors run acme-api <OPERATION> -d '{"payload": {}}'
```

## Tips

- **The spec is read at install time**, not per call. When the API gains an
  endpoint, run `lemma connectors auth-configs refresh-operations acme-api`. It
  answers `200` either way — read `status`; `failed` means the spec could not be
  fetched.
- **Editing `spec_url` or `server_url` invalidates the accounts on the install**:
  they go `REAUTH_REQUIRED` rather than being deleted, because credentials issued
  for one host are not credentials for another.
- **`kind`, `connector_id` and `config_source` cannot be changed.** Pointing at a
  different API is a new install.
- **Private, loopback and link-local addresses are refused** — including
  `169.254.169.254`. An internal API has to be reachable from Lemma.
- The config schema is closed: an unknown key is rejected, not stored.
