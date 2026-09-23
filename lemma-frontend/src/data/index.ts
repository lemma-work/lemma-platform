import { liveSource } from "./live";
import { key } from "@/session/storage";
import type { PodSource } from "./types";
import { isLandingPreview } from "@/marketing/preview-mode";

/** Which source this browser gets, given what the deployment was built with
 *  and what the browser asked for.
 *
 *  Pure and exported so it can be tested: the rule below is a boundary, and a
 *  boundary nothing tests is a boundary that moves.
 *
 *  Live by default — this app exists to show your pods. The sample source is
 *  opt-in, for judging layout where there is no session to be had.
 *
 *  The browser override is a development affordance and is honoured only
 *  there. In a production build the deployment decides, full stop: sample mode
 *  is passed straight through `SessionGate` — that is the whole point of it,
 *  a mode with no backend to authenticate against — so a localStorage key that
 *  worked anywhere would be a way for anybody to skip the sign-in door on the
 *  real site and land in something that looks like the app. Fixtures are not
 *  somebody's data, so this is not a leak; it is a stranger being shown a
 *  convincing workspace that is not theirs and never was.
 */
export function dataSource(built: string | undefined, stored: string | null, production: boolean): string {
    const deployed = built ?? "live";
    return production ? deployed : stored ?? deployed;
}

function chosen(): string {
    const production = process.env.NODE_ENV === "production";
    let stored: string | null = null;
    try {
        stored = localStorage.getItem(key("data"));
    } catch {
        /* No storage to read — server render, or a browser refusing it. */
    }
    return dataSource(process.env.NEXT_PUBLIC_DATA, stored, production);
}

// Sample content is only fetched when explicitly selected, outside the live bundle.
const sampleSource = new Proxy({} as PodSource, {
    get: (_target, method: keyof PodSource) => method === "label" ? "sample" : async (...args: unknown[]) => {
        const { fixtureSource } = await import("./fixtures");
        const operation = fixtureSource[method] as (...args: unknown[]) => unknown;
        return operation(...args);
    },
});
const landingSource = new Proxy({} as PodSource, {
    get: (_target, method: keyof PodSource) => method === "label" ? "sample" : async (...args: unknown[]) => {
        const { previewSource } = await import("@/marketing/preview-source");
        const operation = previewSource[method] as (...args: unknown[]) => unknown;
        return operation(...args);
    },
});
export const source: PodSource = isLandingPreview() ? landingSource : chosen() === "sample" ? sampleSource : liveSource;

export * from "./types";
export * from "./agents";
export * from "./agent-names";
export * from "./connectable";
export * from "./accounts";
export * from "./runtimes";
export * from "./joining";
export * from "./trouble";
