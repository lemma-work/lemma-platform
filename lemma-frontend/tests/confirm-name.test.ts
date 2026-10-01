import test from "node:test";
import assert from "node:assert/strict";
import { namesTeammate } from "../src/space/confirm-name.ts";

test("the teammate's name, as written, confirms", () => {
    assert.equal(namesTeammate("Kit", "Kit"), true);
});

test("capitals and the space a phone keyboard adds do not matter", () => {
    assert.equal(namesTeammate("kit ", "Kit"), true);
    assert.equal(namesTeammate("  ops   desk", "Ops Desk"), true);
});

test("part of the name, or another name, does not confirm", () => {
    assert.equal(namesTeammate("Ki", "Kit"), false);
    assert.equal(namesTeammate("Kite", "Kit"), false);
    assert.equal(namesTeammate("Remy", "Kit"), false);
});

test("an empty box never confirms, even for a name that is only spaces", () => {
    assert.equal(namesTeammate("", "Kit"), false);
    assert.equal(namesTeammate("", "   "), false);
    assert.equal(namesTeammate("  ", "  "), false);
});
