import type { Metadata } from 'next';
import { notFound, redirect } from 'next/navigation';
import { DocsPageView } from '@/components/docs/docs-shell';
import { docsPages, getDocsPageFromSegments } from '@/lib/data/docs';
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

  return <DocsPageView page={page} />;
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
