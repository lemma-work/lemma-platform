import { Fragment, type ReactNode } from "react";
import Link from "next/link";
import {
    CaretRight,
    GithubLogo,
    Info,
    Robot,
    WarningCircle,
} from "@phosphor-icons/react/dist/ssr";
import type { DocsBlock, DocsPage } from "@/site/data/docs";
import { docsPageToMarkdown } from "@/site/markdown/render";
import { ConceptMap } from "@/site/concept-map";
import {
    codeLabel,
    docsCrumbs,
    docsNeighbours,
    docsPieces,
    docsSections,
    docsSourceUrl,
    isShell,
    shellLines,
    type DocsCodeBlock,
    type DocsPiece,
} from "@/site/docs-content";
import { CodeTabs, CopyButton, DocsOutline } from "@/site/docs-client";
import { DocsPhoneBar } from "@/site/docs-shell";

/* One docs page: the reading column, and "On this page" beside it. Drawn on
   the server from the page's blocks; only copying, tabs and the outline's
   lit section run in the browser. */

const LINK = /(https?:\/\/[^\s)]+[^\s).,;:!?])/;

/** A block's words. They are plain text; an address in them is set as code,
 *  something to type rather than a link to follow. */
function Words({ children }: { children: string }) {
    return (
        <>
            {children.split(LINK).map((part, i) =>
                i % 2 ? <code key={i}>{part}</code> : part,
            )}
        </>
    );
}

/** A link out of a page's words: to another page here, or to a guide kept
 *  with the code. */
function Way({ href, children }: { href: string; children: string }) {
    return href.startsWith("/") ? (
        <Link href={href}>{children}</Link>
    ) : (
        <a href={href}>{children}</a>
    );
}

function Heading({ id, title }: { id: string; title: string }) {
    return (
        <h2 id={id}>
            <a href={"#" + id}>{title}</a>
        </h2>
    );
}

function CodeBody({ block }: { block: DocsCodeBlock }) {
    if (!isShell(block.language))
        return (
            <pre>
                <code>{block.code}</code>
            </pre>
        );
    return (
        <pre>
            <code>
                {shellLines(block.code).map((line, i) => (
                    <Fragment key={i}>
                        {i > 0 && "\n"}
                        {line.kind === "command" && (
                            <span className="docs-code__p" aria-hidden="true">
                                ${" "}
                            </span>
                        )}
                        {line.kind === "comment" ? (
                            <span className="docs-code__c">{line.text}</span>
                        ) : (
                            line.text
                        )}
                    </Fragment>
                ))}
            </code>
        </pre>
    );
}

function CodePanel({ block }: { block: DocsCodeBlock }) {
    return (
        <div className="docs-code">
            <div className="docs-code__bar">
                <span>{codeLabel(block.language)}</span>
                <CopyButton text={block.code} />
            </div>
            <CodeBody block={block} />
        </div>
    );
}

function Note({ block }: { block: Extract<DocsBlock, { type: "callout" }> }) {
    const warning = block.tone === "warning";
    return (
        <aside className="docs-note" data-tone={warning ? "warning" : "note"}>
            {warning ? (
                <WarningCircle aria-hidden="true" />
            ) : (
                <Info aria-hidden="true" />
            )}
            <div>
                <p className="docs-note__t">{block.title}</p>
                <p>
                    <Words>{block.body}</Words>
                </p>
            </div>
        </aside>
    );
}

