import test from "node:test";
import assert from "node:assert/strict";
import {
    answering,
    arrivedSince,
    groupStarters,
    groupsSupported,
    howToRemove,
    inviteMailto,
    isGroupPlatform,
    linkShown,
    noGroupsYet,
    offersTakeOn,
    onlyWho,
    platformName,
    readSourceSettings,
    refusedChange,
    sayAnswering,
    sayAnsweringFully,
    slackInvite,
    takeOn,
    toldWhenChanged,
    whatsappShareUrl,
    whomItAnswers,
    type SourceSettings,
} from "../src/channels/sources.ts";

const ME = "user-dana";
const wire = {
    id: "src-1", channel_id: "c-1", platform: "telegram", title: "Launch crew", surface_name: "telegram",
    answers_outsiders: true, welcomes_outsiders: true, can_manage: true,
    owner: { kind: "MEMBER", id: "user-priya", name: "Priya" },
    invite_link: "https://t.me/+abc", pending: false,
    public: { files: [{ name: "pricing.md", path: "/pricing.md" }, { path: "/no-name.md" }, { name: "theirs.md", path: null }], tables: ["price_list", ""], more: true },
};
const source = readSourceSettings(wire)!;
const as = (change: Partial<SourceSettings>): SourceSettings => ({ ...source, ...change });

test("reads a group source's settings the way the API defaults them", () => {
    assert.equal(readSourceSettings({ platform: "TELEGRAM" }), null, "nothing to key it by");
    assert.equal(source.platform, "TELEGRAM");
    assert.equal(source.botAnswersOutsiders, true, "the bot's switch is on unless it says off");
    assert.equal(readSourceSettings({ id: "x" })!.canManage, false, "nobody may change it unless told they may");
    assert.deepEqual(source.owner, { kind: "MEMBER", id: "user-priya", name: "Priya" });
    assert.deepEqual(source.publicReach, {
        files: [{ name: "pricing.md", path: "/pricing.md" }, { name: "theirs.md", path: null }],
        tables: ["price_list"],
        more: true,
    });
    // A link on a group still being made would be a link to nothing.
    assert.equal(readSourceSettings({ ...wire, pending: true })!.inviteLink, null);
});

test("who answers people outside: the first reason it fails, or who does", () => {
    assert.deepEqual(answering(as({ pending: true }), ME), { kind: "pending" });
    assert.deepEqual(answering(as({ platform: "SLACK", sharedExternally: false }), ME), { kind: "invited" });
    assert.deepEqual(answering(as({ botAnswersOutsiders: false }), ME), { kind: "bot-off", platform: "TELEGRAM" });
    assert.deepEqual(answering(as({ owner: null }), ME), { kind: "nobody" });
    assert.deepEqual(answering(as({ answersOutsiders: false }), ME), { kind: "off", owner: { you: false, name: "Priya" } });
    assert.deepEqual(answering(source, ME), { kind: "someone", name: "Priya" });
    assert.deepEqual(answering(as({ owner: { kind: "MEMBER", id: ME, name: "Dana" } }), ME), { kind: "you" });
    assert.deepEqual(answering(as({ platform: "SLACK", sharedExternally: true }), ME), { kind: "someone", name: "Priya" });
});

test("says it under a heading, and on its own", () => {
    assert.equal(sayAnswering({ kind: "someone", name: null }, "Marketing"), "Someone in Marketing answers for them");
    assert.equal(sayAnswering({ kind: "you" }, "Marketing"), "You answer for them");
    assert.equal(sayAnsweringFully({ kind: "bot-off", platform: "WHATSAPP" }, "Marketing"), "Answering people outside Marketing is off for this WhatsApp bot");
    assert.equal(sayAnsweringFully({ kind: "nobody" }, "Marketing"), "Nobody answers people outside Marketing");
});

test("offers Take it on only to whoever may, where nobody or somebody else answers", () => {
    assert.equal(offersTakeOn({ kind: "nobody" }, true), true);
    assert.equal(offersTakeOn({ kind: "someone", name: "Priya" }, true), true);
    assert.equal(offersTakeOn({ kind: "someone", name: "Priya" }, false), false);
    assert.equal(offersTakeOn({ kind: "bot-off", platform: "TELEGRAM" }, true), false);
    assert.deepEqual(takeOn(source), { owner: "me" });
    assert.deepEqual(takeOn(as({ answersOutsiders: false })), { owner: "me", answers_outsiders: true });
});

