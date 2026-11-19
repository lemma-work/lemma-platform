import catalog from "./generated/catalog.json" with { type: "json" };

/* The plugin catalog the site draws `/templates` from. Every word and picture
   here is generated from the plugins themselves by
   `awesome-lemma/_build/site_catalog.py` (each plugin's `listing.json`, its
   `plugin.json`, its tables against the shared contract, its memory block and
   its pictures, and `_catalog/planned.json` for the ones not built yet), so
   this module only reads and relates them. Run that script again rather than
   editing the JSON. */

/** One picture of a plugin's app, in the site's light and dark. */
export interface Picture {
    light: string;
    dark: string;
    width: number;
    height: number;
    caption: string;
}

/** An account a plugin asks each person for: any one of `any` will do. */
export interface Account {
    any: string[];
    why: string;
    optional: boolean;
}

/** Something a plugin puts on a person's Today, by what they do about it. */
export interface DayItem {
    verb: string;
    line: string;
}

/** One theme's files of a capture: the picture, and for a loop its video. */
export interface VisualFiles {
    /** A close-up, or a loop's poster: its first frame worth stopping on. */
    still: string;
    mp4?: string;
    webm?: string;
}

/** A capture of the plugin's real app on its sample, sized in CSS pixels
 *  (the files are 2x). */
export interface VisualSize {
    width: number;
    height: number;
    /** "bottom" when the crop cuts through something there; the page lets it run off its frame. */
    bleed: string | null;
    light: VisualFiles;
    dark: VisualFiles;
}

/** A close-up or a short loop, with the same moment in the app's phone
 *  layout when the wide one would be too small to read on a phone. */
export interface Visual extends VisualSize {
    /** "still" or "loop" (the generated JSON types it as a plain string). */
    kind: string;
    alt: string;
    /** The view it shows, for the title bar of the window it is framed in. */
    window: string | null;
    phone: VisualSize | null;
}

/** A plugin's landing page, written in its `listing.json`. A plugin without
 *  one keeps the older page. */
export interface Landing {
    /** `eyebrow` follows "Plugins ·" in the label over the headline. */
    hero: { eyebrow: string; headline: string; sub: string; visual: Visual };
    /** The headings over how it works, better with, trust and questions. */
    headings: { how: string; better: string; trust: string; faq: string };
    steps: Array<{ title: string; body: string | null; visual: Visual }>;
    /** One claim each: what it does and how, with the piece of the app that
     *  shows it and a few words in the margin on the detail that proves it. */
    moments: Array<{ label: string; title: string; body: string; note: string; visual: Visual }>;
    /** Plugins that build on this one, each with what it adds. */
    betterWith: Array<{ slug: string; line: string }>;
    /** What stays private, each line true of the code. */
    trust: Array<{ title: string; body: string }>;
    faq: Array<{ q: string; a: string }>;
    /** The closing band's one line. */
    closing: string;
}

export interface BuiltPlugin {
    slug: string;
    /** Its folder in the plugins repository. */
    folder: string;
    name: string;
    line: string;
    /** Where it sits in the build order, 1 first. */
    position: number;
    /** The one-line job: its headline. */
    job: string;
    intro: string;
    does: string[];
    /** The server's job ids (`Job` on `PUT /users/me/work`) it is for. */
    jobs: string[];
    accounts: Account[];
    setup: string[];
    today: DayItem[];
    pictures: Picture[];
    /** Every plugin adding it also adds, bases first. */
    carries: string[];
    /** The plugins it names in its own `plugin.json`. */
    carriesDirect: string[];
    /** Shared tables it writes first (keys of `TABLES`). */
    makes: string[];
    /** Shared tables it reads that another plugin makes. */
    reads: string[];
    /** Whether it writes to the day's shared list, or reads it (Today). */
    day: string | null;
    /** The note every agent in a Lemma reads once it is installed. */
    memory: string | null;
    /** The note in each person's own memory, for the one plugin with one. */
    memoryPersonal: string | null;
    /** A try-it demo on made-up data, served from `public/plugins`. */
    demo: string | null;
    /** The apps the demo's own bar switches between, first the one it opens on. */
    demoViews: Array<{ label: string; src: string }>;
    /** Its landing page, once it has moments to show; null keeps the older page. */
    landing: Landing | null;
    /** Where adding it starts; null until it is published. */
    install: string | null;
}

export interface PlannedPlugin {
    slug: string;
    name: string;
    line: string;
    position: number;
    /** What it will do, in one line. */
    one: string;
    /** Plugins it will build on, built or not. */
    buildsOn: string[];
    /** Older catalog entries folded into it. */
    merges: string[];
}

export interface Line {
    id: string;
    name: string;
}

interface Catalog {
    lines: Line[];
    tables: Record<string, { name: string; line: string }>;
    built: BuiltPlugin[];
    planned: PlannedPlugin[];
}

const DATA: Catalog = catalog;

export const LINES: readonly Line[] = DATA.lines;
export const TABLES = DATA.tables;
/** The plugins that are built, in build order. */
export const BUILT: readonly BuiltPlugin[] = DATA.built;
/** The rest of the catalog, in build order. */
export const PLANNED: readonly PlannedPlugin[] = DATA.planned;
/** How many plugins the catalog holds, built or not. */
export const CATALOG_SIZE = BUILT.length + PLANNED.length;

