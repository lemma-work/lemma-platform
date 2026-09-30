import { Extension, type Editor, type Range } from "@tiptap/core";
import Suggestion, { type SuggestionProps } from "@tiptap/suggestion";
import { PluginKey } from "@tiptap/pm/state";

/** What `/` offers. Kept as data so the menu, the filter and the keyboard
 *  all read one list; what each one *does* lives in the editor, which owns
 *  the uploads, the new pages and the bot. */
export type SlashId =
    | "generate" | "visualize" | "page" | "view" | "html" | "image" | "file"
    | "text" | "h1" | "h2" | "h3" | "bullet" | "number" | "todo" | "quote" | "code" | "divider" | "table";

export interface SlashItem {
    id: SlashId;
    group: "Ask" | "Create" | "Media" | "Basic";
    title: string;
    hint: string;
    keywords: string;
    shortcut?: string;
}

export const SLASH_ITEMS: SlashItem[] = [
    { id: "generate", group: "Ask", title: "Write with the bot", hint: "Describe what should go here", keywords: "generate ai write draft visualize ask" },
    { id: "visualize", group: "Ask", title: "Visualize", hint: "A chart or diagram, drawn live", keywords: "chart graph diagram visualise visualization plot widget" },
    { id: "page", group: "Create", title: "Page", hint: "A page inside this one", keywords: "subpage child nested doc" },
    { id: "view", group: "Create", title: "Table view", hint: "Live rows — filter, group, sort, join", keywords: "database query sql rows records filter linked" },
    { id: "html", group: "Create", title: "HTML", hint: "Paste or write an embed", keywords: "embed iframe code widget" },
    { id: "image", group: "Media", title: "Image", hint: "Upload an image", keywords: "picture photo png jpg" },
    { id: "file", group: "Media", title: "File", hint: "Attach a file", keywords: "attachment upload pdf" },
    { id: "text", group: "Basic", title: "Text", hint: "Plain paragraph", keywords: "paragraph plain" },
    { id: "h1", group: "Basic", title: "Heading 1", hint: "", keywords: "title big", shortcut: "#" },
    { id: "h2", group: "Basic", title: "Heading 2", hint: "", keywords: "subtitle", shortcut: "##" },
    { id: "h3", group: "Basic", title: "Heading 3", hint: "", keywords: "", shortcut: "###" },
    { id: "bullet", group: "Basic", title: "Bulleted list", hint: "", keywords: "ul unordered", shortcut: "-" },
    { id: "number", group: "Basic", title: "Numbered list", hint: "", keywords: "ol ordered", shortcut: "1." },
    { id: "todo", group: "Basic", title: "To-do list", hint: "Checkboxes", keywords: "todo to do task check checklist", shortcut: "[]" },
    { id: "quote", group: "Basic", title: "Quote", hint: "", keywords: "blockquote", shortcut: ">" },
    { id: "code", group: "Basic", title: "Code", hint: "", keywords: "snippet pre", shortcut: "```" },
    { id: "table", group: "Basic", title: "Table", hint: "3 × 3", keywords: "grid rows columns" },
    { id: "divider", group: "Basic", title: "Divider", hint: "", keywords: "rule line hr", shortcut: "---" },
];

export function filterSlash(query: string): SlashItem[] {
    const needle = query.trim().toLowerCase();
    if (!needle) return SLASH_ITEMS;
    return SLASH_ITEMS
        .map((item) => {
            const title = item.title.toLowerCase();
            const score = title.startsWith(needle) ? 0 : title.includes(needle) ? 1 : item.keywords.includes(needle) ? 2 : -1;
            return { item, score };
        })
        .filter((one) => one.score >= 0)
        .sort((a, b) => a.score - b.score)
        .map((one) => one.item);
}

export interface SlashState {
    open: boolean;
    items: SlashItem[];
    index: number;
    rect: DOMRect | null;
}

/** Between the ProseMirror plugin, which knows the caret and the keys, and
 *  the React menu, which draws it. A tiny store, read with
 *  `useSyncExternalStore`. */
export class SlashBridge {
    private state: SlashState = { open: false, items: [], index: 0, rect: null };
    private listeners = new Set<() => void>();
    private props: SuggestionProps<SlashItem> | null = null;
    /** Set by the editor: what running an item does. */
    run: ((id: SlashId, editor: Editor, range: Range) => void) | null = null;

    subscribe = (listen: () => void) => {
        this.listeners.add(listen);
        return () => { this.listeners.delete(listen); };
    };
    get = () => this.state;
    private set(next: Partial<SlashState>) {
        this.state = { ...this.state, ...next };
        for (const listen of this.listeners) listen();
    }
    show(props: SuggestionProps<SlashItem>) {
        this.props = props;
        const index = this.state.open ? Math.min(this.state.index, Math.max(0, props.items.length - 1)) : 0;
        this.set({ open: props.items.length > 0, items: props.items, index, rect: props.clientRect?.() ?? null });
    }
    hide() {
        this.props = null;
        this.set({ open: false });
    }
    pick(item: SlashItem) {
        this.props?.command(item);
    }
    hover(index: number) {
        this.set({ index });
    }
    key(event: KeyboardEvent): boolean {
        if (!this.state.open) return false;
        const count = this.state.items.length;
        if (event.key === "ArrowDown") { this.set({ index: (this.state.index + 1) % count }); return true; }
        if (event.key === "ArrowUp") { this.set({ index: (this.state.index - 1 + count) % count }); return true; }
        if (event.key === "Enter" || event.key === "Tab") {
            const item = this.state.items[this.state.index];
            if (item) this.pick(item);
            return true;
        }
        if (event.key === "Escape") { this.hide(); return true; }
        return false;
    }
}

export const slashKey = new PluginKey("slash");

export const SlashCommand = Extension.create<{ bridge: SlashBridge | null }>({
    name: "slashCommand",
    addOptions() {
        return { bridge: null };
    },
    addProseMirrorPlugins() {
        const bridge = this.options.bridge;
        if (!bridge) return [];
        return [
            Suggestion<SlashItem>({
                editor: this.editor,
                pluginKey: slashKey,
                char: "/",
                allowSpaces: false,
                /* Not inside code, where a slash is a slash. */
                allow: ({ state, range }) => {
                    const $from = state.doc.resolve(range.from);
                    return !$from.parent.type.spec.code;
                },
                items: ({ query }) => filterSlash(query),
                command: ({ editor, range, props }) => bridge.run?.(props.id, editor, range),
                render: () => ({
                    onStart: (props) => bridge.show(props),
                    onUpdate: (props) => bridge.show(props),
                    onKeyDown: ({ event }) => bridge.key(event),
                    onExit: () => bridge.hide(),
                }),
            }),
        ];
    },
});
