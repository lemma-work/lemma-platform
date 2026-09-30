import test from "node:test";
import assert from "node:assert/strict";
import { anchorOf, commentRow, findAnchor, mentionAt, mentionsIn, readComment, threadsOf } from "../src/docpages/comments/model.ts";

const row = (over: Record<string, unknown>) => readComment({ id: "c1", file_path: "/pages/a.md", body: "x", created_at: "2026-09-30T10:00:00Z", ...over })!;

test("an anchor finds its words again after the page around them changed", () => {
    const before = "Intro.\nShip the launch post on Monday.\nOutro.";
    const at = before.indexOf("launch post");
    const anchor = anchorOf(before, at, at + "launch post".length);
    const after = "A new first line.\nIntro.\nShip the launch post on Monday.\nOutro.";
    const found = findAnchor(after, anchor)!;
    assert.equal(after.slice(found.from, found.to), "launch post");
    assert.equal(findAnchor("everything rewritten", anchor), null);
});

test("the same words twice land on the occurrence whose surroundings match", () => {
    const whole = "Price: 10. Later we said Price: 10. again";
    const second = whole.lastIndexOf("Price: 10");
    const anchor = anchorOf(whole, second, second + 9);
    assert.equal(findAnchor(whole, anchor)!.from, second);
});

test("mentions match the longest known name, at a word boundary, once each", () => {
    const known = [
        { kind: "agent" as const, label: "Deal", key: "deal" },
        { kind: "agent" as const, label: "Deal desk", key: "deal-desk" },
        { kind: "person" as const, label: "Priya", key: "u-priya" },
    ];
    assert.deepEqual(mentionsIn("@Deal desk please, and @priya — @Deal desk again", known).map((one) => one.key), ["deal-desk", "u-priya"]);
    assert.deepEqual(mentionsIn("email@priya.com", known), []);
    assert.deepEqual(mentionAt("hey @Pri", 8), { start: 4, query: "Pri" });
    assert.equal(mentionAt("hey Pri", 7), null);
});

test("the row carries the first bot named, for the schedule to key on", () => {
    const body = commentRow({
        filePath: "/pages/a.md", body: " fix this ", parentId: null,
        anchor: { quote: "q", quotePrefix: "p", quoteSuffix: "s" },
        mentions: [{ kind: "person", label: "Priya", key: "u" }, { kind: "agent", label: "Butler", key: "butler" }],
    });
    assert.equal(body.mentioned_agent, "butler");
    assert.equal(body.body, "fix this");
    assert.equal(commentRow({ filePath: "/a.md", body: "hi", mentions: [] }).mentioned_agent, null);
});

test("threads group replies under their root, and an orphaned reply still shows", () => {
    const rows = [
        row({ id: "r2", parent_id: "root", created_at: "2026-09-30T10:02:00Z" }),
        row({ id: "root" }),
        row({ id: "r1", parent_id: "root", created_at: "2026-09-30T10:01:00Z" }),
        row({ id: "lost", parent_id: "gone", created_at: "2026-09-30T10:03:00Z" }),
    ];
    const threads = threadsOf(rows);
    assert.deepEqual(threads.map((one) => [one.root.id, one.replies.map((r) => r.id)]), [["root", ["r1", "r2"]], ["lost", []]]);
    assert.equal(readComment({ id: "x" }), null);
});

import { pendingAsk } from "../src/docpages/comments/model.ts";

test("a thread waits on a bot until a bot replies after the ask", () => {
    const ask = row({ id: "a", mentioned_agent: "butler" });
    const human = row({ id: "h", parent_id: "a", created_at: "2026-09-30T10:01:00Z" });
    const bot = row({ id: "b", parent_id: "a", author_agent: "Butler", created_at: "2026-09-30T10:02:00Z" });
    assert.equal(pendingAsk({ root: ask, replies: [human] })?.id, "a");
    assert.equal(pendingAsk({ root: ask, replies: [human, bot] }), null);
    const again = row({ id: "c", parent_id: "a", mentioned_agent: "butler", created_at: "2026-09-30T10:03:00Z" });
    assert.equal(pendingAsk({ root: ask, replies: [bot, again] })?.id, "c");
    assert.equal(pendingAsk({ root: row({ id: "z" }), replies: [] }), null);
});
