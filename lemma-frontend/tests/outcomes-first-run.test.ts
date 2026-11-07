import test from "node:test";
import assert from "node:assert/strict";
import {
    broughtSaid,
    emptyPlan,
    ownName,
    queueFromNeeds,
    sentenceFor,
    sharedLinkPath,
    shelfOf,
    targetText,
    viewOfItem,
    withOwned,
    type NeedsItem,
    type Owned,
    type ShelfCard,
} from "../src/outcomes/model";

const card = (over: Partial<ShelfCard>): ShelfCard => ({
    id: "support",
    name: "Support",
    sentence: "Every customer hears back within a day.",
    kind: "week",
    work: "support",
    face: "bloom",
    place: "Intercom",
    brings: [],
    never: [],
    ...over,
});

test("the shelf groups templates by the work they do, where the server put them", () => {
    const works = shelfOf([
        card({ id: "support" }),
        card({ id: "follow-ups", name: "Follow-ups", work: "sales", place: "HubSpot" }),
        card({ id: "reviews", name: "Reviews", work: "engineering", place: "GitHub" }),
        card({ id: "partners", name: "Partners", work: "sales", place: "HubSpot", kind: "date" }),
        card({ id: "odd", name: "Odd", work: null, place: null }),
    ]);
    assert.deepEqual(works.map((work) => work.id), ["support", "sales", "engineering", "more"]);
    const sales = works[1];
    assert.equal(sales.label, "Sales");
    assert.deepEqual(sales.cards.map((one) => one.id), ["follow-ups", "partners"]);
    assert.deepEqual(sales.places, ["HubSpot"], "a place is said once");
    assert.equal(works[3].label, "More", "a template with no kind of work is not dropped");
});

test("a finish line says its date once one is picked", () => {
    const launch = card({ sentence: "The launch ships on {due}.", kind: "date" });
    assert.equal(sentenceFor(launch, null), "The launch ships on a date you pick.");
    assert.equal(sentenceFor(launch, "2026-11-18"), "The launch ships on November 18.");
    assert.equal(sentenceFor(card({}), "2026-11-18"), "Every customer hears back within a day.");
});

test("a Lemma from its owner's own words is named for what it is about", () => {
    assert.equal(ownName("Every invoice is paid within 30 days"), "Invoice");
    assert.equal(ownName("ship it"), "Ship");
    assert.equal(ownName("  "), "Lemma");
});

const owned: Owned = { sentence: "Every customer hears back within a day.", kind: "week", target: 90, unit: "%", due: null, measure: "SELECT 1", value: 74, problem: null };

test("what the Lemma keeps it owns is shown over its plan file, and never written into it", () => {
    const file = { ...emptyPlan("An old sentence"), target: "50%", sections: { must: [{ text: "x" }], how: [], who: [], never: [] } };
    const shown = withOwned(file, owned);
    assert.ok(shown);
    assert.equal(shown.outcome, owned.sentence);
    assert.equal(shown.target, "90%");
    assert.equal(shown.owned?.value, 74);
    assert.deepEqual(shown.sections.must, [{ text: "x" }], "the file's lines stay");
    assert.equal(file.outcome, "An old sentence", "the file itself is untouched");
    assert.equal(withOwned(file, null), file, "a Lemma that keeps nothing shows its file");
    assert.equal(withOwned(undefined, owned)?.outcome, owned.sentence, "and one with no plan file still shows what it owns");
});

test("a target reads as a person writes it, exactly as kept", () => {
    assert.equal(targetText(99.95, "%"), "99.95%", "never rounded: readings are compared against it");
    assert.equal(targetText(90, "%"), "90%");
    assert.equal(targetText(2, "posts"), "2 posts");
    assert.equal(targetText(20, null), "20");
    assert.equal(targetText(null, "%"), null);
});

const need = (over: Partial<NeedsItem>): NeedsItem => ({
    key: "k",
    kind: "QUESTION",
    podId: "p1",
    podName: "Support",
    title: "Which region?",
    detail: "Quarterly report",
    since: "2026-11-06T09:00:00Z",
    conversationId: "c1",
    workflowRunId: null,
    channelId: null,
    messageId: null,
    choices: [],
    ...over,
});

