import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

/** The line a bot is writing into: `⟦Lem is writing: a summary⟧`.
 *
 *  Plain text in the file, on purpose — the bot finds it by reading the file,
 *  the way it reads everything else — and drawn in the editor as a working
 *  line rather than as brackets somebody typed. */
export function writingMarker(bot: string, ask: string): string {
    return "⟦" + bot + " is writing: " + ask.replace(/[⟦⟧\n]/g, " ").trim() + "⟧";
}

const MARKER = /⟦[^⟧\n]{1,400}⟧/g;
const key = new PluginKey("writing-marker");

export const WritingMarker = Extension.create({
    name: "writingMarker",
    addProseMirrorPlugins() {
        return [
            new Plugin({
                key,
                props: {
                    decorations(state) {
                        const found: Decoration[] = [];
                        state.doc.descendants((node, pos) => {
                            if (!node.isText || !node.text) return;
                            for (const match of node.text.matchAll(MARKER)) {
                                const from = pos + (match.index ?? 0);
                                found.push(Decoration.inline(from, from + match[0].length, { class: "writing-marker" }));
                            }
                        });
                        return DecorationSet.create(state.doc, found);
                    },
                },
            }),
        ];
    },
});
