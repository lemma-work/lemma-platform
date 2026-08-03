import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

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
            /isLocalDeployment\(\)\s*\?\s*<LocalAuthRedirect \/>\s*:\s*<LandingPage \/>/,
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
describe("desktop bridge detection", () => {
    it("is read through a store rather than called during render", () => {
        const capabilities = source("lib/desktop/local-capabilities.ts");

        expect(capabilities).toContain("useSyncExternalStore");
        expect(capabilities).toMatch(/export function useDesktopBridge\(\)/);
    });

    it("is not called during render by the steps that gate on it", () => {
        const steps = source("components/onboarding/local-setup-steps.tsx");

        // The hook, never the bare function: the bare one is for event
        // handlers, where there is no server render to get wrong.
        expect(steps).toContain("useDesktopBridge()");
        expect(steps).not.toContain("desktopBridgeAvailable()");
    });
});
