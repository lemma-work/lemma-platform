import { describe, expect, it } from "vitest";
import type { LiveContext } from "../src/areas/context";
import { SAMPLE_MEMBERS } from "../src/areas/admin/access/sample";
import { orgLive } from "../src/areas/admin/org/live";
import {
    cleanDomain, expiryWords, invitationHint, inviteWrong, memberHint, memberReach, pendingOf, pickJoin, readInvitation,
    roleBlurb, rolesFor, rolesToGive, rosterWords,
} from "../src/areas/admin/org/model";
import { orgSample } from "../src/areas/admin/org/sample";
import type { Invitation } from "../src/areas/admin/org/source";
import { orgJoinChoices, orgJoinSaid, readOrgJoin } from "@web/data/joining";
import { unsentInvitation, type Member } from "@web/org/membership";

/** The organization's people (PARITY.md, F): what the People screens decide. */

const owner: Member = { id: "m-ada", role: "ORG_OWNER", user_id: "me", user: { email: "ada@acme.example", first_name: "Ada", last_name: "Lovelace" } };
const editor: Member = { id: "m-sam", role: "ORG_EDITOR", user_id: "u-sam", user: { email: "sam@acme.example", first_name: "Sam", last_name: "Okafor" } };
const member: Member = { id: "m-dev", role: "ORG_MEMBER", user_id: "u-dev", user: { email: "dev@acme.example", first_name: "Dev", last_name: "Mehta" } };
const people = [owner, editor, member];

const BANNED = /\b(teammate|pod|bot|channel|goal|mission|line)s?\b/i;

function invitation(patch: Partial<Invitation>): Invitation {
    return { id: "i-1", email: "maya@acme.example", role: "ORG_MEMBER", status: "PENDING", expires_at: null, accept_url: null, emailed: true, ...patch };
}

describe("an invitation", () => {
    it("is read off the wire, and nothing without an id", () => {
        expect(readInvitation({ id: "i-9", email: " maya@acme.example ", role: "ORG_EDITOR", status: "PENDING", emailed: false, accept_url: "https://app.lemma.example/i/9" }))
            .toEqual({ id: "i-9", email: "maya@acme.example", role: "ORG_EDITOR", status: "PENDING", expires_at: null, accept_url: "https://app.lemma.example/i/9", emailed: false });
        expect(readInvitation({ email: "x@acme.example" })).toBeNull();
        expect(readInvitation(null)).toBeNull();
        expect(readInvitation({ id: "i-2" })?.emailed).toBeNull();
    });

    it("keeps only the ones still waiting", () => {
        expect(pendingOf([invitation({ id: "a" }), invitation({ id: "b", status: "ACCEPTED" }), invitation({ id: "c", status: "REVOKED" })]).map((one) => one.id)).toEqual(["a"]);
    });

    it("says when it runs out", () => {
        const now = new Date("2026-10-24T12:00:00Z");
        expect(expiryWords("2026-10-31T09:00:00Z", now)).toBe("Expires 31 October");
        expect(expiryWords("2027-01-02T09:00:00Z", now)).toBe("Expires 2 January 2027");
        expect(expiryWords("2026-10-01T09:00:00Z", now)).toBe("Expired");
        expect(expiryWords(null, now)).toBe("");
        expect(expiryWords("soon", now)).toBe("");
    });

    it("tells whoever can hand it over that nobody was emailed", () => {
        const now = new Date("2026-10-24T12:00:00Z");
        const quiet = invitation({ role: "ORG_EDITOR", expires_at: "2026-10-31T09:00:00Z", emailed: false });
        expect(invitationHint(quiet, true, now)).toBe("Editor · Expires 31 October · Not emailed");
        expect(invitationHint(quiet, false, now)).toBe("Editor · Expires 31 October");
        expect(invitationHint(invitation({ emailed: true }), true, now)).toBe("Member");
    });

    it("refuses what cannot work, and anybody already here or asked", () => {
        expect(inviteWrong("", people, [])).toBe("An email address is needed.");
        expect(inviteWrong("not an address", people, [])).toBe("An address cannot contain spaces.");
        expect(inviteWrong("SAM@acme.example", people, [])).toBe("They are already in this organization.");
        expect(inviteWrong("maya@acme.example", people, [invitation({})])).toBe("They have already been invited.");
        expect(inviteWrong("maya@acme.example", people, [invitation({ status: "REVOKED" })])).toBeNull();
    });
});

