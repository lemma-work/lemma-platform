# Decisions module

Closed-set judgements asked about one piece of state: *which of these*, *yes or
no*, *how much on this scale*. A decision sits between a rule, which is free
and understands nothing, and an agent run, which understands everything and
always acts. The design, and what is built on it, is in
[Decisions](../../../docs/design/decisions.md).

## What it owns

- **Questions and deciders.** A question is `choice`, `multi_choice`, `yes_no`
  or `scale`, with options declared or passed per call. A decider is a named,
  versioned definition: questions, guidance, an input view, rules and a
  policy. Pod deciders are `ResourceType.DECIDER` resources with the
  `decider.read|create|update|delete|execute` permissions. System deciders ship
  in `services/system_deciders.py` and are asked as `system:<name>`.
- **The ladder.** Rules (`infrastructure/rules_engine.py`), then Typesafe
  System One when `TYPESAFE_API_KEY` is set (`infrastructure/typesafe_engine.py`),
  then the system model (`infrastructure/model_engine.py`). Each rung answers or
  abstains; `services/ladder.py` climbs them and applies the decider's policy.
  System One sits behind a per-second budget shared across processes, split by
  lane (`infrastructure/limiter.py`).
- **Decisions.** Recorded once per (pod, decider, subject), never recomputed.
  Each keeps its answers, which rung gave them, the question shape it was asked
  with, a trace of every rung, and its evidence (the rendered input view) until
  `DECISION_EVIDENCE_TTL_DAYS` passes; an hourly sweep drops expired evidence.
- **Examples.** A person's answer to a decision -- resolving an open question
  or correcting a machine's -- becomes an example for that decider in that pod,
  and later asks carry the most recent ones. An agent's answer is recorded as
  the agent's and never becomes an example.

Tables: `deciders`, `decider_versions`, `decisions`, `decision_examples`.

## What it does not own

What happens because of a decision. Schedules, workflows, agent tools and
channels ask through `contracts/decide.py` and act on the answer themselves.
Deciding who may ask is core authorization's; this module declares the
resource type and its table.

## Settings

`TYPESAFE_API_KEY`, `TYPESAFE_MODEL` (a version id, never `jev-latest`),
`TYPESAFE_BASE_URL`, `TYPESAFE_TIMEOUT_SECONDS`,
`TYPESAFE_INTERACTIVE_TIMEOUT_SECONDS`, `TYPESAFE_REQUESTS_PER_SECOND`,
`DECISIONS_SYSTEM_ONE_ENABLED`, `DECISION_MODEL`,
`DECISION_EVIDENCE_TTL_DAYS`, `DECISIONS_MAX_ROWS`,
`DECISIONS_ROW_CONCURRENCY`. Each is described in `config.py`.
