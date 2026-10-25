import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
    KINDS, UNREADABLE_INVITATION, arrivalLede, inviteLine, invitationStanding, landingRoute, orgName, podFromRedirect, podRoleWord,
    readInvitationDetail, readInvitations, readOrgs, roleWord, sampleArrival, spokenDate,
} from "../src/areas/more/welcome/model";
import { welcomeSample } from "../src/areas/more/welcome/sample";
import { key } from "../src/web-shims/session-storage";
import { arrivalHeading, canOpenToDomain, defaultOrgKind, freshTeamNameFor, personalNameFor } from "@web/org/arrival";
import { asksFirstProfile, firstProfileNeeds, needsTimezoneSync, settledKey } from "@web/session/first-profile";

/** The first morning (PARITY.md, G): invitations, organizations to join,
 *  making one, the first profile step and an invitation's link. The web's
 *  own modules are held to the web; the phone's reading is `model.ts`. */

const mobile = fileURLToPath(new URL("..", import.meta.url));
describe("the arrival's three rungs", () => {
    it("heads the screen with what is true, never an invitation that was not sent", () => {
        expect(arrivalHeading(1, 3)).toBe("You’ve been invited");
        expect(arrivalHeading(0, 1)).toBe("Your team is already here");
        expect(arrivalHeading(0, 0)).toBe("Who will you be working with?");
        expect(arrivalLede(2, 0)).toBe("Accept an invitation to join your team.");
        expect(arrivalLede(0, 1)).toBe("You can join with your verified work email.");
        expect(arrivalLede(0, 0)).toBe("This decides who else can see your Lemmas and the work they do.");
    });

    it("chooses Just me on a local installation and for a shared mailbox, My team for a company address", () => {
        expect(defaultOrgKind("ada@acme.example", false)).toBe("team");
        expect(defaultOrgKind("ada@acme.example", true)).toBe("personal");
        expect(defaultOrgKind("ada@gmail.com", false)).toBe("personal");
        expect(canOpenToDomain("ada@gmail.com")).toBe(false);
        expect(freshTeamNameFor("ada@acme.example", ["Acme"])).toBe("");
        expect(freshTeamNameFor("ada@acme.example", [])).toBe("Acme");
        expect(personalNameFor("Ada Park")).toBe("Ada's Personal");
        expect(personalNameFor("")).toBe("Personal");
    });

    it("says the two kinds the phone's way", () => {
        expect(KINDS.team.line).toBe("Start Lemmas for your team and choose who can open each one.");
        expect(KINDS.personal.title).toBe("Just me");
    });
});

describe("reading the server", () => {
    it("keeps invitations that can be accepted, and names the organization even when it sent no name", () => {
        const read = readInvitations({
            items: [
                { id: "i1", organization_id: "o1", organization_name: "Globex", pod_id: "p1", pod_name: "Launch", pod_description: "Runs the October launch.", role: "ORG_EDITOR" },
                { id: "i2", organization_id: "o2" },
                { id: "", organization_id: "o3" },
                { id: "i4" },
            ],
        });
        expect(read.map((one) => one.id)).toEqual(["i1", "i2"]);
        expect(read[0]).toMatchObject({ orgName: "Globex", podId: "p1", podName: "Launch", role: "ORG_EDITOR" });
        expect(read[1]).toMatchObject({ orgName: "an organization", podId: null, role: "ORG_MEMBER" });
    });

    it("shortens a personal organization's name as the switcher does", () => {
        expect(orgName("Ada's Personal")).toBe("Ada · personal");
        expect(orgName("ada@acme.example's Personal")).toBe("ada · personal");
        expect(readOrgs({ items: [{ id: "o1", name: "Acme Labs" }, { id: "o2" }] })).toEqual([{ id: "o1", name: "Acme Labs" }]);
    });

    it("reads an invitation from its link, and an unknown status as lapsed rather than open", () => {
        const invite = readInvitationDetail({ id: "i1", organization_id: "o1", organization_name: "Acme", email: "ada@acme.example", role: "ORG_MEMBER", status: "PENDING", expires_at: "2026-10-30T00:00:00Z", pod_role: "POD_EDITOR" });
        expect(invite).toMatchObject({ status: "PENDING", podRole: "POD_EDITOR", redirectUri: null });
        expect(readInvitationDetail({ id: "i2", status: "SOMETHING_NEW" }).status).toBe("EXPIRED");
    });
});

