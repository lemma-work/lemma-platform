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

## Shared WhatsApp and Telegram

Configure the existing shared WhatsApp number/token and Telegram bot token.
Telegram polling starts one receiver for the configured shared bot even before
any personal surfaces exist. Customer-connected bots continue to use their
existing surfaces and access rules.

Telegram requests the sender's own contact and accepts no typed phone proof.
WhatsApp derives phone proof from the authenticated webhook sender. Both support
typed email and code replies. Telegram also presents contact and setup controls.

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
