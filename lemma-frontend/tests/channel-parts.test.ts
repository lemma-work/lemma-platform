import test from "node:test";
import assert from "node:assert/strict";
import { readLine } from "../src/channels/model.ts";
import { mentionables, pickedIn, removeMention } from "../src/channels/mentions.ts";
import { extraLinks, isInlineImage, pieces, sizeShown } from "../src/channels/parts-model.ts";
import { hitText, readSearchPage, snippet } from "../src/channels/search-model.ts";
import { draftKey, keepDraft, parseDraft, readDraft } from "../src/channels/drafts.ts";
import { muteChoices, mutedSaid, publicWarning } from "../src/channels/settings-model.ts";
import { bellCount, readInbox } from "../src/channels/waiting.ts";
import { nextFocus, panelTabTarget } from "../src/ui/focus-trap.ts";

/** What a line carries beside its text, and the small rules around the
 *  composer, the bell and the panels. */

const lem = { type: "mention", kind: "AGENT", id: "agent-lem", name: "Lem" } as const;
const priya = { type: "mention", kind: "MEMBER", id: "user-priya", name: "Priya Shah" } as const;

test("reads a line's parts, keeping only those it can draw", () => {
    const line = readLine({
        id: "l1", seq: 1, sender: { kind: "MEMBER", id: "u" }, text: "hi", created_at: "",
        parts: [
            { type: "mention", kind: "AGENT", id: "agent-lem", name: "Lem" },
            { type: "file", path: "/channels/c1/plan.pdf", name: "plan.pdf", mime_type: "application/pdf", size: 2048, file_id: "f1" },
            { type: "link", url: "https://example.com/a", label: "The brief" },
            { type: "link", url: "javascript:alert(1)" },
            { type: "mention", kind: "ROBOT", id: "x" },
            { type: "poll" },
        ],
    })!;
    assert.deepEqual(line.parts.map((part) => part.type), ["mention", "file", "link"]);
    assert.deepEqual(line.parts[1], { type: "file", path: "/channels/c1/plan.pdf", name: "plan.pdf", mimeType: "application/pdf", size: 2048, fileId: "f1" });
    assert.deepEqual(readLine({ id: "l2", seq: 2, sender: {}, withheld: true, parts: [lem], created_at: "" })!.parts, [], "a withheld line shows nothing it carried");
});

test("each @Name a mention part names is a chip, the longest name first; addresses are links", () => {
    const said = pieces("@Lem ask @Priya Shah about https://example.com/a, and @Priya alone", [lem, priya, { type: "link", url: "https://example.com/a", label: "The brief" }]);
    assert.deepEqual(said.map((piece) => [piece.kind, piece.text]), [
        ["mention", "@Lem"], ["text", " ask "], ["mention", "@Priya Shah"], ["text", " about "], ["link", "The brief"], ["text", ", and @Priya alone"],
    ]);
    assert.deepEqual(pieces("mail me@x.com", [lem]).map((piece) => piece.kind), ["text"]);
    assert.deepEqual(extraLinks("see above", [{ type: "link", url: "https://b.example", label: null }]).map((link) => link.url), ["https://b.example"]);
    assert.deepEqual([sizeShown(800), sizeShown(4096), sizeShown(3_500_000), sizeShown(null)], ["800 B", "4 KB", "3.3 MB", null]);
    assert.deepEqual(["image/PNG", "image/webp", "image/svg+xml", "application/pdf", null].map(isInlineImage), [true, true, false, false, false], "only what the server serves inline is previewed");
});

test("the @ menu lists the channel's agents first, then its people, then the pod's other agents", () => {
    const people = mentionables(
        [{ id: "agent-pod", label: "Reviewer" }, { id: "agent-lem", label: "Lem" }],
        [{ kind: "MEMBER", id: "user-priya", name: "Priya" }, { kind: "AGENT", id: "agent-lem", name: "Lem" }, { kind: "MEMBER", id: "me", name: "Me" }],
        "me",
    );
    assert.deepEqual(people.map((who) => [who.id, who.inChannel]), [["agent-lem", true], ["user-priya", true], ["agent-pod", false]]);
});

