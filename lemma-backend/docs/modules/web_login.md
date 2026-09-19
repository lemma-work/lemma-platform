# Web login module

One person's own way back in to a site Lemma has no connector for.

This is the same product idea as a connector *account* — my credential at a
third party — reached by a different mechanism, which is why its authorization
copies `CONNECTOR_ACCOUNT` rather than inventing a parallel model, and why the
workspace UI shelves the two together rather than adding a second noun.

## What it owns

- Asking a person to sign in, and pausing the agent's run until they answer.
- Saying which sites the person's sandbox browser is signed in to, and signing
  it out of one.
- Deciding what counts as one site: `api.example.com` and `example.com` are one
  login, which is a public-suffix question and is answered here.

It owns **no storage**. There is no table, no secret, no encryption and no
rotation entry, because there is nothing kept: the browser's profile is durable
and the session simply stays in it.

## What it does not own

Driving the browser (`agent/tools/browser`) and the takeover a person signs in
through (`workspace`). It reaches both through their public surfaces and is
reached through `web_login/contracts`.

## The shape this used to be, and why it is not

It captured the browser's state after a sign-in, guessed whether the capture
"was a login", encrypted it, stored it, and injected it back later. Both
guesses were vacuous in practice — an origin with any local-storage entry at
all looked signed in, so a dismissed cookie banner was saved as a session; and
a saved login was validated by opening the origin's root, which on a marketing
homepage looks nothing like a login wall, so a dead session passed. Three
commits in that module's history are the same bug class rediscovered.

Reconstructing a browser's state from outside is the wrong shape for the
problem. The browser already keeps sessions, correctly, and has for thirty
years. So there is one place that answers "is this signed in", and it is the
browser.

## The rules that shape the code

- **No cookie value crosses the sandbox boundary.** The relay reports a host
  and an expiry; that is enough to say "you are signed in to example.com" and
  enough to delete it, and deliberately not enough to sign in as them
  somewhere else. The old capture had to carry values by construction.
- **The browser is asked every time.** Nothing is mirrored into a table, so
  there is no cache to go stale and no second answer to disagree with.
- **Signing out really signs out.** `web_login.delete` clears the site's data
  in the browser. Its predecessor deleted Lemma's own copy and left the browser
  exactly as it was, which is why every caller carried a disclaimer saying so.
- **The relay decides nothing about sites.** It reports hosts and deletes the
  hosts it is given; whether two hosts are one login is answered here, where
  the public-suffix list lives. A policy that can be answered in two places is
  a policy that will be answered differently.

## What the person gives up for it

One browser profile per person means a later conversation inherits an earlier
conversation's logins. That is how a browser works and it is the deliberate
trade: per-conversation cookie isolation is gone. The session also sits in the
sandbox's own filesystem, readable by the agent's shell — which changes less
than it sounds, since an agent that can drive the browser can already use every
session in it.
