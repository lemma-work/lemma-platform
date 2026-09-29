# Chat and email-code onboarding

Email-code login uses the OSS SuperTokens passwordless recipe internally. Lemma
owns challenge endpoints, mailbox verification and canonical account selection;
the recipe's public signup and consume endpoints are disabled. Configure normal
transactional email delivery and the auth frontend origin before enabling it.
Codes last at most ten minutes, allow three wrong attempts and require a
sixty-second resend wait. Web and chat share email send limits.

Apply backend migrations before starting the API and worker. Both must run the
same version: the worker consumes `surface.onboarding.ready` from the normal
transactional outbox. Pending requests are stored outside pod conversations and
purged after handoff, cancellation or expiry. Keep the worker running for replay
and expiry cleanup. Do not log webhook form contents or email codes.

## Web authentication and local verification

The user-facing auth screens live in `lemma-frontend`. Both sign-in and signup
first mint a browser binding at `/auth/email-code/browser`, then submit the email
to `/auth/email-code/continue`. Its `method` selects a password form, Google or
Microsoft, or an email-code challenge. Passwordless accounts created through chat
use the code branch and retain their canonical identity. `/start` is the explicit
code endpoint; it does not select an existing account's login method.

Set backend `AUTH_FRONTEND_URL` and `FRONTEND_URL` to the actual new frontend
origin, including its port in local development. The browser Origin must match
one of those origins. Cookie binding, JSON requests and origin checks remain
required; CORS configuration alone does not authorize an email-code request.
`EMAIL_LOGIN_ORIGIN_NOT_ALLOWED` indicates a deployment configuration mismatch,
not that the address belongs to a Google account.

Root Makefile defaults now point backend auth and frontend URLs at the workspace
on port 3000. Start the backend with the normal local stack and run
`make dev-frontend` for `lemma-frontend`; override `DEV_WORKSPACE_PORT` consistently
in both commands when choosing another port. The operator harness remains on
`DEV_FRONTEND_PORT` and is not the user auth frontend. Existing custom environment
files or deployment overrides must also use the new frontend's origin.

Email-code failures expose a stable top-level `code` alongside `message`.
Clients should use `EMAIL_LOGIN_EXPIRED` to restart the browser binding,
`EMAIL_CODE_EXPIRED` to request a replacement, and `EMAIL_CODE_RATE_LIMITED` with
`Retry-After` to delay another request. Invalid codes use `EMAIL_CODE_INVALID`;
configuration, invalid input, delivery, challenge lifecycle and proof failures
have their own codes. Do not infer recovery actions from HTTP 403 alone.

For browser regression checks, run `npm run test:auth-browser` in
`lemma-frontend`. That suite uses controlled API responses. For actual signup,
sign-in, verification mail, password reset, code exhaustion and resend, boot an
**isolated** local backend with Postgres, Redis and SuperTokens and set
`EMAIL_TRANSPORT=filesystem`, `EMAIL_OUTPUT_DIR` to a private inbox directory,
`AUTH_EMAIL_VERIFICATION_REQUIRED=true` and matching frontend origins. Disable
email domain checks for the suite's reserved example addresses. Point the real
frontend at that API with verification enabled, then run:

```bash
cd lemma-frontend
LEMMA_AUTH_QA_ORIGIN=http://localhost:13009 \
LEMMA_AUTH_QA_API=http://localhost:18719 \
LEMMA_AUTH_QA_MAILBOX=/private/tmp/lemma-auth-qa/emails \
LEMMA_TEST_BROWSER_CHANNEL=chrome npm run test:auth-live
```

The live suite refuses non-loopback hosts, consumes only filesystem test mail,
creates uniquely named test accounts, and closes its browsers. It does not start
or stop the stack; its launcher must trap exit and tear down all owned processes
and services. It skips when the three `LEMMA_AUTH_QA_*` variables are absent.
Keep the inbox private: it contains usable test codes and reset links. Live
provider consent and WhatsApp transport require their own credentials and test
installations; seeded provider/passwordless identities verify routing and account
reuse, not those external transports.

## Shared WhatsApp and Telegram

Configure the existing shared WhatsApp number/token and Telegram bot token.
Telegram polling starts one receiver for the configured shared bot even before
any personal surfaces exist. Customer-connected bots continue to use their
existing surfaces and access rules.