function Piece({ piece }: { piece: DocsPiece }): ReactNode {
    if (piece.kind === "tabs")
        return (
            <>
                <Heading id={piece.id} title={piece.title} />
                {piece.body && (
                    <p>
                        <Words>{piece.body}</Words>
                    </p>
                )}
                <CodeTabs
                    tabs={piece.tabs.map((block) => ({
                        label: codeLabel(block.language),
                        code: block.code,
                        panel: <CodeBody block={block} />,
                    }))}
                />
            </>
        );
    const { block, id } = piece;
    if (block.type === "callout") return <Note block={block} />;
    const heading =
        id && block.title ? <Heading id={id} title={block.title} /> : null;
    const body = block.body ? (
        <p>
            <Words>{block.body}</Words>
        </p>
    ) : null;
    switch (block.type) {
        case "paragraph":
            return (
                <>
                    {heading}
                    {body}
                </>
            );
        case "list":
        case "steps": {
            const List = block.type === "steps" ? "ol" : "ul";
            return (
                <>
                    {heading}
                    {body}
                    <List>
                        {block.items.map((item) => (
                            <li key={item}>
                                <Words>{item}</Words>
                            </li>
                        ))}
                    </List>
                </>
            );
        }
        case "links":
            return (
                <>
                    {heading}
                    {body}
                    <ul className="docs-links">
                        {block.items.map((item) => (
                            <li key={item.href}>
                                <Way href={item.href}>{item.label}</Way>
                                {item.note && <span>{item.note}</span>}
                            </li>
                        ))}
                    </ul>
                </>
            );
        case "code":
            return (
                <>
                    {heading}
                    {body}
                    <CodePanel block={block} />
                </>
            );
        case "table":
            return (
                <>
                    {heading}
                    {body}
                    <div className="docs-table">
                        <table>
                            <thead>
                                <tr>
                                    {block.columns.map((column) => (
                                        <th key={column} scope="col">
                                            {column}
                                        </th>
                                    ))}
                                </tr>
                            </thead>
                            <tbody>
                                {block.rows.map((row, r) => (
                                    <tr key={r}>
                                        {row.map((cell, c) => (
                                            <td key={c}>
                                                <Words>{cell}</Words>
                                            </td>
                                        ))}
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </>
            );
    }
}

export function DocsArticle({ page }: { page: DocsPage }) {
    const markdown = docsPageToMarkdown(page);
    const sections = docsSections(page);
    const { previous, next } = docsNeighbours(page.slug);
    return (
        <>
            <main id="content" className="docs-main docs-main--read">
                <DocsPhoneBar />
                <p className="docs-crumbs">
                    {docsCrumbs(page).map((crumb, i) => (
                        <Fragment key={crumb}>
                            {i > 0 && <CaretRight aria-hidden="true" />}
                            {crumb}
                        </Fragment>
                    ))}
                </p>
                <h1 className="docs-h1">{page.title}</h1>
                <p className="docs-lede">{page.description}</p>
                <div className="docs-acts">
                    <CopyButton
                        text={markdown}
                        label="Copy as Markdown"
                        className="b b--s b--secondary"
                    />
                    <a className="b b--s b--quiet" href="/llms.txt">
                        <Robot aria-hidden="true" />
                        llms.txt
                    </a>
                </div>
                {page.slug === "how-lemma-works" && <ConceptMap />}
                <div className="docs-prose">
                    {docsPieces(page).map((piece, i) => (
                        <Piece key={i} piece={piece} />
                    ))}
                </div>
                <nav className="docs-next" aria-label="More pages">
                    <Link href={previous ? "/docs/" + previous.slug : "/docs"}>
                        <small>Previous</small>
                        <span>{previous?.title ?? "Documentation"}</span>
                    </Link>
                    {next && (
                        <Link
                            className="docs-next__next"
                            href={"/docs/" + next.slug}
                        >
                            <small>Next</small>
                            <span>{next.title}</span>
                        </Link>
                    )}
                </nav>
            </main>
            <aside className="docs-outline">
                {sections.length > 0 && <DocsOutline sections={sections} />}
                <div className="docs-outline__acts">
                    <CopyButton
                        text={markdown}
                        label="Copy as Markdown"
                        className="docs-outline__act"
                    />
                    <a className="docs-outline__act" href={docsSourceUrl(page)}>
                        <GithubLogo aria-hidden="true" />
                        View on GitHub
                    </a>
                </div>
            </aside>
        </>
    );
}
