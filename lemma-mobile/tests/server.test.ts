import { describe, expect, it } from "vitest";
import { HOSTED, addressFrom, customServer, serverFrom, serversFrom } from "../src/session/server";

describe("a typed address", () => {
    it("is a port on this computer when it is only a number", () => {
        expect(addressFrom("8761")).toBe("http://10.0.2.2:8761");
    });

    it("uses http for a local address and https for anything else", () => {
        expect(addressFrom("localhost:8761")).toBe("http://localhost:8761");
        expect(addressFrom("192.168.1.20:8000")).toBe("http://192.168.1.20:8000");
        expect(addressFrom("api.example.com")).toBe("https://api.example.com");
        expect(addressFrom("http://api.example.com/")).toBe("http://api.example.com");
    });

    it("is refused when it is not an address", () => {
        expect(addressFrom("")).toBeNull();
        expect(addressFrom("not an address")).toBeNull();
        expect(addressFrom("example")).toBeNull();
    });
});

describe("a custom server", () => {
    it("finds its web app at the API's own domain", () => {
        expect(customServer("api.example.com", "")).toMatchObject({ api: "https://api.example.com", web: "https://example.com" });
    });

    it("needs the web app's address when the API is not at api.something", () => {
        expect(customServer("localhost:8761", "")).toBeNull();
        expect(customServer("localhost:8761", "localhost:3761")).toMatchObject({ api: "http://localhost:8761", web: "http://localhost:3761" });
    });

    it("is the named server when it is one", () => {
        expect(customServer("api.lemma.work", "")).toBe(HOSTED);
    });
});

describe("the saved choice", () => {
    it("is lemma.work when nothing was saved, or it will not read", () => {
        expect(serverFrom(null)).toBe(HOSTED);
        expect(serverFrom("{nope")).toBe(HOSTED);
    });

    it("keeps a named server by its id", () => {
        expect(serverFrom(JSON.stringify({ id: "hosted" }))).toBe(HOSTED);
    });
});

describe("the servers a build names", () => {
    const STAGING = { id: "staging", label: "staging.example.com", api: "https://api.staging.example.com/", web: "https://staging.example.com" };

    it("reads each from the JSON list in EXPO_PUBLIC_LEMMA_SERVERS", () => {
        expect(serversFrom(JSON.stringify([STAGING]))).toEqual([{ ...STAGING, api: "https://api.staging.example.com" }]);
        expect(serversFrom(JSON.stringify([{ ...STAGING, label: undefined }]))[0].label).toBe("api.staging.example.com");
    });

    it("leaves out what will not read, and never shadows lemma.work", () => {
        expect(serversFrom(undefined)).toEqual([]);
        expect(serversFrom("{nope")).toEqual([]);
        expect(serversFrom(JSON.stringify({ id: "staging" }))).toEqual([]);
        expect(serversFrom(JSON.stringify([{ id: "half", api: "https://api.example.com" }, { ...STAGING, id: "hosted" }]))).toEqual([]);
    });
});