Telegram requests the sender's own contact and accepts no typed phone proof.
WhatsApp derives phone proof from the authenticated webhook sender. Both support
typed email and code replies. Telegram also presents contact and setup controls.

Chat signup ends on an emailed code, so it only runs where mail reaches an
inbox. On a deployment that cannot send mail — the filesystem spool counts as
none here, since nobody in a chat can read it — a stranger on a shared bot is
told the bot belongs to a private Lemma and to ask its owner, and the pending
signup ends as `refused` (logged as
`agent_surfaces.chat_onboarding.email_unavailable.observed`). They are neither
asked for a phone nor an address. Lemma Desktop is this case unless its owner
sets up email.

### Linking a Telegram chat from the app

A signed-in user can link a chat with the shared Telegram bot without email.
`GET /surfaces/me/telegram-link` names the bot (from `getMe`, cached in Redis)
and lists the pods a chat could answer from; `POST /surfaces/me/telegram-link`
with an optional `pod_id` returns a `https://t.me/<bot>?start=link_<token>` URL.
Both answer `409 TELEGRAM_SYSTEM_BOT_UNAVAILABLE` when no working shared bot is
configured, and a pod the user cannot attach a chat to is `403`. Lemma Desktop
offers this as "Chat with your agents on Telegram" under Settings → This Mac →
Server setup → Telegram, and a pod's Reach sheet opens a fresh link for the
shared bot instead of the bare handle.

The token is 32 random URL-safe characters held in Redis under its SHA-256 for
ten minutes, bound to the user and pod, and consumed with `GETDEL` — so a link
works once. When Telegram delivers `/start link_<token>` in a private chat, the
bot records the sender's identity with proof `link_token`, attaches the chosen
pod's default agent through the ordinary workspace step, and replays a bare
`/start`, which the agent answers with its greeting. The reply names the account
the chat is now linked to. A chat that is already linked can be relinked or moved
to another pod by pressing the button again; a Telegram identity that is live on
*another* Lemma account is refused rather than taken over. An expired or used
link is answered with where to get a fresh one.

Every verified identity records how it was proven: `phone`, `email` or
`link_token`. Only a phone proof is held to the account's current verified mobile
number, so a mobile-number change revokes phone-proven identities and leaves the
others alone.

WhatsApp can present two static Flows. From `lemma-backend/`, run
`uv run python scripts/publish_onboarding_flows.py` with `WHATSAPP_ACCESS_TOKEN`
and `WHATSAPP_WABA_ID` supplied through the operator environment. The publisher uploads and publishes the versioned email
and code definitions in `manifests/whatsapp/` and prints
the two configuration values. Configure both Flow IDs to enable native prompts;
typed replies remain available. The Flow token is opaque and bound to sender,
step and challenge. Test publishing and submission in the target WABA before
rolling out the native forms.

## Slack and Teams

Use the organization's existing installation. Slack needs permission to open a
DM and send messages, plus its normal interactivity callback. The setup button
opens a modal using a fresh interaction trigger. If the trigger expires, the
person can use the button again or reply privately with text.

Teams needs personal conversation support in the app manifest and tenant policy
that permits the bot to contact the person. Retain the authenticated Bot Framework
conversation reference. Email and code Adaptive Cards are delivered only in the
personal conversation. If proactive messaging is refused, the person can open
the personal bot chat and retry. Inspect existing webhook/worker failure
diagnostics for delivery errors; signup never falls back to a public channel.

A successful mailbox check creates or reuses an account. It does not bypass the
installation organization's membership policy. Invite-only organizations require
an administrator to add the person before a pod and personal route are created.
Other memberships do not change that destination. No credentials are copied to
the personal pod: execution carries the source installation separately from the
authorized target pod.

## Verification

Run the identity and agent-surfaces module e2e suites against Postgres, Redis and
unlicensed SuperTokens. Run the getting-started product scenarios through the
booted HTTP stack, and frontend auth tests for browser retry and redirect behavior.
Native-provider release verification additionally requires test installations for
Slack, Teams, WhatsApp and Telegram; provider fixtures do not prove tenant policy,
app permissions or published Flow acceptance.