describe("what an invitation says", () => {
    it("names the Lemma it brings you in to work with, else the role", () => {
        expect(inviteLine({ podName: "Launch", podAbout: "Runs the October launch.", role: "ORG_MEMBER" })).toBe("Invited to work with Launch. Runs the October launch.");
        expect(inviteLine({ podName: "Launch", podAbout: null, role: "ORG_MEMBER" })).toBe("Invited to work with Launch");
        expect(inviteLine({ podName: null, podAbout: null, role: "ORG_EDITOR" })).toBe("Invited as an editor");
        expect(inviteLine({ podName: null, podAbout: null, role: "ORG_MEMBER" })).toBe("Invited as a member");
        expect(roleWord("ORG_OWNER")).toBe("owner");
        expect(roleWord("")).toBe("member");
        expect(podRoleWord("POD_VIEWER")).toBe("viewer");
        expect(podRoleWord(null)).toBeNull();
    });

    it("says where it stands, and a pending one past its time as lapsed", () => {
        const now = Date.parse("2026-10-24T12:00:00Z");
        expect(invitationStanding({ status: "PENDING", expiresAt: "2026-10-30T00:00:00Z" }, now)).toEqual({ open: true, said: null });
        expect(invitationStanding({ status: "PENDING", expiresAt: "2026-10-20T00:00:00Z" }, now).open).toBe(false);
        expect(invitationStanding({ status: "ACCEPTED", expiresAt: null }, now).said).toBe("This invitation has been accepted already.");
        expect(invitationStanding({ status: "REVOKED", expiresAt: null }, now).said).toMatch(/declined or withdrawn/);
        expect(invitationStanding({ status: "EXPIRED", expiresAt: "2026-10-15T12:00:00Z" }, now).said).toBe("This invitation lapsed on 15 October. Ask whoever sent it for a new one.");
        expect(spokenDate("2025-03-02T12:00:00Z", new Date(now))).toBe("2 March 2025");
        expect(spokenDate("not a date")).toBe("");
    });

    it("lands on the Lemma a redirect names, and on Home otherwise", () => {
        expect(podFromRedirect("/t/pod-1")).toBe("pod-1");
        expect(podFromRedirect("https://lemma.example/t/pod%201/conversation/c1")).toBe("pod 1");
        expect(podFromRedirect("/t/pod-1?tab=chats")).toBe("pod-1");
        expect(podFromRedirect("/o/pod-1")).toBeNull();
        expect(podFromRedirect("https://lemma.example/")).toBeNull();
        expect(podFromRedirect(null)).toBeNull();
        expect(landingRoute("pod-1")).toBe("/lemma/pod-1");
        expect(landingRoute(null)).toBe("/");
    });
});

describe("the first profile step", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    const fresh = { first_name: null, mobile_number: null, created_at: "2026-10-23T09:00:00Z", timezone: null } as never;

    it("asks a new account for what is missing, once", () => {
        expect(asksFirstProfile(fresh, { whatsApp: true, settled: false, now })).toBe(true);
        expect(asksFirstProfile(fresh, { whatsApp: true, settled: true, now })).toBe(false);
        expect(asksFirstProfile({ first_name: "Ada", mobile_number: null, created_at: "2026-10-23T09:00:00Z" } as never, { whatsApp: false, settled: false, now })).toBe(false);
        expect(asksFirstProfile({ first_name: null, mobile_number: null, created_at: "2026-03-02T09:00:00Z" } as never, { whatsApp: true, settled: false, now })).toBe(false);
        expect(firstProfileNeeds({ first_name: "Ada", mobile_number: null } as never, { whatsApp: true })).toEqual({ name: false, phone: true });
    });

    it("writes the phone's zone for a new account that never sees the sheet", () => {
        expect(needsTimezoneSync(fresh, { dialogOpen: false, now })).toBe(true);
        expect(needsTimezoneSync(fresh, { dialogOpen: true, now })).toBe(false);
        expect(needsTimezoneSync({ timezone: "", created_at: "2026-10-23T09:00:00Z" } as never, { dialogOpen: false, now })).toBe(false);
    });

    it("remembers it under a key the phone's keystore accepts", () => {
        expect(settledKey("3f1c-9a")).toBe("lemma.first-profile_3f1c-9a");
        expect(key("a b/c")).toMatch(/^[A-Za-z0-9._-]+$/);
    });
});

