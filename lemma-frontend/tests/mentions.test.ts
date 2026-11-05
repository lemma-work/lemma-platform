import test from "node:test";
import assert from "node:assert/strict";
import { channelReaders, insertMention, matchMentionable, mentionAt, mentionables, mentionsIn, type Mentionable } from "../src/channels/mentions.ts";

const lem: Mentionable = { kind: "AGENT", id: "agent-lem", name: "Lem" };
const priya: Mentionable = { kind: "MEMBER", id: "user-priya", name: "Priya Shah" };
const pat: Mentionable = { kind: "MEMBER", id: "user-pat", name: "Pat" };
const tomas: Mentionable = { kind: "CONTACT", id: "contact-tomas", name: "Tomás Rivera" };

test("an @ opens the menu at the start or after a space, never inside an address", () => {
    assert.deepEqual(mentionAt("@", 1), { start: 0, query: "" });
    assert.deepEqual(mentionAt("ask @Le", 7), { start: 4, query: "Le" });
    assert.deepEqual(mentionAt("ask @Le and", 7), { start: 4, query: "Le" });
    assert.equal(mentionAt("mail dana@example.com", 21), null);
    assert.equal(mentionAt("ask @Lem now", 12), null, "a space after the name closes it");
    assert.equal(mentionAt("plain words", 5), null);
});

test("offers anyone a word of whose name starts with what was typed, in the order given", () => {
    const people = [lem, priya, pat, tomas];
    assert.deepEqual(matchMentionable(people, "p").map((who) => who.id), ["user-priya", "user-pat"]);
    assert.deepEqual(matchMentionable(people, "riv").map((who) => who.id), ["contact-tomas"]);
    assert.deepEqual(matchMentionable(people, "").map((who) => who.id), ["agent-lem", "user-priya", "user-pat", "contact-tomas"]);
    assert.deepEqual(matchMentionable(people, "", 2).map((who) => who.id), ["agent-lem", "user-priya"]);
    assert.deepEqual(matchMentionable(people, "zz"), []);
});

test("picking puts the name in the text and the caret after it", () => {
    const text = "hey @pr can you";
    const at = mentionAt(text, 7)!;
    assert.deepEqual(insertMention(text, at, 7, priya), { text: "hey @Priya Shah can you", caret: 16 });
    assert.deepEqual(insertMention("@", { start: 0 }, 1, lem), { text: "@Lem ", caret: 5 });
});

test("a post names each person picked whose name is still in the text, once", () => {
    assert.deepEqual(mentionsIn("@Lem and @Priya Shah, then @Lem again", [lem, priya, lem]), [
        { kind: "AGENT", id: "agent-lem" },
        { kind: "MEMBER", id: "user-priya" },
    ]);
    // Deleting the words un-names them; a longer word is not the name.
    assert.deepEqual(mentionsIn("never mind", [lem]), []);
    assert.deepEqual(mentionsIn("@Lemon tart", [lem]), []);
    // A name typed by hand, never picked, names nobody.
    assert.deepEqual(mentionsIn("@Pat look", [lem]), []);
    assert.deepEqual(mentionsIn("@Tomás Rivera, see", [tomas]), [{ kind: "CONTACT", id: "contact-tomas" }]);
});

test("everyone who can be named: the agents, then the people in it other than the reader", () => {
    const people = mentionables(
        [{ id: "agent-lem", label: "Lem" }, { id: "", label: "No id" }],
        [
            { kind: "MEMBER", id: "user-me", name: "Me" },
            { kind: "MEMBER", id: "user-priya", name: "Priya Shah" },
            { kind: "AGENT", id: "agent-lem", name: "Lem" },
            { kind: "CONTACT", id: "contact-tomas", name: "Tomás Rivera" },
            { kind: "MEMBER", id: null, name: "Removed" },
            { kind: "MEMBER", id: "user-x", name: null },
        ],
        "user-me",
    );
    assert.deepEqual(people.map((who) => who.kind + ":" + who.id), ["AGENT:agent-lem", "MEMBER:user-priya", "CONTACT:contact-tomas"]);
});

test("after the channel's own people, the pod's other members who can read it: everyone, in a public group only", () => {
    const pod = [
        { userId: "user-me", name: "Me", kind: "person" },
        { userId: "user-priya", name: "Priya Shah", kind: "person" },
        { userId: "user-sam", name: "Sam Lee", kind: "person" },
        { userId: "user-lem", name: "Lem", kind: "teammate" },
        { name: "Invited, no account", kind: "person" },
    ];
    const readers = channelReaders({ kind: "GROUP", visibility: "PUBLIC" }, pod);
    assert.deepEqual(readers.map((one) => one.userId), ["user-me", "user-priya", "user-sam"]);
    assert.deepEqual(channelReaders({ kind: "GROUP", visibility: "PRIVATE" }, pod), [], "a private group's lines are its members' alone");
    assert.deepEqual(channelReaders({ kind: "CONTACT", visibility: "PUBLIC" }, pod), []);
    const people = mentionables(
        [{ id: "agent-lem", label: "Lem" }],
        [{ kind: "MEMBER", id: "user-priya", name: "Priya Shah" }],
        "user-me",
        readers,
    );
    assert.deepEqual(people.map((who) => who.kind + ":" + who.id + ":" + who.inChannel), [
        "MEMBER:user-priya:true", "MEMBER:user-sam:false", "AGENT:agent-lem:false",
    ], "in the channel first, then who can read it, then agents who answer once; never the reader");
});
