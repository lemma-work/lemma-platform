import test from "node:test";
import assert from "node:assert/strict";
import { groupByKind, highlight, rank, scoreOne, type Candidate } from "../src/search/matching.ts";

function candidate(title: string, over: Partial<Candidate> = {}): Candidate {
    return { kind: "doc", id: title, title, ...over };
}

function order(titles: string[], query: string): string[] {
    return rank(titles.map((title) => candidate(title)), query).map((hit) => hit.title);
}

test("a better kind of match always beats a worse one", () => {
    // This is the whole job. A box that finds the right thing and puts it
    // fourth is a box people stop using.
    assert.deepEqual(
        order(["Quarterly review deck", "review", "Design review", "Reviewing the vendors"], "review"),
        ["review", "Reviewing the vendors", "Design review", "Quarterly review deck"],
    );
});

test("an earlier word beats a later one", () => {
    assert.deepEqual(order(["Design review", "Review notes"], "review"), ["Review notes", "Design review"]);
});

test("initials find a multi-word name", () => {
    // Typing `wf` for "Weekly Flow" is the thing people actually do.
    const hit = scoreOne(candidate("Weekly Flow"), "wf");
    assert.ok(hit);
    assert.deepEqual(highlight("Weekly Flow", hit.ranges).filter((p) => p.hit).map((p) => p.text), ["W", "F"]);
});

test("camelCase is a word boundary", () => {
    assert.ok(scoreOne(candidate("podBrief"), "pb"));
    assert.ok(scoreOne(candidate("ConversationRouter"), "cr"));
});

test("a tighter fuzzy match beats a scattered one", () => {
    // `abc` sitting together should beat the same letters spread across a
    // sentence, or fuzzy matching turns into noise wearing the shape of a list.
    assert.deepEqual(order(["a big cat sat", "abcdef"], "abc"), ["abcdef", "a big cat sat"]);
});

test("a long query does not fuzzy-match everything", () => {
    // A short query as initials is exactly what fuzzy matching is for.
    assert.ok(scoreOne(candidate("Quarterly revenue reconciliation"), "qrr"));

    // A long one is not: every long string contains some long query as a
    // subsequence, and allowing it fills the list with things that share only
    // letters. "aeiou..." is present in almost any sentence, in order.
    assert.equal(scoreOne(candidate("A gentle reminder about our invoices"), "aeioubouoie"), null);
    assert.equal(scoreOne(candidate("Some completely unrelated title here"), "thisisaverylongquery"), null);

    // A single character is too little to mean anything as a subsequence, and
    // would otherwise match every title containing that letter anywhere.
    assert.equal(scoreOne(candidate("Quarterly report"), "z"), null);
});

test("a match only in the hidden text ranks below every visible one", () => {
    // A result whose visible text does not contain what you typed reads as a
    // mistake until you get to the second line.
    const hits = rank([
        candidate("Notes", { haystack: "/me/budget/notes.md" }),
        candidate("Budget"),
    ], "budget");

    assert.deepEqual(hits.map((h) => h.title), ["Budget", "Notes"]);
    assert.deepEqual(hits[1].ranges, [], "a hidden match should not underline the title");
});

test("a path can be found even when the name says nothing", () => {
    assert.ok(scoreOne(candidate("readme.md", { haystack: "/skills/onboarding/readme.md" }), "onboarding"));
});

test("kind breaks a tie in the order people are likely to want", () => {
    const hits = rank([
        candidate("Atlas", { kind: "schedule", id: "s" }),
        candidate("Atlas", { kind: "teammate", id: "t" }),
        candidate("Atlas", { kind: "doc", id: "d" }),
    ], "atlas");

    assert.deepEqual(hits.map((h) => h.kind), ["teammate", "doc", "schedule"]);
});

test("the same query twice gives the same list", () => {
    // A box whose results reshuffle between identical keystrokes is one nobody
    // can build a habit on.
    const pool = ["Atlas", "atlas", "Atlas notes", "The Atlas"].map((t, i) => candidate(t, { id: "id" + i }));

    assert.deepEqual(rank(pool, "atlas").map((h) => h.id), rank([...pool].reverse(), "atlas").map((h) => h.id));
});

test("an empty query matches nothing at all", () => {
    assert.equal(scoreOne(candidate("Anything"), ""), null);
    assert.equal(scoreOne(candidate("Anything"), "   "), null);
    assert.deepEqual(rank([candidate("Anything")], ""), []);
});

