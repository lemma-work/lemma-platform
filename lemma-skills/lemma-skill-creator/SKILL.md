---
name: lemma-skill-creator
description: "Create, revise, publish, and smoke-test pod-owned Lemma agent skills under /skills/skill-name. Use when defining a skill's triggers and instructions, organizing its references or scripts, safely updating an existing custom skill, publishing one through Lemma files, or checking that an agent discovers and follows it. Do not use to modify bundled read-only system skills or to build a full evaluation program."
---

# Lemma Skill Creator

Create the smallest pod-owned skill that changes agent behavior reliably. Treat the skill as shared executable guidance: make its trigger precise, keep its body economical, and prove its behavior in a fresh agent context.

## Respect the skill boundary

- Treat `/skills` as a merged catalog of bundled system skills and pod-owned custom skills.
- Inspect the catalog before choosing a name. Never assume a directory is writable from its path alone.
- Never modify, move, delete, or add files inside a bundled skill directory. The whole system-skill subtree is read-only.
- Create or edit only a distinct pod-owned directory whose scope the user authorized. If a requested name collides with a system skill, choose a new name with the user.
- Stage and review changes locally before writing shared pod state. Back up every existing file that will be replaced.
- Read [the pod skill contract](references/pod-skill-contract.md) before publishing or editing a skill in a pod. Follow its parser rules and exact CLI workflow.

## Build the skill

### 1. Define observable behavior

Write down:

- two or three requests that must trigger the skill;
- one adjacent request that must not trigger it;
- the expected deliverable or state change;
- the tools, pod resources, and permissions required;
- any irreversible or externally visible action that requires confirmation.

Resolve unclear behavior before authoring. Do not hide product decisions inside procedural instructions.

### 2. Inspect the existing universe

List available skills and read the closest overlaps before drafting. In a Lemma agent, use `list_skills`, then `load_skill` for relevant candidates. From an external shell, inspect `/skills` with the Lemma file commands in the contract reference.

Extend an existing pod-owned skill when the behavior belongs to the same trigger and workflow. Create a new skill when it has a distinct trigger, outcome, or safety boundary. Avoid two descriptions that compete for the same request.

### 3. Choose the name and layout

Use a short lowercase hyphenated name that describes the capability. Make the directory name and frontmatter `name` identical.

Always create `SKILL.md`. Add only resources that reduce repeated work:

- Put detailed, conditional knowledge in `references/`.
- Put deterministic, reusable source code in `scripts/`; state its inputs, outputs, and safe invocation in `SKILL.md` or a direct reference.
- Put only UTF-8 text templates in `assets/` when an agent must load them through the skill tool. Keep binary artifacts elsewhere in pod files and reference their paths.
- Keep every resource one link away from `SKILL.md`; avoid reference chains.

Do not add a README, changelog, or installation guide. Do not add `agents/openai.yaml` to a pod-owned skill merely for Lemma discovery; maintain it only when the skill is also packaged for an external agent that consumes that UI metadata.

### 4. Design the trigger

Put all discovery language in the one-line frontmatter `description`; the body is unavailable until the skill is loaded. Include:

1. the capability and outcome;
2. concrete objects, files, or situations that should trigger it;
3. the important boundary with its closest neighboring skill.

Prefer specific verbs and nouns over broad claims. Do not use descriptions such as “helps with research” or “use for many workflows.” Keep the description broad enough to catch natural user phrasing but narrow enough to avoid unrelated tasks.

### 5. Write imperative instructions

Order the body around the actual work:

1. state non-negotiable constraints;
2. give the shortest successful workflow;
3. define decision points and failure handling;
4. link conditional detail directly;
5. require proportionate verification.

Assume the agent can reason. Include only Lemma-specific contracts, fragile sequences, domain knowledge, and reusable patterns it cannot safely infer. Use exact commands only after confirming they exist. Never embed secrets, live credentials, personal data, or production-only identifiers.

Match instruction freedom to risk: use judgment for creative choices, a preferred pattern for repeatable work, and exact steps for destructive or contract-sensitive operations.

## Validate before publishing

Check the staged directory:

- `SKILL.md` is UTF-8, starts with `---` on the first byte, and closes the frontmatter.
- Frontmatter contains only top-level, one-line `name` and `description` fields.
- The name is valid, unique in `/skills`, and exactly matches the directory.
- Every linked resource exists, stays inside the skill directory, and is required by the workflow.
- Instructions contain no placeholders, stale paths, invented commands, hidden prerequisites, or contradictory safety rules.
- Examples use synthetic or explicitly approved data.

Publish through the exact-path file workflow in the contract reference. Do not bulk-delete old resources merely because they are absent from the staged draft; remove them only when the user authorized that cleanup.

## Run a lightweight behavior check

After publishing:

1. Confirm the skill appears in `list_skills`; an invalid custom directory may simply be omitted.
2. Load it and confirm its body and expected resources are readable.
3. In a fresh agent turn, run one clear positive trigger without naming the skill.
4. Run one near-miss prompt and confirm the skill is not selected unnecessarily.
5. Exercise one mandatory resource and one important safety boundary.
6. Compare the observed actions and artifact with the stated behavior; do not accept the agent's self-report as evidence.

Keep this check to a few representative cases. Use `lemma-evals` when the user needs a repeatable suite, variants, scoring, latency or cost tracking, or release-gating.

## Revise safely

Inspect and download the current pod-owned files before editing. Change one behavior hypothesis at a time, republish only the affected files, then repeat discovery, loading, and the relevant smoke cases. Preserve useful resources and user-authored changes outside the requested scope.

If the desired change targets a bundled system skill, stop. Propose either a distinctly named pod-owned skill or a separate reviewed change to Lemma's source bundle; do not try to bypass the read-only overlay.