describe("sample data", () => {
    it("has two invitations, one naming a sample Lemma, and one organization to join", async () => {
        const source = welcomeSample();
        const invites = await source.myInvitations();
        expect(invites).toHaveLength(2);
        expect(invites.find((one) => one.podName)?.podId).toBe("sample-launch-1440");
        expect(await source.suggestedOrgs()).toEqual([{ id: "sample-acme-labs", name: "Acme Labs" }]);
    });

    it("accepts in memory, joins, makes one, and refuses a decline as the server does", async () => {
        const source = welcomeSample();
        expect(await source.acceptInvitation("sample-invite-launch")).toEqual({ redirectUri: "/t/sample-launch-1440" });
        expect((await source.myInvitations()).map((one) => one.id)).toEqual(["sample-invite-globex"]);
        await expect(source.acceptInvitation("sample-invite-launch")).rejects.toThrow(/not pending/);
        await source.joinOrg("sample-acme-labs");
        expect(await source.suggestedOrgs()).toEqual([]);
        expect((await source.createOrg({ name: "Ada's Personal", derived: true })).name).toBe("Ada's Personal");
        await expect(source.declineInvitation("sample-invite-globex")).rejects.toMatchObject({ statusCode: 403 });
    }, 10_000);

    it("has a link for every state, and one that cannot be read", async () => {
        const source = welcomeSample();
        const now = Date.now();
        expect(invitationStanding(await source.invitation("sample-invite-globex"), now).open).toBe(true);
        expect((await source.invitation("sample-invite-accepted")).status).toBe("ACCEPTED");
        expect((await source.invitation("sample-invite-withdrawn")).status).toBe("REVOKED");
        expect(invitationStanding(await source.invitation("sample-invite-lapsed"), now).open).toBe(false);
        await expect(source.invitation("nobody")).rejects.toMatchObject({ statusCode: 404 });
    }, 10_000);
});

describe("copy", () => {
    const now = Date.parse("2026-10-24T12:00:00Z");
    const said = [
        ...Object.values(KINDS).flatMap((one) => [one.title, one.line]),
        UNREADABLE_INVITATION,
        arrivalLede(0, 0), arrivalLede(1, 0), arrivalLede(0, 1),
        ...(["ACCEPTED", "REVOKED", "EXPIRED"] as const).map((status) => invitationStanding({ status, expiresAt: "2026-10-01T00:00:00Z" }, now).said ?? ""),
        sampleArrival("Globex", "Acme"),
        inviteLine({ podName: null, podAbout: null, role: "ORG_OWNER" }),
    ];

    it("calls a Lemma a Lemma and people people, with no exclamation or em dash", () => {
        for (const line of said) {
            expect(line).not.toMatch(/\b(pod|pods|teammate|teammates|workspace|agent|bot|assistant|members?|users?)\b/i);
            expect(line).not.toMatch(/[!—]/);
        }
    });
});

describe("the screens", () => {
    const files = ["arrival.tsx", "make-one.tsx", "first-profile-step.tsx", "screens.tsx"];
    for (const file of files) {
        it(`${file} draws with no banned words, literal colours or bold`, () => {
            const screen = readFileSync(mobile + "src/areas/more/welcome/" + file, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
            const literals = [...screen.matchAll(/"([^"\n]*)"|`([^`\n]*)`|>([^<>{}\n]+)</g)].map((m) => m[1] ?? m[2] ?? m[3]).filter((one) => !/^\.\.?\//.test(one));
            for (const literal of literals) expect(literal).not.toMatch(/\bpods?\b|teammates?|\bworkspaces?\b|\bmembers\b|\busers?\b|—/i);
            expect(screen).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\(/i);
            expect(screen).not.toMatch(/fontWeight|weight="bold"/);
        });
    }
});