describe("roles", () => {
    it("says what each is for in the phone's words", () => {
        for (const role of ["ORG_OWNER", "ORG_EDITOR", "ORG_MEMBER"]) {
            expect(roleBlurb(role)).not.toBe("");
            expect(roleBlurb(role)).not.toMatch(BANNED);
        }
        expect(roleBlurb("ORG_SOMETHING")).toBe("");
    });

    it("never lets an editor hand out ownership", () => {
        expect(rolesToGive("ORG_OWNER").map((one) => one.value)).toEqual(["ORG_OWNER", "ORG_EDITOR", "ORG_MEMBER"]);
        expect(rolesToGive("ORG_EDITOR").map((one) => one.value)).toEqual(["ORG_EDITOR", "ORG_MEMBER"]);
        expect(rolesToGive("ORG_MEMBER")).toEqual([]);
        expect(rolesToGive(null)).toEqual([]);
    });

    it("keeps the role a member holds on their own list", () => {
        expect(rolesFor("ORG_EDITOR", "ORG_OWNER").map((one) => one.value)).toEqual(["ORG_OWNER", "ORG_EDITOR", "ORG_MEMBER"]);
        expect(rolesFor("ORG_EDITOR", "ORG_MEMBER").map((one) => one.value)).toEqual(["ORG_EDITOR", "ORG_MEMBER"]);
    });
});

describe("what the signed-in person may do to a member", () => {
    it("opens nothing for somebody who does not manage", () => {
        expect(memberReach("ORG_MEMBER", people, editor, "u-dev")).toEqual({ opens: false, changeRole: false, remove: false, why: null });
        expect(memberReach(null, people, editor, "me").opens).toBe(false);
    });

    it("never touches the last owner, and says why", () => {
        const mine = memberReach("ORG_OWNER", people, owner, "me");
        expect(mine.changeRole).toBe(false);
        expect(mine.remove).toBe(false);
        expect(mine.why).toMatch(/^You are the only owner/);
        expect(memberReach("ORG_EDITOR", people, owner, "u-sam").why).toMatch(/^Ada Lovelace is the only owner/);
    });

    it("does not let an editor reach over an owner", () => {
        const two = [...people, { ...member, id: "m-lena", role: "ORG_OWNER", user_id: "u-lena" }];
        expect(memberReach("ORG_EDITOR", two, owner, "u-sam")).toEqual({ opens: true, changeRole: false, remove: false, why: "Only an owner can change an owner." });
        expect(memberReach("ORG_OWNER", two, owner, "u-lena")).toEqual({ opens: true, changeRole: true, remove: true, why: null });
    });

    it("lets you change your own role but not remove yourself", () => {
        expect(memberReach("ORG_EDITOR", people, editor, "u-sam")).toEqual({ opens: true, changeRole: true, remove: false, why: null });
        expect(memberReach("ORG_OWNER", people, member, "me")).toEqual({ opens: true, changeRole: true, remove: true, why: null });
    });

    it("says who is you, and their address", () => {
        expect(memberHint(owner, true)).toBe("You · ada@acme.example");
        expect(memberHint(editor, false)).toBe("sam@acme.example");
        expect(memberHint({ id: "m-x", user: { email: "x@acme.example" } }, false)).toBe("");
    });

    it("counts people and invitations", () => {
        expect(rosterWords(4, 1)).toBe("4 people · 1 invited");
        expect(rosterWords(1, 0)).toBe("1 person");
        expect(rosterWords(null, 2)).toBe("2 invited");
    });
});

