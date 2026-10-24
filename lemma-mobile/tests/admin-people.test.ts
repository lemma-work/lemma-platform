import { describe, expect, it } from "vitest";
import { SAMPLE_MEMBERS } from "../src/areas/admin/access/sample";
import {
    ACCESS_CHOICES, accessLabel, accessOf, addPlan, agoOf, askedLine, askingLine, candidatesFrom, peopleLine, pendingFor, personOf, sayJoinRefusal, submitProblem,
} from "../src/areas/admin/people/model";
import { peopleSample } from "../src/areas/admin/people/sample";

/** Who has access to a Lemma (PARITY.md, F): the roster, Add people, who can
 *  join and the queue at the door, decided apart from drawing them. */

const NOW = Date.parse("2026-10-24T12:00:00Z");

describe("the roster", () => {
    it("reads a member the way the web does, roles said as what they let you do", () => {
        const person = personOf({ pod_member_id: "pm-1", user_id: "u-1", user_name: " Sam Okafor ", email: "sam@acme.example", roles: ["POD_USER", "POD_EDITOR"] });
        expect(person).toMatchObject({ id: "pm-1", userId: "u-1", name: "Sam Okafor", email: "sam@acme.example", role: "Editor", can: "Can edit" });
        const bare = personOf({ user_id: "u-2", user_email: "kim@example.com" });
        expect(bare).toMatchObject({ id: "u-2", name: "kim@example.com", role: "Member", can: "—" });
    });

    it("lists only the invitations still waiting on this Lemma", () => {
        const rows = [
            { id: "a", email: "a@x.example", status: "PENDING", pod_id: "p1", pod_role: "POD_USER" },
            { id: "b", email: "b@x.example", status: "ACCEPTED", pod_id: "p1" },
            { id: "c", email: "c@x.example", status: "PENDING", pod_id: "p2" },
            { id: "d", email: "d@x.example", status: "PENDING", pod_id: null },
        ];
        expect(pendingFor(rows, "p1")).toEqual([{ id: "a", email: "a@x.example", role: "POD_USER" }]);
        expect(accessLabel("POD_USER")).toBe("Can use");
        expect(accessLabel(null)).toBe("Invited");
        expect(accessOf(personOf({ roles: ["POD_USER", "POD_ADMIN"] }))).toBe("Admin");
        expect(accessOf(personOf({ roles: ["POD_BILLING_CLERK"] }))).toBe("Billing clerk");
    });

    it("offers the common grant first and handing over the door last", () => {
        expect(ACCESS_CHOICES.map((one) => one.value)).toEqual(["POD_EDITOR", "POD_USER", "POD_VIEWER", "POD_ADMIN"]);
        expect(ACCESS_CHOICES.at(-1)?.hint).toBeTruthy();
    });
});

describe("adding people", () => {
    const people = [personOf({ pod_member_id: "1", user_id: "me", user_name: "Ada Lovelace", email: "ada@acme.example", roles: ["POD_ADMIN"] })];
    const candidates = candidatesFrom(SAMPLE_MEMBERS, people);
    const base = { candidates, people, pending: [{ id: "i", email: "maya@example.com", role: "POD_USER" }], orgRead: true, lemma: "Support", org: "Acme" };

    it("offers everyone in the organization who has no access yet", () => {
        expect(candidates.map((one) => one.email)).toEqual(["sam@acme.example", "dev@acme.example", "lena@acme.example"]);
        expect(candidates[0]).toMatchObject({ name: "Sam Okafor", orgRole: "editor" });
    });

    it("adds somebody already in the organization, by their address", () => {
        const plan = addPlan({ ...base, query: " Sam@acme.example " });
        expect(plan.action).toBe("add");
        expect(plan.exact?.id).toBe("member-sam");
    });

    it("searches by name as well, without guessing which one to add", () => {
        const plan = addPlan({ ...base, query: "Lena" });
        expect(plan.shown.map((one) => one.name)).toEqual(["Lena Park"]);
        expect(plan.action).toBeNull();
    });

    it("invites a new address", () => {
        expect(addPlan({ ...base, query: "rui@example.com" }).action).toBe("invite");
    });

    it("says why the button is off before anybody presses it", () => {
        const here = addPlan({ ...base, query: "ada@acme.example" });
        expect(here.action).toBeNull();
        expect(here.hint).toBe("They already have access to Support.");
        const asked = addPlan({ ...base, query: "MAYA@example.com" });
        expect(asked.action).toBeNull();
        expect(asked.hint).toBe("They have already been invited to Support.");
        const everyone = addPlan({ ...base, candidates: [], pending: [], query: "" });
        expect(everyone.hint).toBe("Everyone in Acme already has access. Add someone new by email.");
    });

    it("says what is wrong with what was typed when pressing would do nothing", () => {
        const plan = addPlan({ ...base, query: "not an address" });
        expect(submitProblem(plan, "not an address")).toBe("An address cannot contain spaces.");
        expect(submitProblem(addPlan({ ...base, query: "rui@example.com" }), "rui@example.com")).toBeNull();
    });
});

