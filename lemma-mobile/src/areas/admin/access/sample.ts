import type { Member } from "@web/org/membership";
import type { AccessSource } from "./source";

/** Acme's people. Ada is the sample's signed-in person (`data/sample.ts`),
 *  and its owner, so every control can be tried. Exported for the parts
 *  whose samples name the same people. */
export const SAMPLE_MEMBERS: Member[] = [
    { id: "member-ada", role: "ORG_OWNER", user_id: "me", user: { email: "ada@acme.example", first_name: "Ada", last_name: "Lovelace" } },
    { id: "member-sam", role: "ORG_EDITOR", user_id: "user-sam", user: { email: "sam@acme.example", first_name: "Sam", last_name: "Okafor" } },
    { id: "member-dev", role: "ORG_MEMBER", user_id: "user-dev", user: { email: "dev@acme.example", first_name: "Dev", last_name: "Mehta" } },
    { id: "member-lena", role: "ORG_MEMBER", user_id: "user-lena", user: { email: "lena@acme.example", first_name: "Lena", last_name: "Park" } },
];

/** Everything an admin of a Lemma holds: Ada can try every control. */
const EVERYTHING = [
    "pod.read", "pod.update", "pod.delete", "pod.member.manage", "contact.read", "contact.message",
    "channel.read", "channel.write", "channel.manage",
];

export function accessSample(): AccessSource {
    return {
        podActions: async () => EVERYTHING,
        orgMembers: async () => SAMPLE_MEMBERS,
        deployment: async () => "hosted",
    };
}
