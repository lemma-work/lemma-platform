import test from "node:test";
import assert from "node:assert/strict";
import { botChoices, readMine, spacesSaid, type TelegramBot } from "../src/session/telegram-bots.ts";

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
