# Signing in to sites

**Journey:** An agent working in a browser meets a login wall on a site the
platform has no connector for, and a person gets it past — without ever being
asked for a password, and without having to do it again next week.

This is the sibling of [connectors and accounts](connectors-and-accounts.md),
and the difference is the mechanism, not the principle. A connector holds a
credential a provider issued for programmatic use. Here there is no such
credential to hold: the site has a login form and nothing else. So what is kept
is what a browser keeps — the session that exists *after* somebody has signed
in — and the person signs in themselves, in the agent's browser, exactly as they
would anywhere.

The promise is the same one: a person's way in is theirs. It is encrypted, it is
never shown back to anyone including them, and nothing uses it except on their
behalf.

Two things follow from that, and they are load-bearing rather than incidental.

**A password is never asked for and never stored.** `PS-CONN-021` already
promises the system will never ask somebody for their provider password, and
that promise does not become weaker because the site has no consent screen. The
person types their password into the site, in a browser, the way they always do.
What is kept afterwards is the session — weaker than a password, revocable by
logging out, and useless anywhere but that site.

**The session is scoped to the site it belongs to.** An agent's browser visits
many places. What is kept from a sign-in is only what that site would receive:
the cookies a browser would send back to it, and the storage it wrote itself.

---

## Capability: Get past a login wall

### PS-BROWSER-010 — An agent that meets a login wall asks, and waits
**Status:** gap

> **Gap:** the capability is there and the scenario suite does not prove it.
> The whole journey -- ask, pause, sign in, capture, resume, reuse, remove --
> runs against a real sandbox and a real browser in
> `workspace/tests/e2e/test_signing_in_to_a_site_e2e.py`, and a paused sign-in
> now reaches a surface as a link. But the only scenario naming this promise
> asserts that somebody else's request is not found, which proves a refusal
> rather than the promise, and the coverage gate cannot tell those apart. This
> stays `gap` until a scenario proves it, rather than being marked covered by a
> test that does not.

- When an agent needs a site it has no working saved login for, the system shall
  ask the person to sign in themselves and shall put that site in front of them.
- The system shall never ask a person for a site password, and an agent shall
  never type one.
- The system shall pause the run until the person answers, however long that
  takes, rather than failing or timing out.
- The system shall reach the person wherever they are, with a link that opens
  only for them.

**Contracts:** `web_login.sign_in_request.get`

### PS-BROWSER-011 — A person can tell what they are signing in to
**Status:** manual

Verified by driving the live view against a real sandbox browser, which is where
the host and the padlock come from. A scenario cannot read what is painted on a
canvas.

- Before a person types anything, the system shall show which site the browser
  is actually on and whether the connection is protected.
- The system shall show the agent's own words for why it is asking, as the
  agent's words.

**Contracts:** `web_login.sign_in_request.get`

### PS-BROWSER-012 — Finishing resumes the run, and says whether it was kept
**Status:** covered

- When a person says they have signed in, the system shall check the browser
  before agreeing, and shall say so plainly when it cannot find a signed-in
  session.
- The system shall tell both the person and the agent whether the login was kept
  for next time, and why it was not when it was not.
- When a person declines, the system shall tell the agent so it can do the task
  another way or stop, rather than leaving it waiting.

**Contracts:** `web_login.sign_in_request.finish`, `web_login.sign_in_request.decline`

---

## Capability: Keep a way back in

### PS-BROWSER-020 — A saved login is the person's own
**Status:** covered

- The system shall keep only the session for the site it was captured on, and
  shall not keep anything belonging to any other site the browser has visited.
- The system shall encrypt it at rest and shall never return it to any caller,
  at any privilege level, including the person who created it.
- The system shall not let one person's saved login be used by another person.
- While a saved login works, the system shall not ask that person for that site
  again.

**Contracts:** `web_login.list`

### PS-BROWSER-021 — A login that has stopped working says so
**Status:** manual

Needs a site whose session can be expired on demand. Verified against a real
sandbox: an injection that lands on a login wall marks the saved login, and the
next run asks the person again.

- When a saved login no longer signs a person in, the system shall record that
  and shall ask them again rather than failing the task the same way twice.

**Contracts:** `web_login.list`

### PS-BROWSER-022 — A person sees and removes what is saved
**Status:** covered

- The system shall show a person every site it holds a login for, when each was
  last used, and whether each still works.
- The system shall let a person remove any of them.
- Removing a saved login does not sign the person out at the site, and the
  system shall say so rather than implying otherwise.
- The system shall keep a durable record of what has been done with a person's
  saved logins, answerable to them.

**Contracts:** `web_login.delete`, `web_login.history`

---

## Capability: Watch the agent's browser

### PS-BROWSER-030 — A person can watch, and drive, their own browser
**Status:** gap

> **Gap:** proved, but not here. Watching and driving are now covered end to
> end by `test_a_person_watches_the_agents_browser_and_then_drives_it` in the
> workspace e2e suite: the agent's own tool opens a page, a socket carries a
> real JPEG of it back, input from a watching socket is refused, and a click
> from a driving one navigates the page. That needs a real browser in a real
> container, which this suite has no way to stand up -- the same reason the
> sign-in flow lives there, noted at the top of `test_saved_logins.py`. The
> only scenario naming this promise asserts that *asking* about the browser
> starts nothing, which is one true corner of it; until the promise can be
> proved black-box over the shipped API, it is recorded as a gap here rather
> than claimed.

- The system shall let a person see what the browser in their own workspace is
  doing, and shall not start a paused workspace merely to answer whether it can.
- The system shall let that person drive it, and shall refuse input from a view
  that is only watching.
- The system shall not let anyone else watch or drive another person's browser.
- A viewer shall be able to move the pointer, type, and scroll, and shall not be
  able to make the browser do anything else.

**Contracts:** `workspace.browser.status`