describe("who can join", () => {
    it("asks for the domain before saving a rule that would let nobody in", () => {
        expect(pickJoin({ policy: "invited", domain: "" }, "domain")).toEqual({ askDomain: true });
        expect(pickJoin({ policy: "invited", domain: "acme.example" }, "domain")).toEqual({ save: { policy: "domain", domain: "acme.example" } });
        expect(pickJoin({ policy: "domain", domain: "acme.example" }, "anyone")).toEqual({ save: { policy: "anyone", domain: "acme.example" } });
    });

    it("keeps a domain bare", () => {
        expect(cleanDomain("  @@Acme.Example ")).toBe("acme.example");
        expect(readOrgJoin("EMAIL_DOMAIN", "")).toEqual({ policy: "invited", domain: "" });
    });

    it("says every rung without the words a phone never says", () => {
        for (const choice of orgJoinChoices("acme.example")) {
            expect(choice.who + " " + choice.then).not.toMatch(BANNED);
        }
        expect(orgJoinSaid({ policy: "domain", domain: "acme.example" }).who).toBe("Anyone with an @acme.example address");
    });
});

describe("the organization's people, live", () => {
    function capture() {
        const sent: { method: string; path: string; body: unknown }[] = [];
        const client = { request: async (method: string, path: string, options: { body?: unknown }) => { sent.push({ method, path, body: options.body }); } };
        return { sent, context: { client: () => client } as unknown as LiveContext };
    }

    it("sends the domain only with the rule that uses it", async () => {
        const { sent, context } = capture();
        await orgLive(context).setJoin("org-1", { policy: "domain", domain: "acme.example" });
        await orgLive(context).setJoin("org-1", { policy: "invited", domain: "acme.example" });
        expect(sent).toEqual([
            { method: "PATCH", path: "/organizations/org-1", body: { join_policy: "EMAIL_DOMAIN", email_domain: "acme.example" } },
            { method: "PATCH", path: "/organizations/org-1", body: { join_policy: "INVITE_ONLY" } },
        ]);
    });
});

describe("the sample organization", () => {
    it("emails inside Acme's domain, and hands over a link anywhere else", async () => {
        const source = orgSample();
        const inside = await source.invite("sample-acme", "nia@acme.example", "ORG_MEMBER");
        const outside = await source.invite("sample-acme", "kai@elsewhere.example", "ORG_EDITOR");
        expect(unsentInvitation(inside)).toBeNull();
        expect(unsentInvitation(outside)?.link).toMatch(/^https:\/\/app\.lemma\.example\//);
        const pending = pendingOf(await source.invitations("sample-acme")).map((one) => one.email);
        expect(pending).toContain("kai@elsewhere.example");
        await source.revoke(outside.id);
        expect(pendingOf(await source.invitations("sample-acme")).map((one) => one.email)).not.toContain("kai@elsewhere.example");
    });

    it("changes Acme's people themselves", async () => {
        const source = orgSample();
        const before = SAMPLE_MEMBERS.map((one) => ({ ...one }));
        await source.setRole("sample-acme", "member-dev", "ORG_EDITOR");
        expect(SAMPLE_MEMBERS.find((one) => one.id === "member-dev")?.role).toBe("ORG_EDITOR");
        await source.remove("sample-acme", "member-lena");
        expect(SAMPLE_MEMBERS.some((one) => one.id === "member-lena")).toBe(false);
        SAMPLE_MEMBERS.splice(0, SAMPLE_MEMBERS.length, ...before);
    });

    it("keeps the domain when the rule moves away from it", async () => {
        const source = orgSample();
        expect(await source.join("sample-acme")).toEqual({ policy: "domain", domain: "acme.example" });
        await source.setJoin("sample-acme", { policy: "invited", domain: "acme.example" });
        expect(await source.join("sample-acme")).toEqual({ policy: "invited", domain: "acme.example" });
        await source.setJoin("sample-acme", { policy: "domain", domain: "acme.example" });
    });
});
