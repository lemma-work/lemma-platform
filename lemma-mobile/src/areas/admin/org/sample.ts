import { SAMPLE_MEMBERS } from "../access/sample";
import type { OrgSource, Invitation } from "./source";
import type { OrgJoin } from "@web/data/joining";

const DAY = 24 * 60 * 60_000;

const pause = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/** Acme's invitations: one emailed, and one from an address outside Acme
 *  that nobody was emailed about, so the link to hand over can be seen. */
const INVITATIONS: Invitation[] = [
    {
        id: "sample-invite-maya", email: "maya@acme.example", role: "ORG_MEMBER", status: "PENDING",
        expires_at: new Date(Date.now() + 5 * DAY).toISOString(), accept_url: "https://app.lemma.example/invitations/sample-invite-maya", emailed: true,
    },
    {
        id: "sample-invite-ravi", email: "ravi@studio.example", role: "ORG_EDITOR", status: "PENDING",
        expires_at: new Date(Date.now() + 12 * DAY).toISOString(), accept_url: "https://app.lemma.example/invitations/sample-invite-ravi", emailed: false,
    },
    {
        id: "sample-invite-old", email: "jo@acme.example", role: "ORG_MEMBER", status: "ACCEPTED",
        expires_at: new Date(Date.now() - 20 * DAY).toISOString(), accept_url: null, emailed: true,
    },
];

/** Open to the domain rule, so the domain it needs is on screen. */
const JOIN: Record<string, OrgJoin> = {
    "sample-acme": { policy: "domain", domain: "acme.example" },
};

/** Writes change Acme's people themselves (`SAMPLE_MEMBERS`, which every
 *  part reads), in memory, until the app closes. The sample server emails an
 *  invitation inside Acme's own domain and not one to anywhere else, so both
 *  answers can be tried: an emailed invitation, and one whose link has to be
 *  handed over. */
export function orgSample(): OrgSource {
    return {
        invitations: async () => {
            await pause(120);
            return INVITATIONS.map((one) => ({ ...one }));
        },
        invite: async (_orgId, email, role) => {
            await pause(450);
            const address = email.trim();
            const id = "sample-invite-" + (INVITATIONS.length + 1);
            const made: Invitation = {
                id, email: address, role, status: "PENDING",
                expires_at: new Date(Date.now() + 14 * DAY).toISOString(),
                accept_url: "https://app.lemma.example/invitations/" + id,
                emailed: address.toLowerCase().endsWith("@acme.example"),
            };
            INVITATIONS.unshift(made);
            return { ...made };
        },
        revoke: async (invitationId) => {
            await pause(250);
            const at = INVITATIONS.findIndex((one) => one.id === invitationId);
            if (at >= 0) INVITATIONS[at] = { ...INVITATIONS[at], status: "REVOKED" };
        },
        setRole: async (_orgId, memberId, role) => {
            await pause(300);
            const at = SAMPLE_MEMBERS.findIndex((one) => one.id === memberId);
            if (at >= 0) SAMPLE_MEMBERS[at] = { ...SAMPLE_MEMBERS[at], role };
        },
        remove: async (_orgId, memberId) => {
            await pause(300);
            const at = SAMPLE_MEMBERS.findIndex((one) => one.id === memberId);
            if (at >= 0) SAMPLE_MEMBERS.splice(at, 1);
        },
        join: async (orgId) => {
            await pause(80);
            return { ...(JOIN[orgId] ?? { policy: "invited", domain: "" }) };
        },
        setJoin: async (orgId, join) => {
            await pause(320);
            /* Kept as the server keeps it: the domain stays when the rule moves away. */
            JOIN[orgId] = { policy: join.policy, domain: join.policy === "domain" ? join.domain : JOIN[orgId]?.domain ?? join.domain };
        },
    };
}
