import test from "node:test";
import assert from "node:assert/strict";
import {
    domainProblem, joinChoices, joinSaid, joinWire, orgJoinChoices, orgJoinSaid, orgJoinWire,
    readJoin, readOrgJoin, sayJoinWords,
} from "../src/data/joining.ts";

/** An access rule is the one setting where reading it wrong is not a cosmetic
 *  failure: a pod drawn as shut while it is open is a door nobody checks. */

test("the three rules round-trip through the wire's spelling", () => {
    for (const choice of joinChoices("Acme")) {
        assert.equal(readJoin(joinWire(choice.policy)), choice.policy);
    }
    assert.equal(joinWire("invited"), "INVITE_ONLY");
    assert.equal(joinWire("org"), "ORG_MEMBERS");
    assert.equal(joinWire("anyone"), "PUBLIC");
});

test("a pod nobody has configured reads as shut", () => {
    // What the backend does with an unset policy, and the state most pods are
    // in — so it is a real answer rather than a fourth, empty one.
    assert.equal(readJoin(undefined), "invited");
    assert.equal(readJoin(null), "invited");
    assert.equal(readJoin(""), "invited");
});

test("a rule this build has not been taught reads as shut, not as open", () => {
    // The one direction a wrong guess cannot be walked back.
    assert.equal(readJoin("EVERYONE_ON_THE_INTERNET"), "invited");
    assert.equal(readJoin(42), "invited");
});

test("the organization is named rather than referred to", () => {
    // Somebody in two of them is reading this to find out which one it means.
    const [, org, anyone] = joinChoices("Acme");
    assert.match(org.who, /Acme/);
    assert.match(org.then, /Acme/);
    assert.match(anyone.then, /Acme/);
});

test("an organization with no name still makes a sentence", () => {
    const [, org] = joinChoices("   ");
    assert.equal(org.who, "Anyone at this organization");
});

test("the shut rule says how to get in, not only that you cannot", () => {
    // Read as a wall it looks like the setting for being unreachable. It is
    // not: the ask has a button, and it reaches somebody.
    const [invited] = joinChoices("Acme");
    assert.match(invited.then, /Request/);
    assert.match(invited.then, /email/);
});

test("the two open rules differ in what happens, not only in who", () => {
    // The control is chosen from by reading the second line, so the second
    // line has to be the thing that differs.
    const [invited, org] = joinChoices("Acme");
    assert.match(invited.then, /Request button/);
    assert.match(org.then, /without asking/);
});

test("what is said about a rule is the rule's own row", () => {
    assert.deepEqual(joinSaid("anyone", "Acme"), {
        who: joinChoices("Acme")[2].who,
        then: joinChoices("Acme")[2].then,
    });
});

test("a refusal names the rules the way the picker does", () => {
    // The real one, which ends by telling somebody to pick a setting that is
    // not written anywhere on the screen they are looking at.
    const said = sayJoinWords(
        "This pod cannot be opened to everyone while its organization is not public. "
        + "Ask an organization owner to open the organization first, or use ORG_MEMBERS to open the pod to the organization.",
        "Acme",
    );
    assert.doesNotMatch(said, /ORG_MEMBERS/);
    assert.match(said, /\u201cAnyone at Acme\u201d/);
    // The rest of the sentence is the platform's and is left alone.
    assert.match(said, /Ask an organization owner to open the organization first/);
});

test("a refusal with nothing to translate is returned untouched", () => {
    const said = "You may not change this pod.";
    assert.equal(sayJoinWords(said, "Acme"), said);
});

/** The organization's door. The same ladder one level up, except the middle
 *  rung needs a domain — which is the whole reason it is a separate control
 *  rather than the pod's pointed at something else. */

test("the organization's three rules round-trip too", () => {
    for (const choice of orgJoinChoices("acme.com")) {
        assert.equal(readOrgJoin(orgJoinWire(choice.policy), "acme.com").policy, choice.policy);
    }
    assert.equal(orgJoinWire("domain"), "EMAIL_DOMAIN");
});

test("a domain rule with no domain admits nobody, so it is not shown as open", () => {
    // EMAIL_DOMAIN with a null email_domain lets exactly no one in. Drawn as
    // the open rule it would be a door that looks ajar and is not.
    assert.deepEqual(readOrgJoin("EMAIL_DOMAIN", null), { policy: "invited", domain: "" });
    assert.deepEqual(readOrgJoin("EMAIL_DOMAIN", "   "), { policy: "invited", domain: "" });
});

test("a domain is stored the way it is shown, however it was typed", () => {
    assert.equal(readOrgJoin("EMAIL_DOMAIN", "@Acme.COM").domain, "acme.com");
    // And it survives a rule that does not use it, so closing the door for a
    // week does not cost somebody their own domain.
    assert.equal(readOrgJoin("INVITE_ONLY", "acme.com").domain, "acme.com");
});

test("an unset or unknown organization rule reads as shut", () => {
    assert.equal(readOrgJoin(undefined, undefined).policy, "invited");
    assert.equal(readOrgJoin("ANYBODY_AT_ALL", "acme.com").policy, "invited");
});

test("the domain rule names the domain, and says so when it has none", () => {
    const [, withOne] = orgJoinChoices("acme.com");
    assert.equal(withOne.who, "Anyone with an @acme.com address");
    const [, without] = orgJoinChoices("");
    assert.match(without.then, /Needs the domain/);
});

test("a domain is refused only for what cannot work", () => {
    assert.equal(domainProblem("acme.com"), null);
    assert.equal(domainProblem("@acme.com"), null);
    assert.equal(domainProblem("mail.acme.co.uk"), null);
    assert.match(domainProblem("") ?? "", /needed/);
    assert.match(domainProblem("acme com") ?? "", /spaces/);
    assert.match(domainProblem("someone@acme.com") ?? "", /after the @/);
    assert.match(domainProblem("acme") ?? "", /after the dot/);
});

test("what is said about an organization rule is that rule's own row", () => {
    const said = orgJoinSaid({ policy: "anyone", domain: "acme.com" });
    assert.equal(said.who, "Anyone with a Lemma account");
});
