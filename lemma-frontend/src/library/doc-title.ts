/** A file's name, as a title somebody would give it.
 *
 *  `lemma-content-plan-2026-08-31.md` is how a file is stored; "Lemma content
 *  plan · 31 Aug 2026" is what it is called. For display only — the path is
 *  still what opens, keys and renames it, and the stored name is one hover
 *  away. Sentence case rather than title case: the first word capitalised,
 *  the rest left as written, acronyms in capitals.
 */

import { asAcronym } from "./reading";

const EXTENSION = /\.([a-z0-9]{1,5})$/i;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"];

/** What kind of thing a file is, in a word — in place of `text/markdown`. */
const KINDS: Record<string, string> = {
    md: "Page", markdown: "Page", mdx: "Page",
    txt: "Text", pdf: "PDF", html: "Web page", htm: "Web page",
    csv: "Spreadsheet", tsv: "Spreadsheet", xlsx: "Spreadsheet", xls: "Spreadsheet",
    doc: "Document", docx: "Document", pptx: "Slides", key: "Slides",
    png: "Image", jpg: "Image", jpeg: "Image", gif: "Image", webp: "Image", svg: "Image", heic: "Image",
    mp4: "Video", mov: "Video", webm: "Video", mp3: "Audio", wav: "Audio", m4a: "Audio",
    json: "Data", yaml: "Data", yml: "Data", zip: "Archive",
    py: "Code", ts: "Code", tsx: "Code", js: "Code", sh: "Code",
};

export function docTitle(name: string): string {
    const raw = String(name ?? "").split("/").filter(Boolean).pop() ?? "";
    let stem = raw.replace(EXTENSION, "");
    if (!stem || stem.startsWith(".")) return raw;
    /* A new page is "Untitled" plus a stamp that only keeps its file name
       unique; the stamp is not part of what it is called. */
    if (/^untitled[-_ ][a-z0-9]{6,10}$/i.test(stem)) return "Untitled";

    /* A date in the name is said as a date, after the title. */
    let when = "";
    const date = /(?:^|[-_ .])(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})(?=$|[-_ .])/.exec(stem);
    if (date) {
        const month = Number(date[2]);
        const day = Number(date[3]);
        if (month >= 1 && month <= 12 && day >= 1 && day <= 31) {
            when = day + " " + MONTHS[month - 1] + " " + date[1];
            stem = (stem.slice(0, date.index) + stem.slice(date.index + date[0].length)).trim();
        }
    }

    const words = stem
        .replace(/([a-z])([A-Z])/g, "$1 $2")
        .split(/[-_\s.]+/)
        .filter(Boolean)
        .map((word, index) => {
            const acronym = asAcronym(word);
            if (acronym) return acronym;
            /* Only the first letter of the first word. The rest is left as
               written: a capital further in is usually a name. */
            return index === 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word;
        });
    const title = words.join(" ");
    if (!title) return when || raw;
    return when ? title + " · " + when : title;
}

/** The kind line under a file's title: its description when it has one,
 *  otherwise a word for what it is rather than a MIME type. */
export function fileKind(name: string, detail?: string | null): string {
    const said = (detail ?? "").trim();
    if (said && !/^[a-z]+\/[\w.+-]+$/i.test(said) && !/^(file|folder|directory)$/i.test(said)) return said;
    const extension = EXTENSION.exec(name)?.[1]?.toLowerCase();
    if (extension && KINDS[extension]) return KINDS[extension];
    if (/markdown/i.test(said)) return "Page";
    if (/^image\//i.test(said)) return "Image";
    if (/pdf/i.test(said)) return "PDF";
    return extension ? extension.toUpperCase() : "File";
}
