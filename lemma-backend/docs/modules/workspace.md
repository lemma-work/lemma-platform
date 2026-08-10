# Workspace module

**Target status:** the sandbox runtime proposed; not yet implemented

The workspace module will own user authorization, conversation/session naming,
short-lived workspace credentials, and translation of agent tools to the sandbox runtime. It
will not own provider lifecycle or provider SDKs.

The canonical target design is:

- [Sandbox design overview](../../../docs/design/agentbox/README.md)
- [Sandbox protocol](../../../docs/design/agentbox/sandbox-protocol.md)
- [Provider adapters](../../../docs/design/agentbox/provider-adapters.md)
- [Testing strategy](../../../docs/design/agentbox/testing-strategy.md)
- [Verification and rollout](../../../docs/design/agentbox/verification-and-rollout.md)

Do not add lifecycle, retry, provider, persistence, or execution protocol rules to
this module document. They belong in the canonical design set.

The current backend code still uses the experimental the sandbox runtime API until the coordinated
breaking migration described in the rollout document.
