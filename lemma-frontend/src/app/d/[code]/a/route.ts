import type { NextRequest } from "next/server";
import { configuredApiUrl, MISSING_API_URL } from "@/session/origins";
import { sharedFileHeaders } from "@/site/shared-file-headers";

/** Something a shared page embeds — a picture, a stylesheet, a script — by
 *  the reference the page writes it with. See `embedded.ts` for which
 *  references are sent here, and the API's `embedded_references.py` for which
 *  it agrees to serve.
 *
 *  Not counted against the page's opens: a reader looking at a page with eight
 *  pictures opened it once. The API meters these against an allowance of their
 *  own instead.
 *
 *  Cross-origin readable, because the page that asks is a sandboxed frame with
 *  an opaque origin, and a font loaded from its stylesheet is refused without
 *  this. It grants nothing a plain `<img>` would not already fetch. */
export const dynamic = "force-dynamic";

const PASSED_THROUGH = ["etag", "content-range", "accept-ranges"];

export async function GET(request: NextRequest, context: { params: Promise<{ code: string }> }) {
    const { code } = await context.params;
    const reference = request.nextUrl.searchParams.get("ref");
    if (!reference) return new Response(null, { status: 404 });

    const origin = configuredApiUrl();
    if (!origin) {
        console.error(MISSING_API_URL);
        return new Response(null, { status: 503 });
    }

    /* A video in a report seeks with ranges, and a revalidation should stay a
       304 rather than a second download. */
    const forwarded = new Headers();
    for (const name of ["range", "if-none-match"]) {
        const value = request.headers.get(name);
        if (value) forwarded.set(name, value);
    }
    const upstream = await fetch(
        origin + "/s/" + encodeURIComponent(code) + "/a?ref=" + encodeURIComponent(reference),
        { cache: "no-store", headers: forwarded },
    );
    if (upstream.status === 304) {
        return new Response(null, { status: 304, headers: { etag: upstream.headers.get("etag") ?? "" } });
    }
    if (!upstream.ok || !upstream.body) {
        return new Response(null, { status: upstream.status === 410 ? 410 : 404 });
    }

    const headers = sharedFileHeaders(upstream.headers, false);
    for (const name of PASSED_THROUGH) {
        const value = upstream.headers.get(name);
        if (value) headers.set(name, value);
    }
    headers.set("access-control-allow-origin", "*");
    return new Response(upstream.body, { status: upstream.status, headers });
}
