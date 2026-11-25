import { isChat, readableName, stampOf } from "../../../data/model";
import { routes } from "../../../data/routes";
import type { ConversationRef, Lemma } from "../../../data/types";
import type { PodPerson } from "../../admin/people/model";
import { chatRoutes } from "../../chat/routes";
import { chatsRoutes } from "../../chats/routes";
import { filesRoutes } from "../../files/routes";
import { docTitle } from "@web/library/doc-title";
import type { TableItem } from "../../tables/source";
import { tablesRoutes } from "../../tables/routes";
import type { AgentRow, StandingJob, WorkflowRow } from "../../work/source";
import { workRoutes } from "../../work/routes";
import { answeringAs } from "@web/data/agents";
import { humanizeName } from "@web/schedule/schedules";
import { KIND_LABEL, type Candidate, type Hit, type HitGroup, type SearchKind } from "@web/search/matching";
import {
    agentCandidates,
    appCandidates,
    functionCandidates,
    itemsOf,
    MAX_RECORD_TABLES,
    nameOf,
    personCandidates,
    scheduleCandidates,
    tableCandidates,
    workflowCandidates,
    type RecordSource,
} from "@web/search/sources";

/** One box for everything in every Lemma: what the phone decides on top of
 *  the web's own matching and readers (`web/`). Pure, so `tests/` can hold
 *  it.
 *
 *  The web's palette searches the one pod it was opened in. The phone opens
 *  search from Home and the Lemmas tab, where no Lemma is current, so it
 *  searches every Lemma in the organization, or the one chosen. Each hit
 *  carries which Lemma it is in and which of the phone's screens it opens,
 *  and its id is made unique across Lemmas (`Support/tickets` and
 *  `Launch/tickets` are two hits). */

/** Long enough that a phrase is typed rather than sent a letter at a time,
 *  short enough not to feel like waiting (the web's). Only the server's
 *  half waits; the lists already held are matched on every keystroke. */
export const DEBOUNCE_MS = 180;

/** Below this, a server search is all noise. */
export const MIN_SERVER_QUERY = 2;

/** How long a Lemma's lists are kept before they are read again. */
export const CATALOGUE_STALE = 5 * 60_000;

/** How many files each Lemma's search returns: the web's twelve for one
 *  Lemma, fewer each when every Lemma is asked at once. */
export function filesPerLemma(scoped: boolean): number {
    return scoped ? 12 : 6;
}

/** What the server is asked, once the words have settled: nothing for a
 *  single letter. */
export function serverQueryOf(settled: string): string {
    const said = settled.trim();
    return said.length >= MIN_SERVER_QUERY ? said : "";
}

/* ── where a hit opens ───────────────────────────────────────────── */

export type Opening =
    | { kind: "lemma" }
    | { kind: "chat"; id: string }
    | { kind: "file"; path: string }
    | { kind: "table"; name: string }
    /** The row itself when it has an id; its table when it has none. */
    | { kind: "record"; table: string; id: string | null }
    | { kind: "app"; name: string }
    | { kind: "agent"; name: string }
    | { kind: "workflow"; name: string }
    /** No screen of its own: its chat. */
    | { kind: "function"; name: string; label: string }
    | { kind: "person" }
    | { kind: "schedule"; id: string };

/** What every hit carries: its Lemma, where it opens, and, for a chat, when
 *  it last moved (said at the row's end, never matched against). */
export interface Found {
    podId: string;
    open: Opening;
    at?: string | null;
}

export function foundOf(hit: Pick<Candidate, "payload">): Found | null {
    const held = hit.payload as Partial<Found> | null | undefined;
    return held && typeof held.podId === "string" && held.open ? (held as Found) : null;
}

/** A screen to push, or a function's chat to find first. */
export type Destination =
    | { href: string }
    | { discuss: { podId: string; name: string; label: string } };

