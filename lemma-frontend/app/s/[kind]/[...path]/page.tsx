import type { Metadata } from 'next';
import { notFound } from 'next/navigation';

import { ContactLanding } from './contact-landing';
import { ShareLanding } from './share-landing';
import { contactCardDownloadPath, readContactCardSpec } from '@/lib/share/contact-card';
import { config } from '@/lib/config';
import {
    getShareKindCopy,
    isShareKind,
    resolveShareDestination,
    resolveShareName,
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

/** The query as the card reader wants it, first value wins. */
function toSearchParams(query: Record<string, string | string[] | undefined>): URLSearchParams {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
        const first = firstValue(value);
        if (first !== undefined) params.set(key, first);
    }
    return params;
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

    // Read only for the one kind that has one, so an ordinary share link never
    // pays for parsing params it does not carry.
    const search = kind === 'contact' ? toSearchParams(query) : null;
    const contact = search ? readContactCardSpec(search, name) : null;
    const downloadHref = search ? contactCardDownloadPath(raw.path, search) : null;
    // Built from the canonical site URL rather than read off the client after
    // mount: the QR is the point of a card on a screen someone else is looking
    // at, and one that appears a beat late is one nobody scans.
    const pageUrl = search
        ? `${config.SITE_URL.replace(/\/$/, '')}/s/${kind}/${(raw.path ?? []).join('/')}?${search}`
        : null;

    return { kind, copy, destination, name, card, target, contact, downloadHref, pageUrl };
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

    // A contact card answers a reader who has no session and is not going to get
    // one, so it renders straight from the link — no access check, nothing
    // fetched, none of the signed-in branching `ShareLanding` exists to do.
    if (share.kind === 'contact' && share.contact && share.downloadHref && share.pageUrl) {
        return (
            <ContactLanding
                card={share.contact}
                destination={share.destination}
                downloadHref={share.downloadHref}
                pageUrl={share.pageUrl}
            />
        );
    }

    return (
        <ShareLanding
            destination={share.destination}
            name={share.name}
            kind={share.kind}
            target={share.target}
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