test("a chip shows while its name is in the text, and removing it takes the name out", () => {
    const picked = [{ kind: "AGENT" as const, id: "agent-lem", name: "Lem" }, { kind: "MEMBER" as const, id: "user-priya", name: "Priya" }];
    assert.deepEqual(pickedIn("@Lem please", picked).map((who) => who.id), ["agent-lem"]);
    assert.equal(removeMention("ask @Lem now", { name: "Lem" }), "ask now");
    assert.equal(removeMention("@Lem now", { name: "Lem" }), "now");
    assert.equal(removeMention("@Lemon now", { name: "Lem" }), "@Lemon now");
});

test("a draft is kept per thread, and forgotten once empty", () => {
    const main = draftKey("pod", "c1");
    const thread = draftKey("pod", "c1", "root-1");
    keepDraft(main, { text: "for everyone", picked: [] });
    keepDraft(thread, { text: "for the thread", picked: [] });
    assert.equal(readDraft(main).text, "for everyone");
    assert.equal(readDraft(thread).text, "for the thread");
    assert.deepEqual(readDraft(draftKey("pod", "c1", "root-2")), { text: "", picked: [] });
    keepDraft(thread, { text: "", picked: [] });
    assert.equal(readDraft(thread).text, "");
});

test("a draft keeps who was picked from the menu, so its @names still name them", () => {
    const key = draftKey("pod", "c1", "root-9");
    const sam = { kind: "MEMBER" as const, id: "user-sam-2", name: "Sam" };
    keepDraft(key, { text: "@Sam can you look", picked: [sam] });
    assert.deepEqual(readDraft(key).picked, [sam]);
    /* What an earlier build stored -- the words alone -- still reads. */
    assert.deepEqual(parseDraft("half a line"), { text: "half a line", picked: [] });
    assert.deepEqual(parseDraft(JSON.stringify({ text: "@Sam hi", picked: [sam, { kind: "ROBOT", id: 1 }] })), { text: "@Sam hi", picked: [sam] });
    assert.deepEqual(parseDraft(null), { text: "", picked: [] });
});

test("a search result reads its line and channel, and its snippet marks the words searched for", () => {
    const found = readSearchPage({
        items: [{ channel: { id: "c1", kind: "GROUP", title: "launch" }, message: { id: "l4", seq: 4, sender: { kind: "MEMBER", name: "Priya" }, text: "Ship the beta Thursday", created_at: "" } }, { channel: {}, message: {} }],
        next_cursor: "n2",
    });
    assert.equal(found.items.length, 1);
    assert.equal(found.nextBefore, "n2");
    assert.equal(found.items[0].snippet, null, "no snippet: the line's own text is cut here");
    /* The server's snippet is plain text; the words are marked here. */
    const ranked = readSearchPage({ items: [{ channel: { id: "c1", kind: "GROUP" }, message: { id: "l5", seq: 5, text: "long text", created_at: "" }, rank: 0.4, snippet: "…the beta ships Thursday…" }] });
    assert.equal(ranked.items[0].snippet, "…the beta ships Thursday…");
    assert.equal(snippet(ranked.items[0].snippet!, "beta").find((piece) => piece.match)?.text, "beta");
    assert.deepEqual(snippet("Ship the beta Thursday", "beta thursday"), [
        { text: "Ship the ", match: false }, { text: "beta", match: true }, { text: " ", match: false }, { text: "Thursday", match: true },
    ]);
    const long = snippet("x ".repeat(200) + "needle at the end", "needle", 40);
    assert.equal(long[0].text.startsWith("…"), true);
    assert.equal(long.some((piece) => piece.match && piece.text === "needle"), true);
});

test("a result shows plain words, whole, with the match marked -- the line's own where the reader has it", () => {
    /* What QA saw: raw markdown, a dropped leading "[", cuts mid-word. */
    const answer = "[Plan] **Constraints**: the _beta_ ships after `deploy` -- see [the doc](https://x.test/doc).\n\n## Next\n- tell support";
    const pieces = snippet(answer, "beta");
    const said = pieces.map((piece) => piece.text).join("");
    assert.equal(said, "[Plan] Constraints: the beta ships after deploy -- see the doc. Next tell support");
    assert.deepEqual(pieces.filter((piece) => piece.match).map((piece) => piece.text), ["beta"]);
    const long = "alpha ".repeat(60) + "the needle is here " + "omega ".repeat(60);
    const cut = snippet(long, "needle", 60).map((piece) => piece.text).join("");
    assert.match(cut, /^…\w/, "starts on a whole word");
    assert.match(cut, /\w…$/, "ends on a whole word");
    for (const word of cut.replace(/…/g, "").split(" ")) assert.ok(["alpha", "omega", "the", "needle", "is", "here"].includes(word), "no word cut: " + word);
    const hit = readSearchPage({ items: [{ channel: { id: "c1" }, message: { id: "l1", seq: 1, text: "[Plan] whole", created_at: "" }, snippet: "Plan] who" }] }).items[0];
    assert.equal(hitText(hit), "[Plan] whole");
    const withheld = readSearchPage({ items: [{ channel: { id: "c1" }, message: { id: "l2", seq: 2, text: null, created_at: "" }, snippet: "server cut" }] }).items[0];
    assert.equal(hitText(withheld), "server cut");
});

