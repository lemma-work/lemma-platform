import GithubSlugger from 'github-slugger';
import { visit } from 'unist-util-visit';
import type { Root } from 'mdast';

export interface ContentHeading {
    depth: 2 | 3;
    text: string;
    id: string;
}

/**
 * Collects the headings a page's "on this page" rail is built from.
 *
 * This runs as a remark plugin rather than a regex over the raw MDX for one
 * reason: an mdast `heading` node is a heading, while `#` inside a fenced code
 * block is not. A regex has to be taught that difference and gets it wrong the
 * first time someone documents a shell comment.
 *
 * Ids come from the same `github-slugger` instance semantics `rehype-slug`
 * uses, including its duplicate-suffix behaviour, so a link in the rail and the
 * `id` on the heading it points at can never disagree.
 *
 * Only h2 and h3 are collected. h1 is the page title, and an h4 in a rail turns
 * navigation into an outline nobody reads.
 */
export function collectHeadings(sink: ContentHeading[]) {
    return () => (tree: Root) => {
        const slugger = new GithubSlugger();
        visit(tree, 'heading', (node) => {
            const text = node.children
                .map((child) => ('value' in child ? child.value : ''))
                .join('')
                .trim();
            // Slug every heading so the duplicate counter stays in step with
            // rehype-slug, but only surface the two depths the rail shows.
            const id = slugger.slug(text);
            if (node.depth !== 2 && node.depth !== 3) return;
            if (!text) return;
            sink.push({ depth: node.depth, text, id });
        });
    };
}
