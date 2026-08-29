/* eslint-disable no-restricted-syntax -- The photo is rasterised by Satori, which lays out with serializable inline styles rather than app CSS. */
import { ImageResponse } from 'next/og';
import type { NextRequest } from 'next/server';

import { config } from '@/lib/config';
import { beingDataUri } from '@/lib/identity/being-svg';
import {
    buildVCard,
    contactCardFilename,
    contactCardParams,
    readContactCardSpec,
    type ContactCardSpec,
    type VCardPhoto,
} from '@/lib/share/contact-card';
import { SHARE_NAME_PARAM } from '@/lib/share/share-link';
import { identityVariantSeed, parseResourceIcon } from '@/lib/utils/resource-icon-value';

export const runtime = 'edge';

/**
 * Large enough that a contact app's own crop still has pixels to work with,
 * small enough that the base64 of it stays a few kilobytes — the whole file is
 * copied into an address book and synced to every device on the account.
 */
const PHOTO_SIZE = 256;

/** A picture past this is not an icon; refuse it rather than inline it. */
const MAX_ICON_BYTES = 512 * 1024;

/**
 * Base64 without `Buffer`, which the edge runtime does not have.
 *
 * Chunked because `String.fromCharCode(...bytes)` spreads every byte as an
 * argument, and a photo is tens of thousands of them — enough to overflow the
 * call stack on exactly the inputs this is for.
 */
function base64(bytes: Uint8Array): string {
    let binary = '';
    for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
    }
    return btoa(binary);
}

/**
 * May we fetch this icon?
 *
 * The URL arrives in a query string anyone can type, and this runs on our own
 * origin — so an unchecked fetch is a request-forgery gadget pointed at whatever
 * the edge can reach. Two conditions, both required: a host we publish from, and
 * the managed icon route, which is the only path on it that serves user
 * pictures. Anything else falls through to the generated face, which is a
 * perfectly good answer rather than an error.
 */
function isFetchableIcon(url: URL, requestOrigin: string): boolean {
    const allowed = new Set(
        [requestOrigin, config.SITE_URL, config.API_URL]
            .map((value) => {
                try {
                    return new URL(value).origin;
                } catch {
                    return null;
                }
            })
            .filter((origin): origin is string => Boolean(origin)),
    );
    return allowed.has(url.origin) && url.pathname.includes('/public/icons/');
}

async function uploadedPhoto(rawUrl: string, requestOrigin: string): Promise<VCardPhoto | null> {
    let url: URL;
    try {
        url = new URL(rawUrl, requestOrigin);
    } catch {
        return null;
    }
    if (!isFetchableIcon(url, requestOrigin)) return null;

    try {
        const response = await fetch(url, { signal: AbortSignal.timeout(4000) });
        if (!response.ok) return null;

        const type = response.headers.get('content-type') ?? '';
        // vCard 3.0 names the format bare, and these are the two every contact
        // app decodes. An SVG or a GIF is served by the icon route but would
        // arrive as an unreadable photo, so it is left to the generated face.
        const format = type.includes('png') ? 'PNG' : type.includes('jpeg') ? 'JPEG' : null;
        if (!format) return null;

        const bytes = new Uint8Array(await response.arrayBuffer());
        if (bytes.length === 0 || bytes.length > MAX_ICON_BYTES) return null;
        return { data: base64(bytes), type: format };
    } catch {
        // A slow or missing icon must not cost someone their contact card.
        return null;
    }
}

/**
 * The agent's generated face, rasterised.
 *
 * The being is an SVG, and no contact app renders one in a `PHOTO` — so it goes
 * through the same Satori pipeline the social card uses, as a data URI, and
 * comes back as the PNG that address books actually draw.
 */