/** Which of the phone's screens a hit opens. */
export function destinationOf(found: Found): Destination {
    const { podId, open } = found;
    switch (open.kind) {
        case "lemma": return { href: routes.lemma(podId) };
        case "chat": return { href: routes.chat(podId, open.id) };
        case "file": return { href: filesRoutes.file(podId, open.path) };
        case "table": return { href: tablesRoutes.table(podId, open.name) };
        /* The row, not the table it lives in: landing on the table after
           searching for a row is finding it twice. */
        case "record": return { href: open.id ? tablesRoutes.record(podId, open.table, open.id) : tablesRoutes.table(podId, open.table) };
        case "app": return { href: routes.app(podId, open.name) };
        case "agent": return { href: workRoutes.agent(podId, open.name) };
        case "workflow": return { href: workRoutes.workflow(podId, open.name) };
        case "schedule": return { href: workRoutes.schedule(podId, open.id) };
        /* A person is not a thing to edit: People is where a Lemma
           says who is in it. */
        case "person": return { href: chatsRoutes.people(podId) };
        case "function": return { discuss: { podId, name: open.name, label: open.label } };
    }
}

/** Where a function's chat goes when none can be bound: a new chat with the
 *  words in its box, sent by nobody. */
export function functionChatFallback(podId: string, label: string): string {
    return chatRoutes.filled(podId, `About the ${label.toLowerCase()} function: `);
}

/* ── readers: what each list holds, as hits in one Lemma ────────── */

function placed(podId: string, candidate: Candidate, open: Opening, extra: Partial<Candidate> = {}): Candidate {
    return { ...candidate, ...extra, id: podId + "/" + candidate.id, payload: { podId, open } satisfies Found };
}

/** The Lemmas themselves: the one kind not inside a Lemma. */
export function lemmaCandidates(lemmas: readonly Lemma[]): Candidate[] {
    return lemmas.map((lemma) => ({
        kind: "teammate",
        id: lemma.id,
        title: lemma.name,
        subtitle: lemma.description || null,
        payload: { podId: lemma.id, open: { kind: "lemma" } } satisfies Found,
    }));
}

/** Chats, the recent ones held and the older ones the server found by
 *  title: the ones a person or a platform started, as the lists draw them
 *  (`isChat`); a run a schedule or a workflow started is that schedule's or
 *  workflow's, found under its own name. When is kept out of the matching:
 *  "mon" is not a search for every chat last touched on a Monday. */
export function chatCandidates(podId: string, refs: readonly ConversationRef[]): Candidate[] {
    return refs.filter(isChat).map((ref) => ({
        kind: "conversation",
        id: podId + "/" + ref.id,
        title: ref.title,
        subtitle: null,
        payload: { podId, open: { kind: "chat", id: ref.id }, at: ref.activityAt } satisfies Found,
    }));
}

/** Agents by what they are called on screen, findable by row name too. */
export function agentHits(podId: string, rows: readonly AgentRow[]): Candidate[] {
    return agentCandidates(rows.map((row) => ({ id: row.id || row.name, name: row.label, description: row.blurb })))
        .map((candidate, index) => placed(podId, candidate, { kind: "agent", name: rows[index].name }, { haystack: rows[index].name }));
}

export function workflowHits(podId: string, rows: readonly WorkflowRow[]): Candidate[] {
    return workflowCandidates(rows.map((row) => ({ id: row.id || row.name, name: humanizeName(row.name), description: row.description })))
        .map((candidate, index) => placed(podId, candidate, { kind: "workflow", name: rows[index].name }, { haystack: rows[index].name }));
}

/** A raw list whose items are named, read by one of the web's readers with
 *  the name said as the phone says it; an item with no name cannot be
 *  opened, so it is left out. */
function named(raw: unknown, say: (name: string) => string, read: (raw: unknown) => Candidate[]): { candidates: Candidate[]; names: string[] } {
    const items = itemsOf(raw);
    const names = items.map((item) => nameOf(item, ""));
    const said = read(items.map((item, index) => ({ ...item, name: names[index] ? say(names[index]) : "" })));
    const kept = said.map((candidate, index) => ({ candidate, name: names[index] })).filter((one) => Boolean(one.name));
    return { candidates: kept.map((one) => one.candidate), names: kept.map((one) => one.name) };
}

