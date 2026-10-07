"use client";

import { createContext, memo, useContext, type ComponentProps, type ReactNode } from "react";
import { CopyButton } from "./copy-button";
import Markdown, { type Components, type Options } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import type { Element, Root } from "hast";
import { visit } from "unist-util-visit";
import { splitSettled } from "./stream-blocks";

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

/** The same, with pictures. Only for a caller that says where a picture's
 *  bytes come from: in chat an `<img>` an agent wrote would fetch from
 *  wherever it pointed the moment the message drew, which is why the default
 *  drops them. */
const schemaWithImages = {
    ...schema,
    tagNames: [...schema.tagNames, "img"],
    attributes: {
        ...schema.attributes,
        img: ["src", "alt", "title", "width", "height"],
    },
};

function codeText(node: Element | Root["children"][number]): string {
    if (node.type === "text") return node.value;
    return "children" in node ? node.children.map(codeText).join("") : "";
}

function CopySection({ children, text }: { children: ReactNode; text: string }) {
    return <div className="copy-section"><CopyButton text={text} label="Copy section" />{children}</div>;
}

/** The markdown being rendered, for the components that copy a slice of it.
 *
 *  Read from context rather than closed over, so the components below can be
 *  defined once. Built inside `Prose` they were new component types on every
 *  render, and React unmounts and remounts everything under a component whose
 *  type changed — every code block and table in a reply, rebuilt for each
 *  update. */
const Source = createContext("");

type Positioned = { node?: Element; children?: ReactNode };

function sliceOf(text: string, node?: Element): string {
    return node?.position ? text.slice(node.position.start.offset, node.position.end.offset) : "";
}

function CodeBlock({ children, node, ...props }: Positioned & ComponentProps<"pre">) {
    return <CopySection text={node ? codeText(node) : ""}><pre {...props}>{children}</pre></CopySection>;
}

function Quote({ children, node, ...props }: Positioned & ComponentProps<"blockquote">) {
    return <CopySection text={sliceOf(useContext(Source), node)}><blockquote {...props}>{children}</blockquote></CopySection>;
}

function Disclosure({ children, node, ...props }: Positioned & ComponentProps<"details">) {
    return <CopySection text={sliceOf(useContext(Source), node)}><details {...props}>{children}</details></CopySection>;
}

function Table({ children, node, ...props }: Positioned & ComponentProps<"table">) {
    return <CopySection text={sliceOf(useContext(Source), node)}><table {...props}>{children}</table></CopySection>;
}

/** Where a picture written as `src` is fetched from, or null to drop it. */
type ImageSource = (src: string) => string | null;

/** Read from context for the same reason as `Source`. Only reached at all
 *  when the caller gave one: without it the sanitiser has already removed
 *  every `<img>`. */
const Pictures = createContext<ImageSource | null>(null);

function Picture({ node: _node, src, alt, ...props }: Positioned & ComponentProps<"img">) {
    const imageSource = useContext(Pictures);
    const resolved = imageSource && typeof src === "string" ? imageSource(src) : null;
    return resolved ? <img {...props} src={resolved} alt={alt ?? ""} loading="lazy" /> : null;
}

const COMPONENTS: Components = { pre: CodeBlock, blockquote: Quote, details: Disclosure, table: Table, img: Picture };
const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS: NonNullable<Options["rehypePlugins"]> = [rehypeRaw, [rehypeSanitize, schema], narrowStyles];
const REHYPE_PLUGINS_WITH_IMAGES: NonNullable<Options["rehypePlugins"]> = [rehypeRaw, [rehypeSanitize, schemaWithImages], narrowStyles];

/** The rendered markdown alone, without the `.md` box: a streaming reply puts
 *  several of these in one box, and the box's first- and last-child rules have
 *  to see one run of blocks. */
const Rendered = memo(function Rendered({ text, imageSource }: { text: string; imageSource?: ImageSource }) {
    return (
        <Source.Provider value={text}>
            <Pictures.Provider value={imageSource ?? null}>
                <Markdown
                    components={COMPONENTS}
                    remarkPlugins={REMARK_PLUGINS}
                    rehypePlugins={imageSource ? REHYPE_PLUGINS_WITH_IMAGES : REHYPE_PLUGINS}
                >
                    {text}
                </Markdown>
            </Pictures.Provider>
        </Source.Provider>
    );
});

/** Markdown, rendered once per text. Memoised: a transcript re-renders for
 *  every few tokens of the reply being written, and every message above it
 *  used to be parsed again each time.
 *
 *  Pictures are drawn only when `imageSource` says where their bytes come
 *  from; chat passes none. */
export const Prose = memo(function Prose({ text, imageSource }: { text: string; imageSource?: ImageSource }) {
    return <div className="md"><Rendered text={text} imageSource={imageSource} /></div>;
});

/** A reply still being written. The blocks it has finished are rendered once
 *  each and left alone; only the one in progress is parsed again as tokens
 *  arrive. See `stream-blocks.ts` for where it is safe to cut. */
export function StreamingProse({ text }: { text: string }) {
    const { settled, tail } = splitSettled(text);
    return (
        <div className="md">
            {settled.map((block, index) => <Rendered key={index} text={block} />)}
            {tail && <Rendered text={tail} />}
        </div>
    );
}
