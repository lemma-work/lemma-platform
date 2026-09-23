import type { Metadata } from 'next';

import { RemixAppClient } from './remix-app-client';

export const metadata: Metadata = {
    title: 'Remix on Lemma',
    description: 'Rebuild or adapt an app with Lem, your pod&rsquo;s default agent.',
    robots: {
        index: false,
        follow: false,
    },
};

export default async function RemixPage({
    searchParams,
}: {
    searchParams: Promise<{ source?: string | string[] }>;
}) {
    const query = await searchParams;
    const source = Array.isArray(query.source) ? query.source[0] : query.source;
    return <RemixAppClient source={source} />;
}
