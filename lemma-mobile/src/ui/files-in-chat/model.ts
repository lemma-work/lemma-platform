/** What the file pieces of a chat decide before they draw: how a few photos
 *  sit together, the first rows of a spreadsheet, a recording's bars, a
 *  document's one line. Nothing in here knows which chat it is in, so a
 *  chat with a Lemma and a group draw files the same way.
 *
 *  Pure, so the tests read it directly. */

/* ── photos ─────────────────────────────────────────────────────────────── */

export interface PhotoTiles {
    /** How many are drawn: one large and up to two small. */
    shown: number;
    /** Said on the last one drawn, "+N", when there are more. */
    more: number;
}

export function photoTiles(count: number): PhotoTiles {
    const shown = Math.min(Math.max(count, 0), 3);
    return { shown, more: Math.max(0, count - shown) };
}

/** "1 of 5". */
export function ofCount(index: number, count: number): string {
    return `${index + 1} of ${count}`;
}

/* ── a spreadsheet or a CSV ─────────────────────────────────────────────── */

export interface Sheet {
    header: string[];
    /** The first rows after the header. */
    rows: string[][];
    /** Every row after the header, however many are shown. */
    total: number;
}

/** The separator a file uses: a tab for `.tsv`, else whichever of comma,
 *  semicolon and tab its first line has most of. */
export function separatorOf(text: string, name = ""): string {
    if (/\.tsv$/i.test(name)) return "\t";
    const first = text.slice(0, text.indexOf("\n") >= 0 ? text.indexOf("\n") : text.length);
    const counts: [string, number][] = [",", ";", "\t"].map((one) => [one, first.split(one).length - 1]);
    counts.sort((a, b) => b[1] - a[1]);
    return counts[0][1] > 0 ? counts[0][0] : ",";
}

/** RFC 4180, forgivingly: quoted fields, doubled quotes inside them, line
 *  breaks inside quotes, CRLF, a last line with or without its break. The
 *  rows past `keep` are counted, not kept. */
export function readSheet(text: string, { keep = 5, name = "" }: { keep?: number; name?: string } = {}): Sheet {
    const sep = separatorOf(text, name);
    const rows: string[][] = [];
    let total = -1;
    let field = "";
    let row: string[] = [];
    let quoted = false;
    let touched = false;
    const endRow = () => {
        row.push(field);
        field = "";
        const empty = row.length === 1 && row[0] === "" && !touched;
        if (!empty) {
            total += 1;
            if (rows.length <= keep) rows.push(row);
        }
        row = [];
        touched = false;
    };
    for (let index = 0; index < text.length; index += 1) {
        const char = text[index];
        if (quoted) {
            if (char === "\"") {
                if (text[index + 1] === "\"") {
                    field += "\"";
                    index += 1;
                } else quoted = false;
            } else field += char;
            continue;
        }
        if (char === "\"" && field === "") {
            quoted = true;
            touched = true;
        } else if (char === sep) {
            row.push(field);
            field = "";
            touched = true;
        } else if (char === "\n" || char === "\r") {
            if (char === "\r" && text[index + 1] === "\n") index += 1;
            endRow();
        } else field += char;
    }
    if (field !== "" || row.length > 0 || touched) endRow();
    const [header = [], ...body] = rows;
    return { header, rows: body.slice(0, keep), total: Math.max(0, total) };
}

/** The first table in a document's markdown (what the server converts a
 *  spreadsheet into), as a sheet. Null when it has none. */
export function sheetFromMarkdown(markdown: string, keep = 5): Sheet | null {
    const lines = markdown.split("\n");
    const start = lines.findIndex((line, index) => /^\s*\|/.test(line) && /^\s*\|?\s*:?-{2,}/.test(lines[index + 1] ?? ""));
    if (start < 0) return null;
    const cells = (line: string) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
    const header = cells(lines[start]);
    const body: string[][] = [];
    for (let index = start + 2; index < lines.length && /^\s*\|/.test(lines[index]); index += 1) body.push(cells(lines[index]));
    return { header, rows: body.slice(0, keep), total: body.length };
}

export function rowCount(total: number): string {
    return `${total.toLocaleString("en-US")} row${total === 1 ? "" : "s"}`;
}

/* ── a recording ────────────────────────────────────────────────────────── */

/** A recording's bars, between 0.18 and 1, the same every time for the same
 *  file. Drawn from its name rather than its sound: the phone does not
 *  decode a recording to draw it, so the bars are its look, and playing
 *  fills them to say how far it has got. */
export function barsOf(seed: string, count = 36): number[] {
    let hash = 2166136261;
    for (let index = 0; index < seed.length; index += 1) hash = Math.imul(hash ^ seed.charCodeAt(index), 16777619);
    const raw: number[] = [];
    for (let index = 0; index < count; index += 1) {
        hash = Math.imul(hash ^ (hash >>> 15), 2246822507) ^ index;
        hash = Math.imul(hash ^ (hash >>> 13), 3266489909);
        raw.push(((hash >>> 0) % 1000) / 1000);
    }
    /* Smoothed, so it reads as speech rather than noise, with a soft start
       and end. */
    return raw.map((value, index) => {
        const near = (raw[index - 1] ?? value) * 0.25 + value * 0.5 + (raw[index + 1] ?? value) * 0.25;
        const edge = Math.min(1, (Math.min(index, count - 1 - index) + 1) / 4);
        return Math.round((0.18 + 0.82 * near * (0.55 + 0.45 * edge)) * 100) / 100;
    });
}

/** "0:42", "12:05". */
export function clockOfSeconds(seconds: number): string {
    const whole = Math.max(0, Math.floor(seconds));
    return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/* ── a document ─────────────────────────────────────────────────────────── */

/** "PDF · 4 pages · 120 KB": what kind, how long, how big, leaving out what
 *  is not known. */
export function documentLine(name: string, pages: number | null, size: string): string {
    const dot = name.lastIndexOf(".");
    const kind = dot > 0 ? name.slice(dot + 1).toUpperCase() : "File";
    return [kind, pages ? `${pages} page${pages === 1 ? "" : "s"}` : "", size].filter(Boolean).join(" · ");
}
