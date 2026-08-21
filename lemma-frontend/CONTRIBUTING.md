# Contributing to Lemma Frontend

Thanks for your interest in improving Lemma. This guide covers how to get set up
and what we expect in a pull request.

## Getting set up

1. Make sure you have the [prerequisites](README.md#prerequisites): Node.js 24 (the version in `.nvmrc`, which `engines` also pins)
   and the `../lemma-typescript` SDK checked out (this package builds it
   automatically).
2. Install and run:

   ```bash
   npm install
   cp .env.example .env.local
   npm run dev
   ```

## Before you open a pull request

Run the full check suite and the tests:

```bash
npm run check   # design-system audit + ESLint + TypeScript + edu-anchor checks
npm test        # Vitest unit tests
```

CI runs `npm run check`, so a PR that fails it will not be merged. Please make
sure both pass locally first.

## Code style

- **TypeScript everywhere.** Avoid `any`; prefer precise types and `unknown` with
  narrowing when a value is genuinely dynamic. The few existing `any` usages are
  explicitly `eslint-disable`d with a reason.
- **Styling via Tailwind and design tokens.** Use `className` with the project's
  CSS variables/tokens. Inline `style` is linted against — keep it only for
  unavoidable runtime geometry.
- **Components are grouped by feature** under `components/<area>/`. Keep new code
  close to the feature it belongs to.
- **Comments explain _why_, not _what_.** Add a comment where the logic is
  non-obvious; don't narrate self-evident code.
- **No secrets, internal hostnames, or personal data in source.** Anything
  environment-specific belongs in configuration (see `.env.example`).

## Commit and PR guidelines

- Keep pull requests focused; unrelated cleanups are easier to review separately.
- Write a clear description of what changed and why.
- Include tests for new behavior where practical.

## Reporting issues

Open a GitHub issue with steps to reproduce, what you expected, and what
happened. For a suspected vulnerability, use the private advisory flow described
in [SECURITY.md](../SECURITY.md) rather than filing a public issue.
