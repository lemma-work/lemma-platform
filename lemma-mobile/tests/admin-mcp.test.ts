import { describe, expect, it } from "vitest";
import { grantLine, mcpLine, plainProblem, whoConnected } from "../src/areas/admin/mcp/model";
import { mcpSample } from "../src/areas/admin/mcp/sample";
import { MCP_CLIENTS, fetchMcpUrl, loadConnections, type ConnectedClient } from "@web/space/agent-access-model";

/** Using a Lemma from other AI apps (PARITY.md, F): the link, the apps
 *  connected, and what each says. */

const NOW = Date.parse("2026-10-24T12:00:00Z");

const grant = (extra: Partial<ConnectedClient> = {}): ConnectedClient => ({
    grant_id: "g1", user_id: "u-sam", client_id: "https://claude.example/meta.json", client_name: "Claude",
    scopes: ["pod:read", "pod:write"], connected_at: new Date(NOW - 3 * 86_400_000).toISOString(), last_used_at: null, ...extra,
});

describe("what the screen says", () => {
    it("never shows a status code", () => {
        expect(plainProblem(new Error("Connected apps could not be loaded (500)."), "fallback")).toBe("Connected apps could not be loaded.");
        expect(plainProblem(new Error("It could not be disconnected (403)."), "fallback")).toBe("It could not be disconnected.");
        expect(plainProblem(new Error("Network request failed"), "The link could not be loaded.")).toBe("The link could not be loaded.");
    });

    it("names who connected an app, the viewer first", () => {
        const people = [{ userId: "u-sam", name: "Sam Okafor" }];
        expect(whoConnected("me", "me", people)).toBe("you");
        expect(whoConnected("u-sam", "me", people)).toBe("Sam Okafor");
        expect(whoConnected("u-gone", "me", people)).toBe("a former member");
    });

    it("says where an app is from, what it may do and when it was used", () => {
        expect(grantLine(grant(), "Sam Okafor", NOW)).toBe("from claude.example · by Sam Okafor · Read and write · connected 3d ago");
        expect(grantLine(grant({ client_id: "client-7f3a", scopes: ["pod:read"], last_used_at: new Date(NOW - 20 * 60_000).toISOString() }), null, NOW))
            .toBe("unverified app · Read only · used 20m ago");
    });

    it("draws the Settings line", () => {
        expect(mcpLine(2).text).toBe("2 apps connected");
        expect(mcpLine(0).text).toBe("ChatGPT, Claude, Claude Code");
        expect(mcpLine(null).text).toBe("ChatGPT, Claude, Claude Code");
    });

    it("gives Claude Code a command and the others their clicks", () => {
        const code = MCP_CLIENTS.find((one) => one.id === "claude-code")!;
        expect(code.command?.("https://api.acme.example/mcp/p1", { name: "Support" })).toBe("claude mcp add --transport http lemma-support https://api.acme.example/mcp/p1");
        expect(MCP_CLIENTS.find((one) => one.id === "chatgpt")?.steps("", { name: "Support" })[1]).toContain("Lemma – Support");
    });
});

describe("the web's calls, through the phone's fetch", () => {
    const answer = (status: number, body: unknown) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

    it("reads the link, and null where the server does not offer MCP", async () => {
        expect(await fetchMcpUrl("https://api.acme.example", "p1", (async () => answer(200, { url: "https://api.acme.example/mcp/p1" })) as typeof fetch)).toBe("https://api.acme.example/mcp/p1");
        expect(await fetchMcpUrl("https://api.acme.example", "p1", (async () => answer(404, {})) as typeof fetch)).toBeNull();
    });

    it("falls back to the person's own connections when refused everyone's", async () => {
        const asked: string[] = [];
        const fetcher = (async (url: string) => {
            asked.push(url);
            return url.includes("everyone=true") ? answer(403, {}) : answer(200, { items: [grant()] });
        }) as unknown as typeof fetch;
        const read = await loadConnections("https://api.acme.example", "p1", fetcher);
        expect(read.everyone).toBe(false);
        expect(read.items).toHaveLength(1);
        expect(asked).toHaveLength(2);
    });
});

describe("the sample", () => {
    it("stops and resumes what an app is told about, and disconnects it", async () => {
        const source = mcpSample();
        const podId = "sample-support-1140";
        expect(await source.endpoint(podId)).toMatch(/\.example\//);
        const before = await source.grants(podId);
        expect(before.items).toHaveLength(3);
        await source.listen("grant-notes", "sub-tickets", false);
        const stopped = (await source.grants(podId)).items.find((one) => one.grant_id === "grant-notes")?.listens_to?.find((one) => one.id === "sub-tickets");
        expect(stopped?.stopped_at).toBeTruthy();
        await source.listen("grant-notes", "sub-tickets", true);
        await source.disconnect("grant-chatgpt");
        expect((await source.grants(podId)).items.map((one) => one.client_name)).toEqual(["Claude", "Ticket notes"]);
        expect((await source.grants("sample-books-986")).items).toEqual([]);
    });
});
