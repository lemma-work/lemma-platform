"use client";

import { type ReactNode } from "react";
import { CopyButton } from "./copy-button";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Element, Root } from "hast";
import { visit } from "unist-util-visit";

/** Agents emit HTML on purpose — a pod can be told to answer with status
 *  strips and `<details>` blocks, and several are. React-markdown drops HTML
 *  by default, which is why those replies were arriving as visible
 *  `<div style="…">` source instead of the thing they describe.
 *
 *  So the HTML is rendered, but never trusted: the sanitiser keeps a small
 *  set of tags, and `style` survives only for the handful of declarations
 *  that can colour a chip. Anything that could position, size or load —
 *  position, z-index, width, background-image, url(), expression() — is
 *  dropped before it reaches the DOM. */

const STYLE_ALLOW = new Set([
    "color",
    "background",
    "background-color",
    "border",
    "border-left",
    "border-right",
    "border-top",
    "border-bottom",
    "border-radius",
    "padding",
    "padding-left",
    "padding-right",
    "padding-top",
    "padding-bottom",
    "margin",
    "margin-top",
    "margin-bottom",
    "font-size",
    "font-weight",
    "font-family",
    "text-align",
    "display",
]);

function safeStyle(value: string): string | undefined {
    const kept: string[] = [];
    for (const declaration of value.split(";")) {
        const at = declaration.indexOf(":");
        if (at < 0) continue;
        const property = declaration.slice(0, at).trim().toLowerCase();
        const setting = declaration.slice(at + 1).trim();
        if (!STYLE_ALLOW.has(property)) continue;
        if (/url\(|expression\(|@import|javascript:/i.test(setting)) continue;
        if (property === "display" && !/^(inline-block|inline|block|flex)$/.test(setting)) continue;
        kept.push(property + ":" + setting);
    }
    return kept.length > 0 ? kept.join(";") : undefined;
}

/** rehype-sanitize can allow `style` but not police its contents, so the
 *  filtering happens here, after it has run. */
function narrowStyles() {
    return (tree: Root) => {
        visit(tree, "element", (node: Element) => {
            const style = node.properties?.style;
            if (typeof style !== "string") return;
            const kept = safeStyle(style);
            if (!kept) {
                delete node.properties.style;
                return;
            }
            /* Agents write light-mode colours. A chip with a pale background
               and no stated foreground turns into white-on-white the moment
               the reader is in dark mode, so a dark ink is supplied. */
            const hasBackground = /(^|;)\s*background(-color)?\s*:/.test(kept);
            const hasColor = /(^|;)\s*color\s*:/.test(kept);
            node.properties.style = hasBackground && !hasColor ? kept + ";color:#1b1a18" : kept;
        });
    };
}

const schema = {
    ...defaultSchema,
    tagNames: [
        ...(defaultSchema.tagNames ?? []).filter((tag) => tag !== "img"),
        "details",
        "summary",
        "mark",
        "kbd",
        "figure",
        "figcaption",
    ],
    attributes: {
        ...defaultSchema.attributes,
        "*": [...(defaultSchema.attributes?.["*"] ?? []), "style"],
        details: ["open"],
    },
};

function codeText(node: Element | Root["children"][number]): string {
    if (node.type === "text") return node.value;
    return "children" in node ? node.children.map(codeText).join("") : "";
}

function CopySection({ children, text }: { children: ReactNode; text: string }) {
    return <div className="copy-section"><CopyButton text={text} label="Copy section" />{children}</div>;
}

export function Prose({ text }: { text: string }) {
    return (
        <div className="md">
            <Markdown
                components={{
                    pre: ({ children, node, ...props }) => <CopySection text={node ? codeText(node) : ""}><pre {...props}>{children}</pre></CopySection>,
                    blockquote: ({ children, node, ...props }) => <CopySection text={node?.position ? text.slice(node.position.start.offset, node.position.end.offset) : ""}><blockquote {...props}>{children}</blockquote></CopySection>,
                    details: ({ children, node, ...props }) => <CopySection text={node?.position ? text.slice(node.position.start.offset, node.position.end.offset) : ""}><details {...props}>{children}</details></CopySection>,
                    table: ({ children, node, ...props }) => <CopySection text={node?.position ? text.slice(node.position.start.offset, node.position.end.offset) : ""}><table {...props}>{children}</table></CopySection>,
                }}
                remarkPlugins={[remarkGfm]}
                rehypePlugins={[rehypeRaw, [rehypeSanitize, schema], narrowStyles]}
            >
                {text}
            </Markdown>
        </div>
    );
}
