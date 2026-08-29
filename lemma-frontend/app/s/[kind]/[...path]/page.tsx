import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { ShareLanding } from './share-landing';
import {
    getShareKindCopy,
    isShareKind,
    resolveShareDestination,
    resolveShareName,
    resolveSharePodId,
    resolveShareTarget,
    SHARE_NAME_PARAM,
    type ShareKind,
} from '@/lib/share/share-link';
import { resolveSocialCardSpec, socialCardPath } from '@/lib/share/social-card';

interface SharePageProps {
    params: Promise<{ kind: string; path?: string[] }>;
    searchParams: Promise<Record<string, string | string[] | undefined>>;
}

function firstValue(value: string | string[] | undefined): string | undefined {
    return Array.isArray(value) ? value[0] : value;
}

/**
 * Everything the card says comes from the link itself — the kind, the slug, and
 * fixed per-kind copy. No backend call, so this page can render for a crawler
 * that will never hold a session, without exposing anything the link did not
 * already carry.
 */
async function readShare({ params, searchParams }: SharePageProps) {
    const [raw, query] = await Promise.all([params, searchParams]);
    if (!isShareKind(raw.kind)) return null;

    const destination = resolveShareDestination(raw.path, query);
    if (!destination) return null;

    const kind: ShareKind = raw.kind;
    const copy = getShareKindCopy(kind);
    const name = resolveShareName({
        name: firstValue(query[SHARE_NAME_PARAM]),
        segments: raw.path,
        query,
    });
    const card = resolveSocialCardSpec({ variant: copy.variant, title: name });
    // Resolved on the server from the link alone — no backend call, so this page
    // still renders for a crawler that will never hold a session.
    const target = resolveShareTarget(kind, raw.path, query);
    const podId = resolveSharePodId(raw.path);

    return { kind, copy, destination, name, card, target, podId };
}

export async function generateMetadata(props: SharePageProps): Promise<Metadata> {
    const share = await readShare(props);
    if (!share) return { title: 'Lemma' };

    const title = share.name ? `${share.name} on Lemma` : `${share.copy.noun} on Lemma`;
    const description = share.card.detail;
    const image = socialCardPath({
        variant: share.copy.variant,
        title: share.name,
        label: 'lemma.work',
    });

    return {
        title,
        description,
        // Deliberately not a blanket `noindex`: X's card crawler honours that
        // directive and refuses to render a preview, which would defeat the
        // only reason this page exists. Google is named directly instead, so
        // shared slugs stay out of search while every social crawler still
        // gets its card.
        robots: { follow: false, googleBot: { index: false, follow: false } },
        openGraph: {
            title,
            description,
            type: 'website',
            images: [{ url: image, width: 1200, height: 630, alt: title }],
        },
        twitter: {
            card: 'summary_large_image',
            title,
            description,
            images: [image],
        },
    };
}

export default async function SharePage(props: SharePageProps) {
    const share = await readShare(props);
    if (!share) notFound();

    return (
        <ShareLanding
            destination={share.destination}
            name={share.name}
            kind={share.kind}
            target={share.target}
            podId={share.podId}
            article={share.copy.article}
            detail={share.card.detail}
            cardPath={socialCardPath({
                variant: share.copy.variant,
                title: share.name,
                label: 'lemma.work',
            })}
        />
    );
}
