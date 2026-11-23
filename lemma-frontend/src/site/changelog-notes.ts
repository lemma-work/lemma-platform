import GithubSlugger from "github-slugger";
import type { ContentDoc } from "@/site/content/types";

/* An entry of What's new, read from its notes: the sections it holds, the
   one written for API, SDK and CLI users pulled out to sit in its own panel,
   and the rest left to read in order. */

export const DEVELOPER_HEADING = "What changes for API, SDK and CLI users";

export type NotesSection = { title: string; body: string };

const FENCE = /^\s*(`{3,}|~{3,})/;

/** A note cut at its h2 headings. Fenced code is left alone, so a `## ` in a
 *  shell example is not taken for a heading. */
export function splitNotes(body: string): {
    intro: string;
    sections: NotesSection[];
} {
    const intro: string[] = [];
    const sections: { title: string; lines: string[] }[] = [];
    let fence: string | null = null;
    for (const line of body.split("\n")) {
        const marker = line.match(FENCE)?.[1];
        if (marker) {
            if (fence === null) fence = marker[0];
            else if (marker[0] === fence) fence = null;
        }
        const heading =
            fence === null && !marker
                ? line.match(/^##\s+(.+?)(?:\s+#+)?\s*$/)
                : null;
        if (heading) {
            sections.push({ title: heading[1], lines: [] });
            continue;
        }
        (sections.at(-1)?.lines ?? intro).push(line);
    }
    return {
        intro: intro.join("\n").trim(),
        sections: sections.map((s) => ({
            title: s.title,
            body: s.lines.join("\n").trim(),
        })),
    };
}

/** Every ATX heading one level deeper, outside fenced code: inside an entry
 *  whose own title is the h2, its sections are h3s. */
export function demoteHeadings(markdown: string): string {
    let fence: string | null = null;
    return markdown
        .split("\n")
        .map((line) => {
            const marker = line.match(FENCE)?.[1];
            if (marker) {
                if (fence === null) fence = marker[0];
                else if (marker[0] === fence) fence = null;
                return line;
            }
            return fence === null && /^#{1,5}\s/.test(line) ? "#" + line : line;
        })
        .join("\n");
}

export type ChangelogKind = "release" | "update";

export type ChangelogEntry = {
    slug: string;
    kind: ChangelogKind;
    /** "Lemma 0.10.1", or an update's title without its date. */
    heading: string;
    /** A release's version; null for an update. */
    version: string | null;
    published: string;
    /** "28 Oct 2026". */
    date: string;
    description: string;
    /** The entry's own section names, the developer section left out. */
    sections: string[];
    /** The developer section's body, or null when the entry has none. */
    developer: string | null;
    /** The id its heading had, so a link to it still lands. */
    developerId: string | null;
    /** Everything else, in order, headings one level down. */
    notes: string;
    breaking: boolean;
    desktop: boolean;
};

const MONTHS = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
];

/** `2026-10-28` as "28 Oct 2026", the same wherever it is drawn. */
export function formatDay(iso: string): string {
    const [year, month, day] = iso.split("-").map(Number);
    return `${day} ${MONTHS[month - 1]} ${year}`;
}

const LEADING_DATE =
    /^(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s*\d{4})?\s*[—–-]\s*/;

/** An update is titled "September 16 — Editable widgets…"; the date is
 *  already beside it. */
export function updateHeading(title: string): string {
    return title.replace(LEADING_DATE, "").trim() || title;
}

export function changelogEntry(doc: ContentDoc): ChangelogEntry {
    const { frontmatter } = doc;
    const { intro, sections } = splitNotes(doc.body);
    const developerTitle = frontmatter.developer ?? DEVELOPER_HEADING;
    const developer = sections.find((s) => s.title === developerTitle) ?? null;
    const rest = sections.filter((s) => s !== developer);
    const release = frontmatter.tags.includes("release");
    const notes = [
        intro,
        ...rest.map((s) => `### ${s.title}\n\n${demoteHeadings(s.body)}`),
    ]
        .filter(Boolean)
        .join("\n\n");
    return {
        slug: doc.slug,
        kind: release ? "release" : "update",
        heading: release
            ? `Lemma ${frontmatter.title}`
            : updateHeading(frontmatter.title),
        version: release ? frontmatter.title : null,
        published: frontmatter.published,
        date: formatDay(frontmatter.published),
        description: frontmatter.description,
        sections: rest.map((s) => s.title),
        developer: developer ? demoteHeadings(developer.body) : null,
        developerId: developer ? new GithubSlugger().slug(developer.title) : null,
        notes,
        breaking: developer !== null && frontmatter.breaking === true,
        desktop: release && frontmatter.desktop === true,
    };
}

/** The one entry that offers the Desktop download: the newest release with a
 *  build. Entries come newest first. */
export function desktopEntry(entries: readonly ChangelogEntry[]): string | null {
    return entries.find((e) => e.kind === "release" && e.desktop)?.slug ?? null;
}

export function changelogCounts(entries: readonly ChangelogEntry[]) {
    return {
        all: entries.length,
        release: entries.filter((e) => e.kind === "release").length,
        update: entries.filter((e) => e.kind === "update").length,
    };
}
