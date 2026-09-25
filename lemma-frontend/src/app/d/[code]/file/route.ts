import type { NextRequest } from "next/server";
import { configuredApiUrl, MISSING_API_URL } from "@/session/origins";
import { sharedFileHeaders } from "@/site/shared-file-headers";

/** The bytes, proxied.
 *
 *  A shared image cannot be pointed straight at `{api}/s/{code}`: that is a
 *  second fetch, and the link is counted in opens rather than in bytes, so the
 *  picture on the page would cost the reader one of their fifty every time it
 *  loaded. Going through here does not make it free — the API still counts one
 *  — but it puts the cost somewhere this app can see, name, and cache.
 *
 *  It also gives download somewhere to point. `?save=1` flips the disposition
 *  to an attachment and nothing else changes. */
export const dynamic = "force-dynamic";

export async function GET(request: NextRequest, context: { params: Promise<{ code: string }> }) {
    const { code } = await context.params;
    const save = request.nextUrl.searchParams.get("save") === "1";

    /* 503, not 500: the deployment is missing configuration rather than
       having failed at it, and the reader gets a status that says come back
       instead of one that says this is broken. The variable is named in the
       log, not in the response — it is not this reader's to set. */
    const origin = configuredApiUrl();
    if (!origin) {
        console.error(MISSING_API_URL);
        return new Response("This site is not finished being set up.", {
            status: 503,
            headers: { "content-type": "text/plain; charset=utf-8" },
        });
    }

    const upstream = await fetch(origin + "/s/" + encodeURIComponent(code), { cache: "no-store" });
    if (!upstream.ok || !upstream.body) {
        return new Response("This link is no longer available.", {
            status: upstream.status === 404 ? 404 : 502,
            headers: { "content-type": "text/plain; charset=utf-8" },
        });
    }

    /* The API's rendering decision travels with the bytes; see
       `sharedFileHeaders` for what dropping it cost. */
    const headers = sharedFileHeaders(upstream.headers, save);
    return new Response(upstream.body, { status: 200, headers });
}
