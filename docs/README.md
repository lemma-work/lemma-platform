# Lemma documentation

Start here. Everything in this directory is written for someone running,
operating, or extending Lemma — not for someone browsing the product. For that,
read the [README](../README.md) or visit [lemma.work](https://lemma.work).

## Install and run

| Document | What it answers |
|---|---|
| [Installation](installation.md) | Install Lemma Desktop on macOS or Windows, first start, URLs and ports, diagnostics, updates, uninstall |
| [Configuration](configuration.md) | What every operator-facing setting decides and why you would change it |
| [Observability](observability.md) | Exporting traces, metrics, and logs to any OTLP collector; the local HyperDX + Phoenix stack |
| [Authentication hardening](authentication-hardening.md) | Production email, verification, abuse protection, bounce handling, Telegram and WhatsApp verification |
| [Running the guest VM by hand](local-runtime-vm.md) | Booting Desktop's Linux guest directly, for debugging the `lemma_local` provider |

## What the product does

| Document | What it answers |
|---|---|
| [Product specification](product/README.md) | What Lemma promises a user, journey by journey, and which test proves each promise |
| [Scenario coverage](product/coverage.md) | Every promise and the scenarios covering it — generated, not edited |

## Architecture

| Document | What it covers |
|---|---|
| [Platform architecture](../ARCHITECTURE.md) | The map: components, state, how work moves, the invariants |
| [Sandbox fabric](architecture/sandbox/README.md) | The provider-neutral sandbox model, and the doc set below it |
| [Desktop architecture](architecture/desktop.md) | Process ownership, lifecycle protocol, ports, and local state |
| [Agent Host](architecture/agent-host.md) | Running local coding agents against a pod, and how Desktop supervises them |
| [Agent memory](architecture/agent-memory.md) | Where an agent's durable facts live, what is loaded into every prompt, and what bounds it |
| [Database connection scope](design/db-connection-scope.md) | How long a pooled connection is held, the gates that keep it short, and what authorization costs |
| [App and function versions](design/app-function-versioning.md) | Revision identity, previews, rollback, bounded retention, and concurrent cleanup |
| [Product analytics](design/product-analytics.md) | The product-analytics plane, its event contract, origins, and the privacy boundary |

The sandbox set breaks down further:
[protocol](architecture/sandbox/sandbox-protocol.md) ·
[lifecycle state model](architecture/sandbox/lifecycle-state-model.md) ·
[provider adapters](architecture/sandbox/provider-adapters.md) ·
[function execution](architecture/sandbox/function-execution.md) ·
[testing strategy](architecture/sandbox/testing-strategy.md) ·
[verification and rollout](architecture/sandbox/verification-and-rollout.md)

## Security

| Document | What it covers |
|---|---|
| [Threat model](security/threat-model.md) | Assets, trust boundaries, primary threats and their mitigations, residual risk |
| [Release checklist](security/release-checklist.md) | The gates a backend release has to clear |
| [Generated-code policy](security/generated-code-policy.md) | Rules for the OpenAPI-generated SDKs and bundles |

To report a vulnerability, follow [SECURITY.md](../SECURITY.md) — not a public
issue.

## Operations

| Document | What it covers |
|---|---|
| [Sandbox function benchmark](operators/sandbox-function-benchmark.md) | The repeatable full-path quality gate for function execution |
| [Datastore ingestion benchmark](../lemma-backend/docs/operators/datastore-ingestion-benchmark.md) | The real upload → extract → index pipeline under load |
| [Object storage](../lemma-backend/docs/operators/object-storage.md) | Local, S3, GCS, and Azure backends |
| [Reliability](../lemma-backend/docs/operators/reliability.md) | Replay, dead-letter, and SLO guidance for the event path |

## Contributing

| Document | What it covers |
|---|---|
| [Contributing](../CONTRIBUTING.md) | Setup, architecture rules, and what a pull request needs |
| [Working in this repository](../AGENTS.md) | A map of the components and the rules broken most often |
| [Engineering standards](engineering/README.md) | The rules the code is held to, each with its enforcing check |
| [Design and abstraction](engineering/design.md) | Module boundaries, ports and adapters, services, shapes, events |
| [Types and data shapes](engineering/types.md) | Typed interfaces, `Any`, named shapes, the type-checker path |
| [Test design](engineering/tests.md) | What a good test looks like in any of the three suites |
| [Agent context](../CLAUDE.md) | Loaded automatically by coding agents; carries the one rule that is wrong by default and points here |
| [Testing strategy](testing.md) | The three suites, which one a change needs, and what gates what |
| [Product scenario suite](../tests/scenarios/README.md) | The black-box suite that proves the product specification, and how to add to it |
| [Backend module guide](../lemma-backend/docs/modules/README.md) | One document per backend module, and the tables each owns |
| [Module contracts](../lemma-backend/docs/contracts/README.md) | What every API operation and product event guarantees |
| [Backend development guidelines](../lemma-backend/docs/development.md) | DB sessions, events, errors — each rule with the canonical example to copy |
| [API and SDK versioning](versioning.md) | The shared compatibility line across the API and both SDKs |
| [Code of conduct](../CODE_OF_CONDUCT.md) | Expected behavior and how to report a concern |

## Component documentation

Each component keeps its own README next to the code:

[lemma-backend](../lemma-backend/README.md) ·
[lemma-frontend](../lemma-frontend/README.md) ·
[lemma-cli](../lemma-cli/README.md) ·
[lemma-python](../lemma-python/README.md) ·
[lemma-typescript](../lemma-typescript/README.md) ·
[lemma-skills](../lemma-skills/README.md) ·
[lemma-stack](../lemma-stack/README.md) ·
[lemma-pod-bundle](../lemma-pod-bundle/README.md) ·
[desktop](../desktop/README.md)

Release notes live on the
[Releases page](https://github.com/lemma-work/lemma-platform/releases).
