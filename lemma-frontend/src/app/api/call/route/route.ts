import { routeWithJev, validRouterState } from "@/call/jev-router";

export const runtime = "nodejs";
let pending = 0;
export async function POST(request: Request) {
    // Next's custom server builds request.url from its bind address (0.0.0.0),
    // not the browser-facing hostname. Host is the actual request authority.
    const url = new URL(request.url);
    const host = request.headers.get("host") ?? url.host;
    const expectedOrigin = `${url.protocol}//${host}`;
    if (request.headers.get("origin") !== expectedOrigin) return Response.json({ error: "Invalid origin" }, { status: 403 });
    if (!process.env.TYPESAFE_API_KEY) return Response.json({ error: "Call routing isn’t set up on this server." }, { status: 503 });
    if (pending >= 8) return Response.json({ error: "Router busy" }, { status: 429 });
    pending++;
    try {
        // Read with a real byte limit, including chunked requests.
        const reader = request.body?.getReader();
        if (!reader) return Response.json({ error: "Missing state" }, { status: 400 });
        const chunks: Uint8Array[] = [];
        let size = 0;
        for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            size += value.byteLength;
            if (size > 2000000) { await reader.cancel(); return Response.json({ error: "State too large" }, { status: 413 }); }
            chunks.push(value);
        }
        let state: unknown;
        try { state = JSON.parse(Buffer.concat(chunks).toString("utf8")); }
        catch { return Response.json({ error: "Invalid JSON" }, { status: 400 }); }
        if (!validRouterState(state)) return Response.json({ error: "Invalid state" }, { status: 400 });
        const decision = await routeWithJev(state, { apiKey: process.env.TYPESAFE_API_KEY, model: process.env.TYPESAFE_MODEL, signal: request.signal });
        return Response.json(decision, { headers: { "Cache-Control": "no-store" } });
    } catch {
        return Response.json({ error: "Call routing is unavailable. No request was dispatched." }, { status: 502 });
    } finally { pending--; }
}
