import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { hireFromCard, readRoleCard, readRoleCards } from "../src/data/roles.ts";
import { openersFor, profileFor } from "../src/data/hires.ts";
import { SAMPLE_ROLES } from "../src/data/sample-roles.ts";
import { characterForSeed } from "../src/shell/cast.ts";

const TEMPLATES = path.resolve(import.meta.dirname, "../../lemma-backend/app/modules/pod_bundle/templates");

interface RoleBlock {
    line: string;
    seed: string;
    brings: string[];
    wins: { say: string; needs?: { connector: string; label: string } }[];
    offers?: { title: string; detail: string; cron: string; instruction: string }[];
}

function manifest(template: string): { name: string; description: string; role: RoleBlock } {
    return JSON.parse(readFileSync(path.join(TEMPLATES, template, "pod.json"), "utf8"));
}

const templates = () => readdirSync(TEMPLATES).filter((name) => !name.startsWith("."));

const WIRE = {
    template: "support-desk",
    name: "Support desk",
    role: "Answers customers",
    about: "Answers customers and knows when to hand over.",
    seed: "arch/reception/13",
    brings: ["Skills", "Tables"],
    wins: [{ say: "Start here." }, { say: "Read Intercom.", needs: { connector: "intercom", label: "Intercom" } }],
    offers: [{ title: "Morning list", detail: "Each weekday", cron: "0 9 * * 1-5", instruction: "List what came in." }],
    judged_on: ["First reply inside an hour"],
    skills: [{ name: "hand-over", description: "Hand a conversation to a person." }],
    tables: [{ name: "conversations", description: "One per conversation." }],
};

test("a card off the wire keeps what a shelf listing shows", () => {
    const card = readRoleCard(WIRE);
    assert.ok(card);
    assert.equal(card.template, "support-desk");
    assert.deepEqual(card.judgedOn, ["First reply inside an hour"]);
    assert.deepEqual(card.wins[1].needs, { connector: "intercom", label: "Intercom" });
    assert.equal(card.wins[0].needs, undefined);
});

test("a card with no name or no face is left off the shelf rather than drawn half-empty", () => {
    assert.equal(readRoleCard({ ...WIRE, seed: "" }), null);
    assert.equal(readRoleCard({ ...WIRE, name: "  " }), null);
    assert.equal(readRoleCard(null), null);
    assert.deepEqual(readRoleCards({ items: [WIRE, { ...WIRE, template: "" }] }).map((card) => card.template), ["support-desk"]);
    assert.deepEqual(readRoleCards({}), []);
});

test("a listing's first things to say are its wins, and its schedules are its offers, none running", () => {
    const hire = hireFromCard(readRoleCard(WIRE)!);
    assert.equal(hire.bundle, "support-desk");
    assert.deepEqual(hire.openers, ["Start here.", "Read Intercom."]);
    assert.deepEqual(hire.taught, WIRE.skills);
    const profile = profileFor(hire);
    assert.equal(profile.commitments.length, 1);
    assert.equal(profile.commitments[0].cadence, "Every weekday at 09:00");
    assert.ok(profile.commitments.every((schedule) => !schedule.active));
    assert.deepEqual(profile.projects, []);
    // The tools are the set a hired teammate's own responder runs with.
    assert.ok(profile.skills.length > 0);
});

test("a listing's openers ignore whatever was typed on the shelf", () => {
    // The job box is the road to the blank hire. Somebody who typed into it
    // and then took a role off the shelf is getting that role.
    const hire = hireFromCard(readRoleCard(WIRE)!);
    assert.deepEqual(openersFor(hire, "something else entirely"), hire.openers);
});

test("every template on the server is a role with a card", () => {
    for (const template of templates()) {
        const { name, description, role } = manifest(template);
        assert.ok(name && description, template);
        assert.ok(role, template + ": no role block, so it is not on the shelf");
        assert.ok(role.brings.length > 0 && role.brings.length <= 3, template + ": brings");
        assert.ok(role.wins.length > 0 && role.wins.length <= 3, template + ": wins");
    }
});

test("no two roles arrive as the same sculpture", () => {
    // At the size a card draws them, silhouette separates two candidates and
    // hue does not.
    const faces = templates().map((template) => characterForSeed(manifest(template).role.seed));
    assert.equal(new Set(faces).size, faces.length, faces.join(", "));
});

test("a win that needs a place names one the settings link can carry", () => {
    for (const template of templates()) {
        for (const win of manifest(template).role.wins) {
            if (win.needs) assert.match(win.needs.connector, /^[a-z-]{1,32}$/, template);
        }
    }
});

test("standing work a role offers clears the platform's frequency floor", () => {
    for (const template of templates()) {
        for (const offer of manifest(template).role.offers ?? []) {
            const minute = offer.cron.split(" ")[0];
            assert.ok(/^\d+$/.test(minute), template + ": " + offer.title + " fires more than once an hour");
            assert.ok(offer.instruction.length > 20, template + ": " + offer.title + " has no instruction to carry");
        }
    }
});

test("the sample shelf is the templates' own cards", () => {
    // A copy, because the sample has no server to ask; held here to what it
    // was copied from.
    const cards = readRoleCards(SAMPLE_ROLES);
    assert.deepEqual(cards.map((card) => card.template).sort(), templates().sort());
    for (const card of cards) {
        const { name, description, role } = manifest(card.template);
        assert.equal(card.name, name, card.template);
        assert.equal(card.about, description, card.template);
        assert.equal(card.role, role.line, card.template);
        assert.equal(card.seed, role.seed, card.template);
        assert.deepEqual(card.brings, role.brings, card.template);
        assert.deepEqual(card.wins, role.wins, card.template);
        assert.deepEqual(card.offers, role.offers ?? [], card.template);
        const skills = readdirSync(path.join(TEMPLATES, card.template, "files/skills")).sort();
        assert.deepEqual(card.skills.map((skill) => skill.name).sort(), skills, card.template);
        assert.ok(card.judgedOn.length > 0, card.template);
    }
});
