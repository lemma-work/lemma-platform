import test from "node:test";
import assert from "node:assert/strict";
import { emptyFor, type Empty, type EmptyAction, type EmptyPlace } from "../src/space/empty-copy.ts";

const PLACES: EmptyPlace[] = [
    { place: "pages" },
    { place: "apps" },
    { place: "tables" },
    { place: "workflows" },
    { place: "rows" },
    { place: "all" },
    { place: "files", scope: "shared", folder: false },
    { place: "files", scope: "personal", folder: false },
    { place: "files", scope: "shared", folder: true },
    { place: "chats", filter: "all" },
    { place: "chats", filter: "chats" },
    { place: "chats", filter: "channels" },
    { place: "chats", filter: "automations" },
    { place: "chats", filter: "docs" },
];

const every = PLACES.map((where) => ({ where, empty: emptyFor(where, "Hazel") }));
const actions = (empty: Empty): EmptyAction[] => [empty.primary, ...empty.starters];
const words = (empty: Empty): string[] => [empty.title, empty.line, ...actions(empty).flatMap((action) => action.kind === "ask" ? [action.label, action.text] : [action.label])];

/** A button, or an ask that is a whole sentence and can go as it stands. */
const needsNoTyping = (action: EmptyAction) => action.kind !== "ask" || /[.?!]$/.test(action.text.trim());

test("every empty place offers one way in that needs no typing", () => {
    // A catalog beneath it (templates, app ideas) is a click per entry.
    for (const { where, empty } of every) {
        assert.ok(actions(empty).some(needsNoTyping) || empty.follow, JSON.stringify(where) + " has only unfinished asks");
    }
});

test("pages and apps are followed by their catalogs", () => {
    assert.equal(emptyFor({ place: "pages" }, "Hazel").follow, "templates");
    assert.equal(emptyFor({ place: "apps" }, "Hazel").follow, "ideas");
    assert.equal(emptyFor({ place: "tables" }, "Hazel").follow, undefined);
});

test("every empty place says what it holds in one sentence", () => {
    for (const { where, empty } of every) {
        assert.ok(empty.title.trim().length > 0, JSON.stringify(where));
        assert.match(empty.line, /\.$/, JSON.stringify(where) + " line should end as a sentence");
        assert.doesNotMatch(empty.line, /[.!?]\s+\S/, JSON.stringify(where) + " line is more than one sentence: " + empty.line);
    }
});

test("options are one short line each", () => {
    for (const { empty } of every) {
        for (const action of actions(empty)) assert.ok(action.label.length <= 32, "too long for one line: " + action.label);
    }
});

test("the teammate is called by its name, never by a category word", () => {
    // Inside the app the category word only names the category; the space's
    // own agent is the teammate, never "the bot" or "the assistant".
    for (const { where, empty } of every) {
        for (const text of words(empty)) {
            assert.doesNotMatch(text, /\b(teammates?|bots?|assistants?|agents?|pods?)\b/i, JSON.stringify(where) + ": " + text);
        }
    }
    const named = every.filter(({ empty }) => words(empty).some((text) => text.includes("Hazel")));
    assert.ok(named.length >= every.length / 2, "most places should name who fills them");
});

test("an ask is put in the box to be finished, so an unfinished one ends open", () => {
    // "Write a page about " is completed by the person; a trailing space is
    // what leaves the caret ready for the next word.
    for (const { empty } of every) {
        for (const action of actions(empty)) {
            if (action.kind !== "ask" || needsNoTyping(action)) continue;
            assert.match(action.text, /\s$/, "unfinished ask should end with a space: " + action.text);
        }
    }
});

test("each place draws its own picture", () => {
    assert.equal(emptyFor({ place: "workflows" }, "Hazel").art, "workflows");
    assert.equal(emptyFor({ place: "files", scope: "personal", folder: false }, "Hazel").art, "folder");
    assert.equal(emptyFor({ place: "chats", filter: "channels" }, "Hazel").primary.kind, "reach");
    assert.equal(emptyFor({ place: "rows" }, "Hazel").primary.kind, "row");
});
