# lemma-skills

The skills Lemma installs into your coding agent so it can design, build, and
operate pods.

A **skill** is a directory with a `SKILL.md` — YAML frontmatter naming it and
describing when to use it, then instructions the agent loads on demand. Heavier
material lives in `references/` and is read only when the task needs it, so a
skill costs almost nothing until it fires.

These are the same skills that ship inside `lemma-terminal`. Install them with:

```bash
lemma skills install
```

That auto-detects Claude Code, Codex, OpenCode, and Cursor. See the
[CLI README](../lemma-cli/README.md#install-lemma-skills-into-your-coding-agent) for targets, scopes, and where each
tool expects skills to live. Restart your coding agent afterwards.

## What's here

Everything named `lemma-*` is a product skill and installs by default.

| Skill | Use it for |
|---|---|
| [lemma-builder](lemma-builder/SKILL.md) | Designing and building a complete pod — tables, files, functions, agents, workflows, schedules, connectors, surfaces, apps — then importing and verifying it |
| [lemma-user](lemma-user/SKILL.md) | Operating a pod that already exists: querying records under RLS, running functions and workflows, chatting with agents |
| [lemma-app-design](lemma-app-design/SKILL.md) | Designing and visually refining a pod app into something production-quality |
| [lemma-app-qa](lemma-app-qa/SKILL.md) | Testing a pod app through real end-to-end journeys, authenticated, local or deployed |
| [lemma-widget](lemma-widget/SKILL.md) | Building lightweight inline widgets for conversations |
| [lemma-artifact-author](lemma-artifact-author/SKILL.md) | Producing durable deliverables — Markdown, HTML, DOCX, PDF, XLSX, PPTX |
| [lemma-data-analysis](lemma-data-analysis/SKILL.md) | Analyzing pod data: schemas, quality, KPIs, read-only queries |
| [lemma-research](lemma-research/SKILL.md) | Source-backed investigations inside a pod |
| [lemma-evals](lemma-evals/SKILL.md) | Repeatable evaluations for agents, functions, and workflows |
| [lemma-skill-creator](lemma-skill-creator/SKILL.md) | Authoring pod-owned skills under `/skills/` |

Two environment helpers install only with `--all-skills`, because they depend on
what the surrounding environment can actually do:

| Skill | Use it for |
|---|---|
| [browser](browser/SKILL.md) | Driving a real browser in a Lemma workspace — navigation, screenshots, login flows, scraping |
| [liteparse-documents](liteparse-documents/SKILL.md) | Parsing documents that live *outside* the pod's file system |

## Choosing between builder and user

The two most-used skills draw a deliberate line:

- **lemma-builder** changes a pod's *structure*. Creating tables, defining
  agents, wiring workflows, deploying apps.
- **lemma-user** changes a pod's *contents*. Reading and writing records,
  running things, answering questions.

Each skill's description tells the agent to hand off to the other rather than
overreach.

## Writing or changing a skill

`SKILL.md` frontmatter takes exactly two fields:

```yaml
---
name: lemma-example
description: "What it does. Use when <trigger>. Do not use for <boundary>; use <other-skill> instead."
---
```

The `description` is the *only* thing an agent sees before deciding to load the
skill, so it has to carry the trigger and the boundary — not just the topic. The
"Do not use for…" clause matters as much as the affirmative one.

Keep `SKILL.md` short enough to read in full, and push depth into
`references/`. `lemma-builder` is the reference example: a 149-line `SKILL.md`
over nineteen reference documents.

To author skills that live inside a pod rather than shipping here, use
[lemma-skill-creator](lemma-skill-creator/SKILL.md).

## License

Apache-2.0. See [LICENSE](LICENSE).