test("case and surrounding space do not matter", () => {
    assert.ok(scoreOne(candidate("Weekly Report"), "  WEEKLY  ".trim()));
    assert.ok(scoreOne(candidate("weekly report"), "Weekly"));
});

test("highlighting covers exactly what matched and nothing else", () => {
    const hit = scoreOne(candidate("Design review"), "review");
    assert.ok(hit);
    assert.deepEqual(highlight("Design review", hit.ranges), [
        { text: "Design ", hit: false },
        { text: "review", hit: true },
    ]);
    assert.deepEqual(
        highlight("Design review", hit.ranges).map((p) => p.text).join(""),
        "Design review",
        "the pieces must rebuild the original exactly",
    );
});

test("highlighting an unmatched title returns it whole", () => {
    assert.deepEqual(highlight("Untouched", []), [{ text: "Untouched", hit: false }]);
});

test("the list is capped", () => {
    const many = Array.from({ length: 120 }, (_, i) => candidate("Report " + i, { id: String(i) }));
    assert.equal(rank(many, "report").length, 40);
    assert.equal(rank(many, "report", 5).length, 5);
});

test("each kind gets one heading, not one per run", () => {
    // Ranking interleaves kinds by score, so grouping by "start a new group
    // when the kind changes" scatters the same heading down the list. This is
    // what the first live search actually did.
    const hits = rank([
        candidate("Atlas", { kind: "teammate", id: "t1" }),
        candidate("Atlas notes", { kind: "conversation", id: "c1" }),
        candidate("Atlas", { kind: "conversation", id: "c2" }),
        candidate("Atlas plan", { kind: "teammate", id: "t2" }),
        candidate("Atlas", { kind: "doc", id: "d1" }),
    ], "atlas");

    const groups = groupByKind(hits);
    const kinds = groups.map((g) => g.kind);

    assert.equal(new Set(kinds).size, kinds.length, "a kind must not appear twice");
    assert.deepEqual(kinds, ["teammate", "conversation", "doc"]);
});

test("groups are ordered by their best hit, so the top result stays on top", () => {
    // Grouping must not fight the ranking it is displaying.
    const hits = rank([
        candidate("Zebra crossing", { kind: "teammate", id: "t" }),
        candidate("Zebra", { kind: "doc", id: "d" }),
    ], "zebra");

    const groups = groupByKind(hits);
    assert.equal(groups[0].kind, "doc", "the exact match leads, whatever kind it is");
    assert.equal(groups[0].hits[0].title, hits[0].title);
});

test("rank order is kept inside a group", () => {
    const hits = rank([
        candidate("Review later", { kind: "doc", id: "a" }),
        candidate("review", { kind: "doc", id: "b" }),
        candidate("Design review", { kind: "doc", id: "c" }),
    ], "review");

    assert.deepEqual(groupByKind(hits)[0].hits.map((h) => h.id), hits.map((h) => h.id));
});

test("grouping nothing gives nothing", () => {
    assert.deepEqual(groupByKind([]), []);
});

test("records are grouped and headed by their table, not by the word 'record'", () => {
    // "RECORD" over five rows each captioned `connections` says the same word
    // twice and the useful word never.
    const hits = rank([
        candidate("Deepak Jha", { kind: "record", id: "c1", subtitle: "connections" }),
        candidate("Deepak Singh", { kind: "record", id: "c2", subtitle: "connections" }),
        candidate("Deepak's invoice", { kind: "record", id: "i1", subtitle: "invoices" }),
    ], "deepak");

    const groups = groupByKind(hits);
    assert.deepEqual(groups.map((g) => g.label), ["connections", "invoices"]);
    assert.deepEqual(groups.map((g) => g.hits.length), [2, 1]);
    assert.ok(groups.every((g) => g.kind === "record"));
});

test("everything that is not a record is still headed by its kind", () => {
    const hits = rank([
        candidate("Atlas", { kind: "teammate", id: "t" }),
        candidate("Atlas notes", { kind: "doc", id: "d", subtitle: "/me/atlas.md" }),
    ], "atlas");

    assert.deepEqual(groupByKind(hits).map((g) => g.label), ["Teammate", "Document"]);
});

test("a record with no table named falls back to its kind", () => {
    const hits = rank([candidate("Orphan", { kind: "record", id: "r" })], "orphan");

    assert.equal(groupByKind(hits)[0].label, "Record");
});
