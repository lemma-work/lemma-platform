import test from "node:test";
import assert from "node:assert/strict";
import { canOpenToDomain, domainOf, personalNameFor, teamNameFor } from "../src/org/arrival.ts";

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

test("the company name is prefilled from the first label of the domain", () => {
    assert.equal(teamNameFor("alice@acme.com"), "Acme");
    assert.equal(teamNameFor("alice@acme.co.uk"), "Acme");
    assert.equal(teamNameFor("alice@my-company.io"), "My company");
    assert.equal(teamNameFor("alice"), "");
});

test("a shared mailbox provider is never offered as a company name", () => {
    // It was prefilling the box with "Gmail" and labelling the button
    // "Create Gmail".
    assert.equal(teamNameFor("alice@gmail.com"), "");
    assert.equal(teamNameFor("alice@proton.me"), "");
});

test("a personal workspace takes the person's first name, or no name at all", () => {
    // `shortOrgName` in data/live.ts already reads this shape back off the
    // wire; a workspace this app makes should shorten the same way.
    assert.equal(personalNameFor("Alice Chen"), "Alice's Personal");
    assert.equal(personalNameFor("Alice"), "Alice's Personal");
    // The local part of an address is a mailbox, not a name: this screen
    // offered somebody "deepakjha0196+99's Personal".
    assert.equal(personalNameFor(""), "Personal");
    assert.equal(personalNameFor(null), "Personal");
});
