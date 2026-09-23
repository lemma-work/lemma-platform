import { instructionWords, readFrontmatter } from "./skill-frontmatter";

/** What a teammate actually knows how to do.
 *
 *  Skills are a real thing in this product and nothing in this app has ever
 *  shown them. The Profile had a section headed Skills that listed the
 *  agent's *toolsets* — `WORKSPACE_CLI`, `BROWSER` — with a meta line counting
 *  `allowed_actions`, which are permissions on an agent row. Neither is a
 *  skill. A skill is a folder under the pod's `/skills/` with a `SKILL.md` in
 *  it, and the teammate reads it when the work calls for it.
 *
 *  This module is the reading, kept pure: a library listing and the text of
 *  each `SKILL.md` go in, and cards come out. Nothing here fetches, so every
 *  rule about what counts as a skill is testable against a payload nobody has
 *  to be online to produce.
 */

/** Where skills live in the pod file tree. The same constant the backend
 *  loader calls `_SKILLS_ROOT`, and the same path the Library's Skills
 *  location already points at. */
export const SKILLS_ROOT = "/skills";

/** The fields of a `LibraryItem` this reader needs, and no more.
 *
 *  Declared rather than imported so the module stays free of the data layer:
 *  `LibraryItem` is structurally one of these, and a pure reader that drags in
 *  `@/data` drags in the SDK behind it. */
export interface ListedItem {
    name?: string;
    kind?: string;
    path?: string;
    updated?: string;
}

/** One directory under `/skills`, before its SKILL.md has been read. */
export interface SkillFolder {
    /** The directory name. The identity: a skill is its folder, whatever its
     *  frontmatter later claims. */
    folder: string;
    /** `/skills/<folder>` */
    dir: string;
    /** `/skills/<folder>/SKILL.md` — what to fetch, and what a click opens. */
    file: string;
    updated: string;
}

/** What came back for one skill's SKILL.md. A failure is a value here rather
 *  than an exception, because a skill whose file cannot be read still has to
 *  appear as a card naming its folder — vanishing is how a permission problem
 *  and an empty pod became the same blank space elsewhere in this app. */
export type SkillRead = { text: string } | { failure: string };

export interface SkillCard {
    /** The folder, which is the key: it is the one thing that is always there
     *  and always unique within `/skills`. */
    folder: string;
    dir: string;
    file: string;
    /** What to print. The frontmatter's `name` on a skill that loads, because
     *  that is what the teammate calls it by — and on one that does not, the
     *  folder, because the folder is what is actually on disk and what somebody
     *  has to go and fix. (The loader requires the two to match, so on a
     *  working skill this is the same string either way.) */
    title: string;
    description: string;
    /** Whether the SKILL.md was read at all.
     *
     *  Not the same question as whether it parsed. A file that could not be
     *  fetched has no description *and no frontmatter*, and the card was
     *  saying "no description in its frontmatter" about a file nobody had
     *  managed to open — a confident statement about something unseen, which
     *  is the class of thing this whole section exists to stop doing. */
    read: boolean;
    /** Why this is not loadable, or `null`. A card that says so beats a card
     *  that is quietly wrong, and beats no card at all. */
    problem: string | null;
    /** Words of instruction under the frontmatter. `0` when unreadable. */
    words: number;
    updated: string;
}

/** The skill folders in a listing of `/skills`.
 *
 *  Defensive on every field, because none of them is guaranteed: the listing
 *  is the generic file listing, the same one that already returns tables and
 *  loose files elsewhere, and `/skills` carries the system skills spliced in
 *  read-only beside the pod's own. Only a folder sitting directly under
 *  `/skills` is a skill — a loose file there is not one, and neither is
 *  anything nested deeper. */
export function skillFoldersFrom(items: readonly ListedItem[]): SkillFolder[] {
    const seen = new Set<string>();
    const found: SkillFolder[] = [];
    for (const item of items ?? []) {
        if (item?.kind !== "folder") continue;
        const path = typeof item.path === "string" ? item.path.replace(/\/+$/, "") : "";
        if (!path.startsWith(SKILLS_ROOT + "/")) continue;
        const folder = path.slice(SKILLS_ROOT.length + 1);
        /* Directly under the root. A nested folder — a skill's own `scripts/`
           or `references/` — is a resource of a skill, not a skill. */
        if (!folder || folder.includes("/")) continue;
        if (seen.has(folder)) continue;
        seen.add(folder);
        found.push({
            folder,
            dir: SKILLS_ROOT + "/" + folder,
            file: SKILLS_ROOT + "/" + folder + "/SKILL.md",
            updated: typeof item.updated === "string" ? item.updated : "",
        });
    }
    return found.sort((left, right) => left.folder.localeCompare(right.folder));
}

/** A skill's name as a person would write it.
 *
 *  Skills are named the way directories are — `lemma-app-design`,
 *  `liteparse_documents` — because that is what they are, a folder on a path.
 *  A card is not a path, and a row of hyphenated slugs reads as filenames
 *  somebody forgot to set in type.
 *
 *  Display only. `folder` stays exactly as it is: it is the identifier the
 *  path is built from and the seed the card's colour and mark come out of, and
 *  a name that drifted from it would send somebody to a file that is not
 *  there.
 */
export function readableName(raw: string): string {
    const words = raw.replace(/[-_]+/g, " ").trim();
    if (!words) return raw;
    return words.charAt(0).toUpperCase() + words.slice(1);
}

export function cardFor(folder: SkillFolder, read: SkillRead): SkillCard {
    const base = { folder: folder.folder, dir: folder.dir, file: folder.file, updated: folder.updated };

    if ("failure" in read) {
        return { ...base, title: readableName(folder.folder), description: "", read: false, problem: read.failure, words: 0 };
    }

    const front = readFrontmatter(read.text, folder.folder);
    return {
        ...base,
        /* The folder is the fallback, and on anything broken it is the answer
           rather than the fallback: a skill renamed in its frontmatter and not
           on disk is findable only by what is on disk, and that is the path
           somebody has to go and fix. */
        title: readableName(front.problem ? folder.folder : front.name || folder.folder),
        description: front.description,
        read: true,
        problem: front.problem,
        words: instructionWords(read.text),
    };
}

/** The line under the deck when some of it does not work, and nothing at all
 *  when it does.
 *
 *  Said once under the hand rather than counted in the heading: a heading that
 *  reads "6 skills" over a deck where two are unloadable is a count standing
 *  in for a fact it does not have, and a warning printed on every card stops
 *  being read. The cards carry the reason; this carries the tally. */
export function sayUnloadable(cards: readonly SkillCard[]): string | null {
    const broken = cards.filter((card) => card.problem).length;
    if (!broken) return null;
    if (broken === cards.length) {
        return cards.length === 1
            ? "This skill cannot be loaded, so the teammate cannot use it."
            : "None of these can be loaded, so the teammate cannot use any of them.";
    }
    return broken === 1
        ? "One of these cannot be loaded, so the teammate cannot use it."
        : broken + " of these cannot be loaded, so the teammate cannot use them.";
}
