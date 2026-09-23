/** How a shared file is read, kept apart from the page so the decision can
 *  be tested without a server render or a live link. Both functions are
 *  pure; between them they decide everything a reader sees. */

/** What to draw. Content-type first, because the server knows; the extension
 *  breaks the ties it leaves — markdown and CSV both arrive as `text/plain`
 *  often enough that the name is the better witness. */
export function readAs(type: string, name: string): "markdown" | "html" | "csv" | "text" | "image" | "pdf" | "other" {
    const ext = (name.split(".").pop() ?? "").toLowerCase();
    if (type === "text/markdown" || ext === "md" || ext === "markdown") return "markdown";
    if (type === "text/html" || ext === "html" || ext === "htm") return "html";
    if (type === "text/csv" || ext === "csv" || ext === "tsv") return "csv";
    if (type === "application/pdf" || ext === "pdf") return "pdf";
    if (type.startsWith("image/")) return "image";
    if (/^text\//.test(type) || /^application\/(json|xml|javascript|x-yaml|yaml)/.test(type)) return "text";
    return "other";
}

/** Enough CSV to read one. Quoted fields carrying commas and escaped quotes
 *  survive; anything stranger falls through to the raw text, which is the
 *  honest outcome — a table built on a bad guess is worse than the file. */
export function toRows(text: string, separator: string): string[][] {
    const rows: string[][] = [];
    let row: string[] = [];
    let cell = "";
    let quoted = false;
    for (let i = 0; i < text.length; i += 1) {
        const ch = text[i];
        if (quoted) {
            if (ch === '"' && text[i + 1] === '"') { cell += '"'; i += 1; }
            else if (ch === '"') quoted = false;
            else cell += ch;
            continue;
        }
        if (ch === '"') quoted = true;
        else if (ch === separator) { row.push(cell); cell = ""; }
        else if (ch === "\n") { row.push(cell); rows.push(row); row = []; cell = ""; }
        else if (ch !== "\r") cell += ch;
    }
    if (cell || row.length) { row.push(cell); rows.push(row); }
    return rows.filter((entry) => entry.some((value) => value.trim() !== ""));
}
