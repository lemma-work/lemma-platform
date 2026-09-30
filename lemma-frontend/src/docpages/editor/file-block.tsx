"use client";

import { Node, mergeAttributes } from "@tiptap/core";
import { NodeViewWrapper, ReactNodeViewRenderer, type ReactNodeViewProps } from "@tiptap/react";
import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { usePageTools } from "@/docpages/page-context";
import { docTitle, fileKind } from "@/library/doc-title";
import { AttachIcon, ChevronRightIcon, FileIcon } from "@/ui/icons";

/** A page inside this one, or a file attached to it, as a block.
 *
 *  On disk it is nothing special — a paragraph holding one link to a path in
 *  the pod, `[Launch notes](/pages/Plan/Launch-notes.md)` — so an agent reads
 *  and writes it as the markdown it is. The editor recognises that one shape
 *  and draws it as a card you can open. */
export function encodeHref(path: string): string {
    return path.split("/").map((part) => encodeURIComponent(part)).join("/");
}
function decodeHref(href: string): string {
    try { return decodeURIComponent(href); } catch { return href; }
}

export const FileBlock = Node.create({
    name: "fileBlock",
    group: "block",
    atom: true,
    draggable: true,
    selectable: true,
    addAttributes() {
        return { href: { default: "" }, label: { default: "" } };
    },
    parseHTML() {
        return [{
            tag: "p",
            priority: 60,
            getAttrs: (element) => {
                const p = element as HTMLElement;
                if (p.childNodes.length !== 1) return false;
                const link = p.firstElementChild;
                if (!link || link.tagName !== "A") return false;
                const href = link.getAttribute("href") ?? "";
                if (!href.startsWith("/")) return false;
                return { href: decodeHref(href), label: link.textContent ?? "" };
            },
        }];
    },
    renderHTML({ HTMLAttributes, node }) {
        return ["p", mergeAttributes({ "data-file-block": "" }), ["a", { href: encodeHref(String(HTMLAttributes.href ?? node.attrs.href)) }, String(node.attrs.label)]];
    },
    addStorage() {
        return {
            markdown: {
                serialize(state: { write: (text: string) => void; closeBlock: (node: unknown) => void }, node: { attrs: { href: string; label: string } }) {
                    const label = (node.attrs.label || docTitle(node.attrs.href)).replace(/[[\]]/g, "");
                    state.write("[" + label + "](" + encodeHref(node.attrs.href) + ")");
                    state.closeBlock(node);
                },
                parse: {},
            },
        };
    },
    addNodeView() {
        return ReactNodeViewRenderer(FileBlockView);
    },
});

function FileBlockView({ node, selected }: ReactNodeViewProps) {
    const tools = usePageTools();
    const href = String(node.attrs.href ?? "");
    const isPage = /\.(md|markdown)$/i.test(href);
    /* A page is called by its own first heading, which is what its author
       renamed it to — the file name keeps whatever it was made with. Read
       from the same cache the page itself opens from. */
    const child = useQuery({
        queryKey: ["file", tools?.podId, href],
        enabled: isPage && Boolean(tools?.podId),
        staleTime: 60_000,
        queryFn: () => source.readFile(tools!.podId, href),
    });
    const heading = isPage ? /^#\s+(.+)$/m.exec(child.data?.text ?? "")?.[1]?.trim() : null;
    const title = isPage ? heading || docTitle(href) : (String(node.attrs.label ?? "") || docTitle(href));
    return (
        <NodeViewWrapper className="fblock" data-selected={selected || undefined} data-drag-handle="">
            <button className="fblock__open" contentEditable={false} onClick={() => tools?.openFile(href)} title={href}>
                <span className="fblock__icon">{isPage ? <FileIcon size={18} /> : <AttachIcon size={17} />}</span>
                <span className="fblock__text">
                    <span className="fblock__title">{title}</span>
                    <small>{isPage ? "Page" : fileKind(href)}</small>
                </span>
                <ChevronRightIcon size={15} />
            </button>
        </NodeViewWrapper>
    );
}
