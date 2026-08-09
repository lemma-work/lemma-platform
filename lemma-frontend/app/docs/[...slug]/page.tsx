import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';
import { DocsPageView } from '@/components/docs/docs-shell';
import { JsonLd } from '@/components/seo/json-ld';
import { docsPages, getDocsPageFromSegments, type DocsPage } from '@/lib/data/docs';
import {
  breadcrumbSchema,
  techArticleSchema,
  type BreadcrumbItem,
} from '@/lib/seo/structured-data';
import { socialCardPath } from '@/lib/share/social-card';

type DocsRouteProps = {
  params: Promise<{
    slug?: string[];
  }>;
};

export function generateStaticParams() {
  return docsPages.map((page) => ({
    slug: page.slug.split('/'),
  }));
}

export async function generateMetadata({ params }: DocsRouteProps): Promise<Metadata> {
  const { slug } = await params;
  const groupRoot = getGroupRootRedirect(slug);
  if (groupRoot) {
    const page = getDocsPageFromSegments(groupRoot.split('/'));
    return {
      title: page ? `${page.title} Documentation` : 'Documentation',
      description: page?.description,
    };
  }

  const page = getDocsPageFromSegments(slug);

  if (!page) {
    return {
      title: 'Documentation',
    };
  }

  return {
    title: `${page.title} Documentation`,
    description: page.description,
    alternates: {
      canonical: `/docs/${page.slug}`,
    },
    openGraph: {
      title: `${page.title} | Lemma Docs`,
      description: page.description,
      type: 'article',
      images: [{
        url: socialCardPath({
          variant: 'build',
          title: page.title,
          detail: page.description,
          label: `lemma.work/docs/${page.slug}`,
        }),
        width: 1200,
        height: 630,
        alt: `${page.title} | Lemma Docs`,
      }],
    },
    twitter: {
      card: 'summary_large_image',
      title: `${page.title} | Lemma Docs`,
      description: page.description,
      images: [socialCardPath({
        variant: 'build',
        title: page.title,
        detail: page.description,
        label: `lemma.work/docs/${page.slug}`,
      })],
    },
  };
}

export default async function DocsRoute({ params }: DocsRouteProps) {
  const { slug } = await params;
  const groupRoot = getGroupRootRedirect(slug);
  if (groupRoot) {
    redirect(`/docs/${groupRoot}`);
  }

  const page = getDocsPageFromSegments(slug);

  if (!page) {
    notFound();
  }

  if (page.slug === 'overview') {
    redirect('/docs');
  }

  return (
    <>
      <JsonLd
        schema={[
          techArticleSchema({
            title: page.title,
            description: page.description,
            path: `/docs/${page.slug}`,
            section: page.group,
          }),
          breadcrumbSchema(docsBreadcrumbs(page)),
        ]}
      />
      <DocsPageView page={page} />
    </>
  );
}

/**
 * Home → Documentation → group → page.
 *
 * The group crumb only earns a place when it is a real destination. Groups like
 * `cli` and `sdk` prefix their pages' slugs and redirect to a landing page, so
 * `/docs/cli` resolves; `Start` and `Concepts` are sidebar headings with no URL
 * behind them, and a crumb pointing nowhere is worse than one less crumb.
 */
function docsBreadcrumbs(page: DocsPage): BreadcrumbItem[] {
  const [prefix, ...rest] = page.slug.split('/');
  const groupIsRoutable = rest.length > 0 && getGroupRootRedirect([prefix]) !== null;

  return [
    { name: 'Lemma', path: '/' },
    { name: 'Documentation', path: '/docs' },
    ...(groupIsRoutable ? [{ name: page.group, path: `/docs/${prefix}` }] : []),
    { name: page.title },
  ];
}

function getGroupRootRedirect(slug?: string[]): string | null {
  const value = slug?.join('/');
  if (value === 'platform') return 'platform/missions-and-pods';
  if (value === 'sdk') return 'sdk/installation';
  if (value === 'cli') return 'cli/overview';
  if (value === 'guides') return 'guides/build-a-app';
  if (value === 'reference') return 'reference/commands';
  return null;
}
