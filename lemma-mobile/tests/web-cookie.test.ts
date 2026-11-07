import { runInNewContext } from "node:vm";
import { describe, expect, it } from "vitest";
import { probeScript, seedScript, signInRedirect, UNSTICK_MARKER } from "../src/session/web-cookie-script";

const API = "https://api.lemma.work";
const WEB = "https://lemma.work";

interface Call {
    url: string;
    init: { method?: string; credentials?: string; headers?: Record<string, string>; body?: string };
}

/** Run a script the way the hidden WebView does, against answers by path,
 *  and return what it posted back and what it asked for. */
async function run(script: string, answers: Record<string, number[] | "throws">): Promise<{ said: { nonce: string; kind: string; body: unknown }[]; calls: Call[] }> {
    const said: { nonce: string; kind: string; body: unknown }[] = [];
    const calls: Call[] = [];
    const fetch = (url: string, init: Call["init"] = {}) => {
        calls.push({ url, init });
        const path = url.slice(API.length);
        const queue = answers[path];
        if (queue === "throws") return Promise.reject(new TypeError("Network request failed"));
        const status = queue?.shift() ?? 404;
        return Promise.resolve({ ok: status >= 200 && status < 300, status, json: () => Promise.resolve({}) });
    };
    const window = { ReactNativeWebView: { postMessage: (data: string) => said.push(JSON.parse(data)) } } as Record<string, unknown>;
    runInNewContext(script, { window, fetch, JSON, String });
    for (let i = 0; i < 20 && said.length === 0; i += 1) await new Promise((resolve) => setTimeout(resolve, 0));
    return { said, calls };
}

describe("checking the WebViews' cookie session", () => {
    it("is in when /users/me answers", async () => {
        const { said, calls } = await run(probeScript(API, "n1"), { "/users/me": [200] });
        expect(said).toEqual([{ nonce: "n1", kind: "in", body: null }]);
        expect(calls[0].init.credentials).toBe("include");
        expect(calls).toHaveLength(1);
    });

    it("refreshes once, as a pod app would, before deciding", async () => {
        const { said, calls } = await run(probeScript(API, "n2"), { "/users/me": [401, 200], "/st/auth/session/refresh": [200] });
        expect(said.map((one) => one.kind)).toEqual(["in"]);
        const refresh = calls.find((call) => call.url.endsWith("/st/auth/session/refresh"));
        expect(refresh?.init).toMatchObject({ method: "POST", credentials: "include", headers: { rid: "session", "st-auth-mode": "cookie" } });
    });

    it("is out when the refresh is refused: the session has ended", async () => {
        const { said } = await run(probeScript(API, "n3"), { "/users/me": [401], "/st/auth/session/refresh": [401] });
        expect(said.map((one) => one.kind)).toEqual(["out"]);
    });

    it("is unsure, not out, when the server or the network fails", async () => {
        expect((await run(probeScript(API, "n4"), { "/users/me": [502] })).said.map((one) => one.kind)).toEqual(["unsure"]);
        expect((await run(probeScript(API, "n5"), { "/users/me": "throws" })).said.map((one) => one.kind)).toEqual(["unsure"]);
    });
});

describe("making the cookie session again", () => {
    const seed = { requestId: "2GSg8CAqzhCX8qhib38KZk3d2-Fisgta", verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk" };

    it("trades the verifier in cookie mode, then confirms it", async () => {
        const { said, calls } = await run(seedScript(API, "s1", seed), { "/auth/desktop/session": [200], "/users/me": [200] });
        expect(said.map((one) => one.kind)).toEqual(["in"]);
        const trade = calls[0];
        expect(trade.url).toBe(API + "/auth/desktop/session");
        expect(trade.init).toMatchObject({ method: "POST", credentials: "include", headers: { "st-auth-mode": "cookie" } });
        expect(JSON.parse(trade.init.body ?? "")).toEqual({ request_id: seed.requestId, code_verifier: seed.verifier });
    });

    it("is out when the trade is refused", async () => {
        const { said, calls } = await run(seedScript(API, "s2", seed), { "/auth/desktop/session": [404] });
        expect(said.map((one) => one.kind)).toEqual(["out"]);
        expect(calls).toHaveLength(1);
    });

    it("answers only with its own nonce, whatever the page holds", async () => {
        const { said } = await run(probeScript(API, "\"});alert(1);//"), { "/users/me": [200] });
        expect(said[0].nonce).toBe("\"});alert(1);//");
    });
});

describe("a page stuck on SuperTokens' half-cleared marker", () => {
    const cookies = (start: string[]) => {
        let jar = [...start];
        const document = {
            get cookie() { return jar.join("; "); },
            set cookie(value: string) {
                const name = value.split("=")[0];
                jar = jar.filter((one) => !one.startsWith(name + "="));
                if (!/expires=Thu, 01 Jan 1970/.test(value)) jar.push(value.split(";")[0]);
            },
        };
        runInNewContext(UNSTICK_MARKER, { document });
        return jar;
    };

    it("loses the marker when there is no front token, so the SDK asks the server", () => {
        expect(cookies(["st-last-access-token-update=1730000000000", "other=1"])).toEqual(["other=1"]);
    });

    it("keeps it beside a front token, and does nothing on a clean host", () => {
        expect(cookies(["st-last-access-token-update=1", "sFrontToken=abc"])).toEqual(["st-last-access-token-update=1", "sFrontToken=abc"]);
        expect(cookies([])).toEqual([]);
    });
});

describe("a page sent to sign in", () => {
    const app = "https://trip.apps.lemma.work/";

    it("is caught on this server's sign-in, and goes back where it was going", () => {
        const sent = `${WEB}/auth?redirect_uri=${encodeURIComponent(app + "plans/3")}`;
        expect(signInRedirect(sent, WEB, app)).toBe(app + "plans/3");
        expect(signInRedirect(`${WEB}/auth/login`, WEB, app)).toBe(app);
    });

    it("goes back to the page itself when the trip names nowhere safe", () => {
        expect(signInRedirect(`${WEB}/auth?redirect_uri=javascript:alert(1)`, WEB, app)).toBe(app);
    });

    it("is not any other navigation", () => {
        expect(signInRedirect(app + "auth", WEB, app)).toBeNull();
        expect(signInRedirect(`${WEB}/authors`, WEB, app)).toBeNull();
        expect(signInRedirect(`${WEB}/pods/1`, WEB, app)).toBeNull();
        expect(signInRedirect("https://evil.example/auth", WEB, app)).toBeNull();
        expect(signInRedirect("not a url", WEB, app)).toBeNull();
    });
});