export function functionHits(podId: string, raw: unknown): Candidate[] {
    const { candidates, names } = named(raw, humanizeName, functionCandidates);
    return candidates.map((candidate, index) => placed(podId, candidate, { kind: "function", name: names[index], label: candidate.title }, { haystack: names[index] }));
}

export function appHits(podId: string, raw: unknown): Candidate[] {
    const { candidates, names } = named(raw, readableName, appCandidates);
    return candidates.map((candidate, index) => placed(podId, candidate, { kind: "app", name: names[index] }, { haystack: names[index] }));
}

export function tableHits(podId: string, items: readonly TableItem[]): Candidate[] {
    return tableCandidates(items.map((item) => ({ id: item.name, name: readableName(item.name), column_count: item.columns ?? undefined })))
        .map((candidate, index) => placed(podId, candidate, { kind: "table", name: items[index].name }, { haystack: items[index].name }));
}

/** People, by the web's reader of the members list (it knows the flat
 *  shape), fed from the roster settings already read. */
export function personHits(podId: string, people: readonly PodPerson[]): Candidate[] {
    return personCandidates(people.map((person) => ({
        pod_member_id: person.id,
        user_id: person.userId,
        user_name: person.email && person.name === person.email ? "" : person.name,
        user_email: person.email ?? "",
        roles: person.roles,
    }))).map((candidate) => placed(podId, candidate, { kind: "person" }));
}

/** Schedules by their heading, said under it as what fires them; found by
 *  that, by what they run, and by the literal behind the words. The ones a
 *  workflow made for its own waits are nobody's to open. */
export function scheduleHits(podId: string, jobs: readonly StandingJob[]): Candidate[] {
    const shown = jobs.filter((job) => !job.internal);
    return scheduleCandidates(shown.map((job) => ({ id: job.id, name: job.title || humanizeName(job.name), cron: job.trigger, agent_name: job.target.label || job.target.name, function_name: job.triggerLiteral })))
        .map((candidate, index) => {
            const job = shown[index];
            return placed(podId, candidate, { kind: "schedule", id: job.id }, { haystack: [job.trigger, job.target.label, job.target.name, job.triggerLiteral, job.name].filter(Boolean).join(" ") });
        });
}

/** The lists each Lemma holds that search matches on every keystroke, in
 *  the order a Lemma's are read. */
export const CATALOGUE = ["chats", "agents", "workflows", "functions", "apps", "people", "schedules", "tables"] as const;
export type CataloguePart = (typeof CATALOGUE)[number];

/** One of those lists, as each area hands it over, as hits in its Lemma:
 *  a page of chats, agent rows, workflow rows, the raw functions and apps
 *  lists, the roster, schedules, a page of tables. */
export function catalogueHits(lemma: Pick<Lemma, "id" | "name">, part: CataloguePart, data: unknown): Candidate[] {
    const podId = lemma.id;
    switch (part) {
        case "chats": return chatCandidates(podId, (data as { items?: ConversationRef[] } | null)?.items ?? []);
        /* The front agent is the Lemma itself, and is called by its name. */
        case "agents": return agentHits(podId, answeringAs([...((data as AgentRow[] | null) ?? [])], lemma.name));
        case "workflows": return workflowHits(podId, (data as WorkflowRow[] | null) ?? []);
        case "schedules": return scheduleHits(podId, (data as StandingJob[] | null) ?? []);
        case "people": return personHits(podId, (data as PodPerson[] | null) ?? []);
        case "tables": return tableHits(podId, (data as { items?: TableItem[] } | null)?.items ?? []);
        case "functions": return functionHits(podId, data);
        case "apps": return appHits(podId, data);
    }
}

/** Files the server found. Its search answers passages, so one file can
 *  come back several times: it is one hit. The passage is matched against
 *  too, below anything whose name or path matched: the server found the
 *  words inside it, and a hit dropped for not saying them in its name is a
 *  search quietly returning less. */
