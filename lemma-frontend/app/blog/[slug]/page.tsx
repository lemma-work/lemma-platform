import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { ArticleShell } from '@/components/content/article-shell';
import { JsonLd } from '@/components/seo/json-ld';
import { compileContent } from '@/lib/content/compile';
import { getContent, listContent } from '@/lib/content/loader';
import { breadcrumbSchema, techArticleSchema } from '@/lib/seo/structured-data';
import { socialCardPath } from '@/lib/share/social-card';

interface BlogPostProps {
    params: Promise<{ slug: string }>;
}

/**
 * Every post is known at build time, so each one is a static page. Nothing
 * about an article needs a request — and a crawler that runs no JavaScript must
 * receive the whole thing.
 */
export function generateStaticParams() {
    return listContent('blog').map((doc) => ({ slug: doc.slug }));
}

export async function generateMetadata({ params }: BlogPostProps): Promise<Metadata> {
    const { slug } = await params;
    const doc = getContent('blog', slug);
    if (!doc) return {};

    const { title, description, published, updated } = doc.frontmatter;
    const image = socialCardPath({
        variant: 'build',
        title,
        detail: description,
        label: `lemma.work/blog/${slug}`,
    });

    return {
        title,
        description,
        alternates: { canonical: `/blog/${slug}` },
        openGraph: {
            title,
            description,
            type: 'article',
            publishedTime: published,
            modifiedTime: updated ?? published,
            images: [{ url: image, width: 1200, height: 630, alt: title }],
        },
        twitter: { card: 'summary_large_image', title, description, images: [image] },
    };
}

export default async function BlogPostPage({ params }: BlogPostProps) {
    const { slug } = await params;
    const doc = getContent('blog', slug);
    if (!doc) notFound();

    const { content, headings } = await compileContent(doc.body);
    const { title, description, published, updated } = doc.frontmatter;

    return (
        <>
            <JsonLd
                schema={[
                    techArticleSchema({
                        title,
                        description,
                        path: `/blog/${slug}`,
                        published,
                        modified: updated,
                    }),
                    breadcrumbSchema([
                        { name: 'Lemma', path: '/' },
                        { name: 'Blog', path: '/blog' },
                        { name: title },
                    ]),
                ]}
            />
            <ArticleShell
                frontmatter={doc.frontmatter}
                headings={headings}
                siblings={listContent('blog')}
                slug={slug}
            >
                {content}
            </ArticleShell>
        </>
    );
}
