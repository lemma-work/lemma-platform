import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/* The phone and the server, stood in for: which platform, whether Apple's
   sheet is there, and what the server answers. */
const phone = vi.hoisted(() => ({ OS: "ios" as string, sheet: true, answer: null as null | { status: number; body: unknown } | "unreachable" }));
const asked = vi.hoisted(() => [] as string[]);
vi.mock("react-native", () => ({ Platform: phone }));
vi.mock("expo-secure-store", () => ({}));
vi.mock("expo-apple-authentication", () => ({ isAvailableAsync: async () => phone.sheet }));
vi.mock("expo/fetch", () => ({
    fetch: async (url: string) => {
        asked.push(url);
        const answer = phone.answer;
        if (!answer || answer === "unreachable") throw new TypeError("Network request failed");
        return { status: answer.status, ok: answer.status >= 200 && answer.status < 300, json: async () => answer.body };
    },
}));

import { offersApple } from "../src/session/apple";

const SERVER = { id: "hosted", label: "lemma.work", api: "https://api.example", web: "https://example" };
const OFFER = { status: "OK", urlWithQueryParams: "https://appleid.apple.com/auth/authorize?client_id=work.lemma.mobile" };

beforeEach(() => {
    phone.OS = "ios";
    phone.sheet = true;
    phone.answer = { status: 200, body: OFFER };
    asked.length = 0;
});

afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
});

describe("Continue with Apple", () => {
    it("is offered on an iPhone when the server answers for the phone's client", async () => {
        expect(await offersApple(SERVER)).toBe(true);
        const [url] = asked;
        expect(new URL(url).pathname).toBe("/st/auth/authorisationurl");
        expect(new URL(url).searchParams.get("clientType")).toBe("ios");
    });

    it("is hidden on Android, without asking the server", async () => {
        phone.OS = "android";
        expect(await offersApple(SERVER)).toBe(false);
        expect(asked).toEqual([]);
    });

    it("is hidden where Apple's sheet is not there, without asking the server", async () => {
        phone.sheet = false;
        expect(await offersApple(SERVER)).toBe(false);
        expect(asked).toEqual([]);
    });

    it("is hidden on a 400, a 404, a captive portal's 200, and no answer", async () => {
        for (const answer of [{ status: 400, body: {} }, { status: 404, body: null }, { status: 200, body: "<html>Sign in to the Wi-Fi</html>" }, "unreachable" as const]) {
            phone.answer = answer;
            expect(await offersApple(SERVER), JSON.stringify(answer)).toBe(false);
        }
    });

    it("says why once per server in a development build", async () => {
        vi.stubGlobal("__DEV__", true);
        const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
        phone.answer = { status: 400, body: {} };
        await offersApple({ ...SERVER, id: "dev-stack", label: "Dev stack" });
        await offersApple({ ...SERVER, id: "dev-stack", label: "Dev stack" });
        expect(warn).toHaveBeenCalledTimes(1);
        expect(warn.mock.calls[0][0]).toMatch(/hidden for Dev stack: .*400/);
    });

    it("says nothing in a release build", async () => {
        vi.stubGlobal("__DEV__", false);
        const warn = vi.spyOn(console, "warn").mockImplementation(() => undefined);
        phone.answer = { status: 400, body: {} };
        await offersApple({ ...SERVER, id: "release" });
        expect(warn).not.toHaveBeenCalled();
    });
});