export function docHits(podId: string, raw: unknown): Candidate[] {
    const seen = new Set<string>();
    const out: Candidate[] = [];
    for (const item of itemsOf(raw)) {
        const path = String(item.path ?? item.file_path ?? "");
        if (!path || seen.has(path)) continue;
        seen.add(path);
        const passage = typeof item.content === "string" ? item.content : "";
        out.push({
            kind: "doc",
            id: podId + "/" + path,
            /* Called what the files screens call it ("Weekly update"), found
               by its stored name too, which is in the path. */
            title: docTitle(nameOf(item, path.split("/").filter(Boolean).pop() || "Document")),
            subtitle: path,
            haystack: path + " " + passage,
            payload: { podId, open: { kind: "file", path } } satisfies Found,
        });
    }
    return out;
}

function said(value: unknown): string {
    if (value === null || value === undefined) return "";
    return typeof value === "object" ? JSON.stringify(value) : String(value);
}

/** Rows one table answered. Called by its first text column, headed by its
 *  table's name; every text column it was searched on is matched against,
 *  since the row was found by any of them. */
export function recordHits(podId: string, table: RecordSource, rows: readonly Record<string, unknown>[]): Candidate[] {
    return rows.map((row, index) => {
        const id = row.id === undefined || row.id === null ? null : String(row.id);
        const title = said(row[table.displayField ?? ""]) || id || "A row";
        return {
            kind: "record",
            id: `${podId}/${table.tableName}:${id ?? index}`,
            title,
            subtitle: readableName(table.tableName),
            haystack: table.searchFields.map((field) => said(row[field])).join(" "),
            payload: { podId, open: { kind: "record", table: table.tableName, id } } satisfies Found,
        };
    });
}

/** What sample data's record search keeps: a row holding the words in any
 *  of the columns searched, as `ILIKE '%words%'` would. */
export function rowMatches(row: Record<string, unknown>, fields: readonly string[], query: string): boolean {
    const needle = query.trim().toLowerCase();
    if (!needle) return false;
    return fields.some((field) => said(row[field]).toLowerCase().includes(needle));
}

/** Which tables a record search asks, across every Lemma searched, and how
 *  many it leaves out. Records cost a request per table, so the bound is
 *  the whole search's, not each Lemma's; it is shared out a table from
 *  each Lemma in turn, so the first Lemma's tables do not take all of it. */
export function pickRecordTables(
    perLemma: readonly { podId: string; sources: readonly RecordSource[] }[],
    limit: number = MAX_RECORD_TABLES,
): { picked: { podId: string; source: RecordSource }[]; skipped: number } {
    const picked: { podId: string; source: RecordSource }[] = [];
    const total = perLemma.reduce((sum, one) => sum + one.sources.length, 0);
    for (let round = 0; picked.length < limit; round += 1) {
        let any = false;
        for (const one of perLemma) {
            const source = one.sources[round];
            if (!source) continue;
            any = true;
            if (picked.length < limit) picked.push({ podId: one.podId, source });
        }
        if (!any) break;
    }
    return { picked, skipped: total - picked.length };
}

/* ── what the list says ──────────────────────────────────────────── */

const HEADINGS: Record<SearchKind, string> = {
    teammate: "Lemmas",
    conversation: "Chats",
    doc: "Files",
    record: "Records",
    app: "Apps",
    agent: "Agents",
    workflow: "Workflows",
    function: "Functions",
    table: "Tables",
    person: "People",
    schedule: "Schedules",
};

/** A group's heading in the phone's words: the kind, except for rows,
 *  which are headed by their table. */
export function headingOf(group: Pick<HitGroup, "kind" | "label">): string {
    if (group.kind === "record" && group.label && group.label !== KIND_LABEL.record) return group.label;
    return HEADINGS[group.kind];
}

/** A hit's second line, or null when it would say nothing: what it is
 *  about or where it is (never its group's heading again), and which Lemma
 *  it is in when every Lemma is searched ("Support · /pages/Plan.md"). */
