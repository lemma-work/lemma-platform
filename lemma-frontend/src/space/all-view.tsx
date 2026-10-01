"use client";

import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { source, type LibraryItem, type Member, type SpaceView, type Tab } from "@/data";
import { AppIcon, ChevronDownIcon, FileIcon, GlobeIcon, LibraryIcon, LockIcon, PeopleIcon, PlusIcon, SearchIcon, TableIcon, UploadIcon } from "@/ui/icons";
import { isDoc } from "@/docs/doc-space";
import { useQueryClient } from "@tanstack/react-query";
import { PAGE_TEMPLATES, makePage, type PageTemplate } from "@/docpages/templates";
import { AppsIcon as GridIcon, CheckCircleIcon, MenuIcon as ListIcon, SparkleIcon, TextIcon, UsageIcon } from "@/ui/icons";
import { docTitle, fileKind } from "@/library/doc-title";
import { useMaking } from "./making";
import { key } from "@/session/storage";
import { useLibraryWrites } from "@/library/library-writes";
import { emptyFor, type EmptyPlace } from "./empty-copy";
import { SpaceEmpty, type EmptyHandlers } from "./empty-state";
import { AppIdeas } from "@/stage/apps";
import { guideTitle } from "@/tour/guides";

/** One row, whatever it is: a page, an app, a table or a file. */
type Row = {
    key: string;
    kind: "page" | "app" | "table" | "file" | "folder";
    name: string;
    /** The stored file name, shown on hover under the title made from it. */
    file?: string;
    /** Where a file is, for reading a page's first lines into its card. */
    path?: string;
    detail: string;
    updated: string | null;
    access: Access;
    rls?: boolean;
    open: () => void;
};

/** Who can open a row, read from what the platform records rather than
 *  assumed. `space` is everyone in it; `rows` is a table everyone opens but
 *  where each person sees only their own rows. */
type Access = "space" | "personal" | "restricted" | "public" | "rows";

function accessOf(visibility: string | undefined, rls?: boolean): Access {
    const said = (visibility ?? "").toUpperCase();
    if (said === "PUBLIC") return "public";
    if (said === "PERSONAL") return "personal";
    if (said === "RESTRICTED") return "restricted";
    if (rls) return "rows";
    return "space";
}

function Faces({ people }: { people: Member[] }) {
    const shown = people.slice(0, 3);
    return (
        <span className="all__faces" aria-hidden="true">
            {shown.map(person => <span key={person.id} className="all__face">{person.initials}</span>)}
            {people.length > shown.length && <span className="all__face all__face--more">+{people.length - shown.length}</span>}
        </span>
    );
}

function AccessCell({ access, people, space }: { access: Access; people: Member[]; space: string }) {
    if (access === "public") return <span className="all__access all__access--public"><GlobeIcon size={15} /> Anyone with the link</span>;
    if (access === "personal") return <span className="all__access"><LockIcon size={15} /> Only you</span>;
    if (access === "restricted") return <span className="all__access"><PeopleIcon size={15} /> Some people</span>;
    const count = people.length;
    const who = count <= 1 ? "Only you so far" : count + " people";
    return (
        <span className="all__access" title={access === "rows" ? "Everyone in " + space + " opens it; each sees only their own rows" : "Everyone in " + space}>
            <Faces people={people} />
            {access === "rows" ? "Own rows each" : who}
        </span>
    );
}

const TITLES: Record<SpaceView, string> = { home: "Home", chats: "Chats", all: "All", pages: "Pages", apps: "Apps", tables: "Tables", files: "Files", workflows: "Workflows", settings: "Settings", about: "About" };
const KIND_NAME: Record<Row["kind"], string> = { page: "Page", app: "App", table: "Table", file: "File", folder: "Folder" };