export type AnyPlugin =
    | ({ kind: "built" } & BuiltPlugin)
    | ({ kind: "planned" } & PlannedPlugin);

export function getBuilt(slug: string): BuiltPlugin | undefined {
    return BUILT.find((p) => p.slug === slug);
}

export function getPlanned(slug: string): PlannedPlugin | undefined {
    return PLANNED.find((p) => p.slug === slug);
}

export function getPlugin(slug: string): AnyPlugin | undefined {
    const built = getBuilt(slug);
    if (built) return { kind: "built", ...built };
    const planned = getPlanned(slug);
    return planned ? { kind: "planned", ...planned } : undefined;
}

/** A plugin's name from its slug; the slug itself if the catalog lacks it. */
export function nameOf(slug: string): string {
    return getBuilt(slug)?.name ?? getPlanned(slug)?.name ?? slug;
}

export function lineName(id: string): string {
    return LINES.find((l) => l.id === id)?.name ?? id;
}

/** Names joined the way a sentence lists them: "A", "A and B", "A, B and C". */
export function listed(names: readonly string[], last = "and"): string {
    if (names.length <= 1) return names.join("");
    return `${names.slice(0, -1).join(", ")} ${last} ${names[names.length - 1]}`;
}

/** The sections of Today, by what a person does, in the order Today shows
 *  them. The verbs are the shared list's (`needs_you.verb`). */
export const DAY_SECTIONS: ReadonlyArray<{ verb: string; name: string }> = [
    { verb: "decide", name: "To decide" },
    { verb: "attend", name: "Meetings" },
    { verb: "do", name: "To do" },
    { verb: "answer", name: "To answer" },
    { verb: "waiting", name: "Waiting on others" },
    { verb: "drifting", name: "Gone quiet" },
];

/** The words for the server's job ids, as the job question on the way in
 *  shows them. */
const JOB_NAMES: Record<string, string> = {
    SALES: "Sales",
    MARKETING: "Marketing",
    PRODUCT: "Product",
    FOUNDER: "Founders",
    OPERATIONS: "Operations",
    HR: "HR",
    PROJECT_MANAGEMENT: "Project management",
    ENGINEERING: "Engineering",
    SUPPORT: "Customer support",
};

export function jobName(id: string): string {
    return JOB_NAMES[id] ?? id;
}

/** The plugin that makes a shared table, if one is built. */
export function makerOf(table: string): BuiltPlugin | undefined {
    return BUILT.find((p) => p.makes.includes(table));
}

/** Built plugins whose own `plugin.json` carries this one. */
export function carriedBy(slug: string): BuiltPlugin[] {
    return BUILT.filter((p) => p.carriesDirect.includes(slug));
}

/** The plugin that reads the day's shared list rather than writing to it. */
export function dayReader(): BuiltPlugin | undefined {
    return BUILT.find((p) => p.day === "reads");
}

/** Every plugin that writes to the day, with what it puts there. */
export function dayWriters(): BuiltPlugin[] {
    return BUILT.filter((p) => p.day === "writes" && p.today.length > 0);
}

export interface Tie {
    plugin: BuiltPlugin;
    /** Shared tables this plugin reads from the other. */
    uses: string[];
    /** Shared tables of this plugin the other reads. */
    usedFor: string[];
    /** The other carries this one: adding it adds this. */
    buildsOnThis: boolean;
}

/** The built plugins this one shares tables with, or that carry it, in
 *  build order. What a plugin itself carries is said apart ("Adding it also
 *  adds"), and so is Today, which reads what every plugin writes. */
export function tiesOf(plugin: BuiltPlugin): Tie[] {
    const ties: Tie[] = [];
    for (const other of BUILT) {
        if (other.slug === plugin.slug) continue;
        const uses = plugin.reads.filter((t) => other.makes.includes(t));
        const usedFor = other.reads.filter((t) => plugin.makes.includes(t));
        const buildsOnThis = other.carriesDirect.includes(plugin.slug);
        if (uses.length || usedFor.length || buildsOnThis) {
            ties.push({ plugin: other, uses, usedFor, buildsOnThis });
        }
    }
    return ties;
}

/** The accounts a plugin's carried plugins ask for, each with whose it is. */
export function carriedAccounts(plugin: BuiltPlugin): Array<{ from: BuiltPlugin; account: Account }> {
    return plugin.carries.flatMap((slug) => {
        const from = getBuilt(slug);
        return from ? from.accounts.map((account) => ({ from, account })) : [];
    });
}

/** Where a plugin's page sends somebody who wants to add it. */
export function installHref(plugin: BuiltPlugin): string | null {
    return plugin.install;
}

/** The catalog in build order, built and planned together, by line. */
export function byLine(): Array<{ line: Line; built: BuiltPlugin[]; planned: PlannedPlugin[] }> {
    return LINES.map((line) => ({
        line,
        built: BUILT.filter((p) => p.line === line.id),
        planned: PLANNED.filter((p) => p.line === line.id),
    }));
}
