# Getting help

## Where to go

| You want to… | Go here |
|---|---|
| Understand a feature or setting | [Documentation](docs/README.md) · [lemma.work/docs](https://lemma.work/docs) |
| Ask "how do I…", or discuss an idea | [Discussions](https://github.com/lemma-work/lemma-platform/discussions) |
| Report something broken | [Open an issue](https://github.com/lemma-work/lemma-platform/issues/new/choose) |
| Report a vulnerability | [Private advisory](https://github.com/lemma-work/lemma-platform/security/advisories/new) — **never a public issue**. See [SECURITY.md](SECURITY.md) |
| Contribute a change | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Before you ask

Most reports get answered faster with three things:

1. **Version** — Desktop → **Local Control Center → Diagnostics**, or
   `lemma --version`, or the commit SHA if you are running from a checkout.
2. **How you're running it** — Desktop (Local), Lemma Cloud, `make dev` from a
   checkout, or your own deployment. The answer often differs.
3. **The actual error** — the log line or stack trace, not a paraphrase.

Diagnostics that usually reveal the problem:

```bash
lemma-stack doctor          # managed local install: health of every component
lemma-stack logs backend -f # follow backend logs
```

From a checkout, `make logs` tails the infrastructure containers.

## Redact before you paste

Logs and config dumps carry API keys, session tokens, connector credentials, and
email addresses. Strip them before pasting into a public issue or discussion.
If you have already exposed a credential, **revoke it** — deleting the comment
is not sufficient.

## Response expectations

Lemma is built by [Folks and Machines, Inc.](https://lemma.work) and maintained
in the open. Issues and discussions are answered on a best-effort basis; there
is no support SLA for the open-source project.

Security reports are the exception and have committed timelines — see
[SECURITY.md](SECURITY.md).

Commercial support and licensing exceptions are available from Folks and
Machines, Inc.