/** "8h", "2d", "14 Sept": short, the way Space's list says it. */
function since(iso: string | null): string {
    if (!iso) return "—";
    const then = new Date(iso);
    if (Number.isNaN(then.getTime())) return iso;
    const minutes = Math.round((Date.now() - then.getTime()) / 60_000);
    if (minutes < 1) return "now";
    if (minutes < 60) return minutes + "m";
    if (minutes < 60 * 24) return Math.round(minutes / 60) + "h";
    if (minutes < 60 * 24 * 7) return Math.round(minutes / 1440) + "d";
    return then.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function Glyph({ kind }: { kind: Row["kind"] }) {
    const icon = kind === "app" ? <AppIcon size={18} />
        : kind === "table" ? <TableIcon size={18} />
        : kind === "folder" ? <LibraryIcon size={18} />
        : <FileIcon size={18} />;
    return <span className={"all__glyph all__glyph--" + kind}>{icon}</span>;
}

export function AllView({ podId, spaceName, botName, members, view, apps, appsPending = false, onOpenFile, onOpenTable, onOpenApp, onOpenFolder, onNewPage, onNewChat, onAsk, onLearn }: {
    podId: string;
    spaceName: string;
    /** The space's bot, by name, for the templates that mention it. */
    botName: string;
    members: Member[];
    view: SpaceView;
    apps: Extract<Tab, { kind: "app" }>[];
    /** The app list is still on its way, so no apps is not yet an answer. */
    appsPending?: boolean;
    onOpenFile: (path: string) => void;
    onOpenTable: (name: string) => void;
    onOpenApp: (id: string) => void;
    onOpenFolder: () => void;
    onNewPage: () => Promise<void>;
    onNewChat: () => void;
    /** Put words for the teammate in the chat box, to finish or send. */
    onAsk?: (text: string) => void;
    /** Open the guide to this place. */
    onLearn?: () => void;
}) {
    const [query, setQuery] = useState("");
    /* Files has two sides, as Space has "Your items" and "Shared": the
       space's shared files, and your own under /me. A folder opens in place,
       with a trail back, rather than jumping to another screen. */
    const [scope, setScope] = useState<"shared" | "personal">("shared");
    const [trail, setTrail] = useState<{ path: string; name: string }[]>([]);
    const root = scope === "personal" ? "/me" : "/";
    const directory = view === "files" && trail.length ? trail[trail.length - 1].path : view === "files" ? root : "/";
    const people = useMemo(() => members.filter(member => member.kind === "person"), [members]);
    const [newOpen, setNewOpen] = useState(false);
    const [layout, setLayout] = useLayout(view);
    const cache = useQueryClient();
    const maker = useMaking();
    const making = maker.busy;
    /* Files come in here as well as going out: uploaded into the folder on
       screen, from the button or by dropping them on the list. */
    const writes = useLibraryWrites(podId, directory);
    const picker = useRef<HTMLInputElement>(null);
    const upload = async (chosen: File[]) => {
        if (chosen.length === 0) return;
        await writes.upload(chosen);
        void cache.invalidateQueries({ queryKey: ["library", podId] });
    };
    const [dropping, setDropping] = useState(false);
    const dragDepth = useRef(0);
    const carriesFiles = (event: React.DragEvent) => Array.from(event.dataTransfer.types).includes("Files");
    const dropProps = view !== "files" ? {} : {
        onDragEnter: (event: React.DragEvent) => { if (!carriesFiles(event)) return; dragDepth.current += 1; setDropping(true); },
        onDragOver: (event: React.DragEvent) => { if (carriesFiles(event)) event.preventDefault(); },
        onDragLeave: (event: React.DragEvent) => {
            if (!carriesFiles(event)) return;
            dragDepth.current = Math.max(0, dragDepth.current - 1);
            if (dragDepth.current === 0) setDropping(false);
        },
        onDrop: (event: React.DragEvent) => {
            if (!carriesFiles(event)) return;
            event.preventDefault();
            dragDepth.current = 0;
            setDropping(false);
            void upload(Array.from(event.dataTransfer.files));
        },
    };

    const wantsFiles = view === "all" || view === "pages" || view === "files";
    const files = useQuery({
        queryKey: ["library", podId, "files", directory, "space"],
        queryFn: () => source.listLibrary(podId, "files", directory),
        enabled: wantsFiles,
        staleTime: 60_000,
    });
    /* Where New page puts pages. Listed beside the root so a page made here
       shows up in the list it was made from. */
    const pagesFolder = useQuery({
        queryKey: ["library", podId, "files", "/pages", "space"],
        queryFn: () => source.listLibrary(podId, "files", "/pages").catch(() => ({ items: [] as LibraryItem[] })),
        enabled: view === "all" || view === "pages",
        staleTime: 60_000,
    });
    /** A page from a template: saved in /pages under a name not yet taken,
     *  and opened. */
    const fromTemplate = (template: PageTemplate) => maker.run(template.id, async () => {
        const taken = new Set((pagesFolder.data?.items ?? []).map((item) => item.name.toLowerCase()));
        const path = await makePage((at, text) => source.createFile(podId, at, text), template.file, template.body(botName, new Date()), taken);
        void cache.invalidateQueries({ queryKey: ["library", podId] });
        onOpenFile(path);
    });
    const tables = useQuery({
        queryKey: ["library", podId, "tables", "/", "space"],
        queryFn: () => source.listLibrary(podId, "tables", "/"),
        enabled: view === "all" || view === "tables",
        staleTime: 60_000,
    });

    const rows: Row[] = useMemo(() => {
        const out: Row[] = [];
        const fileRow = (item: LibraryItem): Row => ({
            key: "file:" + item.path,
            kind: item.kind === "folder" ? "folder" : isDoc(item.path) ? "page" : "file",
            /* Folders keep their names; a file is shown as a title, with the
               stored name on hover. */
            name: item.kind === "folder" ? item.name : docTitle(item.name),
            file: item.kind === "folder" ? undefined : item.name,
            path: item.path,
            detail: item.kind === "folder" ? item.detail : fileKind(item.name, item.detail),
            updated: item.updated || null,
            access: scope === "personal" && view === "files" && !item.visibility ? "personal" : accessOf(item.visibility),
            open: item.kind === "folder"
                ? (view === "files" ? () => setTrail(was => [...was, { path: item.path, name: item.name }]) : onOpenFolder)
                : () => onOpenFile(item.path),
        });
        /* Each view says which kinds it holds, and nothing else decides.
           A query switched off for this view still hands back what it cached
           for another one, so its data being present is not a reason to show
           it. */
        const holds: Record<SpaceView, Row["kind"][]> = {
            all: ["page", "app", "table", "file"],
            pages: ["page"],
            apps: ["app"],
            tables: ["table"],
            /* Files is the browser: everything that is a file, docs included.
               Pages is the view that picks the docs out. */
            files: ["page", "file", "folder"],
            /* Their own pages, never this list. */
            home: [],
            chats: [],
            workflows: [],
            settings: [],
            about: [],
        };
        const wanted = new Set(holds[view]);
        const seen = new Set<string>();
        const fileItems = [...(files.data?.items ?? []), ...(view === "all" || view === "pages" ? pagesFolder.data?.items ?? [] : [])];
        for (const item of fileItems) {
            const row = fileRow(item);
            if (!wanted.has(row.kind) || seen.has(row.key)) continue;
            if (row.kind === "folder" && item.path === "/pages") continue;
            /* Dot-folders are the platform's own bookkeeping, not your files. */
            if (item.name.startsWith(".")) continue;
            /* Your personal folder is the Personal tab, not a row in Shared. */
            if (view === "files" && scope === "shared" && item.path === "/me") continue;
            seen.add(row.key);
            out.push(row);
        }
        if (wanted.has("app")) {
            for (const app of apps) {
                out.push({ key: app.id, kind: "app", name: app.label, detail: app.status ? app.status.charAt(0) + app.status.slice(1).toLowerCase() : "App", updated: app.updated ?? null, access: accessOf(app.visibility), open: () => onOpenApp(app.id) });
            }
        }
        if (wanted.has("table")) {
            for (const item of tables.data?.items ?? []) {
                out.push({ key: "table:" + item.name, kind: "table", name: item.name, detail: item.detail, updated: item.updated || null, access: accessOf(item.visibility, item.rls), rls: item.rls, open: () => onOpenTable(item.name) });
            }
        }
        const needle = query.trim().toLowerCase();
        const shown = needle ? out.filter(row => row.name.toLowerCase().includes(needle) || (row.file ?? "").toLowerCase().includes(needle)) : out;
        return shown.sort((a, b) => (b.updated ?? "").localeCompare(a.updated ?? ""));
    }, [files.data, pagesFolder.data, tables.data, apps, view, query, scope, onOpenFile, onOpenTable, onOpenApp, onOpenFolder]);

    const loading = (wantsFiles && files.isPending) || ((view === "all" || view === "tables") && tables.isPending) || ((view === "all" || view === "apps") && appsPending);

    /* Nothing to list, and nothing asked for: the place says what belongs in
       it instead of drawing an empty table. A search that finds nothing is a
       different answer and keeps its one line. */
    const nothing = !loading && rows.length === 0 && !query;
    const place: EmptyPlace = view === "files" ? { place: "files", scope, folder: trail.length > 0 }
        : view === "pages" || view === "apps" || view === "tables" ? { place: view }
        : { place: "all" };
    const handlers: EmptyHandlers = {
        page: () => void maker.run("page", onNewPage),
        upload: () => picker.current?.click(),
        ask: onAsk,
    };
    /* Apps keep the idea catalog under whatever is listed: the ready-made
       way to a first app, and to the next one. */
    const ideas = view === "apps" && !query && onAsk && (
        <AppIdeas name={botName} onAsk={onAsk} title={nothing ? "Or start from an idea" : "App ideas"} />
    );
    const templates = view === "pages" && !query && (
        <section className="all__templates" aria-label="Start with a template">
            <h2>{nothing ? "Or start from a template" : "Start with a template"}</h2>
            <div className="all__template-row">
                {PAGE_TEMPLATES.map((template) => (
                    <button key={template.id} className="all__template" data-template={template.id} disabled={Boolean(making)} onClick={() => void fromTemplate(template)}>
                        <span className="all__template-icon"><TemplateGlyph id={template.id} /></span>
                        <b>{making === template.id ? "Making…" : template.title}</b>
                        <small>{template.blurb}</small>
                    </button>
                ))}
            </div>
        </section>
    );

    return (
        <div className={"all" + (dropping && !nothing ? " all--drop" : "")} {...dropProps}>
            {view === "files" && (
                <input ref={picker} type="file" multiple hidden tabIndex={-1} aria-hidden="true"
                    onChange={(event) => { void upload(Array.from(event.target.files ?? [])); event.target.value = ""; }} />
            )}
            <header className="all__head">
                <h1>{TITLES[view]}</h1>
                <label className="all__search">
                    <SearchIcon size={17} />
                    <input placeholder="Search" aria-label="Search everything here" value={query} onChange={event => setQuery(event.target.value)} />
                </label>
                <div className="all__layout" role="group" aria-label="Show as">
                    <button aria-pressed={layout === "list"} title="List" aria-label="List" onClick={() => setLayout("list")}><ListIcon size={17} /></button>
                    <button aria-pressed={layout === "grid"} title="Grid" aria-label="Grid" onClick={() => setLayout("grid")}><GridIcon size={17} /></button>
                </div>
                <div className="all__new">
                    <button className="all__new-button" aria-expanded={newOpen} onClick={() => setNewOpen(was => !was)}>
                        New <ChevronDownIcon size={15} />
                    </button>
                    {newOpen && (
                        <div className="all__menu" role="menu" onMouseLeave={() => setNewOpen(false)}>
                            {view === "files" && <button role="menuitem" onClick={() => { setNewOpen(false); picker.current?.click(); }}><UploadIcon size={16} /> Upload files</button>}
                            {view === "tables" && onAsk && <button role="menuitem" onClick={() => { setNewOpen(false); onAsk("Set up a table to track "); }}><TableIcon size={16} /> Table</button>}
                            {view === "apps" && onAsk && <button role="menuitem" onClick={() => { setNewOpen(false); onAsk("Build an app that "); }}><AppIcon size={16} /> App</button>}
                            <button role="menuitem" onClick={() => { setNewOpen(false); void maker.run("page", onNewPage); }}><FileIcon size={16} /> Page</button>
                            <button role="menuitem" onClick={() => { setNewOpen(false); onNewChat(); }}><PlusIcon size={16} /> Conversation</button>
                        </div>
                    )}
                </div>
            </header>
            {maker.error && (
                <p className="all__error" role="alert">
                    Couldn’t make that page. {maker.error}
                    <button onClick={maker.clear} aria-label="Dismiss">×</button>
                </p>
            )}
            {writes.problem && (
                <p className="all__error" role="alert">
                    {writes.problem}
                    <button onClick={writes.clearProblem} aria-label="Dismiss">×</button>
                </p>
            )}
            {writes.busy && <p className="all__busy" role="status">Uploading {writes.busy}…</p>}
            {dropping && !nothing && <p className="all__drop-note">Drop to upload them here.</p>}
            {view === "files" && (
                <div className="all__scopes">
                    <div className="all__tabs" role="tablist" aria-label="Whose files">
                        {(["shared", "personal"] as const).map(each => (
                            <button key={each} role="tab" aria-selected={scope === each} onClick={() => { setScope(each); setTrail([]); }}>
                                {each === "shared" ? "Shared" : "Personal"}
                            </button>
                        ))}
                    </div>
                    <p className="all__scope-note">{scope === "shared" ? "Everyone in " + spaceName + " can open these." : "Only you can open these."}</p>
                    {trail.length > 0 && (
                        <nav className="all__trail" aria-label="Folder">
                            <button onClick={() => setTrail([])}>{scope === "shared" ? "Shared" : "Personal"}</button>
                            {trail.map((step, index) => (
                                <span key={step.path}>
                                    <span className="all__trail-sep">/</span>
                                    <button aria-current={index === trail.length - 1 ? "page" : undefined} onClick={() => setTrail(was => was.slice(0, index + 1))}>{step.name}</button>
                                </span>
                            ))}
                        </nav>
                    )}
                </div>
            )}
            {!nothing && templates}
            {rows.length === 0 ? null : layout === "grid" ? (
                <div className="all__grid">
                    {rows.map((row) => (
                        <button key={row.key} className="all__card" onClick={row.open} title={row.file}>
                            <span className="all__card-top" data-kind={row.kind}>
                                {row.kind === "page" && row.path ? <PagePreview podId={podId} path={row.path} /> : <Glyph kind={row.kind} />}
                            </span>
                            <span className="all__card-body">
                                <b>{row.name}</b>
                                <small>{[row.kind === "page" ? null : row.detail, since(row.updated)].filter(Boolean).join(" · ")}</small>
                            </span>
                        </button>
                    ))}
                </div>
            ) : (
            <table className="all__table">
                <thead>
                    <tr><th>Name</th><th className="all__col-access">Access</th><th className="all__col-when">Last activity</th></tr>
                </thead>
                <tbody>
                    {rows.map(row => (
                        <tr key={row.key} tabIndex={0} onClick={row.open} onKeyDown={event => { if (event.key === "Enter") row.open(); }}>
                            <td>
                                <span className="all__name">
                                    <Glyph kind={row.kind} />
                                    <span className="all__label">
                                        <span title={row.file}>{row.name}{view === "all" && <em className="all__kind">{KIND_NAME[row.kind]}</em>}{row.rls && <em className="rls-badge" title="Row-level security: each person sees only their own rows">RLS</em>}</span>
                                        {row.detail && <small>{row.detail}</small>}
                                    </span>
                                </span>
                            </td>
                            <td className="all__col-access"><AccessCell access={row.access} people={people} space={spaceName} /></td>
                            <td className="all__col-when">{since(row.updated)}</td>
                        </tr>
                    ))}
                </tbody>
            </table>
            )}
            {nothing && (
                <SpaceEmpty empty={emptyFor(place, botName)} on={handlers} dropping={dropping}
                    busy={making === "page" ? "page" : writes.busy ? "upload" : null}
                    learn={onLearn && (view === "pages" || view === "tables" || view === "apps") ? { label: guideTitle(view), onOpen: onLearn } : undefined} />
            )}
            {nothing && (templates || ideas) && <div className="all__after-empty">{templates}{ideas}</div>}
            {!loading && rows.length === 0 && query && <p className="all__empty">Nothing matches.</p>}
            {!nothing && !loading && ideas}
            {loading && <p className="all__empty">Loading…</p>}
        </div>
    );
}

function TemplateGlyph({ id }: { id: PageTemplate["id"] }) {
    if (id === "guide") return <SparkleIcon size={20} />;
    if (id === "todo") return <CheckCircleIcon size={20} />;
    if (id === "tracker") return <UsageIcon size={20} />;
    return <TextIcon size={20} />;
}

/** List or grid, remembered per list — the page list may want cards while
 *  Files stays a list. */
function useLayout(view: SpaceView): ["list" | "grid", (next: "list" | "grid") => void] {
    const storageKey = key("layout:" + view);
    const read = (): "list" | "grid" => {
        try { return localStorage.getItem(storageKey) === "grid" ? "grid" : "list"; } catch { return "list"; }
    };
    const [layout, setLayout] = useState<"list" | "grid">(read);
    const [seen, setSeen] = useState(view);
    if (seen !== view) { setSeen(view); setLayout(read()); }
    return [layout, (next) => {
        setLayout(next);
        try { localStorage.setItem(storageKey, next); } catch { /* per-viewer nicety only */ }
    }];
}

/** A page, in miniature: its first few blocks as they would read — a
 *  heading as a heading, a to-do with its box — shrunk onto the card and
 *  fading out at the bottom, rather than one run-on paragraph of grey. Read
 *  from the same cache the page opens from. */
type Mini = { kind: "h" | "p" | "todo" | "done" | "li" | "rule"; text: string };

function miniBlocks(markdown: string): Mini[] {
    const body = markdown.replace(/^---\n[\s\S]*?\n---\n/, "").replace(/```[a-z-]*\n[\s\S]*?```/g, "\u0000");
    const clean = (line: string) => line
        .replace(/!\[[^\]]*\]\([^)]*\)/g, "")
        .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
        .replace(/[*_`]/g, "")
        .trim();
    const out: Mini[] = [];
    for (const raw of body.split("\n")) {
        const line = raw.trim();
        if (!line || /^\|?\s*:?-{3}/.test(line)) continue;
        if (line === "\u0000") { out.push({ kind: "rule", text: "" }); continue; }
        let block: Mini | null = null;
        const heading = /^#{1,6}\s+(.*)$/.exec(line);
        const todo = /^[-*]\s+\[( |x)\]\s+(.*)$/i.exec(line);
        const item = /^([-*]|\d+\.)\s+(.*)$/.exec(line);
        if (heading) block = { kind: "h", text: clean(heading[1]) };
        else if (todo) block = { kind: todo[1].toLowerCase() === "x" ? "done" : "todo", text: clean(todo[2]) };
        else if (item) block = { kind: "li", text: clean(item[2]) };
        else block = { kind: "p", text: clean(line.replace(/^>\s?/, "").replace(/^\|/, "").replace(/\|/g, " · ")) };
        if (block.text || block.kind === "rule") out.push(block);
        if (out.length >= 8) break;
    }
    return out;
}

function PagePreview({ podId, path }: { podId: string; path: string }) {
    const file = useQuery({
        queryKey: ["file", podId, path],
        queryFn: () => source.readFile(podId, path),
        staleTime: 5 * 60_000,
    });
    const blocks = useMemo(() => miniBlocks(file.data?.text ?? ""), [file.data?.text]);
    if (file.isPending) return <span className="mini mini--loading" aria-hidden="true"><i /><i /><i /></span>;
    if (blocks.length === 0) return <span className="mini mini--empty">Empty page</span>;
    return (
        <span className="mini" aria-hidden="true">
            {blocks.map((block, at) => (
                block.kind === "rule"
                    ? <span key={at} className="mini__embed" />
                    : <span key={at} className={"mini__" + block.kind}>{block.text}</span>
            ))}
        </span>
    );
}
