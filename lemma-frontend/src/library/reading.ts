/** Turning stored values into what a person would have said.
 *
 *  Pure, and apart from the views, because both of these have a right answer
 *  that is worth pinning down in a test rather than discovering on a screen. */

const DAY = 24 * 60 * 60 * 1000;

/** A bare `YYYY-MM-DD` is a day on a calendar, not a moment.
 *
 *  `Date.parse` disagrees: it reads one as midnight UTC, and a view then
 *  compares that against midnight where the reader is. Anywhere east of UTC
 *  every deadline moved back a day — a launch checklist of twelve tasks opened
 *  with today's work listed as "yesterday", in red. A value with a time in it
 *  really is a moment and is parsed as one. */
export function dayStart(value: unknown): number {
    const raw = String(value);
    const bare = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
    if (bare) return new Date(Number(bare[1]), Number(bare[2]) - 1, Number(bare[3])).getTime();
    return Date.parse(raw);
}

/** "in 3 days", "today", "6 days ago".
 *
 *  A deadline stored as `2026-09-21` answers "when" only after the reader has
 *  worked out what day it is, which is the arithmetic the list exists to do. */
export function whenever(value: unknown, now = new Date()): { text: string; days: number } | null {
    const at = dayStart(value);
    if (!Number.isFinite(at)) return null;
    const midnight = new Date(now);
    midnight.setHours(0, 0, 0, 0);
    const days = Math.round((at - midnight.getTime()) / DAY);
    if (days === 0) return { text: "today", days };
    if (days === 1) return { text: "tomorrow", days };
    if (days === -1) return { text: "yesterday", days };
    if (days < 0) return { text: -days + " days ago", days };
    return { text: "in " + days + " days", days };
}

/** A number as it would be written down.
 *
 *  Separated only past ten thousand, which is where a run of digits stops
 *  being readable at a glance — and short of where a year would be caught and
 *  turned into "2,026". */
export function readableNumber(value: unknown): string | null {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || Math.abs(parsed) < 10000) return null;
    return parsed.toLocaleString();
}

/** 1,240 and 18.4k and 2.1m. A chart axis with eight digits on it is an axis
 *  nobody reads and a plot squeezed into what is left. */
export function compact(value: number): string {
    const size = Math.abs(value);
    if (size >= 1e6) return (value / 1e6).toFixed(1).replace(/\.0$/, "") + "m";
    if (size >= 1e4) return Math.round(value / 1e3) + "k";
    if (size >= 1e3) return (value / 1e3).toFixed(1).replace(/\.0$/, "") + "k";
    return String(Math.round(value * 10) / 10);
}

/** A table's name, as a person would write it rather than as a database does.
 *
 *  `content_ideas` is a valid identifier and an odd thing to print at the top
 *  of a page. Only ever for display: the identifier goes on the wire, keys the
 *  cache, and names the tab; this is what gets drawn over it.
 *
 *  Acronyms are the one part that cannot be done by rule alone. A word of four
 *  letters or fewer with no vowel in it is one — `gtm`, `crm`, `sdk`, `mgmt` —
 *  and the rest are a short list, because `Api Url` and `Gtm Targets` read as
 *  typos and there is no reading of the letters that says otherwise.
 */
const SHOUTED = new Set([
    "id", "api", "url", "uri", "ai", "ui", "ux", "sla", "seo", "oss", "arpa",
    "csat", "nps", "faq", "pdf", "csv", "html", "css", "sql", "http", "https",
    "uuid", "eta", "poc", "roi", "saas", "b2b", "b2c", "cta", "kpi", "okr",
]);

export function readableName(name: string): string {
    const words = String(name ?? "").split(/[_\-\s]+/).filter(Boolean);
    if (words.length === 0) return String(name ?? "");
    return words
        .map((word) => {
            const lower = word.toLowerCase();
            if (SHOUTED.has(lower)) return lower.toUpperCase();
                    /* `y` counts, or `sync` is an acronym and every table that syncs
               something gets shouted at. */
            if (lower.length <= 4 && !/[aeiouy]/.test(lower) && /^[a-z]+$/.test(lower)) return lower.toUpperCase();
            /* Only the first letter. Upper-casing the whole word would turn
               `LEDFlex` into `Ledflex`, and a name that arrived already
               capitalised was capitalised on purpose. */
            return word[0].toUpperCase() + word.slice(1);
        })
        .join(" ");
}