export function secondLine(hit: Pick<Hit, "kind" | "subtitle">, group: Pick<HitGroup, "label">, lemmaName: string | null): string | null {
    const own = hit.subtitle && hit.subtitle !== group.label ? hit.subtitle : null;
    if (!lemmaName || hit.kind === "teammate") return own;
    return own ? `${lemmaName} · ${own}` : lemmaName;
}

/** When a chat last moved, for the row's end. */
export function whenOf(found: Found | null, now: number): string | null {
    const at = found?.at ? Date.parse(found.at) : NaN;
    return Number.isFinite(at) ? stampOf(at, now) : null;
}

/** What each source is called when it could not be reached. */
export type SearchPart = "lemmas" | "chats" | "files" | "records" | "apps" | "agents" | "workflows" | "functions" | "tables" | "people" | "schedules";

const PART_ORDER: readonly SearchPart[] = ["lemmas", "chats", "files", "records", "apps", "agents", "workflows", "functions", "tables", "people", "schedules"];

/** How each is said in "Couldn’t reach …". */
const PART_WORDS: Record<SearchPart, string> = {
    lemmas: "your Lemmas",
    chats: "chats",
    files: "files",
    records: "records",
    apps: "apps",
    agents: "agents",
    workflows: "workflows",
    functions: "functions",
    tables: "tables",
    people: "people",
    schedules: "schedules",
};

export interface Failure {
    part: SearchPart;
    /** The Lemma it failed in; null for the list of Lemmas itself. */
    podId: string | null;
}

/** "A, B and C". */
export function joinAnd(words: readonly string[]): string {
    if (words.length <= 1) return words[0] ?? "";
    return words.slice(0, -1).join(", ") + " and " + words[words.length - 1];
}

/** What could not be searched, said rather than left out: a search that
 *  quietly returns less is the one failure nobody can see in its results.
 *  "Couldn’t reach files in Launch and Books; people in Support." Searching
 *  one Lemma, its name goes without saying: "Couldn’t reach files and
 *  people." */
export function failedLine(failures: readonly Failure[], lemmaName: (podId: string) => string, scoped: boolean): string | null {
    const parts = PART_ORDER.filter((part) => failures.some((one) => one.part === part));
    if (parts.length === 0) return null;
    if (scoped) return `Couldn’t reach ${joinAnd(parts.map((part) => PART_WORDS[part]))}.`;
    const said = parts.map((part) => {
        const names = [...new Set(failures.filter((one) => one.part === part && one.podId).map((one) => lemmaName(one.podId!)))];
        return names.length ? `${PART_WORDS[part]} in ${joinAnd(names)}` : PART_WORDS[part];
    });
    return `Couldn’t reach ${said.join("; ")}.`;
}

/** The record search's bound, said where it was applied. */
export function skippedLine(skipped: number): string | null {
    if (skipped <= 0) return null;
    return `${skipped} more ${skipped === 1 ? "table" : "tables"} not searched.`;
}

/** The one quiet line under an empty box: what it finds, and where. */
export function quietLine(scopeName: string | null): string {
    return `Finds chats, files, records, apps, agents, workflows, functions, tables, people and schedules ${scopeName ? `in ${scopeName}` : "in every Lemma"}.`;
}

export function placeholderOf(scopeName: string | null): string {
    return scopeName ? `Search ${scopeName}` : "Search every Lemma";
}

export const NOTHING_MATCHES = "Nothing matches that.";

/** Who "Ask" asks: the Lemma searched, else the one picked, else the first. */
export function askTarget(scope: string | null, picked: string | null, lemmas: readonly Lemma[]): Lemma | null {
    const find = (id: string | null) => (id ? lemmas.find((one) => one.id === id) ?? null : null);
    return find(scope) ?? find(picked) ?? lemmas[0] ?? null;
}

/** A hit read aloud: what it is, and its second line. */
export function hitLabel(hit: Pick<Hit, "title">, second: string | null): string {
    return second ? `${hit.title}, ${second}` : hit.title;
}
