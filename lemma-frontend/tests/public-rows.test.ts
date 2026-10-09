import test from "node:test";
import assert from "node:assert/strict";
import type { TableOpeningResponse } from "lemma-sdk";
import {
    columnQuestion,
    customFormAsk,
    formEmbed,
    formLink,
    htmlFormSnippet,
    openingProblem,
    readOpening,
    startingColumns,
} from "../src/data/public-rows.ts";

const closed = readOpening({
    table: "signups",
    per_user: false,
    contact_owned: false,
    offered: [
        { name: "full_name", type: "TEXT", required: true, options: [], description: "Your name" },
        { name: "seats", type: "INTEGER", required: false, options: [], description: null },
        { name: "track", type: "ENUM", required: false, options: ["Design", "Code"], description: null },
    ],
    audience: null,
    columns: [],
} as unknown as TableOpeningResponse);

test("a closed table starts with everything it offers ticked", () => {
    assert.equal(closed.audience, null);
    assert.deepEqual(startingColumns(closed), ["full_name", "seats", "track"]);
    assert.deepEqual(startingColumns({ ...closed, audience: "anyone", columns: ["full_name"] }), ["full_name"]);
});

test("a column the table needs can't be left closed", () => {
    assert.equal(openingProblem(closed, []), "Tick at least one column people can fill in.");
    assert.match(openingProblem(closed, ["seats"]) ?? "", /needs full_name/);
    assert.equal(openingProblem(closed, ["full_name", "track"]), null);
});

test("the form is the chat's page and script, told which table", () => {
    const embed = '<script src="https://api.example/public/web/widget.js" data-lemma-key="pk_1" async></script>';
    assert.equal(formLink("https://api.example/public/web/pk_1/page", "signups"), "https://api.example/public/web/pk_1/page?table=signups");
    assert.equal(formEmbed(embed, "signups"), '<script src="https://api.example/public/web/widget.js" data-lemma-key="pk_1" data-lemma-table="signups" async></script>');
    const html = htmlFormSnippet(embed, "signups", closed.offered);
    assert.match(html, /<form method="post" data-lemma-table="signups">/);
    assert.match(html, /<input name="full_name" type="text" required>/);
    assert.match(html, /<input name="seats" type="number">/);
    assert.match(html, /<select name="track"><option>Design<\/option><option>Code<\/option><\/select>/);
    assert.match(html, /data-lemma-chat="off" async>/);
    const odd = htmlFormSnippet(embed, "signups", [{ ...closed.offered[0], name: "pick", options: ['A & "B"', "<script>"] }]);
    assert.match(odd, /<option>A &amp; &quot;B&quot;<\/option><option>&lt;script&gt;<\/option>/);
    assert.match(customFormAsk("signups"), /lemma-form skill/);
});

test("a column asks its description, or its name said plainly", () => {
    assert.equal(columnQuestion(closed.offered[0]), "Your name");
    assert.equal(columnQuestion(closed.offered[1]), "Seats");
    assert.equal(columnQuestion({ ...closed.offered[1], name: "work_email" }), "Work email");
});
