/**
 * Docs holds three kinds of thing, and they are not the same kind of thing.
 *
 * Pod docs are a library: shared, organized, searched. Skills are a registry:
 * named packages an agent loads, with a contract to keep. Personal files are a
 * scope: private, disposable, drafted. They share one tree and one storage
 * model — `/`, `/skills`, `/me` — because they are all pod files, and splitting
 * the storage would buy nothing. What they do not share is the set of verbs
 * that make sense on them, so the surface renders each one its own way.
 */

import { SKILLS_ROOT, isSkillsPath } from './skills';

export const PERSONAL_ROOT = '/me';

export type DocSectionId = 'POD' | 'SKILLS' | 'PERSONAL';

export type DocSection = {
    id: DocSectionId;
    /** The switcher's label — short enough to sit beside two others. */
    label: string;
    /** The heading once you are inside it. */
    title: string;
    /** Where the section opens; null is the docs root. */
    root: string | null;
    /** What lives here and who can see it. */
    blurb: string;
    emptyLine: string;
    searchPlaceholder: string;
};

export const DOC_SECTIONS: readonly DocSection[] = [
    {
        id: 'POD',
        label: 'Pod',
        title: 'Docs',
        root: null,
        blurb: 'Shared with everyone in this pod, and readable by its agents.',
        emptyLine: 'No docs here yet — drop files here, or click to browse',
        searchPlaceholder: 'Search inside docs',
    },
    {
        id: 'SKILLS',
        label: 'Skills',
        title: 'Skills',
        root: SKILLS_ROOT,
        blurb: 'Procedures your agents can load. Each skill is a folder holding a SKILL.md.',
        emptyLine: 'No skills yet — create one to teach your agents a procedure',
        searchPlaceholder: 'Search inside skills',
    },
    {
        id: 'PERSONAL',
        label: 'Personal',
        title: 'Personal files',
        root: PERSONAL_ROOT,
        blurb: 'Private to you. Nobody else in the pod can open these until you share one.',
        emptyLine: 'Nothing here yet — drop files to keep them to yourself',
        searchPlaceholder: 'Search inside your files',
    },
] as const;

function normalize(path: string | null | undefined): string {
    if (!path) return '/';
    const cleaned = path.replace(/\\/g, '/').replace(/\/+$/g, '');
    if (!cleaned) return '/';
    return cleaned.startsWith('/') ? cleaned : `/${cleaned}`;
}

export function isPersonalPath(path: string | null | undefined): boolean {
    const normalized = normalize(path);
    return normalized === PERSONAL_ROOT || normalized.startsWith(`${PERSONAL_ROOT}/`);
}

export function isPersonalRootPath(path: string | null | undefined): boolean {
    return normalize(path) === PERSONAL_ROOT;
}

/** Which section a directory belongs to — subfolders included. */
export function docSectionForPath(path: string | null | undefined): DocSectionId {
    if (isSkillsPath(path)) return 'SKILLS';
    if (isPersonalPath(path)) return 'PERSONAL';
    return 'POD';
}

export function docSection(id: DocSectionId): DocSection {
    return DOC_SECTIONS.find((section) => section.id === id) || DOC_SECTIONS[0];
}

/**
 * Skills and personal files are reachable from the switcher, so listing their
 * folders inside the pod root as well would be two doors into one room.
 */
export function isSectionRoot(path: string | null | undefined): boolean {
    const normalized = normalize(path);
    return DOC_SECTIONS.some((section) => section.root !== null && section.root === normalized);
}