async function generatedPhoto(seed: string): Promise<VCardPhoto | null> {
    try {
        const image = new ImageResponse(
            (
                <div style={{ display: 'flex', width: '100%', height: '100%' }}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={beingDataUri({ seed })} width={PHOTO_SIZE} height={PHOTO_SIZE} alt="" />
                </div>
            ),
            { width: PHOTO_SIZE, height: PHOTO_SIZE },
        );
        return { data: base64(new Uint8Array(await image.arrayBuffer())), type: 'PNG' };
    } catch {
        // A card with no picture is still a card worth saving.
        return null;
    }
}

/**
 * The face to save, resolved by the same rule the workspace draws by: an
 * uploaded picture and a typed emoji are explicit choices, the generated being
 * is what fills the silence.
 *
 * An emoji is the one branch that cannot be honoured. Satori draws glyphs from
 * fonts it is handed, and shipping an emoji font to the edge to render one
 * character costs megabytes per request — so a pod that typed one gets the
 * generated face rather than a blank square where a picture should be.
 */
async function resolvePhoto(
    spec: ContactCardSpec,
    requestOrigin: string,
): Promise<VCardPhoto | null> {
    const icon = parseResourceIcon(spec.icon);

    if (icon?.kind === 'url') {
        const uploaded = await uploadedPhoto(icon.url, requestOrigin);
        if (uploaded) return uploaded;
    }

    const variant = icon?.kind === 'identity' ? icon.variant : 0;
    return generatedPhoto(identityVariantSeed(spec.seed, variant));
}

/**
 * The saveable file behind a contact card's "Save contact" button.
 *
 * Everything it needs is in the query — the same link the page renders from, so
 * the file and the page cannot disagree — which is what lets this answer a
 * reader who has no session and never will.
 *
 * The path mirrors the page's, segment for segment, rather than being a bare
 * endpoint with the identity in the query. That is what lets the file name the
 * page it came from: a saved contact carrying a `URL` back to `/s/contact/…` is
 * a card that can be re-opened and re-saved when a handle moves, which is the
 * only repair available once a vCard is sitting in someone else's phone.
 */
export async function GET(
    request: NextRequest,
    context: { params: Promise<{ path?: string[] }> },
): Promise<Response> {
    const params = request.nextUrl.searchParams;
    const spec = readContactCardSpec(params, params.get(SHARE_NAME_PARAM));
    const segments = (await context.params).path?.filter(Boolean) ?? [];

    // The same shape the share page accepts, checked for the same reason: these
    // segments are pasted into a URL the file hands back to a contact app.
    if (!spec || segments[0] !== 'pod' || !segments[1]) {
        return new Response('This link names no contact.', {
            status: 400,
            headers: { 'Content-Type': 'text/plain; charset=utf-8' },
        });
    }

    const origin = request.nextUrl.origin;
    const photo = await resolvePhoto(spec, origin);
    const page = new URL(`/s/contact/${segments.map(encodeURIComponent).join('/')}`, origin);
    // Rebuilt from the parsed card rather than copied from the incoming query.
    // A catch-all route hands its own segments back in `searchParams`, so
    // copying wrote `&path=pod&path=p1…` into a URL that a contact app keeps
    // forever — and it would carry any other junk the link happened to hold.
    const cardQuery = contactCardParams(spec);
    cardQuery.set(SHARE_NAME_PARAM, spec.name);
    page.search = cardQuery.toString();
    const vcard = buildVCard(spec, {
        photo,
        // Scoped to the pod the path names. An agent's seed is its id and would
        // have been unique already; Lem's is one constant every pod shares, so
        // without this a second pod's Lem would overwrite the first one saved.
        uid: `${segments[1]}:${spec.seed}`,
        // The card's own page, so a saved contact keeps a way back to the thing
        // that minted it — and to a fresher copy of itself.
        url: page.toString(),
    });

    return new Response(vcard, {
        headers: {
            'Content-Type': 'text/vcard; charset=utf-8',
            'Content-Disposition': `attachment; filename="${contactCardFilename(spec)}"`,
            // Everything here is a pure function of the query, so a shared link
            // is worth caching — briefly, since `REV` moves with each mint.
            'Cache-Control': 'public, max-age=300',
            'X-Content-Type-Options': 'nosniff',
        },
    });
}
