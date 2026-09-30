"use client";

import Image from "@tiptap/extension-image";
import { NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from "@tiptap/react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { lemma } from "@/session/client";
import { usePageTools } from "@/docpages/page-context";

/** An image in a page. The markdown holds its path in the pod,
 *  `![](/pages/Plan-files/chart.png)`, which is what an agent reads; the
 *  browser needs a signed URL for it, fetched when the block is drawn. */
export const PodImage = Image.extend({
    draggable: true,
    /* A path in the pod is not a URL the browser can fetch. When the editor
       writes HTML — for the clipboard, for a drag — it goes in a data
       attribute, so no <img> is made that asks this site for a pod path. */
    parseHTML() {
        return [{
            tag: "img[src], img[data-pod-src]",
            getAttrs: (element) => {
                const img = element as HTMLElement;
                return { src: img.getAttribute("data-pod-src") ?? img.getAttribute("src"), alt: img.getAttribute("alt"), title: img.getAttribute("title") };
            },
        }];
    },
    renderHTML({ HTMLAttributes }) {
        const src = String(HTMLAttributes.src ?? "");
        if (src.startsWith("/")) {
            const { src: _drop, ...rest } = HTMLAttributes;
            void _drop;
            return ["img", { ...rest, "data-pod-src": src }];
        }
        return ["img", HTMLAttributes];
    },
    /* A block of its own on disk too: the stock serializer writes the image
       inline and runs the next paragraph straight into it. */
    addStorage() {
        return {
            markdown: {
                serialize(state: { write: (text: string) => void; closeBlock: (node: unknown) => void; esc: (text: string) => string }, node: { attrs: { src?: string; alt?: string } }) {
                    const src = String(node.attrs.src ?? "").replace(/ /g, "%20");
                    state.write("![" + state.esc(String(node.attrs.alt ?? "")) + "](" + src + ")");
                    state.closeBlock(node);
                },
                parse: {},
            },
        };
    },
    addNodeView() {
        return ReactNodeViewRenderer(PodImageView);
    },
}).configure({ inline: false, allowBase64: false });

function PodImageView({ node, selected }: ReactNodeViewProps) {
    const tools = usePageTools();
    const src = String(node.attrs.src ?? "");
    const inPod = src.startsWith("/");
    const signed = useQuery({
        queryKey: ["asset-url", tools?.podId, src],
        enabled: inPod && Boolean(tools?.podId) && source.label === "live",
        staleTime: 30 * 60_000,
        queryFn: async () => {
            const made = (await lemma(tools!.podId).files.getUrl(src)) as { url?: string };
            return made.url ?? null;
        },
    });
    const url = inPod ? signed.data ?? null : src;
    return (
        <NodeViewWrapper className="pimage" data-selected={selected || undefined} data-drag-handle="">
            {url ? (
                <img src={url} alt={String(node.attrs.alt ?? "")} draggable={false} />
            ) : (
                <span className="pimage__missing" contentEditable={false}>
                    {inPod && signed.isPending && source.label === "live" ? "Loading image…" : "Image: " + src}
                </span>
            )}
        </NodeViewWrapper>
    );
}
