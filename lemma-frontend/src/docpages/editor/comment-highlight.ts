import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import { findAnchor } from "@/docpages/comments/model";
import { flatText, rangeOf } from "./flat-text";

export interface HighlightAnchor {
    id: string;
    quote: string;
    quotePrefix: string;
    quoteSuffix: string;
}

interface HighlightState {
    anchors: HighlightAnchor[];
    active: string | null;
    set: DecorationSet;
}

export const commentHighlightKey = new PluginKey<HighlightState>("comment-highlight");

function decorate(doc: Parameters<typeof flatText>[0], anchors: HighlightAnchor[], active: string | null): DecorationSet {
    if (anchors.length === 0) return DecorationSet.empty;
    const flat = flatText(doc);
    const decorations: Decoration[] = [];
    for (const anchor of anchors) {
        const found = findAnchor(flat.text, anchor);
        if (!found) continue;
        const range = rangeOf(flat, found.from, found.to);
        if (!range) continue;
        decorations.push(Decoration.inline(range.from, range.to, {
            class: "cmt-mark" + (anchor.id === active ? " cmt-mark--active" : ""),
            "data-comment-id": anchor.id,
        }));
    }
    return DecorationSet.create(doc, decorations);
}

/** Where the open comment threads are, drawn over the words they are about.
 *  Anchors are handed in with a transaction's meta, and re-found on every
 *  edit — the words may have moved, or gone. */
export const CommentHighlight = Extension.create<{ onClick: (id: string) => void }>({
    name: "commentHighlight",
    addOptions() {
        return { onClick: () => undefined };
    },
    addProseMirrorPlugins() {
        const onClick = this.options.onClick;
        return [
            new Plugin<HighlightState>({
                key: commentHighlightKey,
                state: {
                    init: () => ({ anchors: [], active: null, set: DecorationSet.empty }),
                    apply(tr, value, _old, next) {
                        const meta = tr.getMeta(commentHighlightKey) as { anchors?: HighlightAnchor[]; active?: string | null } | undefined;
                        if (meta) {
                            const anchors = meta.anchors ?? value.anchors;
                            const active = meta.active !== undefined ? meta.active : value.active;
                            return { anchors, active, set: decorate(next.doc, anchors, active) };
                        }
                        if (tr.docChanged) return { ...value, set: decorate(next.doc, value.anchors, value.active) };
                        return value;
                    },
                },
                props: {
                    decorations: (state) => commentHighlightKey.getState(state)?.set ?? DecorationSet.empty,
                    handleClick: (_view, _pos, event) => {
                        const target = (event.target as HTMLElement | null)?.closest?.("[data-comment-id]");
                        const id = target?.getAttribute("data-comment-id");
                        if (id) onClick(id);
                        return false;
                    },
                },
            }),
        ];
    },
});
