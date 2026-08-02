# Pod skill contract

Read this reference before creating, publishing, or editing a skill under `/skills`.

## Namespace and ownership

Treat `/skills` as a synthetic pod folder that merges two sources:

- Bundled system skill directories come from Lemma's installed `lemma-skills` bundle. They are readable in the pod and carry read-only metadata.
- Custom skill directories are ordinary pod-shared files created under `/skills` by a caller with the required write permission.

The system entry wins when paths collide. Lemma rejects writes, moves, and deletes at `/skills` itself and anywhere inside an existing bundled skill directory with `System skills are read-only`. Do not use a system skill's name for a custom skill, and do not attempt to shadow one.

Inspect before writing:

```bash
lemma files tree /skills
lemma files stat /skills/<candidate-name>
```

Run `stat` only when the candidate appears in the tree. If its metadata says it is read-only, stop. If it is pod-owned but the user did not authorize changing it, stop.

## Discovery contract

Make every discoverable skill a direct child of `/skills` with this exact entry file:

```text
/skills/<name>/SKILL.md
```

Use uppercase `SKILL.md`. A directory with only `README.md`, a differently cased filename, unreadable bytes, or invalid frontmatter is not a usable skill and may be omitted from the catalog without a validation error being shown to the agent.

Use this minimal layout:

```text
<name>/
├── SKILL.md
├── references/       # optional UTF-8 guidance loaded on demand
├── scripts/          # optional UTF-8 reusable source
└── assets/           # optional UTF-8 templates
```

The loader recursively lists files below the skill directory, excluding `SKILL.md` and `__pycache__`. It classifies anything under `scripts/` as a script and marks only `.sh` paths as executable metadata. Resource loading is text-based, so keep resources that must be loaded through `load_skill` valid UTF-8. Do not assume that executable metadata runs a script automatically.

Do not create `agents/openai.yaml` merely for Lemma discovery. Lemma treats it as another resource rather than skill UI metadata. Include and maintain it only when the same skill is deliberately packaged for an external agent that consumes it; bundled Lemma skills installed into external coding agents are one such case.

## Frontmatter parser

Start the file exactly with an LF-delimited frontmatter block. Do not place a byte-order mark, blank line, heading, or comment before it.

```markdown
---
name: vendor-contract-review
description: "Review vendor contracts against approved commercial and legal policies. Use for uploaded agreements, redlines, renewal reviews, and clause-risk summaries; do not use for general legal research."
---

# Vendor Contract Review
```

Follow all parser constraints:

- Include only `name` and `description` as top-level, single-line scalar fields.
- Use lowercase letters, digits, and single hyphens in `name`.
- Keep `name` between 1 and 64 characters; do not start or end with a hyphen or use `--`.
- Match `name` exactly to the containing directory.
- Keep `description` non-empty and on one line. Quote it when punctuation such as a colon could be ambiguous.
- Close the block with a line containing `---`.

The parser is intentionally simple: it ignores indented YAML and does not resolve multiline scalars, lists, aliases, or nested mappings. Do not rely on general YAML features even if another skill platform accepts them.

## Safe external CLI workflow

Stage the complete skill in a local directory first. Use an editor or patch tool for multiline content; avoid shell-quoted inline documents.

For a new, non-colliding skill:

```bash
lemma files tree /skills
lemma files mkdir /skills/<name>
lemma files write /skills/<name>/SKILL.md --from ./<name>/SKILL.md --no-search
lemma files mkdir /skills/<name>/references
lemma files write /skills/<name>/references/<file>.md --from ./<name>/references/<file>.md --no-search
lemma files tree /skills/<name>
lemma files cat /skills/<name>/SKILL.md
```

Create only the resource directories the draft actually uses. Repeat `mkdir` and `write --from` for each required text resource. Use `lemma files upload <local-file> <remote-path> --no-search` only for a new non-text file that cannot be written as text; remember that the skill loader cannot return binary content as text.

For an existing pod-owned skill, inspect and back up each file before replacing it:

```bash
lemma files stat /skills/<name>
lemma files tree /skills/<name>
lemma files download /skills/<name>/SKILL.md ./<name>-previous-SKILL.md
lemma files write /skills/<name>/SKILL.md --from ./<name>/SKILL.md --no-search
lemma files cat /skills/<name>/SKILL.md
```

Download every resource that will change. Treat `write` as an upsert: after the ownership preflight it is suitable for creating or replacing an exact text path. Do not use `append` to revise structured instructions. Do not use `rm` as synchronization; deletion is a separate destructive action.

Add `--pod <pod>` to file commands when the shell is not already bound to the intended pod. In Lemma's local harness or MCP-routed environment, run these CLI examples through `lemma_exec_command` so the current workspace identity and pod are injected.

Do not confuse pod publication with `lemma skills install`. The latter copies Lemma's bundled skills into supported external coding-agent directories and may replace an installed copy; it does not publish a custom directory into a pod's `/skills` catalog.

## Runtime proof

After writing the files, validate through the same surface an agent uses:

1. Call `list_skills` and find the exact name and description.
2. Call `load_skill` with the name and inspect the returned `SKILL.md` plus resource list.
3. Call `load_skill` for each mandatory relative resource path. Never pass an absolute path or `..`.
4. Start a fresh agent turn for trigger checks so the skill is selected from its description rather than leaked authoring context.

Use a compact smoke matrix:

| Case | Prompt shape | Evidence |
|---|---|---|
| Positive trigger | Natural request that clearly needs the capability | Skill loads; required workflow and artifact appear |
| Paraphrased trigger | Same intent with different vocabulary | Skill still loads without naming it |
| Near miss | Adjacent task owned by another skill or base guidance | Skill stays unloaded or yields to the better match |
| Resource | Task requires one direct reference or script | Agent loads the expected relative resource |
| Safety boundary | Task approaches a forbidden write or external action | Agent stops, narrows scope, or requests authority |

Judge tool traces, file diffs, records, or produced artifacts. Do not count an explanation of intended behavior as successful execution. Escalate to `lemma-evals` instead of expanding this smoke matrix into a permanent benchmark.
