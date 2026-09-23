import type { Metadata } from 'next';
import { DocsHome } from '@/components/docs/docs-shell';
import { socialCardPath } from '@/lib/share/social-card';

const image = socialCardPath({
  variant: 'build',
  title: 'Build on Lemma.',
  detail: 'Apps, agents, workflows, and data—open and programmable.',
  label: 'lemma.work/docs',
});

export const metadata: Metadata = {
  title: 'Documentation',
  description: 'Learn the Lemma platform, SDK, and CLI.',
  openGraph: {
    title: 'Build on Lemma.',
    description: 'Learn the platform, SDK, and CLI.',
    type: 'website',
    images: [{ url: image, width: 1200, height: 630, alt: 'Build on Lemma' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Build on Lemma.',
    description: 'Learn the platform, SDK, and CLI.',
    images: [image],
  },
};

export default function DocsPage() {
  return <DocsHome />;
}
