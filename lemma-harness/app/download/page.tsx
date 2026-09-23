import type { Metadata } from 'next';

import { DownloadPage } from '@/components/download/download-page';
import { fetchLatestDesktopRelease } from '@/lib/desktop/desktop-release';

export const metadata: Metadata = {
    title: 'Download Lemma',
    description:
        'Install the Lemma desktop app to connect your computer, so agents that live on it can pick up work from your workspace.',
};

// The release read is cached for an hour inside the fetch; this keeps the page
// itself static between those reads instead of rendering per visitor.
export const revalidate = 3600;

export default async function Download() {
    const release = await fetchLatestDesktopRelease();
    return <DownloadPage release={release} />;
}
