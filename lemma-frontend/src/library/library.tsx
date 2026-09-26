import { useEffect, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { source, type LibraryItem } from "@/data";
import { FileIcon, FolderIcon, TableIcon, BackIcon, SearchIcon, ChevronRightIcon, PlusIcon, AttachIcon, CloseIcon } from "@/ui/icons";
import { fileLocations, inLocation, parentFolder, type FileLocation } from "./file-locations";
import { Modal } from "@/shell/modal";
import { ConfirmDelete, useLibraryWrites } from "./library-writes";
import { fileReading } from "./file-status";
import { RecordEditor } from "./record-editor";
import { cellText, idOf, rowLabel, withRow, withUpdatedRow, withoutRow, type Row, type RowPages } from "./record-cache";
import { lemma } from "@/session/client";
import { useQueryClient } from "@tanstack/react-query";
import { chooseForm, FORM_NAMES, orderedFields, profileTable } from "./profile";
import { readableName } from "./reading";
import { isAsking, matching, NO_FILTERS, ordered as inOrder, type Filters, type Ordering } from "./filters";
import { FilterBar } from "./filter-bar";
import { TableForm } from "./forms";

/** How many rows this view will read in full to decide what a table is.
 *
 *  Above it the grid answers immediately and nothing extra is fetched. The
 *  datastore caps a query at a thousand rows anyway, so a table past this is a
 *  table nothing here can see whole. */
const WHOLE = 1000;

/** Rows the grid draws at once.
 *
 *  The worst case in the app: a thousand rows read whole, times twenty-three
 *  columns, is twenty-three thousand cells in one table element and a tab that
 *  stops answering. Reading the table whole is what lets the view choose a
 *  shape from all of it; drawing it whole was never needed for anything. */
const DRAWN = 150;

/** The columns to profile and to show: what the table declares, or — when it
 *  declares nothing — whatever keys the rows are found to carry. */
function fieldsOf(declared: { name: string; system?: boolean }[] | undefined, rows: Row[]) {
    return declared ?? Array.from(new Set(rows.flatMap(Object.keys))).map(name => ({ name }));
}

const isInternal = (item: LibraryItem) => item.path.split("/").some(part => part.startsWith(".") || ["node_modules", "__pycache__", "system"].includes(part));

export function Library({ podId, onFile, onTable }: { podId: string; onFile: (path: string) => void; onTable: (name: string) => void }) {
    const [filter, setFilter] = useState<FileLocation | "tables">("shared");
    const location: FileLocation = filter === "tables" ? "shared" : filter;
    const currentLocation = fileLocations[location];
    const [search, setSearch] = useState("");
    const [directory, setDirectory] = useState("/");
    const [internal, setInternal] = useState(false);
    /* Writes are for files. A table is a schema with rows in it, not something
       you rename by typing over its name here. */
    const writes = useLibraryWrites(podId, directory);
    const picker = useRef<HTMLInputElement | null>(null);
    const [newFolder, setNewFolder] = useState<string | null>(null);
    const [renaming, setRenaming] = useState<{ path: string; draft: string } | null>(null);
    const [deleting, setDeleting] = useState<LibraryItem | null>(null);
    /* The reason a file could not be read, for the one row that asked. */
    const [why, setWhy] = useState<{ path: string; text: string | null } | null>(null);
    const writable = filter !== "tables";
    const files = useInfiniteQuery({ queryKey: ["library", podId, "files", directory], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => source.listLibrary(podId, "files", directory, pageParam), getNextPageParam: page => page.next || undefined, staleTime: 60_000, enabled: filter !== "tables" });
    const tables = useInfiniteQuery({ queryKey: ["library", podId, "tables"], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => source.listLibrary(podId, "tables", "/", pageParam), getNextPageParam: page => page.next || undefined, staleTime: 60_000, enabled: filter === "tables" });
    const shownQueries = filter === "tables" ? [tables] : [files];
    const items = shownQueries.flatMap(q => q.data?.pages.flatMap(p => p.items) ?? []).filter(item => (item.kind === "table" || inLocation(item.path, location)) && (internal || !isInternal(item)) && `${item.name} ${item.kind === "table" ? readableName(item.name) : ""} ${item.detail}`.toLowerCase().includes(search.toLowerCase())).sort((a,b) => Number(b.kind === "folder") - Number(a.kind === "folder") || b.updated.localeCompare(a.updated));
    return <section className="library-view" aria-label="Library">
        <header className="library-heading"><div><h1>Library</h1><p>Files, knowledge, and data in one place.</p></div>
            {writable ? <div className="library-actions">
                <input ref={picker} type="file" multiple className="library-picker" tabIndex={-1} aria-hidden="true" onChange={e => { void writes.upload(Array.from(e.target.files ?? [])); e.target.value = ""; }}/>
                <button className="btn" onClick={() => picker.current?.click()} disabled={Boolean(writes.busy)}><AttachIcon size={16}/>Upload</button>
                <button className="btn" onClick={() => { setNewFolder(""); writes.clearProblem(); }} disabled={Boolean(writes.busy)}><PlusIcon size={16}/>New folder</button>
            </div> : <span className="library-readonly">Read only</span>}</header>
        <div className="library-controls"><div className="library-filters" aria-label="Resource type">{(["shared", "personal", "skills", "tables"] as const).map(type => <button key={type} aria-pressed={filter === type} onClick={() => { setFilter(type); setDirectory(type === "tables" ? "/" : fileLocations[type].root); setSearch(""); }}>{type === "tables" ? "Tables" : fileLocations[type].label}</button>)}</div><label className="library-search"><SearchIcon size={17}/><input aria-label="Search loaded items" placeholder="Search loaded items…" value={search} onChange={e => setSearch(e.target.value)}/></label></div>
        <p className="library-location-description">{filter === "tables" ? "Tables you can access with this teammate." : currentLocation.description}</p>
        <div className="library-context"><div>{filter !== "tables" && <><button disabled={directory === currentLocation.root} aria-label="Parent folder" onClick={() => setDirectory(parentFolder(directory, location))}><BackIcon size={16}/></button><span>{directory === currentLocation.root ? currentLocation.label : currentLocation.label + " / " + directory.slice(currentLocation.root === "/" ? 1 : currentLocation.root.length + 1)}</span></>}</div><label><input type="checkbox" checked={internal} onChange={e => setInternal(e.target.checked)}/> Include internal items</label></div>
        {newFolder !== null && <form className="library-new" onSubmit={async e => {
            e.preventDefault();
            if (await writes.createFolder(newFolder)) setNewFolder(null);
        }}>
            <input autoFocus aria-label="Folder name" placeholder="Folder name" value={newFolder} onChange={e => setNewFolder(e.target.value)} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); setNewFolder(null); writes.clearProblem(); } }}/>
            <button className="btn btn--primary" type="submit" disabled={Boolean(writes.busy)}>Create</button>
            <button className="btn" type="button" onClick={() => { setNewFolder(null); writes.clearProblem(); }}>Cancel</button>
        </form>}
        {writes.problem && <p className="library-problem" role="alert">{writes.problem}<button aria-label="Dismiss" onClick={writes.clearProblem}><CloseIcon size={13}/></button></p>}
        {writes.busy && <p role="status" className="library-busy">Working on {writes.busy}…</p>}
        {shownQueries.some(q => q.isPending) && <p role="status">Loading resources…</p>}
        {shownQueries.map((q, i) => q.isError && <p role="alert" key={i}>Could not load {q === files ? "files" : "tables"}. <button onClick={() => void q.refetch()}>Retry</button></p>)}
        {/* A row is a div holding an open button and its actions, rather than
            one big button, which is tidier and cannot hold the others: a button
            inside a button is invalid markup and the browser unnests it, which
            is how "rename" ends up opening the file. */}
        <div className="library-list">{items.map(item => { const reading = item.kind === "file" ? fileReading(item.status) : null; return <div className={"library-row" + (reading?.state === "failed" ? " library-row--failed" : "")} key={item.kind + item.id}>
            {renaming?.path === item.path ? <form className="library-rename" onSubmit={async e => {
                e.preventDefault();
                if (await writes.rename(item, renaming.draft)) setRenaming(null);
            }}>
                <span className="library-item-icon">{item.kind === "folder" ? <FolderIcon size={21}/> : <FileIcon size={21}/>}</span>
                <input autoFocus aria-label={"Rename " + item.name} value={renaming.draft} onChange={e => setRenaming({ path: item.path, draft: e.target.value })} onKeyDown={e => { if (e.key === "Escape") { e.preventDefault(); setRenaming(null); writes.clearProblem(); } }}/>
                <button className="btn btn--primary" type="submit" disabled={Boolean(writes.busy)}>Save</button>
                <button className="btn" type="button" onClick={() => { setRenaming(null); writes.clearProblem(); }}>Cancel</button>
            </form> : <>
                <button className="library-item" onClick={() => { if (item.kind === "folder") { setDirectory(item.path); setSearch(""); } else if (item.kind === "table") onTable(item.path); else onFile(item.path); }}>
                    <span className="library-item-icon">{item.kind === "table" ? <TableIcon size={21}/> : item.kind === "folder" ? <FolderIcon size={21}/> : <FileIcon size={21}/>}</span><span className="library-item-name"><strong>{item.kind === "table" ? readableName(item.name) : item.name}</strong><small>{item.kind === "folder" ? (location === "skills" ? "Skill · open instructions and resources" : "Folder") : item.detail}{reading && <> · <span className={"library-reading library-reading--" + reading.state}>{reading.label}</span></>}</small></span><span className="library-item-date">{new Date(item.updated).toLocaleDateString(undefined, { month: "short", day: "numeric" })}</span><ChevronRightIcon size={16}/>
                </button>
                {writable && <span className="library-row-actions">
                    {reading?.state === "failed" && <>
                        <button aria-expanded={why?.path === item.path} title={"Why " + item.name + " could not be read"} onClick={async () => { if (why?.path === item.path) { setWhy(null); return; } setWhy({ path: item.path, text: null }); const text = await writes.explain(item); setWhy(was => was?.path === item.path ? { path: item.path, text } : was); }}>Why?</button>
                        <button title={"Read " + item.name + " again"} aria-label={"Retry reading " + item.name} disabled={writes.busy === item.path} onClick={() => { setWhy(null); void writes.retry(item); }}>Retry</button>
                    </>}
                    <button title={"Rename " + item.name} aria-label={"Rename " + item.name} onClick={() => { setRenaming({ path: item.path, draft: item.name }); writes.clearProblem(); }}>Rename</button>
                    <button title={"Delete " + item.name} aria-label={"Delete " + item.name} onClick={() => { setDeleting(item); writes.clearProblem(); }}>Delete</button>
                </span>}
            </>}
            {deleting?.path === item.path && <ConfirmDelete item={item} onCancel={() => setDeleting(null)} onConfirm={() => { setDeleting(null); void writes.remove(item); }}/>}
            {why?.path === item.path && reading?.state === "failed" && <p className="library-row-why" role="status">{why.text ?? "Checking…"}</p>}
        </div>; })}</div>
        {!items.length && shownQueries.every(q => !q.isPending && !q.isError) && <p className="library-empty">{search ? "No matching loaded items. Try another search or load more." : "No visible items here."}</p>}
        <footer className="library-footer"><span>{items.length} items loaded · folders first, then recently updated</span>{shownQueries.map((q,i) => q.hasNextPage && <button key={i} disabled={q.isFetchingNextPage} onClick={() => void q.fetchNextPage()}>Load more {q === files ? "files" : "tables"}</button>)}</footer>
    </section>;
}

