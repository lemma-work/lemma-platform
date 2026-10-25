import { SAMPLE_MEMBERS } from "../access/sample";
import { memberName } from "@web/org/membership";
import { personOf, type PendingInvite } from "./model";
import type { PeopleSource } from "./source";
import type { JoinPolicy, JoinRequest } from "@web/data/joining";

/** Who has access to each sample Lemma: Acme's people (`access/sample.ts`),
 *  by user id, with the roles each holds there. Ada is in every Lemma but
 *  Hiring, which Sam runs. Exported so the sample of who a Lemma can ask
 *  (`works-with/sample.ts`) agrees with who is in what. */
export const SAMPLE_ACCESS: Record<string, { userId: string; roles: string[] }[]> = {
    "sample-support-1140": [
        { userId: "me", roles: ["POD_ADMIN"] },
        { userId: "user-sam", roles: ["POD_EDITOR"] },
        { userId: "user-lena", roles: ["POD_USER"] },
    ],
    "sample-launch-1440": [
        { userId: "me", roles: ["POD_ADMIN"] },
        { userId: "user-sam", roles: ["POD_EDITOR"] },
        { userId: "user-dev", roles: ["POD_USER"] },
    ],
    "sample-renewals-790": [
        { userId: "me", roles: ["POD_ADMIN"] },
        { userId: "user-lena", roles: ["POD_EDITOR"] },
    ],
    "sample-hiring-1078": [
        { userId: "user-sam", roles: ["POD_ADMIN"] },
        { userId: "user-dev", roles: ["POD_VIEWER"] },
    ],
    "sample-books-986": [{ userId: "me", roles: ["POD_ADMIN"] }],
};

/** People who joined by asking, who are in no sample organization list. */
const STRANGERS = new Map<string, { name: string; email: string }>();

const HOUR = 3_600_000;

function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString();
}

/** Writes stay in memory while the app is open, so adding, inviting, moving
 *  the door and letting somebody in can all be tried. */
export function peopleSample(): PeopleSource {
    const invitations = new Map<string, PendingInvite[]>([
        ["sample-support-1140", [{ id: "invite-maya", email: "maya@example.com", role: "POD_USER" }]],
    ]);
    const doors = new Map<string, JoinPolicy>([["sample-launch-1440", "org"]]);
    const asking = new Map<string, JoinRequest[]>([
        ["sample-support-1140", [
            { id: "ask-rui", podId: "sample-support-1140", standing: "pending", name: "Rui Costa", email: "rui@example.com", askedAt: ago(3 * HOUR) },
        ]],
        ["sample-renewals-790", [
            { id: "ask-dev", podId: "sample-renewals-790", standing: "pending", name: "Dev Mehta", email: "dev@acme.example", askedAt: ago(26 * HOUR) },
            { id: "ask-new", podId: "sample-renewals-790", standing: "pending", name: "", email: "kim@example.com", askedAt: ago(40 * 60_000) },
        ]],
    ]);
    let made = 0;

    const access = (podId: string) => {
        if (!SAMPLE_ACCESS[podId]) SAMPLE_ACCESS[podId] = [{ userId: "me", roles: ["POD_ADMIN"] }];
        return SAMPLE_ACCESS[podId];
    };

    return {
        members: async (podId) => access(podId).map(({ userId, roles }) => {
            const member = SAMPLE_MEMBERS.find((one) => one.user_id === userId);
            const stranger = STRANGERS.get(userId);
            const email = member?.user?.email ?? stranger?.email ?? "";
            return personOf({
                pod_member_id: `${podId}:${userId}`,
                user_id: userId,
                user_name: member ? memberName(member) : stranger?.name ?? null,
                email,
                roles,
            });
        }),
        invitations: async (_orgId, podId) => invitations.get(podId) ?? [],
        add: async (podId, orgMemberId, role) => {
            const member = SAMPLE_MEMBERS.find((one) => one.id === orgMemberId);
            if (!member?.user_id) throw new Error("They are not in this organization any more.");
            const held = access(podId);
            if (held.some((one) => one.userId === member.user_id)) throw new Error("They already have access.");
            held.push({ userId: member.user_id, roles: [role] });
        },
        invite: async (_orgId, podId, email, role) => {
            made += 1;
            invitations.set(podId, [...(invitations.get(podId) ?? []), { id: `invite-sample-${made}`, email, role }]);
            /* Acme's own addresses are emailed; any other shows what a server
               with no email set up answers, a link to hand over instead. */
            const emailed = email.toLowerCase().endsWith("@acme.example");
            return { email, acceptUrl: emailed ? null : `https://acme.example/accept/sample-${made}`, emailed };
        },
        joinPolicy: async (podId) => doors.get(podId) ?? "invited",
        setJoinPolicy: async (podId, policy) => {
            if (policy === "anyone") {
                throw new Error("This pod cannot be opened to everyone while its organization is not public. Ask an organization owner to open the organization first, or use ORG_MEMBERS to open the pod to the organization.");
            }
            doors.set(podId, policy);
        },
        joinRequests: async (podId) => asking.get(podId) ?? [],
        admit: async (podId, requestId) => {
            const waiting = asking.get(podId) ?? [];
            const request = waiting.find((one) => one.id === requestId);
            if (!request) throw new Error("That ask is not waiting any more.");
            asking.set(podId, waiting.filter((one) => one.id !== requestId));
            const member = SAMPLE_MEMBERS.find((one) => one.user?.email === request.email);
            const userId = member?.user_id ?? `user-${request.id}`;
            if (!member) STRANGERS.set(userId, { name: request.name, email: request.email });
            access(podId).push({ userId, roles: ["POD_USER"] });
            return { ...request, standing: "approved" };
        },
    };
}
