import 'server-only';

import { compileMDX } from 'next-mdx-remote/rsc';
import rehypeShiki from '@shikijs/rehype';
import rehypeSlug from 'rehype-slug';
import remarkGfm from 'remark-gfm';

import { collectHeadings, type ContentHeading } from '@/lib/content/headings';
import { contentComponents } from '@/components/content/mdx-components';

export interface CompiledContent {
    content: React.ReactElement;
    headings: ContentHeading[];
}

/**
 * Turns one document's MDX body into rendered React, on the server.
 *
 * Compilation happens inside a statically generated route, so this runs at
 * build time and the highlighted markup ships as HTML. Nothing here reaches the
 * browser — the point of the whole pipeline is that a crawler and a reader with
 * JavaScript disabled see the same page.
 */
export async function compileContent(body: string): Promise<CompiledContent> {
    const headings: ContentHeading[] = [];

    const { content } = await compileMDX({
        source: body,
        components: contentComponents,
        options: {
            // Frontmatter is parsed and validated by the loader before this is
            // ever called, so the body arriving here has none left to strip.
            parseFrontmatter: false,
            mdxOptions: {
                remarkPlugins: [remarkGfm, collectHeadings(headings)],
                rehypePlugins: [
                    rehypeSlug,
                    [
                        rehypeShiki,
                        {
                            // Two themes, no baked-in colour. Shiki emits both as
                            // `--shiki-light` / `--shiki-dark` custom properties
                            // and `content.css` picks one, so a code block follows
                            // the reader's appearance the same way every other
                            // surface does — rather than being the one element on
                            // the page that ignores dark mode.
                            themes: { light: 'vitesse-light', dark: 'vitesse-dark' },
                            defaultColor: false,
                        },
                    ],
                ],
            },
        },
    });

    return { content, headings };
}
