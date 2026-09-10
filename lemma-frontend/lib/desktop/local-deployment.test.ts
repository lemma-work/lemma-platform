import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

const source = (path: string) =>
    readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");

/**
 * A local installation is not selling anything.
 *
 * The marketing landing page must never render for a local deployment, in any
 * auth state, for any visitor — the desktop webview, a phone on the same Wi-Fi,
 * or someone holding a public link. The last two arrive in an ordinary browser
 * with no `__LEMMA_DESKTOP__` global, which is why the switch reads the
 * deployment rather than the shell.
 *
 * These are source contracts rather than render tests because this suite is
 * deliberately node-only; what they guard is a wiring mistake that typechecks
 * perfectly and only shows up as a pricing page inside someone's desktop app.
 */
describe("local deployments never serve the landing page", () => {
    it("guards the one landing-page call site on the deployment", () => {
        const switchSource = source("components/root/root-page-switch.tsx");

        expect(switchSource.match(/<LandingPage \/>/g) ?? []).toHaveLength(1);
        expect(switchSource).toMatch(
            /if \(!isLocalDeployment\(\) && !isAuthenticated\) \{\s*return isLoading && hasSessionCookie \? <PageLoader \/> : <LandingPage \/>;/,
        );
    });

    it("sends an unauthenticated local visitor to the account portal", () => {
        // Signup rather than sign-in: an install with an account to sign into
        // would not have sent them to the bare root in the first place.
        expect(source("components/root/root-page-switch.tsx")).toContain(
            "router.replace('/auth?show=signup')",
        );
    });

    it("marks the deployment from the frontend process, not the shell", () => {
        // locald sets this on the Next.js process it supervises, so it is true
        // for every visitor to a local install rather than only the ones inside
        // the desktop webview.
        expect(source("lib/config.ts")).toContain("NEXT_PUBLIC_LEMMA_DEPLOYMENT");
        expect(source("lib/config.ts")).toContain('DEPLOYMENT === "local"');
    });
});

/**
 * The desktop bridge must not be read during render.
 *
 * `desktopBridgeAvailable()` returns false on the server, so a component that
 * calls it in its body renders "this has to be done on the computer running
 * Lemma" into the HTML — which is what the user read while sitting at that
 * computer, inside the desktop app. `useSyncExternalStore` gives React a server
 * snapshot it knows to reconcile and a client snapshot that is actually true.
 */
/**
 * Run `work` with `window` and the deployment set as given, then put the
 * globals back.
 *
 * Modules are reset around each one: `local-capabilities` and what it imports
 * read both at module scope, so a cached copy answers for whatever the last
 * test set. A `location` is supplied because a transitive import reads
 * `window.location.hostname` while it is being evaluated.
 */
async function withEnvironment<T>(
    { deployment, windowValue }: { deployment?: string; windowValue?: object },
    work: () => Promise<T>,
): Promise<T> {
    const globals = globalThis as Record<string, unknown>;
    const previousWindow = globals.window;
    const previousDeployment = process.env.NEXT_PUBLIC_LEMMA_DEPLOYMENT;
    const restore = (key: string, value: string | undefined) => {
        if (value === undefined) delete process.env[key];
        else process.env[key] = value;
    };
    if (windowValue === undefined) delete globals.window;
    else globals.window = { location: { hostname: "lemma.local", port: "" }, ...windowValue };
    restore("NEXT_PUBLIC_LEMMA_DEPLOYMENT", deployment);
    vi.resetModules();
    try {
        return await work();
    } finally {
        if (previousWindow === undefined) delete globals.window;
        else globals.window = previousWindow;
        restore("NEXT_PUBLIC_LEMMA_DEPLOYMENT", previousDeployment);
        vi.resetModules();
    }
}

const SHELL = { __TAURI__: { core: { invoke: () => undefined } } };

describe("desktop bridge detection", () => {
    /**
     * The server render is what the user reads first, and on the server the
     * answer has to be "not in the desktop app" whatever the globals say —
     * otherwise React reconciles a mismatch and, in between, the user is
     * looking at HTML that contradicts where they are sitting.
     *
     * Rendered rather than read: `useSyncExternalStore`'s third argument is the
     * only thing that makes this true, and its presence in the source says
     * nothing about what it returns.
     */
    it("renders as unavailable on the server even inside the desktop app", async () => {
        await withEnvironment({ deployment: "local", windowValue: SHELL }, async () => {
            const { useDesktopBridge } = await import("./local-capabilities");
            const Probe = () => createElement("p", null, String(useDesktopBridge()));

            expect(renderToString(createElement(Probe))).toBe("<p>false</p>");
        });
    });

    /**
     * The non-reactive form is for event handlers, where there is no server
     * render to get wrong — and it has to be safe to call with no `window` at
     * all, because a module that imports it is evaluated on the server too.
     */
    it("answers false with no window rather than throwing", async () => {
        await withEnvironment({ deployment: "local" }, async () => {
            const { desktopBridgeAvailable } = await import("./local-capabilities");

            expect(desktopBridgeAvailable()).toBe(false);
        });
    });

    /**
     * Both halves are required, and each is false on its own: a LAN browser
     * pointed at a local install has the deployment and no shell, and a desktop
     * window showing a cloud workspace has the shell and no local stack.
     */
    it("requires both a local deployment and a reachable shell", async () => {
        const cases: Array<[string | undefined, object, boolean]> = [
            ["local", SHELL, true],
            ["local", {}, false],
            [undefined, SHELL, false],
            ["cloud", SHELL, false],
        ];
        for (const [deployment, windowValue, expected] of cases) {
            await withEnvironment({ deployment, windowValue }, async () => {
                const { desktopBridgeAvailable } = await import("./local-capabilities");

                expect(desktopBridgeAvailable(), `${deployment} / ${JSON.stringify(windowValue)}`)
                    .toBe(expected);
            });
        }
    });

    /**
     * The steps that gate on it must use the hook, not the bare function. This
     * one stays a source contract on purpose: what it guards is a *call site*,
     * and a call site's absence cannot be observed by calling anything.
     */
    it("is not called during render by the steps that gate on it", () => {
        const steps = source("components/onboarding/local-setup-steps.tsx");

        expect(steps).toContain("useDesktopBridge()");
        expect(steps).not.toContain("desktopBridgeAvailable()");
    });
});