export function TableView({ podId, name, onOpenRecord }: {
    podId: string;
    name: string;
    /** Open one row on its own page. */
    onOpenRecord?: (tableName: string, recordId: string) => void;
}) {
    const cache = useQueryClient();
    const sample = source.label === "sample";
    /* The primary key is the table's to declare. Assuming `id` is how a delete
       ends up aimed at nothing, or an update writes over a different row. */
    const detail = useQuery({
        queryKey: ["table", podId, name, "detail"],
        queryFn: () => lemma(podId).tables.get(name),
        enabled: !sample,
        staleTime: 300_000,
    });
    const primaryKey = (detail.data as { primary_key_column?: string } | undefined)?.primary_key_column ?? "id";
    const rowsKey = ["table", podId, name, "rows"];
    const [editing, setEditing] = useState<{ row: Row | null; preset?: Row } | null>(null);
    const [removing, setRemoving] = useState<Row | null>(null);
    const [writeProblem, setWriteProblem] = useState<string | null>(null);

    /** One field, changed where it is.
     *
     *  Ticking a task and moving a card to the next pile are the two edits that
     *  have to happen without a dialog: a checklist you cannot tick is a
     *  picture of a checklist, and a board where the only way to move a card is
     *  to open a form and retype the stage is a board for looking at. The write
     *  is the same shape as the delete above it — the screen changes first, the
     *  server is told, and if the server disagrees the screen goes back. */
    async function writeField(row: Row, changes: Row) {
        const id = idOf(row, primaryKey);
        if (!id) { setWriteProblem("This row has no " + primaryKey + ", so there is no way to save a change to it."); return; }
        const before = cache.getQueryData<RowPages>(rowsKey);
        setWriteProblem(null);
        cache.setQueryData<RowPages>(rowsKey, (pages) => withUpdatedRow(pages, primaryKey, id, changes) as RowPages);
        try {
            if (!sample) await lemma(podId).records.update(name, id, changes);
        } catch (failure) {
            cache.setQueryData(rowsKey, before);
            setWriteProblem(failure instanceof Error ? failure.message : "That change was not saved — it has been put back.");
        }
    }

    async function removeRow(row: Row) {
        const id = idOf(row, primaryKey);
        if (!id) { setWriteProblem("This row has no " + primaryKey + ", so there is no way to delete it from here."); return; }
        const before = cache.getQueryData<RowPages>(rowsKey);
        setRemoving(null);
        setWriteProblem(null);
        cache.setQueryData<RowPages>(rowsKey, (pages) => withoutRow(pages, primaryKey, id) as RowPages);
        try {
            if (!sample) await lemma(podId).records.delete(name, id);
        } catch (failure) {
            cache.setQueryData(rowsKey, before);
            setWriteProblem(failure instanceof Error ? failure.message : "That row was not deleted — it has been put back.");
        }
    }

    const columns = useQuery({ queryKey: ["table", podId, name, "columns"], queryFn: () => source.tableColumns(podId, name), staleTime: 300_000 });
    const rows = useInfiniteQuery({ queryKey: ["table", podId, name, "rows"], initialPageParam: undefined as string | undefined, queryFn: ({ pageParam }) => source.tableRows(podId, name, pageParam), getNextPageParam: page => page.next || undefined, staleTime: 60_000 });
    /* Asked once, up front. Without it there is no telling a table of twenty-six
       things from the first page of four thousand, and the two want opposite
       screens. A pod that will not answer returns null, and everything below
       falls back to what this view has always done. */
    const total = useQuery({ queryKey: ["table", podId, name, "count"], queryFn: () => source.tableCount(podId, name), staleTime: 60_000 });
    const [search, setSearch] = useState("");
    const [chosen, setChosen] = useState<string[] | null>(null);
    const [selected, setSelected] = useState<Record<string, unknown> | null>(null);
    const [asGrid, setAsGrid] = useState(false);
    const [drawn, setDrawn] = useState(DRAWN);
    const [filters, setFilters] = useState<Filters>(NO_FILTERS);
    const [order, setOrder] = useState<Ordering | null>(null);
    /* Expanded, by both means at once.
     *
     *  A board of twenty-five cards in three piles is the case this view is
     *  worst at: inside the shell it gets about half the width, so most of the
     *  pile is below the fold and two columns are off the side.
     *
     *  Real fullscreen is the better answer where it is allowed, and it is not
     *  always allowed: an app embedded in an iframe without `allow="fullscreen"`
     *  is refused, and the desktop browser pane this was built in ignores the
     *  request outright — no error, no fullscreen, nothing to catch. A feature
     *  that silently does nothing in the place it will most often be used is
     *  not a feature, so the class that fills the window goes on regardless and
     *  the request is made on top of it. Where it is honoured the view gets the
     *  whole display; where it is not, it still gets the whole window. */
    const view = useRef<HTMLElement | null>(null);
    const [full, setFull] = useState(false);
    useEffect(() => {
        if (!full) return;
        /* Escape leaves real fullscreen on its own, and has to be made to leave
           the fallback, or the way out is a button the browser has just hidden. */
        const key = (event: KeyboardEvent) => { if (event.key === "Escape") setFull(false); };
        const left = () => { if (!document.fullscreenElement) setFull(false); };
        document.addEventListener("keydown", key);
        document.addEventListener("fullscreenchange", left);
        return () => { document.removeEventListener("keydown", key); document.removeEventListener("fullscreenchange", left); };
    }, [full]);
    const allRows = rows.data?.pages.flatMap(p => p.items) ?? [];

    /* Small enough to read whole, so read it whole: a shape is chosen from the
       rows, and a shape chosen from the first fifty of two hundred is a
       different shape fifty rows later. Above the cap nothing is fetched and
       the grid is the answer immediately. */
    const countable = total.isFetched ? total.data : undefined;
    const readWhole = countable !== null && countable !== undefined && countable <= WHOLE;
    useEffect(() => {
        if (readWhole && rows.hasNextPage && !rows.isFetchingNextPage) void rows.fetchNextPage();
    }, [readWhole, rows.hasNextPage, rows.isFetchingNextPage, rows]);

    const complete = countable !== null && countable !== undefined ? allRows.length >= countable : !rows.hasNextPage;
    /* Nothing is drawn until the answer cannot change. One "Loading table…" is
       cheaper to read than a grid that turns into a chart while somebody is
       looking at it. */
    const settled = countable === null || countable === undefined ? !rows.hasNextPage : (complete || !readWhole);
    const profile = settled && allRows.length ? profileTable(fieldsOf(columns.data, allRows), allRows, { primaryKey, complete }) : null;
    const choice = profile ? chooseForm(profile) : null;
    const shaped = choice && choice.form !== "grid" && !asGrid ? choice : null;
    const fields = fieldsOf(columns.data, allRows);
    /* Every column, ranked rather than cut.
       A grid that silently drops seven columns is not somewhere anybody can go
       to see what the table holds, and holding all of it is the whole job the
       grid is left doing once the shapes above it are the opinionated view. So
       the ranking decides the order and nothing decides the count; the picker
       is there for anyone who wants fewer. */
    const ranked = profile ? orderedFields(profile).map(c => c.name) : null;
    const visible = chosen ?? ranked ?? fields.map(f => f.name);
    /* Shared: the grid tags enum cells with it, and the sort needs it to know
       that a date is a date rather than the string it is stored in. */
    const roles = new Map((profile?.columns ?? []).map(c => [c.name, c.role] as const));
    /* One thing narrows this table. A search box filtering separately from the
       filters would be two independent answers to "which rows am I looking
       at", and a footer that can only count one of them. */
    const asked = { ...filters, text: search };
    const matches = inOrder(matching(allRows, asked), order, roles);
    /* Searching is its own reason to start over from the top: the rows that
       match are not the rows that were on screen. */
    const onScreen = matches.slice(0, drawn);
    const undrawn = matches.length - onScreen.length;
    return <section className={"library-view table-view" + (full ? " is-expanded" : "")} ref={view}><header className="library-heading"><div><h1>{readableName(name)}</h1><p>{allRows.length} rows loaded</p></div>
        {/* Values are this button; the table's *shape* is the Ask action in the
            view toolbar. Two buttons for the second thing, one here and one up
            there, would be two places to change the wording and two chances for
            them to disagree. */}
        <div className="library-actions"><button className="btn" onClick={() => { setEditing({ row: null }); setWriteProblem(null); }}><PlusIcon size={16}/>New row</button></div></header>
        <div className="library-controls"><label className="library-search"><SearchIcon size={17}/><input aria-label="Search loaded rows" placeholder="Search loaded rows…" value={search} onChange={e => setSearch(e.target.value)}/></label><details className="library-columns"><summary>Columns ({visible.length})</summary><div>{fields.map(f => <label key={f.name}><input type="checkbox" checked={visible.includes(f.name)} onChange={e => setChosen(e.target.checked ? [...visible, f.name] : visible.filter(n => n !== f.name))}/>{f.name}</label>)}</div></details></div>
        {/* Narrowing the table and knowing what shape it is in are one strip.
            `choice` exists exactly when `profile` does, so nothing here can
            draw a bar with only half of itself in it. */}
        {profile && choice && settled && <FilterBar
            profile={profile}
            filters={filters}
            onFilters={next => { setFilters(next); setDrawn(DRAWN); }}
            order={order}
            onOrder={next => { setOrder(next); setDrawn(DRAWN); }}
            note={shaped ? shaped.because : choice.form === "grid" ? choice.because : "Table view."}
            actions={<>
                {choice.form !== "grid" && <button onClick={() => setAsGrid(!asGrid)}>
                    {asGrid ? "Back to " + FORM_NAMES[choice.form] : "Show as table"}
                </button>}
                <button onClick={() => {
                    if (full) {
                        setFull(false);
                        if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
                        return;
                    }
                    setFull(true);
                    /* Ignored on purpose. The class above has already done the
                       useful part; this only upgrades it to the whole display
                       where the browser permits, and a refusal is not worth
                       telling anyone about. */
                    void view.current?.requestFullscreen?.().catch(() => {});
                }}>{full ? "Exit full screen" : "Full screen"}</button>
            </>}
        />}
        {writeProblem && <p className="library-problem" role="alert">{writeProblem}<button aria-label="Dismiss" onClick={() => setWriteProblem(null)}><CloseIcon size={13}/></button></p>}
        {(rows.isPending || columns.isPending || !settled) && <p role="status">Loading table…</p>}
        {(rows.isError || columns.isError) && <p role="alert">Some of this table did not load. <button onClick={() => { void rows.refetch(); void columns.refetch(); }}>Retry</button></p>}
        {shaped && profile && <TableForm
            rows={matches}
            profile={profile}
            choice={shaped}
            primaryKey={primaryKey}
            onOpen={row => {
                const id = idOf(row, primaryKey);
                if (onOpenRecord && id) onOpenRecord(name, id); else setSelected(row);
            }}
            onEdit={row => { setEditing({ row }); setWriteProblem(null); }}
            onChange={(row, changes) => void writeField(row, changes)}
            onAdd={preset => { setEditing({ row: null, preset }); setWriteProblem(null); }}
        />}
        {!shaped && <div className="library-grid"><table><thead><tr>{visible.map(field => <th key={field}>{field}</th>)}<th>Row</th></tr></thead><tbody>{onScreen.map((row,i) => <tr key={i}>
            {visible.map(field => roles.get(field) === "enum"
                ? <td key={field}><span className="form-tag">{cellText(row[field])}</span></td>
                : <td key={field} title={cellText(row[field])}>{cellText(row[field])}</td>)}
            {/* Last, not first. The three controls were the widest thing on the
                row and the first thing read, ahead of anything the row is. */}
            <td className="row-actions">
                {/* The dialog was the only way to see a whole row. It still is
                    for a quick look; the arrow now goes to the row's own page,
                    which is where what it connects to lives. */}
                <button onClick={() => {
                    const id = idOf(row, primaryKey);
                    if (onOpenRecord && id) onOpenRecord(name, id); else setSelected(row);
                }} aria-label={`Open row ${i + 1}`}>{i + 1} ↗</button>
                <button onClick={() => { setEditing({ row }); setWriteProblem(null); }} aria-label={`Edit row ${i + 1}`}>Edit</button>
                <button onClick={() => { setRemoving(row); setWriteProblem(null); }} aria-label={`Delete row ${i + 1}`}>Delete</button>
            </td>
            </tr>)}</tbody></table></div>}
        {!rows.isPending && !rows.isError && !matches.length && <p className="library-empty">{isAsking(asked) ? "Nothing here matches what you asked for." : "This table has no rows yet."}</p>}
        {/* Three different things to say, and one sentence each. Saying all of
    them at once — "12 of 22 loaded rows · 4093 in the table" — asks the reader
    to work out which number is the one they wanted. */}
        <footer className="library-footer"><span>{
            isAsking(asked) ? matches.length + " of " + allRows.length + " rows match"
            : typeof countable === "number" && countable > allRows.length ? allRows.length + " of " + countable + " rows loaded"
            : allRows.length === 1 ? "1 row" : allRows.length + " rows"
        }</span>
        {!shaped && undrawn > 0 && <button onClick={() => setDrawn(d => d + DRAWN)}>Show more ({undrawn} left)</button>}
        {rows.hasNextPage && <button disabled={rows.isFetchingNextPage} onClick={() => void rows.fetchNextPage()}>{rows.isFetchingNextPage ? "Loading…" : "Load more"}</button>}</footer>
        {selected && <Modal title="Row details" subtitle={readableName(name)} onClose={() => setSelected(null)}><dl className="library-row-details">{Object.entries(selected).map(([key,value]) => <div key={key}><dt>{key}</dt><dd>{cellText(value)}</dd></div>)}</dl></Modal>}
        {editing && !sample && <RecordEditor
            podId={podId}
            tableName={name}
            primaryKey={primaryKey}
            row={editing.row}
            preset={editing.preset}
            profile={profile}
            onClose={() => setEditing(null)}
            onSaved={(record, mode) => {
                /* Patched rather than invalidated: the rows are an infinite
                   query, and refetching every page somebody has scrolled
                   through to show one changed cell is the expensive way to be
                   correct. */
                cache.setQueryData<RowPages>(rowsKey, (pages) => (mode === "create"
                    ? withRow(pages, record)
                    : withUpdatedRow(pages, primaryKey, String(editing.row?.[primaryKey] ?? ""), record)) as RowPages);
            }}
        />}
        {editing && sample && <Modal title="New row" subtitle={readableName(name)} onClose={() => setEditing(null)}>
            <p role="status">This is the sample source — connect a session to write anything.</p>
        </Modal>}
        {removing && <Modal title="Delete row" subtitle={readableName(name)} narrow onClose={() => setRemoving(null)}>
            <p>Delete <strong>{rowLabel(removing, primaryKey)}</strong>? The teammate loses it too, and this cannot be undone from here.</p>
            <div className="record-form__actions">
                <button className="btn btn--danger" onClick={() => void removeRow(removing)}>Delete</button>
                <button className="btn" onClick={() => setRemoving(null)}>Keep it</button>
            </div>
        </Modal>}
    </section>;
}