test("a mute is said until it runs out", () => {
    const now = new Date("2026-10-19T10:00:00Z");
    assert.equal(mutedSaid(null, now), null);
    assert.equal(mutedSaid("2026-10-19T09:00:00Z", now), null);
    assert.match(mutedSaid("2026-10-19T11:00:00Z", now) ?? "", /^Muted until /);
    assert.deepEqual(muteChoices(now).map((one) => one.until), ["2026-10-19T11:00:00.000Z", "2026-10-19T18:00:00.000Z", "2026-10-20T10:00:00.000Z", "2026-10-26T10:00:00.000Z"]);
});

test("the bell counts what the inbox lists: questions, mentions and your own channel's new lines", () => {
    const inbox = readInbox({
        totals: { channels: 3, mentions: 2, waiting: 1 },
        mentions: [{ channel: { id: "c2", kind: "GROUP", title: "launch" }, message: { id: "m1", seq: 9, sender: { kind: "MEMBER", name: "Priya" }, text: "@Dana look", created_at: "x" } }],
    });
    assert.deepEqual(inbox.mentioned.map((item) => [item.channelId, item.from, item.text]), [["c2", "Priya", "@Dana look"]]);
    assert.deepEqual(bellCount(inbox, 4), { badge: "7", said: "1 question waiting on you, 2 mentions of you, 4 new messages for you" });
    assert.deepEqual(bellCount(null, 0), { badge: null, said: "Nothing new for you" });
});

test("the bell opens a reply in its thread: a mention or a question asked in a thread carries its first line", () => {
    const inbox = readInbox({
        totals: { channels: 1, mentions: 1, waiting: 1 },
        mentions: [{ channel: { id: "c2", kind: "GROUP" }, message: { id: "m2", seq: 12, thread_root_id: "root-1", sender: { name: "Priya" }, text: "@Dana here", created_at: "x" } }],
        waiting: [{ channel: { id: "c2", kind: "GROUP" }, message: { id: "q2", thread_root_id: "root-1", sender: { kind: "AGENT", name: "Lem" }, text: "Which?", created_at: "x", question: { kind: "ASK", question: {} } } },
            { channel: { id: "c2", kind: "GROUP" }, message: { id: "q3", sender: { kind: "AGENT", name: "Lem" }, text: "Top?", created_at: "x", question: { kind: "ASK", question: {} } } }],
    });
    assert.equal(inbox.mentioned[0].threadRootId, "root-1");
    assert.deepEqual(inbox.items.map((item) => item.threadRootId), ["root-1", null]);
});

test("a side panel beside the timeline does not hold Tab; a popover over the page does", () => {
    const items = ["close", "name", "save"];
    assert.equal(panelTabTarget(items, "save", false, false), null, "past the last, on out of the panel");
    assert.equal(panelTabTarget(items, "close", true, false), null);
    assert.equal(panelTabTarget(items, "save", false, true), "close", "a trapping panel goes round");
});

test("Tab goes round inside a panel: past the last to the first, and back", () => {
    const items = ["close", "name", "save"];
    assert.equal(nextFocus(items, "save", false), "close");
    assert.equal(nextFocus(items, "close", true), "save");
    assert.equal(nextFocus(items, "name", false), null, "inside, the browser moves it");
    assert.equal(nextFocus(items, "outside", false), "close");
    assert.equal(nextFocus([], null, false), null);
});

test("making a private group public is asked first, saying its history goes with it", () => {
    assert.match(publicWarning("launch", "Acme"), /^Everyone in Acme will be able to find launch and read everything said in it, including what was said before/);
    assert.match(publicWarning(null, "Acme"), /find this chat/);
});
