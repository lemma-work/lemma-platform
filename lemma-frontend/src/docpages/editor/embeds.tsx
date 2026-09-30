"use client";

import { useMemo, useState } from "react";
import { Node } from "@tiptap/core";
import { NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from "@tiptap/react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { EmbedPreview } from "@/thread/embed-preview";
import { usePageTools } from "@/docpages/page-context";
import {
    VIEW_OPS, applySpec, blankSpec, buildSql, readView, writeView,
    type Aggregate, type ViewOp, type ViewSpec,
} from "@/docpages/views/spec";
import { ChatIcon, CloseIcon, CodeIcon, PlusIcon, RefreshIcon, TableIcon } from "@/ui/icons";

/** A block whose content is a fenced code block of its own language: plain
 *  markdown on disk, a live thing in the page. */
function fenced(name: string, lang: string, view: (props: ReactNodeViewProps) => React.ReactElement) {
    return Node.create({
        name,
        group: "block",
        atom: true,
        draggable: true,
        selectable: true,
        addAttributes() {
            return { code: { default: "" }, fresh: { default: false, rendered: false } };
        },
        parseHTML() {
            return [{
                tag: "pre",
                priority: 60,
                preserveWhitespace: "full" as const,
                getAttrs: (element) => {
                    const code = (element as HTMLElement).querySelector("code");
                    if (!code || !code.classList.contains("language-" + lang)) return false;
                    return { code: (code.textContent ?? "").replace(/\n$/, "") };
                },
            }];
        },
        renderHTML({ node }) {
            return ["pre", {}, ["code", { class: "language-" + lang }, String(node.attrs.code ?? "")]];
        },
        addStorage() {
            return {
                markdown: {
                    serialize(state: { write: (t: string) => void; text: (t: string, escape?: boolean) => void; ensureNewLine: () => void; closeBlock: (n: unknown) => void }, node: { attrs: { code: string } }) {
                        state.write("```" + lang + "\n");
                        state.text(String(node.attrs.code ?? ""), false);
                        state.ensureNewLine();
                        state.write("```");
                        state.closeBlock(node);
                    },
                    parse: {},
                },
            };
        },
        addNodeView() {
            return ReactNodeViewRenderer(view);
        },
    });
}

/* ── HTML: a widget, drawn in place ────────────────────────────────── */

function WidgetView({ node, updateAttributes, editor, selected }: ReactNodeViewProps) {
    const tools = usePageTools();
    const code = String(node.attrs.code ?? "");
    const [editing, setEditing] = useState(!code.trim() && Boolean(node.attrs.fresh));
    const [draft, setDraft] = useState(code);
    const [asking, setAsking] = useState(false);
    const [ask, setAsk] = useState("");
    const editable = editor.isEditable;
    const firstLine = code.trim().split("\n")[0]?.slice(0, 80) ?? "";

    return (
        /* No frame: a widget is the thing itself, sitting in the page. Its
           controls come with the pointer, in the corner. */
        <NodeViewWrapper className="pblock pblock--widget" data-selected={selected || undefined} data-editing={editing || asking || !code.trim() || undefined}>
            <div className="pblock__bar" contentEditable={false}>
                <span className="pblock__kind"><CodeIcon size={13} /> HTML</span>
                {editable && !editing && (
                    <>
                        {tools?.sendToBot && code.trim() && (
                            <button onClick={() => setAsking((v) => !v)}><ChatIcon size={13} /> Ask {tools.botName} to change it</button>
                        )}
                        <button onClick={() => { setDraft(code); setEditing(true); }}>Edit HTML</button>
                    </>
                )}
                <span className="pblock__grip" data-drag-handle="" aria-hidden="true">⋮⋮</span>
            </div>
            {asking && tools?.sendToBot && (
                <form className="pblock__ask" contentEditable={false} onSubmit={(event) => {
                    event.preventDefault();
                    if (!ask.trim()) return;
                    tools.sendToBot?.("In " + tools.path + ", change the ```lemma-widget block whose HTML begins `" + firstLine + "`: " + ask.trim());
                    setAsk("");
                    setAsking(false);
                }}>
                    <input autoFocus value={ask} onChange={(event) => setAsk(event.target.value)} placeholder="What should change?" />
                    <button type="submit" disabled={!ask.trim()}>Ask</button>
                </form>
            )}
            {editing ? (
                <div className="pblock__editor" contentEditable={false}>
                    <textarea autoFocus spellCheck={false} value={draft} onChange={(event) => setDraft(event.target.value)}
                        placeholder="<div>Paste HTML, or write it here — scripts and styles run in a sandbox.</div>" />
                    <div className="pblock__acts">
                        <button onClick={() => { setEditing(false); setDraft(code); }}>Cancel</button>
                        <button className="cpanel__primary" onClick={() => { updateAttributes({ code: draft }); setEditing(false); }}>Show it</button>
                    </div>
                </div>
            ) : code.trim() ? (
                <div className="pblock__frame" contentEditable={false}>
                    <EmbedPreview title="Embedded HTML" html={code} />
                </div>
            ) : (
                <p className="pblock__empty" contentEditable={false}>An empty HTML block. Edit it, or ask {tools?.botName ?? "the bot"} to draw something here.</p>
            )}
        </NodeViewWrapper>
    );
}

export const WidgetBlock = fenced("widgetBlock", "lemma-widget", WidgetView);

/* ── a live view of a table ───────────────────────────────────────── */

function cell(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (typeof value === "number") return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
    if (typeof value === "boolean") return value ? "Yes" : "No";
    if (typeof value === "object") return JSON.stringify(value);
    const text = String(value);
    return /^\d{4}-\d{2}-\d{2}T/.test(text) ? new Date(text).toLocaleString() : text;
}

function TableViewBlock({ node, updateAttributes, editor, selected }: ReactNodeViewProps) {
    const tools = usePageTools();
    const view = useMemo(() => readView(String(node.attrs.code ?? "")), [node.attrs.code]);
    const [building, setBuilding] = useState(!view.sql && Boolean(node.attrs.fresh));
    const podId = tools?.podId ?? "";
    const rows = useQuery({
        queryKey: ["page-view", podId, view.sql, view.spec ? JSON.stringify(view.spec) : ""],
        enabled: Boolean(podId && view.sql),
        staleTime: 30_000,
        queryFn: async () => {
            if (source.label !== "live" && view.spec) {
                const page = await source.tableRows(podId, view.spec.table);
                return { items: applySpec(page.items, view.spec), truncated: false };
            }
            return source.runQuery(podId, view.sql);
        },
    });
    const columns = view.spec?.columns.length && !view.spec.group
        ? view.spec.columns
        : Object.keys(rows.data?.items[0] ?? {});
    const table = view.spec?.table ?? /\bfrom\s+"?([A-Za-z_][A-Za-z0-9_]*)"?/i.exec(view.sql)?.[1] ?? null;

    return (
        <NodeViewWrapper className="pblock pblock--view" data-selected={selected || undefined}>
            <div className="pblock__bar" contentEditable={false}>
                <span className="pblock__kind"><TableIcon size={13} /> {view.title || (table ? table : "Table view")}</span>
                {rows.data && <span className="pblock__meta">{rows.data.items.length}{rows.data.truncated ? "+" : ""} rows</span>}
                <button onClick={() => void rows.refetch()} title="Refresh"><RefreshIcon size={13} /></button>
                {table && tools?.openTable && <button onClick={() => tools.openTable?.(table)}>Open table</button>}
                {editor.isEditable && <button onClick={() => setBuilding((v) => !v)}>{building ? "Close" : "Edit view"}</button>}
                <span className="pblock__grip" data-drag-handle="" aria-hidden="true">⋮⋮</span>
            </div>
            {building && (
                <Builder podId={podId} initial={view} onApply={(next) => { updateAttributes({ code: writeView(next) }); setBuilding(false); }} />
            )}
            {!view.sql ? (
                !building && <p className="pblock__empty" contentEditable={false}>No table chosen yet. Edit the view to pick one.</p>
            ) : rows.isPending ? (
                <p className="pblock__empty" contentEditable={false}>Loading rows…</p>
            ) : rows.isError ? (
                <p className="pblock__empty pblock__empty--bad" contentEditable={false}>
                    {rows.error instanceof Error ? rows.error.message : "The query did not run."}
                </p>
            ) : rows.data.items.length === 0 ? (
                <p className="pblock__empty" contentEditable={false}>No rows match.</p>
            ) : (
                <div className="pblock__table" contentEditable={false}>
                    <table>
                        <thead><tr>{columns.map((column) => <th key={column}>{column.replace(/_/g, " ")}</th>)}</tr></thead>
                        <tbody>
                            {rows.data.items.map((row, at) => (
                                <tr key={String(row.id ?? at)}>
                                    {columns.map((column) => <td key={column}>{cell(row[column])}</td>)}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </NodeViewWrapper>
    );
}

function Builder({ podId, initial, onApply }: {
    podId: string;
    initial: { title: string; spec: ViewSpec | null; sql: string };
    onApply: (next: { title: string; spec: ViewSpec | null; sql: string }) => void;
}) {
    const [title, setTitle] = useState(initial.title);
    const [mode, setMode] = useState<"build" | "sql">(initial.sql && !initial.spec ? "sql" : "build");
    const [spec, setSpec] = useState<ViewSpec | null>(initial.spec);
    const [sql, setSql] = useState(initial.sql);
    const tables = useQuery({
        queryKey: ["library", podId, "tables", "view-builder"],
        queryFn: async () => (await source.listLibrary(podId, "tables", "/")).items.map((one) => one.name),
        staleTime: 60_000,
    });
    const columns = useQuery({
        queryKey: ["table-columns", podId, spec?.table],
        enabled: Boolean(spec?.table),
        queryFn: async () => (await source.tableColumns(podId, spec!.table)).map((one) => one.name),
        staleTime: 60_000,
    });
    const names = columns.data ?? [];
    const patch = (next: Partial<ViewSpec>) => setSpec((was) => (was ? { ...was, ...next } : was));
    let built = "";
    try { built = spec ? buildSql(spec) : ""; } catch { built = ""; }

    return (
        <div className="vbuild" contentEditable={false}>
            <div className="vbuild__row">
                <input className="vbuild__title" value={title} placeholder="Title (optional)" onChange={(event) => setTitle(event.target.value)} />
                <div className="vbuild__modes" role="tablist">
                    <button role="tab" aria-selected={mode === "build"} onClick={() => setMode("build")}>Build</button>
                    <button role="tab" aria-selected={mode === "sql"} onClick={() => { if (built) setSql(built); setMode("sql"); }}>SQL</button>
                </div>
            </div>

            {mode === "build" ? (
                <>
                    <label className="vbuild__field">
                        <span>Table</span>
                        <select value={spec?.table ?? ""} onChange={(event) => setSpec(event.target.value ? blankSpec(event.target.value) : null)}>
                            <option value="">Choose a table…</option>
                            {(tables.data ?? []).map((name) => <option key={name} value={name}>{name}</option>)}
                        </select>
                    </label>
                    {spec && (
                        <>
                            <div className="vbuild__field">
                                <span>Show</span>
                                <div className="vbuild__chips">
                                    {names.map((name) => (
                                        <button key={name} aria-pressed={spec.columns.includes(name)} onClick={() => patch({ columns: spec.columns.includes(name) ? spec.columns.filter((one) => one !== name) : [...spec.columns, name] })}>
                                            {name}
                                        </button>
                                    ))}
                                    {names.length > 0 && <small>{spec.columns.length ? "" : "all columns"}</small>}
                                </div>
                            </div>
                            <div className="vbuild__field">
                                <span>Where</span>
                                <div className="vbuild__stack">
                                    {spec.filters.map((one, at) => (
                                        <div key={at} className="vbuild__filter">
                                            <select value={one.column} onChange={(event) => patch({ filters: spec.filters.map((f, i) => (i === at ? { ...f, column: event.target.value } : f)) })}>
                                                {names.map((name) => <option key={name} value={name}>{name}</option>)}
                                            </select>
                                            <select value={one.op} onChange={(event) => patch({ filters: spec.filters.map((f, i) => (i === at ? { ...f, op: event.target.value as ViewOp } : f)) })}>
                                                {VIEW_OPS.map((op) => <option key={op} value={op}>{op}</option>)}
                                            </select>
                                            {one.op !== "empty" && one.op !== "not empty" && (
                                                <input value={one.value} onChange={(event) => patch({ filters: spec.filters.map((f, i) => (i === at ? { ...f, value: event.target.value } : f)) })} />
                                            )}
                                            <button aria-label="Remove filter" onClick={() => patch({ filters: spec.filters.filter((_, i) => i !== at) })}><CloseIcon size={12} /></button>
                                        </div>
                                    ))}
                                    <button className="vbuild__add" disabled={!names.length} onClick={() => patch({ filters: [...spec.filters, { column: names[0], op: "=", value: "" }] })}>
                                        <PlusIcon size={12} /> Filter
                                    </button>
                                </div>
                            </div>
                            <div className="vbuild__field">
                                <span>Group</span>
                                <div className="vbuild__filter">
                                    <select value={spec.group?.column ?? ""} onChange={(event) => patch({ group: event.target.value ? { column: event.target.value, aggregate: spec.group?.aggregate ?? "count", of: spec.group?.of ?? null } : null })}>
                                        <option value="">No grouping</option>
                                        {names.map((name) => <option key={name} value={name}>by {name}</option>)}
                                    </select>
                                    {spec.group && (
                                        <>
                                            <select value={spec.group.aggregate} onChange={(event) => patch({ group: { ...spec.group!, aggregate: event.target.value as Aggregate } })}>
                                                {["count", "sum", "avg", "min", "max"].map((one) => <option key={one} value={one}>{one}</option>)}
                                            </select>
                                            {spec.group.aggregate !== "count" && (
                                                <select value={spec.group.of ?? ""} onChange={(event) => patch({ group: { ...spec.group!, of: event.target.value || null } })}>
                                                    <option value="">of…</option>
                                                    {names.map((name) => <option key={name} value={name}>{name}</option>)}
                                                </select>
                                            )}
                                        </>
                                    )}
                                </div>
                            </div>
                            {!spec.group && (
                                <div className="vbuild__field">
                                    <span>Sort</span>
                                    <div className="vbuild__filter">
                                        <select value={spec.sort?.column ?? ""} onChange={(event) => patch({ sort: event.target.value ? { column: event.target.value, dir: spec.sort?.dir ?? "desc" } : null })}>
                                            <option value="">As stored</option>
                                            {names.map((name) => <option key={name} value={name}>{name}</option>)}
                                        </select>
                                        {spec.sort && (
                                            <select value={spec.sort.dir} onChange={(event) => patch({ sort: { ...spec.sort!, dir: event.target.value as "asc" | "desc" } })}>
                                                <option value="desc">newest / highest first</option>
                                                <option value="asc">oldest / lowest first</option>
                                            </select>
                                        )}
                                    </div>
                                </div>
                            )}
                            <label className="vbuild__field">
                                <span>Rows</span>
                                <input type="number" min={1} max={1000} value={spec.limit} onChange={(event) => patch({ limit: Number(event.target.value) || 50 })} />
                            </label>
                        </>
                    )}
                </>
            ) : (
                <textarea className="vbuild__sql" spellCheck={false} value={sql} onChange={(event) => setSql(event.target.value)}
                    placeholder={'SELECT … FROM "table" — joins across tables work; row access is yours.'} />
            )}

            <div className="pblock__acts">
                <button
                    className="cpanel__primary"
                    disabled={mode === "build" ? !built : !sql.trim()}
                    onClick={() => onApply(mode === "build" ? { title, spec, sql: built } : { title, spec: sql.trim() === built ? spec : null, sql })}
                >
                    Show view
                </button>
            </div>
        </div>
    );
}

export const ViewBlock = fenced("viewBlock", "lemma-view", TableViewBlock);