test("tells anyone who may not change it who can, and an admin who is told", () => {
    assert.equal(onlyWho({ kind: "someone", name: "Priya" }, "Marketing", "Lem"), "Only Priya or an admin of Marketing can change it.");
    assert.equal(onlyWho({ kind: "nobody" }, "Marketing", "Lem"), "Only someone who can change Lem can take it on.");
    assert.equal(onlyWho({ kind: "invited" }, "Marketing", "Lem"), null);
    assert.equal(toldWhenChanged({ kind: "someone", name: "Priya" }), "Priya is told when you change it.");
    assert.equal(toldWhenChanged({ kind: "off", owner: { you: false, name: null } }), "They are told when you change it.");
    assert.equal(toldWhenChanged({ kind: "you" }), null);
    assert.match(refusedChange("Marketing"), /admin of Marketing/);
});

test("says how the bot is asked, whom it answers, and how it is taken out", () => {
    assert.match(whomItAnswers(as({ platform: "SLACK", sharedExternally: false }), "M"), /private note inviting them in/);
    assert.match(whomItAnswers(source, "M"), /Everyone else, from what is Public/);
    assert.match(whomItAnswers(as({ welcomesOutsiders: false }), "M"), /Nobody else is answered here/);
    assert.equal(howToRemove("SLACK", "@lem"), "To take @lem out, send /remove @lem in the channel.");
    assert.equal(howToRemove("WHATSAPP", "Lem"), null);
});

test("names platforms, and which of them a bot can be in a group on", () => {
    assert.equal(platformName("whatsapp"), "WhatsApp");
    assert.equal(platformName("RESEND"), "Email");
    assert.equal(platformName("WEB"), "Web chat");
    assert.equal(platformName("MATRIX"), "MATRIX");
    assert.equal(isGroupPlatform("slack"), true);
    assert.equal(isGroupPlatform("RESEND"), false);
    assert.equal(groupsSupported("TELEGRAM"), true);
    assert.equal(groupsSupported("SLACK"), false, "Slack's channels are in its own settings");
    assert.match(noGroupsYet("WHATSAPP", "Lem"), /Lem’s number/);
    assert.equal(noGroupsYet("SLACK", "Lem"), "No groups yet.");
});

test("offers each platform as its own bot stands: ready, unwell, or to connect", () => {
    const surface = (platform: string, mine: boolean, active = true) => ({ id: platform, platform, name: platform.toLowerCase(), mine, agentName: "Lem", handle: "", active });
    const starters = groupStarters([surface("TELEGRAM", true), surface("WHATSAPP", true, false), surface("SLACK", false)]);
    assert.deepEqual(starters.map((one) => [one.platform, one.state]), [["WHATSAPP", "unwell"], ["TELEGRAM", "ready"], ["SLACK", "missing"]]);
});

test("a group arrives as a new group channel fed from the platform asked about", () => {
    const channel = (id: string, kind: string, platform: string) => ({ id, kind, sources: [{ platform }] });
    const before = new Set(["old"]);
    const now = [channel("old", "GROUP", "TELEGRAM"), channel("dm", "DIRECT", "TELEGRAM"), channel("slack", "GROUP", "SLACK"), channel("new", "GROUP", "TELEGRAM")];
    assert.equal(arrivedSince(before, now, "telegram")?.id, "new");
    assert.equal(arrivedSince(before, now, "WHATSAPP"), null);
});

test("an invite travels with its words, and reads short in a row", () => {
    assert.equal(slackInvite("@lem", "Lem"), "/invite @lem");
    assert.equal(slackInvite("", "Lem"), "/invite @Lem");
    assert.match(whatsappShareUrl("Acme", "https://chat.whatsapp.com/x"), /^https:\/\/wa\.me\/\?text=Join%20%E2%80%9CAcme/);
    assert.match(inviteMailto("Acme", "https://chat.whatsapp.com/x"), /^mailto:\?subject=Join%20Acme%20on%20WhatsApp&body=/);
    assert.equal(linkShown("https://chat.whatsapp.com/x"), "chat.whatsapp.com/x");
});