test("the organization's list becomes the queue, each answered where it can be", () => {
    const queue = queueFromNeeds([
        need({ key: "conversation:c1", since: "2026-11-06T09:00:00Z" }),
        need({ key: "conversation:c2", kind: "APPROVAL", conversationId: "c2", since: "2026-11-06T08:00:00Z" }),
        need({ key: "message:m1", kind: "MESSAGE", conversationId: null, channelId: "ch1", messageId: "m1", choices: ["Send it", "Not mine"], title: "Jonas has waited 21 hours." }),
        need({ key: "contact:ch2", kind: "CONTACT", conversationId: null, channelId: "ch2", title: "Dana", detail: null }),
        need({ key: "wait:w9", kind: "DECISION", conversationId: null, workflowRunId: "r9" }),
    ], []);
    assert.deepEqual(queue.map((item) => item.key), ["ask:c2", "ask:c1", "message:m1", "contact:ch2", "wait:w9"], "oldest first");
    const [approval, question, line, contact, decision] = queue;
    assert.equal(approval.kind, "decide");
    assert.equal(question.kind, "answer");
    assert.deepEqual(line.line, { channelId: "ch1", messageId: "m1", choices: ["Send it", "Not mine"] });
    assert.deepEqual(viewOfItem(line), { name: "chats", open: { kind: "shared", podId: "p1", id: "ch1" } }, "a line question opens the chat it was asked in");
    assert.equal(contact.title, "Reply to Dana");
    assert.equal(contact.contactId, "ch2");
    assert.equal(decision.runId, "r9");
});

test("a form is matched to its wait, which carries the form to fill in here", () => {
    const wait = { id: "w1", type: "HUMAN", question: "Approve the refund", externalRef: null, createdAt: "2026-11-06T07:00:00Z" };
    const rows = [{ podId: "p1", podName: "Support", workflowName: "refund-review", run: { id: "r1" }, wait }] as unknown as Parameters<typeof queueFromNeeds>[1];
    const [form] = queueFromNeeds([need({ key: "wait:w1", kind: "FORM", conversationId: null, workflowRunId: "r1" })], rows);
    assert.equal(form.key, "wait:w1");
    assert.equal(form.wait?.id, "w1");
    const [unread] = queueFromNeeds([need({ key: "wait:w2", kind: "FORM", conversationId: null, workflowRunId: "r2", title: "Fill in" })], rows);
    assert.equal(unread.wait, null, "a form whose wait is not read yet opens its run");
    assert.equal(unread.runId, "r2");
});

test("a pasted link opens where this app installs it, and only such links", () => {
    const own = ["https://lemma.work"];
    assert.equal(sharedLinkPath("https://github.com/acme/support-lemma", own), "/import/github/acme/support-lemma");
    assert.equal(sharedLinkPath("github.com/acme/support-lemma.git", own), "/import/github/acme/support-lemma");
    assert.equal(sharedLinkPath("https://lemma.work/s/app/abc", own), "/s/app/abc");
    assert.equal(sharedLinkPath("https://lemma.work/remix?source=x", own), "/remix?source=x");
    assert.equal(sharedLinkPath("https://example.com/s/app/abc", own), null, "another site's path that looks like a share opens nothing");
    assert.equal(sharedLinkPath("https://example.com/whatever", own), null);
    assert.equal(sharedLinkPath("github.com/acme", own), null);
    assert.equal(sharedLinkPath("not a link at all", own), null);
    assert.equal(sharedLinkPath("https://lemma.work/s/a%20b/<x>?q=<y>", own), "/s/a%20b/%3Cx%3E?q=%3Cy%3E", "every part is encoded again");
    assert.equal(sharedLinkPath("https://lemma.work/s/%E0%A4%A", own), null, "broken percent-encoding opens nothing");
    assert.equal(sharedLinkPath("javascript:alert(1)", own), null);
});

test("what a template brings is counted as a person would count it", () => {
    const steps = [
        { kind: "TABLE", name: "conversations" },
        { kind: "SCHEDULE", name: "friday_number" },
        { kind: "SCHEDULE", name: "morning_drafts" },
        { kind: "FILE", name: "skills" },
        { kind: "FILE", name: "skills/set-up" },
        { kind: "FILE", name: "skills/set-up/SKILL.md" },
        { kind: "FILE", name: "skills/keep-conversations/SKILL.md" },
        { kind: "FILE", name: "notes/readme.md" },
        { kind: "POD", name: "meta" },
    ];
    assert.equal(broughtSaid(steps), "1 table, 2 automations, 2 skills and 1 file", "folders made on the way are nothing");
    assert.equal(broughtSaid([{ kind: "SCHEDULE", name: "x" }]), "1 automation");
    assert.equal(broughtSaid([]), "");
});
