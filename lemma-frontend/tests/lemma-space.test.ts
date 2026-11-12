import test from "node:test";
import assert from "node:assert/strict";
import { bringInRequest, canDo, holdsNothing, isNewcomer, lemmaSays, looksAfter, pageTitle, reachSaid, templateSetupRequest, viewOfAddress, welcomeSaid } from "../src/outcomes/model.ts";
import { ROW_TABS, outcomeView, partOfAbout, readView, writeView } from "../src/outcomes/address.ts";

test("bringing things in names the places the way a person lists them", () => {
    assert.equal(bringInRequest(["Notion"]), "Bring in Notion.");
    assert.equal(bringInRequest(["Notion", "Airtable"]), "Bring in Notion and Airtable.");
    assert.equal(bringInRequest(["Notion", "Google Drive", "Airtable"]), "Bring in Notion, Google Drive and Airtable.");
});

test("a template that landed is set up by the skill it brought", () => {
    assert.equal(templateSetupRequest("Support"), "Set up the Support template I just added. Follow its set-up skill.");
});

test("a Lemma's header says who can find it, and nothing before that is known", () => {
    assert.equal(reachSaid("org", "Fernhill"), "Everyone at Fernhill can find it");
    assert.equal(reachSaid("org", "  "), "Everyone at this organization can find it", "an organization with no name yet");
    assert.equal(reachSaid("invited", "Fernhill"), "Only the people in it can find it");
    assert.equal(reachSaid("anyone", "Fernhill"), "Anyone with a Lemma account can join");
    assert.equal(reachSaid(null, "Fernhill"), "");
});

test("a page is called what its file is called, without the extension", () => {
    assert.equal(pageTitle("Onboarding notes.md"), "Onboarding notes");
    assert.equal(pageTitle("README.MD"), "README");
    assert.equal(pageTitle("notes"), "notes", "a name with no extension is left alone");
});

test("what the system keeps at a Lemma's root does not make it hold something", () => {
    assert.equal(holdsNothing([]), true);
    assert.equal(holdsNothing(["plan.md", "skills", ".lemma", "system", "me"]), true, "a new Lemma comes with each person's own folder");
    assert.equal(holdsNothing(["plan.md", "pages"]), false, "a pages folder is the person's");
    assert.equal(holdsNothing(["Q3 report.pdf"]), false);
});

test("each of a Lemma's tabs has an address of its own, and reads back to the same tab", () => {
    for (const tab of ["chats", "apps", "tables", "pages", "files", "about", "automations", "people", "contacts", "plan"] as const) {
        const view = outcomeView("p1", tab);
        assert.deepEqual(readView(writeView(view)), view, tab);
    }
});

test("a Lemma's row is its home, what is in it, and About; the rest are parts of About", () => {
    assert.deepEqual(ROW_TABS, ["home", "chats", "apps", "tables", "pages", "files", "about"]);
    for (const tab of ["plan", "people", "contacts", "automations", "settings"] as const) assert.equal(partOfAbout(tab), true, tab);
    for (const tab of ROW_TABS) assert.equal(partOfAbout(tab), false, tab);
});

test("About alone is this app's page; an old link to a part of it is still read the old way", () => {
    assert.deepEqual(readView("/l/p1/about"), outcomeView("p1", "about"));
    assert.equal(readView("/l/p1/about/connections"), null);
    assert.deepEqual(viewOfAddress({ podId: "p1", tabId: "space:about", conversationId: null }), outcomeView("p1", "about"));
    assert.deepEqual(viewOfAddress({ podId: "p1", tabId: "space:about", conversationId: null, section: "people" }), outcomeView("p1", "settings", "people"));
});

test("a Lemma says how it is doing in its own voice, from what the frame can show", () => {
    assert.equal(lemmaSays({ empty: true, running: 0, needs: 0, standing: null }), "I’m new here. I don’t have anything to work with yet.");
    assert.equal(lemmaSays({ empty: false, running: 0, needs: 0, standing: null }), "All quiet. Nothing needs you.");
    assert.equal(lemmaSays({ empty: false, running: 1, needs: 1, standing: "behind" }), "I’m working on 1 thing right now. 1 thing needs you.");
    assert.equal(lemmaSays({ empty: false, running: 3, needs: 0, standing: "on" }), "I’m working on 3 things right now. Nothing needs you.");
    assert.equal(lemmaSays({ empty: false, running: 0, needs: 4, standing: "behind" }), "I’m behind on what I own. 4 things need you.");
    assert.equal(lemmaSays({ empty: false, running: 0, needs: 0, standing: "on" }), "I’m on track. Nothing needs you.");
});

test("what a Lemma can do so far is four things a person can check, each with the step that adds it", () => {
    const none = canDo({ stuff: false, chats: false, runs: false, owns: false });
    assert.deepEqual(none.map((step) => [step.label, step.done]), [["Knows your stuff", false], ["Answers questions", false], ["Works by itself", false], ["Owns a result", false]]);
    const some = canDo({ stuff: true, chats: true, runs: false, owns: false });
    assert.equal(some.find((step) => !step.done)?.key, "works", "the next step is the first thing not yet true");
    assert.equal(canDo({ stuff: true, chats: true, runs: true, owns: true }).every((step) => step.done), true);
});

test("a Lemma says hello only to someone new to it, who did not make it", () => {
    assert.equal(isNewcomer({ madeBy: "u1", me: "u2", chats: 0 }), true);
    assert.equal(isNewcomer({ madeBy: "u1", me: "u1", chats: 0 }), false, "its maker met it by making it");
    assert.equal(isNewcomer({ madeBy: "u1", me: "u2", chats: 3 }), false, "someone who has chatted with it has met it");
    assert.equal(isNewcomer({ madeBy: null, me: "u2", chats: 0 }), false, "with no maker known, it does not guess");
    assert.equal(isNewcomer({ madeBy: "u1", me: null, chats: 0 }), false);
});

test("its hello is in its own voice: who it is, what it is for, who else is in it", () => {
    assert.equal(
        welcomeSaid({ name: "Support", description: "Customer questions, from Intercom and email." }, "Priti Shah", 3),
        "Hi Priti. I’m Support. What I’m for: Customer questions, from Intercom and email. 2 other people work with me. Anything waiting for you is listed below, and you can ask me anything in the box at the bottom.",
    );
    assert.equal(
        welcomeSaid({ name: "Returns" }, "You", 2),
        "Hi. I’m Returns. 1 other person works with me. Anything waiting for you is listed below, and you can ask me anything in the box at the bottom.",
    );
    assert.equal(welcomeSaid({ name: "Returns", description: "  " }, "Sam", 1).startsWith("Hi Sam. I’m Returns. Anything waiting"), true, "alone in it, and with nothing said about it, it says neither");
});

test("someone with no profile yet is not greeted by the start of their address", () => {
    assert.equal(welcomeSaid({ name: "Support" }, "lee.space1", 1).startsWith("Hi. I’m Support."), true);
    assert.equal(welcomeSaid({ name: "Support" }, "José Ruiz", 1).startsWith("Hi José. I’m Support."), true);
});

test("whoever made a Lemma, or is an admin of it, looks after it", () => {
    const members = [{ userId: "u2", role: "POD_ADMIN" }, { userId: "u3", role: "POD_USER" }];
    assert.equal(looksAfter({ hiredBy: "u1", members }, "u1"), true, "its maker");
    assert.equal(looksAfter({ hiredBy: "u1", members }, "u2"), true, "an admin");
    assert.equal(looksAfter({ hiredBy: "u1", members }, "u3"), false);
    assert.equal(looksAfter({ hiredBy: "u1", members }, null), false);
});
