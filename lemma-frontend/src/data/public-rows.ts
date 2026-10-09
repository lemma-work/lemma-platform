/* A table open to people outside the space: the rows a form adds.
 *
 * A form is not a thing of its own. It is any page — the one Lemma hosts, a
 * website with the script on it, an app the teammate builds — that adds a row
 * to a table opened here. The table decides who may add rows and which columns
 * they may write; the page only asks. Pure: wire mappers and sentences. */

import type { TableOpeningResponse } from "lemma-sdk";
import type { WebWidget, WidgetAnswer } from "./contacts";

export type Audience = "anyone" | "contacts";

export interface OfferedColumn {
    name: string;
    type: string;
    /** The table can't take a row without it, so it is always open. */
    required: boolean;
    options: string[];
    description: string | null;
    /** The form control that asks for it (`email`, `select`, `textarea`, …),
     *  decided by the table so every page draws the same one. */
    input: string;
}

export interface TableOpening {
    table: string;
    /** Each member sees only their own rows; such a table can't be opened. */
    perUser: boolean;
    contactOwned: boolean;
    offered: OfferedColumn[];
    /** Who outside may add rows, or null when the table is closed. */
    audience: Audience | null;
    columns: string[];
}

export function readOpening(wire: TableOpeningResponse): TableOpening {
    return {
        table: wire.table,
        perUser: Boolean(wire.per_user),
        contactOwned: Boolean(wire.contact_owned),
        offered: (wire.offered ?? []).map((column) => ({
            name: column.name,
            type: column.type,
            required: Boolean(column.required),
            options: column.options ?? [],
            description: column.description ?? null,
            input: wireInput(column),
        })),
        audience: wire.audience === "anyone" || wire.audience === "contacts" ? wire.audience : null,
        columns: wire.columns ?? [],
    };
}

/** The column's form control. Read loosely: a server older than the field
 *  sends none, and a plain text box asks for anything. */
function wireInput(column: TableOpeningResponse["offered"][number]): string {
    const input = (column as { input?: unknown }).input;
    return typeof input === "string" && input ? input : "text";
}

/** What a column asks, as people outside will read it: its description, or
 *  its name said plainly. */
export function columnQuestion(column: OfferedColumn): string {
    if (column.description?.trim()) return column.description.trim();
    const words = column.name.replace(/_/g, " ").trim();
    return words ? words.charAt(0).toUpperCase() + words.slice(1) : column.name;
}

/** The columns ticked when a closed table is first opened: all it offers. */
export function startingColumns(opening: TableOpening): string[] {
    return opening.audience ? opening.columns : opening.offered.map((column) => column.name);
}

/** What is wrong with these columns, or null when they can be opened. */
export function openingProblem(opening: TableOpening, chosen: string[]): string | null {
    if (!chosen.length) return "Tick at least one column people can fill in.";
    const missing = opening.offered.filter((column) => column.required && !chosen.includes(column.name));
    if (missing.length) return "The table needs " + missing.map((column) => column.name).join(", ") + ", so it stays ticked.";
    return null;
}

/** Who may answer, said for the person choosing. */
export function audienceChoices(): { value: Audience | "off"; label: string; note: string }[] {
    return [
        { value: "anyone", label: "Anyone with the link", note: "A person who confirms their email is also kept as a contact." },
        { value: "contacts", label: "Only people who confirm their email", note: "They get a code first, so you know who answered." },
        { value: "off", label: "Nobody", note: "The table takes rows from members only." },
    ];
}

/** What a web chat must answer for a table's form to reach the people the
 *  table takes rows from: a chat for known people only turns strangers away. */
export function widgetAnswerFor(audience: Audience): WidgetAnswer {
    return audience === "contacts" ? "known" : "anyone";
}

/** The web chat a table's form goes through: one switched on, answering the
 *  table's people. Null when there is none, and making one is the member's
 *  choice — it is a key on the open web, never made behind their back. */
export function doorFor(widgets: WebWidget[], audience: Audience): WebWidget | null {
    const on = widgets.filter((widget) => widget.answer !== "off");
    if (audience === "anyone") return on.find((widget) => widget.answer === "anyone") ?? null;
    return on.find((widget) => widget.answer === "known") ?? on[0] ?? null;
}

/** The hosted form for a table, on the space's web chat. */
export function formLink(pageUrl: string, table: string): string {
    return pageUrl + "?table=" + encodeURIComponent(table);
}

/** The script tag that draws the table's form where it is pasted. */
export function formEmbed(embed: string, table: string): string {
    return embed.replace(/\s+async><\/script>$/, ` data-lemma-table="${escapeHtml(table)}" async></script>`);
}

/** Text as it may sit in HTML, inside an element or a quoted attribute. An
 *  option is whatever a member typed, and this snippet is pasted into a page. */
function escapeHtml(value: string): string {
    return value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

/** A plain HTML form on someone's own page, sent through the same script.
 *
 *  `method="post"`: the script sends the answers itself, but if it has not
 *  loaded the browser submits the form on its own, and a GET would put every
 *  answer in the address bar, the history and the server's logs. */
export function htmlFormSnippet(embed: string, table: string, columns: OfferedColumn[]): string {
    const fields = columns.map((column) => {
        const required = column.required ? " required" : "";
        const name = escapeHtml(column.name);
        if (column.input === "select") {
            const options = column.options.map((option) => `<option>${escapeHtml(option)}</option>`).join("");
            return `  <select name="${name}"${required}>${options}</select>`;
        }
        if (column.input === "textarea") return `  <textarea name="${name}"${required}></textarea>`;
        return `  <input name="${name}" type="${escapeHtml(column.input)}"${required}>`;
    });
    return [
        `<form method="post" data-lemma-table="${escapeHtml(table)}">`,
        ...fields,
        "  <button>Send</button>",
        "</form>",
        embed.replace(/\s+async></, " data-lemma-chat=\"off\" async><"),
    ].join("\n");
}

/** What to ask the teammate for a form in the space's own design. */
export function customFormAsk(table: string): string {
    return `Build a form for the ${table} table as an app, using the lemma-form skill. Match our look, keep it short, and put the chat beside it so people can ask questions or have it filled in for them.`;
}
