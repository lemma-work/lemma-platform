import { siteRuntime, siteRuntimeScript } from "@/site/runtime";

/* Per request, never prerendered: a prerendered copy is the build's
   environment frozen into a file, which is exactly what this route exists to
   avoid. See `site/runtime.ts`. */
export const dynamic = "force-dynamic";

export function GET() {
    return new Response(siteRuntimeScript(siteRuntime()), {
        headers: {
            "Content-Type": "application/javascript; charset=utf-8",
            "Cache-Control": "no-store",
        },
    });
}