describe("the door and the queue at it", () => {
    it("puts the picker's own words, and Lemma, into the server's refusal", () => {
        const said = sayJoinRefusal("This pod cannot be opened to everyone while its organization is not public. Ask an organization owner to open the organization first, or use ORG_MEMBERS to open the pod to the organization.", "Acme");
        expect(said).toContain("This Lemma cannot be opened");
        expect(said).toContain("“Anyone at Acme”");
        expect(said).not.toMatch(/ORG_MEMBERS|\bpod\b/);
    });

    it("says who is asking, as a person would", () => {
        expect(askingLine(1, "Support")).toBe("Someone is asking to join Support");
        expect(askingLine(3, "Support")).toBe("3 people are asking to join Support");
        const asked = new Date(NOW - 3 * 3_600_000).toISOString();
        expect(askedLine({ name: "Rui Costa", email: "rui@example.com", askedAt: asked }, NOW)).toBe("rui@example.com · 3h ago");
        expect(askedLine({ name: "", email: "kim@example.com", askedAt: asked }, NOW)).toBe("3h ago");
    });

    it("draws the Settings line with the dot while somebody is asking", () => {
        expect(peopleLine(3, 1)).toEqual({ text: "3 people · 1 asking to join", needs: true });
        expect(peopleLine(1, 0)).toEqual({ text: "1 person", needs: false });
        expect(peopleLine(null, 0).text).toBeNull();
    });

    it("says how long ago", () => {
        expect(agoOf(new Date(NOW - 30_000).toISOString(), NOW)).toBe("just now");
        expect(agoOf(new Date(NOW - 12 * 60_000).toISOString(), NOW)).toBe("12m ago");
        expect(agoOf(new Date(NOW - 15 * 86_400_000).toISOString(), NOW)).toBe("2w ago");
        expect(agoOf("", NOW)).toBe("");
    });
});

describe("the sample", () => {
    it("lets somebody in: their ask leaves the queue and they are in the roster", async () => {
        const source = peopleSample();
        const podId = "sample-renewals-790";
        const [first] = await source.joinRequests(podId);
        await source.admit(podId, first.id);
        expect((await source.joinRequests(podId)).map((one) => one.id)).not.toContain(first.id);
        expect((await source.members(podId)).map((one) => one.email)).toContain(first.email);
    });

    it("refuses to open a Lemma wider than its organization, as the server does", async () => {
        const source = peopleSample();
        await expect(source.setJoinPolicy("sample-books-986", "anyone")).rejects.toThrow(/organization is not public/);
        await source.setJoinPolicy("sample-books-986", "org");
        expect(await source.joinPolicy("sample-books-986")).toBe("org");
    });

    it("emails Acme's own addresses and hands back a link for any other", async () => {
        const source = peopleSample();
        expect((await source.invite("sample-acme", "sample-books-986", "new@acme.example", "POD_USER")).emailed).toBe(true);
        const other = await source.invite("sample-acme", "sample-books-986", "new@example.com", "POD_USER");
        expect(other.emailed).toBe(false);
        expect(other.acceptUrl).toMatch(/^https:\/\/acme\.example\//);
        expect((await source.invitations("sample-acme", "sample-books-986")).map((one) => one.email)).toEqual(["new@acme.example", "new@example.com"]);
    });
});
