import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { OPEN_TO_ANYONE, arrivalHeading, canOpenToDomain, defaultOrgKind, domainOf, freshTeamNameFor, isDomainTaken, personalNameFor, teamNameFor } from "../src/org/arrival.ts";

test("a domain is read off the address, lowercased", () => {
    assert.equal(domainOf("Alice@Acme.COM"), "acme.com");
    assert.equal(domainOf("  alice@acme.co.uk  "), "acme.co.uk");
});

test("anything that is not an address has no domain rather than a broken one", () => {
    // This reads a field filled in somewhere else. A screen that throws because
    // an account came back without an email is worse than one that treats the
    // domain as unknown.
    for (const bad of ["", "alice", "alice@", "@acme.com", "alice@localhost", null, undefined]) {
        assert.equal(domainOf(bad), "", JSON.stringify(bad));
    }
});

test("a shared mailbox domain is never opened to its own domain", () => {
    // EMAIL_DOMAIN on gmail.com would admit every Gmail address on earth.
    for (const email of ["a@gmail.com", "a@GMAIL.com", "a@icloud.com", "a@proton.me", "a@qq.com"]) {
        assert.equal(canOpenToDomain(email), false, email);
    }
});

test("a company domain may be opened to itself", () => {
    assert.equal(canOpenToDomain("alice@acme.com"), true);
    assert.equal(canOpenToDomain("alice@acme.co.uk"), true);
});

test("a domain that could not be read is treated as one nobody owns", () => {
    assert.equal(canOpenToDomain("alice"), false);
    assert.equal(canOpenToDomain(null), false);
});

test("the company name is prefilled from the label that names the company", () => {
    assert.equal(teamNameFor("alice@acme.com"), "Acme");
    assert.equal(teamNameFor("alice@acme.co.uk"), "Acme");
    assert.equal(teamNameFor("alice@my-company.io"), "My company");
    // The company, not the department: a subdomain is not the organization.
    assert.equal(teamNameFor("alice@eng.acme.com"), "Acme");
    assert.equal(teamNameFor("alice@eng.acme.co.uk"), "Acme");
    assert.equal(teamNameFor("alice"), "");
});

test("a shared mailbox provider is never offered as a company name", () => {
    // It was prefilling the box with "Gmail" and labelling the button
    // "Create Gmail".
    assert.equal(teamNameFor("alice@gmail.com"), "");
    assert.equal(teamNameFor("alice@proton.me"), "");
});

test("a personal workspace is named after its person, never a counter", () => {
    // Production grew "Personal 5": a bare "Personal", numbered on a clash.
    assert.equal(personalNameFor("Alice Chen"), "Alice's workspace");
    assert.equal(personalNameFor("Alice", "zed@acme.com"), "Alice's workspace");
    // No name given: an address whose first word reads as a name is used...
    assert.equal(personalNameFor(null, "anukul@lemma.work"), "Anukul's workspace");
    assert.equal(personalNameFor("", "alice.chen+test@acme.com"), "Alice's workspace");
    // ...and a mailbox is not: this screen offered somebody
    // "ada0196+99's Personal".
    assert.equal(personalNameFor("", "ada0196+99@example.com"), "My workspace");
    assert.equal(personalNameFor(null), "My workspace");
    assert.ok(!/\d/.test(personalNameFor(null, "x1@acme.com")));
});

test("a domain another organization holds is told apart from other failures", () => {
    assert.ok(isDomainTaken({ code: "ORGANIZATION_DOMAIN_CONFLICT", message: "" }));
    assert.ok(isDomainTaken(new Error("This email domain is already taken by another organization")));
    assert.ok(!isDomainTaken({ code: "ORGANIZATION_SLUG_CONFLICT", message: "Organization slug already exists" }));
    assert.ok(!isDomainTaken(null));
});

test("the heading says invited only for a real invitation", () => {
    assert.equal(arrivalHeading(1, 0), "You’ve been invited");
    assert.equal(arrivalHeading(1, 2), "You’ve been invited");
    assert.equal(arrivalHeading(0, 1), "Your team is already here");
    assert.equal(arrivalHeading(0, 0), "Who will you be working with?");
});

test("a local install preselects just me, whatever the domain", () => {
    assert.equal(defaultOrgKind("alice@acme.com", true), "personal");
    assert.equal(defaultOrgKind("alice@acme.com", false), "team");
    assert.equal(defaultOrgKind("alice@gmail.com", false), "personal");
});

test("a second organization is not offered the name of the first", () => {
    // The guess comes from the email domain, so somebody at Acme making a
    // second organization was offered "Acme" again.
    assert.equal(freshTeamNameFor("alice@acme.com", ["Acme"]), "");
    assert.equal(freshTeamNameFor("alice@acme.com", [" acme "]), "");
    assert.equal(freshTeamNameFor("alice@acme.com", ["Alice's Personal"]), "Acme");
    assert.equal(freshTeamNameFor("alice@acme.com", []), "Acme");
});

test("the web refuses to open a workspace to exactly the mail providers the server refuses", () => {
    /* The server's list is the authority; this copy only names a workspace
       and preselects how it opens. Read from the Python source, so a domain
       added to one side alone fails here rather than in somebody's sign-up. */
    const python = readFileSync(new URL("../../lemma-backend/app/modules/identity/domain/email_domains.py", import.meta.url), "utf8");
    const block = python.slice(python.indexOf("PUBLIC_EMAIL_PROVIDER_DOMAINS"), python.indexOf(")", python.indexOf("PUBLIC_EMAIL_PROVIDER_DOMAINS")));
    const server = [...block.matchAll(/"([a-z0-9.-]+\.[a-z]{2,})"/g)].map((match) => match[1]).sort();
    assert.ok(server.length > 20, "read the server's list");
    assert.deepEqual([...OPEN_TO_ANYONE].sort(), server);
});
