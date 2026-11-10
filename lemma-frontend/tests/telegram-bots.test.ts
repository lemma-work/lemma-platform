import test from "node:test";
import assert from "node:assert/strict";
import type { AgentSurfaceResponse } from "lemma-sdk";
import { botChoices, botDetail, botTitle, linkedWhen, readBots, readLinkedAccounts, readMine, spacesSaid, type TelegramBot } from "../src/session/telegram-bots.ts";

const shared = (surfaceId: string, podName: string, handle = "@lemma_example_bot"): TelegramBot =>
    ({ surfaceId, podId: "pod-" + surfaceId, podName, handle, shared: true });
const own = (surfaceId: string, podName: string, handle: string): TelegramBot =>
    ({ surfaceId, podId: "pod-" + surfaceId, podName, handle, shared: false });

test("Lemma's shared bot is one choice, first, however many spaces answer through it", () => {
    const choices = botChoices([
        own("s-own", "Sales", "@sales_example_bot"),
        shared("s-a", "Marketing"),
        shared("s-b", "Research"),
    ]);
    assert.deepEqual(choices.map((choice) => [choice.shared, choice.handle, choice.spaces]), [
        [true, "@lemma_example_bot", ["Marketing", "Research"]],
        [false, "@sales_example_bot", ["Sales"]],
    ]);
    assert.equal(choices[0].surfaceId, "s-a", "made on the first shared surface");
});

test("the shared bot's code is made on the member's default Telegram space when it is on that bot", () => {
    const choices = botChoices([shared("s-a", "Marketing"), shared("s-b", "Research")], "s-b");
    assert.equal(choices[0].surfaceId, "s-b");
    assert.equal(choices[0].podName, "Research");
    assert.equal(botChoices([shared("s-a", "Marketing")], "s-own")[0].surfaceId, "s-a", "a default on another bot is not this one's");
});

test("a space's own bot is offered once, by its handle, and named by the spaces it answers for", () => {
    const choices = botChoices([
        own("s-2", "Support", "@Acme_Example_Bot"),
        own("s-1", "Billing", "@acme_example_bot"),
        own("s-3", "Analytics", "@numbers_example_bot"),
    ]);
    assert.deepEqual(choices.map((choice) => [choice.handle, choice.spaces]), [
        ["@numbers_example_bot", ["Analytics"]],
        ["@Acme_Example_Bot", ["Support", "Billing"]],
    ]);
});

test("a bot Telegram has not named yet is still its own choice", () => {
    const choices = botChoices([own("s-1", "Billing", ""), own("s-2", "Support", "")]);
    assert.equal(choices.length, 2);
});

test("the shared bot borrows a handle from any of its surfaces that has one", () => {
    const [choice] = botChoices([shared("s-a", "Marketing", ""), shared("s-b", "Research", "@lemma_example_bot")]);
    assert.equal(choice.surfaceId, "s-a");
    assert.equal(choice.handle, "@lemma_example_bot");
});

test("only the Telegram group of /surfaces/me is read, with its spaces once each", () => {
    const mine = readMine({
        groups: [
            { platform: "SLACK", surfaces: [{ id: "slack-1", pod_id: "pod-1" }] },
            {
                platform: "TELEGRAM",
                default_surface_id: "tg-2",
                surfaces: [{ id: "tg-1", pod_id: "pod-1" }, { id: "tg-2", pod_id: "pod-2" }, { id: "tg-3", pod_id: "pod-1" }, { id: 4 }],
            },
        ],
    });
    assert.deepEqual(mine, { surfaceIds: ["tg-1", "tg-2", "tg-3"], podIds: ["pod-1", "pod-2"], preferred: "tg-2" });
    assert.deepEqual(readMine({ groups: [] }), { surfaceIds: [], podIds: [], preferred: null });
    assert.deepEqual(readMine(null), { surfaceIds: [], podIds: [], preferred: null });
});

test("spaces are said the way a person would list them", () => {
    assert.equal(spacesSaid(["Marketing"]), "Marketing");
    assert.equal(spacesSaid(["Marketing", "Sales"]), "Marketing and Sales");
    assert.equal(spacesSaid(["Marketing", "Sales", "Support"]), "Marketing, Sales and Support");
    assert.equal(spacesSaid(["Marketing", "Sales", "Support", "Billing"]), "Marketing, Sales and 2 more");
});

test("a linked account is read with its name, or none when Telegram gave a blank one", () => {
    assert.deepEqual(readLinkedAccounts({
        items: [
            { id: "acct-1", name: " Dana ", proved_at: "2026-11-04T09:00:00Z" },
            { id: "acct-2", name: "  ", proved_at: null },
            { name: "no id" },
        ],
    }), [
        { id: "acct-1", name: "Dana", provedAt: "2026-11-04T09:00:00Z" },
        { id: "acct-2", name: null, provedAt: null },
    ]);
    assert.deepEqual(readLinkedAccounts(null), []);
});

test("when an account was linked reads as a day, never a bare clock", () => {
    const now = new Date(2026, 10, 4, 15, 0);
    assert.equal(linkedWhen(new Date(2026, 10, 4, 9, 30).toISOString(), now), "today");
    assert.equal(linkedWhen(new Date(2026, 9, 1, 9, 30).toISOString(), now), new Date(2026, 9, 1).toLocaleDateString([], { day: "numeric", month: "short" }));
    assert.equal(linkedWhen(null, now), "");
    assert.equal(linkedWhen("not a date", now), "");
});

test("a space's Telegram surfaces are the member's bots only where /surfaces/me named them and they still work", () => {
    const surface = (id: string, extra: Record<string, unknown> = {}) => ({ id, platform: "TELEGRAM", status: "ACTIVE", ...extra }) as unknown as AgentSurfaceResponse;
    const bots = readBots([
        surface("tg-shared", { reach: { handle: "@lemma_example_bot" } }),
        surface("tg-own", { credential_mode: "CUSTOM", surface_identity_username: "@sales_example_bot" }),
        surface("tg-paused", { status: "PAUSED" }),
        surface("tg-other"),
    ], new Set(["tg-shared", "tg-own", "tg-paused"]), { id: "pod-1", name: "Sales" });
    assert.deepEqual(bots, [
        { surfaceId: "tg-shared", podId: "pod-1", podName: "Sales", handle: "@lemma_example_bot", shared: true },
        { surfaceId: "tg-own", podId: "pod-1", podName: "Sales", handle: "@sales_example_bot", shared: false },
    ]);
});

test("a bot is named by what Telegram calls it, with the spaces it answers for under it", () => {
    assert.equal(botTitle({ shared: true, handle: "@lemma_example_bot" }), "Lemma’s bot");
    assert.equal(botTitle({ shared: false, handle: "@sales_example_bot" }), "@sales_example_bot");
    assert.equal(botTitle({ shared: false, handle: "" }), "Telegram bot");
    assert.equal(botDetail({ shared: true, handle: "@lemma_example_bot", spaces: ["Marketing", "Sales"] }), "@lemma_example_bot · Marketing and Sales");
    assert.equal(botDetail({ shared: true, handle: "", spaces: ["Marketing"] }), "Marketing");
    assert.equal(botDetail({ shared: false, handle: "@acme_example_bot", spaces: ["Support"] }), "Support’s own bot");
    assert.equal(botDetail({ shared: false, handle: "@acme_example_bot", spaces: ["Support", "Billing"] }), "Its own bot in Support and Billing");
});
