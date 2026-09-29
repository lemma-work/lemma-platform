import test from "node:test";
import assert from "node:assert/strict";
import {
    mintTelegramLink, readLinkOptions, telegramChatCard, telegramLinkOptions, whoAnswers,
    type TelegramLinkOptions,
} from "../src/desktop/telegram-link.ts";
import { sayHi } from "../src/shell/say-hi.ts";

const HOME = { id: "pod-home", name: "Home" };
const WORK = { id: "pod-work", name: "Work" };
const OPTIONS: TelegramLinkOptions = { botUsername: "lemma_home_bot", pods: [HOME, WORK], podId: WORK.id };
const LINK = "https://t.me/lemma_home_bot?start=link_AbC-123_xyz";

/* ── the Telegram card on Server setup ─────────────────────────────── */

test("the card stays out of the way until a token is saved and unchanged", () => {
    assert.deepEqual(telegramChatCard({ saved: false, unsaved: false, options: OPTIONS, problem: null, podId: null }), { kind: "hidden" });
    assert.deepEqual(telegramChatCard({ saved: true, unsaved: true, options: OPTIONS, problem: null, podId: null }), { kind: "hidden" });
});

test("with the bot's name, the card says it is ready and who answers", () => {
    const card = telegramChatCard({ saved: true, unsaved: false, options: OPTIONS, problem: null, podId: null });
    assert.equal(card.kind, "ready");
    if (card.kind !== "ready") return;
    assert.equal(card.title, "@lemma_home_bot is ready");
    assert.equal(card.answers, "Your Work agent answers.");
    assert.deepEqual(card.pods, [HOME, WORK]);
});

test("picking a pod changes who answers", () => {
    assert.equal(whoAnswers(OPTIONS, HOME.id), "Your Home agent answers.");
    assert.equal(whoAnswers({ ...OPTIONS, pods: [], podId: null }, null), "Your agent answers from a new personal pod.");
});

test("the card waits for the bot's name, and says why when there is none", () => {
    assert.deepEqual(telegramChatCard({ saved: true, unsaved: false, options: undefined, problem: null, podId: null }), { kind: "loading" });
    const problem = new Error("Telegram did not answer for this Lemma's bot token.");
    assert.deepEqual(
        telegramChatCard({ saved: true, unsaved: false, options: undefined, problem, podId: null }),
        { kind: "problem", text: problem.message },
    );
});

test("options are read from the API's shape", async () => {
    const options = await telegramLinkOptions(async () => ({
        bot_username: "@lemma_home_bot",
        pods: [{ id: "pod-home", name: "Home" }, { name: "no id" }],
        pod_id: "pod-home",
    }));
    assert.deepEqual(options, { botUsername: "lemma_home_bot", pods: [HOME], podId: "pod-home" });
    assert.deepEqual(readLinkOptions(null), { botUsername: "", pods: [], podId: null });
});

/* ── minting ───────────────────────────────────────────────────────── */

test("a link is minted for the chosen pod, or the suggested one", async () => {
    const asked: unknown[] = [];
    const call = async (body: unknown) => { asked.push(body); return { url: LINK }; };
    assert.equal(await mintTelegramLink(HOME.id, call), LINK);
    assert.equal(await mintTelegramLink(null, call), LINK);
    assert.deepEqual(asked, [{ pod_id: HOME.id }, {}]);
});

test("anything but a Telegram start link is refused rather than opened", async () => {
    for (const url of ["https://evil.example/?start=link_x", "http://t.me/bot?start=link_x", "https://t.me/bot", ""]) {
        await assert.rejects(mintTelegramLink(null, async () => ({ url })), /Telegram link/, url);
    }
});

/* ── Reach: saying hi ──────────────────────────────────────────────── */

test("on the shared Telegram bot, saying hi mints a link for whoever is signed in", () => {
    assert.deepEqual(sayHi({ platform: "TELEGRAM", handle: "@lemma_home_bot", system: true }), { kind: "mint" });
    /* Even before the handle is known: the link names the bot itself. */
    assert.deepEqual(sayHi({ platform: "TELEGRAM", handle: "", system: true }), { kind: "mint" });
});

test("a pod's own bot and the other channels keep their plain links", () => {
    assert.deepEqual(sayHi({ platform: "TELEGRAM", handle: "@acme_bot", system: false }), { kind: "href", url: "https://t.me/acme_bot" });
    assert.deepEqual(sayHi({ platform: "WHATSAPP", handle: "+1 555 000 1111", system: true }), { kind: "href", url: "https://wa.me/15550001111" });
    assert.deepEqual(sayHi({ platform: "RESEND", handle: "ops@acme.example", system: true }), { kind: "href", url: "mailto:ops@acme.example" });
    assert.equal(sayHi({ platform: "SLACK", handle: "acme", system: false }), null);
});
