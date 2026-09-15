# Web login module

One person's own way back in to a site Lemma has no connector for.

This is the same product idea as a connector *account* — my credential at a
third party — reached by a different mechanism, which is why its authorization
copies `CONNECTOR_ACCOUNT` rather than inventing a parallel model, and why the
workspace UI shelves the two together rather than adding a second noun.

## What it owns

- `web_logins`: one row per person per origin, with the secret encrypted through
  `app/core/crypto` and registered for rotation.
- `web_login_audit`: a durable record of every use, capture, ask and removal.
- Turning a saved session into a signed-in sandbox browser, and the reverse.
- Six-digit TOTP codes, generated here so the seed never enters the sandbox.

## What it does not own

Driving the browser (`agent/tools/browser`), the takeover a person signs in
through (`workspace`), and the encryption itself (`core/crypto`). It reaches the
first two through their public surfaces and is reached through
`web_login/contracts`.

## Two kinds, in order

**`SESSION`** is the primary one: the cookies and local storage a browser holds
after somebody has signed in themselves. It is the class of secret the platform
already keeps for connectors — an OAuth refresh token — usually weaker, and
always revocable by the person logging out.

**`CREDENTIAL`** is a password, and it is a new class: reused across sites, not
revocable without changing it. It exists for the one case a session cannot
serve — an unattended run when nobody is awake to be asked — and is opt-in per
site for that reason.

## The rules that shape the code

- **Nothing returns a secret.** `WebLogin` has no field for one, so listing,
  auditing and the API are structurally unable to leak it. Exactly one method
  decrypts, and `web_login/contracts` does not export it.
- **The file, not the argument list.** A session reaches the sandbox as a file
  written over the runtime file API, under `/tmp`, `chmod 600`, removed
  afterwards. Argv is world-readable through `/proc`; the environment is worse.
- **One origin at a time.** The session for the site the agent is going to, not
  every session the person owns.
- **Removing a login is not signing out.** Deleting the row revokes Lemma's copy
  and nothing else; the session stays valid at the site until it expires or the
  person logs out there.
