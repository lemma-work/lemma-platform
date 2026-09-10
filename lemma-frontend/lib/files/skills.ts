/**
 * Skills are pod docs — files under `/skills`, in the same tree, read by the
 * same file service. What sets them apart is a contract: a folder named for the
 * skill, a `SKILL.md` inside it, and frontmatter carrying `name` and
 * `description`. The rules here mirror the backend's `skill_loader.py` so the
 * frontend never blesses a skill the agent runtime would then refuse to load.
 */

import { buildFrontmatter, splitFrontmatter } from './frontmatter';

export const SKILLS_ROOT = '/skills';
export const SKILL_MANIFEST_NAME = 'SKILL.md';

const NAME_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

function normalizePath(path: string | null | undefined): string {
    if (!path) return '';
    const cleaned = path.replace(/\\/g, '/').replace(/\/+$/g, '');
    if (!cleaned) return '/';
    return cleaned.startsWith('/') ? cleaned : `/${cleaned}`;
}

function segmentsUnderSkillsRoot(path: string | null | undefined): string[] | null {
    const normalized = normalizePath(path);
    if (normalized === SKILLS_ROOT) return [];
    if (!normalized.startsWith(`${SKILLS_ROOT}/`)) return null;
    return normalized.slice(SKILLS_ROOT.length + 1).split('/').filter(Boolean);
}

/** The skills folder itself — the shelf, not a skill on it. */
export function isSkillsRootPath(path: string | null | undefined): boolean {
    return normalizePath(path) === SKILLS_ROOT;
}

/** Anywhere inside the skills tree, root included. */
export function isSkillsPath(path: string | null | undefined): boolean {
    return segmentsUnderSkillsRoot(path) !== null;
}

export function skillNameFromPath(path: string | null | undefined): string | null {
    const segments = segmentsUnderSkillsRoot(path);
    if (!segments || segments.length === 0) return null;
    return segments[0];
}

/** `/skills/<name>` — a skill's own folder, not a folder within it. */
export function isSkillFolderPath(path: string | null | undefined): boolean {
    const segments = segmentsUnderSkillsRoot(path);
    return segments !== null && segments.length === 1;
}

/** `/skills/<name>/SKILL.md` — the one file that carries the contract. */
export function isSkillManifestPath(path: string | null | undefined): boolean {
    const segments = segmentsUnderSkillsRoot(path);
    return segments !== null && segments.length === 2 && segments[1] === SKILL_MANIFEST_NAME;
}

export function skillFolderPath(name: string): string {
    return `${SKILLS_ROOT}/${name}`;
}

export function skillManifestPath(name: string): string {
    return `${skillFolderPath(name)}/${SKILL_MANIFEST_NAME}`;
}

/** The error to show, or null when the name is one the runtime will accept. */
export function validateSkillName(name: string): string | null {
    const trimmed = name.trim();
    if (!trimmed) return 'Name the skill first';
    if (trimmed.includes('--')) return 'Use one hyphen between words';
    if (!NAME_PATTERN.test(trimmed)) {
        return 'Lowercase letters, numbers, and hyphens — starting and ending with a letter or number';
    }
    return null;
}

/** `Weekly Report` → `weekly-report`, so typing a title still lands a valid name. */
export function suggestSkillName(input: string): string {
    return input
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/-+/g, '-')
        .replace(/^-|-$/g, '')
        .slice(0, 64)
        .replace(/-$/, '');
}

export type SkillManifest = {
    name: string;
    description: string;
    /**
     * Why the runtime would refuse this skill, or null when it loads. The
     * folder name is authoritative: `skill_loader` requires the frontmatter
     * name to match the directory it sits in.
     */
    problem: string | null;
};

export function readSkillManifest(content: string, folderName: string): SkillManifest {
    const { raw, fields } = splitFrontmatter(content);
    const name = (fields.name || '').trim();
    const description = (fields.description || '').trim();

    const problem = (() => {
        if (!raw) return 'No frontmatter — this skill will not load';
        if (!name) return 'Missing a name in the frontmatter';
        if (!description) return 'Missing a description in the frontmatter';
        if (validateSkillName(name)) return validateSkillName(name);
        if (name !== folderName) return `Named "${name}" but stored in "${folderName}" — these must match`;
        return null;
    })();

    return { name: name || folderName, description, problem };
}

export function buildSkillScaffold(name: string, description: string): string {
    const frontmatter = buildFrontmatter({ name, description });
    const title = name.replace(/-/g, ' ').replace(/^./, (character) => character.toUpperCase());

    return `${frontmatter}

# ${title}

${description}

## When to use this

Describe the situations where an agent should reach for this skill, and the
ones where it should not.

## How to do it

Walk through the steps. Be specific about which pod resources to touch — tables,
files, connectors — and what a finished result looks like.
`;
}

export type SkillDescriptionParts = {
    /** What the skill does — everything before it starts naming triggers. */
    summary: string;
    /** When an agent should load it, verbatim: the `Use when …` clause. */
    trigger: string;
};

/**
 * A skill description is written for a model, and it has a shape: what the
 * skill does, then when to load it, then which neighbouring skill to use
 * instead. Rendered as one paragraph it is a wall of text nobody scans — which
 * is the whole reason a shelf of skills reads as a folder of documents.
 *
 * The openers below are anchored to a sentence start, so `DESIGN.md` mid-clause
 * cannot be mistaken for one. Nothing here is required: a description with no
 * `Use when` is simply all summary, and the card renders one block.
 */
const TRIGGER_OPENER = /(?:^|[.!?;]\s+)(Use\s+(?:it\s+|this\s+)?(?:when|for|to)\b)/i;

/**
 * Where the trigger clause stops. `Do not use …`, `Route … instead` and
 * `Pair with …` are routing notes between skills — true, and not what you are
 * reading a card to find out.
 */
const BOUNDARY_OPENER =
    /(?:^|[.!?;]\s+)((?:Do\s+not|Don'?t|Never)\s+use\b|Route\b|Pair\s+with\b|Skip\b|Use\s+(?:an?|the|[a-z][a-z0-9-]*\s+(?:instead|alongside))\b)/i;

function openerIndex(text: string, opener: RegExp, from = 0): number | null {
    const match = opener.exec(text.slice(from));
    if (!match || match.index === undefined) return null;
    return from + match.index + match[0].length - match[1].length;
}

export function splitSkillDescription(description: string): SkillDescriptionParts {
    const text = (description || '').trim();
    const triggerStart = openerIndex(text, TRIGGER_OPENER);

    // No trigger clause, or the description is one: the whole thing is summary,
    // minus any routing note hanging off the end.
    if (triggerStart === null || triggerStart === 0) {
        const boundary = openerIndex(text, BOUNDARY_OPENER);
        const end = boundary !== null && boundary > 0 ? boundary : text.length;
        return { summary: text.slice(0, end).trim(), trigger: '' };
    }

    const triggerEnd = openerIndex(text, BOUNDARY_OPENER, triggerStart + 1) ?? text.length;

    return {
        summary: text.slice(0, triggerStart).trim(),
        trigger: text.slice(triggerStart, triggerEnd).trim(),
    };
}
