import test from "node:test";
import assert from "node:assert/strict";
import { botFromTestDetail, telegramBotCard, telegramBotUrl } from "../src/desktop/telegram-bot.ts";

test("the bot's name comes from the Telegram test's own sentence", () => {
    assert.equal(botFromTestDetail("Connected as @lemma_home_bot."), "lemma_home_bot");
    // The probe's fallback when getMe named nobody: no name, not "your".
    assert.equal(botFromTestDetail("Connected as @your bot."), null);
    assert.equal(botFromTestDetail(undefined), null);
});

test("only a t.me link to a real bot name is ever built", () => {
    assert.equal(telegramBotUrl("lemma_home_bot"), "https://t.me/lemma_home_bot");
    assert.throws(() => telegramBotUrl("evil.example/x"));
});

test("nothing shows until the token is saved, and saved edits too", () => {
    assert.deepEqual(telegramBotCard({ saved: false, unsaved: false }), { kind: "hidden" });
    assert.deepEqual(telegramBotCard({ saved: true, unsaved: true }), { kind: "hidden" });
    assert.deepEqual(telegramBotCard({ saved: true, unsaved: false, pending: true }), { kind: "loading" });
});

test("a saved token names the bot and opens its chat", () => {
    const card = telegramBotCard({ saved: true, unsaved: false, detail: "Connected as @lemma_home_bot." });
    assert.deepEqual(card, {
        kind: "ready",
        bot: "lemma_home_bot",
        title: "@lemma_home_bot is ready",
        url: "https://t.me/lemma_home_bot",
    });
});

test("a test that failed or named no bot says so rather than open nothing", () => {
    assert.equal(telegramBotCard({ saved: true, unsaved: false, failed: true }).kind, "problem");
    assert.equal(telegramBotCard({ saved: true, unsaved: false, detail: "It worked." }).kind, "problem");
});
