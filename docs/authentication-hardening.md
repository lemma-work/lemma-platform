# Authentication hardening operations

This deployment uses only self-hosted SuperTokens recipes and other OSS components. Passwordless login and SuperTokens account linking are intentionally not enabled.

## Production configuration

Configure the backend with the existing SMTP account and the new authentication controls:

```dotenv
EMAIL_TRANSPORT=smtp
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=...
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=no-reply@example.com
SMTP_FROM_NAME=Lemma
SMTP_USE_TLS=true

# Alternatively, Resend needs only its API key. Lemma derives smtp.resend.com,
# port 465, username "resend", and implicit TLS automatically.
# Explicit SMTP_* credentials above take precedence when both are configured.
RESEND_API_KEY=...
RESEND_FROM_EMAIL=local@ops.asur.work
RESEND_WEBHOOK_SECRET=whsec_...

AUTH_EMAIL_VERIFICATION_REQUIRED=true
AUTH_EMAIL_DELIVERABILITY_CHECKS_ENABLED=true
AUTH_DISPOSABLE_EMAIL_DOMAINS_ENABLED=true
AUTH_DISPOSABLE_EMAIL_ALLOWLIST=[]
AUTH_ABUSE_PROTECTION_ENABLED=true
AUTH_TRUSTED_PROXY_IPS=["10.0.0.10"]

# Enable after the frontend and Redis-backed challenge path are deployed.
AUTH_ALTCHA_ENABLED=true
AUTH_ALTCHA_HMAC_KEY=...
AUTH_ALTCHA_MAX_NUMBER=100000

# Set only when the SMTP provider adapter can deliver signed events.
AUTH_BOUNCE_WEBHOOK_SECRET=...

TELEGRAM_OIDC_CLIENT_ID=...
TELEGRAM_OIDC_CLIENT_SECRET=...
TELEGRAM_OIDC_REDIRECT_URI=https://api.example.com/auth/telegram/callback
```

For `lemma-stack`, each value can be set with `lemma-stack config set KEY value`; backend environment overrides are passed through unchanged. The local stack explicitly defaults `AUTH_EMAIL_VERIFICATION_REQUIRED` and its frontend counterpart to `false`, because local users normally have no SMTP account. Production keeps the backend and frontend values enabled.

Only list the immediate reverse proxies in `AUTH_TRUSTED_PROXY_IPS`. Requests from every other peer ignore `Forwarded` and `X-Forwarded-For`.

## Deployment sequence

1. Apply migration `0007_auth_hardening` and deploy with ALTCHA and Telegram disabled.
2. Confirm SMTP TLS, aligned From domain, SPF, DKIM, DMARC, and provider bounce handling. Send verification and password-reset messages to real test mailboxes.
3. Run the reconciliation dry-run and review count-only output:

   ```bash
   cd lemma-backend
   uv run python scripts/reconcile_auth_users.py
   ```

4. Apply the same reconciliation. This preserves SuperTokens-verified users, marks other email/password users unverified, revokes their sessions, and deactivates only deterministic invalid-address evidence:

   ```bash
   uv run python scripts/reconcile_auth_users.py --apply
   ```

5. Enable abuse protection and ALTCHA, then monitor SMTP failures, hard-bounce deactivations, `429` responses, and verification completion.
6. Register the global Telegram OIDC client in BotFather, configure its exact callback URI, enable the three Telegram settings, and perform iOS/Android device acceptance testing before exposing the button.

The reconciliation command is idempotent and never prints complete addresses. An `email_conflict` or `duplicate_emailpassword_identity` count must be investigated before using `--apply` for those records; conflicted records are skipped.

## Bounce adapter contract

For Resend, configure a webhook for `POST /auth/email/bounces/resend` and select
the `email.bounced` event. The endpoint verifies the original request body with
the `svix-id`, `svix-timestamp`, and `svix-signature` headers using
`RESEND_WEBHOOK_SECRET`. Only a `Permanent` bounce deactivates an account;
`Temporary` bounces are accepted without deactivation.

For another SMTP provider, use the normalized adapter below.

POST normalized provider events to `/auth/email/bounces` using this JSON body:

```json
{
  "email": "person@example.com",
  "event": "hard_bounce"
}
```

Set `X-Lemma-Timestamp` to the current Unix timestamp and `X-Lemma-Signature` to `sha256=<hex>`, where the hex value is `HMAC-SHA256(secret, timestamp + "." + exact_request_body)`. Timestamps outside five minutes and altered bodies are rejected. `soft_bounce` is accepted but never deactivates an account; only `hard_bounce` does.

Hard bounces deactivate the unique matching active Lemma user and revoke its sessions. Repeated provider deliveries are idempotent because an inactive user needs no further update. Soft bounces and events for unknown addresses do not deactivate anyone. The user row is the single source of truth for delivery eligibility; there is no separate email-suppression table.

If the SMTP provider cannot produce authenticated hard-bounce events, leave `AUTH_BOUNCE_WEBHOOK_SECRET` unset. Invalid syntax, nonexistent domains, and explicit null MX records are still rejected before a user is created.

## Disposable-domain snapshot

The bundled list at `lemma-backend/app/modules/identity/data/disposable_email_domains.txt` is a reviewed, pinned subset of the MIT-licensed [disposable/disposable-email-domains](https://github.com/disposable/disposable-email-domains) list. Updates must be reviewed and committed; production never fetches a remote list at request time. Add known false positives to `AUTH_DISPOSABLE_EMAIL_ALLOWLIST` before rollout.
