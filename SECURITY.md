# Security policy

## Supported versions

Security fixes are made on `main` and included in the next release. The latest
published major version receives fixes; older majors may require an upgrade.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability or include credentials,
personal data, exploit code, or private deployment details in a public report.

Use GitHub's **Report a vulnerability** flow on this repository to create a
private security advisory. Include the affected version or commit, impact,
reproduction steps, and any suggested mitigation. If private vulnerability
reporting is unavailable, contact a repository owner through their verified
GitHub profile and ask for a private reporting channel without sending the
sensitive details yet.

We target acknowledgement within three business days, initial severity and
scope assessment within seven business days, and regular updates until a fix or
documented mitigation is available. Please allow a coordinated disclosure
window before publishing details.

## Handling expectations

- Never commit real credentials, tokens, customer payloads, or production dumps.
- Revoke exposed credentials immediately; deleting them from Git history is not
  sufficient.
- Security fixes need regression tests that use canary values and verify API
  responses, logs, traces, queued payloads, and generated clients where relevant.
- High and critical scanner findings block release unless maintainers record a
  time-bounded exception with an owner, rationale, compensating control, and
  expiry date.

See the [threat model](docs/security/threat-model.md) for trust boundaries and
the [release checklist](docs/security/release-checklist.md) for release gates.
